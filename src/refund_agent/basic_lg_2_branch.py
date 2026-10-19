"""LangGraph 기초 2: 조건부 분기 — State 값에 따라 다른 노드로 간다.

1번과 달라진 점
  add_edge(고정 연결) 대신 add_conditional_edges(조건부 연결)를 쓴다.
  분기 함수 route는 노드가 아니다. State를 바꾸지 않고 "어디로 갈지" 이름만 돌려준다.
  route의 반환값("pass"/"retry")과 노드 이름("passed"/"retry")은 다른 것이다.
  둘을 이어 주는 것이 path_map 딕셔너리다.

실행: uv run python src/refund_agent/basic_lg_2_branch.py
"""
from typing import Literal, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
#   START → (route) ─┬─ "pass"  (score >= 60) → passed → END
#                    └─ "retry" (score <  60) → retry  → END


# 1. State
class State(TypedDict):
    score: int    # 입력. route가 읽는다.
    message: str  # passed 또는 retry 중 하나가 쓴다.


class StateUpdate(TypedDict, total=False):
    score: int
    message: str


# Routing: 다음에 갈 경로의 이름만 반환한다. State를 바꾸지 않는다.
# 반환 타입을 Literal로 적어 두면 path_map의 키와 맞는지 눈으로 확인하기 쉽다.
def route(state: State) -> Literal["pass", "retry"]:
    return "pass" if state["score"] >= 60 else "retry"


# 2. Node: 분기 끝에서 실행되는 작업 노드
def passed(state: State) -> StateUpdate:
    return {"message": "합격입니다!"}


def retry(state: State) -> StateUpdate:
    return {"message": "다시 도전하세요."}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("passed", passed)
builder.add_node("retry", retry)

# add_conditional_edges(출발 노드, 분기 함수, {분기 함수 반환값: 다음 노드 이름})
# 출발 노드가 START이므로 실행 첫 단계에서 바로 분기한다.
builder.add_conditional_edges(
    START,
    route,
    {"pass": "passed", "retry": "retry"},
)

builder.add_edge("passed", END)
builder.add_edge("retry", END)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# 같은 그래프에 다른 입력을 넣어 두 경로를 모두 지나 본다.
print(cast(State, graph.invoke({"score": 80, "message": ""})))
# {'score': 80, 'message': '합격입니다!'}
print(cast(State, graph.invoke({"score": 40, "message": ""})))
# {'score': 40, 'message': '다시 도전하세요.'}
