"""LangGraph 기초 3: 반복 — 조건이 맞을 때까지 같은 노드를 다시 실행한다.

2번과 달라진 점
  조건부 연결의 목적지 중 하나가 출발 노드 자신(study)이다. 이것이 LangGraph의 루프다.
  route는 study가 반환한 갱신이 State에 병합된 "뒤"에 호출된다. 그래서 새 점수로 판단한다.
  basic.py의 while 루프가 하던 "노드 실행 → 다음 노드 결정 → 반복"을 그래프가 대신한다.

주의: route가 계속 "again"만 돌려주면 끝나지 않는다. LangGraph는 recursion_limit(기본 25단계)을
      넘으면 GraphRecursionError를 던져 무한 루프를 막는다.

실행: uv run python src/refund_agent/basic_lg_3_loop.py
"""
from typing import Literal, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
#   START → study → (route) ─┬─ "again" (score <  60) → study  (다시 실행)
#                            └─ "done"  (score >= 60) → END


# 1. State
class State(TypedDict):
    score: int  # study가 20씩 올린다. route가 읽는다.


class StateUpdate(TypedDict, total=False):
    score: int


# 2. Node: 현재 점수에서 20점 증가
def study(state: State) -> StateUpdate:
    new_score = state["score"] + 20
    print(f"공부 후 점수: {new_score}")
    return {"score": new_score}


# Routing: 갱신된 State를 보고 다음 경로 결정
def route(state: State) -> Literal["again", "done"]:
    return "again" if state["score"] < 60 else "done"


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("study", study)
builder.add_edge(START, "study")

builder.add_conditional_edges(
    "study",
    route,
    {
        "again": "study",  # 같은 노드로 돌아간다 → 루프
        "done": END,       # 그래프 실행 종료
    },
)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# 10 → 30 → 50 → 70. study가 3번 실행되고 70에서 "done"으로 빠져나온다.
result: State = cast(State, graph.invoke({"score": 10}))
print("최종 상태:", result)
# 최종 상태: {'score': 70}
