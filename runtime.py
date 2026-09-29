"""Execution Policy, Observability, Checkpoint, Execution Loop의 직접 구현.

[설계] Runtime은 업무 노드의 실행과 공통 정책을 관리한다.
다음 네 가지 실행 기능을 한 곳에서 처리한다.
  1) Execution Policy : 재시도·timeout·예산·단계 수·LLM 한도. 실행 전 한도를 확인하고 호출에 시간 제한을 적용한다
  2) Observability    : run_id로 묶인 구조화 로그(.trace.jsonl). 각 단계와 호출의 실행 내역을 기록한다
  3) Checkpoint       : 임시 파일 작성 후 교체 방식으로 State + Policy를 저장. 재개와 담당자 확인에 사용
  4) Execution Loop   : status가 running인 동안 step()을 반복
LangGraph로 바꿔도 1~3은 그대로 남고 4만 프레임워크가 대신한다(langgraph_version.py).

호출 한 번의 공통 골격(analyze_request / call 모두 동일):
  시간 확인 → 한도 확인 → 호출 횟수 선반영 → 체크포인트 → 호출(wait_for) →
  성공: 로그 후 반환 / 일시 오류: 일정 시간 대기 후 재시도 / 그 외: 그대로 전파
"""
import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from llm import ClaudeAnalyzer, MODEL, parse_message
from nodes import NODES, route
from state import Policy, State
from tools import MockAPI, ServiceError, TransientError


class PolicyStop(Exception):
    # [설계] "정책 한도 도달"을 업무 오류(ServiceError)와 구분하는 예외.
    # 둘 다 인계로 끝나지만, 인계 사유에 "예산 초과"와 "권한 없음"이 다르게 남아야 한다.
    pass


