# -*- coding: utf-8 -*-
"""剧情图的**最终校验**与两个共用的小工具。

和 `agents/tools.py` 里的 `_structure_errors` 有什么区别
----------------------------------------------------
两个都会查图，但服务对象完全不同：

| | `validate_graph`（本文件） | `_structure_errors`（tools） |
|---|---|---|
| 给谁看 | **导出/落盘**的最后一道门 | 给 writer 模型看 |
| 出错方式 | 第一个错就 `raise` | 一次收集全部错误 |
| 检查强度 | 含资产文件是否真的存在、语音时长是否对得上 | 只查图结构，不碰磁盘 |

为什么要分成两套：模型需要「一次看到所有问题」才能一轮改一批；而导出必须
「有任何问题就别出包」。混成一套的话，导出会带着一堆半成品资产溜出去。
"""
import json
import logging
import re
from collections import Counter
from functools import cache
from pathlib import Path, PurePosixPath
from ..contract import Graph
from ..providers.base import audio_seconds

log = logging.getLogger('gal.graph')
ENDING_KINDS = ('true', 'good', 'bad', 'alt')
# 选项条件只认两种写法：`flag`（旗标为真才显示）/ `!flag`（旗标为假才显示）。
CONDITION_RE = re.compile(r'^!?[A-Za-z_][A-Za-z0-9_]*$')


def safe_asset(root, value):
    """把图里写的资源相对路径解析成绝对路径，并挡掉越界/非法路径。

    挡四类东西（任何一条都直接抛，不给「试着打开看看」的机会）：
    · 反斜杠或盘符 —— 图里只允许 POSIX 风格相对路径，混进 Windows 路径说明有人手改了
    · 绝对路径 —— 同上
    · `..` —— 目录穿越，能把 `assets/x.png` 指到项目外面
    · 第一条路径段不是 `assets` —— 资源必须在 assets/ 下，不能是任意文件

    root 为 None 时只做格式检查、返回相对路径（测试和纯图校验时用）。
    最后还会 resolve 一次再用 is_relative_to 兜底：符号链接也能穿过前四条。
    """
    path = PurePosixPath(value)
    if not value:
        raise ValueError('资产路径是空字符串（这个字段要么填一个 assets/ 下的路径，要么留空不填）')
    if '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'assets':
        raise ValueError(f'非法资产路径: {value}')
    if root is None:
        return Path(value)
    target = (Path(root) / value).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError(f'资产越界: {value}')
    return target


def edges_from_nodes(nodes):
    """把每个节点的 choices 展开成扁平的边列表。

    图的**真实连接**只有一处来源 —— 节点的 choices。`graph['edges']` 是它的
    投影（前端画线要用）。两边必须一致，否则会出现「前端画的线」和
    「引擎实际走的路线」不一样这种最难受的 bug。

    边 ID 用 `e_{节点id}_{出口序号}` 生成：稳定、可读、不用额外计数器。
    """
    return [dict(id=f'e_{n["id"]}_{i}', **{'from': n['id']}, port=i, to=c['to'])
            for n in nodes for i, c in enumerate(n.get('choices') or [])]


def public_graph(graph):
    """去掉内部字段（agent_state）后的图，用于对外返回 / 契约校验。

    `agent_state` 是 writer 的工作状态（章节计划、节点元数据、统计），
    又大又跟 API 契约无关，不该出现在给前端的响应里，也不该参与 pydantic 校验。
    """
    return {key: value for key, value in graph.items() if key != 'agent_state'}


class GraphRejected(ValueError):
    """写入门拒绝：带着**全部**问题，调用方一次展示，不只第一条。"""

    def __init__(self, source, problems):
        self.source = source
        self.problems = list(problems)
        shown = '；'.join(self.problems[:30])
        more = f'（另有 {len(self.problems) - 30} 项）' if len(self.problems) > 30 else ''
        super().__init__(f'图结构不合法（{source}，共 {len(self.problems)} 项）：{shown}{more}')


def normalize_graph(graph):
    """D1：`nodes` 是唯一真相，`edges` 永远按 choices 重算。

    磁盘上的 / 客户端传来的 `edges` 一律丢弃，不参与合并、比较或校验。
    返回新 dict，不改入参。
    """
    nodes = graph.get('nodes') or []
    out = {key: value for key, value in graph.items() if key != 'edges'}
    out['edges'] = edges_from_nodes(nodes)
    return out


