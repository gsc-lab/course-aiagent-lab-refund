"""LangGraph 기초 9: Command — 노드가 State 갱신분과 다음 노드 이름을 함께 반환한다.

2번과 달라진 점
  2번은 분기 함수 route와 add_conditional_edges(path_map)로 "어디로 갈지"를 그래프 정의 쪽에 적었다.
  이 파일은 노드 evaluate가 Command(update=..., goto=...)를 반환해 노드 안에서 직접 다음 노드를 고른다.
  이 예제에는 별도의 분기 함수·path_map·add_conditional_edges가 필요 없다.
  goto에는 실제 노드 이름을 넣는다. path_map처럼 분기 결과를 노드 이름으로 변환하는 과정은 없다.
  반환 타입 Command[Literal["passed", "retry"]]는 가능한 목적지를 그래프 그리기·검증에 알려 준다.
    그래프에 등록되지 않은 노드 이름을 Literal에 적으면 compile 때 ValueError가 난다.
    실행 중 목적지를 고르는 것은 타입 표기가 아니라 반환값의 goto다.
  update는 각 키의 갱신 규칙(reducer 또는 덮어쓰기)에 따라 State에 반영된다.
    다음 노드는 갱신된 State를 받으므로 passed가 state["verdict"]를 읽을 수 있다.
  Command.goto는 add_edge로 등록한 고정 간선을 취소하지 않는다. 함께 쓰면 두 경로가 모두 실행될 수 있다.
    그래서 이 예제에서는 evaluate에서 나가는 고정 간선을 추가하지 않는다.

어느 쪽을 쓰나
  조건부 간선: 분기 기준이 단순하고, 그래프 정의만 봐도 흐름이 보이게 하고 싶을 때.
  Command    : 하나의 판단 결과로 State를 갱신하고 다음 노드도 정할 때.
               (예: LLM 응답에서 intent를 추출해 State에 기록하고, 해당 intent를 처리할 노드로 보낼 때)
  langgraph_version.py는 조건부 간선을 쓴다. 이 파일에서는 Command를 이용한 분기 방식을 비교한다.

langgraph.types 모듈
  StateGraph·START·END처럼 그래프를 "만드는" 도구는 langgraph.graph에 있다.
  langgraph.types에는 실행 제어·설정·상태 조회에 쓰는 타입과 함수가 모여 있다.
    Command·Send는 실행 제어에, RetryPolicy는 재시도 설정에, StateSnapshot은 상태 조회에 쓴다.
    interrupt는 실행을 일시 중단하는 함수다. 10번에서는 StateSnapshot, 11번에서는 interrupt를 쓴다.
    14번에서는 RetryPolicy, 16번에서는 Send를 쓴다.

실행: uv run python src/refund_agent/langgraph_basics/09_command.py
"""
from typing import Literal, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

# [그래프]
#   START → evaluate ─┬─ goto="passed" (score >= 60) → passed → END
#                     └─ goto="retry"  (score <  60) → retry  → END
#   evaluate에서 나가는 고정 간선은 없다. 다음 노드는 반환값의 goto가 정한다.


# 1. State
class State(TypedDict):
    score: int    # 입력. evaluate가 읽는다.
    verdict: str  # evaluate가 Command.update로 쓴다 ("pass"/"fail").
    message: str  # passed 또는 retry 중 하나가 쓴다.


class StateUpdate(TypedDict, total=False):
    score: int
    verdict: str
    message: str


# 2. Node: 점수를 판단해 State 갱신분과 다음 노드를 함께 반환한다.
# Literal은 가능한 목적지를 명시한다. 2번의 path_map과 달리 이름을 변환하지 않는다.
def evaluate(state: State) -> Command[Literal["passed", "retry"]]:
    if state["score"] >= 60:
        return Command(update={"verdict": "pass"}, goto="passed")
    return Command(update={"verdict": "fail"}, goto="retry")


# 분기 끝의 작업 노드. evaluate가 쓴 verdict가 이미 병합된 뒤 실행된다.
def passed(state: State) -> StateUpdate:
    return {"message": f"[{state['verdict']}] 합격입니다!"}


def retry(state: State) -> StateUpdate:
    return {"message": f"[{state['verdict']}] 다시 도전하세요."}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("evaluate", evaluate)
builder.add_node("passed", passed)
builder.add_node("retry", retry)

builder.add_edge(START, "evaluate")
# evaluate의 다음 노드는 Command.goto로 정하므로 add_edge로 연결하지 않는다.
builder.add_edge("passed", END)
builder.add_edge("retry", END)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# 같은 그래프에 다른 입력을 넣어 두 경로를 모두 지나 본다.
print(cast(State, graph.invoke({"score": 80, "verdict": "", "message": ""})))
# {'score': 80, 'verdict': 'pass', 'message': '[pass] 합격입니다!'}
print(cast(State, graph.invoke({"score": 40, "verdict": "", "message": ""})))
# {'score': 40, 'verdict': 'fail', 'message': '[fail] 다시 도전하세요.'}

# evaluate의 반환 타입을 Command[Literal["passed", "nope"]]로 바꾸고 실행해 보라.
# compile 단계에서 "Found edge ending at unknown node `nope`" ValueError가 난다.
