"""테스트용 입력과 고정 응답(fixture). 모델의 자연어 해석 성능은 검증하지 않는다.

[설계] LLM을 "입력 → 고정 출력 표"로 대체한다. 정의하지 않은 입력은 조용히 넘기지 않고
AssertionError로 드러내, 테스트가 의도치 않은 경로를 타는 일을 막는다.
ClaudeAnalyzer와 같은 generate(messages, max_tokens, timeout) 시그니처를 유지하므로
Runtime 코드를 변경하지 않고 테스트용 분석기로 교체할 수 있다.
"""
import json
from types import SimpleNamespace


def message(value, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(value))],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        stop_reason=stop_reason,
    )


class FixtureAnalyzer:
    async def generate(self, messages, max_tokens, timeout):
        request = "\n".join(m["content"] for m in messages)
        cases = {
            "환불": ("refund", ""), "환불해 주세요": ("refund", ""),
            "안녕하세요": ("unknown", ""),
            "ORD-1001 ORD-1002 환불": ("refund", ""),
            "환불해 주세요\nORD-1001": ("refund", "ORD-1001"),
            "ORD-1001 배송 조회": ("lookup", "ORD-1001"),
            "ORD-1001 조회": ("lookup", "ORD-1001"),
        }
        for oid in ["ORD-1001", "ORD-1002", "ORD-1003", "ORD-1004", "ORD-1005", "ORD-9999"]:
            cases[f"{oid} 환불"] = ("refund", oid)
            cases[f"{oid} 환불해 주세요"] = ("refund", oid)
        if request not in cases:
            raise AssertionError(f"정의하지 않은 테스트 입력: {request}")
        intent, order_id = cases[request]
        return message({"intent": intent, "order_id": order_id})
