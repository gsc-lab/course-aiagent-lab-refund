"""1단계: State / Node / Routing / Loop만 먼저 읽는다. Claude 요청 분석부터 시작한다.

[설계] AI Agent Workflow의 최소 골격은 네 가지다.
  State   : 노드들이 공유하는 단 하나의 데이터 묶음 (여기서는 dict, main.py에서는 dataclass)
  Node    : State를 읽고 일부를 갱신하는 단위 작업 함수 (analyze/lookup/assess/refund/respond)
  Routing : "방금 끝난 노드 + 현재 State"만 보고 다음 노드를 고르는 순수 함수 (route)
  Loop    : 노드 실행 → 라우팅을 "end"가 나올 때까지 반복하는 실행기 (run의 while)

LLM(Claude)은 이 골격 안에서 analyze 노드 하나만 맡는다. 자연어를 구조화된 값으로 바꾸는
일만 하고, 업무 판단(환불 가능 여부)과 흐름 제어(다음 노드)는 모두 Python이 한다.
이 파일은 LLM 호출의 시간 제한만 적용한다. 재시도·예산·체크포인트·로그·인계는 생략한다.
같은 골격 위에 그 요소들을 얹은 것이 runtime.py다. 두 파일을 나란히 놓고 비교한다.
"""
import asyncio
from llm import ClaudeAnalyzer, parse_message


# [설계] Tool 자리. 아직 외부 시스템을 분리하지 않아 인메모리 dict를 직접 읽는다.
# tools.py에서는 이것이 MockAPI(get_order / get_refund / create_refund)로 바뀐다.
ORDERS = {
    "ORD-1001": {"days": 3, "amount": 32000},
    "ORD-1002": {"days": 20, "amount": 45000},
}


async def analyze(state, analyzer):
    # [설계] LLM 노드. 입력은 고객 요청, 출력은 {"intent", "order_id"} 두 값뿐이다.
    # LLM 응답은 parse_message에서 Python이 형식·허용값·주문번호 근거를 검증한 뒤에만
    # State에 들어간다. LLM이 State를 직접 쓰거나 Tool을 호출하는 경로는 없다.
    messages = [{"role": "user", "content": state["request"]}]
    # [설계] LLM 호출도 외부 I/O다. timeout 없이 기다리지 않는 것이 최소한의 실행 정책이다.
    # 단, 여기서는 실패(TransientError / ServiceError / TimeoutError)를 잡지 않으므로
    # 예외가 전파되어 실행이 종료된다. 재시도·인계는 runtime.analyze_request가 맡는다.
    response = await asyncio.wait_for(analyzer.generate(messages, 512, 30), timeout=30)
    result = parse_message(response, messages)
    state.update(result)


def lookup(state):
    # [설계] 조회 노드. 주문 데이터를 변경하지 않는 조회 작업이므로 여러 번 반복해도 안전하다.
    state["order"] = ORDERS.get(state["order_id"])


def assess(state):
    # [설계] 판정 노드. 업무 규칙(배송 후 7일)은 LLM이 아니라 코드가 결정한다.
    # 외부 호출 없이 State에 판정 결과를 저장하므로 입력과 결과를 쉽게 테스트할 수 있다.
    state["eligible"] = state["order"]["days"] <= 7


def refund(state):
    # 이 예제는 메모리의 환불 여부만 변경하며 실제 환불이나 DB 저장은 하지 않는다.
    # [설계] nodes.py에서는 기존 환불 기록을 먼저 조회하고, 없을 때만 중복 처리 방지 키로
    # 환불을 요청한다. tools.py에서 DB 저장과 중복 환불 방지를 학습한다.
    state["refunded"] = True


def respond(state):
    # [설계] 응답 노드. 최종 사용자 메시지는 한 곳에서만 만든다.
    # 여기서 분기 조건이 route와 중복 검사되는데, main 버전은 status(waiting_input /
    # completed / escalated)와 reason 필드를 두어 "왜 이 응답인지"를 State에 남긴다.
    if state["intent"] == "unknown":
        state["response"] = "주문 조회 또는 환불 중 원하는 업무를 알려 주세요."
    elif not state["order_id"]:
        state["response"] = "처리할 주문번호 하나를 ORD-1001 형식으로 알려 주세요."
    elif state["order"] is None:
        state["response"] = "주문을 찾을 수 없습니다."
    elif state["intent"] == "lookup":
        state["response"] = f"주문 금액: {state['order']['amount']:,}원"
    elif state["refunded"]:
        state["response"] = f"환불 완료: {state['order']['amount']:,}원 (모의 처리)"
    else:
        state["response"] = "환불 불가: 배송 완료 후 7일이 지났습니다."


def route(state, node):
    # [설계] Routing. 노드 안에서 다음 노드를 정하지 않고, 별도 함수가
    # "끝난 노드 + State"만으로 결정한다. 작업(무엇을 하는가)과 흐름(다음은 어디인가)을
    # 분리해야 그래프를 그림으로 그릴 수 있고, 흐름만 따로 테스트할 수 있다.
    # 모든 경로가 respond → end에 도달하는지 확인한다. 종료 조건이 빠진 그래프는 무한 루프다.
    if node == "analyze":
        return "lookup" if state["order_id"] and state["intent"] != "unknown" else "respond"
    if node == "lookup":
        return "assess" if state["order"] and state["intent"] == "refund" else "respond"
    if node == "assess":
        return "refund" if state["eligible"] else "respond"
    if node == "refund":
        return "respond"
    return "end"


# [설계] 노드 목록(이름 → 함수). 루프는 이름만 알고 구현은 모른다.
# LangGraph의 add_node(name, fn)가 만드는 것도 이 표다.
NODES = {"analyze": analyze, "lookup": lookup, "assess": assess,
         "refund": refund, "respond": respond}


async def run(request, analyzer=None):
    analyzer = analyzer or ClaudeAnalyzer()
    # 기본 예제는 구조 설명용이다. 실행 정책과 환불 기록 DB는 main.py에서 다룬다.
    # [설계] State 초기값. 모든 키를 처음부터 선언해 두면 노드가 "없는 키"를 만나지 않는다.
    state = {"request": request, "intent": "", "order_id": None, "order": None,
             "eligible": False, "refunded": False, "response": ""}
    current_node = "analyze"
    # [설계] Execution Loop. 이 while이 곧 Agent 실행기이며, LangGraph의 graph.invoke가
    # 내부에서 하는 일도 본질적으로 "노드 실행 → 라우팅 → 반복"이다.
    # 이 루프에는 최대 단계 수·전체 시간 제한이 없다. route에 버그가 있으면 멈추지 않는다.
    # runtime.step은 max_steps와 total_timeout_s를 검사해 실행을 제한한다.
    while current_node != "end":
        print(f"실행 노드: {current_node}")
        if current_node == "analyze":
            await analyze(state, analyzer)  # LLM 노드만 비동기 외부 호출이라 따로 처리
        else:
            NODES[current_node](state)
        current_node = route(state, current_node)
    return state


if __name__ == "__main__":
    import sys
    print(asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "ORD-1001 환불해 주세요"))["response"])