def start_id(nodes):
    """唯一的起始节点 id；没有或不唯一时返回 None。旧项目是 n1，新项目可以是别的 id。"""
    starts = [node.get('id') for node in nodes if node.get('type') == 'start']
    return starts[0] if len(starts) == 1 else None


def _edge_problems(edges, expected):
    """I1 / I4 / I5：给定的 edges 是否就是 choices 的投影、port 是否连续且不重复。"""
    problems = []

    def key(edge):
        return edge.get('from'), edge.get('port'), edge.get('to')

    actual, wanted = Counter(map(key, edges)), Counter(map(key, expected))
    if actual != wanted:
        extra, missing = sum((actual - wanted).values()), sum((wanted - actual).values())
        problems.append(f'edges 与 choices 不一致（多 {extra} 条、缺 {missing} 条）')
    edge_ids = [edge.get('id') for edge in edges]
    if len(set(edge_ids)) != len(edge_ids):
        problems.append('边 ID 重复')
    ports = {}
    for edge in edges:
        ports.setdefault(edge.get('from'), []).append(edge.get('port'))
    for source, values in ports.items():
        counts = Counter(values)
        duplicated = sorted(str(port) for port, times in counts.items() if times > 1)
        if duplicated:
            problems.append(f'{source} 的出口 port {"、".join(duplicated)} 有重复边（同一 (from, port) 只能一条）')
        if all(isinstance(port, int) for port in counts) and sorted(counts) != list(range(len(counts))):
            problems.append(f'{source} 的出口 port 不连续：{sorted(counts)}')
    return problems


def structure_problems(graph):
    """图的结构不变量 I1–I8，**返回问题列表，不抛**；空列表 = 通过。

    I1 edges == edges_from_nodes(nodes)   I2 节点 id 全局唯一（只查唯一，不查格式）
    I3 出边数：start/scene = 1，choice = 2–3（key 可 4），ending = 0
    I4 port 连续 0..n-1                   I5 同一 (from, port) 最多一条边
    I6 从起始节点可达全部节点             I7 无环
    I8 ≥1 个 ending，且 ≥1 个 end_kind == "true"
    另外：出口目标必须存在；choice 的 condition 只能写 `flag` / `!flag`。
    密度 / 深度 / 汇合（I9–I11）是生成器的职责，见 skeleton.validate_skeleton。
    """
    nodes = graph.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        return ['图里没有节点']
    problems = []
    ids = [node.get('id') for node in nodes]
    duplicated = sorted(str(nid) for nid, times in Counter(ids).items() if times > 1)
    if duplicated:
        problems.append(f'节点 ID 重复：{"、".join(duplicated)}')
    by_id = {}
    for node in nodes:
        by_id.setdefault(node.get('id'), node)
    starts = [node.get('id') for node in nodes if node.get('type') == 'start']
    if len(starts) != 1:
        problems.append(f'起始节点（type=start）须恰好 1 个，当前 {len(starts)} 个')

    expected = edges_from_nodes(nodes)
    if graph.get('edges') is not None:
        problems += _edge_problems(graph['edges'], expected)

    for node in nodes:
        nid, kind = node.get('id'), node.get('type')
        choices = node.get('choices') or []
        count = len(choices)
        if kind == 'ending':
            if count:
                problems.append(f'{nid}（ending）出边 {count} 条，应为 0')
            if node.get('end_kind') not in ENDING_KINDS:
                problems.append(f'{nid}（ending）须有合法 end_kind（true/good/bad/alt）')
        elif kind == 'choice':
            top = 4 if node.get('branch_kind') == 'key' else 3
            if not 2 <= count <= top:
                problems.append(f'{nid}（choice）出边 {count} 条，应为 2–3（key 分支可为 4）')
        elif kind in ('start', 'scene'):
            if count != 1:
                problems.append(f'{nid}（{kind}）出边 {count} 条，应为 1')
        else:
            problems.append(f'{nid} 的节点类型无效：{kind!r}')
        for choice in choices:
            if choice.get('to') not in by_id:
                problems.append(f'{nid} 的出口指向不存在的节点 {choice.get("to")!r}')
            condition = choice.get('condition')
            if condition is not None and (not isinstance(condition, str) or not CONDITION_RE.match(condition)):
                problems.append(f'{nid} 的选项条件 {condition!r} 无效，只能写 flag 或 !flag')

    endings = [node for node in nodes if node.get('type') == 'ending']
    if not endings:
        problems.append('至少需要 1 个 ending')
    elif not any(node.get('end_kind') == 'true' for node in endings):
        problems.append('至少需要 1 个 end_kind=true 的结局')

    # I6 + I7：一次迭代 DFS（显式栈，节点上千也不会爆栈）
    root = starts[0] if len(starts) == 1 else None
    if root is not None:
        seen, visiting, cycle = set(), set(), None
        stack = [(root, False)]
        while stack:
            node_id, exiting = stack.pop()
            if exiting:
                visiting.discard(node_id)
                seen.add(node_id)
            elif node_id in visiting:
                cycle = cycle or node_id
            elif node_id not in seen and node_id in by_id:
                visiting.add(node_id)
                stack.append((node_id, True))
                stack.extend((c.get('to'), False) for c in by_id[node_id].get('choices') or [])
        if cycle is not None:
            problems.append(f'图中存在环（经过 {cycle}）')
        lost = [nid for nid in by_id if nid not in seen]
        if lost:
            shown = '、'.join(str(nid) for nid in lost[:10])
            problems.append(f'{shown} 从起始节点 {root} 不可达（共 {len(lost)} 个）')
    return problems


