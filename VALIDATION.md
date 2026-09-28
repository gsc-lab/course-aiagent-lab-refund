# 검증 기록 — Claude Sonnet 5 연결

검증일: 2026-09-28

- 모델 ID: claude-sonnet-5 (공식 문서 확인)
- SDK: anthropic 1.8.0
- Python 가상환경에서 `python -m unittest discover -s order_refund_lab -q`: 37개 통과
- 기존 24개: 정상 환불·거절·조회·누락 정보·소유자 확인·재시도·예산·중복 방지·재개 및 두 엔진 비교
- 추가 13개: 실제 SDK를 통과하는 HTTP 모의 응답, 인증 오류, 일시 오류, timeout, 호출/입력 한도, 잘못된 JSON/필드/주문번호, 거절/출력 잘림, 조회 라우팅, 대화/토큰 복구, 기본 예제
- CLI 도움말 및 의존성 검사 확인

테스트는 고정 모델 응답과 HTTP MockTransport를 사용하며 네트워크를 호출하지 않는다. 모델의 의미 해석 정확도와 계정별 Sonnet 5 접근 가능 여부를 입증한 결과는 아니다.

검증 당시 환경에는 ANTHROPIC_API_KEY가 없어 실제 Claude 호출은 실행하지 않았다. API 키를 설정하고 README의 명령으로 실호출을 확인해야 한다. 실호출은 과금되며, 주문/환불은 계속 로컬 모의 처리다.

소스에는 API 키가 없으며 ZIP에는 .venv, 실제 실행 로그/환불 기록 DB, 환경 변수 파일을 포함하지 않는다.
