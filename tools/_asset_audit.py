"""算账：拿真项目统计「素材有多少种」，对比「节点有多少个」。

跑：`app\\.venv\\Scripts\\python.exe tools\\_asset_audit.py <project_id>`
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assets.library import bg_key, pose_key, base_key  # noqa: E402


def main(project_id):
    graph = json.loads((ROOT / 'projects' / project_id / 'graph.json').read_text('utf-8'))
    nodes = graph['nodes']
    chars = graph.get('characters', [])

    bg_raw = Counter()
    bg_norm = defaultdict(list)
    for node in nodes:
        prompt = node['bg']['prompt']
        bg_raw[prompt] += 1
        bg_norm[bg_key(prompt)].append(node['id'])

    pose_raw = Counter()
    pose_norm = defaultdict(list)
    for node in nodes:
        for pose in node['chars']:
            pose_raw[(pose['id'], pose['mood'])] += 1
            pose_norm[pose_key(pose['id'], pose['pose'], pose['mood'])].append(node['id'])

    print(f'项目 {project_id}')
    print(f'节点 {len(nodes)}  角色 {len(chars)}')
    print()
    print(f'【背景】原始描述 {len(bg_raw)} 种 → 归一化后 {len(bg_norm)} 种')
    for key, ids in sorted(bg_norm.items(), key=lambda kv: -len(kv[1])):
        print(f'  {len(ids):>3} 节点  {key}   ← {",".join(ids[:4])}{"…" if len(ids) > 4 else ""}')
    print()
    print(f'【立绘】角色×表情 组合 {len(pose_raw)} 种 → 归一化后 {len(pose_norm)} 种')
    for key, ids in sorted(pose_norm.items(), key=lambda kv: -len(kv[1])):
        print(f'  {len(ids):>3} 节点  {key}')
    print()
    base_need = len(chars)
    print('【生图量对比】')
    print(f'  笨办法（每节点各要一张背景+一张立绘）：{len(nodes) * 2} 张')
    print(f'  素材库（背景去重 + 角色基底）：       {len(bg_norm) + base_need} 张')
    print(f'  素材库（再按表情差分）：             {len(bg_norm) + base_need + len(pose_norm)} 张')
    print(f'  节省：{len(nodes) * 2} → {len(bg_norm) + base_need + len(pose_norm)}，'
          f'约 {100 - round((len(bg_norm) + base_need + len(pose_norm)) / max(1, len(nodes) * 2) * 100)}%')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'p_63b2b758e0ab46d1bf4201629468eb36')
