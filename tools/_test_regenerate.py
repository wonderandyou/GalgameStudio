"""真模式单节点重生成验收：背景重画 + 语音重合成。

先备份 graph.json，跑完恢复（素材文件是新增的，留着无害）。
"""
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')
PID = 'p_e699808204d042ea97fbdf33baf49488'
PDIR = ROOT / 'projects' / PID


def call(method, path, body=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, '_body': exc.read().decode('utf-8', 'replace')[:400]}


def main():
    backup = PDIR / 'graph.json.regenbak'
    if backup.is_file():
        shutil.copy(backup, PDIR / 'graph.json')      # 每次从原始状态开始测
        print('已从备份恢复 graph.json')
    else:
        shutil.copy(PDIR / 'graph.json', backup)
        print('已备份 graph.json')

    graph = json.loads((PDIR / 'graph.json').read_text(encoding='utf-8'))
    node = next(n for n in graph['nodes']
                if n['type'] == 'scene' and n.get('lines') and n['bg'].get('asset'))
    nid = node['id']
    old_bg = node['bg']['asset']
    old_voice = node['lines'][0].get('voice')
    print(f'目标节点 {nid}  原背景 {old_bg}  原语音 {old_voice}')

    res = call('POST', f'/api/projects/{PID}/nodes/{nid}/regenerate',
               {'parts': ['bg', 'voice']})
    if '_http' in res:
        print('提交失败', res['_http'], res['_body'])
        return 1
    jid = res['job_id']
    print('job', jid)

    jf = ROOT / '.jobs' / f'{jid}.json'
    final = None
    for _ in range(80):
        time.sleep(3)
        if not jf.is_file():
            continue
        data = json.loads(jf.read_text(encoding='utf-8'))
        events = data.get('events') or []
        done = [e for e in events if e.get('event') == 'done']
        err = [e for e in events if e.get('event') == 'error']
        logs = [e['data'].get('text') for e in events if e.get('event') == 'log']
        if done:
            final = ('done', done[-1]['data'], logs)
            break
        if err:
            final = ('error', err[-1]['data'], logs)
            break
        if _ % 5 == 0:
            print('  等待…', len(events), '事件')

    if final is None:
        print('超时')
        return 1
    kind, payload, logs = final
    print('状态:', kind)
    for line in logs[-6:]:
        print('  log:', line)
    print('  data:', json.dumps(payload, ensure_ascii=False)[:400])

    after = json.loads((PDIR / 'graph.json').read_text(encoding='utf-8'))
    node2 = next(n for n in after['nodes'] if n['id'] == nid)
    new_bg = node2['bg']['asset']
    new_voice = node2['lines'][0].get('voice')
    print(f'新背景 {new_bg}（变了: {new_bg != old_bg}）')
    print(f'新语音 {new_voice}（变了: {new_voice != old_voice}）')
    ok = kind == 'done' and new_bg != old_bg and new_voice != old_voice
    print('结果:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
