"""无需可写临时目录的 P0 HTTP 回归。"""
import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main
from app.config import DEFAULTS
from app.store import atomic_json

class P0Tests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_preflight_and_validation(self):
        r = self.client.options('/api/providers', headers={'Origin':'null', 'Access-Control-Request-Method':'PUT', 'Access-Control-Request-Headers':'content-type'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['access-control-allow-origin'], '*')
        r = self.client.put('/api/providers', json={'local_llm': {'base_url': 'bad-url'}}, headers={'Origin':'null'})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()['error']['field'], 'local_llm.base_url')
        self.assertEqual(r.headers['access-control-allow-origin'], '*')

    def test_global_500_cors(self):
        with patch.object(main.credentials, 'write', side_effect=RuntimeError('unexpected')):
            r = self.client.put('/api/providers', json={}, headers={'Origin':'null'})
        self.assertEqual(r.status_code, 500, r.text)
        self.assertEqual(r.headers['access-control-allow-origin'], '*')
        self.assertEqual(r.json()['error']['code'], 'INTERNAL_ERROR')

    def test_write_error_path_and_cors(self):
        def fail(body):
            with patch('app.store.tempfile.NamedTemporaryFile', side_effect=PermissionError(13, 'Permission denied')):
                atomic_json('credentials-test.json', {})
        with patch.object(main.credentials, 'write', side_effect=fail), patch.object(main.credentials, 'read', return_value=copy.deepcopy(DEFAULTS)):
            r = self.client.put('/api/providers', json={}, headers={'Origin':'null'})
        self.assertEqual(r.status_code, 500, r.text)
        error = r.json()['error']
        self.assertEqual(error['code'], 'STATE_NOT_WRITABLE')
        self.assertIn('credentials-test.json', error['message'])
        self.assertIn('Permission denied', error['message'])
        self.assertEqual(r.headers['access-control-allow-origin'], '*')

if __name__ == '__main__':
    unittest.main()
