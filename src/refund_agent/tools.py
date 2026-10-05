"""Tool/API: 실제 결제 대신 SQLite를 사용하는 로컬 모의 서비스.

[설계] Tool 층의 책임
  - 외부 시스템을 함수 하나로 감싸, 노드는 "무엇을 호출하는지"만 알고 "어떻게"는 모른다.
  - 실패를 재시도 가능(TransientError) / 불가(ServiceError)로 분류해 던진다.
    runtime은 이 분류만 보고 재시도 여부를 정한다. Tool이 스스로 재시도하지 않는다.
  - 권한(본인 주문)과 업무 규칙(7일)을 Tool이 다시 검사한다. State는 조작될 수 있다.
  - 쓰기 API는 중복 처리 방지 키를 받아 같은 요청의 중복 실행을 서버 쪽에서 막는다.
  - scenario 인자로 장애를 재현해 정책(재시도·timeout·인계)을 재현 가능하게 시험한다.
"""
import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path


class TransientError(Exception):
    """재시도 가능한 일시적 오류."""
    # [설계] 네트워크·일시 장애·과부하 계열. runtime은 TimeoutError와 함께 이것만 재시도한다.


class ServiceError(Exception):
    """재시도로 해결되지 않는 업무 오류."""
    # [설계] 권한·업무 규칙·형식 오류 계열. 다시 보내도 결과가 같으므로 즉시 인계한다.


# [설계] 모의 데이터. 실습 시나리오의 경계값을 일부러 포함한다:
# 3일(정상), 20일(기간 초과), 타 고객(권한), 배송 중(상태), 정확히 7일(경계).
ORDERS = {
    "ORD-1001": {"customer_id": "C001", "amount": 32000, "days": 3, "status": "delivered"},
    "ORD-1002": {"customer_id": "C001", "amount": 45000, "days": 20, "status": "delivered"},
    "ORD-1003": {"customer_id": "C002", "amount": 12000, "days": 2, "status": "delivered"},
    "ORD-1004": {"customer_id": "C001", "amount": 8000, "days": 0, "status": "shipping"},
    "ORD-1005": {"customer_id": "C001", "amount": 16000, "days": 7, "status": "delivered"},
}


class MockAPI:
    def __init__(self, database: Path, scenario: str = "normal"):
        self.database = database
        self.scenario = scenario
        self.seen: dict[str, int] = {}  # 도구별 호출 횟수. 장애 재현과 테스트 검증에 사용
        database.parent.mkdir(parents=True, exist_ok=True)
        # [설계] 환불 기록은 프로그램 종료 후에도 SQLite 파일에 남는다.
        # 체크포인트(JSON)와 따로 저장하므로 재개 시 DB에서 실제 처리 여부를 확인한다.
        # order_id의 PRIMARY KEY와 idempotency_key의 UNIQUE 제약으로 중복 저장을 막는다.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS refunds (
                order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
                refund_id TEXT NOT NULL, amount INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE)""")

    async def _before(self, tool: str):
        # [설계] 장애를 재현하는 부분. flaky(1회 실패 후 성공) / down(계속 실패) / slow(60초 지연).
        # slow의 대기 중 호출 시간 제한에 도달하면 runtime의 wait_for가 코루틴을 취소한다.
        # 장애를 코드로 재현하여 재시도와 시간 제한이 의도대로 동작하는지 검증한다.
        self.seen[tool] = self.seen.get(tool, 0) + 1
        if tool == "get_order":
            if self.scenario == "flaky" and self.seen[tool] == 1:
                raise TransientError("주문 서비스 일시 장애")
            if self.scenario == "down":
                raise TransientError("주문 서비스 지속 장애")
            if self.scenario == "slow":
                await asyncio.sleep(60)  # wait_for가 실제로 취소하는 비동기 지연
        await asyncio.sleep(0.001)

    def _owned_order(self, order_id: str, customer_id: str) -> dict:
        # [설계] 권한 검사는 Tool에서 한다. LLM도 State도 아닌 서버가 소유자를 확인한다.
        order = ORDERS.get(order_id)
        if order is None or order["customer_id"] != customer_id:
            # 타인 주문의 존재 및 상세 정보를 노출하지 않는다.
            # [설계] "없음"과 "남의 주문"을 같은 메시지로 답해 존재 여부를 유추할 수 없게 한다.
            raise ServiceError("해당 고객의 주문을 찾을 수 없습니다.")
        return {"order_id": order_id, **order}

    async def get_order(self, order_id: str, customer_id: str) -> dict:
        # [설계] 읽기 도구. 주문·환불 데이터를 변경하지 않으므로 재시도해도 중복 환불이 발생하지 않는다.
        await self._before("get_order")
        return self._owned_order(order_id, customer_id)

    async def get_refund(self, order_id: str, customer_id: str) -> dict | None:
        # [설계] 쓰기 전 확인용 읽기 도구. None은 조회 시점에 해당 주문의 환불 기록이 없다는 뜻이다.
        # refund 노드는 이 값을 보고 create_refund 호출 여부를 정한다.
        await self._before("get_refund")
        self._owned_order(order_id, customer_id)
        with closing(sqlite3.connect(self.database)) as db, db:
            row = db.execute(
                "SELECT refund_id, amount FROM refunds WHERE order_id=? AND customer_id=?",
                (order_id, customer_id),
            ).fetchone()
        return {"refund_id": row[0], "amount": row[1]} if row else None

    async def create_refund(self, order_id: str, customer_id: str, idempotency_key: str) -> dict:
        await self._before("create_refund")
        order = self._owned_order(order_id, customer_id)
        # State를 조작해도 쓰기 API가 업무 규칙을 다시 확인한다.
        # [설계] 쓰기 도구의 3중 방어: (1) 소유자 (2) 업무 규칙 재검증 (3) 중복 처리 방지 키 형식.
        # assess 노드가 이미 판정했지만 Tool은 그 판정을 신뢰하지 않는다.
        if order["status"] != "delivered" or not 0 <= order["days"] <= 7:
            raise ServiceError("환불 조건 불충족: 배송 완료 후 7일 이내만 가능")
        if idempotency_key != f"refund:{customer_id}:{order_id}":
            raise ServiceError("중복 처리 방지 키가 고객·주문번호와 일치하지 않습니다.")
        refund_id = f"RF-{order_id}"
        # [설계] INSERT OR IGNORE: 같은 주문의 두 번째 삽입은 조용히 무시된다.
        # 그래서 재시도·재개로 create_refund가 두 번 와도 환불 기록은 1건이고 같은 환불번호와 금액을 반환한다.
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO refunds VALUES (?, ?, ?, ?, ?)",
                (order_id, customer_id, refund_id, order["amount"], idempotency_key),
            )
        # 커밋은 끝났지만 네트워크 응답이 오지 않는 경우를 재현한다.
        # [설계] "응답 유실" 시나리오. 호출자는 TimeoutError를 받지만 서버는 이미 커밋했다.
        # 응답 시간 초과 후에도 DB에 환불 기록이 남는 상황을 5단계 실습에서 확인한다.
        # test_lost_response_does_not_duplicate와 test_uncertain_write_escalates_without_false_success로 검증한다.
        if self.scenario == "lost-response" and self.seen["create_refund"] == 1:
            await asyncio.sleep(60)
        return {"refund_id": refund_id, "amount": order["amount"]}
