"""在接触项目文件前验证完整骨架，再将拓扑投影为前端已有的剧情图契约。"""
import re
from collections import Counter, defaultdict, deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .graph import edges_from_nodes

BACKGROUND_PLACEHOLDER = 'assets/bg/待生成-骨架.png'
ENDING_KINDS = ('true', 'good', 'bad', 'alt')
# 一个出口能去哪：切下一章，或者切某种结局。
# 分成这两类是为了让玩家和开发者在**出口上**就能看懂意图，
# 而不是顺藤摸瓜跳三层才知道「这个选项会直接进坏结局」。
EXIT_KINDS = ('chapter',) + ENDING_KINDS
BRANCH_KINDS = ('key', 'flavor')
NODE_TYPES = ('start', 'scene', 'choice', 'ending')


@dataclass
class SkeletonNode:
    """用临时引用保存出边，允许分批接收骨架后统一分配正式节点编号。"""

    ref: str
    type: str
    title: str
    act: int = 1
    chapter: str = ''
    to: list[str] = field(default_factory=list)
    labels: list[str] | None = None
    # 每个出口的去向意图，和 to 一一对应：'chapter' 表示进下一章，
    # 'true'/'good'/'bad'/'alt' 表示切某种结局。留空时按目标节点自动推断。
    exit_kinds: list[str] | None = None
    branch_kind: str | None = None
    end_kind: str | None = None
    summary: str = ''
    # 和 to 一一对应：选了这个出口就把同名旗标置 True（'' 表示不改），落图时变成 choice.effects。
    effects: list[str] | None = None


@dataclass
class Skeleton:
    """把全图连接和简要剧情放在同一份记录中，供验证、填充和重启复用。"""

    nodes: list[SkeletonNode]
    logline: str = ''
    acts: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        """统一解析模型输出和已落盘的数据，并给格式错误标出条目位置。"""
        if not isinstance(data, dict) or not isinstance(data.get('nodes'), list):
            raise ValueError('骨架必须是包含 nodes 列表的对象')
        nodes = []
        for index, entry in enumerate(data['nodes'], 1):
            if not isinstance(entry, dict):
                raise ValueError(f'骨架第 {index} 个节点必须是对象')
            try:
                nodes.append(SkeletonNode(**entry))
            except TypeError as exc:
                ref = entry.get('ref', f'第 {index} 个节点')
                raise ValueError(f'{ref} 的骨架字段无效：{exc}') from exc
        try:
            return cls(**dict(data, nodes=nodes))
        except TypeError as exc:
            raise ValueError(f'骨架字段无效：{exc}') from exc

    def to_dict(self):
        """生成独立的 JSON 数据，避免后续填充影响保留的骨架记录。"""
        data = asdict(self)
        for node in data['nodes']:
            for optional in ('labels', 'exit_kinds', 'branch_kind', 'end_kind', 'effects'):
                if node[optional] is None:
                    del node[optional]
        return data

    def to_graph_stub(self, characters, project):
        """把已校验的骨架变成可展示的全图，给后续阶段保留所有出口与节点编号。"""
        node_ids = {node.ref: f'n{index}' for index, node in enumerate(self.nodes, 1)}
        by_ref = {node.ref: node for node in self.nodes}
        positions = skeleton_layout(self)
        stamp = datetime.now(timezone.utc).isoformat()
        line = '这一幕的台词正在准备'
        slots = {0: (), 1: ('center',), 2: ('left', 'right'), 3: ('left', 'center', 'right')}
        poses = [dict(id=character['id'], pose=character['ref_image'], pos=pos,
                      mood='平静', stance='stand')
                 for character, pos in zip(characters, slots[min(len(characters), 3)])]
        nodes, flags = [], {}
        for entry in self.nodes:
            node = dict(id=node_ids[entry.ref], type=entry.type, title=entry.title,
                        status='pending', end_kind=entry.end_kind, branch_kind=entry.branch_kind,
                        bg=dict(asset=BACKGROUND_PLACEHOLDER, prompt=entry.summary or entry.title,
                                seed=0, provider='pending'),
                        chars=deepcopy(poses), lines=[dict(who='', text=line, voice=None, dur=len(line) / 4.5)],
                        pos=positions[entry.ref], gen=dict(model='skeleton', at=stamp, cost=0.0))
            if entry.to:
                labels = entry.labels or ['继续']
                effects = entry.effects or []
                node['choices'] = []
                for index, (label, target) in enumerate(zip(labels, entry.to)):
                    target_node = by_ref[target]
                    # ★ 最终去向**一律以目标节点为准**：
                    #   目标是 ending 就用它自己的 end_kind，否则就是 chapter。
                    #   出口上声明的 exit_kinds 只当模型的规划意图，可能猜错，
                    #   不能让它污染图数据（否则会出现「标 bad 实际 true」）。
                    kind = target_node.end_kind if target_node.type == 'ending' else 'chapter'
                    choice = dict(label=label, to=node_ids[target], condition=None, kind=kind)
                    if index < len(effects) and effects[index]:
                        choice['effects'] = {effects[index]: True}
                        flags[effects[index]] = None
                    node['choices'].append(choice)
            nodes.append(node)
        graph = dict(version=1, project=deepcopy(project), characters=deepcopy(characters),
                     nodes=nodes, edges=edges_from_nodes(nodes), agent_state={})
        if flags:
            graph['flags'] = {name: dict(type='bool', default=False) for name in flags}
        return graph


