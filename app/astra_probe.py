"""后端验收探针，使用临时项目发实际 ASGI 请求并保存抠图对照。"""

import argparse
from io import BytesIO
from pathlib import Path
from struct import pack_into
from tempfile import TemporaryDirectory
from unittest.mock import patch


def uploads():
    from fastapi.testclient import TestClient
    from PIL import Image

    from .main import app
    from .mock_api import jobs
    from .mock_data import png, silent_wav

    image = png(32, 24, (24, 48, 64, 255))
    jpeg = BytesIO()
    Image.new('RGB', (12, 8), 'white').save(jpeg, 'JPEG')
    wav = bytearray(silent_wav())
    pack_into('<I', wav, 4, 2147483583)
    pack_into('<I', wav, 40, 2147483547)
    wav = bytes(wav)

    with (TemporaryDirectory(prefix='galgame-astra-') as temp,
          patch.object(jobs, 'root', Path(temp)),
          TestClient(app) as client):
        project = client.post('/api/projects', json={'name': '素材接口验收'})
        assert project.status_code == 200, project.text
        pid = project.json()['id']
        base = f'/api/projects/{pid}'

        for name in ('references', 'voices'):
            response = client.get(f'{base}/{name}')
            assert response.status_code == 200 and response.json() == dict(ok=True, count=0, **{name: []})

        ref_data = {'kind': 'character'}
        response = client.post(f'{base}/references', data=ref_data, files={'file': ('立绘.png', image)})
        assert response.status_code == 200, response.text
        first = response.json()['reference']
        assert (first['width'], first['height'], first['label']) == (32, 24, '立绘'), first
        asset = jobs.project_path(pid) / first['asset']
        mtime = asset.stat().st_mtime_ns
        response = client.post(f'{base}/references', data=ref_data, files={'file': ('重复.png', image)})
        assert response.status_code == 200, response.text
        duplicate = response.json()['reference']
        assert duplicate['asset'] == first['asset'] and duplicate['id'] != first['id']
        assert asset.stat().st_mtime_ns == mtime
        response = client.post(f'{base}/references', data={'kind': 'scene'},
                               files={'file': ('场景.jpg', jpeg.getvalue())})
        assert response.status_code == 200, response.text
        scene = response.json()['reference']
        assert scene['asset'].endswith('.jpg') and (scene['width'], scene['height']) == (12, 8)
        assert client.get(f'/api/assets/{pid}/{scene["asset"]}').content == jpeg.getvalue()
        assert client.get(f'{base}/references').json()['count'] == 3
        for ref in (first, duplicate):
            response = client.delete(f'{base}/references/{ref["id"]}')
            assert response.json() == dict(ok=True, deleted_id=ref['id'], note='只删登记，磁盘文件保留')
        assert client.get(f'{base}/references').json()['references'] == [scene]
        response = client.post(f'{base}/references', data=ref_data, files={'file': ('损坏.png', b'nope')})
        assert response.status_code == 422 and response.json()['error']['code'] == 'INVALID_IMAGE'
        response = client.post(f'{base}/references', data={'kind': 'invalid'}, files={'file': ('立绘.png', image)})
        assert response.status_code == 422 and response.json()['error']['code'] == 'INVALID_KIND'
        print('PASS references POST/GET/DELETE, deduplication, PNG/JPEG, INVALID_IMAGE, INVALID_KIND')

        response = client.post(f'{base}/voices', data={'character_id': 'heroine', 'label': '语音'},
                               files={'file': ('voice.wav', wav)})
        assert response.status_code == 200, response.text
        voice = response.json()['voice']
        assert voice['seconds'] == 1.0 and voice['character_id'] == 'heroine'
        assert client.get(f'{base}/voices').json()['voices'] == [voice]
        response = client.post(f'{base}/voices', files={'file': ('voice.mp3', b'ID3')})
        assert response.status_code == 415 and response.json()['error']['code'] == 'NEED_WAV'
        response = client.delete(f'{base}/voices/{voice["id"]}')
        assert response.json() == dict(ok=True, deleted_id=voice['id'])
        assert client.get(f'{base}/voices').json()['count'] == 0
        print('PASS voices POST/GET/DELETE, streaming WAV 1.0 s, NEED_WAV')

        project_root = jobs.project_path(pid)
        for directory in ('bg', 'char'):
            target = project_root / 'assets' / directory / 'unregistered.png'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image)
        response = client.get(f'{base}/library')
        assert response.status_code == 200, response.text
        library = response.json()
        assert library['ok'] and len(library['reference']) == 2
        assert {entry['asset'] for entry in library['reference']} == {first['asset'], scene['asset']}
        assert library['voice'][0]['asset'] == voice['asset']
        assert library['background'][0]['name'] == 'bg/unregistered.png'
        assert library['character'][0]['name'] == 'char/unregistered.png'
        print('PASS library finds all four groups, including unregistered and unlisted files')


def cutouts(screens):
    from PIL import Image

    from .assets.cutout import remove_white_background
    from .config import ROOT

    screens.mkdir(parents=True, exist_ok=True)
    originals, composites = [], []
    for name in ('char_l', 'char_r'):
        source = ROOT / 'ui' / 'assets' / 'title' / f'{name}.png'
        originals.append(Image.open(source).convert('RGB'))
        result = Image.open(BytesIO(remove_white_background(source.read_bytes())))
        assert result.mode == 'RGBA' and result.getchannel('A').getextrema() == (0, 255)
        background = Image.new('RGBA', result.size, '#20242c')
        composites.append(Image.alpha_composite(background, result).convert('RGB'))
        print(f'PASS {name}: {result.size}, RGBA alpha (0, 255)')
    for name, tiles in (('抠图-前.png', originals), ('抠图-后.png', composites)):
        strip = Image.new('RGB', (sum(tile.width for tile in tiles), max(tile.height for tile in tiles)))
        x = 0
        for tile in tiles:
            strip.paste(tile, (x, 0))
            x += tile.width
        strip.save(screens / name)
        print(screens / name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('test', choices=('uploads', 'cutouts'))
    parser.add_argument('--screens', type=Path)
    args = parser.parse_args()
    if args.test == 'uploads':
        uploads()
    else:
        if args.screens is None:
            parser.error('cutouts requires --screens')
        cutouts(args.screens)
