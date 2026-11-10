"""PyInstaller 运行时钩子：第一件事就把「我启动了」写进文件。

用途只有一个 —— 区分这两种情况：
  * boot.log 有记录  → exe 真的启动了，问题在启动之后（窗口/浏览器）
  * boot.log 没记录  → exe 根本没被系统放行（Smart App Control / 其他拦截）

双击没反应时最难受的就是「不知道它到底跑没跑」，这一行字就能分开。
"""
import os
import sys
import time

try:
    base = os.path.dirname(sys.executable)
    with open(os.path.join(base, 'boot.log'), 'a', encoding='utf-8') as handle:
        handle.write('%s  启动  pid=%d  exe=%s\n'
                     % (time.strftime('%Y-%m-%d %H:%M:%S'), os.getpid(), sys.executable))
except Exception:
    pass
