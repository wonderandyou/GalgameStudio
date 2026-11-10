import base64
import copy
import json
import os
import threading
from pathlib import Path
from .config import DEFAULTS, STATE
from .providers.base import ProviderError
from .windows_crypto import transform

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)

class Credentials:
    def __init__(self, directory=STATE):
        self.path = Path(directory) / 'credentials.json'
        self.lock = threading.RLock()

    def read(self):
        result = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                envelope = json.loads(self.path.read_text('utf-8'))
                raw = transform(base64.b64decode(envelope['data']), 'unprotect')
                result.update(json.loads(raw))
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
