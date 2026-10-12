"""실행: uv run refund-agent 'ORD-1001 환불해 주세요'

[설계] main.py는 입출력 경계(어댑터)다. CLI 입력을 검증해 State와 Policy로 바꾸는 일만 하고,
업무 규칙과 실행 정책은 Runtime과 노드가 담당한다. 입력 경로는 네 가지다.
  - 새 실행        : 요청 문자열과 정책 인자로 State와 Policy를 새로 만든다.
  - 재개(--resume) : 체크포인트에서 State와 Policy를 복원한다. 정책은 새 인자로 덮어쓰지 않는다.
  - 답변(--reply)  : waiting_input 상태에서만 허용한다. 답변을 대화 이력(messages)에 추가하고 running으로 되돌린다.
  - 엔진(--engine) : 같은 Runtime과 노드를 Python 루프 또는 LangGraph로 실행한다.
CLI를 웹 API나 메시징 봇으로 바꾸더라도 Runtime 이하는 수정 없이 재사용할 수 있어야 한다.
"""
import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from refund_agent.runtime import Runtime, load_checkpoint
from refund_agent.state import Policy, State
from refund_agent.tools import MockAPI


def parser():
    result = argparse.ArgumentParser(description="주문·환불 Agent 실습 - Claude + 로컬 모의 주문 API")
    result.add_argument("request", nargs="?", help="고객 요청 (예: ORD-1001 환불해 주세요)")
    result.add_argument("--output", type=Path, default=Path("artifacts"), help="로그·체크포인트·환불 기록 DB 저장 폴더")
    result.add_argument("--resume", type=Path, help="저장한 체크포인트 파일")
    result.add_argument("--reply", help="waiting_input 상태에 대한 추가 답변")
    # [설계] --scenario는 주문 API의 장애를 재현하는 옵션이고, 그 아래 인자들은 Policy의 한도다.
    # 장애와 정책을 따로 지정할 수 있으므로 같은 장애에서 정책만 바꿔 결과를 비교할 수 있다.
    result.add_argument("--scenario", choices=["normal", "flaky", "down", "slow", "lost-response"], default="normal", help="모의 API 시나리오: 정상 / 일시 장애 / 지속 장애 / 지연 / 환불 후 응답 유실")
    result.add_argument("--pause-after", type=int, help="지정한 수의 노드 실행 후 저장·중단; --resume으로 재개 (python 엔진 전용)")
    result.add_argument("--engine", choices=["python", "langgraph"], default="python", help="실행 엔진 (기본: python)")
    result.add_argument("--max-steps", type=int, default=12, help="최대 노드 실행 횟수 (기본: 12)")
    result.add_argument("--max-tool-calls", type=int, default=10, help="재시도 포함 최대 주문 API 호출 횟수 (기본: 10)")
    result.add_argument("--budget", type=int, default=20, help="주문 API 가상 비용 한도; 실제 LLM 요금과 별개 (기본: 20)")
    result.add_argument("--retries", type=int, default=2, help="최초 호출 이후 추가 재시도 횟수 (기본: 2)")
    result.add_argument("--tool-timeout", type=float, default=0.2, help="주문 API 호출당 제한 시간, 초 (기본: 0.2)")
    result.add_argument("--total-timeout", type=float, default=120.0, help="누적 실행 시간 한도, 초; 입력 대기 제외 (기본: 120)")
    result.add_argument("--llm-timeout", type=float, default=30.0, help="LLM 호출당 제한 시간, 초 (기본: 30)")
    result.add_argument("--max-llm-calls", type=int, default=6, help="재시도 포함 최대 LLM 호출 횟수 (기본: 6)")
    return result


