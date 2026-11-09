"""LangGraph 기초 13: stream — 실행이 끝나기를 기다리지 않고 단계마다 결과를 받는다.

1~12번과 달라진 점
  invoke는 그래프가 END에 도달한 뒤 최종 State 하나만 돌려준다. 중간에 무슨 일이 있었는지는 보이지 않는다.
  graph.stream(...)은 실행 단계(superstep)가 끝날 때마다 값을 하나씩 내주는 반복자다. for로 받는다.
  그래프는 4번(반복 + reducer)과 같다. 실행 방법만 invoke에서 stream으로 바꿨다.
  무엇을 받을지는 stream_mode로 고른다.
    "updates": 그 단계에서 실행된 노드와 그 노드가 반환한 갱신분. {노드 이름: 갱신분} 모양이다.
               stream_mode를 생략하면 StateGraph는 "updates"로 동작한다.
    "values" : 그 단계의 갱신이 병합된 뒤의 State 전체. 첫 값은 노드 실행 전의 입력 State다.
    ["updates", "values"]: 리스트로 주면 (모드 이름, 값) 튜플로 받는다. 어느 모드의 값인지 구분하기 위해서다.
  invoke의 반환값은 "values"로 받은 마지막 값과 같다.

updates와 values의 차이 (reducer가 붙은 history에서 드러난다)
  updates의 history는 노드가 반환한 값 그대로다. [20], [40], [60]처럼 이번에 추가한 항목만 있다.
  values의 history는 reducer가 병합한 결과다. [20], [20, 40], [20, 40, 60]처럼 누적된다.
  "노드가 무엇을 했는가"는 updates로, "지금 State가 어떤가"는 values로 본다.

그 밖에
  병렬 단계(7번)에서 updates는 노드마다 따로 온다. 같은 단계 안의 순서는 보장되지 않는다.
  interrupt(11번)로 멈추면 updates에 {"__interrupt__": (Interrupt(...),)}가 온다.
  async 그래프(12번)는 async for chunk in graph.astream(...)으로 받는다.
  stream_mode에는 "debug"(단계별 task 시작·결과), "messages"(LLM 토큰), "custom"(노드가 직접 내보내는 값)도 있다.

왜 필요한가
  runtime.py는 emit()으로 step_start·step_end·state_change를 .trace.jsonl에 직접 기록한다(4단계 Observability).
  stream은 같은 정보를 프레임워크가 내주는 방식이다. updates는 step_end에, values는 state_change 뒤의 State에 대응한다.
  langgraph_version.py는 Runtime.step 안의 emit을 그대로 쓰므로 ainvoke만 호출한다.
  오래 걸리는 Agent에서 진행 상황을 화면에 보여 줄 때도 stream을 쓴다.

실행: uv run python src/refund_agent/langgraph_basics/13_stream.py
"""
import operator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프] 3·4번과 같다.
#   START → study → (route) ─┬─ "again" (score <  60) → study  (다시 실행)
#                            └─ "done"  (score >= 60) → END
#   stream은 study가 한 번 끝날 때마다 값을 내준다.


# 1. State
class State(TypedDict):
    score: int                                   # 덮어쓰기 (기본)
    history: Annotated[list[int], operator.add]  # 누적: 기존 + 새 값


class StateUpdate(TypedDict, total=False):
    score: int
    history: list[int]


# 2. Node: 점수를 20 올리고, 올린 점수를 history에 한 개 추가한다
def study(state: State) -> StateUpdate:
    new_score = state["score"] + 20
    return {"score": new_score, "history": [new_score]}


def route(state: State) -> Literal["again", "done"]:
    return "again" if state["score"] < 60 else "done"


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("study", study)
builder.add_edge(START, "study")
builder.add_conditional_edges("study", route, {"again": "study", "done": END})

graph: CompiledStateGraph[State] = builder.compile()

# 4. 실행: invoke 대신 stream. 인자는 invoke와 같고 stream_mode만 추가한다.
initial: State = {"score": 0, "history": []}

# updates: 단계마다 {노드 이름: 그 노드가 반환한 갱신분}
print("[updates]")
update_chunk: dict[str, Any]
for update_chunk in graph.stream(initial, stream_mode="updates"):
    print(update_chunk)
# [updates]
# {'study': {'score': 20, 'history': [20]}}
# {'study': {'score': 40, 'history': [40]}}   ← history는 이번에 추가한 항목만
# {'study': {'score': 60, 'history': [60]}}

# values: 단계마다 병합이 끝난 State 전체
print("[values]")
value_chunk: dict[str, Any]
for value_chunk in graph.stream(initial, stream_mode="values"):
    print(value_chunk)
# [values]
# {'score': 0, 'history': []}                 ← 첫 값은 노드 실행 전의 입력
# {'score': 20, 'history': [20]}
# {'score': 40, 'history': [20, 40]}          ← history는 reducer가 누적한 결과
# {'score': 60, 'history': [20, 40, 60]}      ← 마지막 값 = invoke의 반환값

# 두 모드를 함께: (모드 이름, 값) 튜플로 온다
print("[updates + values]")
for mode, chunk in graph.stream(initial, stream_mode=["updates", "values"]):
    print(f"{mode:7} {chunk}")
# [updates + values]
# values  {'score': 0, 'history': []}
# updates {'study': {'score': 20, 'history': [20]}}
# values  {'score': 20, 'history': [20]}
# updates {'study': {'score': 40, 'history': [40]}}
# values  {'score': 40, 'history': [20, 40]}
# updates {'study': {'score': 60, 'history': [60]}}
# values  {'score': 60, 'history': [20, 40, 60]}

# stream_mode="debug"로 바꿔 실행해 보라. 단계 번호·task 이름·입력·결과가 담긴 dict가 온다.
# for 안에서 break하면 그 뒤 단계는 실행되지 않는다. stream은 값을 요청받을 때만 다음 단계로 진행한다.
