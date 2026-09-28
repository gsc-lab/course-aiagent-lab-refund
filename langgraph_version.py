"""마지막 실습: 같은 State/업무 노드/정책으로 실행 루프만 LangGraph로 바꾼다.

[설계] 무엇이 바뀌고 무엇이 남는가
  바뀜 : runtime.run의 while 루프 → StateGraph + conditional edges + graph.ainvoke
  남음 : State(dataclass), NODES(업무), route(흐름), Runtime.step(정책·로그·체크포인트)
프레임워크는 "노드 실행 → 다음 노드 결정 → 반복"이라는 실행기를 대신할 뿐이다.
재시도·예산·멱등성·인계·Human-in-the-loop 같은 설계는 여전히 우리 코드에 있다.
test_agent.LangGraphComparison이 두 엔진의 최종 State가 같음을 확인한다.
"""
from dataclasses import asdict

from nodes import NODES
from state import State


async def run(runtime):
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as exc:
        raise SystemExit("LangGraph 실행에 필요한 패키지가 없습니다. 설치: python -m pip install -r requirements-langgraph.txt") from exc

    # 그래프에도 analyze / lookup / assess / refund / respond가 각각 드러난다.
    # [설계] 그래프 State 스키마로 우리 dataclass를 그대로 쓴다. 프레임워크에 맞춰
    # State를 다시 정의하지 않는 것이 "같은 설계를 다른 실행기로" 옮기는 요령이다.
    builder = StateGraph(State)

    def wrap(name):
        # [설계] 어댑터. LangGraph 노드는 (State → State 갱신분)을 반환해야 하므로
        # 우리 Runtime.step을 감싸 형식만 맞춘다. 정책·로그·체크포인트는 step 안에 그대로 있다.
        # expected_node 검사로 "그래프가 고른 노드"와 "State의 current_node"가 어긋나면 드러난다.
        async def node(graph_state):
            runtime.state = graph_state
            await runtime.step(expected_node=name)
            return asdict(runtime.state)
        return node

    def next_node(graph_state):
        # [설계] conditional edge = 우리 route 함수의 결과 읽기. step이 이미 current_node를
        # 갱신해 두었으므로 여기서는 그 값을 프레임워크에 알려 주기만 한다.
        # running이 아니면(대기·완료·인계) END로 보내 루프를 멈춘다.
        return graph_state.current_node if graph_state.status == "running" else END

    # [설계] 모든 노드가 모든 노드로 갈 수 있게 등록한다. 실제 허용 전이는 route가 결정하고,
    # 그래프는 "가능한 목적지 목록"만 안다. 그림용으로는 route의 분기를 엣지로 옮겨도 된다.
    destinations = {name: name for name in NODES} | {END: END}
    for name in NODES:
        builder.add_node(name, wrap(name))
        builder.add_conditional_edges(name, next_node, destinations)
    builder.add_conditional_edges(START, next_node, destinations)  # 재개 시 current_node부터 시작
    # [설계] InMemorySaver는 프로세스가 끝나면 사라진다. 파일 체크포인트(runtime.save)와
    # 수명이 다르다는 점을 6단계 질문에서 다룬다. 프로그램 종료 후에도 보관하려면 SQLite/Postgres 체크포인터를 사용한다.
    graph = builder.compile(checkpointer=InMemorySaver())
    runtime.emit("run_start", engine="langgraph")
    runtime.save()
    # [설계] graph.ainvoke가 runtime.run의 while을 대신한다.
    # recursion_limit은 max_steps에 대응하는 프레임워크 쪽 종료 가드다. 우리 step 가드가
    # 먼저 걸리도록 약간 여유(+5)를 둔다.
    result = await graph.ainvoke(
        asdict(runtime.state),
        config={"configurable": {"thread_id": runtime.state.run_id},
                "recursion_limit": runtime.policy.max_steps + 5},
    )
    runtime.state = State(**result)
    runtime.emit("run_end", engine="langgraph", status=runtime.state.status,
                 elapsed_s=runtime.state.elapsed_s, tool_calls=runtime.state.tool_calls,
                 cost_units=runtime.state.cost_units, actual_cost_krw=None, llm_calls=runtime.state.llm_calls,
                 input_tokens=runtime.state.input_tokens, output_tokens=runtime.state.output_tokens)
    return runtime.state