def _validate_node(node):
    """先约束单个节点的数据形状，让遍历只处理明确的引用和出口。"""
    if not isinstance(node, SkeletonNode) or not isinstance(node.ref, str) or not node.ref.strip():
        raise ValueError('骨架节点必须有非空的字符串 ref')
    ref = node.ref
    if node.type not in NODE_TYPES:
        raise ValueError(f'{ref} 的节点类型无效：{node.type}')
    if not isinstance(node.title, str) or not node.title.strip() or len(node.title) > 8:
        raise ValueError(f'{ref} 的标题必须为 1–8 个字')
    if type(node.act) is not int or node.act < 1:
        raise ValueError(f'{ref} 的幕编号必须是从 1 开始的整数')
    if not isinstance(node.chapter, str) or not isinstance(node.summary, str):
        raise ValueError(f'{ref} 的章节标记和剧情简述必须是字符串')
    if not isinstance(node.to, list) or any(not isinstance(target, str) for target in node.to):
        raise ValueError(f'{ref} 的出口 to 必须是引用列表')
    # 不再拦「多个出口指向同一节点」：cosmetic 选项合法，I5 的键是 (from, port)。
    if node.type == 'ending':
        if node.to or node.end_kind not in ENDING_KINDS:
            raise ValueError(f'{ref} 的 ending 必须有合法 end_kind 且不能有出口')
    elif not node.to:
        raise ValueError(f'{ref} 是断头节点，非 ending 必须有出口')
    elif node.end_kind is not None:
        raise ValueError(f'{ref} 的 end_kind 只适用于 ending')

    if node.type == 'choice':
        if node.branch_kind not in BRANCH_KINDS:
            raise ValueError(f'{ref} 的 choice 必须指定 key 或 flavor')
        # 与写入门 I3 同口径：choice 2–3 个出口，key 分支可为 4。
        if not 2 <= len(node.to) <= (4 if node.branch_kind == 'key' else 3):
            raise ValueError(f'{ref} 的 choice 必须有 2–3 个出口（key 可为 4），现有 {len(node.to)} 个')
    elif node.branch_kind is not None:
        raise ValueError(f'{ref} 的 branch_kind 只适用于 choice')
    elif node.type in ('start', 'scene') and len(node.to) != 1:
        raise ValueError(f'{ref} 的 start/scene 必须恰有一个出口')

    if node.effects is not None and (
            not isinstance(node.effects, list) or len(node.effects) != len(node.to)
            or any(not isinstance(item, str) or item and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item)
                   for item in node.effects)):
        raise ValueError(f'{ref} 的 effects 必须与 to 逐项对应（旗标名或空串）')

    labels = node.labels
    if labels is not None and not isinstance(labels, list):
        raise ValueError(f'{ref} 的出口标签必须是列表')
    labels = labels or []
    if (node.type == 'choice' or labels) and (
        len(labels) != len(node.to) or any(not isinstance(label, str) or not label.strip() for label in labels)
    ):
        raise ValueError(f'{ref} 的出口标签必须与 to 逐项对应且非空')


