"""LangGraph 기초 5: dataclass State — TypedDict 대신 dataclass로 State를 선언한다.

4번과 같은 그래프다. State를 선언하는 방식만 바꿨다.
  TypedDict는 dict라서 state["score"]로 읽는다. dataclass는 객체라서 state.score로 읽는다.
  노드 반환값은 그대로 dict(부분 갱신)다. State 객체를 새로 만들어 돌려주지 않는다.
  reducer(Annotated[..., add])는 dataclass 필드에도 똑같이 적용된다.
  invoke에 State(...) 인스턴스를 넘길 수 있다. 단, 결과는 dict로 돌아온다.
  state.py의 State가 dataclass이므로, 이 형태가 실제 Agent 코드(langgraph_version.py)와 가장 가깝다.

실행: uv run python src/refund_agent/basic_lg_5_dataclass.py
"""
from dataclasses import dataclass
from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프] 3·4번과 같다.
#   START → study → (route) ─┬─ "again" (score <  60) → study  (다시 실행)
#                            └─ "done"  (score >= 60) → END


# 1. State: dataclass. 필드 선언과 reducer 표기는 TypedDict와 같다.
@dataclass
class State:
    score: int
    history: Annotated[list[int], add]  # 누적


# 노드 반환값은 여전히 dict다. dataclass 인스턴스를 반환하지 않는다.
class StateUpdate(TypedDict, total=False):
    score: int
    history: list[int]


# 2. Node: 속성 접근(state.score)으로 읽는다
def study(state: State) -> StateUpdate:
    new_score = state.score + 20
    return {"score": new_score, "history": [new_score]}


# Routing
def route(state: State) -> Literal["again", "done"]:
    return "again" if state.score < 60 else "done"


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

# 입력은 State 인스턴스, 출력은 dict. 노드 안에서만 dataclass로 보인다.
result: dict[str, Any] = graph.invoke(State(score=10, history=[]))
print(result)
# {'score': 70, 'history': [30, 50, 70]}
