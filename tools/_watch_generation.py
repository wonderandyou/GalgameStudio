# -*- coding: utf-8 -*-
"""盯一个正在跑的项目，直到它出图/出声或者任务结束。

不依赖 /api/jobs/{id}（那个路由不存在，job 状态只有 SSE）。
用两个信号判断：
  - graph.json 的节点数在涨（writer 在写）
  - assets/ 下出现 png / wav（art、voice 在产出）
  - 再提交一次 generate 若返回 PROJECT_BUSY 说明还在跑
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')

pid = sys.argv[1] if len(sys.argv) > 1 else 'p_6dd092b4c572440abfd34298d04aa07c'
pdir = ROOT / 'projects' / pid


def busy():
    body = json.dumps({'requirement': '探针', 'params': {'node_count': 4}}).encode('utf-8')
    req = urllib.request.Request(f'{BASE}/api/projects/{pid}/generate', data=body,
                                 method='POST', headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            return False                   # 居然又提交成功了
    except urllib.error.HTTPError as exc:
        text = exc.read().decode('utf-8', 'replace')
        return 'PROJECT_BUSY' in text      # 409 且是 busy → 还在跑
    except Exception:                      # noqa: BLE001
        return None                        # 判定不了就继续看文件


def snapshot():
    nodes = 0
    g = pdir / 'graph.json'
    if g.is_file():
        try:
            nodes = len(json.loads(g.read_text(encoding='utf-8')).get('nodes') or [])
        except Exception:                  # noqa: BLE001
            nodes = -1
    imgs = [p for p in pdir.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.webp')]
    auds = [p for p in pdir.rglob('*') if p.suffix.lower() in ('.wav', '.mp3', '.ogg')]
    logs = [p for p in pdir.rglob('*.log')]
    return nodes, imgs, auds, logs


print(f'盯项目 {pid}')
started = time.monotonic()
last = None
for i in range(90):                        # 最多 15 分钟
    nodes, imgs, auds, logs = snapshot()
    # ★ 先查日志有没有 error：任务失败时不会产出任何素材，
    #   只看文件会一直傻等（踩过：白等 890 秒才等到脚本超时）。
    failure = None
    for lp in logs:
        text = lp.read_text(encoding='utf-8', errors='replace')
        if '[error]' in text:
            failure = text.strip().splitlines()[-1]
            break
    line = f'[{time.monotonic()-started:6.0f}s] 节点 {nodes:3d}  图 {len(imgs)}  音频 {len(auds)}  日志 {len(logs)}'
    if line != last:
        print(line, flush=True)
        last = line
    if failure:
        print(f'\n[FAIL] 任务失败：{failure}')
        break
    if imgs and auds:
        print('\n[OK] 图和音频都出来了，提前结束观察')
        break
    time.sleep(10)

nodes, imgs, auds, logs = snapshot()
print(f'\n=== 最终 ===')
print(f'  节点 {nodes}  图 {len(imgs)}  音频 {len(auds)}')
for p in sorted(imgs)[:6]:
    print(f'    IMG {p.relative_to(pdir)}  {p.stat().st_size/1024:.0f} KB')
for p in sorted(auds)[:6]:
    print(f'    AUD {p.relative_to(pdir)}  {p.stat().st_size/1024:.0f} KB')
for p in sorted(logs)[:3]:
    print(f'  --- {p.name} 尾部 ---')
    print('    ' + '\n    '.join(p.read_text(encoding='utf-8', errors='replace').strip().splitlines()[-8:]))

ok = bool(imgs) or bool(auds)
print('\n结果：' + ('有产出' if ok else '暂无产出（任务可能还在写剧本，或者已经失败）'))
