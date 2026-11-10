# -*- coding: utf-8 -*-
r"""真模型实测：往 8790（真实 provider）发一个小项目，看内置 agent 能不能按 skill 产出节点。

用法：python tools/_run_real_gen.py [节点数] [超时秒]
日志写 tools/real_gen.log（stdout 也会打，方便后台读）。
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

BASE = 'http://127.0.0.1:8790'
PROJ = Path(r'D:\软件\GalgameStudio')
LOG = PROJ / 'tools' / 'real_gen.log'
NODES = int(sys.argv[1]) if len(sys.argv) > 1 else 40
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 900

logf = open(LOG, 'w', encoding='utf-8')


def log(msg=''):
    line = str(msg)
    print(line, flush=True)
    logf.write(line + '\n')
    logf.flush()


def main():
    health = httpx.get(BASE + '/api/health', timeout=10).json()
    log(f'后端: {json.dumps(health, ensure_ascii=False)}')
    if health.get('mock'):
        log('✗ 后端是 mock 模式，测不了真模型')
        return 2

    provs = httpx.get(BASE + '/api/providers', timeout=10).json()
    llm = provs.get('llm') or {}
    log(f"llm: enabled={llm.get('enabled')} model={llm.get('model')} base={llm.get('base_url')}")
    if not llm.get('enabled'):
        log('✗ llm 未启用')
        return 2

    r = httpx.post(BASE + '/api/projects', json={'name': f'真模型测试-{NODES}'}, timeout=60)
    r.raise_for_status()
    pid = r.json()['id']
    log(f'项目: {pid}')

    minutes = max(1, round(NODES / 8.125))
    body = {'requirement': '一个雨夜的天台，两个人，她要离开这座城市',
            'params': {'node_count': NODES, 'target_minutes': minutes}}
    r = httpx.post(BASE + f'/api/projects/{pid}/generate', json=body, timeout=180)
    r.raise_for_status()
    info = r.json()
    jid = info['job_id']
    log(f"任务: {jid}  mock={info.get('mock')}")

    t0 = time.time()
    last = -1
    stable = 0
    state = 'running'
    while time.time() - t0 < TIMEOUT:
        try:
            g = httpx.get(BASE + f'/api/projects/{pid}/graph', timeout=15).json()
            n = len(g.get('nodes') or [])
        except Exception:
            n = last
            g = {}
        if n != last:
            stable = 0
            last = n
            log(f'  [{time.time()-t0:6.1f}s] 节点={n} 章={len(g.get("chapters") or [])}')
        else:
            stable += 1
            if n >= NODES:
                state = 'reached'
                break
            # 真模型一轮思考可能十几秒不落盘，别用 40 秒当卡死（实测误判过）
            if stable >= 180:
                state = 'stalled'
                break
        time.sleep(1.0)

    elapsed = time.time() - t0
    log(f'\n=== 结果（{elapsed:.1f}s）state={state} ===')

    g = httpx.get(BASE + f'/api/projects/{pid}/graph', timeout=60).json()
    nodes = g.get('nodes') or []
    edges = g.get('edges') or []
    chapters = g.get('chapters') or []
    log(f'节点: {len(nodes)}/{NODES}  边: {len(edges)}  章: {len(chapters)}')
    log('类型: ' + str(dict(Counter(n.get('type') for n in nodes))))
    log('结局: ' + str(dict(Counter(n.get('end_kind') for n in nodes if n.get('type') == 'ending'))))
    log('有 choices 的节点: %d' % len([n for n in nodes if n.get('choices')]))

    ids = {x['id'] for x in nodes}
    bad = [e for e in edges if e.get('to') not in ids or e.get('from') not in ids]
    log('断边: %d' % len(bad))
    incoming = {e['to'] for e in edges}
    orphans = [x['id'] for x in nodes if x.get('type') != 'start' and x['id'] not in incoming]
    log('孤儿: %d %s' % (len(orphans), orphans[:6]))

    lines = sum(len(x.get('lines') or []) for x in nodes)
    chars = sum(len(l.get('text') or '') for x in nodes for l in (x.get('lines') or []))
    log(f'台词: {lines} 句 / {chars} 字 ≈ {chars/300:.0f} 分钟')
    if nodes:
        xs = [x['pos']['x'] for x in nodes]
        ys = [x['pos']['y'] for x in nodes]
        log(f"布局: x {min(xs)}→{max(xs)}（宽 {max(xs)-min(xs)}） y {min(ys)}→{max(ys)}（高 {max(ys)-min(ys)}）")
    log('writer_metrics: ' + json.dumps((g.get('agent_state') or {}).get('writer_metrics') or {}))
    log(f'PROJECT_ID={pid}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    finally:
        logf.close()
