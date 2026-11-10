import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, mock_api
from app.engine.graph import validate_graph
from app.store import atomic_json

class M2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = mock_api.Jobs(self.root)
        self.manager.delay = 0.001
        for replacement in (patch.object(mock_api, 'jobs', self.manager), patch.object(mock_api, 'MOCK', True)):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def create(self):
        response = self.client.post('/api/projects', json={'name': '中文项目'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def generate(self, project_id, voice=True, count=8, depth=2):
        response = self.client.post(f'/api/projects/{project_id}/generate', json={'requirement': '雨夜天台', 'params': {'node_count': count, 'branch_depth': depth, 'auto_voice': voice}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def stream(self, url):
        response = self.client.get(url, headers={'Origin': 'null'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['access-control-allow-origin'], '*')
        events = []
        for block in response.text.split('\n\n'):
            if not block or block.startswith(':'):
                continue
            lines = dict(line.split(': ', 1) for line in block.splitlines())
            events.append((lines['event'], json.loads(lines['data'])))
        return events, response.text

    def test_generation_and_exports(self):
        project_id = self.create()
        job = self.generate(project_id)
        events, text = self.stream(job['sse'])
        self.assertEqual(events[-1][0], 'done', text)
        self.assertEqual({d['agent'] for e, d in events if e == 'agent' and d['state'] == 'done'}, {'writer', 'art', 'voice', 'build'})
        self.assertEqual(sum(e == 'node' for e, _ in events), 32)
        self.assertNotIn('error', [e for e, _ in events])
        graph = self.client.get(f'/api/projects/{project_id}/graph').json()
        validate_graph(graph, self.manager.project_path(project_id))
        self.assertEqual(len(graph['nodes']), 8)
        self.assertTrue(all('choices' not in n for n in graph['nodes'] if n['type'] == 'ending'))
        for kind, signature in [('bg', b'\x89PNG'), ('voice', b'RIFF')]:
            name = 'mock.png' if kind == 'bg' else 'mock.wav'
            response = self.client.get(f'/api/assets/{project_id}/assets/{kind}/{name}')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(signature))
        for format, artifact in [('web', 'index.html'), ('renpy', 'game/script.rpy')]:
            exported = self.client.post(f'/api/projects/{project_id}/export', json={'format': format})
            self.assertEqual(exported.status_code, 200, exported.text)
            info = exported.json()
            export_events, _ = self.stream(f'/api/jobs/{info["job_id"]}/events')
            self.assertEqual(export_events[-1][0], 'done')
            self.assertTrue((Path(info['out']) / artifact).is_file())
        replay = self.client.get(job['sse'], headers={'Last-Event-ID': '1'})
        self.assertTrue(replay.text.startswith('id: 2\n'))

    def test_voice_disabled_and_error_stream(self):
        project_id = self.create()
        job = self.generate(project_id, False, 4, 0)
        self.stream(job['sse'])
        graph = self.client.get(f'/api/projects/{project_id}/graph').json()
        validate_graph(graph, self.manager.project_path(project_id), False)
        self.assertTrue(all(line['voice'] is None for node in graph['nodes'] for line in node['lines']))
        self.manager.delay = 0.1
        job = self.generate(project_id)
        response = self.client.post(f'/api/jobs/{job["job_id"]}/cancel')
        self.assertEqual(response.status_code, 200)
        events, _ = self.stream(job['sse'])
        self.assertEqual(events[-1], ('error', {'code': 'JOB_CANCELLED', 'message': '任务已取消', 'where': 'jobs.cancel'}))

    def test_heartbeat_and_live_nodes(self):
        # TestClient 会缓冲响应；真实实时顺序另由 scripts/m2_smoke.ps1 的 curl -N 验证。
        self.manager.delay = 0.4
        project_id = self.create()
        job = self.generate(project_id, count=4)
        events, text = self.stream(job['sse'])
        self.assertIn(': keepalive\n\n', text)
        self.assertLess(next(i for i, (e, _) in enumerate(events) if e == 'node'), len(events)-1)

    def test_p0_error_headers_and_fields(self):
        response = self.client.put('/api/providers', json={'local_llm': {'base_url': 'not-a-url'}}, headers={'Origin': 'null'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['field'], 'local_llm.base_url')
        self.assertEqual(response.headers['access-control-allow-origin'], '*')
        with patch('app.mock_api.atomic_json', side_effect=RuntimeError('unexpected failure')):
            response = self.client.post('/api/projects', json={'name': 'test'}, headers={'Origin': 'null'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers['access-control-allow-origin'], '*')
        # 模拟真实文件写入 PermissionError，断言 code/路径/原始错误。
        with patch('app.store.tempfile.NamedTemporaryFile', side_effect=PermissionError(13, 'Permission denied')):
            response = self.client.post('/api/projects', json={'name': 'test'}, headers={'Origin': 'null'})
        self.assertEqual(response.status_code, 500)
        error = response.json()['error']
        self.assertEqual(error['code'], 'STATE_NOT_WRITABLE')
        self.assertIn('graph.json', error['message'])
        self.assertIn('Permission denied', error['message'])
        self.assertEqual(response.headers['access-control-allow-origin'], '*')

    def test_mock_off_does_not_generate(self):
        project_id = self.create()
        with patch.object(mock_api, 'MOCK', False):
            response = self.client.post(f'/api/projects/{project_id}/generate', json={'requirement': 'test'})
        self.assertEqual(response.status_code, 501)
        response = self.client.post(f'/api/projects/{project_id}/export', json={'format': 'video'})
        self.assertEqual(response.status_code, 501)

if __name__ == '__main__':
    unittest.main()
