"""Agent 工具和随图原子提交的工作状态，保证续写读到的是同一份骨架与台词。"""
import asyncio
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import sys
import wave

from pydantic import ValidationError

from ..asset_records import read_records
from ..assets.cutout import has_alpha, remove_white_background
from ..config import LIBRARY, MOCK, ROOT
from .. import ui_presets
from ..contract import Graph, Node
from ..engine.checkpoint import Checkpoints
from ..engine.export_mock import export_game
from ..engine.contracts import stage_report
from ..engine.graph import (GraphRejected, apply_graph, edges_from_nodes, public_graph, safe_asset,
                            start_id, validate_graph)
from ..engine.journal import Journal
from ..engine.skeleton import BACKGROUND_PLACEHOLDER, Skeleton, validate_skeleton
from ..mock_data import png, silent_wav
from ..providers.base import ProviderError, audio_seconds, redact
from ..providers.image import MAX_REFERENCE_BYTES, MAX_REFERENCE_IMAGES
from ..store import atomic_bytes, atomic_json
from ..assets.library import BUDGET, Library, base_key, bg_key, mood_of, pose_key, stance_of
from .tool_schema import MAX_NODES_PER_CALL, TOOLS


def success(**values):
    """构造一个统一的工具成功返回。"""
    return dict(ok=True, **values)


def failure(error, message, hint, **values):
    """构造一个统一的工具失败返回（带 hint 告诉模型怎么改）。"""
    return dict(ok=False, error=error, message=message, hint=hint, **values)


class ToolInputError(ValueError):
    def __init__(self, code, message, hint):
        """记下错误码、人话说明和修复提示。"""
        super().__init__(message)
        self.code, self.hint = code, hint


def _need(condition, code, message, hint):
    """断言式校验：条件不成立就抛 ToolInputError，并带上修复提示。"""
    if not condition:
        raise ToolInputError(code, message, hint)


_TYPES = dict(object=dict, array=list, string=str, integer=int, boolean=bool, number=float)
_FILL_ATTEMPTS = 3
_SCHEMAS = {
    agent: {tool['function']['name']: tool['function']['parameters'] for tool in specs}
    for agent, specs in TOOLS.items()
}


def _reachable_from(entries, successors):
    """从若干入口做 BFS，返回所有能走到的节点集合。

    `successors` 是 {节点: [后继...]}；缺边的节点按「没有后继」处理（用 .get）。
    只沿 choices 展开，不看 edges —— 调用方传进来的都是 choices 展开后的邻接表。
    """
    reachable = set(entries)
    queue = deque(reachable)
    while queue:
        for target in successors.get(queue.popleft(), ()):
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    return reachable


def _duplicate_choice_detail(choices):
    """找出「多个选项指向同一节点」的情况，返回 (重复的目标, 可读的选项清单)。

    两个选项指向同一个节点等于假分支：玩家长按 A 还是按 B 结果一样，
    但作者会以为自己写了分支。所以这条要单独报，并且把每个选项的文字和
    目标都列出来，模型才知道该改哪一个（只报「有重复」它会瞎改）。

    返回的两段直接拼进报错文案：
        dup  → "n12、n13"           （去重后排序，方便阅读）
        shown → "「走吧」→n12 / 「留下」→n12"
    """
    seen, repeats = set(), []
    for choice in choices:
        target = choice['to']
        if target in seen:
            repeats.append(target)
        seen.add(target)
    dup = '、'.join(sorted(set(repeats)))
    shown = ' / '.join(f'{c.get("label") or "（无标签）"}→{c["to"]}' for c in choices)
    return dup, shown


def _foreign_entries(node, selected, allowed_before):
    """看一个「本章以外」的节点，把它接进本章的出口分成合法/非法两类。

    合法 = 来源在上一章（allowed_before）且是起点或普通场景 —— 下一章只能从
    上一章的收尾场景接进来。其余任何接进本章的边都是野的。

    返回 (合法入口目标列表, [(目标, 来源), ...] 非法清单)。
    单独抽出来是因为原来那段是「循环套循环套两层 if」，五层深，
    真出问题时根本没法一眼看出走的是哪个分支。
    """
    entries, illegal = [], []
    for choice in node.get('choices') or []:
        target = choice['to']
        if target not in selected:
            continue
        if node['id'] in allowed_before and node['type'] in ('start', 'scene'):
            entries.append(target)
        else:
            illegal.append((target, node['id']))
    return entries, illegal


def _cycle_path(nodes, edges):
    """在图里找一条环，返回路径（首尾同一节点）；无环返回 None。

    为什么要有这个：`connect_nodes` 连边时会挡成环，但 `write_nodes` 是
    一次能写多条 choice 分支的唯一入口，那条路径**没有**校验 —— 真模型实测
    把 choice 的分支连回前面的节点，写成 3~4 节点的环，白烧 80 次调用额度后
    才在 validate 阶段报「剧情图有环」（主人看到的那条报错）。
    改成提交前就挡：错一次就告诉它哪条边不行，别让它把额度耗光。

    先 Kahn 拓扑剥掉所有不在环上的点，剩下的必在环上或环的上游，
    再顺着走一定能撞到环 —— 这样不用递归，488 节点也不会爆栈。
    """
    ids = [n['id'] for n in nodes]
    adj = {nid: [] for nid in ids}
    indegree = {nid: 0 for nid in ids}
    for edge in edges:
        a, b = edge.get('from'), edge.get('to')
        if a in adj and b in adj:
            adj[a].append(b)
            indegree[b] += 1

    queue = deque(nid for nid in ids if indegree[nid] == 0)
    peeled = set()
    while queue:
        cur = queue.popleft()
        peeled.add(cur)
        for nxt in adj[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    rest = [nid for nid in ids if nid not in peeled]
    if not rest:
        return None

    # 剩下的点包含「环上的点」和「指向环的上游点」。上游点顺着走会走进环，
    # 但如果从上游点起步靠「一直走第一个下游」可能卡在死路上（实测返回过 n24
    # 这种只有单点的假路径）。所以在这批点上做迭代 DFS：DFS 一定能撞到回边，
    # 撞到时 path 里从那次访问开始的片段就是环。
    rest_set = set(rest)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in rest}
    for root in rest:
        if color[root] != WHITE:
            continue
        color[root] = GRAY
        stack = [(root, iter(adj.get(root, [])))]
        path = [root]
        while stack:
            current, iterator = stack[-1]
            advanced = False
            while True:
                try:
                    nxt = next(iterator)
                except StopIteration:
                    break
                if nxt not in rest_set:
                    continue
                if color[nxt] == GRAY:
                    start = path.index(nxt) if nxt in path else 0
                    return path[start:] + [nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(adj.get(nxt, []))))
                    path.append(nxt)
                    advanced = True
                    break
            if not advanced:
                color[current] = BLACK
                stack.pop()
                path.pop()
    return None


def _check_args(value, schema, field='arguments', *, extra_code=None):
    """按工具 schema 校验模型传来的参数（类型、必填、取值范围）。"""
    kind = schema['type']
    if type(value) is not _TYPES[kind]:
        return f'{field} 必须是 {kind}'
    if 'enum' in schema and value not in schema['enum']:
        return f'{field} 必须从 {schema["enum"]} 中选取'
    if kind == 'object':
        if any(not isinstance(key, str) for key in value):
            return f'{field} 的字段名必须是字符串'
        props = schema['properties']
        missing = set(schema.get('required', [])) - value.keys()
        extra = value.keys() - props.keys()
        if extra_code:
            _need(not extra, extra_code, f'{field} 尝试写入未授权字段 {sorted(extra)}',
                  '只提交已有节点的台词、背景提示词或在场角色的表情和姿态')
        if missing or extra:
            return f'{field} 缺少字段 {sorted(missing)}，额外字段 {sorted(extra)}'
        children = ((f'{field}.{key}', child, props[key]) for key, child in value.items())
    elif kind == 'array':
        minimum = schema.get('minItems', 0)
        maximum = schema.get('maxItems')
        if len(value) < minimum:
            return f'{field} 至少需要 {minimum} 条'
        if maximum is not None and len(value) > maximum:
            return f'{field} 最多接受 {maximum} 条'
        children = ((f'{field}[{i}]', child, schema['items']) for i, child in enumerate(value))
    else:
        return None
    return next((error for path, child, spec in children
                 if (error := _check_args(child, spec, path, extra_code=extra_code))), None)


def _skeleton_groups(skeleton):
    """投影出画布仍使用的幕和章节视图，连接关系始终由全图骨架决定。"""
    declared = {act.get('id'): act.get('title') for act in skeleton.acts
                if isinstance(act.get('id'), str)}
    acts = {}
    groups = {}
    metadata = {}
    used_ids = set()
    for index, node in enumerate(skeleton.nodes, 1):
        group = (node.act, node.chapter)
        if group not in groups:
            act = acts.get(node.act)
            if act is None:
                aid = f'a{node.act:02d}'
                title = declared.get(aid) or declared.get(f'a{node.act}')
                title = title.strip() if isinstance(title, str) else ''
                act = acts[node.act] = dict(id=aid, title=title or f'第 {node.act} 幕',
                                           chapters=[])
            chapter_id = node.chapter or f'c{len(groups) + 1:02d}'
            while chapter_id in used_ids:
                chapter_id = f'{chapter_id}-{node.act}'
            used_ids.add(chapter_id)
            chapter = groups[group] = dict(
                id=chapter_id, title=act['title'] if not act['chapters'] else node.title,
                beats=node.summary, summary=node.summary, status='pending', node_budget=0,
            )
            act['chapters'].append(chapter)
        chapter = groups[group]
        chapter['node_budget'] += 1
        metadata[f'n{index}'] = dict(
            ref=node.ref, act=node.act, chapter_id=chapter['id'], summary=node.summary,
            bg_key='骨架', pose_prompts={},
        )
    chapters = (chapter for act in acts.values() for chapter in act['chapters'])
    for ordinal, chapter in enumerate(chapters, 1):
        chapter['ordinal'] = ordinal
    return list(acts.values()), metadata


def _topology(nodes):
    """提取随骨架锁定的字段，素材和台词阶段可以复用同一条提交路径。"""
    return [(node['id'], node['type'], node.get('branch_kind'), node.get('end_kind'),
             node.get('choices') or []) for node in nodes]


def _digest(key):
    """把一段文本压成短哈希，用于生成稳定的文件名。"""
    return sha256(key.encode('utf-8')).hexdigest()[:20]


# ---------------- 资源文件名（人可读） ----------------
# 主人原话：「出的图，配的音不要用编号和英文，直接中文那样用户好分辨」。
# 为什么不再用内容哈希当文件名：哈希保证去重，但用户打开文件夹认不出哪张是
# 「雨夜天台」。现在改成「序号 + 中文短语」，去重仍由内容判断（见 _find_by_content）。
#
# 文件名限制（Windows）：不能含 \ / : * ? " < > |，不能以空格或点结尾，
# 不能整名等于 CON/PRN/AUX/NUL/COM1-9/LPT1-9。这些在这里一次性处理掉。
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_NAME_SEQ = re.compile(r'^(\d{2,4})-')
_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}
# 生成短语时这些开头的主语没信息量，去掉更像人能读的名字
_LEAD_WORDS = ('画面', '这张图', '图像', '场景是', '一位', '一个', '一名')


def safe_stem(text, limit=20, fallback='未命名'):
    """把一段中文/英文描述压成能直接当文件名的短语。

    保留中英文与数字，标点和空白压成单个 `-`（`-` 在 Windows 文件名里合法）。
    超过 limit 个字符就截断 —— 太长的名字在资源管理器里会被省略号吃掉。
    """
    raw = _UNSAFE_NAME.sub('', str(text or ''))
    raw = re.sub(r'[\s\u3000]+', '', raw)
    kept = []
    for char in raw:
        if char.isalnum() or char in '_-':
            kept.append(char)
        elif kept and kept[-1] != '-':
            kept.append('-')
    stem = ''.join(kept).strip('-_')
    for word in _LEAD_WORDS:
        if stem.startswith(word) and len(stem) > len(word) + 1:
            stem = stem[len(word):]
            break
    stem = stem[:limit].strip('-_').rstrip('. ')
    if not stem:
        return fallback
    if stem.upper() in _RESERVED_NAMES:
        stem = f'素材-{stem}'
    return stem


def first_clause(text, limit=40):
    """取描述的第一句/第一个分句：文件名要短，不能把整段散文塞进去。"""
    value = str(text or '').strip()
    if not value:
        return ''
    match = re.split(r'[。；;！!？?\n]|，|,', value, maxsplit=1)
    return (match[0] if match else value)[:limit].strip()


def scene_phrase(prompt, limit=20):
    """从生图提示词里取出「画面描述」那一句，当作背景文件名。

    `draft_prompt` 的拼法是「风格前缀。画法规则。画面描述。后缀」，
    画面描述在**最后一段**（背景的后缀为空）。取第一句只会得到「画法赛璐璐」，
    对用户没有任何信息量；取最后一段再切首句，才是「深夜的小出租屋」这种。
    """
    parts = [item.strip() for item in str(prompt or '').split('。') if item.strip()]
    if not parts:
        return ''
    return safe_stem(first_clause(parts[-1], limit * 2), limit)


def portrait_size(width, height, ratio=4 / 7):
    """立绘尺寸：竖长比例，长边跟随项目分辨率但夹在 1024–1344。

    为什么不能直接用项目分辨率：项目是 1920×1080 的**横屏**，立绘按它出图
    就变成一张横着的方图，人物只占中间一小块（主人实测指出）。
    每部作品的分辨率可能不同，所以按「长边」自适应，而不是写死 768×1344：
      1920×1080 → 768×1344（4:7 竖版）
      1280×720  → 728×1280
      720×1280（竖屏项目）→ 用项目自己的宽高，不硬转
    """
    width, height = int(width), int(height)
    if height > width:
        return width, height
    long_side = max(1024, min(1344, max(width, height)))
    short_side = int(round(long_side * ratio / 8)) * 8
    return short_side, long_side



def _is_real_project(root):
    """判断这个项目目录是不是真的在 `ROOT/projects` 下。

    测试会在临时目录里造出同样结构的 `projects/xxx`，
    只按目录名判断会把测试数据写进主人的生产素材库。
    """
    try:
        from .. import settings as app_settings
        Path(root).resolve().relative_to(app_settings.projects_dir().resolve())
        return True
    except ValueError:
        return False


def _library_for(root):
    """选这次该用哪个素材库。

    * 测试进程一律用**项目内部**的临时库 —— 测试跑完就随临时目录一起没了，
      绝不能让 `python -m tests.xxx` 往主人攒的素材库里写假图。
    * 真项目共用全局库，这样第二个项目能白捡第一个的图。
    * 其它情况（脚本、演示）也隔离。

    ⚠ 隔离库必须落在 `root` 内部（`root/.library`），不能用 `root.parent/library`：
    有的测试把 `root` 直接设成 `ROOT/projects`，那样 parent 就是项目根目录，
    正好撞上生产库 `ROOT/library`，测试数据还是会漏进去（踩过）。
    """
    if 'unittest' in sys.modules or 'pytest' in sys.modules:
        return Library(Path(root) / '.library')
    if _is_real_project(root):
        return Library(LIBRARY)
    return Library(Path(root) / '.library')


def _duration(text):
    """按字数估一句台词的中文朗读时长（秒）。"""
    return round(len(text) / 4.5, 3)


# ---------------- 界面预设相关的纯函数 ----------------
def _apply_ui_tweaks(preset, tweaks):
    """在预设上叠一层覆盖（只改配色与排版参数，不动骨架）。"""
    if not tweaks:
        return preset
    data = dict(preset)
    palette = dict(preset['palette'])
    for key in ('accent', 'bg', 'fg'):
        if tweaks.get(key):
            palette[key] = tweaks[key]
    data['palette'] = palette
    layout = dict(preset['layout'])
    if tweaks.get('title_size'):
        layout['title_size'] = int(tweaks['title_size'])
    if tweaks.get('letter_spacing'):
        layout['letter_spacing'] = tweaks['letter_spacing']
    if tweaks.get('menu'):
        layout['menu'] = tweaks['menu']
    data['layout'] = layout
    return data


