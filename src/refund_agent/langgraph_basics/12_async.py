"""LangGraph 기초 12: async — 노드를 async def로 선언하고 graph.ainvoke로 실행한다.

1~11번과 달라진 점
  지금까지는 노드가 def였고 graph.invoke로 실행했다. 여기서는 left·right가 async def이고
  await graph.ainvoke(...)로 실행한다. 그래프 모양은 7번과 같다.
  async 노드도 반환값은 그대로 부분 갱신 dict(또는 None)다. 바뀌는 것은 선언과 실행 방법뿐이다.
  async def와 def 노드를 한 그래프에 섞어도 된다. 이 파일의 load·add는 def다.
    ainvoke는 def 노드도 실행한다. 반대로 invoke는 async def 노드를 실행하지 못한다.
  ainvoke는 코루틴을 돌려주므로 await해야 한다. 파일 맨 아래처럼 asyncio.run(main())으로 시작한다.
  invoke·stream·get_state에는 각각 ainvoke·astream·aget_state가 대응한다. 인자와 반환값은 같다.

실행 결과에서 확인할 것
  left·right가 각각 1초씩 기다리는데 전체는 약 1초에 끝난다. 같은 단계(superstep)의 노드가 동시에 실행된다.
  await asyncio.sleep(1)은 "기다리는 동안 다른 노드를 실행해도 된다"고 알려 주는 지점이다.
  동기 invoke도 같은 단계의 def 노드를 스레드로 동시에 실행한다. 병렬 실행 자체는 async만의 기능이 아니다.
    async는 스레드를 늘리지 않고 await 지점에서 번갈아 실행한다. API 응답 대기처럼 기다리는 시간이 긴 작업에 맞는다.

async 노드 안에서 주의할 점
  async def 안에서 time.sleep(1)처럼 await 없이 기다리는 호출을 쓰면 그동안 다른 노드도 멈춘다.
    left·right의 asyncio.sleep을 time.sleep으로 바꾸면 약 2초가 걸린다.
  async 노드에서는 async를 지원하는 클라이언트(AsyncAnthropic, httpx.AsyncClient 등)를 쓴다.

왜 필요한가
  langgraph_version.py의 노드는 async def이고 실행은 await graph.ainvoke(...)다.
  runtime.py가 Claude·주문 API 호출에 asyncio.wait_for로 시간 제한을 걸기 때문에 Runtime.step이 async다.
  그 step을 감싼 노드도 async여야 하고, async 노드가 있는 그래프는 ainvoke로 실행해야 한다.

실행: uv run python src/refund_agent/langgraph_basics/12_async.py
"""
import asyncio
import time
from typing import TypedDict, cast

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

# [그래프] 7번과 같다.
#                 ┌─ left  ─┐
#   START → load ─┤         ├─ add → END
#                 └─ right ─┘
#   단계:  [load]  [left, right]  [add]
#   left·right만 async def다. 각각 1초씩 기다린다.


# 1. State
class State(TypedDict):
    left: int    # load가 쓴다. left가 두 배로 만든다.
    right: int   # load가 쓴다. right가 두 배로 만든다.
    result: int  # add가 쓴다.


class StateUpdate(TypedDict, total=False):
    left: int
    right: int
    result: int


# 2. Node
# def 노드: ainvoke로 실행해도 그대로 동작한다.
def load(state: State) -> StateUpdate:
    return {"left": 2, "right": 3}


# async def 노드: 1초 걸리는 API 호출을 asyncio.sleep으로 흉내 낸다.
# 반환값은 def 노드와 같은 부분 갱신 dict다.
async def left(state: State) -> StateUpdate:
    print("left 시작")
    await asyncio.sleep(1)  # 기다리는 동안 right가 실행된다
    print("left 끝")
    return {"left": state["left"] * 2}


async def right(state: State) -> StateUpdate:
    print("right 시작")
    await asyncio.sleep(1)
    print("right 끝")
    return {"right": state["right"] * 2}


def add(state: State) -> StateUpdate:
    return {"result": state["left"] + state["right"]}


# 3. Graph: add_node·add_edge는 def 노드와 똑같이 쓴다.
builder: StateGraph[State] = StateGraph(State)

builder.add_node("load", load)
builder.add_node("left", left)
builder.add_node("right", right)
builder.add_node("add", add)

builder.add_edge(START, "load")
builder.add_edge("load", "left")
builder.add_edge("load", "right")
builder.add_edge(["left", "right"], "add")  # 8번의 리스트 fan-in
builder.add_edge("add", END)

graph: CompiledStateGraph[State] = builder.compile()


# 4. 실행: await는 async 함수 안에서만 쓸 수 있으므로 main을 만들고 asyncio.run으로 시작한다.
async def main() -> None:
    started = time.perf_counter()
    result: State = cast(State, await graph.ainvoke({"left": 0, "right": 0, "result": 0}))
    print(result)
    print(f"걸린 시간: {time.perf_counter() - started:.1f}초")


asyncio.run(main())
# left 시작         ← left가 끝나기 전에 right가 시작된다. 둘의 순서는 바뀔 수 있다
# right 시작
# left 끝
# right 끝
# {'left': 4, 'right': 6, 'result': 10}
# 걸린 시간: 1.0초   ← 1초 + 1초가 아니라 약 1초

# graph.invoke({"left": 0, "right": 0, "result": 0})로 실행해 보라. TypeError가 난다.
#   No synchronous function provided to "left".      ← "right"로 나올 수도 있다. 먼저 실행된 쪽이다
#   Either initialize with a synchronous function or invoke via the async API (ainvoke, astream, etc.)
# left·right의 await asyncio.sleep(1)을 time.sleep(1)로 바꿔 보라. 걸린 시간이 약 2초로 늘어난다.
