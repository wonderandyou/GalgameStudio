# -*- coding: utf-8 -*-
"""直接查产物：graph.json 里的背景提示词到底带没带风格前缀。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / 'tmp' / 'agent_real_path' / 'graph.json'

if not GRAPH.is_file():
    print('找不到 graph.json:', GRAPH)
    sys.exit(1)

graph = json.loads(GRAPH.read_text(encoding='utf-8'))
style = graph.get('style')
print('style 存在:', bool(style))
if style:
    print('  palette:', style.get('palette'))
    print('  medium :', style.get('medium'))
    print('  light  :', style.get('light'))
    print('  negative:', style.get('negative'))

# mock 模式下图是占位色块，但 prompt 照样会写进 bg —— 这才是要验的东西。
all_bg = graph['nodes']
print('节点总数:', len(all_bg))
by_provider = {}
for n in all_bg:
    key = n['bg'].get('provider') or 'none'
    by_provider[key] = by_provider.get(key, 0) + 1
print('bg.provider 分布:', by_provider)

marker = '画法'
hits = [n for n in all_bg if marker in (n['bg'].get('prompt') or '')]
print('背景提示词带风格前缀:', len(hits), '/', len(all_bg))

if all_bg:
    print('样例 prompt:', (all_bg[0]['bg'].get('prompt') or '')[:220])
    print('样例 place/time/weather:', all_bg[0]['bg'].get('place'), '/',
          all_bg[0]['bg'].get('time'), '/', all_bg[0]['bg'].get('weather'))

chars = graph.get('characters') or []
print('角色数:', len(chars), '| look 非空:', sum(1 for c in chars if c.get('look')))

ok = bool(style) and len(hits) == len(all_bg) and len(all_bg) > 0
print('=' * 50)
print('结果:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
