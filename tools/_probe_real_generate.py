# -*- coding: utf-8 -*-
"""真跑一次生成，证明「点生成会真的出图/出语音」。

只建一个小项目（8 节点）控制花费：
  - LLM 写 8 个节点
  - 生 2~3 张背景图（每张约 12 秒）
  - 合成若干句配音
跑完直接数磁盘上的图/音频文件，不靠接口自报。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')

REQUIREMENT = ('雨夜天台，两个高中生说好一起离开这座城。短篇，一条小分支，'
               '两个结局。对话克制，不要独白。')


def call(method, path, body=None, timeout=30):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8') or '{}'
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:400]
        raise SystemExit(f'[FAIL] {method} {path} -> HTTP {exc.code}: {detail}')


def main():
    health = call('GET', '/api/health')
    print(f"后端：ok={health['ok']} mock={health['mock']}")
    if health['mock']:
        print('[FAIL] 后端还在 mock 模式，先解决这个')
        return 2

    project = call('POST', '/api/projects', {'name': '验证-真生成', 'requirement': REQUIREMENT})
    pid = project.get('id') or project.get('project', {}).get('id')
    print(f'[建项目] {pid}')

    job = call('POST', f'/api/projects/{pid}/generate',
               {'requirement': REQUIREMENT,
                'params': {'node_count': 20, 'auto_voice': True, 'consistency': True}})
    job_id = job.get('job_id') or job.get('id')
    print(f'[提交生成] job={job_id}')

    started = time.monotonic()
    last_note = ''
    for _ in range(240):                     # 最多等 8 分钟
        time.sleep(2)
        state = call('GET', f'/api/jobs/{job_id}')
        status = state.get('status') or state.get('state')
        elapsed = time.monotonic() - started
        note = f'{status} {elapsed:.0f}s'
        if note != last_note:
            print(f'  [{elapsed:5.0f}s] {status}  stage={state.get("stage") or "-"}  '
                  f'{(state.get("message") or "")[:70]}')
            last_note = note
        if status in ('done', 'failed', 'error'):
            break

    print(f'\n[结束] status={status}  耗时 {time.monotonic()-started:.1f} 秒')
    if status != 'done':
        print(f'[FAIL] 生成没成功：{json.dumps(state, ensure_ascii=False)[:600]}')
        return 1

    # —— 数磁盘产物，不看接口自报 ——
    pdir = ROOT / 'projects' / pid
    imgs = [p for p in pdir.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.webp')]
    auds = [p for p in pdir.rglob('*') if p.suffix.lower() in ('.wav', '.mp3', '.ogg')]
    print(f'\n=== 磁盘产物 ===')
    print(f'  图片 {len(imgs)} 个：')
    for p in sorted(imgs)[:8]:
        print(f'    {p.relative_to(pdir)}  {p.stat().st_size/1024:.0f} KB')
    print(f'  音频 {len(auds)} 个：')
    for p in sorted(auds)[:8]:
        print(f'    {p.relative_to(pdir)}  {p.stat().st_size/1024:.0f} KB')

    graph = json.loads((pdir / 'graph.json').read_text(encoding='utf-8'))
    print(f'\n节点数：{len(graph.get("nodes", []))}')
    print(f'pipeline：{json.dumps(graph.get("agent_state", {}).get("pipeline", {}), ensure_ascii=False)[:300]}')

    ok = len(imgs) > 0 and len(auds) > 0
    print('\n结果：' + ('PASS —— 真出图真出声' if ok else 'FAIL —— 图或音频缺了'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

