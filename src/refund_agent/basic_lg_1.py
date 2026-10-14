from typing import TypedDict, cast
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

#  그래프
# ┌──────────────────────────────────────┐
# │   START → greet → emphasize → END    │
# └──────────────────────────────────────┘

# 1. State: 단계들이 공유하는 데이터
class State(TypedDict):
    name: str
    message: str


# 노드 반환값은 전체 State가 아니라 변경할 필드만 담는 부분 갱신이다.
class StateUpdate(TypedDict, total=False):
    name: str
    message: str


# 2. Node: 상태를 받아 변경할 값만 반환하는 함수
def greet(state: State) -> StateUpdate:
    return {"message": f"{state['name']}님, 안녕하세요!"}


def emphasize(state: State) -> StateUpdate:
    return {"message": state["message"] + " 반갑습니다."}


# 3. Graph: 노드를 등록하고 Edge로 실행 순서를 연결
builder: StateGraph[State] = StateGraph(State)

builder.add_node("greet", greet)
builder.add_node("emphasize", emphasize)

builder.add_edge(START, "greet")
builder.add_edge("greet", "emphasize")
builder.add_edge("emphasize", END)

# 4. 실행 가능한 그래프로 변환하고 실행
graph: CompiledStateGraph[State] = builder.compile()

# invoke: 초기 State → 노드별 StateUpdate 병합 → 최종 State
initial_state: State = {"name": "민수", "message": ""}
result: State = cast(State, graph.invoke(initial_state))
print(result)
