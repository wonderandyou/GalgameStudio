# -*- coding: utf-8 -*-
"""诊断：参考图「tab 显示 2 条，列表却是空的」。

对比三个来源：
  1. GET /references   —— 登记表（tab 数字用的）
  2. GET /library      —— 磁盘扫描（列表渲染用的）
  3. 真实磁盘文件       —— 到底有没有
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
ROOT = Path(r'D:\软件\GalgameStudio')


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        return {'__http_error': exc.code, 'body': exc.read().decode('utf-8', 'replace')[:300]}
    except Exception as exc:                                   # noqa: BLE001
        return {'__error': f'{type(exc).__name__}: {exc}'}


def main():
    raw = get('/api/projects')
    # 接口返回可能是裸数组，也可能包一层 {projects: [...]}
    projects = raw if isinstance(raw, list) else (raw.get('projects') or [])
    if not projects:
        print(f'[FAIL] 没有项目；返回形态：{type(raw).__name__} {str(raw)[:200]}')
        return 2
    # 取最近改过的项目
    projects.sort(key=lambda p: str(p.get('updated_at') or p.get('created_at') or ''), reverse=True)
    print('=== 最近的项目 ===')
    for p in projects[:5]:
        print(f"  {p.get('id')}  {p.get('name')}  {p.get('updated_at') or p.get('created_at')}")

    target = projects[0]['id']
    print(f'\n=== 诊断项目 {target} ===')

    refs = get(f'/api/projects/{target}/references')
    reg = refs.get('references') or []
    print(f'\n[1] 登记表 /references：{len(reg)} 条')
    for r in reg[:5]:
        exists = (ROOT / 'projects' / target / r.get('asset', '')).is_file()
        print(f"    id={r.get('id')} kind={r.get('kind')} label={r.get('label')!r} asset={r.get('asset')} 磁盘存在={exists}")

    lib = get(f'/api/projects/{target}/library')
    libref = lib.get('reference') or []
    print(f'\n[2] 磁盘扫描 /library.reference：{len(libref)} 项')
    for x in libref[:5]:
        print(f"    name={x.get('name')} asset={x.get('asset')} bytes={x.get('bytes')}")

    disk = ROOT / 'projects' / target / 'assets' / 'ref'
    files = sorted(disk.glob('*')) if disk.is_dir() else []
    print(f'\n[3] 真实磁盘 {disk}：{len(files)} 个文件')
    for f in files[:8]:
        print(f'    {f.name}  {f.stat().st_size} 字节')

    print('\n=== 结论 ===')
    if reg and not libref:
        print('  ⚠ 有登记、磁盘扫描却是 0 —— 说明 file 落盘位置和 /library 扫的目录不一致')
    elif reg and libref and len(reg) != len(libref):
        print(f'  ⚠ 数量不一致：登记 {len(reg)} vs 磁盘 {len(libref)}')
    elif not reg and libref:
        print('  登记 0、磁盘有文件（删除只删登记的老问题）')
    else:
        print(f'  一致：登记 {len(reg)}，磁盘 {len(libref)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
