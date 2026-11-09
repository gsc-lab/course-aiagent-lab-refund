"""LangGraph 기초 8: 병렬 + Reducer — 병렬 노드들이 같은 키에 쓸 때는 reducer가 필요하다.

7번과 달라진 점
  START에서 바로 korean·math·english 세 노드로 갈라진다. fan-out 출발점이 START여도 된다.
  세 노드가 모두 같은 키 scores에 쓴다. 7번과 달리 충돌이 생긴다.
  reducer 없이 한 단계에서 두 노드가 같은 키에 쓰면 LangGraph는 InvalidUpdateError를 던진다.
    "At key 'scores': Can receive only one value per step. Use an Annotated key to handle multiple values."
  한 단계에서 한 키는 한 번만 덮어쓸 수 있기 때문이다. scores에 reducer(merge_scores)를 붙이면
  세 결과가 하나의 dict로 합쳐진다. 병렬로 모은 결과를 한 키에 담을 때 reducer는 거의 필수다.
  fan-in은 add_edge(["korean", "math", "english"], "summarize")처럼 출발 노드를 리스트로 적는다.

add_edge의 리스트는 왜 출발 쪽에만 쓸 수 있나
  add_edge(["a", "b"], "c")는 "a와 b가 모두 끝날 때까지 기다린 뒤 c를 한 번 실행"이라는 대기(join) 규칙이다.
  7번처럼 간선을 따로 그리면 a와 b가 같은 단계에서 끝날 때만 c가 한 번 실행된다. 경로 길이가 다르면 두 번이다.
  반대 방향 add_edge("a", ["b", "c"])는 간선 두 개를 줄여 쓴 것일 뿐 새 규칙이 없어서 제공하지 않는다.
  (실행 시점에 개수가 정해지는 동적 fan-out은 Send 객체로 처리한다. 16번에서 다룬다.)

실행 결과에서 확인할 것
  세 노드의 실행 순서는 선언 순서와 다를 수 있다. 같은 단계 안의 순서는 보장되지 않는다.
  그래서 "첫 번째 결과가 korean"이라고 가정하는 코드는 쓰지 않는다. 과목 이름을 키로 한 dict에 담는 이유다.
  merge_scores의 첫 호출 ({}, {})은 6번에서 본 초기 입력 병합이다.

실행: uv run python src/refund_agent/langgraph_basics/08_parallel_reducer.py
"""
from typing import Annotated, TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프]
#           ┌─ korean  ─┐
#   START ──┼─ math    ─┼─ summarize → END
#           └─ english ─┘
#   단계:   [korean, math, english]  [summarize]


# Reducer: 두 dict를 합친다. 같은 과목이 두 번 오면 새 값이 이긴다.
def merge_scores(current: dict[str, int], new: dict[str, int]) -> dict[str, int]:
    print(f"merge_scores(기존={current}, 새 값={new})")
    return {**current, **new}


# 1. State
class State(TypedDict):
    scores: Annotated[dict[str, int], merge_scores]  # 병렬 노드 셋이 함께 쓴다 → reducer 필요
    total: int                                        # summarize가 쓴다 (덮어쓰기)
    average: float                                    # summarize가 쓴다 (덮어쓰기)


class StateUpdate(TypedDict, total=False):
    scores: dict[str, int]  # 노드는 자기 과목 하나만 담아 반환한다
    total: int
    average: float


# 2. Node: 과목별 점수를 모으는 세 노드. 서로를 모른 채 같은 키에 쓴다.
def korean(state: State) -> StateUpdate:
    print("korean")
    return {"scores": {"korean": 20}}


def math(state: State) -> StateUpdate:
    print("math")
    return {"scores": {"math": 30}}


def english(state: State) -> StateUpdate:
    print("english")
    return {"scores": {"english": 40}}


# fan-in 노드: 세 과목이 모두 모인 뒤 실행된다.
def summarize(state: State) -> StateUpdate:
    total = sum(state["scores"].values())
    return {"total": total, "average": total / len(state["scores"])}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("korean", korean)
builder.add_node("math", math)
builder.add_node("english", english)
builder.add_node("summarize", summarize)

builder.add_edge(START, "korean")   # fan-out: START에서 세 갈래
builder.add_edge(START, "math")
builder.add_edge(START, "english")
builder.add_edge(["korean", "math", "english"], "summarize")  # fan-in: 셋이 모두 끝나면 한 번
builder.add_edge("summarize", END)

# 4. 실행
graph: CompiledStateGraph[State] = builder.compile()

# merge_scores를 빼고 scores: dict[str, int]로만 선언한 뒤 다시 실행해 보라. InvalidUpdateError가 난다.
result: State = cast(State, graph.invoke({"scores": {}, "total": 0, "average": 0.0}))
print(result)
# merge_scores(기존={}, 새 값={})                         ← 초기 입력 병합
# english                                                ← 세 노드의 순서는 실행마다 다를 수 있다
# math
# korean
# merge_scores(기존={}, 새 값={'english': 40})
# merge_scores(기존={'english': 40}, 새 값={'math': 30})
# merge_scores(기존={'english': 40, 'math': 30}, 새 값={'korean': 20})
# {'scores': {'english': 40, 'math': 30, 'korean': 20}, 'total': 90, 'average': 30.0}
