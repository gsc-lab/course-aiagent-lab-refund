"""LangGraph 기초 1: 순차 실행 — 노드 두 개를 Edge로 잇는다.

다섯 예제 모두 State / Node / Graph / 실행, 네 부분을 같은 순서로 적는다.
이 파일은 분기 없이 START → greet → emphasize → END 한 줄로 흐른다.

  State  : 노드들이 공유하는 데이터. TypedDict로 키와 타입을 선언한다.
  Node   : State를 받아 "바꿀 필드만" dict로 반환하는 함수. 전체 State를 돌려주지 않는다.
  Edge   : 어떤 노드 다음에 어떤 노드를 실행할지 고정으로 연결한다.
  invoke : 초기 State를 넣으면 노드를 차례로 실행하고 최종 State를 돌려준다.

실행: uv run python src/refund_agent/basic_lg_1_sequence.py
"""
from typing import TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
# ┌──────────────────────────────────────┐
# │   START → greet → emphasize → END    │
# └──────────────────────────────────────┘


# 1. State: 노드들이 공유하는 데이터
class State(TypedDict):
    name: str     # 입력. 어느 노드도 바꾸지 않는다.
    message: str  # greet가 쓰고, emphasize가 덧붙인다.


# 노드 반환값은 전체 State가 아니라 변경할 필드만 담는 부분 갱신이다.
# total=False: 어떤 키든 생략할 수 있다는 뜻.
class StateUpdate(TypedDict, total=False):
    name: str
    message: str


# 2. Node: State를 읽고 변경할 값만 반환하는 함수
def greet(state: State) -> StateUpdate:
    return {"message": f"{state['name']}님, 안녕하세요!"}


def emphasize(state: State) -> StateUpdate:
    # greet가 반환한 message가 이미 State에 병합된 뒤 호출된다.
    return {"message": state["message"] + " 반갑습니다."}


# 3. Graph: 노드를 등록하고 Edge로 실행 순서를 연결
builder: StateGraph[State] = StateGraph(State)

builder.add_node("greet", greet)  # (노드 이름, 함수)
builder.add_node("emphasize", emphasize)

builder.add_edge(START, "greet")  # START: 실행 시작 지점을 나타내는 상수
builder.add_edge("greet", "emphasize")
builder.add_edge("emphasize", END)  # END: 여기 도달하면 실행이 끝난다

# 4. 실행: compile로 실행 가능한 그래프를 만들고 invoke로 돌린다
graph: CompiledStateGraph[State] = builder.compile()

# invoke: 초기 State → 노드별 StateUpdate 병합 → 최종 State(dict)
initial_state: State = {"name": "민수", "message": ""}
result: State = cast(State, graph.invoke(initial_state))
print(result)
# {'name': '민수', 'message': '민수님, 안녕하세요! 반갑습니다.'}
