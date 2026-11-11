"""项目角色库：角色是一等公民，立绘和语音只能挂到已有角色上。

数据只有一个地方：``graph.json`` 的 ``characters`` 数组。
不另建 characters.json —— 否则 Agent 读 graph、界面读另一份，两边迟早对不上。

立绘（assets/char/*.png）没有登记表，所以「这张立绘属于谁」单独存
``characters_map.json``；语音用 voices.json 里已有的 character_id。
"""

import asyncio
import re
import uuid

from fastapi import APIRouter

from .mock_api import gate_graph, jobs
from .asset_records import read_records
from .providers.base import ProviderError
from .store import atomic_json, read_json


router = APIRouter(prefix='/api')
GENDERS = ('female', 'male', 'other')
# 新角色的默认配色，按顺序轮着用（和界面上的主题色系一致）
PALETTE = ('#8b6bd6', '#d946ef', '#f5a524', '#22b8a6', '#4f8ef7', '#e0559b', '#6c7a89')
MAP_FILE = 'characters_map.json'
WHERE = 'characters'


# ---------------------------------------------------------------- 基础读写

def _graph_path(pid):
    """项目 graph.json 的路径。"""
    return jobs.project_path(pid) / 'graph.json'


def _project_meta(pid):
    """读项目的 project.json；不存在返回空 dict。"""
    path = jobs.project_path(pid) / 'project.json'
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _load_graph(pid):
    """读 graph.json；不存在时返回一座只带 project 的空骨架（不落盘）。"""
    path = _graph_path(pid)
    if not path.exists():
        meta = _project_meta(pid)
        return dict(version=1,
                    project=dict(id=pid, name=meta.get('name') or '',
                                 resolution=meta.get('resolution') or [1920, 1080],
                                 created_at=meta.get('created_at') or ''),
                    characters=[], nodes=[], edges=[]), False
    graph = read_json(path)
    if not isinstance(graph, dict):
        raise ProviderError('STATE_CORRUPT', 'graph.json 结构损坏', WHERE, 500)
    if not isinstance(graph.get('characters'), list):
        graph['characters'] = []
    return graph, True


def _save_graph(pid, graph):
    # 和生成任务共用 graph.json —— 不加锁会和 Agent 写画布互相覆盖
    """把图写回 graph.json（原子写）。"""
    atomic_json(_graph_path(pid), gate_graph(graph, 'api.characters'))


def _read_map(root):
    """读「立绘 → 角色」映射文件。"""
    path = root / MAP_FILE
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except ValueError:
        return {}
    items = data.get('items') if isinstance(data, dict) else None
    return items if isinstance(items, dict) else {}


def _write_map(root, items):
    """写「立绘 → 角色」映射文件。"""
    atomic_json(root / MAP_FILE, dict(version=1, items=items))


def _clean_name(raw):
    """清洗角色名（去首尾空白、限长）。"""
    name = str(raw or '').strip()
    if not name:
        raise ProviderError('CHARACTER_NAME_REQUIRED', '角色名不能为空', WHERE, 422, field='name')
    if len(name) > 20:
        raise ProviderError('CHARACTER_NAME_TOO_LONG', '角色名不能超过 20 个字', WHERE, 422, field='name')
    return name


def _clean_gender(raw):
    """清洗性别字段（只认已知取值，其余留空）。"""
    value = str(raw or 'other').strip().lower()
    return value if value in GENDERS else 'other'


def _clean_color(raw):
    """清洗颜色，必须是 #rrggbb。"""
    value = str(raw or '').strip()
    return value if re.fullmatch(r'#[0-9a-fA-F]{6}', value) else ''


def _find(characters, cid):
    """按 id 在角色表里找角色；找不到抛 404。"""
    for item in characters:
        if str(item.get('id')) == cid:
            return item
    raise ProviderError('CHARACTER_NOT_FOUND', '角色不存在', WHERE, 404)


def _usage(graph, pid, cid):
    """这个角色被多少东西挂着：立绘 / 语音 / 剧情节点。"""
    root = jobs.project_path(pid)
    poses = sum(1 for value in _read_map(root).values() if value == cid)

    voices = 0
    path = root / 'voices.json'
    if path.exists():
        try:
            data = read_json(path)
            voices = sum(1 for item in (data.get('items') or [])
                         if isinstance(item, dict) and item.get('character_id') == cid)
        except ValueError:
            voices = 0

    nodes = 0
    for node in graph.get('nodes') or []:
        if not isinstance(node, dict):
            continue
        hit = any(isinstance(c, dict) and str(c.get('id')) == cid for c in (node.get('chars') or []))
        hit = hit or any(isinstance(l, dict) and str(l.get('who')) == cid for l in (node.get('lines') or []))
        if hit:
            nodes += 1
    return dict(poses=poses, voices=voices, nodes=nodes)


