"""LangGraph 기초 6: 사용자 정의 Reducer — operator.add 대신 직접 만든 함수로 병합한다.

4·5번과 달라진 점
  reducer 자리에는 어떤 함수든 넣을 수 있다. 조건은 "(기존 값, 새 값) → 병합 결과" 모양 하나다.
  operator.add(a, b)도 그 모양이라서 4번에서 바로 쓸 수 있었던 것이다.
  여기서는 int 필드에 직접 만든 add_score를 붙이고, 호출될 때마다 print로 인자를 보여 준다.
  그래프는 분기 없이 START → midterm → final → END다. 반복이 없는데도 값이 누적된다.

실행 결과에서 확인할 것
  노드는 2개인데 add_score는 3번 호출된다.
  첫 호출 add_score(0, 0)은 invoke의 초기 입력을 병합할 때 일어난다.
  초기 입력도 "빈 State에 대한 갱신"으로 취급되어 reducer를 거치기 때문이다.
  (빈 값은 int 필드면 0, list 필드면 []다. 4번에서 history: []를 넣었을 때도 [] + []가 일어났다.)

실행: uv run python src/refund_agent/langgraph_basics/06_custom_reducer.py
"""
from typing import Annotated, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
# ┌──────────────────────────────────────┐
# │   START → midterm → final → END      │
# └──────────────────────────────────────┘


# Reducer: 기존 값과 새 값을 받아 병합 결과를 돌려주는 함수. 이름과 내용은 자유다.
def add_score(current: int, new: int) -> int:
    print(f"add_score(기존={current}, 새 값={new})")
    return current + new


# 1. State
class State(TypedDict):
    score: Annotated[int, add_score]  # 누적: add_score(기존, 새 값)


class StateUpdate(TypedDict, total=False):
    score: int  # 노드는 "더할 점수"만 반환한다


# 2. Node: 각 시험의 점수를 반환한다. 합산은 reducer가 한다.
def midterm(state: State) -> StateUpdate:
    return {"score": 20}


def final(state: State) -> StateUpdate:
    return {"score": 30}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("midterm", midterm)
builder.add_node("final", final)

builder.add_edge(START, "midterm")
builder.add_edge("midterm", "final")
builder.add_edge("final", END)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# reducer가 없었다면 score는 마지막 값 30만 남는다.
result: State = cast(State, graph.invoke({"score": 0}))
print(result)
# add_score(기존=0, 새 값=0)    ← 초기 입력 병합
# add_score(기존=0, 새 값=20)   ← midterm
# add_score(기존=20, 새 값=30)  ← final
# {'score': 50}
