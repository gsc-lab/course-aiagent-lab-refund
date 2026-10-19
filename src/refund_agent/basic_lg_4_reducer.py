"""LangGraph 기초 4: Reducer — 필드를 덮어쓰지 않고 누적한다.

3번과 달라진 점
  State에 history 필드를 추가하고 Annotated[list[int], operator.add]로 선언했다.
  기본 병합 규칙은 "덮어쓰기"다. 노드가 {"score": 30}을 반환하면 score는 30으로 바뀐다.
  Annotated의 두 번째 인자(reducer)를 붙이면 "기존 값 (reducer) 새 값"으로 병합된다.
  operator.add이므로 기존 리스트 + 새 리스트. 노드는 이번에 추가할 항목만 반환하면 된다.
  대화 메시지처럼 실행마다 쌓여야 하는 데이터는 이 방식으로 선언한다.

실행: uv run python src/refund_agent/basic_lg_4_reducer.py
"""
import operator
from typing import Annotated, Literal, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프] 3번과 같다.
#   START → study → (route) ─┬─ "again" (score <  60) → study  (다시 실행)
#                            └─ "done"  (score >= 60) → END


# 1. State
class State(TypedDict):
    score: int                                  # 덮어쓰기 (기본)
    history: Annotated[list[int], operator.add]  # 누적: 기존 + 새 값


class StateUpdate(TypedDict, total=False):
    score: int
    history: list[int]  # 노드는 "추가할 항목"만 담은 리스트를 반환한다


# 2. Node
def study(state: State) -> StateUpdate:
    new_score = state["score"] + 20
    return {
        "score": new_score,     # 그대로 덮어쓴다
        "history": [new_score],  # [new_score]가 기존 history 뒤에 이어진다
    }


# Routing
def route(state: State) -> Literal["again", "done"]:
    return "again" if state["score"] < 60 else "done"


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("study", study)
builder.add_edge(START, "study")
builder.add_conditional_edges(
    "study",
    route,
    {"again": "study", "done": END},
)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# reducer가 없었다면 history는 마지막 값 [70]만 남는다.
result: State = cast(State, graph.invoke({"score": 10, "history": []}))
print(result)
# {'score': 70, 'history': [30, 50, 70]}