def _decorate(graph, pid, characters):
    """给角色补上统计字段（被引用次数、立绘数）。"""
    root = jobs.project_path(pid)
    assignments = _read_map(root)
    out = []
    for item in characters:
        character = dict(item)
        character['counts'] = _usage(graph, pid, str(item.get('id')))
        out.append(character)
    return out, assignments


# ---------------------------------------------------------------- 接口

@router.get('/projects/{pid}/characters')
async def list_characters(pid: str):
    """角色库列表 + 每个角色挂了几张立绘 / 几条语音 / 几个剧情节点。"""
    def run():
        """列出项目角色（GET /projects/{pid}/characters）。"""
        graph, ready = _load_graph(pid)
        items, assignments = _decorate(graph, pid, graph['characters'])
        return dict(ok=True, characters=items, assignments=assignments, graph_ready=ready)
    return await asyncio.to_thread(run)


@router.post('/projects/{pid}/characters')
async def create_character(pid: str, body: dict):
    """在角色库里新建一个角色。id 由服务端生成，名字项目内不许重复。"""
    name = _clean_name(body.get('name'))
    gender = _clean_gender(body.get('gender'))
    look = str(body.get('look') or '').strip()[:400]
    color = _clean_color(body.get('color'))

    async with jobs.admission:
        jobs.require_idle(pid)

        def run():
            """新建角色（POST /projects/{pid}/characters）。"""
            graph, _ready = _load_graph(pid)
            characters = graph['characters']
            if any(str(item.get('name') or '').strip() == name for item in characters):
                raise ProviderError('CHARACTER_NAME_TAKEN', f'已经有叫「{name}」的角色了', WHERE, 409, field='name')
            picked = color or PALETTE[len(characters) % len(PALETTE)]
            item = dict(id='char_' + uuid.uuid4().hex[:10], name=name,
                        color=picked, ref_image='', voice_id='',
                        look=look, gender=gender)
            graph['characters'] = [*characters, item]
            _save_graph(pid, graph)
            decorated, _ = _decorate(graph, pid, [item])
            return dict(ok=True, character=decorated[0])

        return await asyncio.to_thread(run)


def _refs_path(pid):
    return jobs.project_path(pid) / 'refs.json'


def _read_refs(pid):
    """读角色参考图登记表（refs.json）；坏掉就当空表，别让删角色失败。"""
    try:
        return read_records(jobs.project_path(pid), 'refs.json')
    except (ProviderError, ValueError, OSError):
        return []


def _write_refs(pid, items):
    atomic_json(_refs_path(pid), dict(version=1, items=items))


def _unbind_references(pid, cid):
    """删角色时把参考图上的绑定摘掉。

    ★ 只清 character_id 不够：`pipeline.ensure_characters_from_references`
    的过滤条件是「kind=character 且有 name 或 character_id」，
    参考图上还留着名字（例如「小林」）→ 下一次生成还会按名字把这个角色建回来。
    所以额外打 `detached` 标记：图和名字都留着（用户以后还能继续用），
    但生成器看到它就跳过。用户重新把图挂到某个角色上时标记会被清掉。
    """
    items = _read_refs(pid)
    changed = False
    for ref in items:
        if not isinstance(ref, dict):
            continue
        if str(ref.get('character_id') or '') == cid:
            ref['character_id'] = ''
            ref['detached'] = True
            changed = True
    if changed:
        _write_refs(pid, items)
    return changed


def _rename_references(pid, cid, name):
    """角色改名后同步参考图上记的名字，否则素材库还显示旧名。"""
    items = _read_refs(pid)
    changed = False
    for ref in items:
        if isinstance(ref, dict) and str(ref.get('character_id') or '') == cid:
            if ref.get('name') != name:
                ref['name'] = name
                changed = True
            if ref.pop('detached', None) is not None:
                changed = True
    if changed:
        _write_refs(pid, items)
    return changed


@router.patch('/projects/{pid}/characters/{cid}')
async def update_character(pid: str, cid: str, body: dict):
    """改名 / 改性别 / 改配色 / 改外形锚点。"""
    async with jobs.admission:
        jobs.require_idle(pid)

        def run():
            """更新角色（PATCH /characters/{cid}）。"""
            graph, _ready = _load_graph(pid)
            characters = graph['characters']
            item = _find(characters, cid)
            if 'name' in body:
                name = _clean_name(body.get('name'))
                if any(str(other.get('name') or '').strip() == name and str(other.get('id')) != cid
                       for other in characters):
                    raise ProviderError('CHARACTER_NAME_TAKEN', f'已经有叫「{name}」的角色了',
                                        WHERE, 409, field='name')
                item['name'] = name
                # 参考图跟着改名，否则素材库还显示旧名字（删角色时也一样对不上）
                _rename_references(pid, cid, name)
            if 'gender' in body:
                item['gender'] = _clean_gender(body.get('gender'))
            if 'color' in body:
                item['color'] = _clean_color(body.get('color')) or item.get('color') or PALETTE[0]
            if 'look' in body:
                item['look'] = str(body.get('look') or '').strip()[:400]
            _save_graph(pid, graph)
            decorated, _ = _decorate(graph, pid, [item])
            return dict(ok=True, character=decorated[0])

        return await asyncio.to_thread(run)


