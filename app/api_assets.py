"""项目参考图、语音和素材库接口。"""

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PureWindowsPath
from stat import S_ISREG

from fastapi import APIRouter, File, Form, UploadFile

from .asset_records import read_records, register, unregister
from .engine.graph import safe_asset
from .mock_api import jobs, now
from .providers.base import ProviderError, audio_seconds
from .store import atomic_json


router = APIRouter(prefix='/api')
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def _read_upload(file, where):
    """把上传文件读成 bytes，并做大小上限检查。"""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProviderError('FILE_TOO_LARGE', '文件不能超过 20 MB', where, 413, field='file')
    return data


def _label(label, filename):
    """取一个上传项的显示名（原始文件名去掉扩展名）。"""
    return label if label else PureWindowsPath(filename or '').stem


def _add_reference(pid, data, kind, label, name=None, gender=None, character_id=None):
    """把一份参考图落到项目里并登记（可选绑定角色；绑定后名字从角色库取）。"""
    where = 'references.upload'
    root = jobs.project_path(pid)
    if not data.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
        raise ProviderError('INVALID_IMAGE', '请上传有效的 PNG 或 JPEG 图片', where, 422, field='file')

    from PIL import Image

    try:
        with Image.open(BytesIO(data), formats=('PNG', 'JPEG')) as image:
            extension = 'png' if image.format == 'PNG' else 'jpg'
            width, height = image.size
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ProviderError('INVALID_IMAGE', '请上传有效的 PNG 或 JPEG 图片', where, 422, field='file') from exc

    # 角色参考图可以带上「这是谁」：名字 / 性别 / 归到哪个角色。
    # 生成开始时 pipeline 会按这些字段把角色预建进角色表，agent 一开局就知道
    # 「男方叫X、女方叫Y、各自长什么样」，不用自己编名字。
    extra = {}
    if name and name.strip():
        extra['name'] = name.strip()[:40]
    if gender in ('male', 'female', 'other'):
        extra['gender'] = gender
    if character_id:
        extra['character_id'] = character_id
        # 上传时就选好了角色 → 名字和性别按角色库补齐，不再让用户手打
        # （手打的名字和角色库对不上，生成时会把同一个人当成两个角色）
        from .api_characters import _load_graph
        graph, _ready = _load_graph(pid)
        hit = next((c for c in (graph.get('characters') or []) if str(c.get('id')) == character_id), None)
        if hit is None:
            raise ProviderError('CHARACTER_NOT_FOUND', '角色库里没有这个角色',
                                where, 404, field='character_id')
        extra['name'] = str(hit.get('name') or '')[:40]
        extra['gender'] = hit.get('gender') or 'other'

    return register(root, 'refs.json', 'ref_', data, f'assets/ref/{kind}-{{sha16}}.{extension}',
                    kind=kind, label=label, width=width, height=height, created_at=now(), **extra)


@router.post('/projects/{pid}/references')
async def upload_reference(pid: str, file: UploadFile = File(...), kind: str = Form(...),
                           label: str | None = Form(None), name: str | None = Form(None),
                           gender: str | None = Form(None), character_id: str | None = Form(None)):
    """上传参考图的路由处理。"""
    where = 'references.upload'
    if kind not in ('character', 'scene'):
        raise ProviderError('INVALID_KIND', 'kind 必须为 character 或 scene', where, 422, field='kind')
    if gender and gender not in ('male', 'female', 'other'):
        raise ProviderError('INVALID_GENDER', 'gender 只能是 male / female / other',
                            where, 422, field='gender')
    data = await _read_upload(file, where)
    item = await asyncio.to_thread(_add_reference, pid, data, kind, _label(label, file.filename),
                                   name=name, gender=gender, character_id=character_id)
    return dict(ok=True, reference=item)


def _list_records(pid, registry):
    """列出该项目的素材登记记录。"""
    return read_records(jobs.project_path(pid), registry)


@router.get('/projects/{pid}/references')
async def list_references(pid: str):
    """列出参考图。"""
    items = await asyncio.to_thread(_list_records, pid, 'refs.json')
    return dict(ok=True, references=items, count=len(items))


def _delete_record(pid, registry, item_id, *, code, where):
    """删一条素材记录：默认真删文件，被别处引用时只解除引用。"""
    root = jobs.project_path(pid)
    items = read_records(root, registry)
    target = next((item for item in items if item['id'] == item_id), None)
    unregister(root, registry, item_id, code=code, where=where)
    # 真删磁盘文件。以前只删登记不删文件，而素材库底部的「全部素材」是**扫磁盘**的
    # —— 主人删掉参考图后，tab 上显示 0 张，文件却还挂在列表里（主人实测）。
    # ★ 这里曾经只认 assets/ref/，导致「删语音」只删登记、wav 留在磁盘上，
    #   库里照样能看到（主人又踩了一次）。改成 ref 和 voice 都删。
    if not target:
        return
    asset = target.get('asset') or ''
    if not asset.startswith(('assets/ref/', 'assets/voice/')):
        return
    if any(item.get('asset') == asset for item in read_records(root, registry)):
        return
    try:
        safe_asset(root, asset).unlink()
    except (FileNotFoundError, ValueError, OSError):
        pass