def _write_text_file(path, text):
    """写文本文件，父目录自动创建。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target


def _ui_image_refs(html):
    """抓出 HTML 里真正会去加载的图片引用（data URI 不算）。"""
    refs = re.findall(r"url\(['\"]([^'\"]+)['\"]\)", html)
    refs += re.findall(r'src="([^"]+)"', html)
    return [ref for ref in dict.fromkeys(refs) if ref and not ref.startswith('data:')]


def _path_exists(ref):
    """判断一个图片引用能不能真的打开。外链不判（离线环境判不了）。"""
    if ref.startswith(('http://', 'https://')):
        return True
    if ref.startswith('file:///'):
        raw = ref[len('file:///'):]
        return Path(raw.replace('/', '\\')).is_file() if sys.platform == 'win32' else Path(raw).is_file()
    try:
        return Path(ref).is_file()
    except OSError:
        return False


# 生图规则：把 art skill 里的规范落成代码。
# 没 LLM 时（mock / 降级）也要守同一条风格线 ——
# 不能「有模型就讲究，没模型就随便画」。
ART_RULES = {
    'background': dict(
        prefix='场景，无人，无文字',
        suffix='',
        negative=['人物', '角色', '文字', '水印', '签名', '边框', '拼图', '多张'],
        size=(1920, 1080),
    ),
    'character': dict(
        # 只画上半身：带腿的全身像放进播放器里，脚会贴屏幕底边、没有地面托着，
        # 看起来像站在半空（主人实测指出）。这里从生成端就把它卡死。
        prefix='角色立绘，半身像，只画到腰部以上，不画腿、不画脚',
        # 不再要求纯白背景：qwen-image-2.1 系列能直接出带 alpha 的图，
        # 背景怎么说由 generate_image 的 transparent 统一追加（全透明）。
        # 这里写死「纯白」会和它对冲，而且逼着下游非抠图不可（白边就是这么来的）。
        suffix='单人，构图完整不裁切，无文字，无水印，无边框',
        negative=['多个人物', '文字', '水印', '签名', '复杂背景', '裁切', '残肢', '腿', '脚'],
        # 竖长比例（4:7）。实际尺寸由 generate_image 按项目分辨率的**长边**
        # 自适应换算（见 portrait_size），这里只是「默认参考值」。
        size=(768, 1344),
    ),
    'ui': dict(
        prefix='游戏界面，扁平，可读性优先',
        suffix='预留文字区域，不要写死具体文案',
        negative=['乱码', '错别字', '照片质感', '复杂纹理', '水印'],
        size=(2560, 1600),
    ),
    'typography': dict(
        prefix='字体样式与排版层级参考',
        suffix='只给样式，不要生成具体文案',
        negative=['错别字', '乱码', '水印'],
        size=(1600, 900),
    ),
    'fx': dict(
        prefix='氛围特效，尽量单一元素',
        suffix='',
        negative=['人物', '文字', '水印'],
        size=(1920, 1080),
    ),
}


class AgentTools:
    def __init__(self, graph, body, root, providers=None, save_graph=None, save_asset=None,
                 library=None):
        """注入图、角色表、素材库与 provider；node_meta 是节点→章节/ref 的映射。"""
        self.graph = graph
        self.body = body
        self.params = body.get('params', {})
        self.root = Path(root)
        self.providers = providers if providers is not None else {}
        self.save_graph = save_graph or self._save_graph
        self.save_asset = save_asset or self._save_asset
        # 素材库跨项目共用：同一地点、同一角色的图，第二个项目直接用，不重新生成。
        # 测试进程必须隔离，否则「跑一次测试」就会把假图写进主人的素材库。
        self.library = library if library is not None else _library_for(root)
        self.chapter_id = None
        self.lock = asyncio.Lock()
        # 续写设施：检查点管「盘上的图对不对得上」，日志管「崩在哪一步」。
        # 两者都按项目目录隔离，测试用临时目录时不会写到真实项目里。
        self.checkpoints = Checkpoints(self.root)
        self.journal = Journal(self.root)
        # 每个目录下一个可用的序号。初值靠扫目录算一次，之后只递增 ——
        # 不然几百条配音每存一条都要重扫一遍目录。
        self._name_seq = {}

    @property
    def state(self):
        """agent_state 这块可变状态（章节计划、统计、进度）。"""
        return self.graph.get('agent_state', {})

    @property
    def node_meta(self):
        """节点元数据：每个节点属于哪一章、ref、bg_key、立绘提示词。"""
        return self.state.get('nodes', {})

    @property
    def chapters(self):
        """把 agent_state.acts 里所有幕的章节摊平成一个列表。"""
        return [chapter for act in self.state.get('acts', []) for chapter in act['chapters']]

    async def _save_graph(self, value):
        """把图落盘（构造时注入的回调，测试里可换成内存实现）。"""
        await asyncio.to_thread(atomic_json, self.root / 'graph.json', value)

    async def _save_asset(self, relative, data):
        """把素材落盘（同上）。"""
        await asyncio.to_thread(atomic_bytes, safe_asset(self.root, relative), data)

    async def _commit(self, **updates):
        """一次性提交：更新图 / 角色表 / agent_state，然后落盘。"""
        if self.state.get('skeleton') is not None:
            if 'nodes' in updates:
                self._require_mutable_skeleton(_topology(updates['nodes']) == _topology(self.graph['nodes']))
            if 'edges' in updates:
                self._require_mutable_skeleton(updates['edges'] == self.graph['edges'])
            if 'agent_state' in updates:
                self._require_mutable_skeleton(updates['agent_state'].get('skeleton') == self.state['skeleton'])
        # D3 写入门：任何提交都先重算 edges 并查结构不变量 I1–I8；带 skeleton 的图不过就整体拒绝。
        # 被拒时把全部 problems 交给调用方（模型一次看到所有问题）。
        try:
            checked = apply_graph({**self.graph, **updates}, source='agent.commit')
        except GraphRejected as exc:
            raise ToolInputError('GRAPH_REJECTED', str(exc),
                                 '按 problems 逐条修正结构后再提交') from exc
        updates = dict(updates, edges=checked['edges'])
        # 在快照持久化后才替换内存状态，失败的调用不会污染下一轮。
        await self.save_graph({**self.graph, **updates})
        self.graph.update(updates)

    def _require_mutable_skeleton(self, unchanged=False):
        """拦住冻结后仍试图改结构的旧工具，并保护共用的提交入口。"""
        _need(unchanged or self.state.get('skeleton') is None, 'SKELETON_FROZEN',
              '全图骨架已冻结', '用 fill_segment 填已有节点的内容')

    async def dispatch(self, role, name, arguments, *, chapter_id=None):
        """按名字执行一个工具，统一处理参数校验与异常转失败结果。"""
        schema = _SCHEMAS.get(role, {}).get(name)
        if schema is None:
            return failure('TOOL_FORBIDDEN', f'{role} 无权调用 {name}', '选用当前 Agent 的工具白名单')
        if name == 'validate_graph' and isinstance(arguments, dict) and 'chapter_id' in arguments:
            # writer 的公开 schema 只声明了 scope，章节由循环注入。
            arguments = dict(arguments)
            explicit = arguments.pop('chapter_id')
            if not isinstance(explicit, str) or (chapter_id and explicit != chapter_id):
                return failure('INVALID_ARGUMENTS', '校验章节不匹配', '省略 chapter_id，使用当前章节')
            chapter_id = explicit
        # 填充工具需要区分参数错误和越权字段，方法内部统一校验请求与模型回复。
        error = None if name == 'fill_segment' else _check_args(arguments, schema)
        if error:
            return failure('INVALID_ARGUMENTS', error, '按工具参数说明修改后重试')
        try:
            async with self.lock:
                return await getattr(self, name)(arguments, chapter_id or self.chapter_id)
        except ToolInputError as exc:
            return failure(exc.code, str(exc), exc.hint)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = '.'.join(str(part) for part in first['loc'])
            return failure('INVALID_NODE', f'{field}: {first["msg"]}', '按工具说明修正节点字段')
        except ProviderError as exc:
            return failure(exc.payload['code'], self._provider_message(role, exc), '检查项目文件或对应供应商配置后重试')
        except OSError as exc:
            return failure('IO_ERROR', str(exc), '检查项目文件权限和磁盘空间后重试')

    def _provider_message(self, role, exc):
        """取某个 provider 的最近错误信息；没有就返回空串。"""
        provider = self.providers.get(role)
        configs = getattr(provider, 'configs', None) or {role: getattr(provider, 'cfg', {})}
        return redact(exc.payload['message'], configs)

    def _chapter(self, chapter_id):
        """按 id 取章节；找不到抛 404。"""
        for chapter in self.chapters:
            if chapter['id'] == chapter_id:
                return chapter
        raise ToolInputError('CHAPTER_NOT_FOUND', f'章节 {chapter_id!r} 不存在', '先调用 list_chapters 获取章节 id')

    def _nodes(self, chapter_id):
        """取某一章的全部节点（不给 chapter_id 就取全图）。"""
        return [node for node in self.graph['nodes'] if self.node_meta.get(node['id'], {}).get('chapter_id', '') == chapter_id]

    def _chapter_view(self, chapter, count):
        """把章节整理成给模型看的视图（含节点数、状态）。"""
        return dict(chapter, node_count=count)

    def _nearby(self, chapter):
        """取前一章和下一章 —— 跨章连线与章节边界校验都要用。"""
        chapters = self.chapters
        i = chapter['ordinal'] - 1
        previous = chapters[i - 1] if i else None
        following = chapters[i + 1] if i + 1 < len(chapters) else None
        return previous, following

    def _chapter_nodes(self, chapter, previous):
        """取本章节点 + 前一章能被接进来的节点。"""
        local = self._nodes(chapter['id'])
        if previous:
            before = self._nodes(previous['id'])
        else:
            before = [node for node in self.graph['nodes'] if node['type'] == 'start']
        return local, before

    def _require_active(self, chapter_id, scope):
        """确认这一章现在可以写（状态、顺序、当前作用域都合法）。"""
        chapter = self._chapter(chapter_id)
        previous, _ = self._nearby(chapter)
        _need(not scope or chapter_id == scope, 'WRONG_CHAPTER', '本轮只允许写当前章节', '用当前任务中的章节 id')
        _need(chapter['status'] != 'done', 'CHAPTER_DONE', '章节已写完', '使用下一个 pending 或 writing 章节')
        _need(previous is None or previous['status'] == 'done', 'PREVIOUS_INCOMPLETE', '前一章还没完成', '按章节顺序完成前一章')
        return chapter

    def _update_chapter(self, chapter_id, **changes):
        """改某一章的字段，返回新的 acts 列表。"""
        return [dict(act, chapters=[dict(ch, **changes) if ch['id'] == chapter_id else ch for ch in act['chapters']])
                for act in self.state['acts']]

    async def project_brief(self, args, scope):
        """把项目设定整理成给模型看的简报。"""
        return success(requirement=self.body.get('requirement', ''), params=self.params,
                       project=self.graph['project'], chapters_done=sum(c['status'] == 'done' for c in self.chapters),
                       chapters_total=len(self.chapters), node_count=len(self.graph['nodes']))

    async def plan_structure(self, args, scope):
        """规划全书结构：幕、章节、每章节点预算。"""
        self._require_mutable_skeleton()
        _need(not self.chapters, 'ALREADY_PLANNED', '总体结构已保存', '调用 get_structure 继续已有计划')
        acts = args['acts']
        _need(1 <= len(acts) <= 6 and all(act['chapters'] for act in acts), 'INVALID_PLAN',
              '至少有一幕，最多六幕，且每幕都有章节', '为每幕列出章节及其节点预算')
        chapter_specs = [ch for act in acts for ch in act['chapters']]
        target = self.params.get('node_count', 8) - 1
        missing = sum('node_budget' not in ch for ch in chapter_specs)
        assigned = sum(ch.get('node_budget', 0) for ch in chapter_specs)
        remainder = target - assigned
        _need((missing and missing <= remainder <= 20 * missing) or (not missing and remainder == 0),
              'BUDGET_MISMATCH', f'章节预算总和应为 {target}，显式预算当前为 {assigned}',
              '调整各章预算。自动起点占一个节点，常规章节写 12–16 个')
        base, extra = divmod(remainder, missing) if missing else (0, 0)
        saved = []
        ordinal = 0
        for act in acts:
            chapters = []
            for spec in act['chapters']:
                budget = spec.get('node_budget')
                if budget is None:
                    budget = base + int(extra > 0)
                    extra -= 1
                _need(1 <= budget <= 20, 'INVALID_BUDGET', f'章节预算 {budget} 超出 1–20', '将过长的章节拆成两章')
                _need(spec['title'].strip() and spec['beats'].strip(), 'INVALID_PLAN', '章节标题或梗概为空', '为每章填写标题和梗概')
                ordinal += 1
                chapters.append(dict(id=f'c{ordinal:02d}', ordinal=ordinal, title=spec['title'],
                                     beats=spec['beats'], node_budget=budget, status='pending', summary=''))
            saved.append(dict(id=f'a{len(saved) + 1:02d}', title=act['title'], goal=act.get('goal', ''), chapters=chapters))
        minutes = self.params.get('target_minutes', args['target_minutes'])
        await self._commit(agent_state={**self.state, 'logline': args['logline'], 'target_minutes': minutes, 'acts': saved})
        return await self.get_structure({}, scope)

    async def get_structure(self, args, scope):
        """读出已有的全书结构。"""
        return success(planned=bool(self.chapters), logline=self.state.get('logline', ''),
                       target_minutes=self.state.get('target_minutes', self.params.get('target_minutes', 60)),
                       acts=self.state.get('acts', []))

    async def list_chapters(self, args, scope):
        """列出章节及各自状态。"""
        status = args.get('status', 'all')
        counts = Counter(item.get('chapter_id') for item in self.node_meta.values())
        return success(chapters=[self._chapter_view(ch, counts[ch['id']]) for ch in self.chapters
                                 if status == 'all' or ch['status'] == status])

    async def resume_evidence(self, args, scope):
        """续写证据：上次崩在哪、每章真实写了多少、盘上的图和检查点对不对得上。

        只给事实，不下结论 —— 判定由 writer 自己看完再调 set_resume_plan。
        这里每条都是「模型看不见就判不了」的东西：图比检查点少了内容、
        某章标着写作中却一个节点都没有、状态和实际节点数对不上。
        """
        counts = Counter(item.get('chapter_id') for item in self.node_meta.values())
        chapters = [self._chapter_view(ch, counts[ch['id']]) for ch in self.chapters]
        gaps = []
        for chapter in chapters:
            status, nodes = chapter.get('status'), chapter.get('node_count') or 0
            if status == 'writing' and not nodes:
                gaps.append(f"{chapter['id']} 标着写作中但一个节点都没有 —— 可能刚开章就崩了")
            elif status == 'done' and not nodes:
                gaps.append(f"{chapter['id']} 标着已完成但一个节点都没有 —— 记录和实际对不上")
            elif status == 'pending' and nodes:
                gaps.append(f"{chapter['id']} 标着未开始却有 {nodes} 个节点 —— 记录落后于实际")
        # 顺序断裂：前面还有章没写完，后面却有章标着完成。这是「从哪续」最硬的线索。
        # 按顺序号比，不按章节 id 字符串比 —— 超过 99 章以后 'c100' < 'c99'。
        first_open = next((index for index, chapter in enumerate(chapters)
                           if chapter.get('status') != 'done'), len(chapters))
        broken = [chapter['id'] for chapter in chapters[first_open:]
                  if chapter.get('status') == 'done']
        journal = [{key: value for key, value in item.items() if key != 'digest'}
                   for item in self.journal.tail(8)]
        return success(
            checkpoint=self.checkpoints.verify(self.graph),
            chapters=chapters,
            gaps=gaps,
            done_after_open=broken,
            journal=journal,
            note='以上是事实。是否要把某章从已完成退回继续补，由你按 gaps / journal / 检查点判断。',
        )

    async def set_resume_plan(self, args, scope):
        """落「从哪章续、哪几章接着补」的决定。

        redo 的语义是**退回继续补**，不是清空重写 —— 一个节点都不删。
        真要整章重写只能改生成参数触发清空（那条路会先把整图备份一份）。
        """
        self._require_mutable_skeleton()
        ids = [chapter['id'] for chapter in self.chapters]
        resume_from = str(args.get('resume_from') or '')
        _need(resume_from in ids, 'RESUME_UNKNOWN_CHAPTER',
              f'没有章节 {resume_from}', f'可用的章节 id：{ids}')
        asked = [str(item) for item in (args.get('redo') or [])]
        redo = [item for item in asked if item in ids]
        unknown = [item for item in asked if item not in ids]
        acts = []
        for act in self.state.get('acts', []):
            chapters = []
            for chapter in act['chapters']:
                if chapter['id'] in redo and chapter.get('status') == 'done':
                    chapter = dict(chapter, status='writing')
                chapters.append(chapter)
            acts.append(dict(act, chapters=chapters))
        plan = dict(resume_from=resume_from, redo=redo,
                    reason=str(args.get('reason') or '')[:500],
                    at=datetime.now(timezone.utc).isoformat(timespec='seconds'), by='writer')
        await self._commit(agent_state={**self.state, 'acts': acts, 'resume_plan': plan})
        self.journal.append(agent='writer', phase='resume_plan', chapter=resume_from,
                            note=plan['reason'])
        return success(plan=plan, ignored=unknown,
                       note='已记下这个计划，接下来的章节就按它写')

    async def get_chapter(self, args, scope):
        """读一章的详细上下文（节点、边、开放出口、孤岛）—— 写之前必须先调它。"""
        chapter = self._chapter(args['chapter_id'])
        previous, following = self._nearby(chapter)
        local, before = self._chapter_nodes(chapter, previous)
        local_ids = {node['id'] for node in local}
        entry_ids = [c['to'] for node in before for c in node.get('choices') or [] if c['to'] in local_ids]
        details = []
        for node in local:
            meta = self.node_meta[node['id']]
            choices = meta.get('pending_choices', node.get('choices') or [])
            details.append(dict(id=node['id'], ref=meta['ref'], type=node['type'], title=node['title'],
                                end_kind=node.get('end_kind'), bg_key=meta['bg_key'],
                                lines=[line['text'][:20] for line in node['lines']], choices=choices))
        summary = previous['summary'] if previous else ''
        # ★ agent 有「欠出口」的提示（open_exits / frontier），却没有「欠入口」的提示。
        #   实测它写了 n25「修灯」，却忘了把 n24 指向它 —— 这个节点在图上、没入边，
        #   validate_graph 报孤儿，它却不知道该把谁改过来（连着 3 次不过判卡死）。
        #   每次读章节就把「没人指进来的节点」直接列出来，它自己就能发现。
        incoming = set(entry_ids)
        for node in local:
            for choice in node.get('choices') or []:
                incoming.add(choice['to'])
        orphans = [node['id'] for node in local
                   if node['type'] != 'start' and node['id'] not in incoming]
        return success(chapter=dict(self._chapter_view(chapter, len(local)), nodes=details,
                                    prev_summary=summary, is_last=following is None,
                                    entry_id=entry_ids[0] if entry_ids else None,
                                    open_exits=[node['id'] for node in before if node['type'] != 'ending' and not node.get('choices')],
                                    frontier=[node['id'] for node in local if node['type'] == 'scene' and not node.get('choices')],
                                    orphans=orphans,
                                    remaining_budget=max(0, chapter['node_budget'] + 2 - len(local)),
                                    characters=sorted({p['id'] for n in local for p in n['chars']} |
                                                      {line['who'] for n in local for line in n['lines'] if line['who']}),
                                    edges=[edge for edge in self.graph['edges'] if edge['from'] in local_ids or edge['to'] in local_ids]),
                       prev_summary=summary)

    def _resolve(self, value, chapter_id, metadata, by_id):
        """把 ref 或正式 id 解析成正式节点 id；解析不出返回 None。"""
        local = [nid for nid, item in metadata.items() if item.get('chapter_id') == chapter_id and item.get('ref') == value]
        if local:
            return local[0]
        if value in by_id:
            return value
        matches = [nid for nid, item in metadata.items() if item.get('ref') == value]
        return matches[0] if len(matches) == 1 else None

    async def write_skeleton(self, args, scope):
        """将节点、边和续写锚点一起发布，让断点恢复看到的是完整的同一张网。"""
        self._require_mutable_skeleton()
        try:
            skeleton = Skeleton.from_dict(args)
            validate_skeleton(skeleton, target_nodes=self.params.get('node_count'),
                              depth=self.params.get('branch_depth'))
            stub = skeleton.to_graph_stub(self.graph['characters'], self.graph['project'])
            validate_graph(stub, auto_voice=False)
        except ValueError as exc:
            raise ToolInputError('SKELETON_INVALID', str(exc), '修正完整骨架后再提交') from exc

        # 占位图可以先显示在画布上，后续美术仍能凭 pending 和占位路径识别待画素材。
        if not self._asset_exists(BACKGROUND_PLACEHOLDER):
            await self.save_asset(BACKGROUND_PLACEHOLDER, png(64, 36, (35, 50, 75, 255)))
        for character in self.graph['characters']:
            asset = character['ref_image']
            if self._is_placeholder(asset) and not self._asset_exists(asset):
                await self.save_asset(asset, png(32, 64, (125, 86, 155, 255)))

        acts, metadata = _skeleton_groups(skeleton)
        state = dict(self.state, skeleton=skeleton.to_dict(), acts=acts, nodes=metadata,
                     logline=skeleton.logline)
        for key in ('resume_plan', 'writer_published', 'production_plan'):
            state.pop(key, None)
        # 新模板给 key 菱形声明的旗标（graph.flags）随骨架一起落盘，播放器按它初始化。
        extra = dict(flags=stub['flags']) if stub.get('flags') else {}
        await self._commit(nodes=stub['nodes'], edges=stub['edges'], agent_state=state, **extra)
        return success(nodes=len(stub['nodes']), edges=len(stub['edges']), acts=len(acts))

    def _filled_node(self, node, raw, characters):
        """只构造允许写入的字段，避免模型返回的额外键进入图。"""
        nid = node['id']
        lines = []
        for item in raw['lines']:
            speaker, line = item['who'], item['text'].strip()
            _need(not speaker or speaker in characters, 'UNKNOWN_CHARACTER',
                  f'{nid} 的说话人 {speaker!r} 不存在', '用角色表里的 id 或留空表示旁白')
            _need(8 <= len(line) <= 40 and not any(mark in line for mark in '。．.'),
                  'INVALID_DIALOGUE', f'{nid} 的每句台词须为 8–40 字且不含句号',
                  '拆分过长台词，补足太短的台词，删除句号')
            lines.append(dict(who=speaker, text=line, voice=None, dur=_duration(line)))

        poses = {pose['id']: pose for pose in node['chars']}
        changed = set()
        for raw_pose in raw.get('chars', []):
            cid = raw_pose['id']
            _need(cid in poses and cid not in changed, 'SKELETON_FROZEN',
                  f'{nid} 试图添加或重复修改在场角色 {cid!r}', '只能更新该节点已有角色的表情和姿态')
            changed.add(cid)
            pose = dict(poses[cid])
            if 'mood' in raw_pose:
                mood = raw_pose['mood'].strip()
                _need(mood, 'INVALID_MOOD', f'{nid} 的表情描述为空', '填表情描述或省略该字段')
                pose['mood'] = mood
            if 'stance' in raw_pose:
                pose['stance'] = raw_pose['stance']
            poses[cid] = pose

        bg = node['bg']
        if 'bg_prompt' in raw:
            prompt = raw['bg_prompt'].strip()
            _need(prompt, 'EMPTY_PROMPT', f'{nid} 的背景提示词为空', '填写画面描述或省略该字段')
            bg = dict(bg, prompt=prompt)
        return dict(node, lines=lines, bg=bg, chars=list(poses.values()), status='done')

    def _checked_fill_updates(self, raw_nodes, by_id, selected, pending, *, strict=True):
        """对齐输入与响应的节点编号，保证校验失败时整批没有副作用。

        两种来源，两套口径 —— 这是关键：

        **strict=True（默认）—— 调用方显式传了 `nodes`**
            它自己声明「我要填 n1、n2」，那就必须一个不少、一个不错，
            否则就是调用方违约，直接 SKELETON_FROZEN 退回。

        **strict=False —— 模型自动填充（`_generate_fill_updates`）**
            模型没法保证「恰好把这一批 ID 全都写到」。以前也按严格算，
            结果少一个、多一个就整轮报废 —— 主人实测：骨架都成功冻结落盘了，
            填充第一步就死（23:38 / 23:42 两次都这样，距骨架成功只差 10 秒）。
            现在「能用多少用多少」：少给的留下轮继续要，多给/编造的忽略，
            一个都对不上才报 INVALID_FILL（可重试）。

        真正守死的底线两边一样：只能填骨架里已有的节点、不能改结构，
        每句台词仍然走 `_filled_node` 的全套校验。
        """
        if not pending:
            return {}               # 没有待填节点：空更新，不是错误
        supplied = [item['id'] for item in raw_nodes]
        if strict:
            _need(len(supplied) == len(set(supplied))
                  and set(pending) <= set(supplied) <= set(selected),
                  'SKELETON_FROZEN', '填充返回的节点与请求的待填节点不匹配',
                  '按原 ID 填每个待写节点一次')
        payload = {}
        for item in raw_nodes:
            nid = item['id']
            if nid in payload or nid not in by_id:
                continue            # 重复的、骨架里没有的，一律忽略
            payload[nid] = item
        usable = [nid for nid in pending if nid in payload]
        _need(usable, 'INVALID_FILL',
              '填充返回里没有一个能和待填节点对上',
              '按请求里给出的 ID 逐个填写台词，不要改 ID、不要漏整批')
        characters = {character['id'] for character in self.graph['characters']}
        return {nid: self._filled_node(by_id[nid], payload[nid], characters) for nid in usable}

    async def _generate_fill_updates(self, by_id, pending, schema):
        """将可修复的格式和台词问题反馈给编剧，越权和供应商故障直接上报。"""
        from .fill import fill_nodes

        attempts = _FILL_ATTEMPTS if self.providers.get('writer') is not None and not MOCK else 1
        feedback = ''
        for attempt in range(attempts):
            try:
                raw_nodes = await fill_nodes(self, pending, feedback=feedback)
                error = _check_args({'nodes': raw_nodes}, schema, extra_code='SKELETON_FROZEN')
                _need(not error, 'INVALID_FILL', error, '按填充字段格式重新生成台词')
                return self._checked_fill_updates(raw_nodes, by_id, pending, pending,
                                                  strict=False)
            except ToolInputError as exc:
                if exc.code == 'SKELETON_FROZEN' or attempt + 1 == attempts:
                    raise
                feedback = f'{exc}。{exc.hint}'
            except ProviderError as exc:
                if exc.payload['code'] != 'FILL_INVALID' or attempt + 1 == attempts:
                    raise
                feedback = exc.payload['message']

    async def fill_segment(self, args, scope):
        """将一批内容整体校验后提交，已完成节点可重试且不会被覆盖。"""
        _need(self.state.get('skeleton') is not None, 'SKELETON_MISSING',
              '尚未提交全图骨架', '先用 write_skeleton 提交骨架')
        schema = _SCHEMAS['writer']['fill_segment']
        error = _check_args(args, schema, extra_code='SKELETON_FROZEN')
        _need(not error, 'INVALID_ARGUMENTS', error, '按工具参数说明修改后重试')
        _need(('chapter_id' in args) != ('node_ids' in args), 'INVALID_ARGUMENTS',
              '须且只须提供 chapter_id 或 node_ids', '使用一个选择器指定要填充的节点')
        by_id = {node['id']: node for node in self.graph['nodes']}
        if 'chapter_id' in args:
            chapter_id = self._chapter(args['chapter_id'])['id']
            selected = [node['id'] for node in self._nodes(chapter_id)
                        if node['status'] != 'done'][:MAX_NODES_PER_CALL]
        else:
            selected = args['node_ids']
        _need(len(selected) == len(set(selected)) and set(selected) <= by_id.keys(),
              'SKELETON_FROZEN', '请求引用了不存在或重复的节点', '使用骨架中互不重复的正式节点 ID')
        pending = [nid for nid in selected if by_id[nid]['status'] != 'done']
        if 'nodes' in args:
            updates = self._checked_fill_updates(args['nodes'], by_id, selected, pending)
        else:
            updates = await self._generate_fill_updates(by_id, pending, schema) if pending else {}
        if not pending:
            return success(nodes=0, node_ids=[])

        metadata = dict(self.node_meta)
        for nid, updated in updates.items():
            if updated['bg'] is not by_id[nid]['bg']:
                prompt = updated['bg']['prompt']
                # 相同布景复用同一张图，摘要和分支边界不决定素材分组。
                key = f'{safe_stem(first_clause(prompt), 16)}-{_digest(prompt)[:8]}'
                metadata[nid] = dict(metadata[nid], bg_key=key)
        by_id.update(updates)

        complete = Counter(metadata[nid]['chapter_id'] for nid, node in by_id.items()
                           if node['status'] == 'done')
        acts = []
        for act in self.state['acts']:
            chapters = []
            for chapter in act['chapters']:
                done = complete[chapter['id']]
                status = 'done' if done == chapter['node_budget'] else 'writing' if done else 'pending'
                chapters.append(dict(chapter, status=status))
            acts.append(dict(act, chapters=chapters))
        await self._commit(nodes=list(by_id.values()), agent_state=dict(self.state, acts=acts, nodes=metadata))
        # 返回**实际填上的**，不是请求的：模型可能只给了一部分（见 _checked_fill_updates），
        # 谎报完成会让上层漏掉那些还没填的节点，永远补不上。
        filled = list(updates)
        return success(nodes=len(filled), node_ids=filled)

    async def write_nodes(self, args, scope):
        """把一批节点写进当前章，供旧流程兼容已有章节计划。

        一次调用要走完五个阶段，顺序不能换：

        1. **ref 骨架**：先校验 ref 唯一且非空，再给「本章还没有的 ref」分配正式 id
           （n{max+1} 起）。ref 是模型自己的章内编号，id 是全局正式编号，
           两者都要保留 —— 模型认 ref，日志和报错认 id。
        2. **预算闸**：本章预算 + 2 个弹性位。弹性位是故意的：预算写满后模型
           往往还缺一个汇合点或出口，硬卡会逼它反复重试同一批 ref，最后被判卡死。
           但**回报给模型的剩余额度必须是真实的**（含弹性位），否则它以为「还能写 0 个」，
           只好把两条选项指向同一个节点（2026-11-08 踩过）。
        3. **逐节点构建**：类型推断（带 choices 就是 choice、带 end_kind 就是 ending，
           不因为 type 写错就判死）、背景键、立绘、台词时长。
           已经出过真图且 bg_key 没变的，**保留那张图**，不重置成占位。
        4. **前向 ref 解析**：一批里可以先引用后出现的 ref。没解析完的继续揣着
           `pending_choices`，等下一批；**没解析完不要改 choices**，否则出口编号会跳。
        5. **落盘前拦重复目标**：两条选项指向同一个节点 = 假分支。写入阶段不拦的话，
           validate 只会说「分支须有 2–4 个不同目标」却不说是谁，模型改三轮
           同一批内容，然后被判卡死（实测 p_9eae63）。
        """
        self._require_mutable_skeleton()
        chapter_id = args['chapter_id']
        chapter = self._require_active(chapter_id, scope)
        inputs = args['nodes']
        refs = [raw['ref'] for raw in inputs]
        _need(all(ref.strip() and ref == ref.strip() for ref in refs) and len(set(refs)) == len(refs),
              'DUPLICATE_REF', '本批 ref 有重复或为空', '每个节点使用非空的唯一章内 ref')
        graph_nodes = self.graph['nodes']
        by_id = {n['id']: n for n in graph_nodes}
        metadata = dict(self.node_meta)
        local = {item['ref']: nid for nid, item in metadata.items() if item.get('chapter_id') == chapter_id}
        new = [ref for ref in refs if ref not in local]
        # 预算给一点弹性（+2）。原先是硬卡，实测 writer 在预算写满后如果结构校验
        # 还不过（缺汇合点、缺出口），就想加节点补，被硬拒 → 反复重试同一批 ref，
        # 一个任务里撞了 8 次「超出本章预算」，最后被停滞检测判卡死（主人实测）。
        # 预算是估算不是法律，留 2 个弹性位足够收尾，又不会失控。
        # ★ 2026-11-08 再踩：弹性位是允许的，但**回报给 writer 的剩余额度没算上那 2 个**，
        #   它以为「还可新建 0 个」→ 分支的另一条腿建不出来 → 只好把两条选项都指向
        #   同一个节点 → 落盘非法图 → 判卡死。剩余额度必须报真实可用的数。
        allowance = chapter['node_budget'] + 2
        over = len(local) + len(new) - chapter['node_budget']
        remain = allowance - len(local)
        _need(over <= 2, 'CHAPTER_BUDGET', '新节点超出本章预算',
              (f'本章预算 {chapter["node_budget"]} 加 2 个弹性位已全部用完：'
               '不要再写新 ref，改用已有 ref 修改内容，然后调用 finish_chapter 收尾')
              if remain <= 0 else
              f'本章还可写 {remain} 个新 ref，修改节点请使用现有 ref')
        next_id = max((int(nid[1:]) for nid in by_id if re.fullmatch(r'n[0-9]+', nid)), default=0) + 1
        local.update({ref: f'n{next_id + i}' for i, ref in enumerate(new)})
        characters = {c['id']: c for c in self.graph['characters']}
        stamp = datetime.now(timezone.utc).isoformat()
        writer = self.providers.get('writer')
        model = getattr(writer, 'cfg', {}).get('model') or ('llm' if writer else 'mock')
        changed = []
        for raw in inputs:
            ref, kind = raw['ref'], raw['type']
            nid = local[ref]
            old = by_id.get(nid)
            who = raw.get('who', '')
            _need(not who or who in characters, 'UNKNOWN_CHARACTER', f'说话人 {who!r} 不存在', '调用 list_characters 或 upsert_character')
            title = raw['title'].strip()
            lines = [line.strip() for line in raw.get('lines', [title])]
            _need(title and lines and all(0 < len(line) <= 600 for line in lines), 'INVALID_DIALOGUE',
                  f'{ref} 的标题或台词为空，或单句超过 600 字', '填写标题和非空台词，长句拆开写')
            poses = []
            pose_prompts = {}
            for pose in raw.get('chars', []):
                cid = pose['id']
                _need(cid in characters, 'UNKNOWN_CHARACTER', f'在场角色 {cid!r} 不存在', '调用 list_characters 或 upsert_character')
                # 姿态和表情都要归一成枚举再落库：契约里的 `stance` 是 stand/sit/lean/lie/walk，
                # 前端下拉用的也是这套值。以前这里根本不写 stance —— 于是所有角色永远是
                # 默认的 stand，一个角色从头到尾只有一张脸（这就是「没有差分」的真正根因）。
                mood_text = pose.get('mood', '平静')
                stance = stance_of(pose.get('pose') or '')
                mood = mood_of(mood_text)
                # 这个（角色, 姿态, 表情）已经有差分图就沿用，别每次改台词都把差分冲回基底
                # —— 背景图踩过同一个坑（见下面的 keep_bg）。
                kept = next((item.get('pose') for item in ((old or {}).get('chars') or [])
                             if item.get('id') == cid
                             and stance_of(item.get('stance') or item.get('pose') or '') == stance
                             and mood_of(item.get('mood') or '') == mood
                             and self._asset_exists(item.get('pose') or '')), '')
                poses.append(dict(id=cid, pose=kept or characters[cid]['ref_image'],
                                  pos=pose.get('pos', 'center'), mood=mood_text, stance=stance))
                if pose.get('pose'):
                    pose_prompts[cid] = pose['pose']
            # ★ 真模型实测：它经常把选项直接写在 scene 节点上（"scene + choices"），
            #   原来一律判 WRONG_NODE_TYPE，整章就白写了（实测 c01-s11 卡死烧完 40 次调用）。
            #   作者意图很清楚——带 choices 就是要分支，所以按意图升格成 choice。
            if raw.get('choices') and kind != 'choice':
                kind = 'choice'
            # 同理：带 end_kind 就是结局，别因为 type 写成 scene 就判死
            if raw.get('end_kind') and kind != 'ending':
                kind = 'ending'
            bg_key = raw.get('bg_key', '').strip() or nid
            bg_prompt = raw.get('bg_prompt', '').strip() or bg_key.replace('_', ' ')
            index = int(nid[1:]) - 1
            # 分支性质：key=必要分支（决定进哪个结局），flavor=非必要分支（汇合回主线）。
            # 只有 choice 才带，别的类型传了就忽略（宽容，不判死）。
            branch_kind = raw.get('branch_kind') if kind == 'choice' else None
            if branch_kind not in ('key', 'flavor'):
                branch_kind = 'key' if kind == 'choice' else None
            # 占位名带地点（「待生成-雨夜天台.png」），与 generate_image 的占位一致。
            # 但如果这个节点已经出过真图、且地点没变，必须**保留**那张图：
            # 以前每次 write_nodes 都把 asset 重置成占位，改一句台词就要重画一张图。
            previous_bg = (old or {}).get('bg') or {}
            keep_bg = (bool(previous_bg) and previous_bg.get('provider') != 'pending'
                       and self.node_meta.get(nid, {}).get('bg_key') == bg_key
                       and self._asset_exists(previous_bg.get('asset')))
            bg = (dict(previous_bg, prompt=bg_prompt) if keep_bg else
                  dict(asset=f'assets/bg/待生成-{safe_stem(bg_key)}.png',
                       prompt=bg_prompt, seed=0, provider='pending'))
            node = dict(id=nid, type=kind, title=title, status='pending', end_kind=raw.get('end_kind'),
                        branch_kind=branch_kind,
                        bg=bg,
                        chars=poses, lines=[dict(who=who, text=line, voice=None, dur=_duration(line)) for line in lines],
                        pos=old['pos'] if old else dict(x=320 + index * 320, y=320),
                        gen=dict(model=model, at=stamp, cost=0.0))
            if kind == 'scene' and old and old['type'] == 'scene' and old.get('choices'):
                node['choices'] = old['choices']
            by_id[nid] = Node.model_validate(node).model_dump(mode='json')
            metadata[nid] = dict(chapter_id=chapter_id, ref=ref, bg_key=bg_key, pose_prompts=pose_prompts)
            if kind == 'choice':
                metadata[nid]['pending_choices'] = [dict(choice, to_ref=choice['to']) for choice in raw.get('choices', [])]
            changed.append(nid)

        # Forward refs can be supplied in a later batch. Only formal IDs go into the public graph.
        for nid, item in metadata.items():
            if item.get('chapter_id') != chapter_id or 'pending_choices' not in item:
                continue
            choices = []
            unresolved = []
            for choice in item['pending_choices']:
                ref = choice['to_ref']
                target = local.get(ref, ref if ref in local.values() else None)
                if target:
                    choices.append(dict(label=choice['label'], to=target, condition=choice.get('condition')))
                else:
                    unresolved.append(choice)
            # Keep the original list until every target resolves so choice ports stay stable.
            if not unresolved:
                metadata[nid] = {key: value for key, value in item.items() if key != 'pending_choices'}
                if choices != by_id[nid].get('choices'):
                    by_id[nid] = dict(by_id[nid], choices=choices)
                    if nid not in changed:
                        changed.append(nid)
        # 多个选项指向同一节点是合法的 cosmetic 选项（I5 的键是 (from, port) 不是 (from, to)），
        # 这里原来的 DUPLICATE_CHOICE_TARGET 拦截已删除。
        nodes = list(by_id.values())
        pending_edges = edges_from_nodes(nodes)
        # 提交前先挡环。放开这一步就会变成「写到第 80 次调用才发现图有环」——
        # 真模型实测就是这么把额度烧光的（见 _cycle_path 注释）。
        cycle = _cycle_path(nodes, pending_edges)
        if cycle:
            shown = ' → '.join(cycle[:8])
            return failure(
                'GRAPH_CYCLE',
                f'这批节点会让剧情图成环：{shown}',
                '剧情图必须是有向无环的：choice 的两个分支都要走向新节点；'
                '想让两条分支汇合，请新建一个 scene 当汇合点，让两边都指向它',
                cycle=cycle,
            )
        state = {**self.state, 'nodes': metadata, 'acts': self._update_chapter(chapter_id, status='writing')}
        await self._commit(nodes=nodes, edges=pending_edges, agent_state=state)
        mapping = {ref: local[ref] for ref in refs}
        # ★ 真模型实测踩到：它反复提交同一批 ref，节点数一直不涨，
        #   最后 40 次工具调用额度耗尽报「本轮 2 次调用超过剩余工具额度 1」。
        #   所以必须把「这次到底新建了几个」和「本章还剩哪些 ref 可用」明确回给它。
        created = [ref for ref in refs if ref in new]
        reused = [ref for ref in refs if ref in local and ref not in new]
        # ★ 这里必须报「真实还能写几个」（含 2 个弹性位）。报少了 writer 会提前收手，
        #   分支缺腿时只能把两条选项指向同一个节点（2026-11-08 实测卡死）。
        remain = chapter['node_budget'] + 2 - len(local)
        # 告诉 writer「本章哪些节点还没出口」。实测它写完一批节点就不管了，
        # 然后想往回连主线又成环被拒，一路反复 237 轮（孤儿节点连不进去）。
        # 每次写完直接把欠出口的节点列出来，它才知道下一步该连谁。
        open_exits = [
            nid for nid, item in metadata.items()
            if item.get('chapter_id') == chapter_id
            and (by_id.get(nid) or {}).get('type') in ('scene', 'start')
            and not ((by_id.get(nid) or {}).get('choices') or [])
        ]
        exit_hint = (f'。还缺出口的节点：{"、".join(open_exits)}（用 connect_nodes 按剧情顺序连上）'
                     if open_exits else '。本章所有场景节点都已有出口')
        return success(refs=mapping, node_ids=list(mapping.values()), updated_ids=changed,
                       created_refs=created, created_count=len(created),
                       existing_refs=reused,
                       chapter_refs=sorted(local),
                       open_exits=open_exits,
                       remaining_budget=remain,
                       note=('本次新建 %d 个节点；%s' % (
                           len(created),
                           ('这些 ref 已存在，只更新了内容、没有新建：' + '、'.join(reused)) if reused else '全部是新节点'))
                            + '。本章还可新建 %d 个，请用未占用过的新 ref 继续（如 c%02d-s%02d 起）'
                            % (remain, chapter['ordinal'], len(local) + 1)
                            + exit_hint)

    def _chapter_reachable(self, local_ids, entry_sources, by_id):
        """从本章入口出发能走到的节点集合。"""
        def targets(nid):
            """内部小工具：取某个节点的所有出口目标。"""
            return [c['to'] for c in by_id[nid].get('choices') or [] if c['to'] in local_ids]

        entries = [target for source in entry_sources for target in targets(source)]
        return _reachable_from(entries, {nid: targets(nid) for nid in local_ids})

    async def connect_nodes(self, args, scope):
        """改一条连线（支持 replace），并挡住成环与制造孤儿。"""
        self._require_mutable_skeleton()
        by_id = {n['id']: n for n in self.graph['nodes']}
        source = self._resolve(args['from'], scope, self.node_meta, by_id)
        target = self._resolve(args['to'], scope, self.node_meta, by_id)
        _need(source and target, 'NODE_NOT_FOUND', f'找不到连边 {args["from"]} → {args["to"]} 的节点',
              '先写入两端节点，然后用 get_chapter 返回的 ref 或 id')
        _need(source != target, 'SELF_EDGE', '节点不能连回自身', '选另一个已有节点作为后继')
        start = by_id[source]
        _need(start['type'] in ('start', 'scene'), 'WRONG_NODE_TYPE', f'{start["type"]} 不支持普通出口',
              'choice 的分支通过 write_nodes 的 choices 编辑，ending 不连出边')
        # 允许显式修改已有出口。提交前比较本章可达性，避免修好一个孤岛却切断另一段路线。
        old_target = start['choices'][0]['to'] if start.get('choices') else None
        if old_target is not None:
            if old_target == target:
                return success(from_id=source, to_id=target, changed=False)
            _need(args.get('replace') is True, 'EXIT_OCCUPIED',
                  f'{source} 已连向 {old_target}，不能再连 {target}',
                  f'确认 {old_target} 及下游仍有可达路径后，用 replace=true 改出口；'
                  '如果要加分支，用 write_nodes 编辑 choices 并保留原目标')
        target_chapter = self.node_meta.get(target, {}).get('chapter_id')
        source_chapter = self.node_meta.get(source, {}).get('chapter_id')
        chapter = self._require_active(target_chapter, scope)
        previous, _ = self._nearby(chapter)
        allowed = source_chapter == target_chapter or (
            source_chapter == previous['id'] if previous else start['type'] == 'start')
        _need(allowed, 'WRONG_CHAPTER', '连边必须发生在本章，或从上一章的收尾节点连入本章',
              '使用 get_chapter 的 open_exits 获取合法的跨章来源')
        _need(source_chapter == target_chapter or old_target is None
              or self.node_meta.get(old_target, {}).get('chapter_id') == target_chapter,
              'WRONG_CHAPTER', f'{source} 的现有出口 {old_target} 属于本章以外的路线',
              '保留已有章节路线，用 get_chapter 的 open_exits 查找接入点')
        seen, todo = set(), [target]
        while todo:
            nid = todo.pop()
            _need(nid != source, 'GRAPH_CYCLE', '这条连边会让剧情回到已有节点', '选择下游节点作为后继')
            if nid in by_id and nid not in seen:
                seen.add(nid)
                todo.extend(c['to'] for c in by_id[nid].get('choices') or [])
        if old_target is not None:
            local, before = self._chapter_nodes(chapter, previous)
            local_ids = {node['id'] for node in local}
            entry_sources = [node['id'] for node in before if node['type'] in ('start', 'scene')]
            reachable = self._chapter_reachable(local_ids, entry_sources, by_id)
        by_id[source] = dict(start, choices=[dict(label='继续', to=target, condition=None)])
        if old_target is not None:
            lost = reachable - self._chapter_reachable(local_ids, entry_sources, by_id)
            if lost:
                would_orphan = [nid for nid in by_id if nid in lost]
                return failure(
                    'WOULD_ORPHAN',
                    f'改连 {source} → {target} 会切断当前可达节点：{"、".join(would_orphan)}',
                    f'保留原路线 {source} → {old_target}。若要插入一段，先把插入段尾接到 '
                    f'{old_target}，再用 replace=true 改入口；若要分叉，用 choices 保留原目标并接入新路线。',
                    would_orphan=would_orphan,
                )
        nodes = list(by_id.values())
        await self._commit(nodes=nodes, edges=edges_from_nodes(nodes))
        replaced = old_target if old_target and old_target != target else None
        orphaned = []
        if replaced:
            # 换掉出口后，旧目标可能没人再指向它 —— 直接告诉它，别等校验报孤儿
            if not any(c['to'] == replaced for n in nodes for c in (n.get('choices') or [])):
                orphaned.append(replaced)
        note = ''
        if replaced:
            note = f'已把 {source} 的出口从 {replaced} 改到 {target}'
            if orphaned:
                note += f'；{replaced} 没有入边，可用 get_chapter 的 orphans 查看孤岛入口'
        return success(from_id=source, to_id=target, changed=True,
                       replaced=replaced, orphaned=orphaned, note=note)

    async def list_characters(self, args, scope):
        """列出角色（含立绘与音色）。"""
        notes = self.state.get('character_notes', {})
        return success(characters=[dict(c, note=notes.get(c['id'], ''), has_pose=self._asset_exists(c['ref_image']))
                                   for c in self.graph['characters']])

    async def upsert_character(self, args, scope):
        """新建或更新一个角色。"""
        cid = args['id']
        _need(re.fullmatch(r'[a-z][a-z0-9_]*', cid) and args['name'].strip(), 'INVALID_CHARACTER',
              '角色 id 或名字不合法', 'id 用小写英文字母开头，可含数字和下划线，名字不能为空')
        characters = {c['id']: c for c in self.graph['characters']}
        old = characters.get(cid, {})
        _need(old or len(characters) < 8, 'TOO_MANY_CHARACTERS', '角色已达八人', '复用已有角色')
        color = args.get('color', old.get('color', '#d946ef'))
        _need(re.fullmatch(r'#[0-9a-fA-F]{6}', color), 'INVALID_COLOR', '角色配色不是六位十六进制', '例如 #7c3aed')
        gender = args.get('gender', old.get('gender', 'other'))
        _need(gender in ('male', 'female', 'other'), 'INVALID_GENDER',
              '性别只能是 male / female / other', '按角色实际性别填，写台词时要用「他/她」')
        character = dict(id=cid, name=args['name'].strip(), color=color,
                         # 占位名带角色名：图还没出时用户看到的是
                         # 「assets/char/待生成-林晚.png」，不是一串哈希。
                         ref_image=old.get('ref_image',
                                           f'assets/char/待生成-{safe_stem(args["name"].strip())}.png'),
                         voice_id=args.get('voice_id', old.get('voice_id', '')),
                         look=args.get('look', old.get('look', '')),
                         gender=gender)
        characters[cid] = character
        notes = dict(self.state.get('character_notes', {}))
        if 'note' in args:
            notes[cid] = args['note']
        await self._commit(characters=list(characters.values()), agent_state={**self.state, 'character_notes': notes})
        return success(character=dict(character, note=notes.get(cid, '')))

    def _structure_errors(self, chapter=None):
        """把一部（或一章）剧情图体检一遍，返回全部错误，**不抛异常**。

        为什么是一堆 error 而不是一抛了之
        ---------------------------------
        writer 是个循环调模型的 agent。抛第一个错，模型只能改一处、再跑一次、
        再撞下一个错 —— 几百轮都收敛不了（实测过，p_08 跑了 40 轮还卡在结构校验）。
        所以这里一次把所有问题都算出来，按 code 交给模型，它能一轮改一批。

        体检分九段，顺序有讲究（后面的依赖前面的结果）：
        1. **契约**：pydantic 的 Graph 模型能不能过。不过就没法往下算了，直接返回。
        2. **节点唯一性**：ID 重复会让 by_id 少项，后面全是错的。
        3. **边界**：本章的节点集合、前面能接进来的、后面能接出去的。
        4. **边一致性**：`edges` 必须是 `choices` 的投影 —— 两者不一致时前端画的是 A，
           引擎跑的是 B，这种 bug 最难查。用 Counter 比对「应有」与「实有」。
        5. **跨章入口/出口**：一章只能有一个入口、一个收尾场景，否则下一章没法接。
        6. **节点类型**：ending 不能有出口、choice 要 2~4 个不同目标、scene 要一个出口。
        7. **可达性**：从入口做一遍 BFS，到不了的就是孤岛（写作时最容易漏）。
        8. **flavor 语义**：非必要分支不该影响结局；能到不同结局说明标错了。
        9. **环**：Kahn 拓扑排序，排不完说明有回指。

        参数
        ----
        chapter: 章节 dict 或 None。给了就只体检本章（writer 每章结束会调），
                 None 表示全书构图。

        返回
        ----
        `[{code, node_id, message}, ...]`，空列表 = 没问题。
        """
        errors = []

        def error(code, nid, message):
            """内部小工具：往错误列表里追加一条。"""
            errors.append(dict(code=code, node_id=nid, message=message))

        # ---- 1. 契约校验：模型层就不合法的话，后面的图语义无从谈起 ----
        graph = public_graph(self.graph)
        try:
            Graph.model_validate(graph)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = '.'.join(str(part) for part in first['loc'])
            error('INVALID_CONTRACT', None, f'{field}: {first["msg"]}')
            return errors

        # ---- 2. 节点唯一性与起点 ----
        by_id = {node['id']: node for node in graph['nodes']}
        if len(by_id) != len(graph['nodes']):
            error('DUPLICATE_NODE', None, '正式节点 ID 重复')
        starts = [n['id'] for n in graph['nodes'] if n['type'] == 'start']
        if len(starts) != 1:
            error('INVALID_START', None, f'必须有唯一的起始节点（type=start），当前 {len(starts)} 个')

        # ---- 3. 体检范围：单章模式只关心本章 + 前一章尾 + 下一章头 ----
        if chapter:
            previous, following = self._nearby(chapter)
            nodes, before = self._chapter_nodes(chapter, previous)
            selected = {node['id'] for node in nodes}
            allowed_before = {node['id'] for node in before}
            allowed_after = {node['id'] for node in self._nodes(following['id'])} if following else set()
        else:
            previous = following = None
            selected = set(by_id)
            allowed_before = allowed_after = set()

        def relevant(edge):
            """内部小工具：这条边是否落在本次体检范围内。"""
            return chapter is None or edge['from'] in selected or edge['to'] in selected

        def endpoints(edge):
            """内部小工具：取边的 (from, port, to) 三元组。"""
            return edge['from'], edge['port'], edge['to']

        # ---- 4. 边一致性：edges 必须等于「所有节点 choices 展开后的三元组」----
        # 用 Counter 做差集，左右任一方向多出来的都要报（漏边和野边同样致命）。
        edges = [edge for edge in graph['edges'] if relevant(edge)]
        actual = Counter(endpoints(edge) for edge in edges)
        expected = Counter(endpoints(edge) for edge in edges_from_nodes(graph['nodes']) if relevant(edge))
        for source, port, target in sorted((actual - expected).keys() | (expected - actual).keys()):
            error('BROKEN_EDGE', source, f'出口 {port} → {target} 的 edge 与 choices 不一致')
        if len({edge['id'] for edge in edges}) != len(edges):
            error('DUPLICATE_EDGE', None, '边 ID 重复')
        for source, _, target in actual:
            if source not in by_id or target not in by_id:
                error('BROKEN_EDGE', source, f'边 {source} → {target} 引用了不存在的节点')

        # ---- 5. 逐节点校验，顺便收集三样后面要用的东西 ----
        # entries：谁被外部接进了本章；boundaries：本章哪些 scene 留了空出口给下一章；
        # next_nodes/indegree：本章内部的邻接表和入度，供可达性 + 环检测。
        entries = []
        boundaries = []
        next_nodes = defaultdict(list)
        indegree = dict.fromkeys(selected, 0)
        for nid, node in by_id.items():
            choices = node.get('choices') or []
            if nid not in selected:
                # 本章以外的节点：只检查它有没有「非法接进本章」
                foreign, illegal = _foreign_entries(node, selected, allowed_before)
                entries.extend(foreign)
                for target, source in illegal:
                    error('INVALID_ENTRY', target, f'入口来自不相邻章节的节点 {source}')
                continue
            kind = node['type']
            pending = self.node_meta.get(nid, {}).get('pending_choices', [])
            if pending:
                refs = '、'.join(choice['to_ref'] for choice in pending)
                error('UNRESOLVED_REF', nid, f'仍有分支目标待解析，请检查 ref：{refs}')
            # ---- 6. 按节点类型分别校验出口形态 ----
            if kind == 'ending':
                if node.get('end_kind') not in ('true', 'good', 'bad', 'alt') or choices:
                    error('INVALID_ENDING', nid, '结局须填写 end_kind（true/good/bad/alt）且不能有出口')
            elif kind == 'choice':
                if not 2 <= len(choices) <= 4:
                    error('INVALID_CHOICE', nid,
                          f'{nid} 的选项数是 {len(choices)}，分支须有 2–4 个不同目标')
                # 多个选项指向同一节点是合法的（cosmetic / flavor 选项，只改旗标不改拓扑），
                # 这里不再拦；同一 (from, port) 只能一条边由写入门保证。
            elif following and kind == 'scene' and not choices:
                # 收尾场景允许空出口（下一章从这儿接），所以不报 DEAD_END
                boundaries.append(nid)
            elif len(choices) != 1:
                error('DEAD_END', nid, '普通场景或起点须有一个出口')
            # ---- 出口目标：章内 / 下一章 / 不存在，三种情况 ----
            for choice in choices:
                target = choice['to']
                if target in selected:
                    next_nodes[nid].append(target)
                    indegree[target] += 1
                elif target not in by_id:
                    error('MISSING_TARGET', nid, f'目标节点 {target} 不存在')
                elif target in allowed_after and kind == 'scene':
                    boundaries.append(nid)
                else:
                    error('INVALID_EXIT', nid, f'出口 {target} 跳到不相邻章节，或跨章出口不是普通场景')

        # ---- 7. 章节边界：一个入口、一个收尾 ----
        # 下一章只能从「上一章唯一那个收尾场景」接进来，所以本章多留一个空出口，
        # 下一章就不知道该接哪条线了。这是 writer 最常犯的结构错之一。
        if chapter:
            if len(entries) != 1:
                error('INVALID_ENTRY', None, f'章节必须有一个前章或起点接入的入口，当前 {len(entries)} 个')
            if following and len(boundaries) != 1:
                message = f'本章须留一个普通场景接下一章，当前 {len(boundaries)} 个'
                if boundaries:
                    message += f'（{"、".join(boundaries)}）；把续写路线汇合到同一个场景'
                else:
                    message += ('；选一条应继续的路线，以已有 ref 把适合续写的结尾改成 scene'
                                '（省略 end_kind），留它的出口给下一章')
                error('INVALID_FRONTIER', None, message)
        else:
            # 全书模式：入口是唯一的起始节点（旧项目是 n1），且至少要有一个结局
            root = start_id(graph['nodes'])
            entries = [root] if root in selected else []
            if not any(n['type'] == 'ending' for n in by_id.values()):
                error('MISSING_ENDING', None, '全图至少须有一个结局节点')

        # ---- 8. 可达性：从入口 BFS，摸不到的就是孤岛 ----
        # 孤岛分两种，给模型的提示完全不同：
        #   · orphan_children = 孤岛「内部」的节点 → 先把它接回孤岛入口
        #   · 孤岛入口本身 → 要告诉它从哪些开放场景可以连进去
        reachable = _reachable_from(entries, next_nodes)
        unreachable = selected - reachable
        orphan_children = {target for source in unreachable for target in next_nodes[source]}
        open_sources = [nid for nid, node in by_id.items()
                        if nid in reachable and node['type'] == 'scene' and not node.get('choices')]
        for nid in by_id:
            if nid not in unreachable:
                continue
            message = f'从入口到不了节点 {nid}'
            if chapter and entries:
                if nid in orphan_children:
                    message += '；先接回本孤岛的入口节点'
                else:
                    message += '；这是孤岛入口，先用 get_chapter 核对 edges 和 ref'
                    if open_sources:
                        message += f'；可从剧情上衔接的开放场景（{"、".join(open_sources)}）连入'
                    message += ('；若插入已有路线，先把孤岛尾接到原后继，再用 connect_nodes'
                                '（replace=true）改入口；若为分支，用 choices 保留原目标并接入此节点')
            error('ORPHAN_NODE', nid, message)

        # ---- 9a. flavor 语义：非必要分支不该影响结局 ----
        # 从每个选项出发各做一次 DFS，收集「这条腿能走到哪些结局」。
        # 只要两条腿的结局集合不同，这个分支实际上决定了结局 —— 标错会让作者
        # 以为这段随便写，实际它卡着真结局。真模型很容易标错（主人 2026-11-08 定）。
        for nid in [n for n in selected if (by_id.get(n) or {}).get('branch_kind') == 'flavor']:
            legs = []
            for choice in (by_id[nid].get('choices') or []):
                found, seen, todo = set(), set(), [choice['to']]
                while todo:
                    cur = todo.pop()
                    if cur in seen or cur not in by_id:
                        continue
                    seen.add(cur)
                    if by_id[cur]['type'] == 'ending':
                        found.add(cur)
                    else:
                        todo.extend(c['to'] for c in by_id[cur].get('choices') or [])
                legs.append(found)
            if len(legs) > 1 and any(leg != legs[0] for leg in legs[1:]):
                error('FLAVOR_AFFECTS_ENDING', nid,
                      f'{nid} 标成了非必要分支（flavor），但它的不同选项能到达不同结局 —— '
                      '这实际是必要分支，把 branch_kind 改成 key')

        # ---- 9b. 环检测：Kahn 拓扑排序，排不完就是有回指 ----
        # indegree 是第 6 段边遍历时顺手累加的，这里直接消费掉（后面没人再用它）。
        queue = deque(nid for nid, degree in indegree.items() if degree == 0)
        count = 0
        while queue:
            nid = queue.popleft()
            count += 1
            for target in next_nodes[nid]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if count != len(selected):
            error('GRAPH_CYCLE', None, '剧情图有环，检查回指的出口')
        return errors

    async def validate_graph(self, args, scope):
        """跑结构校验，把错误一次返回给模型（不是抛出）。"""
        chapter = None
        if args['scope'] == 'chapter':
            if not scope:
                active = [ch for ch in self.chapters if ch['status'] == 'writing']
                _need(len(active) == 1, 'CHAPTER_REQUIRED', '没有唯一的当前章节', '指定 chapter_id 或先写入当前章节')
                scope = active[0]['id']
            chapter = self._chapter(scope)
        errors = self._structure_errors(chapter)
        if errors:
            return failure('GRAPH_INVALID', f'发现 {len(errors)} 项结构错误',
                           '按 errors 核对现有节点和连线，再修正结构', errors=errors)
        return success(errors=[])

    async def finish_chapter(self, args, scope):
        """收章：结构校验通过才允许，同时写前情摘要给后面章节用。"""
        self._require_mutable_skeleton()
        chapter_id = args['chapter_id']
        chapter = self._chapter(chapter_id)
        raw_summary = args.get('summary') or ''
        summary = str(raw_summary).strip()
        _need(not scope or scope == chapter_id, 'WRONG_CHAPTER', '本轮只允许完成当前章节', '使用当前任务中的章节 id')
        if chapter['status'] == 'done':
            _need(chapter['summary'] == summary, 'CHAPTER_DONE', '章节已写完，摘要已锁定', '继续写下一章')
            return success(chapter_id=chapter_id, summary=summary, changed=False)
        self._require_active(chapter_id, scope)
        # ★ 真模型实测：模型会传空摘要（或超长），直接判错会让整章白写。
        #   这里改成「空就按本章标题拼一句、超长就截断」，并把实际情况回给它，
        #   流程能继续，也不假装它写得对。
        summary_note = ''
        if not summary:
            titles = [n.get('title') or n['id'] for n in self._nodes(chapter_id)]
            summary = ('本章：' + '→'.join(titles[:6]))[:200]
            summary_note = '你传了空摘要，已按本章节点标题自动生成'
        elif len(summary) > 200:
            summary_note = f'摘要 {len(summary)} 字超出 200，已截断'
            summary = summary[:200]
        count = len(self._nodes(chapter_id))
        # 预算只是估算，别按精确值卡。实测同一章写成 11/12、13/12、超 3 个都出现过，
        # 每次都因为「和计划不等」判死整轮。真正要防的是「这章几乎没写」。
        # 下限：预算的一半（至少 3 个）；上限给 3 倍，防某一章吃掉全书篇幅。
        budget = chapter['node_budget']
        floor = max(3, budget // 2)
        _need(floor <= count <= budget * 3, 'BUDGET_MISMATCH',
              f'已写 {count}，计划 {budget}',
              f'本章节点数太少（至少 {floor} 个，预算 {budget}）：继续写满再收尾')
        checked = await self.validate_graph(dict(scope='chapter'), chapter_id)
        if not checked['ok']:
            return checked
        await self._commit(agent_state={**self.state, 'acts': self._update_chapter(chapter_id, status='done', summary=summary)})
        result = success(chapter_id=chapter_id, summary=summary, changed=True)
        if summary_note:
            result['note'] = summary_note
        return result

    def _asset_exists(self, asset):
        """资产路径是否真的指向一个存在的文件。"""
        if not asset:
            return False
        try:
            return safe_asset(self.root, asset).is_file()
        except ValueError:
            return False

    # ---------------- 人可读文件名 ----------------
    def _is_placeholder(self, asset):
        """这条路径是不是「还没出真图」的占位。

        不能再用 `asset == f'assets/char/{_digest(cid)}.png'` 这种写死比较 ——
        占位名里现在带角色名，写死会在改名后判错。
        """
        if not asset:
            return True
        name = Path(asset).name
        return name.startswith('待生成-') or name in ('mock.png', 'mock.wav')

    def _next_serial(self, folder):
        """取这个目录下一个序号（01、02…）。

        初值来自目录里已有的 `NN-xxx` 文件：项目重开时接着编号，
        不会因为进程重启又从 01 开始把旧文件撞掉。
        """
        serial = self._name_seq.get(folder)
        if serial is None:
            serial = 0
            try:
                for item in (self.root / folder).iterdir():
                    match = _NAME_SEQ.match(item.stem)
                    if match:
                        serial = max(serial, int(match.group(1)))
            except OSError:
                pass
        serial += 1
        self._name_seq[folder] = serial
        return serial

    def _find_by_content(self, folder, data):
        """目录里已经有一模一样内容的文件就返回它的相对路径。

        这是「中文文件名之后仍然不丢去重」的关键：同一张图不会再存第二份。
        先比字节数，只有大小相同才算哈希 —— 项目里图都是 1–3 MB，
        全量哈希每次几十 MB 很浪费，比大小能挡掉绝大多数。
        """
        digest = sha256(data).hexdigest()
        try:
            items = list((self.root / folder).iterdir())
        except OSError:
            return None
        for item in items:
            if item.suffix.lower() not in ('.png', '.jpg', '.wav'):
                continue
            try:
                if item.stat().st_size != len(data):
                    continue
                if sha256(item.read_bytes()).hexdigest() == digest:
                    return f'{folder}/{item.name}'
            except OSError:
                continue
        return None

    def _pose_phrase(self, character, char_id, stance=None, mood=None):
        """立绘的可读名字：「角色名-这次要画的姿态」。

        姿态描述存在节点的 `pose_prompts` 里（writer 写节点时带过来的）。
        `stance` / `mood` 给了就只找这个组合的节点 —— 否则画「坐」的差分时
        会拿到「站着看窗外」那句描述，名字和画面对不上。
        """
        name = str(character.get('name') or char_id).strip()
        want = ''
        for node in self.graph['nodes']:
            hit = any(item.get('id') == char_id
                      and (stance is None or stance_of(item.get('stance') or '') == stance)
                      and (mood is None or mood_of(item.get('mood') or '') == mood)
                      for item in node['chars'])
            if not hit:
                continue
            want = (self.node_meta.get(node['id'], {}).get('pose_prompts') or {}).get(char_id) or ''
            if want:
                break
        want = first_clause(want, 12)
        if not want and stance and mood:
            want = f'{stance}-{mood}'
        return f'{name}-{want}' if want else name

    def _pose_hint(self, char_id, stance, mood):
        """给一张差分图一句画面线索。

        剧本里的 `pose_prompts`（writer 写节点时带过来的原始描述，比如「低头看着窗外」）
        比 stand / sit 这类枚举具体得多 —— 拿它当依据，agent 不用自己猜。
        """
        hints = []
        for node in self.graph['nodes']:
            hit = any(item.get('id') == char_id
                      and stance_of(item.get('stance') or '') == stance
                      and mood_of(item.get('mood') or '') == mood
                      for item in node['chars'])
            if not hit:
                continue
            text = first_clause(
                (self.node_meta.get(node['id'], {}).get('pose_prompts') or {}).get(char_id) or '', 16)
            if text and text not in hints:
                hints.append(text)
            if len(hints) >= 2:
                break
        return '；'.join(hints)

    def _parse_char_key(self, key):
        """拆立绘 key → (角色 id, 姿态, 表情, 是否差分)。

        两种写法：
          `<角色id>`                   基底立绘（一个角色一张，透明底）
          `char:<id>:<stance>:<mood>`  差分（拿基底当参考图，长相才不会跑）
        历史数据里还有 `char:<id>:base`，同样算基底。
        """
        text = str(key or '').strip()
        if not text.startswith('char:'):
            return text, 'stand', 'neutral', False
        parts = text.split(':')
        if len(parts) >= 4:
            return parts[1], stance_of(parts[2]), mood_of(parts[3]), True
        if len(parts) == 3 and parts[2] == 'base':
            return parts[1], 'stand', 'neutral', False
        return (parts[1] if len(parts) > 1 else text), 'stand', 'neutral', False

    async def _save_named(self, folder, phrase, data, suffix='.png'):
        """存一张图/一段音，文件名 = `序号-中文短语.png`。

        返回 `(相对路径, 是否新写盘)`。内容已经在项目里 → 直接复用旧路径。
        """
        existing = await asyncio.to_thread(self._find_by_content, folder, data)
        if existing:
            return existing, False
        stem = safe_stem(first_clause(phrase))
        for _ in range(50):
            serial = self._next_serial(folder)
            relative = f'{folder}/{serial:02d}-{stem}{suffix}'
            if not self._asset_exists(relative):
                await self.save_asset(relative, data)
                return relative, True
        # 50 次都撞名（几乎不可能）：退回哈希名，至少不会丢文件
        relative = f'{folder}/{sha256(data).hexdigest()[:20]}{suffix}'
        if not self._asset_exists(relative):
            await self.save_asset(relative, data)
            return relative, True
        return relative, False

    def _backgrounds(self):
        """把图里出现的 bg_key 归并成背景清单（同一地点只出一条）。"""
        groups = defaultdict(list)
        for node in self.graph['nodes']:
            fallback = 'start' if node['type'] == 'start' else node['bg']['asset'] or node['id']
            key = self.node_meta.get(node['id'], {}).get('bg_key') or fallback
            groups[key].append(node)
        return groups

    def _background_ready(self, nodes):
        assets = {node['bg']['asset'] for node in nodes}
        if len(assets) != 1:
            return False
        asset = next(iter(assets))
        blocked = {'pending', 'mock'} if self.providers.get('art') is not None else {'pending'}
        return (all(node['bg']['provider'] not in blocked for node in nodes)
                and self._asset_exists(asset)
                and (self.providers.get('art') is None or not self._is_placeholder(asset)))

    async def list_pending_assets(self, args, scope):
        """列出还没生成的背景与立绘。"""
        backgrounds = []
        for key, nodes in self._backgrounds().items():
            if self._background_ready(nodes):
                continue
            backgrounds.append(dict(key=key, prompt=nodes[0]['bg']['prompt'],
                                    node_ids=[node['id'] for node in nodes], count=len(nodes)))
        backgrounds.sort(key=lambda item: (-item['count'], item['key']))
        notes = self.state.get('character_notes', {})
        real_art = self.providers.get('art') is not None
        characters = {item['id']: item for item in self.graph['characters']}

        def brief(character):
            return (character.get('look') or notes.get(character['id'], '')
                    or character['name'])

        # 立绘按「角色 × 姿态 × 表情」聚合，一个组合出一张图覆盖一组节点。
        # 以前是按角色出一条（key 直接就是角色 id），所以一个角色从头到尾只有一张脸 ——
        # 数据里的 stance/mood 明明都有，但只被当成显示文字，从没参与出图。
        groups = defaultdict(list)
        for node in self.graph['nodes']:
            for pose in node['chars']:
                groups[(pose['id'], stance_of(pose.get('stance') or ''),
                        mood_of(pose.get('mood') or ''))].append(node)

        bases, poses = [], []
        for (char_id, stance, mood), nodes in groups.items():
            character = characters.get(char_id)
            if not character:
                continue
            base = character['ref_image']
            base_ready = self._asset_exists(base) and not (real_art and self._is_placeholder(base))
            if not base_ready:
                # 基底都没出，差分没有参考图可用 —— 先把基底排前面
                if character not in bases:
                    bases.append(character)
                continue
            # 基底立绘本就是「站着 · 平静」，这个组合不需要再画一张
            if stance == 'stand' and mood == 'neutral':
                continue
            # 这一组节点的图现在是不是已经换成了差分图（不是还指着基底）
            paths = {item.get('pose') for node in nodes for item in node['chars']
                     if item['id'] == char_id
                     and stance_of(item.get('stance') or '') == stance
                     and mood_of(item.get('mood') or '') == mood}
            done = len(paths) == 1 and base not in paths and all(
                self._asset_exists(item) and not (real_art and self._is_placeholder(item))
                for item in paths)
            if done:
                continue
            poses.append(dict(
                key=pose_key(char_id, stance, mood),
                name=character['name'], char_id=char_id, stance=stance, mood=mood,
                prompt=brief(character), count=len(nodes),
                node_ids=[node['id'] for node in nodes],
                pose_hint=self._pose_hint(char_id, stance, mood)))
        # 出现次数多的先画：一张图覆盖的节点越多，性价比越高
        poses.sort(key=lambda item: (-item['count'], item['key']))
        bases_out = [dict(key=item['id'], name=item['name'], prompt=brief(item))
                     for item in bases]
        return success(bases=bases_out, backgrounds=backgrounds, poses=poses,
                       note='先画 bases（基底立绘），再用 poses 里的 key 出差分；'
                            '差分要拿基底当参考图，顺序反了角色会变样')

    async def generate_image(self, args, scope):
        """生成一张背景或立绘并写回节点；已有真图且键没变就复用。"""
        target, key = args['target'], args['key']
        prompt = args['prompt'].strip()
        _need(prompt, 'EMPTY_PROMPT', '生图描述为空', '填写地点与光线，或角色外形')
        width, height = self.params.get('resolution', self.graph['project']['resolution'])
        # transparent=True：目标是「无背景的立绘」。百炼这条通道没有原生 alpha，
        # 所以实际做法是「提示词约束纯色背景 + 抠图」，但调用方不用关心，
        # 结果一定带透明通道（抠图失败会报错，不会悄悄交白底图）。
        transparent = bool(args.get('transparent'))
        if args.get('size'):
            size = re.fullmatch(r'([0-9]{2,4})[*x×]([0-9]{2,4})', args['size'])
            _need(size, 'INVALID_SIZE', '图片尺寸格式错误', '使用如 1920*1080 的写法')
            width, height = map(int, size.groups())
            _need(64 <= width <= 8192 and 64 <= height <= 8192, 'INVALID_SIZE', '图片边长须为 64–8192', '调整 width 和 height')
        elif target != 'background':
            # 立绘必须竖长：项目分辨率是 1920×1080 的横屏，直接拿来出生图会得到
            # 一张横图、人物只占中间一小块（主人实测指出「立绘太小」）。
            # 这里按项目分辨率的长边自适应换算成 4:7 竖版，不写死某个尺寸。
            width, height = portrait_size(width, height)
        # 文件名短语：agent 可以自己起名（label），没给就从结构化字段派生。
        # 这里只决定「叫什么」，不改任何画面判断 —— 好不好看仍由 agent 自己看。
        label = str(args.get('label') or '').strip()
        nodes = self.graph['nodes']
        characters = self.graph['characters']
        provider = self.providers.get('art')
        # force=True：跳过「项目内已有」和「素材库命中」两条复用路径，强制重画。
        # 单节点「重新生成背景」必须用它 —— 否则同一地点同一时间只有一张图，
        # 所谓重画只是把那张图又复制一遍（实测踩过）。
        force = bool(args.get('force'))
        # 这几个是立绘分支才算出来的，但「没有 provider」的占位分支背景和立绘共用，
        # 不预先定义就会在画背景时读到未赋值的局部变量（踩过：UnboundLocalError）。
        char_id, stance, mood, is_diff = '', 'stand', 'neutral', False
        reference_kind = 'scene' if target == 'background' else 'character'
        references = await asyncio.to_thread(self._art_references, reference_kind) if provider else []
        place = moment = sky = ''
        if target == 'background':
            selected = self._backgrounds().get(key, [])
            _need(selected, 'ASSET_TARGET_NOT_FOUND', f'背景复用键 {key!r} 不存在', '调用 list_pending_assets 获取复用键')
            ids = {node['id'] for node in selected}
            background = selected[0]['bg']
            if not force and not references and self._background_ready(selected):
                return success(asset=background['asset'], node_ids=[n['id'] for n in selected],
                               generated=False, reused='project', provider=background['provider'])
            # 结构化三项决定「是不是同一个地方」，prompt 只是给生图模型看的散文。
            # place 为空时不能拿整段 prompt 兜底 —— 那样每个节点的 prompt 都不同，
            # 素材库就再也判不出「同一个地方」，复用必然失效。用 bg_key 兜底。
            place = background.get('place') or key
            moment = background.get('time') or ''
            sky = background.get('weather') or ''
            found_key, entry, how = (self.library.find_bg(place, moment, sky)
                                     if not references and not force else (None, None, None))
            if entry and (not provider or entry.get('provider') != 'mock'):
                # 库里已有同一地点同一时间：复制进项目，一次生图都不花。
                # 落盘仍然走 _save_named：库里拷过来也是「人能读懂的名字」。
                data = (self.library.root / entry['file']).read_bytes()
                phrase = label or scene_phrase(background.get('prompt') or '') or place
                relative, _ = await self._save_named('assets/bg', phrase, data)
                self.library.bump(found_key)
                reused_bg = dict(asset=relative, prompt=prompt, seed=int(entry.get('seed') or 0),
                                 provider=entry.get('provider') or 'library',
                                 place=place, time=moment, weather=sky)
                await self._commit(nodes=[dict(node, bg=reused_bg) if node['id'] in ids else node
                                          for node in self.graph['nodes']])
                return success(asset=relative, node_ids=[n['id'] for n in selected], generated=False,
                               reused=how, library_key=found_key, provider=reused_bg['provider'])
            phrase = label or scene_phrase(background.get('prompt') or '') or place
            relative = ''
            default = (64, 36, (35, 50, 75, 255))
            # 占位名带地点：还没出图时用户看到的也是「待生成-雨夜天台.png」，
            # 而不是一串哈希。write_nodes 写入的占位路径必须与这里一致。
            placeholder = f'assets/bg/待生成-{safe_stem(key)}.png'
        else:
            char_id, stance, mood, is_diff = self._parse_char_key(key)
            character = next((c for c in characters if c['id'] == char_id), None)
            _need(character, 'ASSET_TARGET_NOT_FOUND',
                  f'角色 {char_id!r} 不存在', '调用 list_pending_assets 获取角色 id')
            if is_diff:
                # 一张差分只服务「同角色 + 同姿态 + 同表情」的那组节点
                selected = [n for n in nodes if any(
                    item.get('id') == char_id
                    and stance_of(item.get('stance') or '') == stance
                    and mood_of(item.get('mood') or '') == mood
                    for item in n['chars'])]
                _need(selected, 'ASSET_TARGET_NOT_FOUND',
                      f'没有节点在用「{char_id} · {stance} · {mood}」这个组合',
                      '调用 list_pending_assets 拿准确的 poses key')
                # 差分必须拿基底当参考图：不然换个表情就换了一张脸，
                # 这正是「同一角色跨节点长相不一致」的来源。用户上传的外形参考图
                # 已经封在基底里了，这里只喂基底（I2I），透明底也跟着继承。
                base_asset = character['ref_image']
                _need(self._asset_exists(base_asset), 'BASE_ART_MISSING',
                      f'角色 {char_id} 的基底立绘还没出，差分没有参考图',
                      f'先调 generate_image(target=character, key={char_id}) 出基底')
                data = safe_asset(self.root, base_asset).read_bytes()
                _need(len(data) <= MAX_REFERENCE_BYTES, 'REFERENCE_TOO_LARGE',
                      f'基底立绘 {base_asset} 超过参考图大小上限', '压一下立绘尺寸再试')
                references = [data]
                phrase = label or self._pose_phrase(character, char_id, stance, mood)
                box = pose_key(char_id, stance, mood)
            else:
                selected = [n for n in nodes if any(p['id'] == char_id for p in n['chars'])]
                phrase = label or self._pose_phrase(character, char_id)
                box = base_key(char_id)
            ids = {node['id'] for node in selected}
            found_key, entry, how = (self.library.find_pose(char_id, stance, mood)
                                     if not references and not force else (None, None, None))
            if entry and how == 'exact' and (not provider or entry.get('provider') != 'mock'):
                relative, _ = await self._save_named(
                    'assets/char', phrase, (self.library.root / entry['file']).read_bytes())
                self.library.bump(found_key)
                if is_diff:
                    await self._commit(nodes=[
                        dict(node, chars=[dict(p, pose=relative)
                                          if p['id'] == char_id else p for p in node['chars']])
                        if node['id'] in ids else node for node in nodes])
                else:
                    await self._commit(
                        characters=[dict(c, ref_image=relative) if c['id'] == char_id else c
                                    for c in characters],
                        nodes=[dict(node, chars=[
                            dict(pose, pose=relative)
                            if pose['id'] == char_id and (pose.get('pose') or '') in ('', character['ref_image'])
                            else pose for pose in node['chars']])
                            if node['id'] in ids else node for node in nodes])
                return success(asset=relative, node_ids=[n['id'] for n in selected], generated=False,
                               reused=how, library_key=found_key, provider=entry.get('provider') or 'library')
            # 占位图沿用角色表里登记的路径，否则已有节点的引用会指向不存在的文件。
            default = (32, 64, (125, 86, 155, 255))
            placeholder = character['ref_image'] or 'assets/char/mock.png'

        generated = False
        folder = 'assets/bg' if target == 'background' else 'assets/char'
        native_alpha = bool(getattr(provider, 'supports_transparency', False)) if provider else False
        if provider:
            negative = args.get('negative', '').strip()
            description = f'{prompt}\n避免：{negative}' if negative else prompt
            if target != 'background' and transparent and not native_alpha:
                # 这条通道没有原生透明底，所以用提示词把背景钉成纯色 ——
                # 抠图算法靠「背景色与主体反差」工作，背景五花八门时边缘必然脏。
                if '纯色背景' not in description and '纯白背景' not in description:
                    description += '\n背景必须是单一纯色（纯白），不要渐变、不要地面、不要投影。'
            # 重生成必须换 seed：不换的话模型对同一 prompt 给出完全一样的图
            # （文件名哈希都不变，用户点「重新生成」等于没点，实测踩过）。
            seed = (int(datetime.now(timezone.utc).timestamp() * 1000) % 2147483647) if force else None
            if references:
                description += '\n以参考图为绘画风格示例，沿用笔触、用色和明暗处理，按上面的描述重新构图。'
                data = await provider.call(description, width=width, height=height,
                                           reference_images=references, seed=seed,
                                           transparent=transparent)
            else:
                data = await provider.call(description, width=width, height=height,
                                           seed=seed, transparent=transparent)
            if target != 'background' and not (native_alpha and has_alpha(data)):
                # 原生透明优先；拿不到就抠图（内部还有「模型 → 自适应阈值」两层兜底）。
                try:
                    data = await asyncio.to_thread(remove_white_background, data)
                except (ImportError, OSError, ValueError) as exc:
                    code = 'REFERENCE_CUTOUT_FAILED' if references else 'CUTOUT_FAILED'
                    raise ProviderError(code, f'立绘抠图失败：{exc}', 'art.cutout') from exc
            _need(isinstance(data, bytes) and data.startswith(b'\x89PNG\r\n\x1a\n'),
                  'REFERENCE_INVALID_RESPONSE' if references else 'INVALID_IMAGE',
                  '供应商未返回可导出的 PNG 图像', '调整图像供应商的输出格式后重试')
            if transparent and target != 'background':
                _need(has_alpha(data), 'CUTOUT_EMPTY',
                      '立绘要求无背景，但抠图结果没有透明区域',
                      '换纯色背景重画，或检查抠图模型是否可用')
            # 文件名 = 序号 + 中文短语；内容相同则复用已有文件（去重不靠文件名）。
            relative, generated = await self._save_named(folder, phrase, data)
            # 新生成的图同时进库，下一个项目/下一章直接复用，不再花钱。
            # 模拟模式（GAL_MOCK=1）产出的是占位图，进库只会挤掉真素材。
            if not MOCK and not references:
                key_name = bg_key(place, moment, sky) if target == 'background' else box
                self.library.put(key_name, data, prompt=prompt, seed=0,
                                 provider=getattr(provider, 'kind', 'image'), method='txt2img')
        else:
            if is_diff:
                # 模拟模式也要能看出差分：每个「角色×姿态×表情」组合一个独立占位文件，
                # 否则节点全指向同一张基底图，等于没差分（验证时会误判成做完了）。
                relative = f'assets/char/待生成-{safe_stem(box)}.png'
                if not self._asset_exists(relative):
                    await self.save_asset(relative, png(*default))
                    generated = True
            else:
                relative = placeholder
                if not self._asset_exists(relative):
                    data = png(*default)
                    await self.save_asset(relative, data)
                    generated = True
                # 占位图不入库：一张纯色块没有任何复用价值，进库只会把真素材挤掉。

        source = getattr(provider, 'kind', 'image') if provider else 'mock'
        if target == 'background':
            bg = dict(asset=relative, prompt=prompt, seed=0,
                      provider=source,
                      place=place, time=moment, weather=sky)
            nodes = [dict(node, bg=bg) if node['id'] in ids else node for node in nodes]
        else:
            old_base = character['ref_image']
            if is_diff:
                nodes = [dict(node, chars=[dict(p, pose=relative)
                                           if p['id'] == char_id else p
                                           for p in node['chars']])
                         if node['id'] in ids else node for node in nodes]
            else:
                characters = [dict(c, ref_image=relative) if c['id'] == char_id else c
                              for c in characters]
                # 只把「还指着旧基底」的节点切到新基底。已经出了差分的节点不能动 ——
                # 否则重画一次基底，之前所有差分图全被冲回同一张脸
                # （和下面 keep_bg 是同一个坑）。
                nodes = [dict(node, chars=[
                    dict(p, pose=relative)
                    if p['id'] == char_id and (p.get('pose') or '') in ('', old_base) else p
                    for p in node['chars']])
                    if node['id'] in ids else node for node in nodes]
        if nodes != self.graph['nodes'] or characters != self.graph['characters']:
            await self._commit(nodes=nodes, characters=characters)
        return success(asset=relative, node_ids=[n['id'] for n in selected], generated=generated,
                       label=phrase, size=f'{width}*{height}', provider=source,
                       transparent=bool(target != 'background'))

    def _art_references(self, kind):
        """收集美术要用的参考图（角色外形锚点）。"""
        items = read_records(self.root, 'refs.json')
        images, seen = [], set()
        for item in reversed(items):
            if item['kind'] != kind or item['asset'] in seen:
                continue
            asset = item['asset']
            try:
                with safe_asset(self.root, asset).open('rb') as source:
                    data = source.read(MAX_REFERENCE_BYTES + 1)
            except (OSError, ValueError) as exc:
                raise ProviderError('REFERENCE_UNREADABLE', f'无法读取参考图 {asset}', 'art.references', 422) from exc
            if len(data) > MAX_REFERENCE_BYTES:
                raise ProviderError('REFERENCE_TOO_LARGE', f'参考图 {asset} 超过百炼的 10 MB 限制', 'art.references', 413)
            images.append(data)
            seen.add(asset)
            if len(images) == MAX_REFERENCE_IMAGES:
                break
        return images

    async def check_consistency(self, args, scope):
        """检查同一角色跨节点是否用了不同立绘。"""
        characters = {c['id']: c for c in self.graph['characters']}
        issues = defaultdict(list)
        for character in self.graph['characters']:
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', character['color']):
                issues[character['id']].append('配色不是六位十六进制')
        # 立绘差分化之后，「节点图必须等于角色基底图」这条判据就不成立了 ——
        # 差分图本来就该和基底不同。真正要查的是「文件在不在」和「张数超没超预算」。
        used = defaultdict(set)
        for node in self.graph['nodes']:
            for pose in node['chars']:
                cid = pose['id']
                if cid not in characters:
                    issues[cid].append(f'{node["id"]} 用了不存在的角色')
                    continue
                asset = pose.get('pose') or ''
                used[cid].add(asset)
                if not self._asset_exists(asset):
                    issues[cid].append(f'{node["id"]} 的立绘文件不存在：{asset or "(空)"}')
        cap = 1 + int(BUDGET.get('pose_per_char') or 12)
        for cid, paths in used.items():
            if len(paths) > cap:
                issues[cid].append(
                    f'用了 {len(paths)} 张立绘，超出「基底 + {cap - 1} 张差分」的预算')
        return success(issues=[dict(character_id=cid, problems=problems) for cid, problems in sorted(issues.items())])

    # ---- 风格圣经：全片唯一的美术输入源 ----
    def _style_prefix(self):
        """把风格圣经压成一句短前缀。不堆形容词，只给画法 / 光影 / 主色调。"""
        style = self.graph.get('style') or {}
        bits = []
        medium = str(style.get('medium') or '').strip()
        light = str(style.get('light') or '').strip()
        palette = [str(c) for c in (style.get('palette') or []) if str(c).strip()][:4]
        if medium:
            bits.append(f'画法{medium}')
        if light:
            bits.append(f'光影{light}')
        if palette:
            bits.append('色调' + ' '.join(palette))
        return '，'.join(bits)

    async def set_style(self, args, scope):
        """定全书风格圣经（配色、画法、光影，生图前必须先定）。"""
        current = self.graph.get('style')
        if current and not args.get('overwrite'):
            return success(style=current, existed=True,
                           note='风格圣经已存在，沿用；要改风格请带 overwrite=true')
        palette = [str(c).strip() for c in (args.get('palette') or []) if str(c).strip()]
        _need(palette, 'EMPTY_PALETTE', '色板是空的', '至少给 4 个十六进制颜色')
        bad = [c for c in palette if not re.fullmatch(r'#?[0-9a-fA-F]{6}', c)]
        _need(not bad, 'INVALID_COLOR', f'色值格式不对：{bad[:3]}', '用 #RRGGBB 或 RRGGBB')
        style = dict(
            palette=[c if c.startswith('#') else '#' + c for c in palette],
            medium=str(args.get('medium') or '').strip(),
            light=str(args.get('light') or '').strip(),
            typeface=str(args.get('typeface') or '').strip(),
            negative=[str(n).strip() for n in (args.get('negative') or []) if str(n).strip()],
            refs=[str(r).strip() for r in (args.get('refs') or []) if str(r).strip()],
            version=int((current or {}).get('version') or 0) + 1,
        )
        await self._commit(style=style)
        return success(style=style, existed=False,
                       note='之后每张图都用 draft_prompt 生成提示词，不要自己编')

    async def draft_prompt(self, args, scope):
        """按风格圣经起草一条生图提示词。"""
        target = args['target']
        rule = ART_RULES[target]
        brief = args['brief'].strip()
        _need(brief, 'EMPTY_PROMPT', '这次要画什么没写', '填写地点与光线，或角色的姿态与情绪')
        style = self.graph.get('style')
        _need(style, 'STYLE_MISSING', '还没定风格圣经', '先调用 set_style，否则各画各的会三套画风')
        parts = [item for item in (self._style_prefix(), rule['prefix'], brief, rule['suffix']) if item]
        negative = list(dict.fromkeys(
            [str(n) for n in (style.get('negative') or []) if str(n).strip()] + rule['negative']))
        return success(prompt='。'.join(parts), negative='，'.join(negative), target=target,
                       note='把 prompt 与 negative 原样交给 generate_image')

    async def generate_ui(self, args, scope):
        """按关键词生成一套界面：选预设 → 渲染 → 自检。"""
        surface = args['surface']
        prompt = args['prompt'].strip()
        _need(prompt, 'EMPTY_PROMPT', '界面描述为空', '先用 draft_prompt(target=ui) 生成')
        width, height = self.params.get('resolution', self.graph['project']['resolution'])
        if args.get('size'):
            size = re.fullmatch(r'([0-9]{2,4})[*x×]([0-9]{2,4})', args['size'])
            _need(size, 'INVALID_SIZE', '图片尺寸格式错误', '使用如 2560*1600 的写法')
            width, height = map(int, size.groups())
            _need(64 <= width <= 8192 and 64 <= height <= 8192, 'INVALID_SIZE',
                  '图片边长须为 64–8192', '调整尺寸')
        provider = self.providers.get('art')
        negative = str(args.get('negative') or '').strip()
        description = f'{prompt}\n避免：{negative}' if negative else prompt
        if provider:
            data = await provider.call(description, width=width, height=height)
            _need(isinstance(data, bytes) and data.startswith(b'\x89PNG\r\n\x1a\n'), 'INVALID_IMAGE',
                  '供应商未返回可导出的 PNG 图像', '调整图像供应商的输出格式后重试')
            relative = f'assets/ui/{surface}-{sha256(data).hexdigest()[:16]}.png'
            if not self._asset_exists(relative):
                await self.save_asset(relative, data)
        else:
            relative = f'assets/ui/mock-{surface}.png'
            if not self._asset_exists(relative):
                await self.save_asset(relative, png(max(64, width // 8), max(64, height // 8),
                                                    (24, 24, 32, 255)))
        state = dict(self.state)
        ui = dict(state.get('ui') or {})
        ui[surface] = dict(asset=relative, prompt=prompt,
                           provider=getattr(provider, 'kind', 'image') if provider else 'mock',
                           size=f'{width}*{height}')
        state['ui'] = ui
        await self._commit(agent_state=state)
        return success(surface=surface, asset=relative, provider=ui[surface]['provider'])

    async def check_style(self, args, scope):
        """验收关卡：只查元数据有没有按规范做，不查画面好不好看。"""
        style = self.graph.get('style')
        issues = []
        if not style:
            issues.append(dict(where='graph.style', problem='还没定风格圣经，先调 set_style'))
        else:
            palette = style.get('palette') or []
            if len(palette) < 4:
                issues.append(dict(where='graph.style.palette',
                                   problem=f'色板只有 {len(palette)} 色，至少 4 色（主/辅/强调/中性）'))
            for field in ('medium', 'light'):
                if not str(style.get(field) or '').strip():
                    issues.append(dict(where=f'graph.style.{field}',
                                       problem='为空，出图没有统一基准'))
        for character in self.graph['characters']:
            if not str(character.get('look') or '').strip():
                issues.append(dict(where=f"character:{character['id']}",
                                   problem='look 为空，这个角色的立绘每次都会变样'))
        prefix = self._style_prefix()
        marker = prefix.split('，')[0] if prefix else ''
        for node in self.graph['nodes']:
            bg = node.get('bg') or {}
            if bg.get('provider') in ('mock', 'pending', None) or not bg.get('prompt'):
                continue
            if marker and marker not in bg['prompt']:
                issues.append(dict(where=f"node:{node['id']}",
                                   problem='已出的背景没用 draft_prompt，提示词里没有风格前缀'))
                break
        return success(issues=issues, style=style, marker=marker)

    # ------------------------------------------------ 界面（ui agent）
    # 界面的做法和美术一样：先选预设，再渲染。从不允许「从零画一套界面」——
    # 那正是启动页一个风格、对话框另一个风格的老毛病来源。
    UI_DEFAULT_MENU = ('开始游戏', '继续游戏', '读取存档', '设置')

    def _ui_tweaks(self):
        """取用户的微调参数。"""
        return dict(self.state.get('ui_style_tweaks') or {})

    def _ui_preset(self, preset_id):
        """按 id 取界面预设。"""
        try:
            base = ui_presets.get(str(preset_id or ''))
        except KeyError as exc:
            raise ToolInputError('PRESET_NOT_FOUND', str(exc),
                                 '先调 list_ui_presets 取预设 id') from exc
        return _apply_ui_tweaks(base, self._ui_tweaks().get(base['id']) or {})

    def _ui_asset(self, kind, skip=''):
        """项目里挑一张已经有内容的图（跳过 mock 那种几百字节的占位）。"""
        base = self.root / 'assets' / kind
        if not base.is_dir():
            return ''
        for item in sorted(base.glob('*.png')):
            if skip and str(item) == str(skip):
                continue
            try:
                if item.stat().st_size > 20_000:
                    return str(item)
            except OSError:
                continue
        return ''

    async def list_ui_presets(self, args, scope):
        """列出所有界面预设。"""
        presets = ui_presets.all_presets()
        return success(
            presets=[ui_presets.brief(p) for p in presets],
            note='挑一套用 render_ui；同一部作品的所有界面只能用同一套预设',
        )

    async def match_ui_preset(self, args, scope):
        """按关键词挑最贴合的一套预设。"""
        text = str(args['keywords'] or '').strip()
        _need(text, 'EMPTY_KEYWORDS', '关键词是空的', '把用户的原始描述传进来，不要自己改写')
        hits = ui_presets.match(text)
        if not hits:
            return success(matches=[], hit=False, strength='none',
                           note='没有任何预设命中这些词。照实告诉用户，并列出现有预设让他挑 —— '
                                '不许硬套一个气质不对的')
        strong = [item for item in hits if item.get('strength') == 'strong']
        note = ('取 score 最高的 strong 项；同一档里命中词多的更可信'
                if strong else
                '只沾到边缘词（全是 weak）—— 气质很可能不对，先跟用户确认再渲染')
        return success(matches=hits, hit=True,
                       strength='strong' if strong else 'weak', note=note)

    async def render_ui(self, args, scope):
        """渲染选定预设的界面 HTML。"""
        preset = self._ui_preset(args['preset'])
        screen = str(args.get('screen') or 'title')
        _need(screen in ui_presets.SCREENS, 'INVALID_SCREEN',
              f'screen 必须是 {ui_presets.SCREENS} 之一', '用 title 或游戏内五块皮之一')
        title = str(args.get('title') or self.graph['project'].get('title') or '奇迹工坊').strip()
        _need(title, 'EMPTY_TITLE', '标题是空的', '给作品一个标题')
        menu = [str(item).strip() for item in (args.get('menu') or []) if str(item).strip()]
        _need(len(menu) <= 6, 'TOO_MANY_MENU', f'菜单有 {len(menu)} 项，最多 6 项',
              '4 项最稳：开始游戏 / 继续游戏 / 读取存档 / 设置')
        menu = menu or list(self.UI_DEFAULT_MENU)

        bg = str(args.get('bg') or '').strip() or self._ui_asset('bg')
        left = str(args.get('char_left') or '').strip() or self._ui_asset('char')
        right = (str(args.get('char_right') or '').strip()
                 or self._ui_asset('char', skip=left))
        subtitle = str(args.get('subtitle') or 'WHALE FALL WORKSHOP')
        version = str(args.get('version') or 'v0.1.0')

        html = ui_presets.render(
            preset, screen, title=title, subtitle=subtitle, menu=tuple(menu),
            version=version, bg=bg, char_left=left, char_right=right)
        relative = f"ui/rendered/{preset['id']}-{screen}.html"
        path = self.root / relative
        await asyncio.to_thread(_write_text_file, path, html)

        state = dict(self.state)
        style = dict(state.get('ui_style') or {})
        style[screen] = dict(preset=preset['id'], name=preset['name'], path=relative,
                             title=title, menu=menu, bytes=len(html.encode('utf-8')))
        state['ui_style'] = style
        await self._commit(agent_state=state)
        return success(path=str(path), relative=relative, preset=preset['id'],
                       preset_name=preset['name'], screen=screen, bytes=len(html.encode('utf-8')),
                       used_bg=bool(bg), used_characters=bool(left or right),
                       next='自己截图核对这张界面，再调 check_ui')

    async def tweak_ui_preset(self, args, scope):
        """把用户的微调写进预设并重新渲染。"""
        preset_id = str(args['preset'] or '')
        try:
            ui_presets.get(preset_id)
        except KeyError as exc:
            raise ToolInputError('PRESET_NOT_FOUND', str(exc), '先 list_ui_presets 取预设 id') from exc

        all_tweaks = dict(self._ui_tweaks())
        if args.get('reset'):
            all_tweaks.pop(preset_id, None)
            await self._commit(agent_state={**self.state, 'ui_style_tweaks': all_tweaks})
            return success(preset=preset_id, tweaks={}, note='已清掉这套预设的所有覆盖')

        tweaks = dict(all_tweaks.get(preset_id) or {})
        for key, field in (('accent', 'accent'), ('bg_color', 'bg'), ('fg_color', 'fg')):
            if args.get(key):
                value = str(args[key]).strip()
                _need(re.fullmatch(r'#[0-9a-fA-F]{6}', value), 'INVALID_COLOR',
                      f'{key}={value!r} 不是 #RRGGBB', '用 6 位十六进制，如 #b8443a')
                tweaks[field] = value
        if args.get('title_size') is not None:
            size = int(args['title_size'])
            _need(40 <= size <= 140, 'INVALID_SIZE', f'标题字号 {size} 超出 40~140',
                  '取 56~96 之间通常最稳')
            tweaks['title_size'] = size
        if args.get('letter_spacing'):
            spacing = str(args['letter_spacing']).strip()
            _need(re.fullmatch(r'0?\.\d+em|\d+px', spacing), 'INVALID_SPACING',
                  f'字距 {spacing!r} 格式不对', '用 0.10em 或 2px 这种写法')
            tweaks['letter_spacing'] = spacing
        if args.get('menu'):
            tweaks['menu'] = args['menu']
        _need(tweaks, 'EMPTY_TWEAK', '没有任何要改的参数', '至少给一个字段')

        all_tweaks[preset_id] = tweaks
        await self._commit(agent_state={**self.state, 'ui_style_tweaks': all_tweaks})
        return success(preset=preset_id, tweaks=tweaks,
                       note='之后的 render_ui 会自动套用这些覆盖；别忘了重新渲染并截图')

    async def check_ui(self, args, scope):
        """界面自检：截图核对排版与可读性。"""
        issues = []
        rendered = dict(self.state.get('ui_style') or {})
        if not rendered:
            issues.append(dict(where='ui_style', problem='还没渲染过任何界面，先调 render_ui'))

        checks = []
        for screen, record in sorted(rendered.items()):
            path = self.root / record['path']
            item = dict(screen=screen, preset=record['preset'], file_exists=path.is_file())
            if not item['file_exists']:
                issues.append(dict(where=f'ui:{screen}', problem=f"渲染文件不存在（{record['path']}）"))
                checks.append(item)
                continue
            html = path.read_text(encoding='utf-8')
            item['bytes'] = len(html.encode('utf-8'))

            body = html.split('</style>')[-1]
            if '$' in body or '${' in html:
                issues.append(dict(where=f'ui:{screen}', problem='HTML 里有没替换掉的占位符'))

            links = html.count('<a href="#">')
            item['menu_items'] = links
            if screen == 'title' and links < 2:
                issues.append(dict(where=f'ui:{screen}', problem=f'菜单只有 {links} 项，至少 2 项'))

            missing = [ref for ref in _ui_image_refs(html) if not _path_exists(ref)]
            item['images'] = len(_ui_image_refs(html))
            if missing:
                issues.append(dict(where=f'ui:{screen}', problem=f'图片引用不存在：{missing[:3]}'))

            try:
                # 必须用「套过微调」的预设来比 —— 否则微调过的强调色会被误判成没套上（踩过）
                preset = self._ui_preset(record['preset'])
            except ToolInputError:
                issues.append(dict(where=f'ui:{screen}', problem=f"预设 {record['preset']!r} 已经不存在"))
                checks.append(item)
                continue
            size = int(preset['layout'].get('title_size') or 0)
            item['title_size'] = size
            if screen == 'title' and size and size < 40:
                issues.append(dict(where=f'ui:{screen}', problem=f'标题字号只有 {size}px，太小'))
            accent = str(preset['palette'].get('accent') or '')
            if accent and accent.lower() not in html.lower():
                issues.append(dict(where=f'ui:{screen}',
                                   problem=f'强调色 {accent} 没出现在界面里，可能没套上预设'))
            checks.append(item)

        return success(issues=issues, rendered=checks,
                       note='issues 为空才算过关；有 issue 必须改完再回报')

    async def design_ui(self, args, scope):
        """按「文字要求 + 参考图」设计一套新界面预设，并渲染出来。

        和 `render_ui` 的分工：`render_ui` 只能从四套现成预设里挑；
        这里能按用户的具体要求生成新的那一套。

        设计**只改参数**（配色 / 字体 / 字号 / 字距 / 立绘开关 / 圆角），
        骨架仍复用最贴近的内置预设（`preset['base']`）——
        这是六块界面不散架的前提。参考图只用于取色与判断明暗，
        原图不落盘、不进成品。
        """
        requirement = str(args.get('requirement') or '').strip()
        references = [str(item) for item in (args.get('reference_images') or []) if item]
        _need(len(references) <= ui_presets.MAX_REFERENCES, 'TOO_MANY_REFERENCES',
              f'参考图有 {len(references)} 张，最多 {ui_presets.MAX_REFERENCES} 张',
              '挑最接近最终效果的那几张传就行，不用一股脑全塞')
        target = str(args.get('target') or 'both')
        _need(target in ('title', 'dialog', 'both'), 'INVALID_TARGET',
              f'target={target!r} 不在 title / dialog / both 里', '用 title、dialog 或 both')

        title = str(self.graph['project'].get('title') or '奇迹工坊').strip()
        preset, path = await asyncio.to_thread(
            ui_presets.run_design, requirement=requirement, references=references,
            base_id=str(args.get('base') or ''), title=title)

        screens = ('title', 'dialog') if target == 'both' else (target,)
        menu = list(self.UI_DEFAULT_MENU)
        rendered = []
        for screen in screens:
            html = ui_presets.render(preset, screen, title=title, menu=menu)
            # 和 HTTP 接口保持同一个位置：都在 assets/ 下，
            # 这样渲染出来的 HTML 自己引用的相对资源也都在 assets 里，搬走不散。
            relative = f"assets/ui/rendered/{preset['id']}-{screen}.html"
            await asyncio.to_thread(_write_text_file, self.root / relative, html)
            rendered.append(dict(screen=screen, relative=relative,
                                 bytes=len(html.encode('utf-8'))))

        state = dict(self.state)
        style = dict(state.get('ui_style') or {})
        for item in rendered:
            style[item['screen']] = dict(preset=preset['id'], name=preset['name'],
                                         path=item['relative'], title=title,
                                         menu=menu, bytes=item['bytes'])
        state['ui_style'] = style
        await self._commit(agent_state=state)

        design = dict(preset.get('design') or {})
        return success(
            preset=dict(id=preset['id'], name=preset['name'], vibe=preset['vibe'],
                        base=preset['base'], palette=dict(preset['palette']),
                        notes=preset['notes'], file=str(path)),
            screens=rendered,
            analysis=dict(colors=list(design.get('colors') or []),
                          matched=list(design.get('matched') or []),
                          contrast=design.get('contrast'),
                          references_used=design.get('references_used', 0)),
            unsupported=list(design.get('unsupported') or []),
            next='先自己截图核对这两块界面，再调 check_ui；'
                 '不满意就重调 design_ui（换要求或换参考图），别手改渲染出的 HTML',
        )

    def _voice_for(self, who):
        """角色 id → 配音音色 id。

        没标注音色的角色返回空串，由 TTS 那边用默认音色（不要把空串当音色传下去）。
        """
        if not who:
            return ''
        return next((c.get('voice_id') or '' for c in self.graph['characters']
                     if c.get('id') == who), '')

    async def list_pending_lines(self, args, scope):
        """列出还没配音的台词（每条已带 voice_id，配音阶段直接用）。"""
        chapter_id = args.get('chapter_id', '')
        if chapter_id:
            self._chapter(chapter_id)
        limit = args.get('limit', 40)
        _need(1 <= limit <= 200, 'INVALID_LIMIT', 'limit 须为 1–200', '使用默认 40 条，按章节查看')
        nodes = self._nodes(chapter_id) if chapter_id else self.graph['nodes']
        pending = []
        for node in nodes:
            meta = self.node_meta.get(node['id'], {})
            for index, line in enumerate(node['lines']):
                if not self._asset_exists(line.get('voice')):
                    pending.append(dict(chapter_id=meta.get('chapter_id', ''), node_ref=meta.get('ref', node['id']),
                                        node_id=node['id'], line_index=index, text=line['text'],
                                        who=line['who'], voice_id=self._voice_for(line['who'])))
        return success(lines=pending[:limit], pending=len(pending))

    async def _synthesize(self, node, index, voice_id='', overwrite=False):
        """合成一句台词，并把真实时长写回 dur。"""
        line = node['lines'][index]
        if not overwrite and self._asset_exists(line.get('voice')):
            return line, False
        provider = self.providers.get('voice')
        if provider:
            voice_id = voice_id or self._voice_for(line['who'])
            result = await provider.call(line['text'], voice_id=voice_id) if voice_id and voice_id != 'mock' else await provider.call(line['text'])
            data = result.get('data') if isinstance(result, dict) else None
            seconds = audio_seconds(data) if isinstance(data, bytes) else None
            _need(seconds is not None and seconds > 0, 'INVALID_AUDIO', '供应商未返回有时长的 WAV', '检查 TTS 输出格式后重试')
            # 文件名用台词本身：用户一眼能看出这句是哪句（原来是一串哈希）。
            # 重新合成同一句时内容变了 → 拿到新序号，旧文件不动，
            # 仍被别的节点引用的旧音频不会被覆盖。
            phrase = first_clause(line['text'], 24) or line.get('who') or '台词'
            relative, _ = await self._save_named('assets/voice', phrase, data, suffix='.wav')
        else:
            seconds = _duration(line['text'])
            relative = 'assets/voice/mock.wav' if seconds == 1.0 else f'assets/voice/silent_{seconds:.3f}.wav'
            data = silent_wav(seconds)
        if not self._asset_exists(relative):
            await self.save_asset(relative, data)
        return dict(line, voice=relative, dur=seconds), True

    async def synthesize_line(self, args, scope):
        """合成单句台词。"""
        chapter_id = args['chapter_id']
        if chapter_id:
            self._chapter(chapter_id)
        by_id = {n['id']: n for n in self._nodes(chapter_id)}
        nid = self._resolve(args['node_ref'], chapter_id, self.node_meta, by_id)
        _need(nid in by_id, 'NODE_NOT_FOUND', '本章没有该节点', '调用 list_pending_lines 获取节点 ref 或 id')
        node = by_id[nid]
        index = args['line_index']
        _need(0 <= index < len(node['lines']), 'LINE_NOT_FOUND', '台词序号超出节点范围', '使用从 0 开始的 line_index')
        line, generated = await self._synthesize(node, index, args.get('voice_id', ''))
        if generated:
            lines = list(node['lines'])
            lines[index] = line
            await self._commit(nodes=[dict(n, lines=lines) if n['id'] == nid else n for n in self.graph['nodes']])
        return success(node_id=nid, line_index=index, generated=generated, asset=line['voice'], dur=line['dur'])

    async def batch_synthesize(self, args, scope):
        """批量合成一章的待配台词。"""
        chapter_id = args['chapter_id']
        if chapter_id:
            self._chapter(chapter_id)
        updates, failures = {}, []
        count = 0
        for node in self._nodes(chapter_id):
            lines = list(node['lines'])
            for index in range(len(lines)):
                try:
                    line, generated = await self._synthesize(node, index, overwrite=args.get('overwrite', False))
                except ToolInputError as exc:
                    failures.append(dict(node_id=node['id'], line_index=index, code=exc.code, message=str(exc)))
                    continue
                except ProviderError as exc:
                    failures.append(dict(node_id=node['id'], line_index=index, code=exc.payload['code'],
                                         message=self._provider_message('voice', exc)))
                    continue
                if generated:
                    lines[index] = line
                    count += 1
            if lines != node['lines']:
                updates[node['id']] = dict(node, lines=lines)
        if updates:
            await self._commit(nodes=[updates.get(n['id'], n) for n in self.graph['nodes']])
        result = dict(chapter_id=chapter_id, generated=count, node_ids=list(updates), failures=failures)
        if failures:
            return failure('VOICE_BATCH_FAILED', f'{len(failures)} 条台词合成失败',
                           '查看 failures 中的错误，保留已完成结果后重试本章', **result)
        return success(**result)

    async def check_assets(self, args, scope):
        """装配前检查素材是否齐全。"""
        missing = []
        counts = dict(backgrounds=0, poses=0, voices=0, references=0)
        checked = {}

        def exists(asset):
            """内部小工具：资产是否存在（带缓存，避免重复读盘）。"""
            if asset not in checked:
                checked[asset] = self._asset_exists(asset)
            return checked[asset]

        for node in self.graph['nodes']:
            assets = []
            if not exists(node['bg']['asset']):
                assets.append(dict(kind='background', asset=node['bg']['asset']))
                counts['backgrounds'] += 1
            for pose in node['chars']:
                if not exists(pose['pose']):
                    assets.append(dict(kind='pose', character_id=pose['id'], asset=pose['pose']))
                    counts['poses'] += 1
            for index, line in enumerate(node['lines']):
                if (self.params.get('auto_voice', True) or line.get('voice')) and not exists(line.get('voice')):
                    assets.append(dict(kind='voice', line_index=index, asset=line.get('voice')))
                    counts['voices'] += 1
            if assets:
                missing.append(dict(node_id=node['id'], assets=assets))
        characters = [dict(character_id=c['id'], asset=c['ref_image']) for c in self.graph['characters']
                      if not exists(c['ref_image'])]
        counts['references'] = len(characters)
        # 素材库里的语音登记：每条标注的是「谁的声音」。
        # 生成 Agent 靠这个判断某条现成语音能不能配给某个角色；没标注的只能瞎猜，
        # 所以把未标注的 id 一起报出来，让它知道该先请主人标注。
        voice_library = []
        try:
            for item in read_records(self.root, 'voices.json'):
                voice_library.append(dict(
                    id=item.get('id') or '',
                    character_id=item.get('character_id') or '',
                    label=item.get('label') or '',
                    asset=item.get('asset') or '',
                    seconds=item.get('seconds') or 0,
                ))
        except Exception:                              # noqa: BLE001
            voice_library = []
        unlabeled = [v['id'] for v in voice_library if not v['character_id']]
        return success(missing=missing, characters=characters, counts=counts,
                       voice_library=voice_library, unlabeled_voices=unlabeled)

    async def run_export(self, args, scope):
        """导出可玩工程包。"""
        checked = await self.validate_graph(dict(scope='all'), None)
        if not checked['ok']:
            return checked
        assets = await self.check_assets({}, None)
        if any(assets['counts'].values()):
            return failure('ASSETS_INCOMPLETE', '项目仍缺少导出所需素材', '调用 check_assets 定位缺失素材并补齐',
                           counts=assets['counts'], missing=assets['missing'], characters=assets['characters'])
        graph = public_graph(self.graph)
        try:
            await asyncio.to_thread(validate_graph, graph, self.root, self.params.get('auto_voice', True))
            result = await asyncio.to_thread(export_game, self.root, self.graph, args['format'])
        except (ValueError, wave.Error, EOFError) as exc:
            return failure('GRAPH_NOT_READY', str(exc), '修复图结构或损坏的素材后重试导出')
        out = result['out'] if isinstance(result, dict) else result
        return success(format=args['format'], out=str(out), pipeline=stage_report(self.graph))

    # ---------------- 质检（只读，不改任何节点） ----------------
    async def inspect_graph(self, args, scope):
        """跑一遍成品检查，返回问题清单。

        检查口径见 app/skills/review/SKILL.md —— 要加/改检查项，先改那份手册，
        再回来改这里，两边别脱节。
        这个工具永远不写图：质检报告只能描述问题，修是别的 agent 的事。
        """
        want = args.get('scope') or 'all'
        nodes = self.graph['nodes']
        by_id = {node['id']: node for node in nodes}
        issues = []
        checked = []

        def add(level, code, message, node_id='', where=''):
            """内部小工具：往集合里加一条。"""
            issues.append(dict(level=level, code=code, node_id=node_id,
                               where=where, message=message))

        # ---- 结构 ----
        if want in ('all', 'structure'):
            checked.append('结构')
            starts = [n for n in nodes if n['type'] == 'start']
            if not starts:
                add('error', 'NO_START', '整部作品没有起点节点，玩家进不去')
            elif len(starts) > 1:
                add('error', 'MULTI_START', f'有 {len(starts)} 个起点节点，正常只应该是 1 个')

            for node in nodes:
                if node['type'] != 'ending' and not (node.get('choices') or []):
                    add('error', 'DEAD_END', '这个节点没有任何出边，玩家走到这里会卡住',
                        node['id'], node.get('title') or '')

            if starts:
                reachable, stack = set(), [starts[0]['id']]
                while stack:
                    nid = stack.pop()
                    if nid in reachable or nid not in by_id:
                        continue
                    reachable.add(nid)
                    stack.extend(choice['to'] for choice in by_id[nid].get('choices') or []
                                 if choice.get('to') in by_id)
                for node in nodes:
                    if node['id'] not in reachable:
                        add('error', 'UNREACHABLE', '从起点走不到这个节点，玩家永远看不到它',
                            node['id'], node.get('title') or '')

            for node in nodes:
                for index, choice in enumerate(node.get('choices') or []):
                    if choice.get('to') not in by_id:
                        add('error', 'BROKEN_CHOICE',
                            f'第 {index + 1} 个选项指向不存在的节点 {choice.get("to")}',
                            node['id'], node.get('title') or '')

        # ---- 文本 ----
        if want in ('all', 'text'):
            checked.append('文本')
            placeholders = ('待定', 'TODO', 'XXX', '（略）', '[待补]')
            for node in nodes:
                title = (node.get('title') or '').strip()
                if not title:
                    add('warn', 'EMPTY_TITLE', '节点标题是空的', node['id'])
                for line in node['lines']:
                    text = (line.get('text') or '').strip()
                    if not text:
                        add('error', 'EMPTY_LINE', '有一句台词是空的，游戏里会显示成一个空气泡',
                            node['id'], title)
                        continue
                    if len(text) > 120:
                        add('warn', 'LINE_TOO_LONG',
                            f'这句台词 {len(text)} 字，对话框装不下', node['id'], title)
                    hit = next((p for p in placeholders if p in text), None)
                    if hit:
                        add('error', 'PLACEHOLDER',
                            f'台词里还留着占位符「{hit}」：{text[:24]}', node['id'], title)

        # ---- 素材 ----
        if want in ('all', 'assets'):
            checked.append('素材')
            assets = await self.check_assets({}, None)
            for entry in assets.get('missing') or []:
                for asset in entry['assets']:
                    add('error', 'MISSING_ASSET',
                        f'素材文件不存在（{asset["kind"]}）：{asset.get("asset") or "空路径"}',
                        entry['node_id'])
            for char in assets.get('characters') or []:
                add('error', 'MISSING_REF', f'角色参考图不存在：{char.get("asset")}')

        # ---- 一致性 ----
        if want in ('all', 'consistency'):
            checked.append('一致性')
            for char in self.graph['characters']:
                images = {p.get('pose') for node in nodes for p in node['chars']
                          if p.get('id') == char['id'] and p.get('pose')}
                refs = images | ({char['ref_image']} if char.get('ref_image') else set())
                if len(refs) > 2:
                    add('warn', 'REF_DRIFT',
                        f'角色「{char.get("name") or char["id"]}」用了 {len(refs)} 张不同立绘，长相可能漂')

            scenes = defaultdict(set)
            for node in nodes:
                bg = node.get('bg') or {}
                key = bg.get('key') or bg.get('bg_key') or bg.get('ref')
                if key and bg.get('asset'):
                    scenes[key].add(bg['asset'])
            for key, used in scenes.items():
                if len(used) > 1:
                    add('warn', 'BG_DRIFT', f'同一个场景「{key}」用了 {len(used)} 张不同背景')

        # ---- 规模 ----
        if want in ('all', 'scale'):
            checked.append('规模')
            want_nodes = int(self.params.get('node_count') or 0)
            if want_nodes and abs(len(nodes) - want_nodes) > want_nodes * 0.3:
                add('warn', 'NODE_COUNT_OFF',
                    f'要求 {want_nodes} 个节点，实际生成 {len(nodes)} 个')
            want_minutes = self.params.get('target_minutes')
            if want_minutes:
                lines = [line for node in nodes for line in node['lines']]
                got = round(len(lines) / 8.125) if lines else 0
                if got and abs(got - int(want_minutes)) > int(want_minutes) * 0.4:
                    add('warn', 'DURATION_OFF',
                        f'目标 {want_minutes} 分钟，按台词量估算约 {got} 分钟')

        errors = sum(1 for item in issues if item['level'] == 'error')
        warns = len(issues) - errors
        hint = 'fail' if errors else ('warn' if warns else 'pass')
        return success(issues=issues, checked=checked, errors=errors, warns=warns,
                       nodes=len(nodes), verdict_hint=hint)

    async def file_report(self, args, scope):
        """把质检结论写进项目，用户在完成面板里能看到。"""
        items = args.get('items') or []
        checked = list(args.get('checked') or [])
        errors = sum(1 for item in items if item.get('level') == 'error')
        warns = sum(1 for item in items if item.get('level') == 'warn')

        verdict = args['verdict']
        # 判定往回兜一层：漏报的错误不该被一句 pass 盖过去
        if errors and verdict != 'fail':
            verdict = 'fail'
        elif warns and verdict == 'pass':
            verdict = 'warn'

        score = max(0, min(100, int(args['score'])))
        report = dict(verdict=verdict, score=score, summary=args['summary'],
                      items=items, checked=checked, errors=errors, warns=warns,
                      nodes=len(self.graph['nodes']),
                      at=datetime.now(timezone.utc).isoformat())

        state = self.graph.setdefault('agent_state', {})
        state.setdefault('review', {})['final'] = report
        await self.save_graph(self.graph)
        return success(report=report)