@router.delete('/projects/{pid}/characters/{cid}')
async def delete_character(pid: str, cid: str, force: int = 0):
    """删角色。还挂着立绘/语音/剧情时不硬删 —— 先返回 409 说明撞到多少处。

    force=1 才连同挂载一起清掉（立绘映射删除、语音标注置空、节点里的角色移除）。
    """
    async with jobs.admission:
        jobs.require_idle(pid)

        def run():
            """删除角色（DELETE /characters/{cid}）；force=1 时连带清掉所有挂载。"""
            graph, ready = _load_graph(pid)
            characters = graph['characters']
            item = _find(characters, cid)
            counts = _usage(graph, pid, cid)
            total = counts['poses'] + counts['voices'] + counts['nodes']
            if total and not force:
                raise ProviderError(
                    'CHARACTER_IN_USE',
                    f'「{item.get("name")}」还挂着 {counts["poses"]} 张立绘、'
                    f'{counts["voices"]} 条语音、{counts["nodes"]} 个剧情节点',
                    WHERE, 409, field='force')

            root = jobs.project_path(pid)
            if total:
                assignments = _read_map(root)
                kept = {k: v for k, v in assignments.items() if v != cid}
                if len(kept) != len(assignments):
                    _write_map(root, kept)

                voices_path = root / 'voices.json'
                if voices_path.exists():
                    try:
                        data = read_json(voices_path)
                        items = data.get('items') or []
                        changed = False
                        for voice in items:
                            if isinstance(voice, dict) and voice.get('character_id') == cid:
                                voice['character_id'] = ''
                                changed = True
                        if changed:
                            atomic_json(voices_path, dict(version=1, items=items))
                    except ValueError:
                        pass

                touched = False
                for node in graph.get('nodes') or []:
                    if not isinstance(node, dict):
                        continue
                    chars = node.get('chars') or []
                    kept_chars = [c for c in chars if not (isinstance(c, dict) and str(c.get('id')) == cid)]
                    if len(kept_chars) != len(chars):
                        node['chars'] = kept_chars
                        touched = True
                    for line in node.get('lines') or []:
                        if isinstance(line, dict) and str(line.get('who')) == cid:
                            line['who'] = ''
                            touched = True
                if touched:
                    _save_graph(pid, graph)

            graph['characters'] = [c for c in characters if str(c.get('id')) != cid]
            _save_graph(pid, graph)
            # ★ 参考图必须一起解绑（无条件，不看 total）：不清的话下一次生成
            #   会按 refs.json 里的名字把刚删掉的角色重新建出来。
            if _unbind_references(pid, cid):
                counts['references'] = counts.get('references', 0) + 1
            return dict(ok=True, id=cid, cleared=counts)

        return await asyncio.to_thread(run)


@router.post('/projects/{pid}/characters/assign')
async def assign_character(pid: str, body: dict):
    """把一张立绘 / 一条语音挂到某个角色上（character_id 为空 = 取消挂载）。"""
    asset = str(body.get('asset') or '').strip().replace('\\', '/')
    cid = str(body.get('character_id') or '').strip()
    if not asset.startswith('assets/'):
        raise ProviderError('ASSET_INVALID', 'asset 要以 assets/ 开头', WHERE, 422, field='asset')

    def run():
        """把某个素材（立绘 / 语音）指派给某角色（POST /characters/assign）。"""
        graph, _ready = _load_graph(pid)
        if cid:
            _find(graph['characters'], cid)   # 角色必须存在，不许挂到不存在的 id 上
        root = jobs.project_path(pid)

        if asset.startswith('assets/char/'):
            assignments = _read_map(root)
            if cid:
                assignments[asset] = cid
            else:
                assignments.pop(asset, None)
            _write_map(root, assignments)
            return dict(ok=True, asset=asset, character_id=cid, kind='pose')

        if asset.startswith('assets/voice/'):
            from .api_assets import _label_by_asset
            item = _label_by_asset(pid, asset, cid)
            return dict(ok=True, asset=asset, character_id=cid, kind='voice', voice=item)

        raise ProviderError('ASSET_UNSUPPORTED', '只有立绘和语音能挂角色', WHERE, 422, field='asset')

    return await asyncio.to_thread(run)
