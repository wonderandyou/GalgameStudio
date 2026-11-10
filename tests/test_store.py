import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import call, patch

from app import store
from app.providers.base import ProviderError


class AtomicStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'job.json'
        store.atomic_json(self.path, {'version': 0})

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_retries_permission_errors_then_commits(self):
        original = Path.replace
        attempts = []

        def replace(source, target):
            attempts.append(source)
            if len(attempts) < 3:
                raise PermissionError(5, 'temporarily opened by reader')
            return original(source, target)

        with patch.object(Path, 'replace', autospec=True, side_effect=replace), \
                patch.object(store.time, 'sleep') as sleep:
            store.atomic_json(self.path, {'version': 1})

        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [call(0.02), call(0.04)])
        self.assertEqual(json.loads(self.path.read_text('utf-8')), {'version': 1})
        self.assert_no_temporary_files()

    def test_exhaustion_keeps_original_and_reports_error(self):
        with patch.object(Path, 'replace', side_effect=PermissionError(5, 'still opened')) as replace, \
                patch.object(store.time, 'sleep') as sleep:
            with self.assertRaises(ProviderError) as caught:
                store.atomic_json(self.path, {'version': 1})

        self.assertEqual(replace.call_count, 5)
        self.assertEqual(sleep.call_args_list, [call(0.02), call(0.04), call(0.06), call(0.08)])
        self.assertEqual(caught.exception.payload['code'], 'STATE_NOT_WRITABLE')
        self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertEqual(json.loads(self.path.read_text('utf-8')), {'version': 0})
        self.assert_no_temporary_files()

    def test_other_os_errors_are_not_retried(self):
        with patch.object(Path, 'replace', side_effect=OSError(22, 'invalid operation')) as replace, \
                patch.object(store.time, 'sleep') as sleep:
            with self.assertRaises(ProviderError) as caught:
                store.atomic_json(self.path, {'version': 1})

        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(caught.exception.payload['code'], 'STATE_NOT_WRITABLE')
        self.assertEqual(json.loads(self.path.read_text('utf-8')), {'version': 0})
        self.assert_no_temporary_files()

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing test')
    def test_recovers_from_actual_windows_reader_lock(self):
        opened = threading.Event()
        release = threading.Event()

        def read_with_handle_open():
            # The ordinary Windows reader holds a handle that blocks replacement.
            with self.path.open('r', encoding='utf-8') as stream:
                opened.set()
                release.wait(2)
                return json.load(stream)

        original = Path.replace
        denied = []

        def replace(source, target):
            try:
                return original(source, target)
            except PermissionError as exc:
                denied.append(exc.winerror)
                release.set()
                raise

        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(read_with_handle_open)
            try:
                self.assertTrue(opened.wait(2))
                with patch.object(Path, 'replace', autospec=True, side_effect=replace):
                    store.atomic_json(self.path, {'version': 1})
            finally:
                release.set()
            self.assertEqual(reader.result(timeout=2), {'version': 0})

        self.assertTrue(denied)
        self.assertTrue(all(code in (5, 32) for code in denied), denied)
        self.assertEqual(json.loads(self.path.read_text('utf-8')), {'version': 1})
        self.assert_no_temporary_files()

if __name__ == '__main__':
    unittest.main()
