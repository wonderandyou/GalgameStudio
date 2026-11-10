# -*- coding: utf-8 -*-
"""测：Python 能否直接写目标文件（绕开 tempfile），以及 D 盘是否也有同样问题。"""
import os
import time
from pathlib import Path

LOG = Path(r'D:\软件\GalgameStudio\tools\_write_probe.log')


def log(msg, t0):
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f'{time.strftime("%H:%M:%S")} {msg}  (+{time.monotonic()-t0:.2f}s)\n')


def try_direct(base, tag):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    target = base / 'probe_direct.txt'
    try:
        target.write_text('hello', encoding='utf-8')
        log(f'[{tag}] write_text ok size={target.stat().st_size}', t0)
        log(f'[{tag}] read={target.read_text(encoding="utf-8")!r}', t0)
        target.unlink()
        log(f'[{tag}] unlink ok', t0)
    except Exception as exc:
        log(f'[{tag}] FAILED {type(exc).__name__}: {exc}', t0)


if __name__ == '__main__':
    LOG.write_text('', encoding='utf-8')
    try_direct(os.path.join(os.environ['LOCALAPPDATA'], 'GalgameStudio'), 'LOCALAPPDATA')
    try_direct(r'D:\软件\GalgameStudio\tmp', 'D-tmp')
    log('ALL DONE', time.monotonic())
