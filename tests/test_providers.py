"""provider 真实调用与降级路径的测试。

覆盖三件事：
1. `is_available()` 的判断（没 key / 没启用 / ws 协议不可用）；
2. `call()` 发出的请求形状与响应解析（用假 request 拦，不联网、不花钱）；
3. 流水线用工具写作、回灌校验错误，并保留编剧调用失败前的检查点；
   缺少 provider 或素材调用失败时，支持带 mock 标记的降级。
"""
import asyncio
import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main, mock_api
from app.config import DEFAULTS
from app.engine.graph import validate_graph
from app.engine.pipeline import Pipeline, silent_wav_for
from app.mock_data import skeleton
from app.providers import registry
from app.providers.base import ProviderError
from app.providers.image import DashScopeImageProvider
from app.providers.llm import ChatProvider
from app.providers.tts import BailianTTSProvider

PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16


def configs(**patches):
    """基于默认配置造一份配置；patches 形如 llm=dict(enabled=True, api_key='sk-x')。"""
    result = {kind: dict(values) for kind, values in DEFAULTS.items()}
    for kind, values in patches.items():
        result[kind].update(values)
    return result


class AvailabilityTests(unittest.TestCase):
    def test_disabled_reports_reason(self):
        provider = ChatProvider(configs()['llm'])
        self.assertFalse(provider.is_available())
        self.assertEqual(provider.unavailable_reason(), '未启用')

    def test_enabled_without_key_reports_key(self):
        provider = ChatProvider(configs(llm=dict(enabled=True, api_key=''))['llm'])
        self.assertFalse(provider.is_available())
        self.assertIn('API key', provider.unavailable_reason())

    def test_enabled_with_key_is_available(self):
        provider = ChatProvider(configs(llm=dict(enabled=True, api_key='sk-test'))['llm'])
        self.assertTrue(provider.is_available())

    def test_tts_websocket_is_unavailable_with_reason(self):
        provider = BailianTTSProvider(
            configs(bailian_tts=dict(enabled=True, api_key='sk-test'))['bailian_tts'])
        self.assertTrue(provider.base.startswith('ws'))
        self.assertFalse(provider.is_available())
        self.assertIn('WebSocket', provider.unavailable_reason())

    def test_tts_http_is_available(self):
        provider = BailianTTSProvider(configs(bailian_tts=dict(
            enabled=True, api_key='sk-test', base_url='https://dashscope.aliyuncs.com',
            model='qwen3-tts-flash'))['bailian_tts'])
        self.assertTrue(provider.is_available())


class PlanTests(unittest.TestCase):
    def test_no_keys_means_all_stages_degraded(self):
        plan = registry.plan(configs())
        self.assertTrue(plan['mock'])
        self.assertEqual(set(plan['degraded']), {'writer', 'art', 'voice'})
        self.assertTrue(all(p is None for p in plan['providers'].values()))

    def test_only_llm_configured_leaves_two_degraded(self):
        plan = registry.plan(configs(llm=dict(enabled=True, api_key='sk-test')))
        self.assertNotIn('writer', plan['degraded'])
        self.assertIsInstance(plan['providers']['writer'], ChatProvider)
        self.assertEqual(set(plan['degraded']), {'art', 'voice'})
        self.assertTrue(plan['mock'])

    def test_all_configured_is_not_mock(self):
        plan = registry.plan(configs(
            llm=dict(enabled=True, api_key='sk-test'),
            qwen_image=dict(enabled=True, api_key='sk-test'),
            bailian_tts=dict(enabled=True, api_key='sk-test',
                             base_url='https://dashscope.aliyuncs.com'),
        ))
        self.assertFalse(plan['mock'])
        self.assertEqual(plan['degraded'], {})


