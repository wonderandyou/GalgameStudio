# -*- coding: utf-8 -*-
"""契约一致性端到端验收（只连已启动的 8790，不自己起服务）。

用法：
    app\\.venv\\Scripts\\python.exe tools\\audit_contract_e2e.py [base_url]

覆盖：health / create / generate SSE 全事件 / graph §3.6 结构 /
PUT graph / 单节点 regenerate / export renpy / tools\\verify_rpy.py。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8790').rstrip('/')
ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / 'app' / '.venv' / 'Scripts' / 'python.exe')
failures = []


def check(name, ok, detail=''):
    print(('[PASS] ' if ok else '[FAIL] ') + name + (('  ' + str(detail)) if detail else ''))
    if not ok:
        failures.append(name)


def sse(client, url):
    events, buffer = [], ''
    with client.stream('GET', url, timeout=30) as response:
        for chunk in response.iter_text():
            buffer += chunk
            while '\n\n' in buffer:
                block, buffer = buffer.split('\n\n', 1)
                if not block.strip() or block.lstrip().startswith(':'):
                    continue
                name = data = None
                for line in block.splitlines():
                    if line.startswith('event: '):
                        name = line[7:]
                    elif line.startswith('data: '):
                        data = json.loads(line[6:])
                if name:
                    events.append((name, data))
    return events


def main():
    with httpx.Client(base_url=BASE, timeout=60) as client:
        health = client.get('/api/health').json()
        check('health.ok', health.get('ok') is True, health)
        check('health.mock', health.get('mock') is True)

        created = client.post('/api/projects', json={'name': '契约审计项目'}).json()
        project = created['id']
        print('project =', project)

        started = client.post(f'/api/projects/{project}/generate', json={
            'requirement': '雨夜天台，两张车票。', 'params': {
                'node_count': 8, 'branch_depth': 2, 'auto_voice': True,
                'consistency': True, 'resolution': [1920, 1080]}}).json()
        events = sse(client, started['sse'])

        names = [e for e, _ in events]
        check('SSE 以 done 收尾', names[-1] == 'done', names[-3:])
        check('SSE agent 四角色', {d['agent'] for e, d in events if e == 'agent'} ==
              {'writer', 'art', 'voice', 'build'},
              sorted({d['agent'] for e, d in events if e == 'agent'}))
        check('SSE agent.state 合法',
              {d['state'] for e, d in events if e == 'agent'} <= {'idle', 'run', 'done', 'failed'})
        check('SSE node 带 node_id+patch',
              all('node_id' in d and 'patch' in d for e, d in events if e == 'node'))
        node_events = [d for e, d in events if e == 'node']
        check('SSE 逐节点发 patch（不是 done 时集中发）', 0 < len(node_events) < len(names))
        check('SSE done 带 stats', 'stats' in events[-1][1], list(events[-1][1]))
        check('SSE log 带 level+text',
              all('level' in d and 'text' in d for e, d in events if e == 'log'))
        check('无 error 事件', 'error' not in names)

        graph = client.get(f'/api/projects/{project}/graph').json()
        types = [n['type'] for n in graph['nodes']]
        check('type 仅 start/scene/choice/ending',
              set(types) <= {'start', 'scene', 'choice', 'ending'}, sorted(set(types)))
        check('恰好 1 个 start', types.count('start') == 1)
        check('至少 1 个 ending', types.count('ending') >= 1)
        choices_ok = True
        ending_ok = True
        lines_ok = True
        assets_ok = True
        for n in graph['nodes']:
            if n['type'] == 'choice':
                choices_ok &= isinstance(n.get('choices'), list) and 2 <= len(n['choices']) <= 4
            if n['type'] == 'ending':
                ending_ok &= (n.get('end_kind') in ('true', 'bad', 'alt')
                              and 'choices' not in n)
            if n['type'] != 'ending':
                lines_ok &= all(isinstance(l.get('voice'), str) and isinstance(l.get('dur'), (int, float))
                                for l in n['lines'])
                assets_ok &= isinstance(n.get('bg', {}).get('asset'), str)
        check('choice 必有 2–4 个 choices', choices_ok)
        check('ending 有 end_kind 且无 choices', ending_ok)
        check('lines 每条带 voice+dur', lines_ok)
        check('非 ending 有 bg.asset', assets_ok)
        check('asset 是相对项目根的 POSIX 路径',
              all(isinstance(n['bg']['asset'], str) and not n['bg']['asset'].startswith(('/', '\\'))
                  and ':' not in n['bg']['asset'] for n in graph['nodes']))

        graph['nodes'][1]['pos'] = {'x': 1234, 'y': 567}
        saved = client.put(f'/api/projects/{project}/graph', json=graph)
        check('PUT graph 200', saved.status_code == 200, saved.status_code)
        after = client.get(f'/api/projects/{project}/graph').json()
        check('PUT graph 真落盘', after['nodes'][1]['pos'] == {'x': 1234, 'y': 567},
              after['nodes'][1]['pos'])

        bad = json.loads(json.dumps(graph))
        bad['nodes'][1]['type'] = 'bogus'
        check('PUT graph 坏结构返回 422', client.put(
            f'/api/projects/{project}/graph', json=bad).status_code == 422)

        regen = client.post(f'/api/projects/{project}/nodes/n2/regenerate',
                            json={'parts': ['bg', 'voice']}).json()
        regen_events = sse(client, regen['sse'])
        check('regenerate 有 node 事件',
              [d['node_id'] for e, d in regen_events if e == 'node'] == ['n2'])
        check('regenerate 以 done 收尾', regen_events[-1][0] == 'done')

        exported = client.post(f'/api/projects/{project}/export', json={'format': 'renpy'}).json()
        export_events = sse(client, f"/api/jobs/{exported['job_id']}/events")
        check('export job 以 done 收尾', export_events[-1][0] == 'done')

    out = Path(exported['out'])
    check('script.rpy 存在', (out / 'game' / 'script.rpy').is_file())
    script = (out / 'game' / 'script.rpy').read_text('utf-8')

    import re
    scenes = re.findall(r'^\s*scene\s+bg\s+(\S+)', script, re.M)
    check('script 使用 scene bg X', len(scenes) > 0, scenes[:3])
    check('scene bg X 对应 images/bg/X.png',
          all((out / 'game' / 'images' / 'bg' / (name + '.png')).is_file() for name in scenes),
          scenes[:3])
    check('导出保留角色名（who）', 'Character(' in script)

    verify = subprocess.run([PY, str(ROOT / 'tools' / 'verify_rpy.py'), str(out)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    body = (verify.stdout or '') + (verify.stderr or '')
    print(body.strip())
    check('verify_rpy 输出 [PASS]', verify.returncode == 0 and '[PASS]' in body)
    check('verify_rpy 真的检查了背景（scene 数 > 0）',
          re.search(r'scene:\s*([1-9]\d*)', body) is not None,
          (re.search(r'scene:\s*\d+', body) or [None])[0] if re.search(r'scene:\s*\d+', body) else '')

    print('-' * 60)
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('ALL_PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
