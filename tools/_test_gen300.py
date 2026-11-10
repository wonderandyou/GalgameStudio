# -*- coding: utf-8 -*-
r"""实测：起隔离后端跑大节点生成，统计真实结果。

用法：python _test_gen300.py [节点数]   （默认 300）
日志同时写 tools\gen_test.log 和 stdout。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

PROJ = Path(r'D:\软件\GalgameStudio')
PY = PROJ / 'app' / '.venv' / 'Scripts' / 'python.exe'
PORT = 8796
BASE = f'http://127.0.0.1:{PORT}'
NODES = int(sys.argv[1]) if len(sys.argv) > 1 else 300
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 900
MOCKMODE = sys.argv[3] if len(sys.argv) > 3 else '1'

WORK = PROJ / 'tmp' / 'gen_test'
if WORK.exists():
    shutil.rmtree(WORK, ignore_errors=True)
ROOT = WORK / 'root'
ROOT.mkdir(parents=True, exist_ok=True)

LOGF = PROJ / 'tools' / 'gen_test.log'
logf = open(LOGF, 'w', encoding='utf-8')


def log(msg=''):
    line = str(msg)
    print(line, flush=True)
    logf.write(line + '\n')
    logf.flush()


backend = open(PROJ / 'tools' / 'gen300_backend.log', 'w', encoding='utf-8')
env = dict(os.environ)
env.update(GAL_MOCK=MOCKMODE, GAL_PORT=str(PORT), GAL_ROOT=str(ROOT),
           GAL_STATE_DIR=str(WORK / 'state'), GAL_MOCK_STEP_DELAY='0')

log(f'根目录: {ROOT}')
log(f'目标节点: {NODES}  超时: {TIMEOUT}s  GAL_MOCK={MOCKMODE}')

proc = subprocess.Popen([str(PY), '-m', 'app.main'], cwd=str(PROJ), env=env,
                        stdout=backend, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
try:
    ready = False
    for _ in range(120):
        try:
            if httpx.get(BASE + '/api/health', timeout=1.0).status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        log('✗ 后端 60s 未就绪')
        sys.exit(1)
    log('✓ 后端就绪')

    r = httpx.post(BASE + '/api/projects', json={'name': f'TEST-{NODES}'}, timeout=60)
    r.raise_for_status()
    pid = r.json()['id']
    log(f'✓ 项目 {pid}')

    # 时长必须跟节点数自洽：节点数是硬约束，时长是派生量
    minutes = max(1, round(NODES / 8.125))
    body = {'requirement': '一个雨夜的天台，两个人，她要离开这座城市',
            'params': {'node_count': NODES, 'target_minutes': minutes}}
    r = httpx.post(BASE + f'/api/projects/{pid}/generate', json=body, timeout=120)
    r.raise_for_status()
    info = r.json()
    jid = info['job_id']
    log(f"✓ 任务 {jid} mock={info.get('mock')}")

    # 只轮询 API，**绝不读 job 文件** —— Windows 上文件被打开时
    # 后端 os.replace 临时文件会报 WinError 5，等于自己把任务搞挂。
    t0 = time.time()
    last = -1
    stable = 0
    state = 'running'
    while time.time() - t0 < TIMEOUT:
        try:
            g = httpx.get(BASE + f'/api/projects/{pid}/graph', timeout=10).json()
            n = len(g.get('nodes') or [])
        except Exception:
            n = last
        if n != last:
            stable = 0
            last = n
            ch = len(g.get('chapters') or []) if isinstance(g, dict) else 0
            log(f'  [{time.time()-t0:5.1f}s] 节点={n} 章={ch}')
        else:
            stable += 1
            if n >= NODES:
                state = 'reached'
                break
            if stable >= 8:                 # 8 秒没涨 = 停住了
                state = 'stalled'
                break
        time.sleep(1.0)

    elapsed = time.time() - t0
    log(f'\n=== 结果（{elapsed:.1f}s）state={state} ===')

    # job 详情
    jobfiles = sorted((ROOT / '.jobs').glob('*.json'), key=lambda p: p.stat().st_mtime) if (ROOT / '.jobs').is_dir() else []
    log(f'job 文件: {[p.name for p in jobfiles]}')
    if jobfiles:
        jd = json.loads(jobfiles[-1].read_text(encoding='utf-8'))
        if jd.get('error'):
            log('job.error: ' + json.dumps(jd['error'], ensure_ascii=False))
        for ev in (jd.get('events') or []):
            d = ev.get('data') or {}
            if ev.get('event') == 'agent' and d.get('state') in ('done', 'failed'):
                log(f"  agent {d.get('agent'):6s} {d.get('state'):6s} {d.get('msg','')}")
            if ev.get('event') == 'log' and d.get('level') in ('warn', 'error'):
                log(f"  log[{d.get('level')}] {d.get('text')}")

    g = httpx.get(BASE + f'/api/projects/{pid}/graph', timeout=60).json()
    nodes = g.get('nodes') or []
    edges = g.get('edges') or []
    chapters = g.get('chapters') or []
    log(f'\n节点: {len(nodes)}/{NODES}   边: {len(edges)}   章: {len(chapters)}')
    log('类型: ' + str(dict(Counter(n.get('type') for n in nodes))))
    log('结局: ' + str(dict(Counter(n.get('end_kind') for n in nodes if n.get('type') == 'ending'))))
    log('有分支节点: %d' % len([n for n in nodes if n.get('choices')]))

    ids = {n['id'] for n in nodes}
    bad = [e for e in edges if e.get('to') not in ids or e.get('from') not in ids]
    log('断边: %d' % len(bad))
    if bad:
        log('  ' + json.dumps(bad[:3], ensure_ascii=False))
    incoming = {e['to'] for e in edges}
    orphans = [n['id'] for n in nodes if n.get('type') != 'start' and n['id'] not in incoming]
    log('孤儿: %d %s' % (len(orphans), orphans[:6]))
    durbad = [n['id'] for n in nodes for ln in (n.get('lines') or []) if not ln.get('dur') or ln['dur'] <= 0]
    log('dur<=0: %d' % len(durbad))
    log('writer_metrics: ' + json.dumps((g.get('agent_state') or {}).get('writer_metrics') or {}))

    lines = sum(len(n.get('lines') or []) for n in nodes)
    chars = sum(len(l.get('text') or '') for n in nodes for l in (n.get('lines') or []))
    log(f'台词: {lines} 句 / {chars} 字 ≈ {chars/300:.0f} 分钟')

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    backend.close()
    logf.close()
