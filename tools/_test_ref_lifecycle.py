"""参考图全生命周期：上传带名字 → 改名 → 删除（文件必须真的没了）。

背景（主人实测）：删掉参考图后，素材库 tab 显示 0 张，文件却还挂在
「全部素材」列表里 —— 那个列表扫的是磁盘，而删除只删登记。
"""
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
BASE = 'http://127.0.0.1:8790'
SRC = ROOT / 'projects' / 'p_30620485f9924bd688fb74e82b8b9c64' / 'assets' / 'ref'


def call(method, path, body=None, files=None):
    url = BASE + path
    if files:
        boundary = '----test' + uuid.uuid4().hex
        parts = []
        for key, value in (body or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        for key, (filename, data) in files.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                         f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode() + data + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        data = b''.join(parts)
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
    else:
        data = json.dumps(body or {}).encode()
        headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')[:300]


def main():
    pictures = sorted(SRC.glob('*.png')) if SRC.is_dir() else []
    if pictures:
        blob = pictures[0].read_bytes()
    else:
        # 找一张现成的参考图；都没有就现画一张 64×64 的
        found = list(ROOT.glob('projects/*/assets/ref/*.png'))
        if found:
            blob = found[0].read_bytes()
        else:
            from io import BytesIO
            from PIL import Image
            buf = BytesIO()
            Image.new('RGB', (64, 64), (200, 180, 210)).save(buf, format='PNG')
            blob = buf.getvalue()
    print('测试图片', len(blob), '字节')

    code, body = call('POST', '/api/projects', {'name': '参考图生命周期测试'})
    assert code in (200, 201), (code, body)
    pid = (body.get('project') or body).get('id')
    assert pid, f'建项目返回里没有 id：{body}'
    print('项目', pid)

    # ① 上传（带名字和性别）
    code, body = call('POST', f'/api/projects/{pid}/references',
                      {'kind': 'character', 'name': '林夏', 'gender': 'female'},
                      files={'file': ('test.png', blob)})
    assert code == 200, (code, body)
    ref = body['reference']
    print('① 上传:', ref['id'], 'name=', ref.get('name'), 'gender=', ref.get('gender'), 'asset=', ref['asset'])
    assert ref.get('name') == '林夏' and ref.get('gender') == 'female'
    target = ROOT / 'projects' / pid / ref['asset']
    assert target.is_file(), f'上传后文件不存在：{target}'

    # ② 改名
    code, body = call('PATCH', f'/api/projects/{pid}/references/{ref["id"]}',
                      {'name': '林夏·雨夜', 'gender': 'female'})
    assert code == 200, (code, body)
    print('② 改名后:', body['reference'].get('name'))
    assert body['reference']['name'] == '林夏·雨夜'

    # ③ 删除：登记和文件都要没
    code, body = call('DELETE', f'/api/projects/{pid}/references/{ref["id"]}')
    assert code == 200, (code, body)
    code, body = call('GET', f'/api/projects/{pid}/references')
    remaining = body['references']
    print('③ 删除后登记剩:', len(remaining))
    assert not remaining, '登记没删干净'
    print('   文件还在吗:', target.is_file())
    assert not target.is_file(), f'文件没删掉：{target}'

    # ④ 素材库扫描里也不该再有它
    code, body = call('GET', f'/api/projects/{pid}/library')
    refs = (body.get('library') or body).get('reference') or []
    print('④ 素材库参考图剩:', len(refs))
    assert not refs, f'素材库还在列它：{refs}'

    print('\n全部通过 ✔')


sys.exit(main())