@router.delete('/projects/{pid}/references/{ref_id}')
async def delete_reference(pid: str, ref_id: str):
    """删除参考图的路由处理。"""
    await asyncio.to_thread(_delete_record, pid, 'refs.json', ref_id,
                            code='REFERENCE_NOT_FOUND', where='references.delete')
    return dict(ok=True, deleted_id=ref_id, note='登记和图片文件都已删除')


def _rename_reference(pid, ref_id, name, gender, character_id=None):
    """改参考图的名字 / 性别 / 绑定角色；绑定时名字从角色库同步。"""
    root = jobs.project_path(pid)
    items = read_records(root, registry_name := 'refs.json')
    target = next((item for item in items if item['id'] == ref_id), None)
    if target is None:
        raise ProviderError('REFERENCE_NOT_FOUND', '参考图登记不存在', 'references.rename', 404)
    if name is not None:
        target['name'] = name.strip()[:40]
    if gender is not None:
        if gender not in ('male', 'female', 'other'):
            raise ProviderError('INVALID_GENDER', 'gender 只能是 male / female / other',
                                'references.rename', 422, field='gender')
        target['gender'] = gender
    # 参考图挂到角色库里的某个角色上（主人 2026-11-08 定：别再让用户手打名字）。
    # 选了角色就以角色名和性别为准 —— 生成时建角色表读的就是 name/gender，
    # 两套名字对不上会让 Agent 把同一个人当成两个。
    if character_id is not None:
        cid = str(character_id or '').strip()
        target['character_id'] = cid
        if cid:
            from .api_characters import _load_graph
            graph, _ready = _load_graph(pid)
            hit = next((c for c in (graph.get('characters') or []) if str(c.get('id')) == cid), None)
            if hit is None:
                raise ProviderError('CHARACTER_NOT_FOUND', '角色库里没有这个角色',
                                    'references.rename', 404, field='character_id')
            target['name'] = str(hit.get('name') or '')[:40]
            target['gender'] = hit.get('gender') or 'other'
    atomic_json(root / registry_name, dict(version=1, items=items))
    return target


@router.patch('/projects/{pid}/references/{ref_id}')
async def rename_reference(pid: str, ref_id: str, body: dict):
    """改参考图：挂到哪个角色（推荐）/ 名字 / 性别。

    角色参考图应该「从角色库里选一个人」，而不是手打名字 ——
    手打的名字和角色库对不上时，生成出来的剧情会把同一个人当成两个角色。
    """
    item = await asyncio.to_thread(_rename_reference, pid, ref_id,
                                   body.get('name'), body.get('gender'), body.get('character_id'))
    return dict(ok=True, reference=item)


def _add_voice(pid, data, character_id, label):
    """把一段语音落到项目里并登记。"""
    where = 'voices.upload'
    root = jobs.project_path(pid)
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ProviderError('NEED_WAV', '当前环境请上传 PCM WAV 文件', where, 415, field='file')
    seconds = audio_seconds(data)
    if not seconds:
        raise ProviderError('INVALID_AUDIO', 'WAV 文件无法解析或没有采样数据', where, 422, field='file')
    return register(root, 'voices.json', 'voi_', data, 'assets/voice/{sha16}.wav',
                    character_id=character_id, label=label, seconds=seconds, created_at=now())


@router.post('/projects/{pid}/voices')
async def upload_voice(pid: str, file: UploadFile = File(...), character_id: str | None = Form(None),
                       label: str | None = Form(None)):
    """上传语音的路由处理。"""
    data = await _read_upload(file, 'voices.upload')
    item = await asyncio.to_thread(_add_voice, pid, data, character_id, _label(label, file.filename))
    return dict(ok=True, voice=item)


@router.get('/projects/{pid}/voices')
async def list_voices(pid: str):
    """列出语音。"""
    items = await asyncio.to_thread(_list_records, pid, 'voices.json')
    return dict(ok=True, voices=items, count=len(items))


@router.delete('/projects/{pid}/voices/{voice_id}')
async def delete_voice(pid: str, voice_id: str):
    """删除语音的路由处理。"""
    await asyncio.to_thread(_delete_record, pid, 'voices.json', voice_id,
                            code='VOICE_NOT_FOUND', where='voices.delete')
    return dict(ok=True, deleted_id=voice_id, note='登记和音频文件都已删除')