def _topological_order(by_ref):
    """一次检查所有引用与环路，包括入口无法走到的那部分节点。"""
    indegrees = dict.fromkeys(by_ref, 0)
    for node in by_ref.values():
        for target in node.to:
            if target not in indegrees:
                raise ValueError(f'{node.ref} 的出口指向不存在的引用 {target}')
            indegrees[target] += 1

    order = [ref for ref, indegree in indegrees.items() if indegree == 0]
    for ref in order:
        for target in by_ref[ref].to:
            indegrees[target] -= 1
            if indegrees[target] == 0:
                order.append(target)
    if len(order) != len(by_ref):
        blocked = next(ref for ref, indegree in indegrees.items() if indegree)
        raise ValueError(f'骨架有环，{blocked} 无法完成拓扑排序')
    return order


def _branch_depth(by_ref, order, limit=None, warnings=None):
    """用必经汇合点计算分支的实际开启区间，避免把已汇合的分支计入后续深度。

    `warnings` 给了就**不抛错**，只把超限情况记进去 ——
    分支嵌套超了是「玩起来会晕」的质量问题，不是断头那种硬伤，
    不该让整轮生成报废（主人实测：k056 深度 3 超上限 2，直接三次重试全挂）。
    """
    size = len(order)
    indices = {ref: index for index, ref in enumerate(order)}
    successors = [[indices[target] for target in by_ref[ref].to] for ref in order]
    required_after = [0] * size
    joins = {}
    for index in range(size - 1, -1, -1):
        targets = successors[index]
        common = 0
        if targets:
            common = required_after[targets[0]]
            for target in targets[1:]:
                common &= required_after[target]
        node = by_ref[order[index]]
        if node.type == 'choice':
            if common:
                # 拓扑序中最早的公共必经点即首次保证汇合的位置。
                joins[index] = (common & -common).bit_length() - 1
            elif node.branch_kind == 'flavor':
                raise ValueError(f'{node.ref} 的 flavor 分支不能保证所有路径汇合')
            else:
                # 没有汇合点的必要分支，直到结局都仍处于开启状态。
                joins[index] = size
        required_after[index] = common | (1 << index)

    maximum = 0
    for endpoint in reversed(joins):
        # 深度只会在 choice 增加。对于到达该点的路径，拓扑序在它前面的必经
        # 汇合点一定已经经过，只需计算仍然开启的分支数量的最长路径。
        active = [0] * (endpoint + 1)
        for index in range(endpoint):
            count = active[index] + (joins.get(index, -1) > endpoint)
            for target in successors[index]:
                if target <= endpoint and count > active[target]:
                    active[target] = count
        count = active[endpoint] + 1
        if limit is not None and count > limit:
            message = f'{order[endpoint]} 的分支深度 {count} 超过上限 {limit}'
            if warnings is None:
                raise ValueError(message)
            warnings.append(message + '（不阻塞：分支嵌套深一点只是玩起来乱，结构是完整的）')
        maximum = max(maximum, count)
    return maximum


def _layers(start, by_ref):
    """以最短路径为层号，同一遍遍历也能找出不属于入口路线的节点。"""
    layers = {start: 0}
    queue = deque([start])
    while queue:
        ref = queue.popleft()
        for target in by_ref[ref].to:
            if target not in layers:
                layers[target] = layers[ref] + 1
                queue.append(target)
    return layers


def check_rejoin(by_ref, choice, indegree, rejoin_max):
    """§3.3：choice 的每个出口须在 rejoin_max 步内走到入度 ≥2 的节点或 ending，途中不再分叉。"""
    problems = []
    for index, target in enumerate(choice.to, 1):
        current, steps = target, 0
        while steps < rejoin_max:
            node = by_ref.get(current)
            if node is None:
                problems.append(f'{choice.ref} 第 {index} 个出口指向不存在的 {current}')
                break
            if node.type == 'ending' or indegree[current] >= 2 and steps > 0:
                break
            if len(node.to) != 1:
                problems.append(f'{choice.ref} 第 {index} 个出口在 {current} 处再度分叉，没有先汇合')
                break
            current = node.to[0]
            steps += 1
        else:
            problems.append(f'{choice.ref} 第 {index} 个出口走 {rejoin_max} 步仍未汇合')
    return problems


