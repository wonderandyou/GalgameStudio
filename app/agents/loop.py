"""编排整图骨架与对白填充，保留旧编剧流程以便续写历史任务。"""

import asyncio
import json
import time
from collections import defaultdict, deque

from ..assets.library import mood_of, pose_key, stance_of
from ..engine.graph import validate_graph
from ..engine.skeleton import Skeleton, skeleton_branch_depth, validate_skeleton
from ..providers.base import ProviderError
from . import registry
from .fill import mock_fill
from .mock_dialogue import mock_lines
from .outline import FALLBACK_WARNING_PREFIX, generate_skeleton, writer_unavailable
from .tool_schema import WRITER_TOOLS


MAX_CHAPTER_CALLS = 80
MAX_PLAN_CALLS = 12
MAX_REPAIRS = 2

#: 模拟模式里轮换的姿态与表情。让 mock 章节也有立绘差分，
#: 否则「一个角色只有一张脸」这个能力在离线演示里永远看不到效果。
_MOCK_STANCES = ('站', '坐下', '靠着', '走', '站')
_MOCK_MOODS = ('平静', '微微一笑', '低头', '难过', '惊讶')

# ★ 2026-11-07 主人定：**不按工具调用次数设上限**。
#   理由：有人写上万节点的项目，按次数算根本没法给一个合理值 —— 给 80 是崩，
#   给 3000 也可能崩，还给不出解释。所以改成按「时间 + 有没有进展」防卡死：
#     · MAX_STAGE_SECONDS —— 单个写图阶段最多跑多久（默认 6 小时，够 488 节点）
#     · STALL_ROUNDS      —— 连续这么多轮「节点数 + 边数」都没变化 → 判卡死
#   停滞检测比次数上限准：合法的取章节/连边/校验不会连续几十轮毫无产出。
MAX_STAGE_SECONDS = 6 * 3600
STALL_ROUNDS = 24
STRUCT_CHECK_INTERVAL = 10
STRUCT_STALL_CHECKS = 3
WRITER_BRANCH_VERSION = 2
# 填充最多rounds轮补齐。模型可能只填了一批里的一部分（多给/少给 ref 都会被容忍），
# 所以不能只遍历一遍 pending —— 一次性遍历漏掉的节点永远补不上（实测踩到）。
FILL_ROUNDS = 3
FILL_CONTENT_ERRORS = {
    'FILL_INVALID', 'INVALID_FILL', 'INVALID_DIALOGUE', 'INVALID_MOOD',
    'EMPTY_PROMPT', 'UNKNOWN_CHARACTER', 'SKELETON_FROZEN',
}
PLAN_TOOLS = {'project_brief', 'get_structure', 'plan_structure',
              'list_characters', 'upsert_character',
              # 续写判定用的两个：先看证据，再落决定
              'resume_evidence', 'set_resume_plan'}
CHAPTER_TOOLS = {'project_brief', 'get_chapter', 'list_characters',
                 'upsert_character', 'write_nodes', 'connect_nodes',
                 'validate_graph', 'finish_chapter'}


def _encode(value):
    """把工具返回压成 JSON 字符串，塞进 tool 消息给模型看。"""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _split_evenly(total, groups):
    """把 total 尽量平均分成 count 份，余数从前往后补。"""
    base, extra = divmod(total, groups)
    return [base + (index < extra) for index in range(groups)]


