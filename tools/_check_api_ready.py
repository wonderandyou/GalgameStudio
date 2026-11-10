# -*- coding: utf-8 -*-
"""经理验收：后端 7 个新接口真调一遍（给 Kimi 联调前的地基确认）。

跑：python tools\_check_api_ready.py
前提：后端已在 8790 起（scripts\\run-backend.ps1）
"""
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = 'http://127.0.0.1:8790'
PID = 'p_63b2b758e0ab46d1bf4201629468eb36'   # 有 40 节点的现成项目


def call(method, path, *, body=None, raw=None, headers=None):
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    elif raw is not None:
        data = raw
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode('utf-8') or '{}')
        except ValueError:
            payload = {'raw': '非 JSON'}
        return exc.code, payload


def multipart(fields, files):
    """手工拼 multipart：fields={k:v}, files={k:(filename, bytes, mime)}"""
    boundary = '----probe' + uuid.uuid4().hex
    lines = []
    for key, value in fields.items():
        lines.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n')
    chunks = [''.join(lines).encode('utf-8')]
    for key, (filename, data, mime) in files.items():
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n')
        chunks.append(head.encode('utf-8') + data + b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(chunks), f'multipart/form-data; boundary={boundary}'


def png_bytes():
    """1x1 透明 PNG（最小合法图）"""
    import base64
    return base64.b64decode(
        b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def wav_bytes():
    """0.25 秒 24kHz 16bit mono 静音 WAV"""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b'\x00\x00' * 6000)
    return buf.getvalue()


def main():
    checks = []

    def ok(label, cond, detail=''):
        checks.append((label, bool(cond), str(detail)[:180]))

    status, body = call('GET', '/api/health')
    ok('health', status == 200 and body.get('ok'), body)

    # 参考图：上传 → 列表 → 删除
    data, ctype = multipart({'kind': 'character', 'label': 'Kimi联调测试'},
                            {'file': ('probe.png', png_bytes(), 'image/png')})
    status, body = call('POST', f'/api/projects/{PID}/references', raw=data,
                        headers={'Content-Type': ctype})
    ref = (body or {}).get('reference') or {}
    ok('POST references', status == 200 and ref.get('asset'), body)
    ref_id = ref.get('id')

    status, body = call('GET', f'/api/projects/{PID}/references')
    ok('GET references', status == 200 and any(r.get('id') == ref_id
                                               for r in (body.get('references') or [])), body.get('count'))

    # 语音：上传 → 列表
    data, ctype = multipart({'character_id': 'heroine', 'label': 'Kimi联调语音'},
                            {'file': ('probe.wav', wav_bytes(), 'audio/wav')})
    status, body = call('POST', f'/api/projects/{PID}/voices', raw=data,
                        headers={'Content-Type': ctype})
    voice = (body or {}).get('voice') or {}
    ok('POST voices', status == 200 and voice.get('asset'),
       f"seconds={voice.get('seconds')}")

    status, body = call('GET', f'/api/projects/{PID}/voices')
    ok('GET voices', status == 200, body.get('count'))

    # 素材库
    status, body = call('GET', f'/api/projects/{PID}/library')
    groups = {k: len(body.get(k) or []) for k in ('background', 'character', 'reference', 'voice')}
    ok('GET library', status == 200 and all(groups.values()), groups)

    # 导出 format 枚举（不该再 422 说 unity 不合法）
    status, body = call('POST', f'/api/projects/{PID}/export', body={'format': 'unity'})
    ok('export 接受 unity', status != 422, f'HTTP {status}')

    # 清理探针留下的登记
    if ref_id:
        call('DELETE', f'/api/projects/{PID}/references/{ref_id}')
    if voice.get('id'):
        call('DELETE', f'/api/projects/{PID}/voices/{voice["id"]}')

    print('=' * 58)
    for label, passed, detail in checks:
        print(('  ✓ ' if passed else '  ✗ ') + label + (f'  [{detail}]' if not passed else ''))
    print('=' * 58)
    good = sum(1 for _, p, _ in checks if p)
    print(f'结果: {"PASS" if good == len(checks) else "FAIL"}  ({good}/{len(checks)})')
    return 0 if good == len(checks) else 1


if __name__ == '__main__':
    sys.exit(main())