class CallTests(unittest.TestCase):
    def test_chat_call_uses_openai_shape(self):
        seen = {}

        async def fake_request(method, url, **kwargs):
            seen.update(method=method, url=url, **kwargs)
            return httpx.Response(200, json={'choices': [{'message': {'content': '  你好  '}}]})

        provider = ChatProvider(configs(llm=dict(enabled=True, api_key='sk-test'))['llm'])
        with patch('app.providers.llm.request', fake_request):
            text = asyncio.run(provider.call([{'role': 'user', 'content': 'hi'}], temperature=0.5))
        self.assertEqual(text, '你好')
        self.assertEqual(seen['method'], 'POST')
        self.assertEqual(seen['url'], 'https://api.deepseek.com/v1/chat/completions')
        self.assertEqual(seen['json']['model'], 'deepseek-chat')
        self.assertEqual(seen['json']['temperature'], 0.5)
        self.assertEqual(seen['headers']['Authorization'], 'Bearer sk-test')

    def test_chat_bad_shape_raises(self):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(200, json={'nope': 1})

        provider = ChatProvider(configs(llm=dict(enabled=True, api_key='sk-test'))['llm'])
        with patch('app.providers.llm.request', fake_request):
            with self.assertRaises(ProviderError) as caught:
                asyncio.run(provider.call([{'role': 'user', 'content': 'hi'}]))
        self.assertEqual(caught.exception.payload['code'], 'INVALID_RESPONSE')

    def test_chat_json_strips_fence(self):
        async def fake_request(method, url, **kwargs):
            payload = {'choices': [{'message': {'content': '```json\n{"nodes": []}\n```'}}]}
            return httpx.Response(200, json=payload)

        provider = ChatProvider(configs(llm=dict(enabled=True, api_key='sk-test'))['llm'])
        with patch('app.providers.llm.request', fake_request):
            data = asyncio.run(provider.call_json([{'role': 'user', 'content': 'hi'}]))
        self.assertEqual(data, {'nodes': []})

    def test_image_parses_b64(self):
        seen = {}

        async def fake_request(method, url, **kwargs):
            seen.update(method=method, url=url, **kwargs)
            encoded = base64.b64encode(PNG_BYTES).decode()
            return httpx.Response(200, json={'data': [{'b64_json': encoded}]})

        provider = DashScopeImageProvider(
            configs(qwen_image=dict(enabled=True, api_key='sk-test'))['qwen_image'])
        with patch('app.providers.image.request', fake_request):
            data = asyncio.run(provider.call('雨夜天台', width=512, height=768))
        self.assertEqual(data, PNG_BYTES)
        self.assertEqual(seen['url'],
                         'https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations')
        self.assertEqual(seen['json']['size'], '512*768')

    def test_image_downloads_url(self):
        async def fake_request(method, url, **kwargs):
            if method == 'GET':
                return httpx.Response(200, content=PNG_BYTES)
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/x.png'}]})

        provider = DashScopeImageProvider(
            configs(qwen_image=dict(enabled=True, api_key='sk-test'))['qwen_image'])
        with patch('app.providers.image.request', fake_request):
            data = asyncio.run(provider.call('场景'))
        self.assertEqual(data, PNG_BYTES)

    def test_image_rejects_empty_prompt(self):
        provider = DashScopeImageProvider(
            configs(qwen_image=dict(enabled=True, api_key='sk-test'))['qwen_image'])
        with self.assertRaises(ProviderError) as caught:
            asyncio.run(provider.call('   '))
        self.assertEqual(caught.exception.payload['code'], 'EMPTY_PROMPT')

    def test_tts_parses_wav_and_duration(self):
        wav = silent_wav_for(1.5)
        seen = {}

        async def fake_request(method, url, **kwargs):
            seen.update(url=url, **kwargs)
            encoded = base64.b64encode(wav).decode()
            return httpx.Response(200, json={'output': {'audio': {'data': encoded}}})

        provider = BailianTTSProvider(configs(bailian_tts=dict(
            enabled=True, api_key='sk-test', base_url='https://dashscope.aliyuncs.com',
            model='qwen3-tts-flash', voice='Cherry'))['bailian_tts'])
        with patch('app.providers.tts.request', fake_request):
            result = asyncio.run(provider.call('你好'))
        self.assertAlmostEqual(result['seconds'], 1.5, places=3)
        self.assertEqual(result['data'], wav)
        self.assertEqual(seen['url'],
                         'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
        self.assertEqual(seen['json']['input']['voice'], 'Cherry')

    def test_tts_rejects_non_wav(self):
        async def fake_request(method, url, **kwargs):
            encoded = base64.b64encode(b'ID3\x03\x00\x00\x00mp3data').decode()
            return httpx.Response(200, json={'output': {'audio': {'data': encoded}}})

        provider = BailianTTSProvider(configs(bailian_tts=dict(
            enabled=True, api_key='sk-test', base_url='https://dashscope.aliyuncs.com'))['bailian_tts'])
        with patch('app.providers.tts.request', fake_request):
            with self.assertRaises(ProviderError) as caught:
                asyncio.run(provider.call('你好'))
        self.assertEqual(caught.exception.payload['code'], 'INVALID_AUDIO')


# ---------------- 流水线 ----------------

def make_graph(node_count=4, depth=1):
    project = dict(id='p_' + '0' * 32, name='流水线测试', resolution=[1920, 1080],
                   created_at='2026-01-01T00:00:00+00:00')
    body = dict(requirement='雨夜天台', params=dict(
        node_count=node_count, branch_depth=depth, auto_voice=True,
        consistency=True, resolution=(1920, 1080)))
    graph = skeleton(project)
    graph['nodes'] = graph['nodes'][:1]
    graph['nodes'][0].update(status='pending', choices=[])
    graph.update(edges=[], agent_state={})
    return graph, body


PLAN = ('plan_structure', dict(logline='天台的重逢', target_minutes=60, acts=[
    dict(title='雨夜', chapters=[dict(title='重逢', beats='两人商量是否打开来信', node_budget=3)]),
]))
READ_CHAPTER = ('get_chapter', dict(chapter_id='c01'))
WRITE_CHAPTER = ('write_nodes', dict(chapter_id='c01', nodes=[
    dict(ref='c01-s01', title='标题·来信', type='choice', who='heroine', lines=['一二三四五'],
         bg_key='roof_rain', choices=[dict(label='打开', to='c01-s02'), dict(label='放下', to='c01-s03')]),
    dict(ref='c01-s02', title='回应', type='ending', end_kind='true', lines=['她终于打开了那封来信'],
         bg_key='roof_rain'),
    dict(ref='c01-s03', title='失约', type='ending', end_kind='bad', lines=['她把那封来信还了回去'],
         bg_key='roof_rain'),
]))
CONNECT_ENTRY = ('connect_nodes', {'from': 'n1', 'to': 'c01-s01'})
VALIDATE_CHAPTER = ('validate_graph', {'scope': 'chapter'})
FINISH_CHAPTER = ('finish_chapter', dict(chapter_id='c01', summary='她打开了来信。两人明天会再见。'))


class FakeWriter:
    kind = 'llm'
    cfg = {'model': 'fake-llm'}

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.messages = []
        self.turn_count = 0

    async def call_with_tools(self, messages, tools, **kwargs):
        self.messages = copy.deepcopy(messages)
        self.turn_count += 1
        calls = next(self.turns)
        if isinstance(calls, ProviderError):
            raise calls
        names = {tool['function']['name'] for tool in tools}
        assert all(name in names for name, _ in calls)
        return dict(text=None, tool_calls=[
            dict(id=f'call-{self.turn_count}-{i}', name=name, arguments=arguments)
            for i, (name, arguments) in enumerate(calls)
        ])


class FakeTTS:
    kind = 'bailian_tts'
    cfg = {'model': 'fake-tts', 'voice': 'Cherry'}

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def call(self, text):
        self.calls.append(text)
        if self.error:
            raise self.error
        seconds = round(len(text) / 4.5, 3)   # 和字数对齐，保证 graph 校验能过
        return dict(data=silent_wav_for(seconds), seconds=seconds)


class FakeImage:
    kind = 'qwen_image'
    cfg = {'model': 'fake-image'}

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def call(self, prompt, width=1024, height=1024):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return PNG_BYTES


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_pipeline(self, providers, graph=None, body=None):
        if graph is None:
            graph, body = make_graph()
        events = []

        async def emit(event, data):
            events.append((event, data))

        async def save_graph(value):
            (self.root / 'graph.json').write_text(json.dumps(value, ensure_ascii=False), 'utf-8')

        async def save_asset(relative, data):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        pipeline = Pipeline(graph=graph, body=body, root=self.root, providers=providers,
                            emit=emit, save_graph=save_graph, save_asset=save_asset, delay=0)
        stats = asyncio.run(pipeline.run())
        return stats, events, graph

    def test_all_real_providers_mark_not_mock(self):
        writer = FakeWriter([PLAN], [READ_CHAPTER], [WRITE_CHAPTER, CONNECT_ENTRY],
                            [VALIDATE_CHAPTER], [FINISH_CHAPTER])
        stats, events, graph = self.run_pipeline(dict(
            writer=writer, art=FakeImage(), voice=FakeTTS()))
        self.assertFalse(stats['mock'])
        self.assertEqual(stats['stages'], {'writer': 'real', 'art': 'real', 'voice': 'real', 'build': 'real'})
        self.assertEqual(stats['chapters'], 1)
        self.assertEqual(stats['tool_rounds'], 5)
        self.assertEqual(graph['nodes'][0]['type'], 'start')
        self.assertEqual(graph['nodes'][1]['title'], '标题·来信')
        self.assertEqual(graph['nodes'][1]['lines'][0]['text'], '一二三四五')
        self.assertEqual(graph['nodes'][0]['bg']['provider'], 'qwen_image')
        self.assertEqual(len({node['bg']['asset'] for node in graph['nodes'][1:]}), 1)
        tool_calls = [call['id'] for msg in writer.messages for call in msg.get('tool_calls', [])]
        tool_replies = [msg['tool_call_id'] for msg in writer.messages if msg['role'] == 'tool']
        self.assertEqual(tool_replies, tool_calls)
        self.assertEqual(events[-1][0], 'done')
        validate_graph(graph, self.root)

    def test_writer_failure_surfaces_and_preserves_checkpoint(self):
        graph, body = make_graph()
        error = ProviderError('PROVIDER_UNREACHABLE', '连不上', 'llm.call')
        writer = FakeWriter([PLAN], [READ_CHAPTER], [WRITE_CHAPTER], error)
        with self.assertRaises(ProviderError) as caught:
            self.run_pipeline(dict(writer=writer, art=None, voice=None), graph, body)
        self.assertIs(caught.exception, error)
        graph = json.loads((self.root / 'graph.json').read_text('utf-8'))
        chapter = graph['agent_state']['acts'][0]['chapters'][0]
        self.assertEqual(chapter['status'], 'writing')
        node_ids = [node['id'] for node in graph['nodes']]
        self.assertEqual(len(node_ids), 4)

        writer = FakeWriter([READ_CHAPTER], [CONNECT_ENTRY, VALIDATE_CHAPTER], [FINISH_CHAPTER])
        _, events, graph = self.run_pipeline(dict(writer=writer, art=None, voice=None), graph, body)
        self.assertEqual([node['id'] for node in graph['nodes']], node_ids)
        self.assertTrue(any(event == 'log' and '已落盘的 3 个节点' in data['text'] for event, data in events))
        validate_graph(graph, self.root)

    def test_writer_repairs_validation_error_from_tool_feedback(self):
        writer = FakeWriter([PLAN], [READ_CHAPTER], [WRITE_CHAPTER, FINISH_CHAPTER],
                            [CONNECT_ENTRY], [VALIDATE_CHAPTER, FINISH_CHAPTER])
        stats, _, graph = self.run_pipeline(dict(writer=writer, art=None, voice=None))
        replies = [json.loads(msg['content']) for msg in writer.messages if msg['role'] == 'tool']
        failure = next(reply for reply in replies if reply.get('error') == 'GRAPH_INVALID')
        self.assertTrue(failure['hint'])
        self.assertIn('INVALID_ENTRY', {error['code'] for error in failure['errors']})
        self.assertEqual(stats['stages']['writer'], 'real')
        validate_graph(graph, self.root)

    def test_image_failure_falls_back_to_placeholder(self):
        graph, _ = make_graph()
        image = FakeImage(error=ProviderError('PROVIDER_HTTP_ERROR', 'HTTP 500', 'qwen_image.call'))
        stats, _, graph = self.run_pipeline(dict(writer=None, art=image, voice=None))
        self.assertEqual(stats['stages']['art'], 'mock')
        self.assertEqual(graph['nodes'][0]['bg']['provider'], 'mock')
        self.assertTrue((self.root / graph['nodes'][0]['bg']['asset']).is_file())
        validate_graph(graph, self.root)

    def test_voice_failure_uses_timed_silence(self):
        graph, _ = make_graph()
        tts = FakeTTS(error=ProviderError('PROVIDER_UNREACHABLE', '超时', 'tts.call'))
        stats, _, graph = self.run_pipeline(dict(writer=None, art=None, voice=tts))
        self.assertEqual(stats['stages']['voice'], 'mock')
        line = graph['nodes'][0]['lines'][0]
        self.assertAlmostEqual(line['dur'], round(len(line['text']) / 4.5, 3), places=3)
        self.assertTrue((self.root / line['voice']).is_file())
        validate_graph(graph, self.root)

    def test_real_voice_duration_is_taken_from_wav(self):
        graph, _ = make_graph()
        tts = FakeTTS()
        stats, _, graph = self.run_pipeline(dict(writer=None, art=None, voice=tts))
        self.assertEqual(stats['stages']['voice'], 'real')
        self.assertTrue(tts.calls)
        validate_graph(graph, self.root)

    def test_auto_voice_off_skips_tts(self):
        graph, _ = make_graph()
        body = dict(requirement='x', params=dict(
            node_count=4, branch_depth=1, auto_voice=False, consistency=True,
            resolution=(1920, 1080)))
        tts = FakeTTS()
        stats, _, graph = self.run_pipeline(dict(writer=None, art=None, voice=tts), graph, body)
        self.assertEqual(stats['stages']['voice'], 'off')
        self.assertEqual(tts.calls, [])
        self.assertTrue(all(line['voice'] is None for node in graph['nodes'] for line in node['lines']))
        validate_graph(graph, self.root, False)


# ---------------- 集成：GAL_MOCK=0 且没有 key ----------------

class RealModeDegradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = mock_api.Jobs(self.root, projects_root=self.root / 'projects')
        self.manager.delay = 0.001
        replacements = [
            patch.object(mock_api, 'jobs', self.manager),
            patch.object(mock_api, 'MOCK', False),
            patch.object(mock_api, 'configs_source', lambda: configs()),  # 全部未启用
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_generate_returns_200_with_mock_true(self):
        project_id = self.client.post('/api/projects', json={'name': '降级'}).json()['id']
        response = self.client.post(f'/api/projects/{project_id}/generate',
                                    json={'requirement': '雨夜天台', 'params': {'node_count': 8}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body['mock'], body)
        self.assertEqual(set(body['degraded']), {'writer', 'art', 'voice'})
        self.assertIn('job_id', body)

        events, text = self.stream(body['sse'])
        self.assertEqual(events[-1][0], 'done', text)
        self.assertTrue(events[-1][1]['mock'])
        self.assertTrue(events[-1][1]['stats']['mock'])
        # 流程后来加了 review 阶段；核心四个在就算过，多出来的阶段不算错
        self.assertTrue({'writer', 'art', 'voice', 'build'} <=
                        {d['agent'] for e, d in events if e == 'agent' and d['state'] == 'done'})
        graph = self.client.get(f'/api/projects/{project_id}/graph').json()
        validate_graph(graph, self.manager.project_path(project_id))
        self.assertTrue(all(node['status'] == 'done' for node in graph['nodes']))

    def test_generate_reports_degraded_reason(self):
        project_id = self.client.post('/api/projects', json={'name': '降级'}).json()['id']
        body = self.client.post(f'/api/projects/{project_id}/generate',
                                json={'requirement': 'x', 'params': {'node_count': 8}}).json()
        self.assertIn('未启用', body['degraded']['writer'])
        self.assertIn('未启用', body['degraded']['art'])
        self.assertIn('未启用', body['degraded']['voice'])

    def test_writer_error_reaches_sse_and_retry_keeps_checkpoint(self):
        project_id = self.client.post('/api/projects', json={'name': '断点续写'}).json()['id']
        endpoint = f'/api/projects/{project_id}/generate'
        body = {'requirement': '雨夜天台', 'params': {'node_count': 4, 'auto_voice': False}}
        failed_writer = FakeWriter([PLAN], [READ_CHAPTER, WRITE_CHAPTER],
                                   ProviderError('PROVIDER_UNREACHABLE', '连接超时', 'llm.call'))
        retry_writer = FakeWriter([READ_CHAPTER, CONNECT_ENTRY, VALIDATE_CHAPTER, FINISH_CHAPTER])

        def generate_with(writer):
            plan = dict(providers={'writer': writer}, mock=True, degraded={})
            with patch.object(mock_api.registry, 'plan', return_value=plan):
                response = self.client.post(endpoint, json=body)
                self.assertEqual(response.status_code, 200, response.text)
                return self.stream(response.json()['sse'])

        events, output = generate_with(failed_writer)
        self.assertEqual(events[-1][0], 'error', output)
        self.assertEqual(events[-1][1]['code'], 'PROVIDER_UNREACHABLE')
        graph_url = f'/api/projects/{project_id}/graph'
        saved = self.client.get(graph_url).json()
        self.assertEqual(saved['chapters'][0]['status'], 'writing')
        self.assertEqual(len(saved['nodes']), 4)

        events, output = generate_with(retry_writer)
        self.assertEqual(events[-1][0], 'done', output)
        finished = self.client.get(graph_url).json()
        self.assertEqual(finished['chapters'][0]['status'], 'done')
        self.assertEqual([(node['id'], node['gen']) for node in finished['nodes']],
                         [(node['id'], node['gen']) for node in saved['nodes']])

    def stream(self, url):
        response = self.client.get(url, headers={'Origin': 'null'})
        self.assertEqual(response.status_code, 200, response.text)
        events = []
        for block in response.text.split('\n\n'):
            if not block or block.startswith(':'):
                continue
            lines = dict(line.split(': ', 1) for line in block.splitlines())
            events.append((lines['event'], json.loads(lines['data'])))
        return events, response.text

    def test_export_still_rejects_video(self):
        project_id = self.client.post('/api/projects', json={'name': '降级'}).json()['id']
        response = self.client.post(f'/api/projects/{project_id}/export', json={'format': 'video'})
        self.assertEqual(response.status_code, 501)


if __name__ == '__main__':
    unittest.main()
