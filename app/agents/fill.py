"""读取冻结骨架和相邻剧情生成台词，持久化由受限的填充工具统一负责。"""

import json
from collections import defaultdict

from ..config import MOCK
from ..providers.base import ProviderError
from ..providers.fallback import INVALID_MODEL_OUTPUT
from .mock_dialogue import mock_lines
from .tool_schema import FILL_NODE_SCHEMA, MAX_NODES_PER_CALL


MOODS = ('平静', '微微一笑', '低头', '难过', '惊讶')
STANCES = ('stand', 'sit', 'lean', 'walk', 'stand')
PERIOD_TO_COMMA = str.maketrans('。．.', '，，，')
FILL_SYSTEM = (
    '你是视觉小说台词编剧，剧情结构已经冻结。按给出的梗概、前后剧情和角色写台词。'
    '只返回包含 nodes 的 JSON 对象，每个节点只允许 id、lines、bg_prompt 和 chars。'
    '所有输入 id 都应恰好出现一次，不能添加节点，也不能输出标题、结构、choices 或边。'
    '每个节点写 1 到 6 句，每句 8 到 40 字，不写句号，分别填说话人 who 与 text。'
    'who 使用已知角色的 id，旁白用空串。先写可感知的动作，再用对白补出人物意图，'
    '避免逐字复述梗概。根据入口和后续梗概衔接情节，汇合场景须接得上所有入口。'
    'bg_prompt 只写地点、时间、天气、光线与气氛，相同地点和时段沿用同一段描述。'
    'chars 可省略。若需改表情或姿态，只能指定该节点已有角色的 id、mood、stance。'
    '同一角色保持外貌，stance 只能是 stand、sit、lean、lie、walk。'
)


def mock_fill(tools, nodes):
    """用正式编号挑选稳定台词，重新分批或断点重跑时仍可复现。"""
    updates = []
    for node in nodes:
        node_id = node['id']
        index = int(node_id[1:]) - 1
        group, offset = divmod(index, MAX_NODES_PER_CALL)
        lines = mock_lines(group + 1, offset, node_type=node['type'], end_kind=node.get('end_kind'))
        summary = tools.node_meta.get(node_id, {}).get('summary', '').strip()
        summary = summary.translate(PERIOD_TO_COMMA).strip('，')[:40]
        who = node['chars'][0]['id'] if node['chars'] else ''
        variant = index % len(MOODS)
        updates.append(dict(
            id=node_id,
            lines=[dict(who='', text=summary if len(summary) >= 8 else lines[0]),
                   dict(who=who, text=lines[1])],
            bg_prompt='雨夜的窗边，室内灯光微弱',
            chars=[dict(id=pose['id'], mood=MOODS[variant], stance=STANCES[variant])
                   for pose in node['chars']],
        ))
    return updates


def _fill_context(tools, nodes, by_id):
    """同时给出入口对白和出口梗概，让分段填充延续已经发生的情节。"""
    selected = {node['id'] for node in nodes}
    previous = defaultdict(list)
    for node in by_id.values():
        for choice in node.get('choices') or []:
            if choice['to'] in selected:
                previous[choice['to']].append((node, choice['label']))

    def brief(node):
        """始终从骨架取梗概，已完成台词只作为衔接依据。"""
        return tools.node_meta.get(node['id'], {}).get('summary') or node['title']

    rows = []
    for node in nodes:
        entries = previous[node['id']]
        rows.append(dict(
            id=node['id'], type=node['type'], title=node['title'], summary=brief(node),
            branch_kind=node.get('branch_kind'), end_kind=node.get('end_kind'),
            bg_prompt=node['bg']['prompt'],
            chars=[{key: pose[key] for key in ('id', 'mood', 'stance')} for pose in node['chars']],
            previous=[dict(id=parent['id'], summary=brief(parent), option=label,
                           background=parent['bg']['prompt'] if parent['status'] == 'done' else '',
                           last_line=parent['lines'][-1]['text'] if parent['status'] == 'done' else '')
                      for parent, label in entries[:4]],
            previous_count=len(entries),
            following=[dict(id=choice['to'], option=choice['label'], summary=brief(by_id[choice['to']]))
                       for choice in node.get('choices') or []],
        ))
    return dict(
        requirement=tools.body.get('requirement', ''),
        logline=(tools.state.get('skeleton') or {}).get('logline', ''),
        characters=[{key: character[key] for key in ('id', 'name', 'look', 'gender') if key in character}
                    for character in tools.graph['characters']],
        nodes=rows,
    )


async def fill_nodes(tools, node_ids, *, feedback=''):
    """生成一次内容补丁，让写入工具统一控制结构校验和内容重试的次数。"""
    by_id = {node['id']: node for node in tools.graph['nodes']}
    nodes = [by_id[node_id] for node_id in node_ids]
    provider = None if MOCK else tools.providers.get('writer')
    if provider is None:
        return mock_fill(tools, nodes)
    schema = dict(type='object', additionalProperties=False, required=['nodes'],
                  properties=dict(nodes=dict(type='array', items=FILL_NODE_SCHEMA)))
    messages = [
        dict(role='system', content=FILL_SYSTEM + '\n返回 JSON Schema：'
             + json.dumps(schema, ensure_ascii=False)),
        dict(role='user', content=json.dumps(_fill_context(tools, nodes, by_id), ensure_ascii=False)),
    ]
    if feedback:
        messages.append(dict(role='user', content=feedback + '\n请保留所有节点 ID，修正内容后重新输出'))
    try:
        # 一批最多 20 个节点、每个节点数行台词，6144 容易被截断
        # （骨架那边就踩过：写到 "act": 1, 直接断，JSON 不闭合）。
        raw = await provider.call_json(messages, temperature=0.6, max_tokens=8192)
    except ProviderError as exc:
        if exc.payload['code'] not in INVALID_MODEL_OUTPUT:
            raise
        raise ProviderError('FILL_INVALID', str(exc), 'writer.fill') from exc
    if isinstance(raw, dict):
        if raw.keys() - {'nodes'}:
            from .tools import ToolInputError
            raise ToolInputError('SKELETON_FROZEN', '填充响应试图提交结构字段',
                                 '只返回 nodes 中指定节点的内容补丁')
        if isinstance(raw.get('nodes'), list):
            return raw['nodes']
    raise ProviderError('FILL_INVALID', '填充响应必须是包含 nodes 列表的 JSON 对象', 'writer.fill')
