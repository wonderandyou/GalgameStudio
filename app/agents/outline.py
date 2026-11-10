"""由代码构造全图拓扑，再分批请模型填写剧情文案。"""

import json
import re
from collections import Counter

from ..config import MOCK
from ..engine.skeleton import ENDING_KINDS, Skeleton, SkeletonNode, validate_skeleton
from ..providers.base import ProviderError
from ..providers.fallback import INVALID_MODEL_OUTPUT


OUTLINE_BATCH_SIZE = 20
# §4.3 生成器参数默认值：先算数再建图，I9–I11 由 validate_skeleton 对新骨架强制。
CHAPTERS = 9
CHOICE_DENSITY = 0.06
BRANCH_LEN = (3, 6)
REJOIN_MAX = 10
DEPTH_MAX = 2
ENDINGS = 4
TEMPLATE_VERSION = 2
OUTLINE_ATTEMPTS = 3  # 首次尝试加两次重试，只重试当前批
FALLBACK_WARNING_PREFIX = '骨架文案占位：'

ENDING_TEXT = {
    'true': ('真结局', '说明来龙去脉', '两人把信里的真相说清，决定一起承担这件事留下的后果'),
    'good': ('好结局', '约定再见', '两人说好下次见面再把话讲完，离开时已能放下过去的误会'),
    'bad': ('坏结局', '转身离开', '两人最终没有说出各自的隐情，带着那封没打开的信各自离开'),
    'alt': ('分歧结局', '按自己的方式继续', '他们说清了彼此的打算，选择在这条街分开，各自试着重新开始'),
}
SPINE_BEATS = (
    '循着眼前的线索向前找，两人都没有提起上一次见面时的争执',
    '两人对照手里的东西，发现其中一个细节与最初听到的说法不同',
    '对方几次想开口又停下来，最后指着灯下的一处痕迹说出了疑虑',
    '两人沿着刚找到的线索继续走，重新想起此前没有问出口的问题',
)
ROUTE_TEXT = (
    '沿着左侧的线索往下查，眼前的细节让两人不得不重新考虑先前的推断',
    '顺着另一条线索核对，之前被忽略的话在两人心中有了不同的意思',
)

OUTLINE_SYSTEM = (
    '你负责给视觉小说填写剧情骨架的文案。按给定槽位的顺序返回一个 JSON 对象，'
    '每个节点原样引用槽位的 ref，填入 1 到 8 字的 title、20 到 40 字的 summary。'
    'choice 槽位按 hint 中的选项意图依次填写 labels。剧情沿用需求、人物设定及前情，'
    '不同路线按 hint 处理分歧或汇合。每批用 handoff 交接剧情与待回收伏笔。'
    '第一批可提供一句话 logline 和新角色。look 写明发型发色、眼睛、常服、身高体型和年龄气质，'
    '使用具体名词和数字，沿用已有角色的外形。'
    '填写文案即可，不返回连线、节点类型或其他结构字段。'
)

OUTLINE_CONTENT_SCHEMA = {
    'type': 'object', 'required': ['nodes'], 'additionalProperties': False,
    'properties': {
        'nodes': {
            'type': 'array',
            'items': {
                'type': 'object', 'required': ['ref', 'title', 'summary'],
                'additionalProperties': False,
                'properties': {
                    'ref': {'type': 'string'},
                    'title': {'type': 'string', 'maxLength': 8},
                    'summary': {'type': 'string', 'maxLength': 40},
                    'labels': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4},
                },
            },
        },
        'logline': {'type': 'string', 'maxLength': 120},
        'handoff': {'type': 'string', 'maxLength': 240},
        'characters': {
            'type': 'array',
            'items': {
                'type': 'object', 'required': ['id', 'name', 'look'], 'additionalProperties': False,
                'properties': {field: {'type': 'string'} for field in
                               ('id', 'name', 'gender', 'look', 'note', 'color')},
            },
        },
    },
}


def _ref(index):
    """全书共用一套引用，后批节点还未生成时也能成为前批出口。"""
    return f'k{index:03d}'


