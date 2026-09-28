# 주문·환불 고객지원 Agent — Claude Sonnet 5

요청 분석부터 실제 Claude API를 호출한다. State·Node·Routing·Execution Loop는 Python으로 직접 구현하고, 마지막에 같은 Node를 LangGraph로 실행한다. **순수 Python 구현은 Agent 프레임워크 없이 실행 구조를 만든다는 뜻**이며, Claude 연결에는 Anthropic SDK를 사용한다.

- Claude: 고객의 문맥을 해석하여 `intent`와 `order_id`를 반환한다.
- Python: 응답 형식 검증, 상태 갱신, 다음 Node 선택, 환불 정책 판단을 담당한다.
- 주문·환불 API: 로컬 SQLite 기반 모의 시스템이다. 실제 결제는 발생하지 않는다.
- Claude API: 실제 네트워크 요청이며 과금된다. 주문 API의 가상 비용과 구분한다.

## 설치와 실행

Python 3.11 이상. 이 폴더에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 현재 PowerShell 세션에만 설정한다. 소스에 키를 넣지 않는다.
$env:ANTHROPIC_API_KEY = "발급받은_API_키"
.\.venv\Scripts\python.exe basic.py "ORD-1001 배송 조회"
.\.venv\Scripts\python.exe main.py "ORD-1001 환불해 주세요" --output artifacts/refund
```

`basic.py`는 작은 dict State와 Loop를 읽기 위한 예제다. 주문 조회·환불 의도 모두 Claude로 분석하지만, SQLite에 저장하는 환불 기록·재시도·체크포인트는 `main.py`에 있다. `basic.py`의 환불은 메모리상의 모의 표시다.

새 출력 폴더의 정상 환불은 `completed`, 5단계, 주문 API 3회, 가상 비용 5로 끝난다. Claude 호출은 별도 `LLM 호출`, `입력 토큰`, `출력 토큰` 항목에 표시한다. 실제 자연어 해석 결과와 토큰 수는 실행마다 달라질 수 있다. 같은 폴더의 DB에 해당 주문의 환불 기록이 있으면 그 결과를 재사용한다.

## 코드를 읽는 순서

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

`llm.py`의 응답 계약은 `{"intent":"refund","order_id":"ORD-1001"}`이다.
허용 의도는 `lookup`, `refund`, `unknown`이다. 모호한 의도는 `unknown`, 누락되거나 여러 개로 모호한 주문번호는 빈 문자열로 반환하도록 지시한다. Python은 응답 필드·허용값·주문번호 형식과 고객이 제시한 번호인지 확인한다. 본인 주문 여부는 이후 주문 API에서 확인한다.

환불 가능 여부만 묻는 요청을 실제 환불 명령으로 해석하지 않도록 지시한다. 이 경우 현재 두 업무의 범위에서는 확인 질문을 한다. JSON 스키마 검증이 의미 해석의 정확성까지 보장하지는 않는다.

## 시나리오

아래 명령의 `python`은 설치한 가상환경의 Python을 사용한다.

```powershell
python main.py "ORD-1001 환불하지 말고 배송만 조회해 주세요" --output artifacts/lookup
python main.py "ORD-1002 환불" --output artifacts/denied
python main.py "ORD-1001 환불" --scenario flaky --output artifacts/flaky
python main.py "ORD-1001 환불" --scenario down --output artifacts/down
python main.py "ORD-1001 환불" --scenario slow --tool-timeout 0.05 --output artifacts/slow
python main.py "ORD-1001 환불" --scenario lost-response --output artifacts/lost
python main.py "ORD-1001 환불" --budget 2 --output artifacts/budget
python main.py "ORD-1001 환불" --max-llm-calls 0 --output artifacts/llm-limit
```

`--scenario`는 주문·환불 모의 API 장애에만 적용한다. Claude 오류는 실제 서비스 또는 모의 테스트로 확인한다. 시나리오마다 새 출력 폴더를 사용하면 기존 환불 기록이 실습 결과에 영향을 주지 않는다.

## 입력 대기와 체크포인트 재개

```powershell
python main.py "환불해 주세요" --output artifacts/input
python main.py --resume "<출력된 체크포인트 경로>" --reply "ORD-1001"
python main.py "ORD-1001 환불" --pause-after 3 --output artifacts/resume
python main.py --resume "<출력된 체크포인트 경로>"
```

추가 답변은 `State.messages`에 고객 메시지로 보관하고 다음 분석 시 함께 전달한다. 과거 요청을 무조건 우선하지 않고 최신 답변의 정정을 반영하도록 지시한다. 키는 체크포인트에 저장하지 않는다.

재개는 저장된 정책·횟수·토큰·실행 시간을 유지한다. 재개 명령의 새 정책 인자는 적용하지 않는다. 대기/프로세스 종료 시간은 실행 시간에서 제외한다. 체크포인트와 `refunds.sqlite3`를 함께 보관한다. 동일 실행의 동시 재개는 지원하지 않는다. 이전 버전 체크포인트는 새 필드의 기본값으로 읽지만, 예전 전체 시간 제한도 그대로 유지되므로 새 실습은 새 실행을 권장한다.

## 실행 정책과 관측

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

SDK 자동 재시도는 꺼서 Runtime의 횟수 제한을 우회하지 않게 했다. LLM 재시도는 0.5초부터 지수적으로 대기한다. 인증·권한·요청 설정 오류는 재시도하지 않는다. timeout은 서버 처리가 없었거나 요금이 발생하지 않았음을 뜻하지 않는다.

`--budget`은 **주문 Tool의 교육용 가상 비용만** 제한한다. Claude의 달러/원화 비용 상한이 아니다. LLM은 호출 횟수·입력 문자 수·출력 토큰 수로 사용량을 제한한다. 실제 결제 금액은 Anthropic 사용량에서 확인한다.

로그는 `.trace.jsonl`이다. `step_start/end`, `state_change`, `tool_start/end/error`, `llm_start/usage/end/error`, `retry`, `checkpoint`, `escalation`을 기록한다. `llm_usage`는 API가 응답한 토큰 수다. 응답 유실/프로세스 중단 시 서버에서 소비한 토큰이 로컬에 기록되지 않을 수 있으므로 실제 청구 금액과 대조할 완전한 사용량 자료는 아니다. `actual_cost_krw`는 계산하지 않아 `null`이다. 교육용 로그에는 고객 요청/State가 포함된다.

## 테스트와 LangGraph 비교

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe -m pip install -r requirements-langgraph.txt
.\.venv\Scripts\python.exe main.py "ORD-1001 환불" --engine langgraph --output artifacts/langgraph
```

테스트는 API 키 없이 고정 응답과 HTTP MockTransport를 사용한다. Claude 자연어 성능이나 실제 계정의 모델 접근 권한을 검증한 것은 아니다. 두 엔진의 비교도 같은 고정 모델 응답을 사용한다. 실제 모델로 비교하면 의도 해석이나 토큰 수가 달라질 수 있다.

## 공식 문서

2026-09-28 확인:
- [Claude Sonnet 5와 모델 ID](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5)
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

모델은 `claude-sonnet-5`로 지정한다. 단순 추출 예제이므로 thinking을 명시적으로 disabled로 설정하고 temperature/top_p/top_k는 지정하지 않는다.
