# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = r"D:\\软件\\GalgameStudio"

# uvicorn 和 pywebview 都是「运行期按字符串选实现」的库，
# 静态分析看不到它们真正要加载的子模块，必须整包收进来。
hidden = (
    collect_submodules('uvicorn') +
    collect_submodules('webview') +
    collect_submodules('app') +
    ['clr', 'pythonnet']
)

# ★ pythonnet 的 dll 是数据文件，PyInstaller 默认只建了空目录。
#   打包后报：Failed to resolve Python.Runtime.Loader.Initialize from
#   .../_internal/pythonnet/runtime/Python.Runtime.dll（文件根本不存在）。
#   collect_all 会把 runtime/*.dll、*.deps.json 一起收进去。
pn_datas, pn_bins, pn_hidden = collect_all('pythonnet')
cl_datas, cl_bins, cl_hidden = collect_all('clr_loader')
hidden += pn_hidden + cl_hidden

# collect_all 漏了 ClrLoader.dll（它在 pythonnet/runtime/ 里，但被当成 clr_loader 的二进制），
# 结果 netfx 模式在 runtime 目录里找不到它，报 Failed to resolve ...Initialize。
import os
import pythonnet
_PN_RUNTIME = os.path.join(os.path.dirname(pythonnet.__file__), 'runtime')
for _name in ('ClrLoader.dll', 'Python.Runtime.dll', 'Python.Runtime.deps.json'):
    _path = os.path.join(_PN_RUNTIME, _name)
    if os.path.isfile(_path):
        pn_datas.append((_path, 'pythonnet/runtime'))

datas = [
    (ROOT + r"\ui", 'ui'),
    # agent 的 skill 是运行期按文件读的，不打进去模型就变成"没有技能"
    (ROOT + r"\app\skills", 'app/skills'),
] + pn_datas + cl_datas
binaries = pn_bins + cl_bins

a = Analysis(
    [ROOT + r"\launcher\galapp.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[ROOT + r"\launcher\pyi_rth_bootlog.py",
                   ROOT + r"\launcher\pyi_rth_pythonnet.py"],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='奇迹工坊',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=r"D:\\软件\\GalgameStudio\\ui\\brand\\奇迹工坊.ico",
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='奇迹工坊',
)
