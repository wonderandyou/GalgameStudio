# -*- coding: utf-8 -*-
"""遍历所有项目，找出参考图「登记有、磁盘没有」的那个。

定位「tab 显示 2 条，列表却空」到底是哪边缺。
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
        with urllib.request.urlopen(BASE + path, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        return {'__http_error': exc.code}
    except Exception as exc:                                   # noqa: BLE001
        return {'__error': f'{type(exc).__name__}: {exc}'}


def main():
    raw = get('/api/projects')
    projects = raw if isinstance(raw, list) else (raw.get('projects') or [])
    print(f'项目总数: {len(projects)}\n')

    rows = []
    for p in projects:
        pid = p.get('id')
        refs = get(f'/api/projects/{pid}/references')
        reg = (refs.get('references') or []) if isinstance(refs, dict) else []
        lib = get(f'/api/projects/{pid}/library')
        libref = (lib.get('reference') or []) if isinstance(lib, dict) else []
        disk_dir = ROOT / 'projects' / pid / 'assets' / 'ref'
        disk = sorted(f.name for f in disk_dir.glob('*')) if disk_dir.is_dir() else []
        if reg or libref or disk:
            rows.append(dict(pid=pid, name=p.get('name'), reg=len(reg), lib=len(libref),
                             disk=len(disk), reg_items=reg, disk_files=disk))

    if not rows:
        print('没有任何项目有参考图数据（登记/磁盘都是 0）')
        return 0

    print(f'{"项目":40s} {"名字":12s} 登记 扫描 磁盘')
    for r in rows:
        flag = '  ← 不一致' if (r['reg'] != r['disk'] or r['reg'] != r['lib']) else ''
        print(f"{r['pid']:40s} {str(r['name'])[:10]:12s} {r['reg']:3d}  {r['lib']:3d}  {r['disk']:3d}{flag}")

    print('\n=== 不一致的明细 ===')
    for r in rows:
        if r['reg'] != r['disk'] or r['reg'] != r['lib']:
            print(f"\n项目 {r['pid']}（{r['name']}）")
            print(f"  登记 {r['reg']} 条：")
            for it in r['reg_items'][:6]:
                print(f"    id={it.get('id')} kind={it.get('kind')} asset={it.get('asset')}")
            print(f"  磁盘 ref 目录：{r['disk_files'][:6]}")
            print(f"  /library 扫描到 {r['lib']} 项")
    return 0


if __name__ == '__main__':
    sys.exit(main())
