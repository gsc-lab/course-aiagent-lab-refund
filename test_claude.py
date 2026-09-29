"""LLM 경계 테스트: SDK 요청 형식, 오류 분류, 호출 횟수 선반영, 응답 검증, 대화 이력 복구.

[설계] 모델의 해석 성능 대신 응답 형식 검증과 오류 처리 정책을 확인한다.
잘못된 형식·거절·출력 잘림 응답에서 도구 호출이 0회인지,
인증 오류를 재시도하지 않는지, 호출 횟수를 API 호출 전에 기록하는지를 본다.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx2
from llm import ClaudeAnalyzer, MODEL, parse_message
from runtime import Runtime, load_checkpoint
from state import State, Policy
from tools import ServiceError, TransientError
from test_fixtures import message, FixtureAnalyzer


class ClaudeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def runtime(self, analyzer, **policy):
        return Runtime(State('ORD-1001 환불해 주세요'), Policy(**policy), self.folder, analyzer=analyzer)

    async def test_sdk_request_and_json_response(self):
        # [설계] 실제 SDK를 통과하되 HTTP만 가로챈다. 모델 ID·thinking 비활성·JSON 스키마·
        # temperature 미지정이라는 "요청 형식"이 바뀌면 여기서 잡힌다.
        def handle(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], MODEL)
            self.assertEqual(payload['thinking'], {'type': 'disabled'})
            self.assertEqual(payload['output_config']['format']['type'], 'json_schema')
            self.assertEqual(payload['messages'][0]['content'], 'ORD-1001 환불해 주세요')
            self.assertNotIn('temperature', payload)
            return httpx2.Response(200, json={
                'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': MODEL,
                'content': [{'type':'text','text':'{"intent":"refund","order_id":"ORD-1001"}'}],
                'stop_reason':'end_turn', 'stop_sequence':None,
                'usage':{'input_tokens':100,'output_tokens':20},
            })
        async with anthropic.AsyncAnthropic(api_key='test-only', max_retries=0,
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))) as client:
            runtime = self.runtime(ClaudeAnalyzer(client))
            final = await runtime.run()
        self.assertEqual(final.status, 'completed')
        self.assertEqual(final.refund['amount'], 32000)
        self.assertEqual((final.llm_calls, final.input_tokens, final.output_tokens), (1,100,20))

    async def test_sdk_auth_error_not_retried(self):
        # [설계] 401은 ServiceError → 1회로 끝. 인증 설정을 수정하지 않은 반복 호출을 막는다.
        count = 0
        def handle(request):
            nonlocal count
            count += 1
            return httpx2.Response(401, json={'type':'error','error':{'type':'authentication_error','message':'bad key'}})
        async with anthropic.AsyncAnthropic(api_key='test-only', max_retries=0,
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))) as client:
            final = await self.runtime(ClaudeAnalyzer(client)).run()
        self.assertEqual(count, 1)
        self.assertEqual(final.status, 'escalated')
        self.assertEqual(final.tool_calls, 0)

    async def test_transient_retries_then_success(self):
        analyzer = SimpleNamespace(generate=AsyncMock(side_effect=[TransientError('busy'), message({'intent':'refund','order_id':'ORD-1001'})]))
        final = await self.runtime(analyzer).run()
        self.assertEqual(final.status, 'completed')
        self.assertEqual(final.llm_calls, 2)

    async def test_timeout_never_calls_order_api(self):
        # [설계] LLM 단계가 실패하면 뒤 단계(주문 API)는 절대 실행되지 않는다(도구 0회).
        async def slow(*args):
            await asyncio.sleep(10)
        final = await self.runtime(SimpleNamespace(generate=slow), llm_timeout_s=0.01, max_retries=0).run()
        self.assertEqual(final.status, 'escalated')
        self.assertEqual(final.tool_calls, 0)
        self.assertEqual(final.llm_calls, 1)

    async def test_llm_call_limit_reserved_before_request(self):
        # [설계] 사전 차단: 한도 0이면 generate가 한 번도 await되지 않는다.
        analyzer = SimpleNamespace(generate=AsyncMock())
        final = await self.runtime(analyzer, max_llm_calls=0).run()
        analyzer.generate.assert_not_awaited()
        self.assertEqual(final.status, 'escalated')

    async def test_invalid_or_refused_output_does_not_act(self):
        # [설계] 근거 없는 주문번호·허용 밖 intent·잘림·거절·타입 오류. 어느 경우도 도구를 부르지
        # 않고 인계한다. 단, 토큰은 소비됐으므로 사용량은 기록된다(input_tokens=100).
        for response in [message({'intent':'refund','order_id':'ORD-9999'}),
                         message({'intent':'delete','order_id':'ORD-1001'}),
                         message({'intent':'refund','order_id':'ORD-1001'},'max_tokens'),
                         message({'intent':'refund','order_id':'ORD-1001'},'refusal'),
                         message({'intent':[], 'order_id':None})]:
            analyzer = SimpleNamespace(generate=AsyncMock(return_value=response))
            final = await self.runtime(analyzer).run()
            self.assertEqual(final.status, 'escalated')
            self.assertEqual(final.tool_calls, 0)
            self.assertEqual(final.input_tokens, 100)

    async def test_malformed_json(self):
        response = message({})
        response.content[0].text = 'not json'
        with self.assertRaises(ServiceError):
            parse_message(response, [{'role':'user','content':'ORD-1001 환불'}])

    async def test_partial_order_number_is_rejected(self):
        response = message({'intent':'refund','order_id':'ORD-1001'})
        with self.assertRaises(ServiceError):
            parse_message(response, [{'role':'user','content':'ORD-10010 환불'}])

    async def test_unknown_intent_waits(self):
        final = await self.runtime(SimpleNamespace(generate=AsyncMock(return_value=message({'intent':'unknown','order_id':'ORD-1001'})))).run()
        self.assertEqual(final.status,'waiting_input')
        self.assertEqual(final.tool_calls,0)

    async def test_negated_refund_routes_to_lookup_with_model_output(self):
        # 모델 정확도 검증이 아닌, 모델이 반환한 lookup을 따르는 라우팅 검증.
        rt = self.runtime(SimpleNamespace(generate=AsyncMock(return_value=message({'intent':'lookup','order_id':'ORD-1001'}))))
        rt.state.request='ORD-1001 환불하지 말고 배송만 조회해 주세요'
        final=await rt.run()
        self.assertIsNone(final.refund)
        self.assertEqual(final.tool_calls,1)

    async def test_reply_history_and_usage_survive_checkpoint(self):
        # [설계] 대화 이력(messages)이 State에 있으므로 재개 후 LLM에 전체 대화가 전달되고,
        # 토큰 누적도 체크포인트를 넘어 이어진다.
        analyzer = SimpleNamespace(generate=AsyncMock(side_effect=[message({'intent':'refund','order_id':''}),message({'intent':'refund','order_id':'ORD-1001'})]))
        rt=self.runtime(analyzer)
        rt.state.request='환불해 주세요'
        await rt.run()
        state,policy=load_checkpoint(rt.checkpoint)
        state.messages=[{'role':'user','content':state.request},{'role':'user','content':'ORD-1001'}]
        state.status='running'
        final=await Runtime(state,policy,self.folder,analyzer=analyzer).run()
        self.assertEqual(final.llm_calls,2)
        self.assertEqual(final.input_tokens,200)
        self.assertEqual(analyzer.generate.call_args.args[0],state.messages)
        self.assertEqual(final.status,'completed')

    async def test_input_limit_before_call(self):
        analyzer=SimpleNamespace(generate=AsyncMock())
        final=await self.runtime(analyzer,max_input_chars=1).run()
        analyzer.generate.assert_not_awaited()
        self.assertEqual(final.status,'escalated')

    async def test_basic_uses_same_analyzer(self):
        from basic import run
        result=await run('ORD-1001 배송 조회',FixtureAnalyzer())
        self.assertEqual(result['intent'],'lookup')
        self.assertFalse(result['refunded'])

if __name__=='__main__':
    unittest.main()