def is_strict_graph(graph):
    """按「图的新旧」分级：带 agent_state.skeleton 的是新流程产物，严格把关。

    没有 skeleton 的是存量旧图（手工建的 / legacy 分章写的）：全量扫描里 31 个旧项目
    过不了 I1–I8，严格拒绝会让用户的手动保存 422、数据锁死。
    """
    return (graph.get('agent_state') or {}).get('skeleton') is not None


def apply_graph(graph, *, source, previous=None):
    """D3：**所有**写入路径的唯一入口。先重算 edges，再查 I1–I8。

    - 有 skeleton（新流程产物）：任何问题都整体拒绝（GraphRejected），没有 source 能跳过。
    - 无 skeleton（存量旧图）且**改坏了一张本来合法的图**：同样拒绝。
      宽松放行的本意是「不锁死本来就跑不过 I1–I8 的存量图」，
      不是「允许把好图改成坏图」—— 新建项目的图、编辑器存盘的图都属于这一类。
    - 无 skeleton 且旧图本来就有问题：只重算 edges + log.warning 列出全部 problems，放行，
      不改 edges 以外的任何数据（保住存量旧项目的可编辑性）。
    `source` 只用于日志（api.save / agent.commit / pipeline.save / checkpoint.restore …）。
    `previous` 是**磁盘上当前那份图**，只有 api.save 这类"覆盖已有图"的路径才传。
    """
    graph = normalize_graph(graph)
    problems = structure_problems(graph)
    if not problems:
        return graph
    if not is_strict_graph(graph):
        if previous is not None:
            # ★ 改坏护栏：旧图合法 → 这次提交把它改坏了 → 拒，别写盘。
            # 典型的坏事方式就是删掉起始节点的出口，之后导出直接 409。
            before = structure_problems(normalize_graph(previous))
            if not before:
                log.warning('写入被拒 source=%s（旧图合法，本次提交破坏 I1–I8）problems=%d first=%s',
                            source, len(problems), problems[0])
                raise GraphRejected(source, problems)
        log.warning('存量旧图（无 skeleton）有 %d 项结构问题，仅重算 edges 后放行 source=%s：%s',
                    len(problems), source, '；'.join(problems))
        return graph
    log.warning('写入被拒 source=%s problems=%d first=%s', source, len(problems), problems[0])
    raise GraphRejected(source, problems)


