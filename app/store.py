# -*- coding: utf-8 -*-
"""状态文件与凭据的读写入。

两类数据，两种口径：

· **状态**（settings / credentials）—— 必须**原子写**，写一半断电会毁掉全部配置。
· **凭据**—— 落盘前一律用 Windows DPAPI 加密，文件里只有密文 + 一个 `protection` 标记。

为什么原子写要自己实现
--------------------
直接覆盖写：如果写到一半崩了/断电，文件变成半截，下次启动直接读不出来。
这里的做法是「写临时文件 → fsync → rename」。rename 在同一分区是原子的。

`atomic_bytes` 还额外处理 Windows 的一个老毛病：`os.replace` 会因为
**目标文件正被别的进程打开**而抛 PermissionError（杀毒软件在扫、另一个实例在读）。
实测偶发，所以重试 5 次、间隔递增（20/40/60/80ms）。
"""
import base64
import copy
import json
import os
import threading
import tempfile
import time
from pathlib import Path
from .config import DEFAULTS, STATE
from .providers.base import ProviderError
from .windows_crypto import transform


def atomic_bytes(path, data):
    """把 bytes 原子地写到 path：要么是旧内容，要么是完整的新内容，没有半截。

    步骤：建父目录 → 临时文件写入 + flush + fsync → os.replace 覆盖目标。
    失败一律转成 ProviderError（保留原始异常作为 cause，方便追）。

    注意 finally 里会删临时文件：正常路径下 rename 已经把它用掉了，
    这行只是兜底，避免权限出错时在目录里堆一堆 `.tmp`。
    """
    path = Path(path)
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            temp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                temp.replace(path)
                return
            except PermissionError:
                # 目标被占用：等一下再试，最后一次还不行就真的抛出去
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    except OSError as exc:
        raise ProviderError('STATE_NOT_WRITABLE', f'{path}: {type(exc).__name__}: {exc}', 'store.write', 500) from exc
    finally:
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def atomic_json(path, data):
    """把 dict 原子地写成 JSON。

    `ensure_ascii=False`：中文直接落盘，文件可读、diff 也好看。
    `allow_nan=False`：NaN/Infinity 不是合法 JSON，写进去别的解析器会炸，
    这里让它当场报错而不是留个坏文件。
    """
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))


def read_json(path):
    """读 JSON，并把两类失败翻译成带 code 的 ProviderError。

    `utf-8-sig` 是故意的：Windows 记事本另存会带 BOM，用 utf-8 读会在开头
    多出一个 \\ufeff，json.loads 直接报「Expecting value」。
    """
    try:
        return json.loads(Path(path).read_text('utf-8-sig'))
    except OSError as exc:
        raise ProviderError('STATE_NOT_READABLE', f'{path}: {type(exc).__name__}: {exc}', 'store.read', 500) from exc
    except ValueError as exc:
        raise ProviderError('STATE_CORRUPT', f'{path}: JSON 格式损坏', 'store.read', 500) from exc


class Credentials:
    """供应商凭据（API key / base_url 等）的读写，落盘用 DPAPI 加密。

    文件结构：{"protection": "Windows-DPAPI", "data": "<base64 密文>"}
    `protection` 是给将来的算法升级留的标记；现在只认 DPAPI。

    DPAPI 是按**当前 Windows 用户**加密的：换个用户账户读不出来。
    这是有意的 —— 凭据不该跨账户共享。
    """

    def __init__(self, directory=STATE):
        """directory 默认是 config.STATE（受 GAL_STATE_DIR 环境变量控制，测试用）。"""
        self.path = Path(directory) / 'credentials.json'
        self.lock = threading.RLock()

    def read(self):
        """读全部凭据。文件不存在就返回默认配置。

        注意异常分两种：ProviderError 原样往上抛（已经是结构化错误），
        其他异常统一转成「无法解密」—— 那通常是换了 Windows 账户或文件被改坏，
        这时候给用户的提示必须明确说「用原账户」，否则他会以为是程序 bug。
        """
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
        """读出来但把所有凭据字段抹成 `***`。给前端 / 日志用，绝不能把 key 发出去。"""
        result = self.read()
        for cfg in result.values():
            for key in cfg:
                if any(x in key.lower() for x in ('key', 'token', 'secret', 'password')):
                    cfg[key] = '***'
        return result

    def write(self, patch):
        """带锁写（同进程多线程并发保存时不会互相覆盖）。"""
        with self.lock:
            return self._write(patch)

    def _write(self, patch):
        """把 patch 合并进现有配置再加密落盘。

        `***` 表示「保持原值」：前端拿到的就是打码后的配置，它回传时那些字段
        必然还是 `***`，不能当成真的新 key 写进去。
        空字符串表示「明确清除」，这是唯一能删掉 key 的方式。

        加密失败要显式报错并且**不落盘** —— 绝不把明文写进文件。
        """
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
