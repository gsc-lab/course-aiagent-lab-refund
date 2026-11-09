"""LangGraph 기초 14: RetryPolicy — 노드가 예외를 던지면 LangGraph가 그 노드를 다시 실행한다.

1~13번과 달라진 점
  지금까지는 노드가 예외를 던지면 invoke가 그 예외로 바로 끝났다.
  add_node(..., retry_policy=RetryPolicy(...))를 주면 실패한 노드를 정해진 횟수만큼 다시 실행한다.
  재시도 규칙은 노드마다 따로 붙인다. 이 파일은 fetch에만 붙였고 report에는 없다.
  RetryPolicy의 필드
    max_attempts    : 첫 시도를 포함한 전체 시도 횟수. 3이면 재시도는 최대 2번이다. (기본 3)
    initial_interval: 첫 재시도 전 대기 시간(초). (기본 0.5)
    backoff_factor  : 재시도할 때마다 대기 시간에 곱하는 값. 2.0이면 0.2초 → 0.4초 → 0.8초. (기본 2.0)
    max_interval    : 대기 시간의 상한(초). (기본 128)
    jitter          : 대기 시간에 무작위 값을 더할지 여부. (기본 True. 이 예제는 시간을 보여 주려고 False)
    retry_on        : 재시도할 예외. 예외 클래스, 클래스 튜플, 또는 (예외 → bool) 함수.

실행 결과에서 확인할 것
  재시도는 노드를 처음부터 다시 실행한다. 실패한 시도의 반환값은 없으므로 State는 바뀌지 않는다.
    그래서 시도 횟수를 State에 셀 수 없다. 이 예제는 그래프 밖의 변수 attempts에 센다.
  앞에서 성공한 노드는 다시 실행하지 않는다. 재시도 대상은 실패한 노드 하나다.
  max_attempts를 다 쓰면 마지막 예외가 invoke 밖으로 전파된다. 호출자가 try/except로 처리한다.
  retry_on에 해당하지 않는 예외는 재시도 없이 바로 전파된다. 잘못된 입력은 다시 해도 실패하기 때문이다.

retry_on을 생략하면
  기본 규칙은 ConnectionError와 5xx HTTP 오류를 재시도하고, ValueError·TypeError·RuntimeError·OSError 같은
  내장 예외는 재시도하지 않는다. 직접 만든 예외 클래스는 재시도한다.
  TimeoutError는 OSError의 하위 클래스라서 기본 규칙으로는 재시도하지 않는다.
    timeout도 재시도하려면 retry_on=(TransientError, TimeoutError)처럼 직접 적는다.

재시도할 때 주의할 점
  노드가 처음부터 다시 실행되므로, 예외 전에 실행된 API 호출·DB 쓰기는 반복된다(11번의 재개와 같은 문제).
  환불처럼 외부 상태를 바꾸는 노드에 재시도를 붙이려면 중복 처리 방지가 필요하다.

왜 필요한가
  runtime.py의 Runtime.call은 TransientError·TimeoutError일 때만 대기 시간을 두 배씩 늘리며 재시도하고,
  그 외 예외는 그대로 전파한다(3단계 Execution Policy). RetryPolicy는 그 while/except를 설정값으로 바꾼 것이다.
  runtime.py는 재시도를 "호출 한 번" 단위로 하고 시도마다 로그·체크포인트·예산 검사를 한다.
  RetryPolicy는 "노드 전체" 단위로 다시 실행하며 예산·전체 시간 한도는 확인하지 않는다.
  langgraph_version.py는 Runtime.step 안의 재시도를 그대로 쓰므로 RetryPolicy를 붙이지 않는다.

실행: uv run python src/refund_agent/langgraph_basics/14_retry.py
"""
import time
from typing import TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

# [그래프]
#   START → fetch → report → END
#            └─ 재시도: TransientError면 0.2초, 0.4초 뒤 fetch를 처음부터 다시 실행 (최대 3번 시도)


# 일시 오류: 잠시 뒤 다시 하면 성공할 수 있는 실패. tools.py의 TransientError와 같은 역할이다.
class TransientError(Exception):
    pass