def validate_graph(graph, root=None, auto_voice=True):
    """导出前的最终校验。**有任何问题直接抛 ValueError**，返回原图便于链式调用。

    检查顺序是从便宜到昂贵，能在前面拦住就别去读磁盘：

    1. **契约**：pydantic 的 Graph 模型（字段类型、必填项）
    2~4. **结构不变量 I1–I8**：调用 `structure_problems`（和写入门同一套，
       起始节点只要求唯一的 type=start，不再写死 n1）
    5. **语音时长**：按字数估算和真实时长做交叉验证（见下面两段注释的阈值差异）
    6. **资产存在性**：背景 / 立绘 / 语音文件真的在磁盘上
    8. **角色立绘**：每个角色的 ref_image 也存在

    参数
    ----
    root: 项目根目录。给了就检查资产是否真的存在；不给只查结构。
    auto_voice: 是否要求每句台词都有配音。演示 / 草稿阶段可以关掉。
    """
    Graph.model_validate(public_graph(graph))

    # ---- 2~3. 结构不变量 I1–I8：和写入门 apply_graph 同一套（structure_problems）----
    nodes = graph['nodes']
    problems = structure_problems(graph)
    if problems:
        raise ValueError('；'.join(problems))

    # 资产检查带缓存：同一个背景会被几十个节点引用，重复读盘没必要
    @cache
    def checked_asset(value):
        target = safe_asset(root, value)
        if root and not target.is_file():
            raise ValueError(f'缺少资产: {value}')
        return target

    @cache
    def wav_duration(value):
        # 必须和合成那边（providers.base.audio_seconds）用同一套算法：
        # 流式 WAV（百炼 TTS 返回的就是）头里会写一个巨大的占位帧数，
        # 直接 getnframes() 会算出 4 万多秒的假时长，和真实时长差出几万倍，
        # 于是校验报「语音实际时长不一致」—— 产物其实全都是好的（主人实测踩到）。
        data = checked_asset(value).read_bytes()
        seconds = audio_seconds(data)
        if seconds is None:
            raise ValueError(f'语音文件读不出时长: {value}')
        return seconds

    # ---- 4~6. 逐节点检查（出口形态 + 语音时长 + 资产）----
    for n in nodes:
        paths = [n['bg']['asset']] + [c['pose'] for c in n['chars']]
        for index, line in enumerate(n['lines']):
            if auto_voice and not line['voice']:
                raise ValueError('缺少 voice')
            # 按字数估时长只对「成句」的台词成立：短句（「嗯」「能」这类）
            # 光前后静音就将近 1 秒，和字数完全不成比例 —— 实测「嗯」估 0.22 秒、
            # 实际 1.12 秒，偏差 404%，把整轮生成判死。
            # 所以：够长的句子才比相对偏差；短句只兜底「明显离谱」的绝对值。
            text_len = len(line['text'])
            estimate = text_len / 4.5
            if text_len >= 8:
                if abs(line['dur'] - estimate) / estimate >= 0.6:
                    raise ValueError('语音时长与字数偏差超过 60%')
            elif line['dur'] > 6 or line['dur'] < 0.05:
                raise ValueError(f'语音时长明显不合理：{line["dur"]:.2f} 秒配 {text_len} 个字')
            if line['voice']:
                paths.append(line['voice'])
                if root:
                    actual = wav_duration(line['voice'])
                    if abs(actual - line['dur']) >= 0.15:
                        # P2-4：报出是哪个节点的第几句，用户才知道去改哪儿
                        raise ValueError(
                            f'语音实际时长不一致：节点 {n["id"]} 第 {index + 1} 句'
                            f'（lines[{index}]，{line["voice"]}）'
                            f'期望 dur={line["dur"]:.2f} 秒，实际 {actual:.2f} 秒')
        for asset in paths:
            checked_asset(asset)


    # ---- 8. 角色立绘 ----
    for character in graph['characters']:
        ref = character.get('ref_image') or ''
        if not ref:
            # ★ 角色**可以不绑参考图**（建角色时允许只填名字）。
            #   以前这里直接 checked_asset('')，报一句 `非法资产路径: `（冒号后面是空的），
            #   用户既不知道是哪个角色、哪一项，而且整个项目永远导不出去。
            continue
        try:
            checked_asset(ref)
        except ValueError as exc:
            raise ValueError(
                f'角色 {character.get("id")}（{character.get("name") or "未命名"}）'
                f'的参考图 ref_image 有问题：{exc}') from exc
    return graph


if __name__ == '__main__':
    # 命令行用法：python -m app.engine.graph --check projects/<id>/graph.json
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', type=Path, required=True)
    parser.add_argument('--no-voice', action='store_true')
    args = parser.parse_args()
    validate_graph(json.loads(args.check.read_text('utf-8')), args.check.parent, not args.no_voice)
    print('GRAPH_VALID')
