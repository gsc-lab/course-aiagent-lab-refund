"""LangGraph 기초 7: 병렬 실행(fan-out / fan-in) — 한 노드 뒤에 여러 노드를 동시에 실행한다.

1~6번과 달라진 점
  지금까지는 한 노드 다음에 노드가 하나였다. 여기서는 load 다음에 left·right 두 노드로 갈라지고(fan-out),
  둘이 모두 끝난 뒤 add에서 다시 합쳐진다(fan-in).
  fan-out: 같은 출발 노드에서 add_edge를 두 번 그린다. add_edge("load", "left"), add_edge("load", "right")
  fan-in : 같은 도착 노드로 add_edge를 두 번 그린다. add_edge("left", "add"), add_edge("right", "add")
  left·right는 None을 반환한다. "State를 바꾸지 않는 노드"라는 뜻이며 출력만 한다. 실수가 아니다.
  left·right가 State에 쓰는 키가 없으니 충돌도 없다. 둘이 같은 키에 쓰면 어떻게 되는지는 8번에서 본다.

superstep
  LangGraph는 "지금 실행할 수 있는 노드 묶음"을 한 단계(superstep)로 실행한다.
  이 그래프는 [load] → [left, right] → [add] 세 단계다. 같은 단계 안의 left와 right 중
  무엇이 먼저 출력되는지는 보장되지 않는다. 한 단계가 끝나면 그 단계의 갱신이 모두 State에 병합된다.
  add는 left·right가 같은 단계에서 끝나므로 한 번만 실행된다. 두 경로의 길이가 다르면
  (예: right 뒤에 노드가 하나 더 있다면) add가 두 번 실행된다. 그때는 8번의 리스트 fan-in을 쓴다.

실행: uv run python src/refund_agent/langgraph_basics/07_parallel.py
"""
from typing import TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
#                 ┌─ left  ─┐
#   START → load ─┤         ├─ add → END
#                 └─ right ─┘
#   단계:  [load]  [left, right]  [add]


# 1. State
class State(TypedDict):
    left: int    # load가 쓴다. left가 읽는다.
    right: int   # load가 쓴다. right가 읽는다.
    result: int  # add가 쓴다.


class StateUpdate(TypedDict, total=False):
    left: int
    right: int
    result: int


# 2. Node
def load(state: State) -> StateUpdate:
    return {"left": 2, "right": 3}


# None 반환 = State를 바꾸지 않는다. 읽기만 하는 노드다.
def left(state: State) -> None:
    print(f"left: {state['left']}")


def right(state: State) -> None:
    print(f"right: {state['right']}")


# fan-in 노드: left·right가 모두 끝난 뒤 실행된다.
def add(state: State) -> StateUpdate:
    print("add")
    return {"result": state["left"] + state["right"]}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("load", load)
builder.add_node("left", left)
builder.add_node("right", right)
builder.add_node("add", add)

builder.add_edge(START, "load")
builder.add_edge("load", "left")   # fan-out: load → left
builder.add_edge("load", "right")  #          load → right
builder.add_edge("left", "add")    # fan-in : left  → add
builder.add_edge("right", "add")   #          right → add
builder.add_edge("add", END)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

result: State = cast(State, graph.invoke({"left": 0, "right": 0, "result": 0}))
print(result)
# left: 2          ← left와 right의 순서는 바뀔 수 있다
# right: 3
# add              ← 한 번만 실행된다
# {'left': 2, 'right': 3, 'result': 5}
