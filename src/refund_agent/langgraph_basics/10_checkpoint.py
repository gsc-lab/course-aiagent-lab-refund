"""LangGraph 기초 10: Checkpoint — State와 실행 정보를 저장해 다음 호출에서 활용한다.

1~9번과 달라진 점
  지금까지는 invoke가 끝나면 그래프가 State를 기억하지 않았다. 다음 호출은 매번 처음부터였다.
  compile(checkpointer=...)를 주면 LangGraph가 실행 단계(superstep)마다 체크포인트를 저장한다.
    체크포인트에는 State뿐 아니라 다음에 실행할 노드 등 실행을 이어가는 데 필요한 정보도 담긴다.
  thread_id는 체크포인트를 모아 관리하는 실행 흐름의 식별자다. 대화형 앱에서는 보통 대화 하나에 대응한다.
    invoke의 config={"configurable": {"thread_id": ...}}로 어떤 흐름의 기록을 사용할지 지정한다.
  이 예제처럼 실행이 끝난 뒤 같은 thread_id로 새 입력 dict를 주면, 저장된 State에 병합하고 START부터 실행한다.
    입력에서 생략한 키는 저장된 값을 유지한다. 입력에 포함된 키는 reducer 또는 덮어쓰기 규칙으로 갱신한다.
  새로운 thread_id에는 이전 기록이 없으므로 필요한 초기값을 입력해야 한다.
  graph.get_state(config)는 저장된 실행 상태를 StateSnapshot으로 반환한다.
    .values는 State 값, .next는 다음에 실행할 노드 이름의 튜플이다.

왜 필요한가
  호출 사이의 State 유지, 사람 확인 뒤 재개(11번 interrupt), 실패 후 복구에 체크포인트를 활용한다.
  langgraph_version.py도 compile(checkpointer=InMemorySaver())와 thread_id=run_id를 쓴다.
  InMemorySaver는 해당 객체의 메모리에만 기록을 보관하므로 프로세스 종료 후에는 복구할 수 없다.
    프로세스를 재시작해도 복구하려면 SqliteSaver처럼 파일·DB에 기록을 남기는 checkpointer가 필요하다.
  runtime.py의 Runtime.save()/load_checkpoint()도 저장·복원을 담당하지만 저장 형식과 대상은 다르다.
    langgraph_version.py에서는 기존 파일 체크포인트와 LangGraph의 메모리 체크포인트를 함께 사용한다.

실행: uv run python src/refund_agent/langgraph_basics/10_checkpoint.py
"""
from operator import add
from typing import Annotated, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

# [그래프] 1번처럼 단순하다. checkpointer는 그래프 구조를 바꾸지 않고 실행 단계마다 기록을 남긴다.
#   START → visit → END
#   thread "1": invoke ─ invoke ─ invoke   (count 1 → 2 → 3, history 누적)
#   thread "2": invoke                     (count 1, 새로 시작)


# 1. State
class State(TypedDict):
    count: int                          # visit가 1씩 올린다. 덮어쓰기.
    history: Annotated[list[str], add]  # visit가 한 줄씩 덧붙인다. 누적(4번 reducer).


class StateUpdate(TypedDict, total=False):
    count: int
    history: list[str]


# 2. Node: 방문 횟수를 올리고 기록을 남긴다
def visit(state: State) -> StateUpdate:
    new_count = state["count"] + 1
    return {"count": new_count, "history": [f"visit {new_count}"]}


# 3. Graph
builder: StateGraph[State] = StateGraph(State)

builder.add_node("visit", visit)
builder.add_edge(START, "visit")
builder.add_edge("visit", END)

# 4. 실행: checkpointer를 붙여 compile한다. 이후 모든 invoke에 thread_id가 필요하다.
graph: CompiledStateGraph[State] = builder.compile(checkpointer=InMemorySaver())

# RunnableConfig 타입을 명시하면 Pylance가 이 dict를 실행 설정으로 검사할 수 있다.
thread_1: RunnableConfig = {"configurable": {"thread_id": "1"}}
thread_2: RunnableConfig = {"configurable": {"thread_id": "2"}}

# 첫 invoke: 초기 State를 넣는다.
print(cast(State, graph.invoke({"count": 0, "history": []}, thread_1)))
# {'count': 1, 'history': ['visit 1']}

# 두 번째 invoke: 빈 dict를 주면 State를 바꾸지 않고 START부터 다시 실행한다.
# 저장된 count=1을 visit가 2로 올린다.
# 일부 키만 넣어도 실행에는 문제없다. 입력 타입이 State로 선언되어 있어 cast로 타입 검사만 맞춘다.
# cast는 타입 검사기에만 영향을 준다. 누락된 키를 채우거나 값을 검증하지 않는다.
print(cast(State, graph.invoke(cast(State, {}), thread_1)))
# {'count': 2, 'history': ['visit 1', 'visit 2']}

# 세 번째 invoke: history만 넣었다. count는 저장된 값이 유지되고, history는 reducer가 덧붙인 뒤 visit가 실행된다.
print(cast(State, graph.invoke(cast(State, {"history": ["사용자 요청"]}), thread_1)))
# {'count': 3, 'history': ['visit 1', 'visit 2', '사용자 요청', 'visit 3']}

# 입력 None은 새 입력 없이 저장된 체크포인트의 남은 실행을 이어가라는 뜻이다.
# 이 예제는 이미 END까지 끝났으므로 실행할 노드가 없어 저장된 State만 돌려준다.
# 11번처럼 interrupt()의 답을 전달하며 재개할 때는 None이 아니라 Command(resume=답)을 쓴다.
print(cast(State, graph.invoke(None, thread_1)))
# {'count': 3, 'history': ['visit 1', 'visit 2', '사용자 요청', 'visit 3']}   ← count가 그대로 3

# get_state: 최신 체크포인트를 조회한다. next가 ()이면 남은 노드가 없다는 뜻이다.
snapshot: StateSnapshot = graph.get_state(thread_1)
print("values:", snapshot.values)
print("next:", snapshot.next)
# values: {'count': 3, 'history': ['visit 1', 'visit 2', '사용자 요청', 'visit 3']}
# next: ()

# 다른 thread_id: 저장된 것이 없으므로 처음부터다.
print(cast(State, graph.invoke({"count": 0, "history": []}, thread_2)))
# {'count': 1, 'history': ['visit 1']}

# thread_id를 빼고 graph.invoke({"count": 0, "history": []})를 실행해 보라.
# checkpointer가 있는 그래프는 thread_id 없이 실행할 수 없다는 ValueError가 난다.