def writer_unavailable(code):
    """区分上游连接故障和内容错误，只有前者可以降级后续写。"""
    return code in {'PROVIDER_UNREACHABLE', 'PROVIDER_HTTP_ERROR', 'PROVIDER_NOT_IMPLEMENTED',
                    'PROVIDER_UNAVAILABLE', 'WRITER_UNAVAILABLE', 'ALL_CHANNELS_FAILED', 'NO_PROVIDER'}


def _ending_count(target_nodes, depth, endings):
    """小图和零分支深度时收窄结局数，保证请求可用给定节点表达；默认 4 个（§4.3）。"""
    if depth == 0:
        return 1
    desired = endings if endings is not None else ENDINGS
    return max(1, min(len(ENDING_KINDS), target_nodes - 2, desired))


def _act_records(nodes):
    """用最终节点的分组生成展示目录，避免把传输批次当成章节。"""
    chapters = {}
    for node in nodes:
        chapter_ids = chapters.setdefault(node.act, {})
        if node.chapter:
            chapter_ids[node.chapter] = None
    return [
        dict(id=f'a{act:02d}', title=f'第 {act} 幕', chapters=list(chapter_ids))
        for act, chapter_ids in sorted(chapters.items())
    ]


def _leg_lengths(diamonds, branch_len):
    """每个侧枝的长度在 branch_len 区间里轮转（3,4,5,6,3,…），可离线复现。"""
    low, high = branch_len
    return [low + index % (high - low + 1) for index in range(diamonds)]


def plan_template(*, target_nodes, depth, endings=None, choice_density=CHOICE_DENSITY,
                  branch_len=BRANCH_LEN, depth_max=DEPTH_MAX):
    """§4.4 先算数，再建图：返回 (结局数, 是否有最终分歧, 菱形数, 各菱形侧枝长度, 主干场景数)。

    形状（§4.2）：主干 ─ choice ─┬─ 侧枝 A（L 个场景）─┐
                                 └─ 侧枝 B（L 个场景）─┴─ 汇合场景 ─ 主干 …
    每个菱形占 1 + 2L + 1 个节点；结局只挂在主干末尾的最终分歧（kind=key）上（§4.5）。
    选择点总数 = round(total × density)，最终分歧也算一个；小图至少允许 1 个。
    """
    ending_count = _ending_count(target_nodes, depth, endings)
    final_choice = int(ending_count > 1)
    budget = target_nodes - 1 - ending_count - final_choice
    diamonds = 0
    if depth and depth_max:
        diamonds = max(0, round(target_nodes * choice_density) - final_choice)
    low = branch_len[0]
    # 预算不够就先把侧枝压到最短，再减少菱形数 —— 永远不会建完再发现超出目标。
    while diamonds and diamonds * (2 * low + 2) > budget:
        diamonds -= 1
    lengths = _leg_lengths(diamonds, branch_len)
    while sum(2 * length + 2 for length in lengths) > budget:
        longest = max(range(len(lengths)), key=lengths.__getitem__)
        lengths[longest] -= 1
    trunk = budget - sum(2 * length + 2 for length in lengths)
    return ending_count, final_choice, diamonds, lengths, trunk


