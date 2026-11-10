"""PyInstaller 运行时钩子：让 pythonnet 在冻结包里找到 python311.dll。

为什么需要
----------
打包后一调 `import clr`（pywebview 开窗口必经），就报：

    RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize from
    ...\\_internal\\pythonnet\\runtime\\Python.Runtime.dll

dll 明明在，缺的是 **`PYTHONNET_PYDLL`** —— pythonnet 要拿它去初始化 .NET 运行时。
冻结包里 sys.prefix 指向 _internal，pythonnet 自己找不到解释器 dll，于是放弃。

做法参照 razzant/ouroboros 的 pyi_rth_pythonnet.py（GitHub 上验证过的写法）：
设 PYTHONNET_RUNTIME=netfx、设 PYTHONNET_PYDLL，再把相关目录加进 PATH
和 os.add_dll_directory。

必须作为 runtime hook 在 `import clr` / `import webview` **之前**执行，
所以写进 spec 的 runtime_hooks，不能当普通模块 import。
"""
import os
import sys

if sys.platform == 'win32':
    base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    exe_dir = os.path.dirname(sys.executable)
    dll_name = 'python%d%d.dll' % (sys.version_info[0], sys.version_info[1])
    runtime_dir = os.path.join(base, 'pythonnet', 'runtime')

    # .NET Framework 那条路最稳（本机 WebView2 走的就是它）
    os.environ['PYTHONNET_RUNTIME'] = 'netfx'

    selected = None
    for path in (os.path.join(base, dll_name), os.path.join(exe_dir, dll_name)):
        if os.path.isfile(path):
            os.environ['PYTHONNET_PYDLL'] = path
            selected = path
            break

    search = [base, exe_dir, runtime_dir, os.path.join(base, 'webview', 'lib')]
    if selected:
        search.append(os.path.dirname(selected))
    search = [p for p in dict.fromkeys(search) if p and os.path.isdir(p)]

    os.environ['PATH'] = os.pathsep.join(
        search + [p for p in os.environ.get('PATH', '').split(os.pathsep)
                  if p and p not in search])

    if hasattr(os, 'add_dll_directory'):
        for path in search:
            try:
                os.add_dll_directory(path)
            except (FileNotFoundError, OSError):
                pass
