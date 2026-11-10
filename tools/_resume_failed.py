"""用失败项目原样的 body 续跑，验证放宽后的规则能不能过 c04。"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')
PID = 'p_30620485f9924bd688fb74e82b8b9c64'


def main():
    best = None
    for path in (ROOT / '.jobs').glob('*.json'):
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                   # noqa: BLE001
            continue
        if job.get('kind') != 'generate' or job.get('project_id') != PID:
            continue
        if best is None or job.get('started', '') > best.get('started', ''):
            best = job
    if best is None:
        print('没找到这个项目的 generate job')
        return 1
    print('用 job', best['id'], '的 body 续跑（原状态', best.get('status'), '）')
    body = best['body']

    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE + f'/api/projects/{PID}/generate', data=data,
                                 method='POST', headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            res = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        print('提交失败', exc.code, exc.read().decode('utf-8', 'replace')[:300])
        return 1
    jid = res.get('job_id')
    print('新 job', jid, ' mock=', res.get('mock'), ' degraded=', res.get('degraded'))

    jf = ROOT / '.jobs' / f'{jid}.json'
    started = time.monotonic()
    status = 'running'
    for i in range(120):                      # 最多 10 分钟
        time.sleep(5)
        if not jf.is_file():
            continue
        doc = json.loads(jf.read_text(encoding='utf-8'))
        events = doc.get('events') or []
        status = doc.get('status') or 'running'
        logs = [e['data'].get('text', '') for e in events if e.get('event') == 'log']
        last = logs[-1][:110] if logs else ''
        print(f'[{time.monotonic()-started:5.0f}s] {status:8s} 事件 {len(events):3d} | {last}', flush=True)
        if status in ('done', 'failed', 'cancelled'):
            break
    print('\n=== 结束 ===', status)
    for e in (json.loads(jf.read_text(encoding='utf-8')).get('events') or [])[-6:]:
        if e.get('event') == 'error':
            print('  error:', json.dumps(e['data'], ensure_ascii=False)[:300])
        if e.get('event') == 'done':
            print('  stats:', json.dumps(e['data'].get('stats', {}), ensure_ascii=False)[:300])
    return 0 if status == 'done' else 1


if __name__ == '__main__':
    sys.exit(main())