def build_template(*, target_nodes, depth, endings=None, requirement='',
                   key_branches=None, flavor_branches=None, chapters=CHAPTERS,
                   choice_density=CHOICE_DENSITY, branch_len=BRANCH_LEN,
                   rejoin_max=REJOIN_MAX, depth_max=DEPTH_MAX):
    """用「主干 + 会汇合的侧枝」构造整图：节点数恰好等于 target_nodes，每条侧枝必然汇合。

    key_branches 只决定菱形里有几个标成 key（记旗标、改结局倾向），不改变形状；
    flavor_branches 保留参数兼容旧调用，选择点数量一律由 choice_density 决定。
    """
    if target_nodes < 2 or depth < 0:
        raise ValueError('骨架至少需要两个节点，分支深度不能为负')
    ending_count, final_choice, diamond_count, lengths, trunk = plan_template(
        target_nodes=target_nodes, depth=depth, endings=endings, choice_density=choice_density,
        branch_len=branch_len, depth_max=depth_max)
    key_branches = max(0, key_branches if key_branches is not None else 2)
    key_count = min(diamond_count, max(0, key_branches - final_choice))
    gap, extra = divmod(trunk, diamond_count + 1)
    chapter_count = max(1, min(chapters, target_nodes // 12 or 1))
    act_count = min(3, chapter_count)
    nodes = []

    def append(kind, title, summary, **details):
        """按出现顺序分配唯一 ref；章节按位置均分，幕把章节再均分成至多三段。"""
        chapter = len(nodes) * chapter_count // target_nodes + 1
        act = (chapter - 1) * act_count // chapter_count + 1
        node = SkeletonNode(ref=_ref(len(nodes) + 1), type=kind, title=title,
                            act=act, chapter=f'c{chapter:02d}', summary=summary, **details)
        nodes.append(node)
        return node

    logline = ' '.join(requirement.split())[:120] or '雨夜里，两个人沿着一封信查明往事'
    setting = logline[:12]
    tail = append('start', '序幕', f'围绕{setting}，两个人循着一封信第一次有机会把话说清')
    for index in range(diamond_count + 1):
        for _ in range(gap + (index < extra)):
            sequence = len(nodes) + 1
            scene = append('scene', f'线索{sequence}', SPINE_BEATS[sequence % len(SPINE_BEATS)])
            tail.to.append(scene.ref)
            tail = scene
        if index == diamond_count:
            break
        is_key = (index + 1) * key_count // diamond_count > index * key_count // diamond_count
        kind = 'key' if is_key else 'flavor'
        labels = ['核对来信', '听完口述'] if is_key else ['先开口问', '先听对方说']
        ref = _ref(len(nodes) + 1)
        branch = append('choice', '交换线索' if is_key else '话到嘴边',
                        '手里的线索还缺了一环，两人对下一步该先做什么有了不同想法',
                        branch_kind=kind, labels=labels,
                        # key 菱形记旗标（flag = 选择点 ref + 第几条路线），flavor 只换对白不记。
                        effects=[f'{ref}_{leg + 1}' for leg in range(len(ROUTE_TEXT))]
                        if is_key else None)
        tail.to.append(branch.ref)
        legs = []
        for route_summary in ROUTE_TEXT:
            previous = branch
            for step in range(lengths[index]):
                summary = route_summary if step == 0 else SPINE_BEATS[(index + step) % len(SPINE_BEATS)]
                scene = append('scene', f'细节{len(nodes) + 1}', summary)
                previous.to.append(scene.ref)
                previous = scene
            legs.append(previous)
        tail = append('scene', '重新会面', '两人碰头交换刚才听见的话和找到的细节，终于看出线索之间的联系')
        for last in legs:
            last.to.append(tail.ref)

    if final_choice:
        kinds = ENDING_KINDS[:ending_count]
        decision = append('choice', '最后决定', '真相已摆在眼前，接下来怎样对待那封信，会让两人的关系走向不同结果',
                          branch_kind='key', labels=[ENDING_TEXT[kind][1] for kind in kinds])
        tail.to.append(decision.ref)
        tail = decision
    for kind in ENDING_KINDS[:ending_count]:
        title, _, summary = ENDING_TEXT[kind]
        tail.to.append(append('ending', title, summary, end_kind=kind).ref)
    return Skeleton(nodes=nodes, logline=logline, acts=_act_records(nodes),
                    meta=dict(target_nodes=target_nodes, depth=depth, endings=ending_count,
                              source='mock', template_version=TEMPLATE_VERSION,
                              choice_density=choice_density, rejoin_max=rejoin_max,
                              depth_max=depth_max, branch_len=list(branch_len)))


def mock_skeleton(*, target_nodes, depth, endings=None, requirement='', **shape):
    """离线使用模板自带的文案，保留原有 MOCK 输出。"""
    skeleton = build_template(target_nodes=target_nodes, depth=depth, endings=endings,
                              requirement=requirement, **shape)
    return validate_skeleton(skeleton, target_nodes=target_nodes, depth=depth)


def _text(value, limit):
    """截取可安全写进 JSON 的非空文案。"""
    if not isinstance(value, str):
        return ''
    return value.encode('utf-8', errors='ignore').decode('utf-8').strip()[:limit].rstrip()


def _new_cast(value, characters):
    """只保留可交给角色工具的新条目，避免模型改写用户已有角色。"""
    seen = {character['id'] for character in characters}
    available = max(0, 8 - len(seen))
    result = []
    for item in value if isinstance(value, list) else ():
        if len(result) == available:
            break
        if not isinstance(item, dict):
            continue
        cid, name = item.get('id'), _text(item.get('name'), 40)
        if (not isinstance(cid, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', cid)
                or cid in seen or not name):
            continue
        row = dict(id=cid, name=name)
        for field in ('look', 'note'):
            if text := _text(item.get(field), 200):
                row[field] = text
        if item.get('gender') in ('male', 'female', 'other'):
            row['gender'] = item['gender']
        if isinstance(item.get('color'), str) and re.fullmatch(r'#[0-9a-fA-F]{6}', item['color']):
            row['color'] = item['color']
        seen.add(cid)
        result.append(row)
    return result


def _content_slots(nodes):
    """给模型说明剧情功能与选项顺序，保留代码里的目标引用。"""
    by_ref = {node.ref: node for node in nodes}
    arrivals = Counter(target for node in nodes for target in node.to)
    routes = {}
    for choice in nodes:
        if choice.type == 'choice':
            for index, target in enumerate(choice.to, 1):
                while by_ref[target].type == 'scene' and arrivals[target] == 1:
                    routes[target] = f'选择 {choice.ref} 的第 {index} 条路线'
                    target = by_ref[target].to[0]
    for node in nodes:
        if node.type == 'choice':
            intents = [f'进入{ENDING_TEXT[by_ref[target].end_kind][0]}'
                       if by_ref[target].type == 'ending' else f'选择路线 {index}'
                       for index, target in enumerate(node.to, 1)]
            kind = '两条路线随后汇合' if node.branch_kind == 'flavor' else '做出重要决定'
            hint = f'{kind}。按顺序填写 {len(intents)} 个选项：{"、".join(intents)}'
        elif node.type == 'ending':
            hint = ENDING_TEXT[node.end_kind][0]
        elif arrivals[node.ref] > 1:
            hint = '汇合此前的不同路线'
        elif node.type == 'start':
            hint = '引出人物和故事的起因'
        else:
            hint = routes.get(node.ref, '在主线上推进一个情节')
        yield dict(ref=node.ref, kind=node.type, hint=f'第 {node.act} 幕，{hint}')


def _apply_content(nodes, raw):
    """仅把当前批的合法文字合入模板，缺失字段逐项沿用模板。"""
    by_ref = {node.ref: node for node in nodes}
    seen = set()
    discarded = duplicates = structure = 0
    fallback_nodes = len(nodes)
    for row in raw['nodes']:
        ref = row.get('ref') if isinstance(row, dict) else None
        if not isinstance(ref, str) or ref not in by_ref:
            discarded += 1
            continue
        if ref in seen:
            duplicates += 1
            continue
        node = by_ref[ref]
        structure += bool(row.keys() - {'ref', 'title', 'summary', 'labels'})
        title, summary = _text(row.get('title'), 8), _text(row.get('summary'), 40)
        labels = row.get('labels')
        choice_labels = [_text(label, 40) for label in labels] if (
            node.labels and isinstance(labels, list) and len(labels) == len(node.labels)
        ) else []
        if not (title or summary or any(choice_labels)):
            continue
        seen.add(ref)
        node.title = title or node.title
        node.summary = summary or node.summary
        if choice_labels:
            node.labels = [label or original for label, original in zip(choice_labels, node.labels)]
        if title and summary and (node.labels is None or choice_labels and all(choice_labels)):
            fallback_nodes -= 1
    return len(seen), fallback_nodes, discarded, duplicates, structure


async def _model_skeleton(pipeline, provider, skeleton, *, characters, requirement):
    """分批填文案，失败只影响该批，服务不可用时保留已写好的批次。"""
    nodes = skeleton.nodes
    slots = list(_content_slots(nodes))
    warnings = skeleton.meta.setdefault('structure_warnings', [])
    system = dict(role='system', content=OUTLINE_SYSTEM + '\n返回 JSON Schema：'
                  + json.dumps(OUTLINE_CONTENT_SCHEMA, ensure_ascii=False))
    batch_count = (len(nodes) + OUTLINE_BATCH_SIZE - 1) // OUTLINE_BATCH_SIZE
    size, extra = divmod(len(nodes), batch_count)
    offset, handoff, new_cast = 0, '', []
    for batch in range(1, batch_count + 1):
        stop = offset + size + (batch <= extra)
        batch_nodes = nodes[offset:stop]
        context = dict(
            requirement=requirement, logline=skeleton.logline, prior_story=handoff,
            batch=batch, batches=batch_count, characters=characters + new_cast,
            slots=slots[offset:stop],
        )
        offset = stop
        for attempt in range(OUTLINE_ATTEMPTS):
            try:
                raw = await provider.call_json(
                    [system, dict(role='user', content=json.dumps(context, ensure_ascii=False))],
                    temperature=0.4, max_tokens=8192,
                )
                if not isinstance(raw, dict) or not isinstance(raw.get('nodes'), list):
                    raise ValueError('回复必须包含 nodes 列表')
                applied, fallback, discarded, duplicates, structure = _apply_content(batch_nodes, raw)
                if not applied:
                    raise ValueError('没有当前批可用的节点文案')
            except ProviderError as exc:
                code = exc.payload['code']
                if writer_unavailable(code):
                    skeleton.meta['degraded_reason'] = _text(str(exc), 1000) or code
                    warnings.append(f'{FALLBACK_WARNING_PREFIX}从第 {batch} 批起模型不可用')
                    return
                if code not in INVALID_MODEL_OUTPUT:
                    raise
                feedback = _text(str(exc), 1000) or code
            except ValueError as exc:
                feedback = _text(str(exc), 1000) or '回复格式无效'
            else:
                skeleton.meta['source'] = 'model'
                if duplicates or discarded or structure:
                    warnings.append(
                        f'骨架第 {batch} 批忽略 {duplicates} 条重复引用、'
                        f'{discarded} 条批外或无效记录和 {structure} 条结构字段')
                if fallback:
                    warnings.append(f'{FALLBACK_WARNING_PREFIX}第 {batch} 批有 {fallback} 个节点保留了模板文字')
                if batch == 1:
                    skeleton.logline = _text(raw.get('logline'), 120) or skeleton.logline
                    new_cast = _new_cast(raw.get('characters'), characters)
                    if new_cast:
                        skeleton.meta['characters'] = new_cast
                recent = '；'.join(node.summary for node in batch_nodes if node.summary)
                handoff = _text(raw.get('handoff'), 240) or recent[-240:]
                break

            await pipeline.log(f'骨架第 {batch} 批第 {attempt + 1} 次文案未采用：{feedback}', 'warn')
            context['previous_error'] = _text(feedback, 400)
        else:
            warnings.append(f'{FALLBACK_WARNING_PREFIX}第 {batch} 批无可用回复，使用模板文字')


def template_params(params):
    """把生成参数映射到 build_template；没给的用 §4.3 默认值。

    flavor_branches 只对 legacy 分章模式有意义，骨架模式的选择点数一律由 choice_density 决定。
    """
    shape = dict(key_branches=params.get('key_branches'))
    for name in ('chapters', 'choice_density', 'rejoin_max', 'depth_max'):
        if params.get(name) is not None:
            shape[name] = params[name]
    if params.get('branch_len') is not None:
        shape['branch_len'] = tuple(params['branch_len'])
    return shape


async def generate_skeleton(pipeline, tools, *, target_nodes, depth, endings=None, requirement):
    """先定整图结构，再合入可用文案并校验，模型始终只提供内容。"""
    if endings is None:
        endings = pipeline.params.get('endings')
    skeleton = build_template(
        target_nodes=target_nodes, depth=depth, endings=endings, requirement=requirement,
        **template_params(pipeline.params),
    )
    validate_skeleton(skeleton, target_nodes=target_nodes, depth=depth)
    provider = None if MOCK or pipeline.mock else pipeline.providers.get('writer')
    if provider is None:
        return skeleton
    characters = [
        {key: character[key] for key in ('id', 'name', 'look', 'gender') if key in character}
        for character in tools.graph['characters']
    ]
    await _model_skeleton(pipeline, provider, skeleton, characters=characters, requirement=requirement)
    return validate_skeleton(skeleton, target_nodes=target_nodes, depth=depth)
