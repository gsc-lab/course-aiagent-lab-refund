# 주문·환불 고객지원 Agent — Claude Sonnet 5

요청 분석부터 실제 Claude API를 호출한다. State·Node·Routing·Execution Loop는 Python으로 직접 구현하고, 마지막에 같은 Node를 LangGraph로 실행한다. **순수 Python 구현은 Agent 프레임워크 없이 실행 구조를 만든다는 뜻**이며, Claude 연결에는 Anthropic SDK를 사용한다.

- Claude: 고객의 문맥을 해석하여 `intent`와 `order_id`를 반환한다.
- Python: 응답 형식 검증, 상태 갱신, 다음 Node 선택, 환불 정책 판단을 담당한다.
- 주문·환불 API: 로컬 SQLite 기반 모의 시스템이다. 실제 결제는 발생하지 않는다.
- Claude API: 실제 네트워크 요청이며 과금된다. 주문 API의 가상 비용과 구분한다.

## 설치와 실행

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/)가 필요하다. 이 폴더에서 실행한다. Windows·macOS·Linux 명령이 같다.

```powershell
uv sync    # .venv 생성 + uv.lock에 고정된 의존성 설치
uv run refund-basic "ORD-1001 배송 조회"
uv run refund-agent "ORD-1001 환불해 주세요" --output artifacts/refund
```

API 키는 이 폴더의 `.env` 파일에 `ANTHROPIC_API_KEY=발급받은_API_키` 한 줄로 적는다. `.env`는 `.gitignore`에 있어 커밋되지 않는다. 소스에 키를 넣지 않는다. 두 명령은 시작할 때 `.env`를 자동으로 읽는다(`python-dotenv`). 셸 환경 변수로 직접 설정한 값이 있으면 그 값이 우선한다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `src/refund_agent/` | Agent 소스 (아래 "코드를 읽는 순서"의 파일) |
| `tests/` | 고정 응답 기반 테스트 |
| `docs/` | `LESSON.md` 수업 진행, `WORKFLOW.md` 흐름도, `VALIDATION.md` 검증 기록 |
| `pyproject.toml`, `uv.lock` | 의존성 선언과 고정 버전 |

`refund-basic`은 `basic.py`, `refund-agent`는 `main.py`를 실행하는 명령이다.

`basic.py`는 작은 dict State와 Loop를 읽기 위한 예제다. 주문 조회·환불 의도 모두 Claude로 분석하지만, SQLite에 저장하는 환불 기록·재시도·체크포인트는 `main.py`에 있다. `basic.py`의 환불은 메모리상의 모의 표시다.

새 출력 폴더의 정상 환불은 `completed`, 5단계, 주문 API 3회, 가상 비용 5로 끝난다. Claude 호출은 별도 `LLM 호출`, `입력 토큰`, `출력 토큰` 항목에 표시한다. 실제 자연어 해석 결과와 토큰 수는 실행마다 달라질 수 있다. 같은 폴더의 DB에 해당 주문의 환불 기록이 있으면 그 결과를 재사용한다.

## 코드를 읽는 순서

`src/refund_agent/` 안의 파일이다.

| 파일 | 역할 |
|---|---|
| `basic.py` | 작은 State → Node → Routing → Loop |
| `state.py` | 업무 데이터·대화 메시지·실행 상태·정책 |
| `nodes.py` | 요청 분석 → 주문 조회 → 조건 판단 → 환불 → 안내 |
| `llm.py` | system/user 메시지 → Claude → JSON 검증 |
| `tools.py` | 본인 주문·정책 재검증, 환불 기록 DB·중복 환불 방지 |
| `runtime.py` | LLM/Tool 호출 제한·재시도·시간·기록·체크포인트 |
| `main.py` | CLI 입력, 추가 답변, 재개 |
| `langgraph_version.py` | 같은 Node를 StateGraph로 연결 |

`llm.py`의 응답 형식은 `{"intent":"refund","order_id":"ORD-1001"}`이다.
허용 의도는 `lookup`, `refund`, `unknown`이다. 모호한 의도는 `unknown`, 누락되거나 여러 개로 모호한 주문번호는 빈 문자열로 반환하도록 지시한다. Python은 응답 필드·허용값·주문번호 형식과 고객이 제시한 번호인지 확인한다. 본인 주문 여부는 이후 주문 API에서 확인한다.

