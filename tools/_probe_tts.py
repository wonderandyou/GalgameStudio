"""探针：验证百炼 qwen3-tts-flash 能否真合成语音。

凭据从 ~/.dsh/.credentials.yaml 现场读，不打印 key。
"""
import base64
import io
import pathlib
import re
import sys
import time
import wave

import httpx

CRED = pathlib.Path.home() / '.dsh' / '.credentials.yaml'
URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
OUT = pathlib.Path(__file__).with_name('_probe_tts_out.wav')


def load_key():
    for line in CRED.read_text(encoding='utf-8', errors='replace').splitlines():
        m = re.match(r'^\s*DASHSCOPE_API_KEY\s*:\s*(.+?)\s*$', line)
        if m:
            return m.group(1).strip().strip('"\'')
    raise SystemExit('取不到 DASHSCOPE_API_KEY')


def main():
    key = load_key()
    print(f'[OK] key 长度 {len(key)}')
    payload = {
        'model': 'qwen3-tts-flash',
        'input': {'text': '雨停之前，我不会离开这条码头。', 'voice': 'Cherry',
                  'language_type': 'Chinese'},
    }
    t0 = time.time()
    with httpx.Client(timeout=httpx.Timeout(180, connect=15), trust_env=False) as c:
        r = c.post(URL, json=payload,
                   headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        print(f'[HTTP] {r.status_code}  {time.time()-t0:.1f}s')
        if r.status_code != 200:
            print('[FAIL] ' + r.text.replace(key, '***')[:800])
            return 2
        body = r.json()
        audio = (body.get('output') or {}).get('audio') or {}
        data = None
        if audio.get('data'):
            data = base64.b64decode(audio['data'])
            print(f'[OK] base64 音频 {len(data)} 字节')
        elif audio.get('url'):
            got = c.get(audio['url'])
            got.raise_for_status()
            data = got.content
            print(f'[OK] URL 音频 {len(data)} 字节')
        if not data:
            print('[FAIL] 既无 data 也无 url：' + str(body)[:600])
            return 3

    fmt = 'WAV' if data[:4] == b'RIFF' else f'未知({data[:4]!r})'
    print(f'[形态] {fmt}')
    if fmt == 'WAV':
        with wave.open(io.BytesIO(data), 'rb') as w:
            params = f'{w.getframerate()} Hz / {w.getsampwidth()*8} bit / {w.getnchannels()} ch'
        # ⚠ 不能用 getnframes()：百炼返回的流式 WAV 头里块大小是占位值（声明 2147483547 字节），
        #   读出来会是几万秒。必须用 provider 里同一套算法（按实际字节算）。
        from app.providers.base import audio_seconds
        seconds = audio_seconds(data)
        print(f'[WAV] {params} / 真实时长 {seconds:.2f} 秒')
        if seconds and not 0.2 <= seconds <= 600:
            print(f'[警告] 时长 {seconds:.2f} 秒不合理，检查 audio_seconds 实现')
            return 4
    OUT.write_bytes(data)
    print(f'[PASS] 已存 {OUT} ({len(data)/1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