def _act_budgets(total):
    """把全书节点预算切成「每幕一组章节预算」。"""
    count = 1 if total <= 18 else (total - 1 + 15) // 16
    chapters = _split_evenly(total - 1, count)
    act_count = min(len(chapters), 6, max(3, (len(chapters) + 7) // 8))
    acts = []
    offset = 0
    for count in _split_evenly(len(chapters), act_count):
        acts.append(chapters[offset:offset + count])
        offset += count
    return acts


def _split_branches(chapters, total, capacity=None):
    """把 total 个分支摊到各章，每章受节点预算约束（一个分支至少吃 3 个节点）。

    分支分两类之后（key 必要 / flavor 气氛），要分两次调用：
    先分 key，再用「剩余容量」分 flavor —— 否则两类各自按全量容量算，
    加起来必然超预算。
    返回 (每章个数, 塞不下的剩余)。
    """
    budgets = [max(0, int(chapter.get('node_budget') or 0)) for chapter in chapters]
    if capacity is None:
        capacity = [max(0, (budget - 2) // 3) for budget in budgets]
    counts = [0] * len(budgets)
    remaining = max(0, int(total))
    while remaining > 0:
        room = [index for index in range(len(counts)) if counts[index] < capacity[index]]
        if not room:
            break
        # 谁的预算大先给谁（按「预算 / 已分+1」摊平，避免全堆在前几章）
        index = max(room, key=lambda i: budgets[i] / (counts[i] + 1))
        counts[index] += 1
        remaining -= 1
    return counts, remaining


def _choice_limits(total):
    """全书 choice 数量的上下限（占节点总数的 8%~15%）。"""
    minimum = (8 * total + 99) // 100
    maximum = 15 * total // 100
    if minimum > maximum:
        # For tiny graphs the percentage range may contain no integer count.
        closest = max(1, (total + 5) // 10)
        return closest, closest
    return minimum, maximum


def _chapter_choice_counts(chapters, total, depth):
    """把全书 choice 数量摊到各章，受每章节点预算约束。"""
    if not depth:
        return [0] * len(chapters)

    def slots(budget, choices, last):
        """算「写 n 个选择节点需要多少节点预算」。"""
        terminal = min(4, budget) if last else 0
        diamonds = choices - int(last)
        groups = (diamonds + depth - 1) // depth
        return 2 * (diamonds + groups) + terminal

    budgets = [chapter['node_budget'] for chapter in chapters]
    counts = [0] * len(budgets)
    capacities = [max((n for n in range(1, (budget - 1) // 2 + 1)
                       if slots(budget, n, i == len(budgets) - 1) <= budget), default=0)
                  for i, budget in enumerate(budgets)]
    minimum, maximum = _choice_limits(total)
    if sum(capacities) < minimum:
        raise ProviderError('WRITER_BRANCH_PLAN', f'当前章节预算无法安排至少 {minimum} 个选择节点', 'writer.loop')
    target = min(max(minimum, (total + 5) // 10), maximum, sum(capacities))
    # Reserve a final decision when it fits, then distribute across chapter sizes.
    counts[-1] = int(capacities[-1] > 0)
    for _ in range(target - sum(counts)):
        index = max((i for i, cap in enumerate(capacities) if counts[i] < cap),
                    key=lambda i: target * budgets[i] - counts[i] * (total - 1))
        counts[index] += 1
    return counts


def _mock_layout(budget, branches, depth, last):
    """List (type, target indexes, end kind); None targets advance one scene."""
    if last and branches:
        endings = ('true', 'bad', 'alt')[:min(3, budget - 1)]
        branches -= 1
    else:
        endings = ('true',) if last else ()
    groups = [min(depth, branches - offset) for offset in range(0, branches, max(1, depth))]
    terminal_slots = len(endings) + int(len(endings) > 1)
    filler = budget - sum(2 * (size + 1) for size in groups) - terminal_slots
    gaps = _split_evenly(filler, len(groups) + 1)
    scene = ('scene', None, None)
    layout = []
    for gap, size in zip(gaps, groups):
        layout.extend([scene] * gap)
        start = len(layout)
        merge = start + 2 * size + 1
        for i in range(size):
            main = start + i + 1 if i < size - 1 else merge - 1
            layout.append(('choice', (main, start + size + i), None))
        layout.extend([('scene', (merge,), None)] * (size + 1))
        layout.append(scene)
    layout.extend([scene] * gaps[-1])
    if len(endings) > 1:
        first = len(layout) + 1
        layout.append(('choice', tuple(range(first, first + len(endings))), None))
    layout.extend(('ending', (), ending) for ending in endings)
    return layout


def _legacy_layout(budget, branches, last):
    """Use the original node ordering for a saved mock job."""
    main = budget - branches
    first_choice = main - branches - 1
    layout = []
    for i in range(main - 1):
        if i >= first_choice:
            layout.append(('choice', (i + 1, main + i - first_choice), None))
        else:
            layout.append(('scene', None, None))
    layout.append(('ending', (), 'true') if last else ('scene', (), None))
    layout.extend([('ending', (), 'bad')] * branches)
    return layout


def _branch_depth(nodes, limit=None):
    """Count choices still open on a path until their routes all reconverge."""
    by_id = {node['id']: node for node in nodes}
    successors = {nid: [choice['to'] for choice in node.get('choices') or []
                        if choice['to'] in by_id]
                  for nid, node in by_id.items()}
    incoming = dict.fromkeys(by_id, 0)
    for targets in successors.values():
        for target in targets:
            incoming[target] += 1
    queue = deque(nid for nid, count in incoming.items() if count == 0)
    ordered = []
    while queue:
        nid = queue.popleft()
        ordered.append(nid)
        for target in successors[nid]:
            incoming[target] -= 1
            if not incoming[target]:
                queue.append(target)
    if len(ordered) != len(nodes):
        return None  # Chapter graph validation reports cycles before completion.

    # Give every ending (or open chapter exit) the same virtual successor.
    positions = {nid: i for i, nid in enumerate(ordered)}
    virtual_end = 1 << len(ordered)
    postdominators = {}
    merges = {}
    for nid in reversed(ordered):
        common = postdominators[successors[nid][0]] if successors[nid] else virtual_end
        for target in successors[nid][1:]:
            common &= postdominators[target]
        postdominators[nid] = common | (1 << positions[nid])
        if by_id[nid]['type'] == 'choice':
            # The first node common to every route is the immediate postdominator.
            merges[nid] = (common & -common).bit_length() - 1

    depth = 0
    contexts = {}
    for i, nid in enumerate(ordered):
        for active in contexts.pop(nid, (frozenset(),)):
            unresolved = frozenset(choice for choice in active if merges[choice] != i)
            # ★ 必要分支（key）走的是「早分、各跑各的、最后各进各的结局」，本来就不汇合。
            #   把 key 算进嵌套深度，等于逼模型「分了就赶紧合回去」—— 那正是主人要的
            #   「必要分支早点分、走向几条完全不同的链路」的反面。
            #   所以这里只管气氛分支（flavor）的嵌套；key 的量由 key_branches 上限单独管。
            if nid in merges and by_id[nid].get('branch_kind') != 'key':
                unresolved |= {nid}
                depth = max(depth, len(unresolved))
                if limit is not None and depth > limit:
                    return depth
            for target in successors[nid]:
                paths = contexts.setdefault(target, set())
                # A superset always has at least as many active choices on any suffix.
                if not any(unresolved <= path for path in paths):
                    paths.difference_update([path for path in paths if path < unresolved])
                    paths.add(unresolved)
    return depth


def _graph_signature(graph):
    """把整张图压成一个可比较的指纹，用来判断「这一轮到底有没有推进」。

    三样东西都算进去，因为任意一样发生变化都说明模型在干活：
      · 节点结构（id/类型/分支性质/结局性质）—— 加节点、改类型、改分支性质
      · 边（from/port/to）—— 换走向、改接线（等量换边也要能识别出来）
      · 台词字数总和 —— 只改文字不改结构时，这一项会变

    只比节点数是不够的：实测模型会「删一个节点再补一个」，节点数不变但剧情变了，
    按节点数判会误判成卡死。
    """
    nodes = graph['nodes']
    return (
        tuple((node['id'], node['type'], node.get('branch_kind'), node.get('end_kind'))
              for node in nodes),
        tuple((edge['from'], edge['port'], edge['to']) for edge in graph['edges']),
        sum(len(line.get('text') or '')
            for node in nodes
            for line in (node.get('lines') or [])),
    )


def _tool_error(code, message, hint):
    """构造一个工具失败结果（统一格式，前端和模型都按它读）。"""
    return dict(ok=False, error=code, message=message, hint=hint)


def _describe(result):
    # ★ 真模型实测：原先只拼 e['message']，错误里的 node_id 全丢了。
    #   writer 看到「分支须有 2–4 个不同目标」根本不知道是哪个节点，
    #   只能瞎重写同一批内容 → 连续 3 次校验不过被判卡死（p_9eae63 实测）。
    #   这里把节点 id 一并带上，让它知道该改谁。
    """把工具失败结果拼成一句给模型看的话，带上节点 id，让它知道该改谁。"""
    details = []
    for item in result.get('errors', [])[:4]:
        node_id = item.get('node_id')
        message = item.get('message', '')
        details.append(f'{node_id}：{message}' if node_id and node_id not in message else message)
    parts = [result.get('message', '操作未完成')]
    for item in result.get('errors', [])[:4]:
        node_id = item.get('node_id')
        message = item.get('message', '')
        if node_id and node_id not in message:
            message = f'{node_id}：{message}'
        if message not in parts:
            parts.append(message)
    for dupe in (result.get('duplicates') or [])[:3]:
        node_id = dupe.get('node_id')
        if node_id and node_id not in parts[0]:
            parts.append(f'{dupe.get("ref") or node_id} 的选项都指向 {dupe.get("to")}')
    return '；'.join(parts)


def _resume_anomaly(evidence):
    """这次续写有没有「状态和内容对不上」的地方；没有就返回 None。

    正常的断点续写只有一个答案：从标着 writing 的那一章接着写。那种情况
    不该再花一轮模型去「判定」—— 答案本来就是确定的，多问一轮只是多加一次
    崩的机会。真正需要编剧自己看证据判的是下面这些异常，它们没有固定答案：

    · 盘上的图比检查点少了内容（被删过 / 被覆盖过）
    · 前面还有章没写完，后面却有章标着完成（顺序断了）
    · 某章标着写作中却一个节点都没有（刚开章就崩了）
    · 某章标着已完成，节点数却远低于本章预算（其实没写完）
    """
    checkpoint = evidence.get('checkpoint') or {}
    if checkpoint.get('status') == 'regressed':
        return '盘上的图比检查点少了内容（可能被删或被覆盖过）'
    if evidence.get('done_after_open'):
        return f"顺序断裂：{evidence['done_after_open']} 标着已完成，前面还有章没写完"
    if evidence.get('gaps'):
        return '；'.join(evidence['gaps'][:3])
    for chapter in evidence.get('chapters') or []:
        budget = int(chapter.get('node_budget') or 0)
        count = int(chapter.get('node_count') or 0)
        if chapter.get('status') == 'done' and budget and count < budget * 0.4:
            return (f"{chapter['id']} 标着已完成，但只有 {count}/{budget} 个节点，"
                    '可能其实没写完')
    return None


class WriterLoop:
    def __init__(self, pipeline, tools):
        """记下依赖与初始状态；这个阶段自己不做重活，调度交给下面的方法。"""
        self.pipeline = pipeline
        self.tools = tools
        self.provider = pipeline.providers.get('writer')
        self.total = pipeline.params['node_count']
        self.depth = pipeline.params.get('branch_depth', 2)
        # 分支分两类（主人 2026-11-08 定）：必要分支决定结局数量（最多 10 个），
        # 气氛分支只换对话、可以几十上百个。
        self.key_branches = max(1, min(10, int(pipeline.params.get('key_branches', 2))))
        self.flavor_branches = max(0, min(200, int(pipeline.params.get('flavor_branches', 8))))
        self.legacy_branches = False
        self.act_budgets = _act_budgets(self.total)
        self.system = (registry.system_prompt('writer', writer_mode='legacy')
                       if self.provider is not None and pipeline.params.get('writer_mode', 'skeleton') == 'legacy'
                       else '')

    @property
    def metrics(self):
        """本阶段的统计数字（写了多少节点 / 多少分支），最后进 agent_state。"""
        state = self.pipeline.graph.setdefault('agent_state', {})
        return state.setdefault('writer_metrics', dict(tool_calls=0, tool_rounds=0))

    async def dispatch(self, name, args, chapter_id=None):
        """按名字调一个工具，并把 ToolInputError 转成统一失败结果。"""
        self.metrics['tool_calls'] += 1
        return await self.tools.dispatch('writer', name, args, chapter_id=chapter_id)

    async def checked(self, name, args, chapter_id=None):
        """先跑一次前置校验，失败就抛（用于「不做完不能往下」的步骤）。"""
        result = await self.dispatch(name, args, chapter_id)
        if not result['ok']:
            raise ProviderError(result.get('error', 'WRITER_TOOL_FAILED'),
                                _describe(result), f'writer.{name}')
        return result

    async def mock_turn(self, name, args, chapter_id=None):
        """mock 模式下假装模型回了一次工具调用，让流程能跑完。"""
        self.metrics['tool_rounds'] += 1
        result = await self.checked(name, args, chapter_id)
        if name == 'write_nodes' and self.pipeline.delay:
            await asyncio.sleep(self.pipeline.delay)
        return result

    async def progress(self, label, **details):
        """对外报一次进度。"""
        count = len(self.pipeline.graph['nodes'])
        await self.pipeline.agent('writer', 'run', min(count / self.total, 0.99),
                                  f'{label} · 节点 {count}/{self.total}', **details)

    async def model_turn(self, messages, tools, chapter_id):
        """让模型看当前对话，拿回这一轮它要调的工具；JSON 坏了重试一次。"""
        self.metrics['tool_rounds'] += 1
        # ★ 模型偶尔会把工具参数的 JSON 写坏（台词里的引号/换行没转义），
        #   以前一次坏 JSON 就让整章几十轮的工作全部作废 ——
        #   实测（p_b097ebf4 c07 轮3）：Expecting ',' delimiter: char 607，直接 INVALID_RESPONSE 判死。
        #   这里坏一次就带着提示重试一次，还坏才算失败。
        hint = ('上一次的工具参数不是合法 JSON（解析报错：{err}）。'
                '请重新给出这一次工具调用，arguments 必须是严格合法的 JSON：'
                '字符串里的双引号和换行必须转义，不要有尾随逗号，不要写注释。')
        response = None
        for attempt in (1, 2):
            try:
                send = messages
                if attempt == 2:
                    send = messages + [dict(role='user', content=hint.format(err=last_error))]
                response = await self.provider.call_with_tools(send, tools, max_tokens=8192)
                break
            except ProviderError as exc:
                payload = exc.payload if isinstance(exc.payload, dict) else {}
                code = payload.get('code')
                if code == 'INVALID_RESPONSE' and attempt == 1:
                    last_error = str(payload.get('message') or exc)[:200]
                    await self.pipeline.log(
                        f'编剧返回的工具参数 JSON 不合法（{chapter_id or "规划"}），重试一次：{last_error}',
                        'warn')
                    await asyncio.sleep(1.0)
                    continue
                await self.pipeline.log(f'编剧调用失败（{chapter_id or "规划"}）：{code}', 'warn')
                raise
        calls = response['tool_calls']
        assistant = dict(role='assistant', content=response['text'])
        if calls:
            assistant['tool_calls'] = [
                dict(id=call['id'], type='function',
                     function=dict(name=call['name'], arguments=_encode(call['arguments'])))
                for call in calls
            ]
        messages.append(assistant)
        return calls

    def chapter_branch_error(self, chapter_id, key_target, flavor_target=0):
        """检查本章 key / flavor 分支数量，不够就返回错误让模型补。"""
        nodes = [node for node in self.pipeline.graph['nodes']
                 if self.tools.node_meta.get(node['id'], {}).get('chapter_id') == chapter_id]
        keys = [node for node in nodes
                if node['type'] == 'choice' and (node.get('branch_kind') or 'key') == 'key']
        flavors = [node for node in nodes
                   if node['type'] == 'choice' and node.get('branch_kind') == 'flavor']
        # 必要分支决定结局数量：至少 1 个（计划为 0 时跳过），比计划多 1 个也放过。
        if key_target > 0:
            high = key_target + 1
            if len(keys) < 1:
                return _tool_error(
                    'CHOICE_BUDGET',
                    f'本章一个必要分支都没有（计划 {key_target} 个）—— 必要分支决定走向哪个结局',
                    '用 write_nodes 写一个 type=choice、branch_kind=key 的节点')
            if len(keys) > high:
                return _tool_error(
                    'CHOICE_BUDGET',
                    f'本章必要分支 {len(keys)} 个，超过计划 {key_target} 个（最多 {high} 个）',
                    '必要分支多一个就多一个结局要收；把多余的改成 branch_kind=flavor，'
                    '或删除后重连')
        # 气氛分支不卡上限：写多写少都行，少了只是玩家少点互动感。
        # limit 多要一层：这样被挡时能报出「真的是几层」，而不是只知道超了
        depth = _branch_depth(self.pipeline.graph['nodes'], limit=self.depth + 1)
        if depth is not None and depth > self.depth:
            return _tool_error(
                'BRANCH_DEPTH',
                f'这次改动会让分支同时开到 {depth} 层，超过上限 {self.depth} 层',
                '先把最早那个 choice 的两条路线汇合到同一个场景节点，再开下一个独立选择；'
                '不要靠连边硬塞第三层。如果这章确实需要更深的选择，'
                '让作者把「分支嵌套」调大再续跑（它不影响已写好的章节，不会清空）')
        if chapter_id == self.tools.chapters[-1]['id'] and not any(
            node['type'] == 'ending' and node.get('end_kind') == 'true' for node in nodes
        ):
            return _tool_error('TRUE_ENDING_MISSING', '最后一章需要至少一个真结局',
                               '用 write_nodes 为收尾节点设置 type=ending、end_kind=true')
        return None

    def _gate_call(self, name, args, allowed, chapter_id, repairs, finished, chapter_read):
        """工具调用前的门禁：返回错误结果，None 表示放行。

        五道检查，从便宜到贵排：
        1. **已完成**：已经 finish 过，本轮剩下的调用全忽略（模型常同时返回多个调用）
        2. **修正次数用尽**：repairs 超上限后一律拒绝，只保留读的能力
        3. **工具白名单**：规划阶段和写章阶段能用的工具不同
        4. **单章作用域**：不能拿别的章的 chapter_id，也不能把 scope 写成 all
        5. **没读就写**：没先 get_chapter 就 write_nodes —— 不读就改图等于闭眼改

        单独抽出来是因为原来这五道是「if / elif 套在一个 for 里」，
        和内层真正的执行逻辑缠在一起，七层深的那个坑主要就在这儿。
        """
        if finished:
            return _tool_error('ALREADY_COMPLETE', '当前任务已完成', '结束本轮')
        if repairs > MAX_REPAIRS:
            return _tool_error('REPAIR_LIMIT', '已达修正次数上限', '检查本章落盘内容后续写')
        if name not in allowed:
            return _tool_error('TOOL_NOT_ALLOWED', '当前步骤不可使用该工具', '使用本轮工具列表中的工具')
        if chapter_id and (
            ('chapter_id' in args and args['chapter_id'] != chapter_id)
            or (name == 'validate_graph' and args.get('scope') != 'chapter')
        ):
            return _tool_error('CHAPTER_SCOPE', '仅可操作当前章',
                               f'使用 chapter_id={chapter_id}、scope=chapter')
        if name == 'write_nodes' and not chapter_read:
            return _tool_error('CHAPTER_NOT_READ', '写入前必须读取当前章',
                               f'先调用 get_chapter({chapter_id})')
        return None

    async def _run_call(self, name, args, chapter_id, key_target, flavor_target):
        """门禁放行后真正执行一次工具调用。

        只有一处额外动作：`finish_chapter` 之前先核算 key / flavor 分支数量，
        对不上就直接拦下 —— 让模型拿到「差几个」的具体数字，而不是先落盘一个
        不合格的结局再被打回（打回后它往往不知道改哪，只能反复重写）。
        """
        if name == 'finish_chapter' and not self.legacy_branches:
            blocked = self.chapter_branch_error(chapter_id, key_target, flavor_target)
            if blocked is not None:
                return blocked
        return await self.dispatch(name, args, chapter_id)

    async def model_work(self, task, chapter_id=None, key_target=0, flavor_target=0,
                         finish_tool=None):
        """开一段独立对话，让模型把这一章（或全书的规划）写出来。

        这是 writer 唯一的主循环，里面有五道闸，任何一道触发都会结束本阶段：

        1. **总时长**（MAX_STAGE_SECONDS）—— 跑了几小时还没完，判定卡死。
        2. **修正次数**（MAX_REPAIRS）—— 反复拿不到有效结果。
        3. **结构停滞**（STRUCT_STALL_CHECKS）—— 连续几轮结构校验的错误数不下降。
        4. **整图停滞**（STALL_ROUNDS）—— 刚图指纹连续几轮完全没变。
        5. **结构修复对话**—— 3、4 触发时不是直接死，而是换一段带新提示的对话
           再抢救一轮；抢救后仍无进展才抛 WRITER_CHAPTER_FAILED。

        `finish_tool` 指定「哪个工具算这一步做完」，默认按阶段推：写章是
        finish_chapter、规划是 plan_structure。断点续写判定传 set_resume_plan。

        为什么不用「工具调用次数」当上限：上万节点的项目根本算不出合理次数，
        算小了正常项目会被误杀。所以只按「时间 + 有没有进展」判。
        """
        allowed = CHAPTER_TOOLS if chapter_id else PLAN_TOOLS
        finish = finish_tool or ('finish_chapter' if chapter_id else 'plan_structure')
        offered = [tool for tool in WRITER_TOOLS if tool['function']['name'] in allowed]
        messages = [dict(role='system', content=self.system), dict(role='user', content=task)]
        calls_used = repairs = 0
        chapter_read = False
        failure = '模型未提交有效结果'

        # 不按调用次数设上限：上万节点的项目算不出合理次数。
        # 改用「总时长 + 连续无进展」两道闸防卡死，见文件头常量注释。
        started = time.monotonic()
        stall_rounds = 0
        last_signature = None
        last_heartbeat = 0.0
        best_error_count = None
        struct_stalls = 0
        recovering = False

        while repairs <= MAX_REPAIRS:
            elapsed = time.monotonic() - started
            if elapsed > MAX_STAGE_SECONDS:
                failure = f'本阶段跑了 {elapsed / 3600:.1f} 小时仍未完成，判定卡死'
                break
            calls = await self.model_turn(messages, offered, chapter_id)
            calls_used += len(calls)

            # 心跳：每 20 秒报一次进度，前端那条进度条才不会看起来冻住
            if elapsed - last_heartbeat >= 20:
                last_heartbeat = elapsed
                await self.progress(f'正在写 · 已用 {calls_used} 次调用 · '
                                    f'{int(elapsed // 60)} 分 {int(elapsed % 60)} 秒')
            if not calls:
                repairs += 1
                if chapter_id:
                    result = await self.dispatch('validate_graph', {'scope': 'chapter'}, chapter_id)
                    failure = (_describe(result) if not result['ok']
                               else '结构校验通过，仍须调用 finish_chapter 提交前情摘要')
                messages.append(dict(role='user', content=f'{failure}。请继续调用工具完成任务。'))
                continue

            finished = False
            round_notes = []
            for call in calls:
                name, args = call['name'], call['arguments']
                result = self._gate_call(name, args, allowed, chapter_id,
                                         repairs, finished, chapter_read)
                if result is None:
                    result = await self._run_call(name, args, chapter_id, key_target, flavor_target)
                    if name == 'get_chapter' and result['ok']:
                        chapter_read = True
                    if name == 'write_nodes' and result['ok']:
                        await self.progress(f'{chapter_id} 写入 {len(result["node_ids"])} 个节点')
                round_notes.append(f"{name}{'✓' if result['ok'] else '✗'}")
                messages.append(dict(role='tool', tool_call_id=call['id'], content=_encode(result)))
                if not result['ok']:
                    failure = _describe(result)
                if name == 'validate_graph' and result['ok']:
                    best_error_count = None
                    struct_stalls = 0
                if name == finish:
                    if result['ok']:
                        finished = True
                    elif result.get('error') != 'GRAPH_INVALID':
                        repairs += 1
            # 每轮记一条工具调用摘要：卡死或反复失败时，能直接从 run.log 看出
            # writer 到底在反复调什么（之前日志只有最后一条 error，全靠猜）。
            if round_notes:
                bad = any(note.endswith('✗') for note in round_notes)
                tail = f'  ← {failure[:70]}' if bad and failure else ''
                await self.pipeline.log(
                    f'[{chapter_id or "规划"} 轮{calls_used}] ' + ' · '.join(round_notes) + tail,
                    'warn' if bad else 'info')
            if finished:
                return

            # 比较改边后的实际拓扑和台词字数，等量换边也算一次变化。
            signature = _graph_signature(self.pipeline.graph)
            if signature == last_signature:
                stall_rounds += 1
            else:
                stall_rounds = 0
                last_signature = signature

            stalled = stall_rounds >= STALL_ROUNDS
            crossed_check = calls_used % STRUCT_CHECK_INTERVAL < len(calls)
            if chapter_id and (crossed_check or stalled):
                checked = await self.dispatch('validate_graph', {'scope': 'chapter'}, chapter_id)
                if checked['ok']:
                    best_error_count = None
                    struct_stalls = 0
                else:
                    error_count = len(checked['errors'])
                    if best_error_count is None or error_count < best_error_count:
                        best_error_count = error_count
                        struct_stalls = 0
                    else:
                        struct_stalls += 1
                    feedback = f'当前章节结构校验结果：{_encode(checked)}'
                    if stalled or struct_stalls >= STRUCT_STALL_CHECKS:
                        if recovering:
                            failure = (f'结构修复后仍无进展（已用 {calls_used} 次工具调用）：'
                                       f'{_describe(checked)}')
                            break
                        recovering = True
                        await self.pipeline.log(
                            f'[{chapter_id}] 进入结构修复（已用 {calls_used} 次工具调用）：{_describe(checked)}',
                            'warn')
                        messages = [
                            dict(role='system', content=self.system),
                            dict(role='user', content=(
                                f'{task}\n\n进入结构修复。以当前落盘图为准，先重新调用 get_chapter 查看 nodes、edges、'
                                'orphans 和 frontier。保留已写节点和可达路线，优先用已有 ref 修复。'
                                '按孤岛入口接回整段剧情，检查原后继仍可达，并确认本章接点。'
                                '修完后调用 validate_graph 和 finish_chapter。\n' + feedback)),
                        ]
                        chapter_read = False
                        repairs = stall_rounds = struct_stalls = 0
                        last_signature = None
                        best_error_count = error_count
                        continue
                    messages.append(dict(role='user', content=feedback))

            if stalled:
                failure = (f'连续 {STALL_ROUNDS} 轮没有任何图结构或台词字数变化'
                           f'（已用 {calls_used} 次工具调用）')
                break

        raise ProviderError('WRITER_CHAPTER_FAILED' if chapter_id else 'WRITER_PLAN_FAILED',
                            f'{chapter_id or "规划"}：{failure}', 'writer.loop')

    async def plan(self):
        """规划阶段：让模型先排章节结构，再进逐章写。"""
        if self.provider is not None:
            minutes = self.pipeline.params.get('target_minutes', 60)
            await self.model_work(
                f'玩家需求：{self.pipeline.body["requirement"]}\n'
                f'目标游玩时长 {minutes} 分钟，总节点数 {self.total}，含系统起点 1 个。'
                f'按此幕和章预算规划：{_encode(self.act_budgets)}。'
                '每个内层数字对应一章的 node_budget。保持预算顺序，为每章写标题和简短梗概，'
                '为每幕写标题和戏剧目标。梗概用两句短语表达，避免过长的工具参数。'
                '先读 project_brief，再调用 plan_structure。'
            )
            return

        acts = []
        ordinal = 0
        for index, budgets in enumerate(self.act_budgets, 1):
            chapters = []
            for budget in budgets:
                ordinal += 1
                chapters.append(dict(title=f'第 {ordinal} 章', beats='两人在雨声中相遇，试探彼此的决定',
                                     node_budget=budget))
            acts.append(dict(title=f'第 {index} 幕', goal='推进两人对同一事件的理解', chapters=chapters))
        await self.mock_turn('plan_structure', dict(
            logline=self.pipeline.body['requirement'].strip()[:40] or '雨夜的相遇',
            target_minutes=self.pipeline.params.get('target_minutes', 60), acts=acts,
        ))

    async def model_chapter(self, chapter, key_count, flavor_count):
        """写一章：给模型这一章的上下文，循环调用工具直到 finish_chapter。"""
        chapter_id = chapter['id']
        resume_feedback = ''
        if chapter['status'] == 'writing':
            checked = await self.dispatch('validate_graph', {'scope': 'chapter'}, chapter_id)
            if not checked['ok']:
                resume_feedback = f'\n已落盘的章节校验结果：{_encode(checked)}'
        if chapter['is_last']:
            ending = '本章是最后一章，主线结束于 end_kind=true 的真结局。'
        else:
            ending = '只保留一个无出口的主线 scene 作为下一章的接点。'
            if not self.legacy_branches:
                ending = '在本章内汇合各条路线，' + ending
        if self.legacy_branches:
            branch_instruction = f'沿用此前章节的分支计划，本章建议 {key_count + flavor_count} 个选择节点。'
        else:
            branch_instruction = (
                f'本章要写 {key_count} 个 **必要分支**（branch_kind="key"，选项直接决定走向哪个结局）'
                f'和 {flavor_count} 个 **气氛分支**（branch_kind="flavor"，只换对话和氛围，'
                f'两三条腿各走一小段后必须汇合回主线，不影响结局）。'
                '必要分支不要多写（多一个就多一个结局要收）；气氛分支多多益善，'
                '玩家「我在做选择」的手感主要靠它。'
                f'分支最多嵌套 {self.depth} 层。先让同一选择的路线汇合，再进入下一个独立选择。'
            )
        await self.model_work(
            f'玩家需求：{self.pipeline.body["requirement"]}\n'
            f'本章 {chapter_id}「{chapter["title"]}」，预算 {chapter["node_budget"]} 个节点，'
            f'已写 {len(chapter["nodes"])} 个。{ending}\n'
            f'{branch_instruction}\n'
            '先调用 get_chapter 读取本章、前情摘要和入口，按已有 ref 续写。'
            '只写本章，每批最多 15 个，已写内容保留，必要时更新有问题的 ref。'
            '通过 connect_nodes 把上一章的开放出口（第一章是系统起点）接入本章，'
            '连接剧情中的后继节点，choice 的分支写在 choices 里。'
            '把孤岛接回主线时，先接好它的后续路线，保证原后继仍可达。'
            '用 validate_graph(scope=chapter) 校验，然后用 finish_chapter 提交前情摘要。'
            + resume_feedback,
            chapter_id, key_count, flavor_count,
        )

    async def mock_chapter(self, chapter, branches):
        """mock 模式下按固定布局造一章，供离线演示。"""
        chapter_id = chapter['id']
        budget = chapter['node_budget']
        characters = (await self.mock_turn('list_characters', {}, chapter_id))['characters']
        who = characters[0]['id'] if characters else ''
        refs = [f'c{chapter["ordinal"]:02d}-s{i:02d}' for i in range(1, budget + 1)]
        known = {node['ref'] for node in chapter['nodes']}
        nodes = []
        ordinary = []
        layout = (_legacy_layout(budget, branches, chapter['is_last']) if self.legacy_branches
                  else _mock_layout(budget, branches, self.depth, chapter['is_last']))
        for index, (kind, targets, ending) in enumerate(layout):
            ref = refs[index]
            node = dict(ref=ref, type=kind, title=f'片段 {index + 1}', who=who,
                        bg_key='mock_rain',
                        bg_prompt='雨夜的窗边，室内灯光微弱')
            if who:
                # 模拟模式也要造出姿态与表情的变化：否则离线演示永远是「站 + 平静」，
                # 立绘差分在 mock 下根本触发不到，这个能力就没法自查。
                node['chars'] = [dict(id=who,
                                      pose=_MOCK_STANCES[index % len(_MOCK_STANCES)],
                                      pos='center',
                                      mood=_MOCK_MOODS[index % len(_MOCK_MOODS)])]
            if kind == 'ending':
                titles = {'true': '真结局', 'bad': '坏结局', 'alt': '分歧结局'}
                node.update(title=titles[ending], end_kind=ending)
            elif kind == 'choice':
                labels = (('继续追问', '就此告别') if self.legacy_branches else
                          ('把信交给她', '先去核对落款', '带着信一起离开'))
                node.update(title='做出选择', choices=[
                    dict(label=label, to=refs[target]) for label, target in zip(labels, targets)
                ])
            elif targets or (targets is None and index + 1 < budget):
                target = targets[0] if targets else index + 1
                ordinary.append((ref, refs[target]))
            if ref not in known:
                node['lines'] = mock_lines(chapter['ordinal'], index, node_type=kind, end_kind=ending)
                nodes.append(node)

        for offset in range(0, len(nodes), 10):
            batch = nodes[offset:offset + 10]
            await self.mock_turn('write_nodes', dict(chapter_id=chapter_id, nodes=batch), chapter_id)
            await self.progress(f'{chapter_id} 写入 {len(batch)} 个节点')

        if not chapter['entry_id']:
            for source in chapter['open_exits']:
                await self.mock_turn('connect_nodes', {'from': source, 'to': refs[0]}, chapter_id)
        connected = {node['ref'] for node in chapter['nodes'] if node.get('choices')}
        for source, target in ordinary:
            if source not in connected:
                await self.mock_turn('connect_nodes', {'from': source, 'to': target}, chapter_id)
        await self.mock_turn('validate_graph', {'scope': 'chapter'}, chapter_id)
        await self.mock_turn('finish_chapter', dict(
            chapter_id=chapter_id, summary='雨夜的谈话改变了两人对彼此的看法。她决定再等一会。'
        ), chapter_id)

    async def chapter(self, chapter_id):
        """取当前正在写的章节（或指定 id 的章节）。"""
        return (await self.checked('get_chapter', dict(chapter_id=chapter_id), chapter_id))['chapter']

    async def publish(self, chapter):
        """把这一章写完的节点推给前端。"""
        ids = {node['id'] for node in chapter['nodes']}
        if chapter['ordinal'] == 1:
            ids.add('n1')
        nodes = [node for node in self.pipeline.graph['nodes'] if node['id'] in ids]
        for node in nodes:
            node['status'] = 'generating'
        await self.pipeline.save_graph(self.pipeline.graph)
        await self.pipeline.publish_nodes(nodes)
        self.pipeline.graph['agent_state'].setdefault('writer_published', []).append(chapter['id'])
        await self.pipeline.save_graph(self.pipeline.graph)

    async def checkpoint(self, phase, chapter=None, note=''):
        """落一份续写快照 + 记一条运行日志。

        失败绝不往上抛：快照是保险，不能因为保险没上就拖垮整次生成。
        """
        try:
            return self.tools.checkpoints.note(
                self.pipeline.graph,
                job=(self.pipeline.graph.get('agent_state') or {}).get('job_id'),
                agent='writer', phase=phase, chapter=chapter, note=note)
        except Exception as exc:                    # noqa: BLE001
            await self.pipeline.log(f'检查点写入失败：{type(exc).__name__}: {exc}', 'warn')
            return None

    async def resume_decision(self):
        """断点续写时，把证据交给编剧自己判「从哪章续」。

        为什么不写死「status==done 就跳过」：实测状态和实际内容经常对不上 ——
        标着 writing 的章一个节点都没有、标着 done 的章只有 3 个节点、
        崩溃前刚 finish 的章摘要没落盘。变化率这么大，让看过证据的编剧判，
        比任何固定阈值都准。这里只负责**摆证据**，不替它下结论。

        模型不可用（mock / 纯离线）时返回 None，走原来的按状态续写。
        """
        evidence = await self.dispatch('resume_evidence', {})
        if not evidence['ok']:
            await self.pipeline.log(f'续写证据读不出来：{_describe(evidence)}', 'warn')
            return None
        chapters = evidence['chapters']
        touched = [chapter for chapter in chapters
                   if chapter.get('status') != 'pending' or (chapter.get('node_count') or 0)]
        status = evidence['checkpoint'].get('status')
        if not touched and status == 'empty':
            return None                         # 全新项目，没什么可判的
        anomaly = _resume_anomaly(evidence)
        if not anomaly:
            # 状态和内容对得上 → 从 writing 那章接着写，答案唯一，不用问模型
            await self.pipeline.log(f'断点续写：按章节状态继续（{len(touched)} 章有痕迹）', 'info')
            return None
        summary = ('、'.join(f"{c['id']}({c.get('status')}, {c.get('node_count') or 0} 节点)"
                            for c in touched[:12]) or '无')
        await self.pipeline.log(
            f'断点续写：发现「状态和内容对不上」—— {anomaly}；检查点判定 {status}；'
            f'交给编剧按证据判', 'warn')
        try:
            await self.model_work(
                '这是一次断点续写：上次生成被打断了，盘上已经有一些章节，但记录和实际对不上。\n'
                f'异常：{anomaly}\n'
                f'章节痕迹：{summary}\n'
                f"检查点：{_encode({key: value for key, value in evidence['checkpoint'].items() if key != 'graph'})}\n"
                f"状态与内容不符的地方：{_encode(evidence['gaps'])}\n"
                f"顺序断裂：{_encode(evidence['done_after_open'])}\n"
                '请先调 resume_evidence 读完整证据，再调 set_resume_plan 落决定：'
                '从哪一章继续写；有没有哪一章标着已完成、其实只写了一半、要退回继续补。\n'
                '判定原则：节点数远低于本章预算的章退回补写；标着 writing 的章直接接着写；'
                '已经写实的章不要动 —— 动它等于把那段剧情重写一遍。',
                None, finish_tool='set_resume_plan')
        except Exception as exc:                    # noqa: BLE001
            # 判定是增强，不是必经环节：模型不配合就退回按章节状态续写。
            await self.pipeline.log(
                f'续写判定跳过（{type(exc).__name__}: {exc}），按章节状态续写', 'warn')
            return None
        plan = (self.pipeline.graph.get('agent_state') or {}).get('resume_plan')
        if plan:
            await self.pipeline.log(
                f"编剧判定：从 {plan['resume_from']} 续写"
                + (f"，退回补写 {plan['redo']}" if plan.get('redo') else '')
                + f"（{plan.get('reason', '')}）", 'info')
        return plan

    async def run(self):
        """把默认编剧入口切到整图生成，同时为历史任务保留逐章写法。"""
        if self.pipeline.params.get('writer_mode', 'skeleton') == 'legacy':
            if self.pipeline.graph['agent_state'].get('skeleton') is not None:
                raise ProviderError('SKELETON_FROZEN', '已冻结的骨架只能按骨架模式续写', 'writer.loop')
            return await self.run_legacy()
        return await self.run_skeleton()

    async def run_skeleton(self):
        """先提交完整拓扑，再以磁盘上的节点状态决定哪些对白还要生成。"""
        pipeline = self.pipeline
        graph = pipeline.graph
        raw = graph['agent_state'].get('skeleton')
        if raw is None:
            await pipeline.log(f'正在生成整图骨架，目标 {self.total} 个节点')
            skeleton = await generate_skeleton(
                pipeline, self.tools, target_nodes=self.total, depth=self.depth,
                endings=None, requirement=pipeline.body['requirement'])
            if reason := skeleton.meta.get('degraded_reason'):
                await self.use_mock_writer(reason)
            elif any(warning.startswith(FALLBACK_WARNING_PREFIX)
                     for warning in skeleton.meta.get('structure_warnings', ())):
                await pipeline.degrade('writer', '部分骨架文案未生成',
                                       impact='缺失的标题、梗概或选项使用模板文案')
            existing = {character['id'] for character in graph['characters']}
            for character in skeleton.meta.get('characters', ()):
                if character['id'] not in existing and len(existing) < 8:
                    await self.checked('upsert_character', character)
                    existing.add(character['id'])
            await self.checked('write_skeleton', skeleton.to_dict())
            await self.checkpoint('skeleton_written', note='整图骨架已校验并冻结')
        else:
            try:
                skeleton = validate_skeleton(Skeleton.from_dict(raw))
                self.verify_frozen_graph(skeleton)
            except ValueError as exc:
                raise ProviderError('SKELETON_INVALID', str(exc), 'writer.resume') from exc
            done = sum(node['status'] == 'done' for node in graph['nodes'])
            await pipeline.log(f'从已冻结的骨架续写，跳过 {done} 个已完成节点')

        await pipeline.publish_nodes(graph['nodes'])
        total = len(graph['nodes'])
        pending = [node['id'] for node in graph['nodes'] if node['status'] != 'done']
        done = total - len(pending)
        batch_size = 10 if self.provider is not None else 20
        # 反复补齐：模型一次可能只填一部分，漏下的要在下一轮继续要，
        # 不能只遍历一遍 pending（那样漏掉的节点永远是 pending）。
        for _fill_round in range(FILL_ROUNDS):
            pending = [node['id'] for node in graph['nodes'] if node['status'] != 'done']
            if not pending:
                break
            if _fill_round:
                await pipeline.log(f'第 {_fill_round + 1} 轮补齐：还有 {len(pending)} 个节点没填')
            for offset in range(0, len(pending), batch_size):
                ids = pending[offset:offset + batch_size]
                self.metrics['tool_rounds'] += 1
                try:
                    await self.checked('fill_segment', {'node_ids': ids})
                except ProviderError as exc:
                    code = exc.payload['code']
                    if self.provider is None:
                        raise
                    if writer_unavailable(code):
                        await self.use_mock_writer(exc.payload['message'])
                        await self.checked('fill_segment', {'node_ids': ids})
                    elif code in FILL_CONTENT_ERRORS:
                        await self.fill_with_placeholders(ids, exc.payload['message'])
                    else:
                        raise
                await self.publish_fill(ids, total)

        remaining = [node['id'] for node in graph['nodes'] if node['status'] != 'done']
        for offset in range(0, len(remaining), batch_size):
            ids = remaining[offset:offset + batch_size]
            await self.fill_with_placeholders(ids, '多轮填充后仍有节点缺少台词')
            await self.publish_fill(ids, total)

        plan = await direct_production(pipeline, self.tools)
        key = sum(node.branch_kind == 'key' for node in skeleton.nodes)
        flavor = sum(node.branch_kind == 'flavor' for node in skeleton.nodes)
        director = dict(source=plan.get('source'), backgrounds=len(plan['backgrounds']),
                        poses=len(plan['poses']), voice_batches=len(plan['voice']['order']))
        return dict(writer_mode='skeleton', chapters=len(self.tools.chapters), choices=key + flavor,
                    choice_ratio=round((key + flavor) / total, 4), key_choices=key, flavor_choices=flavor,
                    key_target=self.key_branches, flavor_target=self.flavor_branches,
                    branch_depth=skeleton_branch_depth(skeleton), director=director, **self.metrics)

    async def fill_with_placeholders(self, ids, reason):
        await self.pipeline.degrade('writer', reason,
                                    impact='缺失的对白和布景使用本地占位内容')
        selected = set(ids)
        nodes = [node for node in self.pipeline.graph['nodes']
                 if node['id'] in selected and node['status'] != 'done']
        await self.checked('fill_segment', {'node_ids': ids, 'nodes': mock_fill(self.tools, nodes)})

    async def publish_fill(self, ids, total):
        nodes = self.pipeline.graph['nodes']
        done = sum(node['status'] == 'done' for node in nodes)
        await self.checkpoint('fill_done', note=f'对白已填充 {done}/{total} 个节点')
        selected = set(ids)
        await self.pipeline.publish_nodes([node for node in nodes if node['id'] in selected],
                                          ('status', 'lines', 'bg', 'chars'))
        await self.pipeline.agent('writer', 'run', min(done / total, 0.99), f'填充对白 · {done}/{total}')

    async def use_mock_writer(self, reason):
        """同时停用填充和导演的模型调用，让供应商故障不影响已冻结的拓扑。"""
        self.provider = self.pipeline.providers['writer'] = self.tools.providers['writer'] = None
        await self.pipeline.degrade('writer', reason)
        await self.pipeline.save_graph(self.pipeline.graph)

    def verify_frozen_graph(self, skeleton):
        """核对恢复点里的节点和选项，防止对白填到与骨架不一致的图。"""
        graph = self.pipeline.graph
        validate_graph(graph, auto_voice=False)
        expected = skeleton.to_graph_stub(graph['characters'], graph['project'])
        if graph['edges'] != expected['edges']:
            raise ValueError('已落盘的连线与冻结骨架不一致')
        fields = ('id', 'type', 'branch_kind', 'end_kind', 'choices')
        actual_nodes, expected_nodes = graph['nodes'], expected['nodes']
        if len(actual_nodes) != len(expected_nodes):
            raise ValueError('已落盘的节点数与冻结骨架不一致')
        for actual, stub in zip(actual_nodes, expected_nodes):
            if any(actual.get(field) != stub.get(field) for field in fields):
                raise ValueError(f'{actual["id"]} 的结构与冻结骨架不一致')

    async def run_legacy(self):
        """延续已写章节的读写规则，供旧任务和显式回退使用。"""
        await self.checked('project_brief', {})
        structure = await self.checked('get_structure', {})
        # planned 记的是「进来之前就有结构」= 这是断点续写，不是首次生成。
        # 不能直接看 structure['planned']：刚 plan() 完它也会变成 True。
        planned = bool(structure['planned'])
        if not planned:
            await self.plan()
        state = self.pipeline.graph['agent_state']
        version = state.get('writer_branch_version', state.get('mock_writer_version'))
        if version is None:
            version = 1 if len(self.pipeline.graph['nodes']) > 1 else WRITER_BRANCH_VERSION
            state['writer_branch_version'] = version
            await self.pipeline.save_graph(self.pipeline.graph)
        chapters = (await self.checked('list_chapters', {'status': 'all'}))['chapters']
        self.legacy_branches = version == 1
        if self.legacy_branches:
            await self.pipeline.log('恢复旧版任务，保留已写节点和连线，剩余章节沿用原分支布局', 'warn')
            remaining = min(self.depth, (self.total - 2) // 2)
            branch_plan = []
            for chapter in chapters:
                count = min(remaining, (chapter['node_budget'] - 1) // 2)
                branch_plan.append((count, 0))
                remaining -= count
        else:
            # 先分必要分支，再把「剩下的容量」分给气氛分支 —— 两类共用同一份节点预算。
            key_counts, key_left = _split_branches(chapters, self.key_branches)
            budgets = [max(0, int(chapter.get('node_budget') or 0)) for chapter in chapters]
            full = [max(0, (budget - 2) // 3) for budget in budgets]
            rest = [max(0, full[i] - key_counts[i]) for i in range(len(chapters))]
            flavor_counts, flavor_left = _split_branches(chapters, self.flavor_branches, capacity=rest)
            branch_plan = list(zip(key_counts, flavor_counts))
            if key_left:
                await self.pipeline.log(
                    f'节点预算只够排 {self.key_branches - key_left} 个必要分支'
                    f'（计划 {self.key_branches} 个）—— 想再多就加节点数', 'warn')
            if flavor_left:
                await self.pipeline.log(
                    f'节点预算只够排 {self.flavor_branches - flavor_left} 个气氛分支'
                    f'（计划 {self.flavor_branches} 个）—— 想再多就加节点数', 'warn')
        done = sum(chapter['status'] == 'done' for chapter in chapters)
        if planned:
            await self.pipeline.log(f'编剧断点续写：跳过 {done} 个已完成章节')

        # 断点续写：先让编剧看证据自己判「从哪章续、哪章要退回补」。
        # 它可能把某章的 status 从 done 退回 writing，所以判完要重读章节列表。
        plan = await self.resume_decision() if (planned and self.provider is not None) else None
        if plan:
            chapters = (await self.checked('list_chapters', {'status': 'all'}))['chapters']
        resume_at = 0
        if plan and plan.get('resume_from'):
            resume_at = next((chapter['ordinal'] for chapter in chapters
                              if chapter['id'] == plan['resume_from']), 0)
        redo = set((plan or {}).get('redo') or [])

        for info, (key_n, flavor_n) in zip(chapters, branch_plan):
            chapter_id = info['id']
            if resume_at and info['ordinal'] < resume_at and chapter_id not in redo:
                await self.progress(f'按续写计划跳过 {chapter_id}')
                continue
            if info['status'] == 'done' and chapter_id not in redo:
                if chapter_id not in self.pipeline.graph['agent_state'].get('writer_published', ()):
                    await self.publish(await self.chapter(chapter_id))
                continue
            chapter = await self.chapter(chapter_id)
            if info['status'] == 'writing':
                await self.pipeline.log(f'编剧断点续写：从 {chapter_id} 已落盘的 {len(chapter["nodes"])} 个节点继续')
            await self.progress(f'编剧第 {chapter["ordinal"]}/{len(chapters)} 章「{info["title"]}」',
                                chapter_id=chapter_id, chapter_title=info['title'])
            if self.provider is None:
                await self.mock_chapter(chapter, key_n + flavor_n)
            else:
                await self.model_chapter(chapter, key_n, flavor_n)
            await self.publish(await self.chapter(chapter_id))
            await self.checkpoint('chapter_done', chapter_id)
            await self.progress(f'完成 {chapter_id}「{info["title"]}」')

        await self.checked('validate_graph', {'scope': 'all'})
        nodes = self.pipeline.graph['nodes']
        # 不按目标节点数判失败。目标只是「用户期望」，LLM 估不准：
        # 实测同一个目标 24，一次写成 23、一次写成 27，两次都是两章 finish_chapter
        # 全部通过、布局都排好了，最后被这条判死整盘白跑。
        # 多几个少几个节点完全不影响游戏能不能玩，这条校验真正该防的是「写崩了」——
        # 也就是几乎没写出东西（或者跟目标差得离谱），而不是差几个。
        floor = max(4, int(self.total * 0.5))
        if len(nodes) < floor:
            raise ProviderError('WRITER_COUNT_MISMATCH',
                                f'编剧只落盘了 {len(nodes)} 个节点，远少于目标 {self.total}（至少要有 {floor} 个）',
                                'writer.loop')
        choices = sum(node['type'] == 'choice' for node in nodes)
        key_choices = sum(1 for node in nodes if node['type'] == 'choice'
                          and (node.get('branch_kind') or 'key') == 'key')
        flavor_choices = sum(1 for node in nodes if node['type'] == 'choice'
                             and node.get('branch_kind') == 'flavor')
        depth = _branch_depth(nodes, limit=None if self.legacy_branches else self.depth)
        if not self.legacy_branches:
            # 必要分支决定结局数量：太少游戏没分支，太多收不住（硬上限 10）。
            if key_choices < self.key_branches:
                raise ProviderError(
                    'WRITER_KEY_BRANCH_MISSING',
                    f'必要分支只写了 {key_choices} 个，要 {self.key_branches} 个 —— 它决定有几个结局',
                    'writer.loop')
            if key_choices > 10:
                raise ProviderError(
                    'WRITER_KEY_BRANCH_TOO_MANY',
                    f'必要分支 {key_choices} 个，超过上限 10 个（每个都要配一套结局）', 'writer.loop')
            # 气氛分支不卡数量：少了只影响互动感，多了会自然被节点预算约束住。
            if depth > self.depth:
                raise ProviderError('WRITER_BRANCH_DEPTH', f'全书分支嵌套超过 {self.depth} 层', 'writer.loop')
            if not any(node['type'] == 'ending' and node.get('end_kind') == 'true' for node in nodes):
                raise ProviderError('WRITER_TRUE_ENDING_MISSING', '全书缺少真结局', 'writer.loop')

        # 全书写完再统一整理布局：章内主干往右、分支上下展开，章节沿横向接着排。
        # 生成过程中是「一直往右的长龙」，最后这一步把分叉/汇合摆清楚。
        try:
            from ..engine.layout import apply_layout
            meta = getattr(self.tools, 'node_meta', {}) or {}
            chapter_of = {nid: (m or {}).get('chapter_id') for nid, m in meta.items()}
            # 起点 n1 是系统建的、没有 chapter_id，不归给第一章就会被排到图最下面，
            # 单章视图拉进来一个孤零零的远端节点（实测 y 跨度 280→10002，缩到 7%）。
            chapters_order = getattr(self.tools, 'chapters', None) or []
            if chapters_order and 'n1' in {n['id'] for n in self.pipeline.graph['nodes']}:
                chapter_of.setdefault('n1', chapters_order[0]['id'])
            moved = apply_layout(self.pipeline.graph, chapter_of=chapter_of or None)
            if moved:
                await self.pipeline.log(f'布局已整理：{moved} 个节点按章节分行、分支上下展开', 'info')
        except Exception as exc:            # 布局失败绝不能拖垮生成
            await self.pipeline.log(f'布局整理跳过：{type(exc).__name__}', 'warn')

        # 剧本写完不算收工：编剧要先把下游的活排清楚（生产计划），
        # 后面 art / voice 都按这张表干活。排产失败不能拖垮已经写好的剧本。
        director = {}
        try:
            plan = await direct_production(self.pipeline, self.tools)
            director = dict(source=plan.get('source'),
                            backgrounds=len(plan.get('backgrounds') or []),
                            poses=len(plan.get('poses') or []),
                            voice_batches=len((plan.get('voice') or {}).get('order') or []))
        except Exception as exc:            # noqa: BLE001
            await self.pipeline.log(f'导演排产跳过：{type(exc).__name__}: {exc}', 'warn')

        await self.pipeline.save_graph(self.pipeline.graph)
        stats = dict(chapters=len(chapters), choices=choices, choice_ratio=round(choices / self.total, 4),
                     key_choices=key_choices, flavor_choices=flavor_choices,
                     key_target=self.key_branches, flavor_target=self.flavor_branches,
                     branch_depth=depth, director=director, **self.metrics)
        if self.legacy_branches:
            stats['legacy_branch_plan'] = True
        return stats


# ==================== 导演：编剧是总导演 ====================
# 主人 2026-11-08 定：「编剧 agent 一定是最重要的，可以由他来分配任务给下面的 agent」。
# 落地成三件事：
#   1. 排产 production_plan —— 每个背景一句话画面描述 + 验收要素 + 优先级，
#      每个角色一句外形锚点，配音按什么顺序配。写进 graph.agent_state，下游照表干活。
#   2. 验收 review_art —— 画完把「计划 vs 实际交付」交回给编剧自己判：
#      哪张 pass、哪张 redo（并给出更具体的描述）、哪张 skip。
#   3. 兜底 —— 模型不可用时按剧本已有字段生成确定性计划，保证链路能跑完。
#
# 这里故意**没有**「画 3 次不成就放弃」「超过 10 张就分批」这类判定：
# 合格与否、要不要重做，都是导演看了实际交付之后自己说的。
# DIRECTOR_ROUNDS 只是防死循环的保险丝，不是合格判定。

PRODUCTION_PLAN_VERSION = 1
PLAN_ATTEMPTS = 2          # 排产表格式不对时让导演重排几次（格式兜底）
DIRECTOR_ROUNDS = 2        # 「重做 → 再看一眼」最多几轮（防死循环，不是合格线）
BRIEF_LIMIT = 200
ACCEPT_LIMIT = 100
LABEL_LIMIT = 8


def _background_groups(graph, node_meta):
    """按 bg_key 归并节点。口径与美术工具一致：同一个键 = 同一个地方 = 一张图。"""
    groups = {}
    for node in graph['nodes']:
        fallback = 'start' if node['type'] == 'start' else (node['bg'].get('asset') or node['id'])
        key = ((node_meta or {}).get(node['id']) or {}).get('bg_key') or fallback
        groups.setdefault(key, []).append(node)
    return groups


def _production_payload(pipeline, tools):
    """把「下游要交付什么」摊成清单交给导演。纯读图，不改图。"""
    graph = pipeline.graph
    node_meta = getattr(tools, 'node_meta', None) or {}
    groups = _background_groups(graph, node_meta)
    appearance, stances, variants = {}, {}, {}
    for node in graph['nodes']:
        for pose in node['chars']:
            cid = pose['id']
            appearance[cid] = appearance.get(cid, 0) + 1
            stance = stance_of(pose.get('stance') or '')
            mood = mood_of(pose.get('mood') or '')
            if stance or pose.get('pose'):
                stances.setdefault(cid, set()).add(stance)
            # 差分维度：(角色, 姿态, 表情) 一个组合要一张图。
            # 导演不需要按组合写 brief（外形锚点是角色级的），
            # 但必须知道「这个角色要画几张」，否则会低估美术工作量。
            cell = variants.setdefault(cid, defaultdict(int))
            cell[(stance, mood)] += 1
    backgrounds = [dict(key=key,
                        nodes=len(nodes),
                        prompt=_strip_style(nodes[0]['bg'].get('prompt')),
                        has_asset=bool((nodes[0]['bg'].get('asset') or '').strip()),
                        titles=[node.get('title') or node['id'] for node in nodes[:3]])
                   for key, nodes in groups.items()]
    poses = []
    for character in graph['characters']:
        cid = character['id']
        cells = variants.get(cid) or {}
        # 差分张数：stand + neutral 由基底覆盖，其余组合各一张
        extra = sum(1 for (stance, mood) in cells
                    if not (stance == 'stand' and mood == 'neutral'))
        poses.append(dict(
            key=cid,
            name=character.get('name') or cid,
            gender=character.get('gender') or 'other',
            look=(character.get('look') or '').strip(),
            nodes=appearance.get(cid, 0),
            stances=sorted(stances.get(cid, ())),
            # 这个角色一共要出几张立绘：基底 1 张 + 差分 N 张。
            # 导演不需要按组合写 brief（外形锚点是角色级的），但必须知道工作量。
            draw_count=1 + extra,
            variants=[dict(key=pose_key(cid, stance, mood), stance=stance,
                           mood=mood, nodes=count)
                      for (stance, mood), count in
                      sorted(cells.items(), key=lambda kv: -kv[1])[:12]],
        ))
    chapters = [chapter for act in graph['agent_state'].get('acts', []) for chapter in act['chapters']]
    return dict(
        requirement=(pipeline.body.get('requirement') or '').strip()[:400],
        logline=graph['agent_state'].get('logline') or '',
        backgrounds=backgrounds,
        poses=poses,
        chapters=[dict(id=chapter['id'], ordinal=chapter['ordinal'], title=chapter['title'],
                       beats=(chapter.get('beats') or '')[:120],
                       summary=(chapter.get('summary') or '')[:120])
                  for chapter in chapters],
    )


def _clip(text, limit):
    """截断过长的字符串，给日志用。"""
    return str(text or '').strip()[:limit]


def _strip_style(brief):
    """剥掉 draft_prompt 拼进去的风格前缀。

    兜底计划直接拿剧本里已出图的 prompt 当 brief；那条 prompt 是
    「画法…色调#xxxxxx。场景…。画面描述」拼出来的，带着风格前缀。
    再走一遍 draft_prompt 就会把画法/光影/色板写两遍 —— 提示词一膨胀，
    生图模型就开始各画各的。所以只留「画面本身」那一段。
    """
    text = str(brief or '').strip()
    head, dot, rest = text.partition('。')
    if dot and head.startswith('画法') and '色调' in head:
        return rest.strip()
    return text


def _normalize_plan(raw, payload):
    """把导演给的表对齐到实际素材清单：key 不能多不能少，缺的用剧本字段补。

    返回 (plan, issues)。issues 不为空表示这张表有结构问题（漏条目/编造 key），
    上层会让导演重排一次；最终仍不齐就接受已修好的部分。
    """
    raw = raw if isinstance(raw, dict) else {}
    issues = []

    def index(items, key_field='key'):
        """内部小工具：在列表里找下标。"""
        found = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get(key_field) or '').strip()
            if key:
                found[key] = item
        return found

    def entries(planned, given, fallback_brief, fallback_label):
        """内部小工具：取某个计划项的分批入口。"""
        result, extra = [], []
        for item in planned:
            key = item['key']
            row = given.get(key)
            if row is None:
                issues.append(f'漏了 {key}')
                row = {}
            brief = _clip(row.get('brief'), BRIEF_LIMIT) or fallback_brief(item)
            accept = _clip(row.get('acceptance'), ACCEPT_LIMIT) or brief[:ACCEPT_LIMIT]
            # label 会变成文件名（01-雨夜天台.png），给下游一个能认的名字。
            label = _clip(row.get('label'), LABEL_LIMIT) or fallback_label(item)
            try:
                priority = int(row.get('priority'))
            except (TypeError, ValueError):
                priority = 0
            result.append(dict(key=key, label=label, brief=brief, acceptance=accept,
                               priority=priority, plan_item=item))
        known = {item['key'] for item in planned}
        extra = [key for key in given if key not in known]
        if extra:
            issues.append('编造了不存在的 key：' + '、'.join(extra[:4]))
        # priority 缺省时按「出现节点多 / 关键场景」排，保证下游有个确定顺序。
        if all(not item['priority'] for item in result):
            result.sort(key=lambda item: -(item['plan_item'].get('nodes') or 0))
            for index, item in enumerate(result, 1):
                item['priority'] = index
        return result

    def bg_fallback(item):
        """排产表没给背景说明时，按 bg_key 拼一句兜底提示词。"""
        text = (item.get('prompt') or '').strip()
        if text:
            return text
        titles = '、'.join(item.get('titles') or [])
        return f'{titles or item["key"]} 的场景' if titles else f'{item["key"]} 的场景'

    def pose_fallback(item):
        """排产表没给立绘说明时，按角色信息拼一句兜底提示词。"""
        look = (item.get('look') or '').strip()
        base = f'{item.get("name") or item["key"]}'
        return f'{base}，{look}' if look else f'{base} 的半身立绘'

    backgrounds = entries(payload['backgrounds'], index(raw.get('backgrounds')), bg_fallback,
                         lambda item: _clip((item.get('titles') or [item['key']])[0], LABEL_LIMIT))
    poses = entries(payload['poses'], index(raw.get('poses')), pose_fallback,
                    lambda item: _clip(item.get('name') or item['key'], LABEL_LIMIT))

    chapter_ids = [chapter['id'] for chapter in payload['chapters']]
    voice = raw.get('voice') if isinstance(raw.get('voice'), dict) else {}
    order = [str(item) for item in (voice.get('order') or []) if str(item) in chapter_ids]
    if chapter_ids and not order:
        order = list(chapter_ids)
    elif set(order) != set(chapter_ids):
        # 只排了部分章节：把没提到的章节补在末尾，顺序由导演给的那部分优先。
        missing = [cid for cid in chapter_ids if cid not in order]
        if missing:
            order = order + missing
    plan = dict(backgrounds=[{k: v for k, v in item.items() if k != 'plan_item'} for item in backgrounds],
                poses=[{k: v for k, v in item.items() if k != 'plan_item'} for item in poses],
                voice=dict(order=order, notes=_clip(voice.get('notes'), 300)),
                notes=_clip(raw.get('notes'), 300))
    return plan, issues


PLAN_SYSTEM = (
    '你是这部 Galgame 的编剧，现在兼任总导演。剧本已经写完，下游美术和配音在等你的排产表才能开工。\n'
    '你的活：把剧本里写过的画面翻译成下游能直接执行的任务清单。\n'
    '1. 每个背景一条：brief 写清地点、时间、天气、光线、画面里有什么（场景图不要有人）；'
    'acceptance 写「验收时看到什么才算画对」的关键要素（雨、深夜、天台栏杆、台灯…）；\n'
    '2. 每个角色一条：brief 写外形锚点（发色发型、衣着、标志性特征），会逐字用在每一张立绘上；'
    '我给你的 payload 里，每个角色还带着 variants（它会出现的姿态×表情组合）和 draw_count（一共要出几张立绘）——'
    '这两项只用来看工作量，**不要**按组合拆成多条；\n'
    '3. 配音：给章节一个合理的合成顺序，notes 写全书配音基调。\n'
    '硬性要求：\n'
    '- brief 只写客观画面要素，不写画风/配色/画法/笔触 —— 那些由风格圣经统一注入，你再写一遍会打架；\n'
    '- key 必须原样照抄我给你的 key：一个不许新增、一个不许改名；\n'
    '- label 是给这张图起的中文短名（8 字以内，如「雨夜天台」「她摊开手」），会变成文件名，\n'
    '  要让人一眼认出这是哪个场景、哪个人；\n'
    '- priority 决定先画哪个：1 最重要（核心场景、出场节点最多的），可重复但别全填 1；\n'
    '- 只输出 JSON，不要 markdown 代码块，不要解释。\n'
    '结构：{"backgrounds":[{"key":"","label":"","brief":"","acceptance":"","priority":1}],'
    '"poses":[{"key":"","label":"","brief":"","acceptance":"","priority":1}],'
    '"voice":{"order":["c01"],"notes":""},"notes":""}'
)


async def _agent_plan(pipeline, payload):
    """让编剧自己排产。格式不对就带着问题重排一次；仍不行返回 None（上层兜底）。"""
    provider = pipeline.providers.get('writer')
    if provider is None:
        return None
    messages = [dict(role='system', content=PLAN_SYSTEM),
                dict(role='user', content='剧本与素材清单：' + _encode(payload))]
    last = ''
    for attempt in range(PLAN_ATTEMPTS):
        try:
            raw = await provider.call_json(messages, temperature=0.5, max_tokens=4096)
        except ProviderError as exc:
            last = f'{exc.payload["code"]}: {exc.payload["message"][:120]}'
            await pipeline.log(f'导演排产调用失败（第 {attempt + 1} 次）：{last}', 'warn')
            continue
        plan, issues = _normalize_plan(raw, payload)
        if not issues:
            return plan
        last = '；'.join(issues[:6])
        await pipeline.log(f'导演排产表需要修正（第 {attempt + 1} 次）：{last}', 'warn')
        messages.append(dict(role='user', content=f'这张表有问题：{last}。改掉后重新只输出 JSON。'))
    await pipeline.log(f'导演排产改用兜底计划：{last or "模型未给出可用计划"}', 'warn')
    return None


def _fallback_plan(payload):
    """模型不可用时的确定性计划：直接用剧本里已经写好的画面描述和外形锚点。"""
    backgrounds = []
    for item in payload['backgrounds']:
        brief = (item.get('prompt') or '').strip() or (
            f'{item["key"]} 的场景')
        label = _clip((item.get('titles') or [item['key']])[0], LABEL_LIMIT)
        backgrounds.append(dict(key=item['key'], label=label, brief=_clip(brief, BRIEF_LIMIT),
                                acceptance=_clip(brief, ACCEPT_LIMIT), priority=0))
    backgrounds.sort(key=lambda row: -(next((item['nodes'] for item in payload['backgrounds']
                                             if item['key'] == row['key']), 0)))
    for index, row in enumerate(backgrounds, 1):
        row['priority'] = index
    poses = []
    for item in payload['poses']:
        look = (item.get('look') or '').strip()
        name = item.get('name') or item['key']
        brief = f'{name}，{look}' if look else f'{name} 的半身立绘'
        poses.append(dict(key=item['key'], label=_clip(name, LABEL_LIMIT),
                          brief=_clip(brief, BRIEF_LIMIT),
                          acceptance=_clip(look or brief, ACCEPT_LIMIT), priority=0))
    poses.sort(key=lambda row: -(next((item['nodes'] for item in payload['poses']
                                       if item['key'] == row['key']), 0)))
    for index, row in enumerate(poses, 1):
        row['priority'] = index
    return dict(backgrounds=backgrounds, poses=poses,
                voice=dict(order=[chapter['id'] for chapter in payload['chapters']], notes=''),
                notes='')


async def direct_production(pipeline, tools, force=False):
    """导演排产：有现成计划就直接复用（断点恢复不会重复烧 token）。"""
    state = pipeline.graph['agent_state']
    plan = state.get('production_plan')
    if plan and plan.get('version') == PRODUCTION_PLAN_VERSION and not force:
        return plan
    payload = _production_payload(pipeline, tools)
    plan, source = None, 'fallback'
    if pipeline.providers.get('writer') is not None:
        plan = await _agent_plan(pipeline, payload)
        source = 'agent' if plan else 'fallback'
    if plan is None:
        plan = _fallback_plan(payload)
    record = dict(plan, version=PRODUCTION_PLAN_VERSION, source=source,
                  logline=payload['logline'], created_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    state['production_plan'] = record
    await pipeline.save_graph(pipeline.graph)
    who = '编剧亲自排的' if source == 'agent' else '模型不可用，按剧本字段兜底'
    await pipeline.log(
        f'编剧排产（{who}）：{len(record["backgrounds"])} 个背景 · {len(record["poses"])} 个立绘 · '
        f'{len(record["voice"]["order"])} 个配音批次')
    return record


REVIEW_SYSTEM = (
    '你是编剧兼总导演，正在验收美术交上来的图。逐项判断画面描述有没有覆盖你的计划：\n'
    '- pass：描述覆盖了 acceptance 里的关键要素；\n'
    '- redo：没覆盖或有明显错位 —— 给一条更具体的 brief（写清缺什么、怎么改），下游会拿它重画；\n'
    '- skip：这一项这次先不要（说明原因，比如图已经反复失败）。\n'
    '判断要针对具体要素，不要凭「看起来还行」。只输出 JSON：'
    '{"verdicts":[{"key":"","action":"pass|redo|skip","brief":"","reason":""}],"notes":""}'
)


def _normalize_review(raw, keys):
    """把模型返回的质检结果规整成固定结构，缺字段补默认值。"""
    verdicts, issues = [], []
    raw = raw if isinstance(raw, dict) else {}
    for item in raw.get('verdicts') or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip()
        if key not in keys:
            continue
        action = str(item.get('action') or '').strip().lower()
        if action not in ('pass', 'redo', 'skip'):
            issues.append(f'{key} 的 action 不认识：{action}')
            continue
        brief = _clip(item.get('brief'), BRIEF_LIMIT)
        if action == 'redo' and not brief:
            issues.append(f'{key} 要求重画但没给新描述')
            continue
        verdicts.append(dict(key=key, action=action, brief=brief,
                             reason=_clip(item.get('reason'), 120)))
    return verdicts, issues


async def review_art(pipeline, kind, plan, outcomes, failures, history):
    """把「计划 vs 实际交付」交回导演验收。

    kind: backgrounds / poses
    outcomes: [{key, prompt, asset, provider, generated, reused, node_count}]
    failures: [{key, error, message}]
    history:  上一轮已要求的重画 [{key, brief}]，避免导演重复给同一句描述
    返回 {verdicts:[{key,action,brief,reason}], notes, source}
    """
    planned = {item['key']: item for item in plan.get(kind) or []}
    keys = {item['key'] for item in outcomes} | {item['key'] for item in failures}
    keys &= set(planned)
    if not keys:
        return dict(verdicts=[], notes='', source='empty')
    brief = {
        'kind': '背景' if kind == 'backgrounds' else '角色立绘',
        'plan': [dict(key=item['key'], brief=item['brief'], acceptance=item['acceptance'])
                 for item in plan.get(kind) or [] if item['key'] in keys],
        'delivered': [dict(key=row['key'], prompt=_clip(row.get('prompt'), 300),
                           provider=row.get('provider'), generated=row.get('generated'),
                           reused=row.get('reused'), node_count=row.get('node_count'))
                      for row in outcomes if row['key'] in keys],
        'failed': [dict(key=row['key'], error=row.get('error'),
                        message=_clip(row.get('message'), 160))
                   for row in failures if row['key'] in keys],
        'already_asked_redo': history[-1] if history else [],
    }
    provider = pipeline.providers.get('writer')
    if provider is not None:
        messages = [dict(role='system', content=REVIEW_SYSTEM),
                    dict(role='user', content=_encode(brief))]
        for attempt in range(PLAN_ATTEMPTS):
            try:
                raw = await provider.call_json(messages, temperature=0.3, max_tokens=2048)
            except ProviderError as exc:
                await pipeline.log(f'导演验图调用失败（第 {attempt + 1} 次）：'
                                   f'{exc.payload["code"]}', 'warn')
                continue
            verdicts, issues = _normalize_review(raw, keys)
            if verdicts and not issues:
                return dict(verdicts=verdicts, notes=_clip(raw.get('notes'), 200), source='agent')
            if verdicts:
                return dict(verdicts=verdicts, notes=_clip(raw.get('notes'), 200), source='agent')
            messages.append(dict(role='user', content=(
                '这张验收单不合格：' + ('；'.join(issues[:5]) or '没有给出任何 verdict') +
                '。按结构重新只输出 JSON。')))
        await pipeline.log('导演验图改用兜底规则：模型没给出可用验收单', 'warn')
    # 兜底：只处理「真的失败了」这件事，不凭文本猜画得好不好。
    verdicts = [dict(key=row['key'], action='pass', brief='', reason='已生成，导演不可用未做验收')
                for row in outcomes if row['key'] in keys]
    asked = {item.get('key') for item in (history[-1] if history else [])}
    for row in failures:
        if row['key'] not in keys:
            continue
        retried = row['key'] in asked
        verdicts.append(dict(key=row['key'], action='skip' if retried else 'redo',
                             brief='' if retried else (planned[row['key']]['brief']),
                             reason='重画一次仍失败' if retried else '生成失败，重画一次'))
    return dict(verdicts=verdicts, notes='模型不可用，按失败与否兜底', source='fallback')


async def run_writer(pipeline, tools):
    """writer 阶段的入口（被 pipeline 调用的薄壳）。"""
    return await WriterLoop(pipeline, tools).run()