def _update_voice(pid, voice_id, body):
    """改语音的标签 / 绑定角色。"""
    root = jobs.project_path(pid)
    items = read_records(root, 'voices.json')
    target = next((item for item in items if item['id'] == voice_id), None)
    if target is None:
        raise ProviderError('VOICE_NOT_FOUND', '语音登记不存在', 'voices.update', 404)
    if 'character_id' in body:
        target['character_id'] = str(body.get('character_id') or '').strip()[:40]
    if 'label' in body:
        target['label'] = str(body.get('label') or '').strip()[:60]
    atomic_json(root / 'voices.json', dict(version=1, items=items))
    return target


@router.patch('/projects/{pid}/voices/{voice_id}')
async def update_voice(pid: str, voice_id: str, body: dict):
    """给语音改「这是谁的声音」/ 备注。

    素材库上传完一堆语音后，主人要能标出每条是谁说的 —— 生成 Agent 靠这个
    字段判断能不能把这条语音用到某个角色身上，不标它就只能瞎猜。
    """
    item = await asyncio.to_thread(_update_voice, pid, voice_id, body)
    return dict(ok=True, voice=item)


def _delete_library_item(pid, asset):
    """删掉「全部素材」里的一条。

    ★ 这个地方以前根本没有删除入口 —— 「全部素材」是扫磁盘出来的，
      界面上只显示播放器和文件名，删不掉（主人实测）：登录列表能删的只是
      voices.json 里的登记，磁盘上那些没登记的 wav 依然在。
    """
    root = jobs.project_path(pid)
    try:
        target = safe_asset(root, asset)
    except ValueError as exc:
        raise ProviderError('ASSET_INVALID', str(exc), 'library.delete', 422) from exc
    if not target.is_file():
        raise ProviderError('ASSET_NOT_FOUND', '文件不存在', 'library.delete', 404)
    target.unlink()
    touched = []
    for registry in ('voices.json', 'refs.json'):
        try:
            items = read_records(root, registry)
        except Exception:                              # noqa: BLE001
            continue
        kept = [item for item in items if item.get('asset') != asset]
        if len(kept) != len(items):
            atomic_json(root / registry, dict(version=1, items=kept))
            touched.append(registry)
    return dict(ok=True, deleted=asset, registries=touched)


@router.delete('/projects/{pid}/library')
async def delete_library_item(pid: str, asset: str):
    """删掉「全部素材」里的一条（磁盘文件 + 对应登记）。"""
    return await asyncio.to_thread(_delete_library_item, pid, asset)


def _label_by_asset(pid, asset, character_id):
    """按 asset 路径标注语音是谁的声音；没登记的就补一条登记。"""
    root = jobs.project_path(pid)
    target = safe_asset(root, asset)
    items = read_records(root, 'voices.json')
    for item in items:
        if item.get('asset') == asset:
            item['character_id'] = character_id
            atomic_json(root / 'voices.json', dict(version=1, items=items))
            return item
    import uuid as _uuid
    item = dict(id='voi_' + _uuid.uuid4().hex, character_id=character_id, label='',
                asset=asset, seconds=0,
                bytes=target.stat().st_size if target.is_file() else 0,
                created_at=now())
    items.append(item)
    atomic_json(root / 'voices.json', dict(version=1, items=items))
    return item


@router.post('/projects/{pid}/voices/label-by-asset')
async def label_by_asset(pid: str, body: dict):
    """给「全部素材」里直接看到的语音标注角色（不需要先登记）。"""
    asset = str(body.get('asset') or '').strip().replace('\\', '/')
    if not asset.startswith('assets/'):
        raise ProviderError('ASSET_INVALID', 'asset 要以 assets/ 开头', 'library.label', 422)
    item = await asyncio.to_thread(_label_by_asset, pid, asset, str(body.get('character_id') or '').strip())
    return dict(ok=True, voice=item)


def _scan_library(pid):
    """扫一遍素材库目录按类型统计 —— 前端网格显示的是这个真实数，不是登记数。"""
    root = jobs.project_path(pid)
    assets = root / 'assets'
    groups = {}
    for group, directory in (('background', 'bg'), ('character', 'char'), ('reference', 'ref'), ('voice', 'voice')):
        items = []
        for path in sorted((assets / directory).rglob('*')):
            if path.suffix == '.tmp':
                continue
            asset = path.relative_to(root).as_posix()
            try:
                info = safe_asset(root, asset).stat()
            except (FileNotFoundError, ValueError):
                continue
            if S_ISREG(info.st_mode):
                items.append(dict(name=path.relative_to(assets).as_posix(), asset=asset,
                                  bytes=info.st_size, mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()))
        groups[group] = items
    return dict(ok=True, **groups)


@router.get('/projects/{pid}/library')
async def list_library(pid: str):
    """素材库总览：各类数量 + 条目列表。"""
    return await asyncio.to_thread(_scan_library, pid)
