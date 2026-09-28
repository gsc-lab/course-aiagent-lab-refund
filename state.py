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
from uuid import uuid4


@dataclass
class State:
    # ── 입력 ─────────────────────────────────────────────────────────────
    request: str
    customer_id: str = "C001"  # 실습에서 인증된 고객을 대신하는 고정 값
    # [설계] 인증 정보는 LLM이 추출하지 않는다. 시스템이 State에 넣어 주는 값이며,
    # Tool은 이 값으로 본인 주문 여부를 검사한다.

    # ── 실행 State: 어디까지 왔고 어떤 상태인가 ──────────────────────────
    run_id: str = field(default_factory=lambda: uuid4().hex)  # 로그·체크포인트·인계 파일을 연결하는 실행 식별자
    current_node: str = "analyze"  # 재개 시 이 노드부터 다시 실행한다
    status: str = "running"  # running / waiting_input / completed / escalated (WORKFLOW.md 표 참고)

    # ── 업무 State: 노드가 순서대로 채워 가는 업무 결과 ──────────────────
    intent: str = ""  # analyze가 채움: lookup / refund / unknown
    order_id: str = ""  # analyze가 채움: 빈 문자열이면 입력 대기
    order: dict | None = None  # lookup이 채움: Tool 결과
    eligible: bool = False  # assess가 채움: 업무 규칙 판정
    reason: str = ""  # assess가 채움: 판정 근거를 State에 남겨야 응답·로그·인계에서 설명할 수 있다
    refund: dict | None = None  # refund가 채움: 영수증. None이면 "환불이 확인되지 않음"
    response: str = ""  # respond(또는 입력 대기·인계 시)가 채움
    messages: list[dict] = field(default_factory=list)  # 고객 요청과 추가 답변
    # [설계] 대화 이력도 State다. 재개 후 LLM에 "지금까지의 대화"를 다시 줄 수 있어야
    # 최신 답변으로 정정·보완이 가능하다.

    # ── 실행 State: Policy 한도와 비교할 누적 사용량 ─────────────────────
    llm_calls: int = 0  # LLM 호출 전 예약한 횟수. 도구 호출과 별도로 센다
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0  # Termination 가드(max_steps)와 비교
    tool_calls: int = 0  # 재시도 포함 총 호출 수
    cost_units: int = 0  # 실제 요금이 아닌 교육용 가상 비용
    elapsed_s: float = 0.0  # 입력 대기·프로세스 종료 시간은 제외한 순수 실행 시간
    attempts: dict[str, int] = field(default_factory=dict)  # 도구는 "노드.도구", LLM은 "analyze.단계.claude"별 시도 횟수
    last_error: str = ""  # 인계 사유·디버깅 근거


@dataclass(frozen=True)
class Policy:
    # [설계] Execution Policy. 각 항목은 runtime.py에서 "호출 전에" 검사한다.
    # 한도를 넘는 호출은 시작조차 하지 않는 것이 원칙이다(사후 차단이 아니라 사전 차단).
    max_retries: int = 2  # 최초 1회 + 추가 2회. 도구 호출 또는 LLM 분석의 재시도 한도
    tool_timeout_s: float = 0.2  # 도구 호출 1회의 상한
    total_timeout_s: float = 120.0  # 입력 대기 시간 제외, 실행 시간 누적
    max_steps: int = 12  # Termination: 라우팅 버그·무한 루프 방지
    max_tool_calls: int = 10  # 재시도까지 포함한 총 도구 호출 수
    max_cost_units: int = 20  # Budget: 도구별 가상 비용의 합계 상한
    llm_timeout_s: float = 30.0  # LLM 호출 1회의 상한 (도구와 별도 관리)
    max_llm_calls: int = 6  # LLM 호출 수 상한 (실제 과금 대상)
    llm_max_tokens: int = 512  # 출력 토큰 상한
    max_input_chars: int = 12000  # 입력 크기 상한. 대화가 길어져도 무한히 보내지 않는다
    backoff_s: float = 0.01  # 재시도 대기 기본값. 시도마다 지수적으로 늘어난다 (runtime.call)

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
