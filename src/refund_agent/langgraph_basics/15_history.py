"""LangGraph 기초 15: State history — 과거 체크포인트를 조회하고, 그 지점에서 다시 실행하거나 값을 고쳐 이어간다.

10번과 달라진 점
  10번의 get_state(config)는 가장 최근 체크포인트 하나만 조회했다.
  graph.get_state_history(config)는 그 thread의 체크포인트를 모두 내준다. 최신 것이 먼저 온다.
    각 항목은 10번에서 본 StateSnapshot이다. .values(State), .next(다음에 실행할 노드)에 더해
    .config(그 체크포인트를 가리키는 설정. thread_id + checkpoint_id)와 .metadata(step, source)가 있다.
  과거 체크포인트의 .config로 할 수 있는 일이 두 가지다.
    다시 실행: graph.invoke(None, 과거.config). 그 시점의 State로 .next 노드부터 다시 실행한다.
               입력 None은 10번에서 본 "새 입력 없이 저장된 지점부터 이어가라"는 뜻이다.
    고쳐서 실행: new_config = graph.update_state(과거.config, {키: 값})으로 State를 고친 새 체크포인트를 만들고
               graph.invoke(None, new_config)로 이어간다.
  update_state의 값도 노드의 반환값과 같은 규칙(reducer 또는 덮어쓰기)으로 병합된다.
    as_node="노드 이름"을 주면 그 노드가 쓴 것으로 처리해 그 노드의 다음 노드부터 실행한다.
    생략하면 그 체크포인트의 .next가 그대로 유지된다(이 예제의 경우).

실행 결과에서 확인할 것
  노드는 3개인데 체크포인트는 5개다. 입력을 받기 전(step -1), 입력 병합 후(step 0), 그리고 노드가 끝날 때마다 하나씩이다.
  과거 지점에서 다시 실행하면 그 앞의 노드(lookup)는 실행하지 않는다. 저장된 State를 쓴다.
  과거 기록은 지워지거나 덮어써지지 않는다. 다시 실행할 때마다 그 지점에서 갈라진 새 체크포인트가 뒤에 쌓인다.
    그래서 체크포인트 개수가 5 → 8 → 11로 늘어난다. get_state(thread)는 가장 나중에 만든 것을 돌려준다.

다시 실행할 때 주의할 점
  체크포인트가 되돌리는 것은 State뿐이다. 노드가 이미 실행한 API 호출·DB 쓰기는 취소되지 않는다.
    assess 앞으로 돌아가 다시 실행하면 assess·respond가 다시 실행된다. 그 안에 환불 API 호출이 있었다면 다시 호출된다.
    11번(재개)·14번(재시도)과 같은 문제다. 외부 상태를 바꾸는 작업에는 중복 처리 방지가 필요하다(5단계).

왜 필요한가
  실패한 실행을 처음부터 다시 돌리지 않고 실패 직전 단계부터 복구할 때 쓴다.
  "금액이 달랐다면 어떻게 판단했을까"처럼 State 일부만 바꿔 같은 흐름을 다시 확인할 때도 쓴다.
  runtime.py의 파일 체크포인트는 마지막 상태 하나만 남긴다. --resume은 그 지점에서 이어가는 것만 가능하다.
  LangGraph의 체크포인트는 단계마다 남으므로 중간 지점을 골라 돌아갈 수 있다.

실행: uv run python src/refund_agent/langgraph_basics/15_history.py
"""
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

# [그래프]
#   START → lookup → assess → respond → END
#   체크포인트:  ①입력 전  ②입력 병합  ③lookup 뒤  ④assess 뒤  ⑤respond 뒤
#   ③(next가 assess)으로 돌아가 다시 실행하거나, amount를 고쳐 이어간다.


# 1. State
class State(TypedDict):
    order_id: str  # 입력.
    amount: int    # lookup이 쓴다. assess가 읽는다.
    verdict: str   # assess가 쓴다 ("auto"/"manual").
    message: str   # respond가 쓴다.


class StateUpdate(TypedDict, total=False):
    amount: int
    verdict: str
    message: str


# 2. Node: 주문 금액 조회 → 자동 환불 가능 여부 판단 → 안내
def lookup(state: State) -> StateUpdate:
    print("lookup 실행")
    return {"amount": 80000}


def assess(state: State) -> StateUpdate:
    print("assess 실행")
    return {"verdict": "auto" if state["amount"] <= 50000 else "manual"}


def respond(state: State) -> StateUpdate:
    print("respond 실행")
    text = "자동 환불했습니다." if state["verdict"] == "auto" else "담당자 확인이 필요합니다."
    return {"message": f"{state['amount']}원: {text}"}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("lookup", lookup)
