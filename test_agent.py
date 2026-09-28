"""실행 구조(정책·재시도·예산·체크포인트·멱등성) 테스트. LLM은 고정 응답으로 대체한다.

[설계] Agent 테스트의 원칙
  - 외부 의존성(LLM, API)은 주입·패치로 결정론적으로 만든다. 자연어 정확도는 여기서 다루지 않는다.
  - 최종 응답과 함께 State·환불 기록 DB·로그·인계 파일을 검증한다.
    완료 응답이 있더라도 같은 주문의 환불 기록이 두 건이면 실패다.
  - 각 테스트는 설계 원칙 하나에 대응한다. 원칙이 바뀌면 어느 테스트가 깨질지 알 수 있어야 한다.
"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from runtime import Runtime, load_checkpoint
from unittest.mock import patch
from test_fixtures import FixtureAnalyzer

def setUpModule():
    # [설계] Runtime이 기본으로 만드는 ClaudeAnalyzer를 고정 응답 fixture로 교체한다.
    # 네트워크·API 키 없이 실행 구조만 검증하기 위한 격리.
    global analyzer_patch
    analyzer_patch = patch("runtime.ClaudeAnalyzer", FixtureAnalyzer)
    analyzer_patch.start()

def tearDownModule():
    analyzer_patch.stop()

from state import Policy, State
from tools import MockAPI, ServiceError


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def make(self, request="ORD-1001 환불해 주세요", scenario="normal", **policy):
        return Runtime(State(request), Policy(**policy), self.folder,
                       MockAPI(self.folder / "refunds.sqlite3", scenario))

    def refund_count(self):
        with closing(sqlite3.connect(self.folder / "refunds.sqlite3")) as db:
            return db.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]

    # ── 정상 경로와 업무 규칙 ──────────────────────────────────────────
    async def test_normal_refund(self):
        # [설계] 기준선: 5단계, 도구 3회(get_order, get_refund, create_refund), 비용 5, 환불 기록 1건.
        runtime = self.make()
        state = await runtime.run()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.refund["amount"], 32000)
        self.assertEqual((state.steps, state.tool_calls, state.cost_units), (5, 3, 5))
        self.assertEqual(self.refund_count(), 1)

    async def test_refund_rejected(self):
        # [설계] 거절도 completed다(안내 완료). 단 환불 기록은 늘지 않아야 한다.
        for order_id in ("ORD-1002", "ORD-1004"):
            state = await self.make(f"{order_id} 환불").run()
            self.assertEqual(state.status, "completed")
            self.assertIn("환불 불가", state.response)
        self.assertEqual(self.refund_count(), 0)

    async def test_boundary_seven_days(self):
        # [설계] 업무 규칙의 경계값(정확히 7일)은 반드시 테스트로 고정한다.
        state = await self.make("ORD-1005 환불").run()
        self.assertIsNotNone(state.refund)

    async def test_lookup_does_not_refund(self):
        # [설계] 라우팅 검증: lookup 의도는 assess/refund를 거치지 않는다(도구 1회).
        state = await self.make("ORD-1001 배송 조회").run()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.tool_calls, 1)
        self.assertEqual(self.refund_count(), 0)

    # ── Human-in-the-loop와 권한 ───────────────────────────────────────
    async def test_missing_input_and_resume(self):
        # [설계] 정보 부족 → waiting_input(도구 0회) → 체크포인트 재개 → 완료. 단계 수는 이어서 센다.
        runtime = self.make("환불해 주세요")
        state = await runtime.run()
        self.assertEqual(state.status, "waiting_input")
        self.assertEqual(state.tool_calls, 0)
        loaded, policy = load_checkpoint(runtime.checkpoint)
        loaded.request += "\nORD-1001"
        loaded.status = "running"
        resumed = await Runtime(loaded, policy, self.folder).run()
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.steps, 6)

    async def test_ambiguous_input_waits(self):
        for request in ("안녕하세요", "ORD-1001 ORD-1002 환불"):
            state = await self.make(request).run()
            self.assertEqual(state.status, "waiting_input")
            self.assertEqual(state.tool_calls, 0)

    async def test_wrong_customer_and_missing_order(self):
        # [설계] 타인 주문·없는 주문은 ServiceError → 재시도 없이 즉시 인계(도구 1회).
        for order_id in ("ORD-1003", "ORD-9999"):
            state = await self.make(f"{order_id} 환불").run()
            self.assertEqual(state.status, "escalated")
            self.assertEqual(state.tool_calls, 1)  # 영구 오류는 재시도하지 않는다.
        self.assertEqual(self.refund_count(), 0)

    # ── Execution Policy: 재시도·timeout·예산·단계 ─────────────────────
    async def test_flaky_tool_retries(self):
        # [설계] 일시 오류는 재시도로 흡수한다. attempts에 2회, 총 호출은 4회로 남는다.
        state = await self.make(scenario="flaky").run()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.attempts["lookup.get_order"], 2)
        self.assertEqual(state.tool_calls, 4)

    async def test_retry_exhaustion(self):
        # [설계] 재시도 한도(최초 1 + 2) 소진 → 인계 파일 생성. 무한 재시도는 없다.
        runtime = self.make(scenario="down")
        state = await runtime.run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.tool_calls, 3)
        self.assertTrue((self.folder / f"{state.run_id}.handoff.json").exists())

    async def test_real_async_timeout(self):
        # [설계] timeout은 실제로 코루틴을 취소해야 한다. 60초 sleep이 3초 안에 끝나는지 확인.
        state = await self.make(scenario="slow", tool_timeout_s=0.01, backoff_s=0).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.tool_calls, 3)
        self.assertIn("TimeoutError", state.last_error)
        self.assertLess(state.elapsed_s, 3)

    async def test_global_timeout_caps_tool(self):
        # [설계] 전체 시간 한도가 호출당 timeout보다 우선한다(min 적용). 재시도도 막힌다.
        state = await self.make(scenario="slow", total_timeout_s=0.03,
                                tool_timeout_s=10, backoff_s=0).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.tool_calls, 1)
        self.assertLess(state.elapsed_s, 3)

    async def test_budget_stops_before_write(self):
        # [설계] 사전 차단: 예산 2로는 create_refund(3)를 시작조차 못 한다. 환불 기록 0건.
        state = await self.make(max_cost_units=2).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.cost_units, 2)
        self.assertEqual(self.refund_count(), 0)

    async def test_call_budget(self):
        state = await self.make(max_tool_calls=1).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.tool_calls, 1)
        self.assertEqual(self.refund_count(), 0)

    async def test_step_limit(self):
        # [설계] Termination 가드. 단계 수 한도는 노드 실행 "전에" 검사되어 steps가 2를 넘지 않는다.
        state = await self.make(max_steps=2).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(state.steps, 2)
        self.assertEqual(self.refund_count(), 0)

    # ── 멱등성과 복구: timeout ≠ 실패 ──────────────────────────────────
    async def test_lost_response_does_not_duplicate(self):
        # [설계] 1차 create_refund는 커밋 후 응답 유실(timeout). 같은 멱등성 키로 2차 시도 → 환불 기록 1건.
        state = await self.make(scenario="lost-response", tool_timeout_s=0.02).run()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.attempts["refund.create_refund"], 2)
        self.assertEqual(self.refund_count(), 1)

    async def test_uncertain_write_escalates_without_false_success(self):
        # [설계] 재시도 0회면 결과를 확인할 수 없다. DB에는 환불 기록이 있지만
        # 영수증을 못 받았으므로 "환불 완료"라고 말하지 않고 인계한다. 단정 금지의 핵심 테스트.
        state = await self.make(scenario="lost-response", tool_timeout_s=0.02,
                                max_retries=0).run()
        self.assertEqual(state.status, "escalated")
        self.assertEqual(self.refund_count(), 1)
        self.assertNotIn("환불 완료", state.response)

    async def test_restart_after_committed_write(self):
        # [설계] "외부 쓰기 성공 → 체크포인트 저장 전에 프로세스 종료" 재현.
        # 재개 시 refund 노드가 get_refund로 먼저 확인하므로 create_refund를 다시 부르지 않는다.
        runtime = self.make()
        await runtime.run(pause_after=3)
        self.assertEqual(runtime.state.current_node, "refund")
        # 외부 쓰기는 성공했지만 그 뒤의 체크포인트를 저장하지 못한 상황.
        await runtime.api.create_refund("ORD-1001", "C001", "refund:C001:ORD-1001")
        state, policy = load_checkpoint(runtime.checkpoint)
        resumed = Runtime(state, policy, self.folder)
        final = await resumed.run()
        self.assertEqual(final.status, "completed")
        self.assertNotIn("create_refund", resumed.api.seen)
        self.assertEqual(self.refund_count(), 1)

    async def test_checkpoint_preserves_consumed_budget(self):
        # [설계] 재개해도 소비한 한도는 초기화되지 않는다. 사용량이 State에 있는 이유.
        runtime = self.make(max_tool_calls=1)
        await runtime.run(pause_after=2)
        state, policy = load_checkpoint(runtime.checkpoint)
        final = await Runtime(state, policy, self.folder).run()
        self.assertEqual(final.status, "escalated")
        self.assertEqual(final.tool_calls, 1)

    async def test_new_run_cannot_duplicate_same_order(self):
        # [설계] 체크포인트가 다른 "새 실행"도 같은 환불 기록 DB를 조회하므로 중복 환불이 없다(도구 2회로 끝).
        await self.make().run()
        state = await self.make().run()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.tool_calls, 2)
        self.assertEqual(self.refund_count(), 1)

    async def test_terminal_checkpoint_does_not_repeat_tools(self):
        # [설계] completed 상태의 체크포인트를 재개하면 아무 도구도 호출하지 않는다(종료 상태는 불변).
        runtime = self.make()
        await runtime.run()
        state, policy = load_checkpoint(runtime.checkpoint)
        resumed = Runtime(state, policy, self.folder)
        await resumed.run()
        self.assertEqual(resumed.api.seen, {})

    async def test_tool_rechecks_refund_rule(self):
        # [설계] 노드를 우회해 Tool을 직접 불러도 업무 규칙이 막는다. State를 신뢰하지 않는 방어선.
        runtime = self.make()
        with self.assertRaises(ServiceError):
            await runtime.api.create_refund("ORD-1002", "C001", "refund:C001:ORD-1002")
        self.assertEqual(self.refund_count(), 0)

    # ── Observability ─────────────────────────────────────────────────
    async def test_trace_has_observability_fields(self):
        # [설계] 로그 계약. 이벤트 종류·실행 식별자 run_id·latency가 빠지면 실행을 설명할 수 없다.
        runtime = self.make(scenario="flaky")
        await runtime.run()
        events = [json.loads(line) for line in runtime.trace.read_text(encoding="utf-8").splitlines()]
        kinds = {item["event"] for item in events}
        self.assertTrue({"run_start", "step_start", "step_end", "state_change", "tool_start",
                         "tool_end", "tool_error", "retry", "checkpoint", "run_end"} <= kinds)
        self.assertTrue(all(item["run_id"] == runtime.state.run_id for item in events))
        self.assertTrue(all("latency_ms" in item for item in events if item["event"] == "tool_end"))
        self.assertIsNone(events[-1]["actual_cost_krw"])
        self.assertEqual(events[-1]["llm_calls"], 1)

    async def test_invalid_policy(self):
        for change in ({"max_retries": -1}, {"max_cost_units": -1}, {"tool_timeout_s": float("nan")}):
            with self.assertRaises(ValueError):
                Policy(**change)


class LangGraphComparison(unittest.IsolatedAsyncioTestCase):
    # [설계] 실행기를 바꿔도 결과가 같아야 한다. 정상·거절·조회·대기·권한·재시도·소진·
    # 응답 유실·예산·단계 한도의 10개 시나리오에서 최종 State(run_id, 시간 제외)를 비교한다.
    async def test_same_scenarios(self):
        try:
            import langgraph.graph
        except ImportError:
            self.skipTest("선택 의존성 langgraph 미설치")
        from langgraph_version import run
        cases = [
            ("ORD-1001 환불", "normal", {}),
            ("ORD-1002 환불", "normal", {}),
            ("ORD-1001 조회", "normal", {}),
            ("환불", "normal", {}),
            ("ORD-1003 환불", "normal", {}),
            ("ORD-1001 환불", "flaky", {}),
            ("ORD-1001 환불", "down", {}),
            ("ORD-1001 환불", "lost-response", {"tool_timeout_s": 0.02}),
            ("ORD-1001 환불", "normal", {"max_cost_units": 2}),
            ("ORD-1001 환불", "normal", {"max_steps": 2}),
        ]
        for request, scenario, overrides in cases:
            with self.subTest(request=request, scenario=scenario, policy=overrides):
                outputs = []
                for engine in ("python", "langgraph"):
                    with tempfile.TemporaryDirectory() as temporary:
                        folder = Path(temporary)
                        runtime = Runtime(State(request), Policy(**overrides), folder,
                                          MockAPI(folder / "refunds.sqlite3", scenario))
                        state = await (runtime.run() if engine == "python" else run(runtime))
                        outputs.append({k: v for k, v in asdict(state).items()
                                        if k not in {"run_id", "elapsed_s"}})
                self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
