"""重抠立绘验收：parts=['char']，看是否把带白边的立绘重新抠成透明。"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')
PID = 'p_e699808204d042ea97fbdf33baf49488'
PDIR = ROOT / 'projects' / PID


def post(path, body=None, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def main():
    graph = json.loads((PDIR / 'graph.json').read_text(encoding='utf-8'))
    node = next(n for n in graph['nodes'] if n.get('chars'))
    nid = node['id']
    print(f"目标节点 {nid}  立绘 {[c['pose'] for c in node['chars']]}")
    res = post(f'/api/projects/{PID}/nodes/{nid}/regenerate', {'parts': ['char']})
    jid = res['job_id']
    print('job', jid)
    jf = ROOT / '.jobs' / f'{jid}.json'
    final = None
    for _ in range(60):
        time.sleep(3)
        if not jf.is_file():
            continue
        events = json.loads(jf.read_text(encoding='utf-8')).get('events') or []
        done = [e for e in events if e.get('event') == 'done']
        err = [e for e in events if e.get('event') == 'error']
        if done:
            final = ('done', done[-1]['data'], [e['data'].get('text') for e in events if e.get('event') == 'log'])
            break
        if err:
            final = ('error', err[-1]['data'], [e['data'].get('text') for e in events if e.get('event') == 'log'])
            break
    if not final:
        print('超时')
        return 1
    kind, payload, logs = final
    print('状态:', kind)
    for line in logs[-5:]:
        print('  log:', line)
    print('  stats:', json.dumps(payload.get('stats', {}), ensure_ascii=False))
    after = json.loads((PDIR / 'graph.json').read_text(encoding='utf-8'))
    node2 = next(n for n in after['nodes'] if n['id'] == nid)
    print('新立绘', [c['pose'] for c in node2['chars']])
    return 0 if kind == 'done' else 1


if __name__ == '__main__':
    sys.exit(main())
