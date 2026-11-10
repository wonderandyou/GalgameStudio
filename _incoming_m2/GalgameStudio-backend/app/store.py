import base64
import copy
import json
import os
import threading
import tempfile
from pathlib import Path
from .config import DEFAULTS, STATE
from .providers.base import ProviderError
from .windows_crypto import transform

def atomic_bytes(path, data):
    path = Path(path)
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            temp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    except OSError as exc:
        raise ProviderError('STATE_NOT_WRITABLE', f'{path}: {type(exc).__name__}: {exc}', 'store.write', 500) from exc
    finally:
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass

def atomic_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))

def read_json(path):
    try:
        return json.loads(Path(path).read_text('utf-8-sig'))
    except OSError as exc:
        raise ProviderError('STATE_NOT_READABLE', f'{path}: {type(exc).__name__}: {exc}', 'store.read', 500) from exc
    except ValueError as exc:
        raise ProviderError('STATE_CORRUPT', f'{path}: JSON 格式损坏', 'store.read', 500) from exc

class Credentials:
    def __init__(self, directory=STATE):
        self.path = Path(directory) / 'credentials.json'
        self.lock = threading.RLock()

    def read(self):
        result = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                envelope = read_json(self.path)
                raw = transform(base64.b64decode(envelope['data']), 'unprotect')
                result.update(json.loads(raw))
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError('CREDENTIALS_UNREADABLE', '无法解密凭据，请使用原 Windows 用户账户。', 'store.credentials', 500) from exc
        return result

    def masked(self):
        result = self.read()
        for cfg in result.values():
            for key in cfg:
                if any(x in key.lower() for x in ('key', 'token', 'secret', 'password')):
                    cfg[key] = '***'
        return result

    def write(self, patch):
        with self.lock:
            return self._write(patch)

    def _write(self, patch):
        # *** 表示保持原值；空字符串表示明确清除密钥。
        result = self.read()
        for kind, cfg in patch.items():
            for key, value in cfg.items():
                if value != '***':
                    result[kind][key] = value
        try:
            raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
            encrypted = transform(raw, 'protect')
        except Exception as exc:
            raise ProviderError('CREDENTIALS_ENCRYPTION_FAILED', 'Windows DPAPI 加密失败；未保存明文。', 'store.credentials', 500) from exc
        atomic_json(self.path, dict(protection='Windows-DPAPI', data=base64.b64encode(encrypted).decode('ascii')))
        return self.masked()
