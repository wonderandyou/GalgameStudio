# -*- coding: utf-8 -*-
"""只重跑配音阶段，验证 _voice_for 修复是否生效。

项目 p_6dd092b4 图已经画好了（3 背景 + 2 立绘），只缺语音。
走 POST /regenerate {parts:['voice']}，不重画图，省额度也快。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')
PID = sys.argv[1] if len(sys.argv) > 1 else 'p_6dd092b4c572440abfd34298d04aa07c'
PDIR = ROOT / 'projects' / PID


def call(method, path, body=None, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:400]
        print(f'[HTTP {exc.code}] {detail}')
        raise SystemExit(1)


def wavs():
    return sorted(p for p in PDIR.rglob('*') if p.suffix.lower() in ('.wav', '.mp3'))


def logtail(n=12):
    logs = sorted(PDIR.rglob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return '(没有日志)'
    lines = logs[0].read_text(encoding='utf-8', errors='replace').strip().splitlines()
    return '\n'.join(lines[-n:])


before = len(wavs())
print(f'开始前现有音频：{before} 个')

r = call('POST', f'/api/projects/{PID}/regenerate', {'parts': ['voice']})
job = r.get('job_id') or r.get('id')
print(f'[提交] job={job}  （只重跑 voice 阶段）')

started = time.monotonic()
last = None
for _ in range(100):                        # 最多等 8 分钟
    time.sleep(4)
    cur = len(wavs())
    elapsed = time.monotonic() - started
    if cur != last:
        print(f'  [{elapsed:5.0f}s] 音频 {cur} 个（+{cur-before}）', flush=True)
        last = cur
    # 任务结束的判据：再提交一次会被 PROJECT_BUSY 拒
    if cur > before and elapsed > 20:
        break

final = wavs()
print(f'\n=== 结果 ===')
print(f'  音频 {before} -> {len(final)} 个')
for p in final[:8]:
    print(f'    {p.relative_to(PDIR)}  {p.stat().st_size/1024:.0f} KB')
print(f'\n=== 日志尾部 ===\n{logtail()}')

ok = len(final) > before
print('\n结果：' + ('PASS —— 配音跑通了' if ok else 'FAIL —— 还是没出音频'))
sys.exit(0 if ok else 1)
