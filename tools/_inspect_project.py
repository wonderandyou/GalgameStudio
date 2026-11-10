# -*- coding: utf-8 -*-
"""查一个项目的收尾质量：布局 / 孤儿 / 结局 / 断边 / 章结构。"""
import json
import sys
from collections import Counter

import httpx

PID = sys.argv[1] if len(sys.argv) > 1 else 'p_63b2b758e0ab46d1bf4201629468eb36'
g = httpx.get(f'http://127.0.0.1:8790/api/projects/{PID}/graph', timeout=60).json()
nodes = g.get('nodes') or []
edges = g.get('edges') or []
chapters = g.get('chapters') or []

print('节点', len(nodes), '边', len(edges), '章', len(chapters))
print('类型', dict(Counter(n.get('type') for n in nodes)))
print('结局', dict(Counter(n.get('end_kind') for n in nodes if n.get('type') == 'ending')))

if nodes:
    xs = [n['pos']['x'] for n in nodes]
    ys = [n['pos']['y'] for n in nodes]
    print(f'布局 x {min(xs)}→{max(xs)}（宽 {max(xs)-min(xs)}） y {min(ys)}→{max(ys)}（高 {max(ys)-min(ys)}）')

ids = {n['id'] for n in nodes}
bad = [e for e in edges if e.get('to') not in ids or e.get('from') not in ids]
print('断边', len(bad))
incoming = {e['to'] for e in edges}
orphans = [n['id'] for n in nodes if n.get('type') != 'start' and n['id'] not in incoming]
print('孤儿', len(orphans), orphans[:8])
print('有 choices 的节点', len([n for n in nodes if n.get('choices')]))

for ch in chapters:
    print(f"  {ch['id']} {ch.get('title')} | 预算 {ch.get('node_budget')} | 节点 {len(ch.get('node_ids') or [])} | {ch.get('status')}")