def generator_problems(skeleton, *, by_ref, order, depth):
    """I9–I11：选择点密度、分叉深度、侧枝汇合。只对新模板（template_version ≥ 2）强制。

    写入门 apply_graph 不查这三条（旧项目只要结构合法就能存）；新生成的骨架必须满足。
    """
    meta = skeleton.meta
    density = meta.get('choice_density', 0.06)
    rejoin_max = meta.get('rejoin_max', 10)
    depth_max = min(depth, meta.get('depth_max', depth))
    nodes = skeleton.nodes
    problems = []
    choices = [node for node in nodes if node.type == 'choice']
    # 小图里最终分歧本身就超过密度上限，允许至少一个选择点。
    if len(choices) > max(1, len(nodes) * density * 1.5):
        problems.append(f'I9 选择点 {len(choices)} 个 / {len(nodes)} 节点，超过密度 {density} × 1.5')
    try:
        _branch_depth(by_ref, order, depth_max)
    except ValueError as exc:
        problems.append(f'I10 {exc}')
    indegree = Counter(target for node in nodes for target in node.to)
    for choice in choices:
        problems.extend(f'I11 {problem}' for problem in check_rejoin(by_ref, choice, indegree, rejoin_max))
    return problems


def validate_skeleton(skeleton, *, target_nodes=None, depth=None):
    """一次校验内存中的全图，失败时返回可直接交给编剧模型定位的错误。"""
    if isinstance(skeleton, dict):
        skeleton = Skeleton.from_dict(skeleton)
    if not isinstance(skeleton, Skeleton) or not isinstance(skeleton.nodes, list) or not skeleton.nodes:
        raise ValueError('骨架必须有节点，且第一个节点必须是唯一的 start')
    if (not isinstance(skeleton.logline, str) or not isinstance(skeleton.meta, dict)
            or not isinstance(skeleton.acts, list) or any(not isinstance(act, dict) for act in skeleton.acts)):
        raise ValueError('骨架的 logline、acts 或 meta 格式无效')

    nodes = skeleton.nodes
    target_nodes = skeleton.meta.get('target_nodes', len(nodes)) if target_nodes is None else target_nodes
    depth = skeleton.meta.get('depth', 3) if depth is None else depth
    if type(target_nodes) is not int or target_nodes < 1:
        raise ValueError('目标节点数必须是正整数')
    if type(depth) is not int or depth < 0:
        raise ValueError('分支深度必须是非负整数')
    # 节点数只做「宽底线 + 软提示」，不再精确卡 ±10%。
    # 为什么：模型没法精确命中数字，硬卡会让整轮白跑 —— 主人实测目标 100、
    # 模型给了 150（结构完全正常），直接报错，台词和美术全废。
    # 真正该硬的指标是**结构**：断头 / 环 / 不可达 / 结局 / 分支深度，
    # 这几个下面一条不少地查。数量少几个多几个，玩家根本感觉不到。
    if len(nodes) < target_nodes * 0.5:
        raise ValueError(
            f'骨架只有 {len(nodes)} 个节点，不到目标 {target_nodes} 的一半 —— '
            '这说明没有按 refs 把剧情展开，请补全每批节点')
    if not target_nodes * 0.7 <= len(nodes) <= target_nodes * 1.6:
        skeleton.meta['nodes_warning'] = (
            f'骨架实际 {len(nodes)} 个节点，与目标 {target_nodes} 相差较多'
            '（不阻塞：结构完整就能玩，数量只是参考）')

    by_ref = {}
    for node in nodes:
        _validate_node(node)
        if node.ref in by_ref:
            raise ValueError(f'骨架节点 ref 重复：{node.ref}')
        by_ref[node.ref] = node
    if nodes[0].type != 'start' or sum(node.type == 'start' for node in nodes) != 1:
        raise ValueError('骨架必须有唯一的 start，且位于列表第一项')
    if not any(node.type == 'ending' and node.end_kind == 'true' for node in nodes):
        raise ValueError('骨架必须有至少一个 true 结局')

    order = _topological_order(by_ref)
    # 出口去向：每个出口必须能说清「切下一章」还是「切哪种结局」，
    # 而且声明必须和目标节点对得上（主人 2026-11-10 要求）。
    # 没声明时按目标节点自动推断，所以旧骨架不用改也能过。
    for node in nodes:
        kinds = node.exit_kinds or []
        if kinds and len(kinds) != len(node.to):
            raise ValueError(
                f'{node.ref} 的 exit_kinds 有 {len(kinds)} 项，但出口有 {len(node.to)} 个')
        for index, target in enumerate(node.to):
            target_node = by_ref[target]
            inferred = target_node.end_kind if target_node.type == 'ending' else 'chapter'
            declared = kinds[index] if index < len(kinds) else None
            kind = declared or inferred
            if kind not in EXIT_KINDS:
                raise ValueError(
                    f'{node.ref} 第 {index + 1} 个出口的去向 {kind!r} 无效，'
                    f'只能是 {EXIT_KINDS}')
            if kind == 'chapter':
                if target_node.type == 'ending':
                    # 声明和目标不符：**以目标节点为准**，不阻塞生成。
                    # 目标 ending 自己带 end_kind，那就是真相；
                    # 源节点上的 exit_kinds 只是模型的猜测，猜错很正常
                    # （实测：k120 标 bad，但 k180 自己写的是 end_kind=true）。
                    warnings = skeleton.meta.setdefault('structure_warnings', [])
                    warnings.append(
                        f'{node.ref} 第 {index + 1} 个出口标成「下一章」，'
                        f'但目标是 ending（{target} 的 end_kind={target_node.end_kind}），'
                        '已按目标的结局类型纠正')
                elif target_node.chapter != node.chapter and node.type != 'choice':
                    # 跨章节最好经过 choice：玩家在切换点上该有「就此收手 → 坏结局 /
                    # 继续追查 → 下一章」的选择。这里只记提示不阻塞 ——
                    # 离线兜底图按序号切章，硬拦会把兜底打挂；模型那边由提示词要求。
                    warnings = skeleton.meta.setdefault('structure_warnings', [])
                    warnings.append(
                        f'{node.ref}（{node.type}）直接跨到下一章 {target_node.chapter!r}，'
                        '建议改成在章末放一个 choice，让切换由玩家决定')
            else:
                if target_node.type != 'ending':
                    warnings = skeleton.meta.setdefault('structure_warnings', [])
                    warnings.append(
                        f'{node.ref} 第 {index + 1} 个出口标成结局 {kind}，'
                        f'但目标 {target} 是 {target_node.type}，已按「下一章」纠正')
                elif target_node.end_kind != kind:
                    # 同一个道理：出口上的猜测服从目标节点自己声明的 end_kind。
                    warnings = skeleton.meta.setdefault('structure_warnings', [])
                    warnings.append(
                        f'{node.ref} 第 {index + 1} 个出口标成 {kind}，'
                        f'但 {target} 的 end_kind 是 {target_node.end_kind}，'
                        '已按目标的结局类型纠正')
    reached = _layers(nodes[0].ref, by_ref)
    if len(reached) != len(nodes):
        ref = next(ref for ref in by_ref if ref not in reached)
        raise ValueError(f'{ref} 从起点不可达')
    if skeleton.meta.get('template_version', 1) >= 2:
        if problems := generator_problems(skeleton, by_ref=by_ref, order=order, depth=depth):
            raise ValueError('骨架不满足生成器约束：' + '；'.join(problems))
    # 旧模型骨架可能已经在警告后冻结落盘，续跑仍需接受它们。
    warnings = skeleton.meta.get('structure_warnings', [])
    _branch_depth(by_ref, order, depth, warnings=warnings if skeleton.meta.get('source') == 'model' else None)
    if warnings:
        skeleton.meta['structure_warnings'] = list(dict.fromkeys(warnings))
    else:
        skeleton.meta.pop('structure_warnings', None)
    return skeleton


def skeleton_branch_depth(skeleton):
    """按校验采用的汇合规则报告实际层数，避免进度显示与允许的深度口径不一致。"""
    by_ref = {node.ref: node for node in skeleton.nodes}
    return _branch_depth(by_ref, _topological_order(by_ref))


def skeleton_layout(skeleton):
    """按入口最短距离排同层节点，为超过五个节点的层留出独立的第二列。"""
    nodes = skeleton.nodes
    layers = _layers(nodes[0].ref, {node.ref: node for node in nodes})
    counts = defaultdict(int)
    positions = {}
    for node in nodes:
        layer = layers[node.ref]
        index = counts[layer]
        counts[layer] += 1
        # 溢出列放在本层区间内，下一层的整列仍使用原本的横坐标。
        column_offset = 160 if index >= 5 else 0
        row = index - 5 if index >= 5 else index
        positions[node.ref] = dict(x=320 * layer + column_offset, y=320 + row * 220)
    return positions
