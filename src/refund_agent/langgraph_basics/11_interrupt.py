"""LangGraph 기초 11: interrupt — 노드 안에서 멈춰 사람의 답을 기다리고, Command(resume=)로 이어간다.

9·10번과 달라진 점
  노드 confirm이 interrupt(질문)를 호출하면 실행이 멈추고 invoke가 그 자리에서 돌아온다.
    반환 dict에는 "__interrupt__" 키가 생기고, 그 값은 Interrupt 객체를 담은 튜플이다.
    호출자는 첫 객체의 .value에 담긴 질문을 사용자에게 보여 준다. interrupt가 직접 입력을 받는 것은 아니다.
    get_state(config).next는 ("confirm",)이다. "confirm을 아직 못 끝냈다"는 뜻이다.
  사람의 답은 같은 thread_id로 graph.invoke(Command(resume=답), config)를 호출해 전달한다.
    Command(resume=...)는 invoke의 인자이지 노드의 반환값이 아니다. 9번의 Command(goto=...)와 쓰는 자리가 다르다.
  재개하면 confirm이 "처음부터" 다시 실행된다. 이미 답을 받은 interrupt()는 resume 값을 그대로 반환한다.
    그래서 "confirm 실행"이 두 번 찍힌다. interrupt() 앞에 둔 코드는 두 번 실행된다는 뜻이다.
  멈춘 자리를 기억하려면 10번의 checkpointer와 thread_id가 반드시 있어야 한다.

재개할 때 주의할 점
  interrupt() 앞의 API 호출·DB 쓰기는 노드를 재개할 때 반복된다.
    외부 상태를 바꾸는 작업은 답을 받은 뒤로 미루거나 별도 노드(refund)로 분리한다.
    앞에 꼭 두어야 한다면 반복 실행해도 결과가 중복되지 않도록(멱등성) 설계한다.
  별도 노드로 분리해도 오류 후 재시도·복구까지 포함해 "정확히 한 번 실행"이 보장되지는 않는다.
    실제 환불 API에는 중복 처리 방지도 필요하다. 이 예제의 refund는 메시지만 만드는 모의 작업이다.
  사용자의 답을 goto에 그대로 넣지 않는다. 여기서는 "yes"만 refund로, 나머지는 cancel로 보낸다.
  한 노드에 interrupt()가 여럿이면 호출 순서대로 resume 값이 짝지어진다. 재실행 때 순서가 바뀌면 안 된다.

왜 필요한가
  runtime.py는 환불 전 담당자 확인을 "실행을 끝내고 인계 파일을 남긴 뒤, 다음 실행에서 --resume으로 재개"하는
  순수 Python 방식으로 구현했다. interrupt()와 Command(resume=)는 같은 확인·재개 흐름을 LangGraph가 제공하는 장치다.
  이 예제는 사용자 입력 대신 resume="yes"를 코드에 지정하고, 같은 프로세스 안에서 바로 재개한다.

실행: uv run python src/refund_agent/langgraph_basics/11_interrupt.py
"""
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

# [그래프]
#   START → confirm ─┬─ goto="refund" (답이 "yes") → refund → END
#            │        └─ goto="cancel" (그 외)      → cancel → END
#            └─ interrupt: 여기서 멈춘다. Command(resume=답)으로 재개하면 confirm 처음부터 다시 실행.


# 1. State
class State(TypedDict):
    order_id: str  # 입력. confirm이 질문에 쓴다.
    decision: str  # confirm이 사용자의 답을 쓴다.
    message: str   # refund 또는 cancel이 쓴다.


class StateUpdate(TypedDict, total=False):
    order_id: str
    decision: str
    message: str


# 2. Node: 질문하고 멈추는 노드. 답을 받으면 9번처럼 Command로 분기한다.
def confirm(state: State) -> Command[Literal["refund", "cancel"]]:
    print("confirm 실행")  # 첫 실행과 재개 때 모두 찍힌다
    answer: str = interrupt(f"{state['order_id']} 환불을 진행할까요? (yes/no)")
    # 첫 실행: 위 줄에서 멈추고 invoke가 돌아온다. 아래는 실행되지 않는다.
    # 재개: confirm의 처음부터 다시 실행한 뒤, 위 줄이 resume 값을 반환하면 아래가 이어진다.
    next_node: Literal["refund", "cancel"] = "refund" if answer == "yes" else "cancel"
    return Command(update={"decision": answer}, goto=next_node)


# 승인 뒤 할 작업은 별도 노드로 분리한다. confirm을 재개해도 이 노드는 다시 실행되지 않는다(오류 후 재시도는 별개).
# 이 예제는 실제 API 호출 없이 메시지만 반환한다.
def refund(state: State) -> StateUpdate:
    print("refund 실행")
    return {"message": f"{state['order_id']} 환불을 처리했습니다."}


def cancel(state: State) -> StateUpdate:
    print("cancel 실행")
    return {"message": f"{state['order_id']} 환불을 취소했습니다."}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("confirm", confirm)
builder.add_node("refund", refund)
builder.add_node("cancel", cancel)

builder.add_edge(START, "confirm")
builder.add_edge("refund", END)
builder.add_edge("cancel", END)

# 4. 실행: 멈춘 자리를 기억해야 하므로 checkpointer가 필수다.
graph: CompiledStateGraph[State] = builder.compile(checkpointer=InMemorySaver())
thread: RunnableConfig = {"configurable": {"thread_id": "1"}}

# 1차 invoke: confirm이 interrupt에서 멈춘다. 결과에 __interrupt__가 들어 있고 message는 비어 있다.
paused: dict[str, Any] = graph.invoke(
    {"order_id": "ORD-1001", "decision": "", "message": ""}, thread
)
print(paused["__interrupt__"][0].value)  # 사용자에게 보여 줄 질문
print("next:", graph.get_state(thread).next)
# confirm 실행
# ORD-1001 환불을 진행할까요? (yes/no)
# next: ('confirm',)

# 2차 invoke: 같은 thread 설정으로 호출하고, 입력 자리에 Command(resume=답)을 넣는다.
# confirm이 처음부터 다시 실행되고, interrupt()가 "yes"를 반환해 refund로 간다.
resumed: dict[str, Any] = graph.invoke(Command(resume="yes"), thread)
print(resumed)
print("next:", graph.get_state(thread).next)
# confirm 실행
# refund 실행
# {'order_id': 'ORD-1001', 'decision': 'yes', 'message': 'ORD-1001 환불을 처리했습니다.'}
# next: ()

# resume="no"로 바꿔 실행해 보라. cancel 실행으로 끝난다.
# checkpointer를 빼고 compile하면 1차 invoke는 멈추지만, 2차 invoke에서
# "Cannot use Command(resume=...) without checkpointer" RuntimeError가 난다. 멈춘 자리를 기억할 곳이 없기 때문이다.
