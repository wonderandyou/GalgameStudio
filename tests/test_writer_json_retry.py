"""模型把工具参数 JSON 写坏时，要重试一次而不是让整章作废。

对应实测故障：p_b097ebf4 c07 轮3
  LLM 工具响应结构异常：Expecting ',' delimiter: line 1 column 608 (char 607)
  → 以前直接 INVALID_RESPONSE 判死，整个任务失败。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agents.loop import WriterLoop
from app.providers.base import ProviderError

TOOLS = [{'type': 'function', 'function': {'name': 'get_chapter', 'parameters': {}}}]
OK_CALL = {'id': 'call_1', 'name': 'get_chapter', 'arguments': {'chapter_id': 'c01'}}


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    async def call_with_tools(self, messages, tools, max_tokens=None):
        self.seen.append(messages)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_loop(provider):
    # metrics 是 property，读的是 pipeline.graph['agent_state']，所以给它一个空 graph 就够
    loop = WriterLoop.__new__(WriterLoop)          # 不走 __init__，只装需要的字段
    loop.provider = provider
    loop.pipeline = SimpleNamespace(graph={'agent_state': {}}, log=AsyncMock())
    return loop


def bad_json_error():
    return ProviderError('INVALID_RESPONSE',
                         "LLM 工具响应结构异常：Expecting ',' delimiter: line 1 column 608 (char 607)",
                         'openai.call_with_tools')


class WriterJsonRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_once_when_arguments_json_broken(self):
        provider = FakeProvider([bad_json_error(),
                                 {'text': None, 'tool_calls': [OK_CALL]}])
        loop = make_loop(provider)
        messages = [{'role': 'user', 'content': '继续写'}]

        calls = await loop.model_turn(messages, TOOLS, 'c07')

        self.assertEqual(len(provider.seen), 2, '应该调用两次模型')
        self.assertEqual(calls[0]['name'], 'get_chapter')
        # 重试时要在最后追加一条提示，告诉它上次 JSON 坏了
        self.assertEqual(provider.seen[1][-1]['role'], 'user')
        self.assertIn('合法 JSON', provider.seen[1][-1]['content'])
        # 那条提示不能写进调用方传进来的原列表（重试只在临时副本上生效）
        self.assertFalse(
            any(isinstance(m.get('content'), str) and '合法 JSON' in m['content'] for m in messages),
            '重试提示不该污染原消息列表')

    async def test_second_failure_still_raises(self):
        provider = FakeProvider([bad_json_error(), bad_json_error()])
        loop = make_loop(provider)
        with self.assertRaises(ProviderError) as ctx:
            await loop.model_turn([{'role': 'user', 'content': 'x'}], TOOLS, 'c07')
        self.assertEqual(ctx.exception.payload['code'], 'INVALID_RESPONSE')
        self.assertEqual(len(provider.seen), 2, '只重试一次，不要无限重试')

    async def test_other_errors_do_not_retry(self):
        boom = ProviderError('HTTP_500', '上游 500', 'openai.call_with_tools', status=500)
        provider = FakeProvider([boom])
        loop = make_loop(provider)
        with self.assertRaises(ProviderError):
            await loop.model_turn([{'role': 'user', 'content': 'x'}], TOOLS, 'c07')
        self.assertEqual(len(provider.seen), 1, '非 JSON 错误不该重试')


if __name__ == '__main__':
    unittest.main()