환불 가능 여부만 묻는 요청을 실제 환불 명령으로 해석하지 않도록 지시한다. 이 경우 현재 두 업무의 범위에서는 확인 질문을 한다. JSON 스키마 검증이 의미 해석의 정확성까지 보장하지는 않는다.

## 명령과 옵션

```
uv run refund-agent "<고객 요청>" [옵션]                       # 새 실행
uv run refund-agent --resume <체크포인트 경로> [--reply "<답변>"]   # 재개
uv run refund-agent --help                                      # 전체 옵션
```

| 옵션 | 기본값 | 뜻 | 언제 쓰나 |
|---|---|---|---|
| `--output 폴더` | `artifacts` | 로그·체크포인트·환불 기록 DB 저장 위치 | 실험마다 새 폴더를 준다. 같은 폴더의 DB에 환불 기록이 있으면 재사용된다 |
| `--scenario 이름` | `normal` | 주문 API 장애 재현: `flaky` 간헐 실패, `down` 지속 실패, `slow` 지연, `lost-response` 환불 후 응답 유실 | 3·5단계 장애 실험 |
| `--retries N` | 2 | 최초 호출 이후 추가 재시도 횟수 | `0`으로 두면 첫 실패에 바로 인계 |
| `--tool-timeout 초` | 0.2 | 주문 API 호출 1회 제한 시간 | `slow`와 함께 timeout 재현 |
| `--budget N` | 20 | 주문 API 가상 비용 합계 상한 | 환불(비용 3) 직전에 끊기는 값으로 예산 실험 |
| `--max-tool-calls N` | 10 | 재시도 포함 주문 API 호출 수 상한 | 횟수 제한 실험 |
| `--max-steps N` | 12 | 노드 실행 횟수 상한 | `2`로 두면 lookup 이후 강제 종료 |
| `--total-timeout 초` | 120 | 누적 실행 시간 상한 (입력 대기 제외) | 보통 바꾸지 않는다 |
| `--llm-timeout 초` | 30 | Claude 호출 1회 제한 시간 | 보통 바꾸지 않는다 |
| `--max-llm-calls N` | 6 | 재시도 포함 Claude 호출 수 상한 | `0`으로 두면 호출 전에 인계 |
| `--pause-after N` | 없음 | 노드 N개 실행 후 저장하고 중단 | 재개 실습용. python 엔진 전용 |
| `--resume 경로` | 없음 | 체크포인트에서 이어서 실행 | 중단·입력 대기 후 |
| `--reply "답변"` | 없음 | 입력 대기에 대한 고객 답변. `--resume`과 함께 | `waiting_input` 상태일 때만 |
| `--engine 이름` | `python` | 실행 루프: `python` 또는 `langgraph` | 6단계 비교 |

재개할 때 정책 옵션(`--retries` 등)은 무시되고 체크포인트에 저장된 값이 유지된다. 새 요청과 `--resume`은 같이 쓸 수 없다.

### 상황별 사용 예

**1. 정상 흐름 확인** — 조회, 환불 성공, 환불 불가를 각각 한 번씩 본다.

```powershell
uv run refund-agent "ORD-1001 환불하지 말고 배송만 조회해 주세요" --output artifacts/lookup
uv run refund-agent "ORD-1001 환불해 주세요" --output artifacts/refund
uv run refund-agent "ORD-1002 환불" --output artifacts/denied
```

확인할 것: 실행 단계 수(조회 3, 환불 5, 불가 4)와 `실행 상태`. 환불 성공은 주문 API 3회, 가상 비용 5로 끝난다.

**2. 장애와 재시도** — 같은 장애에서 재시도 정책을 바꿔 비교한다.

```powershell
uv run refund-agent "ORD-1001 환불" --scenario flaky --output artifacts/flaky
uv run refund-agent "ORD-1001 환불" --scenario flaky --retries 0 --output artifacts/flaky-noretry
uv run refund-agent "ORD-1001 환불" --scenario down --output artifacts/down
uv run refund-agent "ORD-1001 환불" --scenario slow --tool-timeout 0.05 --output artifacts/slow
```

