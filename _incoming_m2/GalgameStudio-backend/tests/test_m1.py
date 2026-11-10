import socket
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from app.store import Credentials

class M1Tests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        replacement = patch.object(main, 'credentials', Credentials(directory.name))
        replacement.start()
        self.addCleanup(replacement.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_health_and_cors(self):
        self.assertEqual(self.client.get('/api/health').json(), dict(ok=True, version='0.1.0', mock=main.MOCK))
        response = self.client.options('/api/providers', headers={'Origin': 'null', 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'content-type'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], '*')
        response = self.client.get('/api/health', headers={'Origin': 'https://unrelated.example'})
        self.assertEqual(response.headers['access-control-allow-origin'], '*')

    def test_encrypted_roundtrip(self):
        secret = 'test-secret-not-a-real-key'
        response = self.client.put('/api/providers', json={'llm': {'api_key': secret}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(secret, main.credentials.path.read_text('utf-8'))
        self.assertEqual(main.credentials.read()['llm']['api_key'], secret)
        self.assertEqual(self.client.get('/api/providers').json()['llm']['api_key'], '***')
        self.client.put('/api/providers', json={'llm': {'api_key': '***'}})
        self.assertEqual(main.credentials.read()['llm']['api_key'], secret)
        self.client.put('/api/providers', json={'llm': {'api_key': ''}})
        self.assertEqual(main.credentials.read()['llm']['api_key'], '')

    def test_validation(self):
        for body in ({'llm': {'api_key': 'not-saved'}, 'unknown': {}}, {'comfyui': {'concurrency': 2}}, {'llm': {'enabled': 'true'}}, {'llm': {'base_url': 'http://user:secret@localhost'}}):
            self.assertEqual(self.client.put('/api/providers', json=body).status_code, 422)
        self.assertEqual(main.credentials.read()['llm']['api_key'], '')
        response = self.client.put('/api/providers', content='{"api_key":"secret"', headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('secret', response.text)
        self.assertEqual(self.client.post('/api/providers/unknown/test').status_code, 404)

    def test_missing_key(self):
        result = self.client.post('/api/providers/qwen_image/test').json()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'MISSING_API_KEY')

    def test_refused_connection(self):
        # 占住但不监听，避免误碰其他服务。
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            result = self.client.put('/api/providers', json={'comfyui': {'base_url': f'http://127.0.0.1:{port}'}})
            self.assertEqual(result.status_code, 200, result.text)
            result = self.client.post('/api/providers/comfyui/test').json()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'PROVIDER_UNREACHABLE')

    def test_redaction(self):
        from app.providers.base import ProviderError
        response = self.client.put('/api/providers', json={'llm': {'api_key': 'super-secret-test'}})
        self.assertEqual(response.status_code, 200, response.text)
        async def fail(*args, **kwargs):
            raise ProviderError('PROVIDER_HTTP_ERROR', 'upstream echoed super-secret-test', 'test')
        with patch('app.providers.probe.request', fail):
            response = self.client.post('/api/providers/llm/test')
        self.assertNotIn('super-secret-test', response.text)
        self.assertIn('***', response.text)

if __name__ == '__main__':
    unittest.main()
