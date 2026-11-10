"""磁盘上的项目素材登记。"""

import hashlib
from threading import Lock
from uuid import uuid4

from .engine.graph import safe_asset
from .providers.base import ProviderError
from .store import atomic_bytes, atomic_json, read_json


_write_lock = Lock()


def read_records(root, registry):
    """读某个项目的素材登记表。"""
    path = root / registry
    if not path.exists():
        return []
    data = read_json(path)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('items'), list):
        raise ProviderError('STATE_CORRUPT', f'{registry} 登记格式损坏', 'assets.registry', 500)
    return data['items']


def register(root, registry, id_prefix, data, asset_name, *, created_at, **fields):
    """登记一条素材（同名同路径则覆盖）。"""
    sha256 = hashlib.sha256(data).hexdigest()
    asset = asset_name.format(sha16=sha256[:16])
    path = safe_asset(root, asset)
    item = dict(id=id_prefix + uuid4().hex, asset=asset, bytes=len(data),
                sha256=sha256, created_at=created_at, **fields)
    with _write_lock:
        items = read_records(root, registry)
        if not path.is_file():
            atomic_bytes(path, data)
        atomic_json(root / registry, dict(version=1, items=[*items, item]))
    return item


def unregister(root, registry, item_id, *, code, where):
    """注销一条素材登记。"""
    with _write_lock:
        items = read_records(root, registry)
        remaining = [item for item in items if item['id'] != item_id]
        if len(remaining) == len(items):
            raise ProviderError(code, '素材登记不存在', where, 404)
        atomic_json(root / registry, dict(version=1, items=remaining))