확인할 것: `.trace.jsonl`의 `tool_error`·`retry` 이벤트와 `attempts`. `flaky`는 재시도로 완료되고, `--retries 0`과 `down`은 인계(`escalated`)로 끝난다. `slow`의 `tool_error`에는 `TimeoutError`와 `latency_ms`가 남는다.

**3. 한도 실험** — 호출을 "시작하기 전에" 끊기는지 본다.

```powershell
uv run refund-agent "ORD-1001 환불" --budget 2 --output artifacts/budget
uv run refund-agent "ORD-1001 환불" --max-steps 2 --output artifacts/steps
uv run refund-agent "ORD-1001 환불" --max-llm-calls 0 --output artifacts/llm-limit
```

확인할 것: `중단 사유`와 `.handoff.json`. 예산 2는 조회(1)+환불 기록 조회(1) 뒤 환불 요청(3)을 시작하지 않는다. 단계 2는 lookup까지만 실행된다.

**4. 입력 부족 → 답변으로 재개** — Human-in-the-loop.

```powershell
uv run refund-agent "환불해 주세요" --output artifacts/input
```

주문번호가 없으므로 "처리할 주문번호 하나를 ORD-1001 형식으로 알려 주세요."와 `실행 상태: 추가 입력 대기 (waiting_input)`, 그리고 `체크포인트: artifacts/input/<run_id>.checkpoint.json`이 출력된다. 그 경로를 `--resume`에 넣고 답을 `--reply`로 준다.

```powershell
uv run refund-agent --resume artifacts/input/<run_id>.checkpoint.json --reply "ORD-1001"
```

확인할 것: 로그의 `step`이 2부터 이어지고 `analyze`가 다시 실행된다. 답변은 `State.messages`에 추가되어 이전 요청과 함께 Claude에 전달되므로, 최신 답변으로 정정이 가능하다. `--reply` 없이 재개하면 "추가 정보가 필요합니다"로 거부된다.

**5. 중간 중단 → 재개** — 체크포인트가 실행의 전부임을 확인한다.

```powershell
uv run refund-agent "ORD-1001 환불" --pause-after 3 --output artifacts/resume
uv run refund-agent --resume artifacts/resume/<run_id>.checkpoint.json
```

첫 명령은 analyze → lookup → assess까지 실행하고 `실행 상태: 중단됨 · 재개 가능 (running)`으로 멈춘다. 둘째 명령은 refund부터 이어가며 Claude를 다시 호출하지 않는다. 확인할 것: 재개 후 `LLM 호출` 수가 늘지 않고 `step`이 4부터 시작한다.

**6. 응답 유실과 중복 환불** — 체크포인트만으로는 막을 수 없는 문제.

```powershell
uv run refund-agent "ORD-1001 환불" --scenario lost-response --output artifacts/lost
uv run refund-agent "ORD-1001 환불" --scenario lost-response --retries 0 --output artifacts/lost-noretry
```

첫 명령은 환불이 DB에 기록된 뒤 응답이 유실되고, 재시도가 같은 중복 처리 방지 키로 기존 기록을 돌려받아 완료된다. 둘째 명령은 인계로 끝나지만 `refunds.sqlite3`에는 환불 기록이 남아 있을 수 있다. `.handoff.json`의 `next_action`이 담당자에게 DB 확인을 요청한다. 아래 "중복 환불 방지"를 같이 읽는다.

**7. LangGraph 엔진** — 같은 노드·정책을 다른 실행기로.

```powershell
uv sync --extra langgraph
uv run refund-agent "ORD-1001 환불" --engine langgraph --output artifacts/langgraph
```

확인할 것: 최종 State와 로그 이벤트가 python 엔진과 같다. `--pause-after`는 이 엔진에서 지원하지 않는다.

`--scenario`는 주문·환불 모의 API 장애에만 적용한다. Claude 오류는 실제 서비스 또는 모의 테스트로 확인한다. 실험마다 새 출력 폴더를 쓰면 기존 환불 기록이 결과에 영향을 주지 않는다.

## 중복 환불 방지

같은 요청을 여러 번 보내도 한 번 처리한 것과 같은 결과를 유지하는 성질을 **멱등성**이라고 한다. 이 실습에서는 이를 **중복 환불 방지**로 구현한다.

