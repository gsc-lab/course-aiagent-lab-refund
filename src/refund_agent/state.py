"""State: 노드가 공유하고 체크포인트에 저장하는 데이터.

[설계] State는 노드들이 공유하는 업무 데이터와 실행 상태를 모아 둔다.
  - 노드끼리는 함수 인자로 값을 주고받지 않고 오직 State를 통해서만 소통한다.
  - 체크포인트는 State와 Policy를 파일로 저장한다. 메모리에만 둔 정보는 재개 시 복원되지 않는다.
    (재시도 횟수 attempts, 누적 비용 cost_units까지 State에 둔 이유가 이것이다)
  - 업무 State(주문·환불·응답)와 실행 State(run_id·steps·비용·오류)를 한 클래스에 두되
    아래 구획처럼 구분해서 읽는다.
Policy는 State와 달리 실행 중 바뀌지 않는 "한도"다. frozen으로 선언해 노드가 정책을
실수로 변경하는 일을 막고, 체크포인트에 함께 저장해 재개 시에도 같은 한도를 유지한다.
"""
from dataclasses import dataclass, field
from typing import Literal, TypeAlias
from uuid import uuid4


# 업무 노드 이름과 루프의 종료 표식. LangGraph의 END("__end__")와는 구분한다.
NodeName: TypeAlias = Literal["analyze", "lookup", "assess", "refund", "respond"]
NextNode: TypeAlias = NodeName | Literal["end"]
RunStatus: TypeAlias = Literal["running", "waiting_input", "completed", "escalated"]


@dataclass
class State:
    # ── 입력 ─────────────────────────────────────────────────────────────
    request: str
    customer_id: str = "C001"  # 실습에서 인증된 고객을 대신하는 고정 값
    # [설계] 인증 정보는 LLM이 추출하지 않는다. 시스템이 State에 넣어 주는 값이며,
    # Tool은 이 값으로 본인 주문 여부를 검사한다.

    # ── 실행 State: 어디까지 왔고 어떤 상태인가 ──────────────────────────
    run_id: str = field(default_factory=lambda: uuid4().hex)  # 로그·체크포인트·인계 파일을 연결하는 실행 식별자
    current_node: NextNode = "analyze"  # 재개 시 이 노드부터 다시 실행한다
    status: RunStatus = "running"  # 실행·대기·완료·인계 (WORKFLOW.md 표 참고)

    # ── 업무 State: 노드가 순서대로 채워 가는 업무 결과 ──────────────────
    intent: str = ""  # analyze가 채움: lookup / refund / unknown
    order_id: str = ""  # analyze가 채움: 빈 문자열이면 입력 대기
    order: dict | None = None  # lookup이 채움: Tool 결과
    eligible: bool = False  # assess가 채움: 업무 규칙 판정
    reason: str = ""  # assess가 채움: 판정 근거를 State에 남겨야 응답·로그·인계에서 설명할 수 있다
    refund: dict | None = None  # refund가 채움: 환불번호와 금액. None이면 "환불이 확인되지 않음"
    response: str = ""  # respond(또는 입력 대기·인계 시)가 채움
    messages: list[dict] = field(default_factory=list)  # 고객 요청과 추가 답변
    # [설계] 대화 이력도 State다. 재개 후 LLM에 "지금까지의 대화"를 다시 줄 수 있어야
    # 최신 답변으로 정정·보완이 가능하다.

    # ── 실행 State: Policy 한도와 비교할 누적 사용량 ─────────────────────
    llm_calls: int = 0  # LLM 호출 전에 미리 누적한 시도 횟수. 도구 호출과 별도로 센다
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0  # 최대 단계 수(max_steps)와 비교
    tool_calls: int = 0  # 재시도 포함 총 호출 수
    cost_units: int = 0  # 실제 요금이 아닌 교육용 가상 비용
    elapsed_s: float = 0.0  # 입력 대기·프로세스 종료 시간은 제외한 순수 실행 시간
    attempts: dict[str, int] = field(default_factory=dict)  # 도구는 "노드.도구", LLM은 "analyze.단계.claude"별 시도 횟수
    last_error: str = ""  # 인계 사유·디버깅 근거


@dataclass(frozen=True)
class Policy:
    # [설계] Execution Policy. 모든 항목은 runtime.py에서 "호출 전에" 검사하며, 한도에 걸리면
    # PolicyStop으로 인계한다. 한도를 넘는 호출은 시작조차 하지 않는다(사후 차단이 아니라 사전 차단).
    # 횟수 한도는 재시도 < 총 호출 < 노드 수의 세 층, 시간 한도는 호출당 < 실행 전체의 두 층이다.

    # ── 실행 전체: Runtime.step이 노드를 실행하기 전에 검사 ──────────────────
    max_steps: int = 12  # 노드 실행 횟수 상한. route 버그로 무한 루프가 돌아도 여기서 끊긴다
    total_timeout_s: float = 120.0  # 누적 실행 시간 상한(초). 입력 대기·종료 중 시간은 제외. 모든 호출 전과 재시도 대기 전에도 검사

    # ── 주문 API 호출: Runtime.call (lookup·refund 노드) ─────────────────────
    max_tool_calls: int = 10  # 재시도까지 포함한 총 호출 횟수 상한
    max_cost_units: int = 20  # 가상 비용 합계 상한. 이번 호출 비용을 더해 비교하므로 환불(3) 직전에 끊기는 실험이 가능
    tool_timeout_s: float = 0.2  # 호출 1회의 대기 상한(초). 남은 전체 시간과 비교해 작은 쪽을 wait_for에 적용

    # ── Claude 호출: Runtime.analyze_request (analyze 노드). 실제 과금 대상이라 도구와 따로 센다 ──
    max_llm_calls: int = 6  # 재시도까지 포함한 총 호출 횟수 상한
    max_input_chars: int = 12000  # 대화 이력 총 글자 수 상한. 답변이 쌓여도 입력이 무한히 커지지 않게 한다
    llm_max_tokens: int = 512  # 응답 출력 토큰 상한. 두 필드 JSON이라 충분하며, 넘치면 stop_reason이 달라져 거부된다
    llm_timeout_s: float = 30.0  # 호출 1회의 대기 상한(초). SDK timeout과 바깥 wait_for 양쪽에 적용

    # ── 재시도: call과 analyze_request 공통. 일시 오류·timeout에만 적용하고 권한·형식 오류는 재시도하지 않는다 ──
    max_retries: int = 2  # 최초 1회 이후 추가 시도 횟수
    backoff_s: float = 0.01  # 재시도 사이 대기 시간의 시작값(초). 시도마다 두 배. Claude는 최소 0.5초부터. 남은 전체 시간을 넘으면 기다리지 않고 인계

    def __post_init__(self):
        # [설계] 정책 값은 실행 전에 검증한다. 음수나 NaN은 한도 비교를 잘못 동작하게 할 수 있다.
        for name in ("max_retries", "max_steps", "max_tool_calls", "max_cost_units", "max_llm_calls", "llm_max_tokens", "max_input_chars"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name}: 0 이상의 정수가 필요합니다.")
        if self.llm_max_tokens == 0:
            raise ValueError("llm_max_tokens: 1 이상의 정수가 필요합니다.")
        for name in ("tool_timeout_s", "total_timeout_s", "backoff_s", "llm_timeout_s"):
            import math
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name}: 0 이상의 유한한 값이 필요합니다.")
