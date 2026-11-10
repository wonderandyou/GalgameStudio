"""诊断：为什么 .venv 的 python 写不了 D:\\UnityProject，而系统 python 可以。"""
import os
import sys
from pathlib import Path

print('解释器:', sys.executable)
print('cwd:', os.getcwd())
print()

targets = [
    r'D:\UnityProject',
    r'D:\UnityProject\Assets',
    r'D:\软件\GalgameStudio',
    r'D:\AI工作站',
    r'D:\软件\GalgameStudio\projects',
]

for target in targets:
    base = Path(target)
    if not base.exists():
        print(f'{target:40s} 不存在')
        continue
    probe = base / '_diag_write_probe.txt'
    try:
        probe.write_text('x', encoding='utf-8')
        probe.unlink()
        print(f'{target:40s} 可写')
    except Exception as exc:                                   # noqa: BLE001
        code = getattr(exc, 'errno', None)
        print(f'{target:40s} 失败 {type(exc).__name__} errno={code} winerror={getattr(exc, "winerror", None)}')
        print(f'    {exc}')

print()
print('--- 目录权限 ---')
for target in targets[:2]:
    base = Path(target)
    if not base.exists():
        continue
    try:
        st = base.stat()
        print(f'{target}: mode={oct(st.st_mode)}')
    except OSError as exc:
        print(f'{target}: stat 失败 {exc}')