# 1. State
class State(TypedDict):
    order_id: str    # 입력. 비어 있으면 fetch가 ValueError를 던진다.
    fail_times: int  # 입력. fetch가 처음 몇 번 실패할지 정한다(실습용).
    status: str      # fetch가 쓴다.
    message: str     # report가 쓴다.


class StateUpdate(TypedDict, total=False):
    status: str
    message: str


# 시도 횟수와 시작 시각. 실패한 시도는 State를 갱신하지 못하므로 그래프 밖에 둔다.
attempts: int = 0
started: float = 0.0


# 2. Node: 주문 조회를 흉내 낸다. 처음 fail_times번은 일시 오류로 실패한다.
def fetch(state: State) -> StateUpdate:
    global attempts
    attempts += 1
    print(f"fetch 시도 {attempts} ({time.perf_counter() - started:.1f}초)")
    if not state["order_id"]:
        raise ValueError("주문번호가 없습니다")  # 다시 해도 실패한다 → 재시도 대상이 아니다
    if attempts <= state["fail_times"]:
        raise TransientError("주문 API 일시 오류")  # 다시 하면 될 수 있다 → 재시도 대상
    return {"status": "delivered"}


def report(state: State) -> StateUpdate:
    print("report 실행")
    return {"message": f"{state['order_id']}: {state['status']}"}


# 3. Graph: 재시도 규칙은 add_node에서 노드마다 붙인다.
builder: StateGraph[State] = StateGraph(State)

builder.add_node(
    "fetch",
    fetch,
    retry_policy=RetryPolicy(
        max_attempts=3,            # 첫 시도 포함 3번
        initial_interval=0.2,      # 첫 재시도 전 0.2초
        backoff_factor=2.0,        # 그다음은 0.4초
        jitter=False,              # 대기 시간을 일정하게 (실습용)
        retry_on=TransientError,   # 이 예외일 때만 재시도
    ),
)
builder.add_node("report", report)  # 재시도 규칙 없음

builder.add_edge(START, "fetch")
builder.add_edge("fetch", "report")
builder.add_edge("report", END)

graph: CompiledStateGraph[State] = builder.compile()


# 4. 실행: 세 가지 경우를 차례로 본다.
def run(title: str, order_id: str, fail_times: int) -> None:
    global attempts, started
    attempts, started = 0, time.perf_counter()
    print(f"[{title}]")
    try:
        result: State = cast(State, graph.invoke(
            {"order_id": order_id, "fail_times": fail_times, "status": "", "message": ""}
        ))
        print(result)
    except Exception as exc:  # 재시도로 해결되지 않은 예외는 invoke 밖으로 나온다
        print(f"예외 전파: {type(exc).__name__}: {exc}")


run("두 번 실패 후 성공", "ORD-1001", fail_times=2)
# [두 번 실패 후 성공]
# fetch 시도 1 (0.0초)
# fetch 시도 2 (0.2초)    ← 0.2초 대기 후 재시도
# fetch 시도 3 (0.6초)    ← 0.4초 대기 후 재시도. 성공
# report 실행             ← 한 번만 실행된다
# {'order_id': 'ORD-1001', 'fail_times': 2, 'status': 'delivered', 'message': 'ORD-1001: delivered'}

run("계속 실패", "ORD-1001", fail_times=5)
# [계속 실패]
# fetch 시도 1 (0.0초)
# fetch 시도 2 (0.2초)
# fetch 시도 3 (0.6초)    ← max_attempts=3을 다 썼다
# 예외 전파: TransientError: 주문 API 일시 오류

run("재시도 대상이 아닌 예외", "", fail_times=0)
# [재시도 대상이 아닌 예외]
# fetch 시도 1 (0.0초)    ← 한 번만 시도한다
# 예외 전파: ValueError: 주문번호가 없습니다

# retry_on=TransientError 줄을 지우고 실행해 보라. 직접 만든 예외 TransientError는 기본 규칙으로도 재시도하고,
# ValueError는 기본 규칙에서도 재시도하지 않으므로 결과가 같다.
# retry_policy 인자 전체를 지우고 실행해 보라. 첫 경우부터 fetch 시도 1에서 TransientError로 끝난다.