`idempotency_key`는 **중복 처리 방지 키**다. 예를 들어 고객 `C001`의 주문 `ORD-1001`에는 항상 `refund:C001:ORD-1001`을 사용한다. API는 주문번호와 이 키에 대한 DB의 중복 저장 제한을 이용해 환불 기록을 한 건만 유지한다. 재시도 시에는 같은 환불번호와 금액을 반환한다. 코드의 식별자는 `idempotency_key`를 유지하고, 이후 설명에서는 “중복 처리 방지 키”라고 부른다.

## 체크포인트 재개 규칙

재개는 저장된 정책·횟수·토큰·실행 시간을 유지한다. 재개 명령의 새 정책 인자는 적용하지 않는다. 대기/프로세스 종료 시간은 실행 시간에서 제외한다. 체크포인트와 `refunds.sqlite3`를 함께 보관한다. 동일 실행의 동시 재개는 지원하지 않는다. API 키는 체크포인트에 저장하지 않는다. 이전 버전 체크포인트는 새 필드의 기본값으로 읽지만, 예전 전체 시간 제한도 그대로 유지되므로 새 실습은 새 실행을 권장한다.

## 실행 정책과 로그 확인

| 대상 | 기본 한도·동작 |
|---|---|
| Claude | 호출당 30초, 전체 실행에서 최대 6회, 응답 최대 512토큰 |
| Claude 입력 | 대화 전체 12,000자 이내 (`Policy.max_input_chars`) |
| 주문 Tool | 호출당 0.2초, 최대 10회, 가상 비용 20 |
| 전체 | 누적 실행 120초, 최대 12단계 |
| 재시도 | 일시 연결 오류·429·5xx·timeout만 최초 호출 + 추가 2회 |
| LLM 응답 오류 | JSON/필드 오류·거절·출력 잘림은 업무를 진행하지 않고 담당자 확인 요청 저장 |
| 입력 부족 | 질문 후 `waiting_input`; 답변을 받으면 분석 재개 |
| 중단 | 로컬 `.handoff.json`에 확인 요청 저장; 실제 담당자 전송 없음 |

SDK 자동 재시도는 꺼서 Runtime의 횟수 제한을 우회하지 않게 했다. LLM 재시도 대기 시간은 기본 0.5초부터 시작해 재시도마다 두 배로 늘어난다. 인증·권한·요청 설정 오류는 재시도하지 않는다. timeout은 서버 처리가 없었거나 요금이 발생하지 않았음을 뜻하지 않는다.

`--budget`은 **주문 Tool의 교육용 가상 비용만** 제한한다. Claude의 달러/원화 비용 상한이 아니다. LLM은 호출 횟수·입력 문자 수·출력 토큰 수로 사용량을 제한한다. 실제 결제 금액은 Anthropic 사용량에서 확인한다.

로그는 `.trace.jsonl`이다. `step_start/end`, `state_change`, `tool_start/end/error`, `llm_start/usage/end/error`, `retry`, `checkpoint`, `escalation`을 기록한다. `llm_usage`는 API가 응답한 토큰 수다. 응답 유실/프로세스 중단 시 서버에서 소비한 토큰이 로컬에 기록되지 않을 수 있으므로 실제 청구 금액과 대조할 완전한 사용량 자료는 아니다. `actual_cost_krw`는 계산하지 않아 `null`이다. 교육용 로그에는 고객 요청/State가 포함된다.

## 테스트와 LangGraph 비교

```powershell
uv run python -m unittest discover -s tests -v
uv sync --extra langgraph
uv run refund-agent "ORD-1001 환불" --engine langgraph --output artifacts/langgraph
```

테스트는 API 키 없이 고정 응답과 HTTP MockTransport를 사용한다. Claude 자연어 성능이나 실제 계정의 모델 접근 권한을 검증한 것은 아니다. 두 엔진의 비교도 같은 고정 모델 응답을 사용한다. 실제 모델로 비교하면 의도 해석이나 토큰 수가 달라질 수 있다.

## 공식 문서

2026-09-28 확인:
- [Claude Sonnet 5와 모델 ID](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5)
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

모델은 `claude-sonnet-5`로 지정한다. 단순 추출 예제이므로 thinking을 명시적으로 disabled로 설정하고 temperature/top_p/top_k는 지정하지 않는다.
