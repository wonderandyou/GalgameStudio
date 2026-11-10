# -*- coding: utf-8 -*-
"""分步定位：C 盘 %LOCALAPPDATA% 下建临时文件 / flush / fsync / replace 哪一步卡。"""
import os
import tempfile
import time
from pathlib import Path

LOG = Path(r'D:\软件\GalgameStudio\tools\_fsync_probe.log')


def log(msg, t0):
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f'{time.strftime("%H:%M:%S")} {msg}  (+{time.monotonic()-t0:.2f}s)\n')


def run(base, name):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    log(f'[{name}] mkdir {base}', t0)
    f = tempfile.NamedTemporaryFile(dir=base, prefix='probe.', suffix='.tmp', delete=False)
    log(f'[{name}] NamedTemporaryFile ok {f.name}', t0)
    f.write(b'x' * 100)
    log(f'[{name}] write ok', t0)
    f.flush()
    log(f'[{name}] flush ok', t0)
    os.fsync(f.fileno())
    log(f'[{name}] fsync ok', t0)
    f.close()
    log(f'[{name}] close ok', t0)
    Path(f.name).replace(base / 'probe_target.json')
    log(f'[{name}] replace ok', t0)
    (base / 'probe_target.json').unlink(missing_ok=True)
    log(f'[{name}] done', t0)


if __name__ == '__main__':
    LOG.write_text('', encoding='utf-8')
    run(os.path.join(os.environ['LOCALAPPDATA'], 'GalgameStudio'), 'LOCALAPPDATA')
    run(r'D:\软件\GalgameStudio\tmp', 'D-tmp')
    log('ALL DONE', time.monotonic())
