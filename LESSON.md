# 단계별 실습 가이드

## 학습 목표

학생은 State·Node·Routing·Tool·Loop가 있는 코드를 설명하고 수정할 수 있다. 정상 결과뿐 아니라 실패·재시도·대기·인계·재개를 재현하고, 로그를 근거로 실행 과정을 설명할 수 있다. 마지막에는 직접 만든 실행 구조를 LangGraph 개념과 대응한다.

## 1단계 — Workflow Design

먼저 `WORKFLOW.md`를 읽고 다음 작업을 한다.

1. 사용자 요청을 분석·조회·판정·처리·응답으로 분해한다.
2. 주문 조회만 필요한 경우와 환불 요청을 구분한다.
3. 환불 가능/불가, 입력 부족, 도구 오류 분기를 그린다.
4. 도구 재시도와 사용자 추가 답변의 반복을 구분한다.
5. 정상 완료·담당자 확인·입력 대기의 종료 조건을 쓴다.

완료 기준: 각 화살표에 조건이 있고 모든 경로가 완료·대기·인계 중 하나에 도달한다.

## 2단계 — Program Structure

`basic.py`를 실행한 뒤 State 초기값, 노드 함수, route, while을 각각 찾아 표시한다. 이 파일도 Claude로 조회·환불 의도를 해석한다. 실행 전에 README의 SDK 설치와 API 키 설정을 마친다.

```powershell
python basic.py "ORD-1001 환불"
python basic.py "ORD-1002 환불"
python basic.py "환불"
```

그다음 `state.py`, `nodes.py`, `tools.py`로 이동한다. 외부 호출을 Tool로 분리하고 State를 dataclass로 선언한 이유를 설명한다. 주문·환불은 MockAPI로 통제하고, 요청 분석은 실제 Claude API를 호출한다. llm.py에서 메시지·응답 형식·검증을 확인한다.

질문: 모든 일을 하나의 함수에 넣으면 어느 단계가 실패했는지 어떻게 알 수 있을까? `route` 안에서 API를 호출하면 어떤 책임이 섞일까?

완료 기준: 새 주문 샘플을 추가하고 조회·환불 가능·환불 불가를 예측한 대로 실행한다.

## 3단계 — Execution Policy

`runtime.py`의 `call`과 `step`을 읽으며 다음 정책을 하나씩 바꾼다.

| 과제 | 실험 | 확인할 근거 |
|---|---|---|
| Retry | flaky / down 시나리오와 `--retries 0` 비교 | attempts, retry 로그 |
| Timeout | slow 시나리오에 timeout 적용 | tool_error의 TimeoutError, latency_ms |
| Budget | 비용 2, 호출 1로 제한 | 호출 전에 중단했는지 |
| Termination | 단계 수 2로 제한 | steps가 2를 넘지 않는지 |
| Escalation | 지속 장애 발생 | handoff.json 사유·다음 조치 |
| Checkpoint | 3단계 후 중단·재개 | refund부터 이어가는지 |

질문: timeout은 “환불 실패”와 같은 뜻인가? 같은 함수 호출을 재시도하는 것과 같은 금액을 다시 지급하는 것은 어떻게 다른가?

완료 기준: 각 정책을 설정하고 경계값에서 도구 실행이 제한되는 것을 보여준다.

## 4단계 — 실행 과정 확인(Observability)

정상 실행과 flaky 실행 로그를 비교해 run_id, step, node, state_change, tool_start/end, tool_error, retry를 찾는다. 하나의 도구 호출에 여러 시도가 생기는 위치를 설명한다.

제출할 실행 보고서는 다음 항목을 포함한다.

- 입력과 최종 응답
- 실행한 노드 순서
- 변경된 주요 State 3개와 변경 이유
- 도구별 시도 횟수·지연 시간·가상 비용
- 실패 시 발생한 오류와 재시도/인계 근거
- 체크포인트를 이용한 재개 결과

완료 기준: 최종 응답만 보고 추측하지 않고 로그의 이벤트로 설명한다.

## 5단계 — 중복 처리와 복구

새 출력 폴더에서 lost-response를 실행한다. 환불 API는 DB에 환불 기록을 저장한 뒤 응답 전달을 지연한다. wait_for가 시간 초과를 발생시켜도 이미 저장된 환불 기록은 취소되지 않는다. 같은 중복 처리 방지 키로 재시도하므로 해당 주문의 환불 기록은 한 건으로 유지된다.

그다음 `--retries 0`으로 새 폴더에서 실행한다. 이번에는 담당자 확인으로 끝나지만 DB에는 환불 기록이 남아 있을 수 있다. `test_uncertain_write_escalates_without_false_success`와 `test_restart_after_committed_write`를 읽고 왜 이 두 테스트가 필요한지 설명한다.

완료 기준: timeout과 쓰기 실패를 구분하고, 체크포인트만으로 중복 환불을 막을 수 없는 이유를 설명한다.

## 6단계 — 같은 예제를 LangGraph로 구현

이제 `requirements-langgraph.txt`를 설치하고 `langgraph_version.py`를 읽는다. 이미 이해한 업무 노드와 실행 정책을 유지하면서 StateGraph에 노드를 하나씩 연결한다.

질문: 어떤 반복 코드가 사라졌고, 어떤 업무 규칙과 API 처리는 그대로 남았는가? 메모리 체크포인터와 파일 체크포인트의 수명은 어떻게 다른가?

완료 기준: `LangGraphComparison`의 10개 시나리오가 통과하고, 직접 만든 while과 graph.ainvoke의 역할을 대응해 설명한다.

## Claude 해석 결과 평가

이미 연결된 `nodes.analyze` → `Runtime.analyze_request` → `ClaudeAnalyzer.generate` 순서로 읽는다.

1. `SYSTEM` 지시문과 고객 `messages`를 구분한다.
2. Claude가 `intent`, `order_id`를 만들고 Python이 검증하는 위치를 찾는다.
3. 검증한 값이 State와 Routing에 어떻게 사용되는지 설명한다.
4. “환불하지 말고 배송만 조회”, 주문번호 누락, 여러 주문, 추가 답변에 의한 주문 정정을 평가한다.
5. API 토큰 수와 모의 주문 Tool 비용을 구분한다.

완료 기준: 정상 해석뿐 아니라 모호한 요청에서 확인 질문을 하는지 확인한다. 고정 응답 단위 테스트는 모델 해석 정확도 평가와 다르다. 실제 모델 평가는 API 요금이 발생하며, JSON 형식이 맞아도 의미가 틀릴 수 있다.

추가 확장: 부분 환불, 담당자 승인 후 재개, 프로그램 종료 후에도 기록을 유지하는 LangGraph 체크포인터. 확장 전 State·분기·정책·검증 사례를 먼저 정한다.