def atomic_json(path, value):
    # [설계] 체크포인트는 임시 파일에 내용을 모두 쓴 뒤 기존 파일을 교체한다.
    # 대상 파일을 교체하기 전까지 기존 체크포인트를 유지한다. 전원 장애까지 보장하지는 않는다.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Runtime:
    # [설계] 도구별 가상 비용. 쓰기(create_refund)를 읽기보다 비싸게 매겨
    # "예산이 쓰기 직전에 끊기는" 상황을 실습에서 재현한다.
    PRICES = {"get_order": 1, "get_refund": 1, "create_refund": 3}

    def __init__(self, state: State, policy: Policy, folder: Path, api=None, analyzer=None):
        self.state, self.policy = state, policy
        self.folder = folder.resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        # [설계] 실행 산출물은 모두 run_id로 이름을 붙인다. 로그·체크포인트·인계 파일을
        # 한 실행 단위로 묶어 추적할 수 있다.
        self.checkpoint = self.folder / f"{state.run_id}.checkpoint.json"
        self.trace = self.folder / f"{state.run_id}.trace.jsonl"
        # [설계] API와 LLM 분석기를 생성자 인자로 받는다. 테스트에서는 고정 응답 구현을 전달하고,
        # 실행은 기본값을 쓴다. 노드 코드는 어느 쪽인지 모른다.
        self.api = api or MockAPI(self.folder / "refunds.sqlite3")
        self.analyzer = analyzer or ClaudeAnalyzer()
        self._last_tick = time.monotonic()

    def tick(self):
        # [설계] 실행 시간은 "프로세스가 실제로 돌던 구간"만 누적한다.
        # 입력 대기·종료 후 재개 사이의 시간은 total_timeout_s에 포함하지 않는다.
        now = time.monotonic()
        self.state.elapsed_s += now - self._last_tick
        self._last_tick = now

    def emit(self, event, **data):
        # [설계] Observability의 최소 단위. 모든 이벤트에 run_id·step·node를 자동으로 붙여
        # 나중에 "몇 번째 단계의 어느 노드에서 무슨 일이 있었는지"를 로그만으로 재구성한다.
        # JSON Lines 형식으로 이벤트마다 한 줄을 추가한다. 쓰기 중 중단되면 마지막 줄은 불완전할 수 있다.
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.state.run_id, "step": self.state.steps,
            "node": self.state.current_node, "event": event, **data,
        }
        with self.trace.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save(self):
        # [설계] Checkpoint = State + Policy. 정책까지 함께 저장해야 재개 시 한도가
        # 새 CLI 인자로 바뀌지 않는다. schema_version은 파일 형식이 바뀔 때를 대비한 표식이다.
        self.tick()
        atomic_json(self.checkpoint, {
            "schema_version": 1, "state": asdict(self.state), "policy": asdict(self.policy)
        })
        self.emit("checkpoint", path=self.checkpoint.name)

    def time_left(self):
        # [설계] 전체 실행 시간 검사. 단계 시작·호출 전·재시도 대기 전마다 호출해
        # "남은 시간"을 돌려주고, 없으면 PolicyStop으로 즉시 인계한다.
        self.tick()
        remaining = self.policy.total_timeout_s - self.state.elapsed_s
        if remaining <= 0:
            raise PolicyStop("전체 실행 시간 제한 도달")
        return remaining

    async def analyze_request(self):
        # [설계] LLM 호출의 실행 정책. call()과 같은 골격이되 한도가 따로 있다.
        # LLM은 실제 과금 대상이므로 횟수(max_llm_calls)·입력 크기(max_input_chars)·
        # 출력 크기(llm_max_tokens)·시간(llm_timeout_s)을 도구와 독립적으로 제한한다.
        messages = self.state.messages or [{"role": "user", "content": self.state.request}]
        if sum(len(m["content"]) for m in messages) > self.policy.max_input_chars:
            raise PolicyStop("LLM 입력 길이 제한 도달")
        # 추가 답변 후에는 새로운 분석 단계이므로 새 재시도 묶음으로 센다.
        key = f"analyze.{self.state.steps}.claude"
        while True:
            remaining = self.time_left()
            attempts = self.state.attempts.get(key, 0)
            if attempts >= 1 + self.policy.max_retries:
                raise PolicyStop("Claude 재시도 한도 소진")
            if self.state.llm_calls >= self.policy.max_llm_calls:
                raise PolicyStop("LLM 호출 수 제한 도달")
            # [설계] 호출 횟수를 먼저 늘려 저장한 다음 API를 호출한다.
            # 호출 도중 프로세스가 죽어도 체크포인트에는 "이미 1회 썼다"가 남아
            # 재개 시 한도를 우회하지 못한다. 요금이 발생했을 수 있는 호출은 반드시 센다.
            self.state.llm_calls += 1
            self.state.attempts[key] = attempts + 1
            self.save()
            started = time.monotonic()
            self.emit("llm_start", model=MODEL, attempt=attempts + 1)
            try:
                # [설계] timeout은 "호출당 한도"와 "전체 남은 시간" 중 작은 쪽이다.
                # 전체 한도가 1초 남았는데 호출 한도가 30초라고 30초를 기다리면 안 된다.
                response = await asyncio.wait_for(
                    self.analyzer.generate(messages, self.policy.llm_max_tokens,
                                           self.policy.llm_timeout_s),
                    timeout=min(self.policy.llm_timeout_s, remaining),
                )
                # 거절·형식 오류 응답도 토큰이 소비되었으므로 파싱 전에 기록한다.
                usage = response.usage
                self.state.input_tokens += usage.input_tokens
                self.state.output_tokens += usage.output_tokens
                self.emit("llm_usage", model=MODEL, input_tokens=usage.input_tokens,
                          output_tokens=usage.output_tokens, stop_reason=response.stop_reason)
                self.save()
                # [설계] 검증은 여기서. 실패하면 ServiceError가 위로 올라가 step()에서 인계된다.
                # 형식이 틀린 응답으로 재시도하지 않는 이유는 llm.py 주석 참고.
                result = parse_message(response, messages)
                self.emit("llm_end", model=MODEL, result=result,
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                return result
            except (TransientError, TimeoutError) as exc:
                # [설계] 재시도는 "일시 오류 + timeout"에만. 실패도 로그·체크포인트에 남긴다.
                error = f"{type(exc).__name__}: {exc}"
                self.state.last_error = error
                self.emit("llm_error", error=error,
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                self.save()
                if attempts >= self.policy.max_retries:
                    raise PolicyStop("Claude 재시도 한도 소진") from exc
                # [설계] 재시도 간 대기 시간을 두 배씩 늘린다(지수 백오프). LLM은 최소 0.5초부터 시작한다.
                # 대기 시간이 남은 전체 시간을 넘으면 기다리지 않고 바로 인계한다.
                delay = max(self.policy.backoff_s, 0.5) * (2 ** attempts)
                if delay >= self.time_left():
                    raise PolicyStop("Claude 재시도 대기 중 전체 시간 제한 도달") from exc
                self.emit("retry", tool="claude", next_attempt=attempts + 2, delay_s=delay)
                await asyncio.sleep(delay)
            except Exception as exc:
                # [설계] 그 외 예외(ServiceError, 프로그래밍 오류)는 기록만 하고 그대로 전파.
                # 예외 종류에 따라 재시도를 제한하여 반복해도 해결되지 않는 호출을 중단한다.
                self.emit("llm_error", error=f"{type(exc).__name__}: {exc}",
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                raise

    async def call(self, name, **arguments):
        """재시도는 일시 장애와 timeout에만 적용하고, 호출 전에 예상 비용을 누적 사용량에 반영한다."""
        # [설계] 모든 Tool 호출에 공통 정책을 적용하는 함수. 노드는 runtime.call만 부르므로
        # 정책·로그·체크포인트를 도구마다 다시 구현할 필요가 없다.
        # attempts 키를 "노드.도구"로 두면 체크포인트에 시도 횟수가 남아 재개해도 이어진다.
        key = f"{self.state.current_node}.{name}"
        while True:
            remaining = self.time_left()
            attempts = self.state.attempts.get(key, 0)
            if attempts >= 1 + self.policy.max_retries:
                raise PolicyStop(f"{name}: 재시도 한도 소진")
            price = self.PRICES[name]
            # [설계] 사전 차단. 횟수·예산이 부족하면 호출을 "시작하지 않는다".
            # 호출 후에 초과를 발견하면 이미 환불이 처리된 뒤일 수 있다.
            if self.state.tool_calls >= self.policy.max_tool_calls:
                raise PolicyStop("도구 호출 수 제한 도달")
            if self.state.cost_units + price > self.policy.max_cost_units:
                raise PolicyStop("가상 비용 예산 초과")
            self.state.tool_calls += 1
            self.state.cost_units += price
            self.state.attempts[key] = attempts + 1
            # 프로세스가 도중에 끝나도 미리 반영한 호출 횟수와 비용을 체크포인트에 남긴다.
            self.save()
            started = time.monotonic()
            self.emit("tool_start", tool=name, attempt=attempts + 1, arguments=arguments,
                      cost_units=price, total_cost_units=self.state.cost_units)
            try:
                # [설계] wait_for가 실제로 코루틴을 취소한다. slow 시나리오의 60초 sleep이
                # 0.2초 만에 TimeoutError로 바뀌는 이유. 단, 취소는 "클라이언트가 기다리기를
                # 그만둔 것"이지 서버 처리가 취소된 것이 아니다(lost-response 참고).
                result = await asyncio.wait_for(
                    getattr(self.api, name)(**arguments),
                    timeout=min(self.policy.tool_timeout_s, remaining),
                )
            except (TransientError, TimeoutError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.state.last_error = error
                self.emit("tool_error", tool=name, error=error,
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                self.save()
                if self.state.attempts[key] >= 1 + self.policy.max_retries:
                    raise PolicyStop(f"{name}: 재시도 한도 소진 ({error})") from exc
                delay = self.policy.backoff_s * (2 ** attempts)
                if delay >= self.time_left():
                    raise PolicyStop("재시도 대기 중 전체 시간 제한 도달") from exc
                self.emit("retry", tool=name, next_attempt=attempts + 2, delay_s=delay)
                await asyncio.sleep(delay)
            except Exception as exc:
                # [설계] ServiceError(권한·규칙 위반)는 재시도하지 않는다. 같은 인자로
                # 다시 불러도 같은 답이고, 재시도는 비용만 늘린다.
                self.emit("tool_error", tool=name, error=f"{type(exc).__name__}: {exc}",
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                raise
            else:
                self.emit("tool_end", tool=name, result=result,
                          latency_ms=round((time.monotonic() - started) * 1000, 3))
                return result

    def escalate(self, reason):
        # [설계] Escalation(인계)은 "실패"가 아니라 정해진 종료 상태다.
        # 자동 처리를 계속할 수 없을 때 (1) 고객에게 단정하지 않는 답을 주고
        # (2) 담당자가 이어받을 수 있게 사유·마지막 노드·State 전체를 파일로 남긴다.
        # 응답 유실 후 인계처럼 "환불됐을 수도 있는" 상황에서는 next_action이 핵심이다.
        state = self.state
        state.status = "escalated"
        state.last_error = reason
        state.response = "자동 처리를 중단했습니다. 담당자 확인이 필요합니다."
        # 실제 상담 시스템 전송이 아니라 로컬 인계 파일 생성이다.
        ticket = {
            "run_id": state.run_id, "order_id": state.order_id, "reason": reason,
            "last_node": state.current_node, "refund": state.refund,
            "next_action": "환불 기록 DB(refunds.sqlite3)에서 처리 여부를 확인하고 고객에게 안내",
            "state": asdict(state),
        }
        atomic_json(self.folder / f"{state.run_id}.handoff.json", ticket)
        self.emit("escalation", reason=reason)
        state.current_node = "end"

    async def step(self, expected_node=None):
        """한 단계: 한도 검사 → 노드 실행 → 다음 노드 선택 → 상태 변경 기록 → 저장."""
        # [설계] "한 단계"의 정의. 노드 실행부터 다음 노드 선택·저장까지 처리하며,
        # Python 루프와 LangGraph 노드 래퍼가 똑같이 이 함수를 부른다.
        state = self.state
        if state.status != "running":
            return
        before = asdict(state)
        node = state.current_node
        started = time.monotonic()
        outcome = "ok"
        try:
            # [설계] 노드를 실행하기 전에 시간·단계 한도를 검사한다.
            self.time_left()
            if state.steps >= self.policy.max_steps:
                raise PolicyStop("최대 단계 수 도달")
            if expected_node is not None and node != expected_node:
                raise ValueError(f"노드 불일치: {node} / {expected_node}")
            state.steps += 1
            self.emit("step_start")
            await NODES[node](state, self)  # node: 업무 실행
            state.current_node = route(state, node)  # routing: 다음 노드 결정
        except (PolicyStop, ServiceError) as exc:
            # [설계] 예상된 중단(정책 한도, 업무 오류)은 인계로 끝낸다.
            outcome = "escalated"
            self.escalate(str(exc))
        except Exception as exc:
            # [설계] 예상하지 못한 오류도 프로세스를 죽이지 않고 인계한다.
            # 오류 사유를 남긴 뒤 체크포인트 저장을 시도한다. 파일 저장 자체의 실패는 별도로 전파된다.
            outcome = "error"
            self.emit("error", error=f"{type(exc).__name__}: {exc}")
            self.escalate(f"예상하지 못한 오류: {type(exc).__name__}: {exc}")
        self.tick()
        after = asdict(state)
        self.emit("step_end", executed_node=node, outcome=outcome,
                  latency_ms=round((time.monotonic() - started) * 1000, 3))
        # [설계] state diff. 노드가 State의 어떤 필드를 바꿨는지만 기록한다.
        # 업무 결과뿐 아니라 실행 중 바뀐 상태와 사용량도 함께 확인할 수 있다.
        self.emit("state_change", changes={
            k: {"before": before[k], "after": v} for k, v in after.items() if before[k] != v
        })
        self.save()  # 매 단계 끝에 체크포인트. 다음 단계부터 재개 가능

    async def run(self, pause_after=None):
        # 이것이 순수 Python Execution Loop다.
        # [설계] basic.py의 while과 비교하라. 종료 조건이 "current_node != end"가 아니라
        # "status == running"이다. 대기·완료·인계가 모두 같은 조건으로 루프를 멈춘다.
        # pause_after는 지정한 단계까지 완료·저장한 뒤 중단하여 재개를 실습하는 옵션이다.
        self._last_tick = time.monotonic()
        self.emit("run_start")
        self.save()
        executed = 0
        while self.state.status == "running":
            await self.step()
            executed += 1
            if pause_after is not None and executed >= pause_after:
                self.emit("paused", reason="학습용 단계 중단")
                break
        self.emit("run_end", status=self.state.status, elapsed_s=self.state.elapsed_s,
                  tool_calls=self.state.tool_calls, cost_units=self.state.cost_units,
                  llm_calls=self.state.llm_calls, input_tokens=self.state.input_tokens,
                  output_tokens=self.state.output_tokens, actual_cost_krw=None)
        return self.state


def load_checkpoint(path: Path):
    # [설계] 재개의 입구. 버전을 확인한 뒤 State와 Policy를 함께 복원한다.
    # 재개 후에는 이 두 값으로 새 Runtime을 만들면 current_node부터 이어서 실행된다.
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("지원하지 않는 체크포인트 버전")
    return State(**value["state"]), Policy(**value["policy"])
