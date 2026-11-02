"""LangGraph 기초 예제 모음 — langgraph_version.py를 읽기 전에 LangGraph 문법만 따로 익힌다.

Agent 코드와 무관하게 각 파일이 단독으로 실행되고 Claude를 호출하지 않는다.
번호 순서대로 읽는다. 각 파일 첫머리에 이전 파일과 달라진 점을 적어 두었다.

  01_sequence.py          순차 실행: State·Node·Edge·invoke
  02_branch.py            조건부 분기: add_conditional_edges
  03_loop.py              반복: 조건부 간선을 자기 자신으로, recursion_limit
  04_reducer.py           Reducer: Annotated[list, operator.add]로 누적
  05_dataclass.py         dataclass State
  06_custom_reducer.py    사용자 정의 Reducer 함수, 초기 입력도 reducer를 거친다
  07_parallel.py          병렬 실행: fan-out / fan-in, superstep
  08_parallel_reducer.py  병렬 노드가 같은 키에 쓸 때 reducer가 필요한 이유, 리스트 fan-in

실행: uv run python src/refund_agent/langgraph_basics/01_sequence.py
"""
