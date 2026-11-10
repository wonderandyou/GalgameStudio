# -*- coding: utf-8 -*-
"""经理验收：验证前端「导出 Unity」按钮背后那条完整链路（不点按钮，但走同一条 API）。

前端点按钮 → POST /export {format:'unity'} → 拿 out → watchJob 轮询进度。
这里直接走前两步，确认 out 是真 exe 路径而不是 export/unity 目录。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
PID = 'p_63b2b758e0ab46d1bf4201629468eb36'


def main():
    body = json.dumps({'format': 'unity'}).encode('utf-8')
    req = urllib.request.Request(BASE + f'/api/projects/{PID}/export', data=body,
                                 method='POST', headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            payload = json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:800]
        print(f'[FAIL] HTTP {exc.code}: {detail}')
        return 2
    except Exception as exc:                                   # noqa: BLE001
        print(f'[FAIL] {type(exc).__name__}: {exc}')
        return 3

    dt = time.time() - t0
    out = str(payload.get('out') or '')
    path = Path(out)
    print(f'[OK] 耗时 {dt:.1f} 秒')
    print(f'[OK] ok={payload.get("ok")}  job_id={payload.get("job_id")}')
    print(f'[OK] out = {out}')
    is_exe = out.lower().endswith('.exe')
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    print(f'     是 exe 路径: {is_exe}   文件存在: {exists}   大小: {size} 字节')
    if payload.get('warnings'):
        print(f'     warnings: {payload["warnings"]}')

    ok = payload.get('ok') and is_exe and exists and size > 300_000
    print('结果:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