async def main():
    cli = parser()
    args = cli.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        cli.error("ANTHROPIC_API_KEY 환경 변수를 설정하세요. README.md를 참고하세요.")
    if args.pause_after is not None and args.pause_after < 1:
        cli.error("--pause-after는 1 이상이어야 합니다.")
    if args.engine == "langgraph" and args.pause_after is not None:
        cli.error("--pause-after 실습은 python 엔진에서 지원합니다.")
    if args.reply and not args.resume:
        cli.error("--reply에는 --resume이 필요합니다.")
    if args.resume and args.request:
        cli.error("재개 시에는 새 request 대신 --reply를 사용하세요.")
    if args.resume:
        # [설계] 재개 경로. State와 Policy는 체크포인트에서만 가져온다.
        # 산출물 폴더도 체크포인트가 있는 폴더로 정해 환불 기록 DB(refunds.sqlite3)를 같은 곳에서 쓴다.
        state, policy = load_checkpoint(args.resume)
        folder = args.resume.resolve().parent
        if args.reply:
            # [설계] Human-in-the-loop의 답변 처리. 질문은 nodes.analyze가 status를 waiting_input으로
            # 바꾸면서 response에 남기고, 그 답변은 여기서 받는다. 답변을 대화 이력에 추가하고
            # status를 running으로 되돌리면 멈췄던 analyze 노드부터 다시 실행된다.
            # 이전 response를 비우지 않으면 지난 질문이 최종 응답으로 남는다.
            if state.status != "waiting_input":
                cli.error("--reply는 waiting_input 상태에서만 사용할 수 있습니다.")
            if not state.messages:
                state.messages = [{"role": "user", "content": state.request}]
            state.messages.append({"role": "user", "content": args.reply})
            state.request += "\n" + args.reply
            state.status = "running"
            state.response = ""
        elif state.status == "waiting_input":
            cli.error("추가 정보가 필요합니다. --reply로 답변하세요.")
    else:
        if not args.request:
            cli.error("사용자 요청 또는 --resume을 지정하세요.")
        # [설계] 새 실행 경로. 정책은 여기서 한 번만 정해지고 체크포인트에 State와 함께 저장된다.
        state, folder = State(request=args.request), args.output
        try:
            policy = Policy(max_steps=args.max_steps, max_tool_calls=args.max_tool_calls,
                            max_cost_units=args.budget, max_retries=args.retries,
                            tool_timeout_s=args.tool_timeout, total_timeout_s=args.total_timeout,
                            llm_timeout_s=args.llm_timeout, max_llm_calls=args.max_llm_calls)
        except ValueError as exc:
            cli.error(str(exc))
    # [설계] 조립 지점. State, Policy, 산출물 폴더, 주문 API(환불 기록 DB와 장애 시나리오 포함)를
    # Runtime 생성자에 전달한다. 어느 경로로 왔든 이 아래는 같은 코드다.
    runtime = Runtime(state, policy, folder, MockAPI(folder / "refunds.sqlite3", args.scenario))
    if args.engine == "langgraph":
        from refund_agent.langgraph_version import run
        final = await run(runtime)
    else:
        final = await runtime.run(args.pause_after)
    # [설계] 출력은 고객 응답, 실행 지표, 산출물 위치의 세 부분이다. 응답만 보고 판단하지 말고
    # 로그와 체크포인트를 근거로 설명한다는 실습 원칙을 출력 형식에도 반영한 것이다.
    print(final.response or "체크포인트에 저장했습니다. --resume으로 계속 실행하세요.")
    status_label = {
        "running": "중단됨 · 재개 가능", "waiting_input": "추가 입력 대기",
        "completed": "고객 안내 완료", "escalated": "담당자 확인 필요",
    }.get(final.status, final.status)
    print(f"실행 상태: {status_label} ({final.status}) / 실행 단계: {final.steps}회")
    print(f"주문 API 호출: {final.tool_calls}회 / 가상 비용: {final.cost_units}단위")
    print(f"LLM 호출: {final.llm_calls}회 / 입력 토큰: {final.input_tokens} / 출력 토큰: {final.output_tokens}")
    print("LLM 실제 요금은 Anthropic 사용량에서 확인하세요. 응답 유실 시 토큰 기록이 누락될 수 있습니다.")
    print(f"체크포인트: {runtime.checkpoint}")
    print(f"실행 로그: {runtime.trace}")
    print(f"환불 기록 DB: {runtime.folder / 'refunds.sqlite3'}")
    if final.status == "escalated":
        print(f"중단 사유: {final.last_error}")
        print(f"담당자 확인 요청 파일: {runtime.folder / (final.run_id + '.handoff.json')}")


def cli():
    load_dotenv()  # 프로젝트 폴더의 .env에서 ANTHROPIC_API_KEY를 읽는다. 이미 설정된 셸 환경 변수가 우선한다.
    asyncio.run(main())


if __name__ == "__main__":
    cli()

