"""LangGraph 기초 예제 모음 — langgraph_version.py를 읽기 전에 LangGraph 문법만 따로 익힌다.

각 파일은 Agent 코드와 무관하게 단독으로 실행되며 Claude를 호출하지 않는다.
번호 순서대로 읽는다. 각 파일 첫머리에 이전 파일과 달라진 점을 적어 두었다.

  01_sequence.py          순차 실행: State·Node·Edge·invoke
  02_branch.py            조건부 분기: add_conditional_edges
  03_loop.py              반복: 조건부 간선을 자기 자신으로, recursion_limit
  04_reducer.py           Reducer: Annotated[list, operator.add]로 누적
  05_dataclass.py         dataclass State
  06_custom_reducer.py    사용자 정의 Reducer 함수, 초기 입력도 reducer를 거친다
  07_parallel.py          병렬 실행: fan-out / fan-in, superstep
  08_parallel_reducer.py  병렬 노드가 같은 키에 쓸 때 reducer가 필요한 이유, 리스트 fan-in
  09_command.py           Command: 노드가 State 갱신과 다음 노드를 한 번에 반환, 분기 함수 없는 분기
  10_checkpoint.py        Checkpoint: compile(checkpointer=)·thread_id·get_state, 실행 사이에 State 보관
  11_interrupt.py         interrupt: 노드 안에서 멈춰 사람의 답을 기다리고 Command(resume=)로 재개
  12_async.py             async: async def 노드와 graph.ainvoke, 같은 단계의 노드가 동시에 실행되는 것을 시간으로 확인
  13_stream.py            stream: 단계마다 결과 받기, stream_mode "updates"와 "values"의 차이
  14_retry.py             RetryPolicy: add_node(retry_policy=)로 실패한 노드만 다시 실행, retry_on으로 대상 예외 지정
  15_history.py           State history: get_state_history로 과거 체크포인트 조회, 그 지점부터 재실행, update_state로 고쳐서 실행
  16_send.py              Send: 실행 중에 정해지는 개수만큼 같은 노드를 병렬 실행(동적 fan-out)

실행: uv run python src/refund_agent/langgraph_basics/01_sequence.py
"""
