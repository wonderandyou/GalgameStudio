# -*- coding: utf-8 -*-
r"""定位凭据写入卡在哪一步：每一步直接落日志文件（不靠 stdout，避免管道缓冲）。"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / 'tools' / '_cred_probe.log'


def log(msg):
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f'{time.strftime("%H:%M:%S")} {msg}\n')


def main():
    LOG.write_text('', encoding='utf-8')
    log('A start')
    base = Path(os.environ['LOCALAPPDATA']) / 'GalgameStudio'
    log(f'B base={base} isdir={base.is_dir()}')

    from app.store import atomic_json, transform, Credentials      # noqa: E402
    log('C import ok')

    probe = base / '__probe.json'
    atomic_json(probe, {'a': 1})
    log(f'D atomic_json ok size={probe.stat().st_size}')
    probe.unlink(missing_ok=True)

    enc = transform(b'{"a": 1}', 'protect')
    log(f'E protect ok len={len(enc)}')
    back = transform(enc, 'unprotect')
    log(f'F unprotect ok {back!r}')

    cred = base / 'credentials.json'
    log(f'G cred exists={cred.exists()} size={cred.stat().st_size if cred.exists() else "-"}')

    c = Credentials()
    d = c.read()
    log(f'H read ok keys={sorted(d)}')
    log('I 开始 write（同内容覆盖）')
    c.write(d)
    log('J write ok')
    log('K done')


if __name__ == '__main__':
    main()
