"""LangGraph 기초 16: Send — 실행 중에 정해지는 개수만큼 같은 노드를 병렬로 실행한다(동적 fan-out).

8번과 달라진 점
  8번은 korean·math·english 세 노드를 그래프에 미리 그렸다. 병렬 개수가 코드를 쓸 때 3으로 고정된다.
  여기서는 과목 목록을 입력으로 받는다. 과목이 몇 개인지는 실행해야 알 수 있다.
  노드는 grade 하나만 등록하고, 분기 함수 assign이 과목마다 Send("grade", {...})를 하나씩 만들어 리스트로 반환한다.
    Send(노드 이름, 그 노드에 줄 입력) 하나가 "이 입력으로 이 노드를 한 번 실행하라"는 지시다.
    Send가 3개면 grade가 같은 단계(superstep)에서 3번 실행된다.
  2번의 분기 함수는 "어디로 갈지" 이름 하나를 반환했다. 여기서는 Send 객체의 리스트를 반환한다.
    add_conditional_edges(START, assign, ["grade"])의 세 번째 인자는 path_map이 아니라 가능한 목적지 목록이다.
    Send에 이미 노드 이름이 들어 있어서 이름을 변환할 필요가 없다. 그래프 그림에 쓰인다.

Send로 실행되는 노드의 입력
  grade가 받는 것은 그래프의 State가 아니라 Send의 두 번째 인자다. 그래서 인자 타입이 State가 아니라 Task다.
    grade는 State의 subjects를 볼 수 없다. 필요한 값은 Send에 담아 보내야 한다.
  grade의 반환값은 다른 노드와 똑같이 그래프 State에 대한 부분 갱신이다.
  여러 grade가 같은 키 scores에 쓰므로 8번처럼 reducer가 필요하다.

실행 결과에서 확인할 것
  과목 수에 따라 grade 실행 횟수가 달라진다. 그래프 정의는 그대로다.
  summarize는 한 번만 실행된다. Send로 만든 grade들이 모두 같은 단계에서 끝나기 때문이다.
  assign이 빈 리스트를 반환하면 실행할 노드가 없어 그대로 끝난다. grade도 summarize도 실행되지 않는다.
  grade들의 실행 순서는 보장되지 않는다. 8번과 같은 이유로 과목 이름을 키로 한 dict에 모은다.

그 밖에
  9번의 Command에도 쓸 수 있다. 노드가 Command(goto=[Send("grade", {...}), ...])를 반환하면 같은 fan-out이다.
  Send마다 다른 노드를 지정해도 된다. [Send("grade", ...), Send("review", ...)]처럼 섞을 수 있다.

왜 필요한가
  "주문 하나에 상품이 여러 개이고 상품마다 환불 가능 여부를 따로 조회"처럼 개수가 데이터에 달린 작업에 쓴다.
  문서 여러 개를 각각 요약한 뒤 합치는 map-reduce 구조가 대표적이다. grade가 map, summarize가 reduce다.
  langgraph_version.py는 주문 하나를 순서대로 처리하므로 Send를 쓰지 않는다.

실행: uv run python src/refund_agent/langgraph_basics/16_send.py
"""
from typing import Annotated, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

# [그래프]
#                        ┌─ grade {"subject": "korean"}  ─┐
#   START ── (assign) ───┼─ grade {"subject": "math"}    ─┼─ summarize → END
#                        └─ grade {"subject": "english"} ─┘
#   등록된 노드는 grade 하나다. 몇 번 실행할지는 assign이 반환한 Send의 개수가 정한다.
#   단계:  [grade × 과목 수]  [summarize]


# Reducer: 8번과 같다. 여러 grade의 결과를 하나의 dict로 합친다.
def merge_scores(current: dict[str, int], new: dict[str, int]) -> dict[str, int]:
    return {**current, **new}


# 1. State
class State(TypedDict):
    subjects: list[str]                              # 입력. assign이 읽는다. 개수가 실행마다 다르다.
    scores: Annotated[dict[str, int], merge_scores]  # 여러 grade가 함께 쓴다 → reducer 필요
    total: int                                       # summarize가 쓴다.


class StateUpdate(TypedDict, total=False):
    scores: dict[str, int]
    total: int


# Send로 grade에 보내는 입력. 그래프의 State와 다른 타입이다.
class Task(TypedDict):
    subject: str


# 분기 함수: 과목마다 Send를 하나씩 만든다. 노드가 아니며 State를 바꾸지 않는다.
def assign(state: State) -> list[Send]:
    return [Send("grade", {"subject": subject}) for subject in state["subjects"]]


# 2. Node
# Send로 실행되는 노드: 인자는 State가 아니라 Send에 담긴 Task다. 반환값은 State에 대한 부분 갱신이다.
# 매개변수 이름은 다른 노드처럼 state로 둔다(add_node의 타입 검사가 이 이름을 요구한다). 타입이 Task라는 점이 다르다.
def grade(state: Task) -> StateUpdate:
    print(f"grade: {state}")
    score = len(state["subject"]) * 10  # 채점을 흉내 낸다: 과목 이름 길이 × 10
    return {"scores": {state["subject"]: score}}


# fan-in 노드: 모든 grade가 끝난 뒤 한 번 실행된다. 이 노드는 그래프의 State를 받는다.
def summarize(state: State) -> StateUpdate:
    print("summarize")
    return {"total": sum(state["scores"].values())}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("grade", grade)  # 한 번만 등록한다
builder.add_node("summarize", summarize)

builder.add_conditional_edges(START, assign, ["grade"])  # assign이 Send 리스트를 반환한다
builder.add_edge("grade", "summarize")
builder.add_edge("summarize", END)

# 4. 실행: 같은 그래프에 과목 수만 바꿔 넣는다.
graph: CompiledStateGraph[State] = builder.compile()

print(cast(State, graph.invoke({"subjects": ["korean", "math", "english"], "scores": {}, "total": 0})))
# grade: {'subject': 'korean'}     ← grade가 3번 실행된다. 순서는 바뀔 수 있다
# grade: {'subject': 'math'}
# grade: {'subject': 'english'}
# summarize                        ← 한 번만 실행된다
# {'subjects': ['korean', 'math', 'english'], 'scores': {'korean': 60, 'math': 40, 'english': 70}, 'total': 170}

print(cast(State, graph.invoke({"subjects": ["math"], "scores": {}, "total": 0})))
# grade: {'subject': 'math'}       ← 과목이 하나면 한 번
# summarize
# {'subjects': ['math'], 'scores': {'math': 40}, 'total': 40}

print(cast(State, graph.invoke({"subjects": [], "scores": {}, "total": 0})))
# {'subjects': [], 'scores': {}, 'total': 0}   ← Send가 없으면 grade도 summarize도 실행되지 않는다

# grade의 print를 print(f"grade: {state['subjects']}")로 바꿔 실행해 보라. KeyError가 난다.
# grade가 받은 것은 State가 아니라 Send에 담긴 {"subject": ...}이기 때문이다.
# scores의 Annotated[..., merge_scores]를 dict[str, int]로 바꿔 실행해 보라.
# 과목이 둘 이상이면 8번에서 본 InvalidUpdateError가 난다. 과목이 하나면 오류 없이 끝난다.
