# -*- coding: utf-8 -*-
"""24 节点终验：分支重复目标修复后的真跑。

盯三样：节点数、图/音频产物、run.log 尾部。
失败判据：日志出现 [error]；成功判据：pipeline 阶段到 done 或产物齐 + 日志出现统计行。
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


def call(method, path, body=None, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8') or '{}'
        return json.loads(raw)


def job_state(job_id):
    """任务状态必须读 .jobs/<id>.json 的 events —— /api/jobs/{id} 这个路由不存在（404），
    早期版本拿它判状态，结果任务早就 done 了脚本还在傻等（白等 6 分钟）。"""
    path = ROOT / '.jobs' / f'{job_id}.json'
    if not path.is_file():
        return 'running', ''
    data = json.loads(path.read_text(encoding='utf-8'))
    for event in reversed(data.get('events') or []):
        kind = event.get('event')
        if kind == 'done':
            return 'done', json.dumps(event.get('data', {}), ensure_ascii=False)
        if kind in ('failed', 'error'):
            return 'failed', json.dumps(event.get('data', {}), ensure_ascii=False)
    return 'running', ''


def main():
    health = call('GET', '/api/health')
    print(f"后端：ok={health['ok']} mock={health['mock']}", flush=True)
    if health['mock']:
        print('[FAIL] 还在 mock 模式')
        return 2

    project = call('POST', '/api/projects', {'name': '终验-24节点', 'requirement': REQUIREMENT})
    pid = project.get('id') or (project.get('project') or {}).get('id')
    print(f'[建项目] {pid}', flush=True)

    job = call('POST', f'/api/projects/{pid}/generate',
               {'requirement': REQUIREMENT,
                'params': {'node_count': 24, 'auto_voice': True, 'consistency': True}})
    job_id = job.get('job_id') or job.get('id')
    print(f'[提交] job={job_id}', flush=True)

    pdir = ROOT / 'projects' / pid
    started = time.monotonic()
    status, tail, last_line = 'running', '', ''
    rounds = 0
    while time.monotonic() - started < 900:          # 最多 15 分钟
        time.sleep(15)
        rounds += 1
        status, detail = job_state(job_id)
        state = {}
        if detail:
            try:
                state = json.loads(detail)
            except Exception:                        # noqa: BLE001
                state = {}

        nodes = imgs = auds = 0
        logs = sorted(pdir.rglob('*.log')) if pdir.is_dir() else []
        if logs:
            text = max(logs, key=lambda p: p.stat().st_mtime).read_text(encoding='utf-8', errors='replace')
            lines = [ln for ln in text.strip().splitlines() if ln.strip()]
            tail = lines[-1] if lines else ''
            stage = ''
            for ln in reversed(lines):
                if '] [job] ' in ln:
                    stage = ln.split('] [job] ')[-1][:66]
                    break
        else:
            stage = ''
        graph = pdir / 'graph.json'
        if graph.is_file():
            try:
                nodes = len(json.loads(graph.read_text(encoding='utf-8')).get('nodes') or [])
            except Exception:                        # noqa: BLE001
                nodes = -1
        imgs = len([p for p in pdir.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.webp')])
        auds = len([p for p in pdir.rglob('*') if p.suffix.lower() in ('.wav', '.mp3', '.ogg')])

        line = (f'[{time.monotonic()-started:6.0f}s] {status:8s} 节点 {nodes:3d}  '
                f'图 {imgs:3d} 音频 {auds:3d} | {stage}')
        print(line, flush=True)

        if '[error]' in (tail or ''):
            status = 'failed'
            break
        if status in ('done', 'failed', 'error'):
            break
        if stage.startswith('done') or '全部完成' in stage:
            status = 'done'
            break

    print(f'\n=== 结束 status={status} 用时 {time.monotonic()-started:.0f}s 项目 {pid} ===', flush=True)
    if tail:
        print('日志尾：', tail[:400], flush=True)

    imgs = sorted(p for p in pdir.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.webp'))
    auds = sorted(p for p in pdir.rglob('*') if p.suffix.lower() in ('.wav', '.mp3', '.ogg'))
    graph = pdir / 'graph.json'
    nodes = json.loads(graph.read_text(encoding='utf-8')).get('nodes') if graph.is_file() else []
    print(f'磁盘：节点 {len(nodes)} / 图 {len(imgs)} / 音频 {len(auds)}', flush=True)
    for p in imgs[:6]:
        print(f'  IMG {p.relative_to(pdir)} {p.stat().st_size // 1024} KB', flush=True)
    for p in auds[:6]:
        print(f'  AUD {p.relative_to(pdir)} {p.stat().st_size // 1024} KB', flush=True)

    # 结构自查：分支是否还有重复目标
    bad = []
    for node in nodes:
        targets = [c.get('to') for c in (node.get('choices') or [])]
        if node.get('type') == 'choice' and len(set(targets)) != len(targets):
            bad.append(node['id'])
    print('分支重复目标：' + ('无 ✔' if not bad else f'仍有 {bad} ✗'), flush=True)
    ok = status == 'done' and bool(imgs) and bool(auds) and not bad
    print('结果：' + ('PASS' if ok else 'FAIL'), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
