"""Node는 작업을, route는 다음 이동을 담당한다.

[설계] 노드 계약
  - 시그니처는 모두 async def node(state, runtime). 노드는 State만 읽고 쓴다.
  - 외부 세계(LLM, 주문 API)에는 runtime.analyze_request / runtime.call로만 나간다.
    그래서 재시도·timeout·예산·로그·체크포인트 코드가 노드에는 한 줄도 없다.
    노드는 "업무"만, runtime은 "실행 정책"만 담당한다는 분리가 이 실습의 핵심이다.
  - 노드는 다음 노드를 정하지 않는다. 그 일은 아래 route가 한다.
  - 노드 이름 다섯 개는 WORKFLOW.md의 흐름도, LangGraph 그래프의 노드와 1:1로 대응한다.
"""


async def analyze(state, runtime):
    # [설계] LLM 노드. runtime이 호출 횟수·시간·재시도를 통제하고 Python 검증을 마친
    # 결과만 돌려준다. 노드는 검증된 두 값을 State에 옮기는 일만 한다.
    result = await runtime.analyze_request()
    state.intent = result["intent"]
    state.order_id = result["order_id"]
    # [설계] Human-in-the-loop. 정보가 부족하면 추측하지 않고 status를 waiting_input으로
    # 바꾼 뒤 "질문"을 response에 담는다. 루프는 이 status를 보고 저장 후 종료하고,
    # 고객 답변(--reply)이 오면 같은 노드부터 재개한다. 자동 재시도 대상이 아니다.
    if state.intent == "unknown":
        state.status = "waiting_input"
        state.response = "주문 조회 또는 환불 중 원하는 업무를 알려 주세요."
    elif not state.order_id:
        state.status = "waiting_input"
        state.response = "처리할 주문번호 하나를 ORD-1001 형식으로 알려 주세요."


async def lookup(state, runtime):
    # [설계] Tool 노드(읽기). runtime.call을 거치므로 timeout·재시도·비용이 자동 적용된다.
    # customer_id를 함께 넘겨 "본인 주문인지"는 Tool이 검사한다. 권한 검사는 Tool의 책임이다.
    state.order = await runtime.call(
        "get_order", order_id=state.order_id, customer_id=state.customer_id
    )


async def assess(state, runtime):
    # [설계] 판정 노드. I/O가 없는 순수 규칙이다. LLM에 맡기지 않는 이유:
    # 결정론적이어야 하고, 테스트 가능해야 하며, 거절 사유를 설명할 수 있어야 한다.
    # reason을 State에 남겨 respond·로그·인계 파일이 같은 근거를 쓴다.
    order = state.order
    state.eligible = order["status"] == "delivered" and 0 <= order["days"] <= 7
    state.reason = (
        "배송 완료 후 7일 이내" if state.eligible
        else "배송이 아직 완료되지 않았습니다." if order["status"] != "delivered"
        else "배송 완료 후 7일이 지났습니다."
    )


async def refund(state, runtime):
    # 체크포인트가 환불 직전이어도 기존 환불 결과를 먼저 조회해 처리 여부를 확인한다.
    # [설계] 쓰기 노드의 "조회 후 쓰기(read-before-write)" 패턴.
    #   1) get_refund : 이전 실행이나 응답이 유실된 호출의 환불 기록을 DB에서 확인
    #   2) 없을 때만 create_refund, 그것도 고객·주문번호로 만든 동일한 멱등성 키와 함께 호출
    # 체크포인트 저장과 외부 쓰기는 한 트랜잭션이 아니므로 "쓰기 성공 후 저장 실패"가
    # 생길 수 있다. 1)의 조회로 기존 결과를 복원하고, DB의 고유 제약으로
    # 같은 주문이나 멱등성 키의 환불 기록이 중복 저장되지 않게 한다.
    state.refund = await runtime.call(
        "get_refund", order_id=state.order_id, customer_id=state.customer_id
    )
    if state.refund is None:
        state.refund = await runtime.call(
            "create_refund", order_id=state.order_id, customer_id=state.customer_id,
            idempotency_key=f"refund:{state.customer_id}:{state.order_id}",
        )


async def respond(state, runtime):
    # [설계] 응답 노드이자 정상 종료점. status를 completed로 바꾸는 유일한 곳이다.
    # completed는 "고객 안내 완료"이지 "환불 완료"가 아니다. 확인된 환불 결과는 state.refund에 저장된다.
    # 환불 완료 문장은 반드시 Tool이 돌려준 영수증(refund_id)에 근거한다. 추측하지 않는다.
    if state.intent == "lookup":
        delivery_status = {"delivered": "배송 완료", "shipping": "배송 중"}.get(
            state.order["status"], state.order["status"]
        )
        state.response = f"{state.order_id}: {delivery_status}, 주문 금액 {state.order['amount']:,}원"
    elif state.refund:
        state.response = (
            f"{state.order_id} 환불 완료: {state.refund['amount']:,}원 "
            f"(환불번호 {state.refund['refund_id']}, 모의 처리)"
        )
    else:
        state.response = f"{state.order_id} 환불 불가: {state.reason}"
    state.status = "completed"


# [설계] 노드 레지스트리. runtime.step은 이 표에서 이름으로 노드를 찾고,
# langgraph_version.py는 같은 표를 순회하며 add_node 한다.
NODES = {"analyze": analyze, "lookup": lookup, "assess": assess, "refund": refund, "respond": respond}


def route(state, finished_node):
    # [설계] Routing 우선순위: (1) 종료·대기 status → (2) 그래프 구조.
    # status 검사를 먼저 두면 어떤 노드에서 대기·인계가 발생해도 같은 규칙으로 멈춘다.
    if state.status == "waiting_input":
        return "analyze"  # 루프를 돌지 않고 저장 후 종료; 답변이 오면 여기서 재개
    if state.status in {"completed", "escalated"}:
        return "end"
    # [설계] 아래는 WORKFLOW.md 흐름도의 화살표를 그대로 옮긴 것이다.
    # 분기 조건은 모두 State의 값이며, 노드가 한 일을 여기서 다시 계산하지 않는다.
    if finished_node == "analyze":
        return "lookup"
    if finished_node == "lookup":
        return "assess" if state.intent == "refund" else "respond"
    if finished_node == "assess":
        return "refund" if state.eligible else "respond"
    if finished_node == "refund":
        return "respond"
    # [설계] 정의되지 않은 전이는 조용히 end로 보내지 않고 오류로 드러낸다.
    # runtime.step이 이 예외를 잡아 인계 처리한다.
    raise ValueError(f"정의되지 않은 라우팅: {finished_node}")
