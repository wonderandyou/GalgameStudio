# -*- coding: utf-8 -*-
"""实测云端两路通道：生图 + 配音（真调，会消耗额度）。

只看结果，不打印任何 key。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))
os.environ.setdefault('GAL_STATE_DIR', str(ROOT / '.state'))

OUT = ROOT / 'tmp' / 'cloud_probe'


async def main():
    from app.providers import registry
    from app.store import Credentials

    cfg = Credentials().read()
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0

    # —— 生图 ——
    print('=== 1) 云端生图 qwen_image ===')
    art, reason = registry.choose('art', cfg)
    if art is None:
        print(f'  [FAIL] 不可用：{reason}')
    else:
        print(f'  通道：{art.chain() if hasattr(art, "chain") else art.kind}')
        t0 = time.monotonic()
        try:
            data = await art.call('雨夜天台，两个学生背影，远景，冷色调，动漫风格', 1024, 576, 180)
            p = OUT / 'cloud_bg.png'
            p.write_bytes(data)
            print(f'  [OK] {len(data)/1024:.0f} KB  {time.monotonic()-t0:.1f} 秒  -> {p}')
            print(f'  是 PNG/JPEG：{data.startswith(bytes([0x89]) + b"PNG") or data.startswith(bytes([0xFF, 0xD8]))}')
            ok += 1
        except Exception as exc:                                # noqa: BLE001
            print(f'  [FAIL] {type(exc).__name__}: {exc}')

    # —— 配音 ——
    print('\n=== 2) 云端配音 bailian_tts ===')
    voice, reason = registry.choose('voice', cfg)
    if voice is None:
        print(f'  [FAIL] 不可用：{reason}')
    else:
        print(f'  通道：{voice.chain() if hasattr(voice, "chain") else voice.kind}')
        t0 = time.monotonic()
        try:
            r = await voice.call('雨停了，我们还是走吧。', timeout=120)
            p = OUT / 'cloud_voice.wav'
            p.write_bytes(r['data'])
            print(f'  [OK] {len(r["data"])/1024:.0f} KB  时长 {r["seconds"]:.2f} 秒  '
                  f'{time.monotonic()-t0:.1f} 秒  -> {p}')
            if hasattr(voice, 'last_used') and voice.last_used:
                print(f'  实际用了通道：{voice.last_used}')
            ok += 1
        except Exception as exc:                                # noqa: BLE001
            print(f'  [FAIL] {type(exc).__name__}: {exc}')

    print(f'\n结果：{ok}/2 通过')
    return 0 if ok == 2 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
