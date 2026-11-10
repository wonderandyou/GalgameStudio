# -*- coding: utf-8 -*-
"""诊断「剧情图有环」：把环上的节点路径打出来。

不做推测：用 DFS 在真实 graph.json 上找环，打印环路径，
再看那些边的 type（是 choice 分支、跳转、还是回指）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
PID = sys.argv[1] if len(sys.argv) > 1 else 'p_440aa7ade97043c8b59f53228c7969f9'
PDIR = ROOT / 'projects' / PID


def main():
    gf = PDIR / 'graph.json'
    if not gf.is_file():
        print(f'[FAIL] 没有 {gf}')
        return 2
    g = json.loads(gf.read_text(encoding='utf-8'))
    nodes = g.get('nodes') or []
    edges = g.get('edges') or []
    ids = {n.get('id') for n in nodes}
    print(f'节点 {len(nodes)}  边 {len(edges)}')

    # 建邻接表
    adj = {}
    for e in edges:
        a, b = e.get('from'), e.get('to')
        if a in ids and b in ids:
            adj.setdefault(a, []).append(b)
        else:
            print(f'  ⚠ 边指向不存在的节点：{a} -> {b}')

    # DFS 找环
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in ids}
    stack_path = []
    cycles = []

    def dfs(u):
        color[u] = GRAY
        stack_path.append(u)
        for v in adj.get(u, []):
            if color.get(v) == GRAY:
                idx = stack_path.index(v) if v in stack_path else 0
                cycles.append(stack_path[idx:] + [v])
            elif color.get(v) == WHITE:
                dfs(v)
        stack_path.pop()
        color[u] = BLACK

    for nid in ids:
        if color[nid] == WHITE:
            dfs(nid)

    if not cycles:
        print('\n没有环（图和校验结论不一致，要查校验的 scope）')
        return 0

    print(f'\n找到 {len(cycles)} 个环：')
    byid = {n.get('id'): n for n in nodes}
    for i, cyc in enumerate(cycles[:5], 1):
        print(f'\n  环 {i}（{len(cyc)-1} 个节点）:')
        for nid in cyc:
            n = byid.get(nid) or {}
            text = (n.get('text') or n.get('title') or '')[:28]
            lines = n.get('lines') or []
            first = (lines[0].get('text') or '')[:20] if lines else ''
            out = [e.get('to') for e in edges if e.get('from') == nid]
            print(f'    {nid:12s} type={str(n.get("type")):8s} 出边={len(out)}  {text or first}')
        print('    边明细：')
        for nid in cyc[:-1]:
            for e in edges:
                if e.get('from') == nid and e.get('to') in cyc:
                    print(f'      {nid} -> {e.get("to")}  label={e.get("label")!r}')

    # 有多少节点在环里
    in_cycle = set()
    for cyc in cycles:
        in_cycle.update(cyc)
    print(f'\n涉及环的节点共 {len(in_cycle)} 个（占总节点 {len(ids)} 的 {len(in_cycle)/max(1,len(ids))*100:.0f}%）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
