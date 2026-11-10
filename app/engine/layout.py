# -*- coding: utf-8 -*-
"""剧情图的分层布局：主干往右延伸，分支在同一列上下展开，章节沿横向排开。

为什么要有这个
--------------
之前 `tools.py` 生成节点时给的是 `x = 320 + index % 12 * 320`、`y = 220 + index // 12 * 260`，
也就是「一行 12 个、然后换行往下堆」—— 几百个节点看成一张网格，完全看不出哪条是主线、
哪里分叉、分支在哪儿汇合。主人原话：「应该是往右生成的，分支等结构清晰」。

为什么章节要横向排，而不是一章一行往下堆
--------------------------------------
原来每章固定回到最左边、往下挪一行：31 章就堆成 5780 × 22358 的竖条，越写越长，
最后一屏根本看不下去（主人原话：「节点一直往画布右边延伸，不要往下排，像树枝叶一样」）。
现在改成**同一横带里并排好几章，排满 row_width 才折行** —— 整张图横着长，
主干的走向和章节的先后一眼能看出来。

为什么不能「无条件全往右」
------------------------
实测这个项目 510 节点、最长路径 444 层；全往右要 444 × 352 ≈ 15.6 万像素，
前端 fitView 有缩放下限，字会糊成色块。所以必须折行，只是折的方向从「往下」改成
「往右排满再折」—— 横着长是主，「太长就折」是兜底。

做法（Sugiyama 简化版，够用且稳定）
-----------------------------------
1. **分层**：用最长路径算每个节点在**本章内**的列号 `depth`（章入口 0，沿出边 +1）。
2. **同列排序**：按前驱的 y 重心排序，子节点尽量贴近父节点，减少连线交叉。
3. **同列展开**：一列里 n 个节点以中线为轴对称上下铺开，分支自然分到上下两侧。
4. **横向排章**：章与章接着往右放（中间留 chapter_gap），一行超过 row_width 才折到下一横带。
5. **归一化**：整体平移到非负坐标。

尺寸要和前端卡片对齐：`ui/graph-editor.js` 的 NODE_W=352 / NODE_H=216，
所以 col_w / row_h 取 356 / 224（留 4~8px 缝，别让卡片互相压边）。
"""
from collections import defaultdict


def _depth_within(nodes, edges, known):
    """最长路径分层（迭代到不动，天然防环）。"""
    ids = [n['id'] for n in nodes]
    preds = defaultdict(list)
    for edge in edges:
        src, dst = edge.get('from'), edge.get('to')
        if src in known and dst in known and src != dst:
            preds[dst].append(src)
    depth = {nid: 0 for nid in ids}
    for _ in range(len(ids) + 1):
        moved = False
        for nid in ids:
            for src in preds[nid]:
                if depth[src] + 1 > depth[nid]:
                    depth[nid] = depth[src] + 1
                    moved = True
        if not moved:
            break
    return depth, preds


def _layout_block(nodes, edges, *, col_w, row_h):
    """给一组节点算相对坐标（左上角为 0,0）；返回 ({id: (dx, dy)}, 高度)。"""
    known = {n['id'] for n in nodes}
    depth, preds = _depth_within(nodes, edges, known)
    columns = defaultdict(list)
    for nid in [n['id'] for n in nodes]:
        columns[depth[nid]].append(nid)

    order = {n['id']: i for i, n in enumerate(nodes)}
    ypos = {}
    for level in sorted(columns):
        column = columns[level]

        def barycenter(nid):
            parents = [ypos[p] for p in preds[nid] if p in ypos]
            if parents:
                return sum(parents) / len(parents)
            return order[nid] * 1000.0

        column.sort(key=barycenter)
        count = len(column)
        for slot, nid in enumerate(column):
            ypos[nid] = (slot - (count - 1) / 2.0) * row_h

    min_y = min(ypos.values()) if ypos else 0
    coords = {nid: (depth[nid] * col_w, ypos[nid] - min_y) for nid in ypos}
    height = (max((y for _, y in coords.values()), default=0) + row_h)
    return coords, height


def layered_positions(nodes, edges, *, col_w=356, row_h=224,
                      margin_x=260, margin_y=280, chapter_gap=300,
                      row_width=24000):
    """返回 {node_id: {'x': int, 'y': int}}。

    传了 chapter_of（{node_id: chapter_key}）就按章分段；没传就整图一段。
    """
    return _positions(nodes, edges, chapter_of=None, col_w=col_w, row_h=row_h,
                      margin_x=margin_x, margin_y=margin_y, chapter_gap=chapter_gap,
                      row_width=row_width)


def _positions(nodes, edges, chapter_of=None, *, col_w, row_h,
               margin_x, margin_y, chapter_gap, row_width):
    if not nodes:
        return {}

    groups = defaultdict(list)
    if chapter_of:
        for node in nodes:
            groups[chapter_of.get(node['id'])].append(node)
    else:
        groups[None] = list(nodes)

    result = {}
    cursor_x = 0
    cursor_y = 0
    row_height = 0
    for key in sorted(groups, key=lambda k: (k is None, str(k))):
        block = groups[key]
        block_ids = {n['id'] for n in block}
        block_edges = [e for e in (edges or [])
                       if e.get('from') in block_ids and e.get('to') in block_ids]
        coords, height = _layout_block(block, block_edges, col_w=col_w, row_h=row_h)
        width = (max((dx for dx, _ in coords.values()), default=0) + col_w) if coords else 0
        # ★ 章与章「接着往右排」，排满一行才折下去 —— 原来是无条件回到最左边、
        #   往下挪一行，31 章就堆成一条 2 万像素高的竖条（主人原话「不要往下排」）。
        #   现在整张图是横着长的：同一条横带上并排好几章，读起来像枝干往右伸。
        if cursor_x and cursor_x + width > row_width:
            cursor_x = 0
            cursor_y += row_height + chapter_gap
            row_height = 0
        for nid, (dx, dy) in coords.items():
            result[nid] = {'x': int(margin_x + cursor_x + dx),
                           'y': int(margin_y + cursor_y + dy)}
        cursor_x += width + chapter_gap
        row_height = max(row_height, height)

    return result


def apply_layout(graph, chapter_of=None, **kwargs):
    """把布局写回 graph['nodes'] 的 pos。返回改动的节点数。"""
    positions = _positions(graph.get('nodes') or [], graph.get('edges') or [],
                           chapter_of=chapter_of, col_w=kwargs.get('col_w', 356),
                           row_h=kwargs.get('row_h', 224),
                           margin_x=kwargs.get('margin_x', 260),
                           margin_y=kwargs.get('margin_y', 280),
                           chapter_gap=kwargs.get('chapter_gap', 300),
                           row_width=kwargs.get('row_width', 24000))
    changed = 0
    for node in graph.get('nodes') or []:
        target = positions.get(node.get('id'))
        if target and node.get('pos') != target:
            node['pos'] = target
            changed += 1
    return changed