builder.add_node("assess", assess)
builder.add_node("respond", respond)

builder.add_edge(START, "lookup")
builder.add_edge("lookup", "assess")
builder.add_edge("assess", "respond")
builder.add_edge("respond", END)

# 4. 실행: 과거 기록은 체크포인트에 있으므로 checkpointer가 필수다.
graph: CompiledStateGraph[State] = builder.compile(checkpointer=InMemorySaver())
thread: RunnableConfig = {"configurable": {"thread_id": "1"}}

print("[처음 실행]")
print(cast(State, graph.invoke({"order_id": "ORD-1001", "amount": 0, "verdict": "", "message": ""}, thread)))
# [처음 실행]
# lookup 실행
# assess 실행
# respond 실행
# {'order_id': 'ORD-1001', 'amount': 80000, 'verdict': 'manual', 'message': '80000원: 담당자 확인이 필요합니다.'}

# get_state_history: 이 thread의 체크포인트 전체. 최신 것이 먼저 온다.
print("[기록]")
history: list[StateSnapshot] = list(graph.get_state_history(thread))
snapshot: StateSnapshot
for snapshot in history:
    # metadata는 None일 수 있고 step도 필수 키가 아니라서 .get으로 읽는다.
    step: int = (snapshot.metadata or {}).get("step", 0)
    print(f"step {step:>2}  next={snapshot.next}  values={snapshot.values}")
# [기록]
# step  3  next=()  values={'order_id': 'ORD-1001', 'amount': 80000, 'verdict': 'manual', 'message': '80000원: 담당자 확인이 필요합니다.'}
# step  2  next=('respond',)  values={'order_id': 'ORD-1001', 'amount': 80000, 'verdict': 'manual', 'message': ''}
# step  1  next=('assess',)  values={'order_id': 'ORD-1001', 'amount': 80000, 'verdict': '', 'message': ''}
# step  0  next=('lookup',)  values={'order_id': 'ORD-1001', 'amount': 0, 'verdict': '', 'message': ''}
# step -1  next=('__start__',)  values={}

# 돌아갈 지점을 고른다: lookup은 끝났고 assess를 실행하기 직전의 체크포인트.
before_assess: StateSnapshot = next(s for s in history if s.next == ("assess",))
print("돌아갈 지점:", sorted(before_assess.config.get("configurable", {})))
# 돌아갈 지점: ['checkpoint_id', 'checkpoint_ns', 'thread_id']   ← thread_id에 checkpoint_id가 더해진 설정

# (가) 다시 실행: 입력 None + 과거 체크포인트의 config. assess부터 실행한다.
print("[assess부터 다시 실행]")
print(cast(State, graph.invoke(None, before_assess.config)))
print("체크포인트 수:", len(list(graph.get_state_history(thread))))
# [assess부터 다시 실행]
# assess 실행                 ← lookup은 실행하지 않는다. 저장된 amount=80000을 쓴다
# respond 실행
# {'order_id': 'ORD-1001', 'amount': 80000, 'verdict': 'manual', 'message': '80000원: 담당자 확인이 필요합니다.'}
# 체크포인트 수: 8             ← 5개는 그대로 있고 3개가 새로 쌓였다

# (나) 고쳐서 실행: 같은 지점의 amount를 30000으로 바꾼 새 체크포인트를 만들고, 거기서 이어간다.
print("[amount를 고쳐서 실행]")
edited: RunnableConfig = graph.update_state(before_assess.config, {"amount": 30000})
print("고친 뒤 next:", graph.get_state(edited).next)
print(cast(State, graph.invoke(None, edited)))
print("체크포인트 수:", len(list(graph.get_state_history(thread))))
# [amount를 고쳐서 실행]
# 고친 뒤 next: ('assess',)   ← update_state는 노드를 실행하지 않는다. State만 고친다
# assess 실행
# respond 실행
# {'order_id': 'ORD-1001', 'amount': 30000, 'verdict': 'auto', 'message': '30000원: 자동 환불했습니다.'}
# 체크포인트 수: 11

# thread_id만 있는 config는 가장 나중에 만든 체크포인트를 가리킨다. 지금은 (나)의 결과다.
print("최신:", graph.get_state(thread).values["message"])
print("처음 실행의 결과:", history[0].values["message"])
# 최신: 30000원: 자동 환불했습니다.
# 처음 실행의 결과: 80000원: 담당자 확인이 필요합니다.   ← 과거 기록은 그대로 남아 있다

# update_state에 as_node="assess"와 {"verdict": "auto"}를 주고 실행해 보라.
# assess가 그 값을 쓴 것으로 처리되어 assess는 실행되지 않고 respond부터 실행된다. amount는 80000 그대로다.
