# -*- coding: utf-8 -*-
"""打独立 exe：把后端 + 界面 + Python 运行时塞进一个目录。

为什么是 onedir 而不是 onefile
------------------------------
onefile 每次启动都要把几百 MB 解压到临时目录，实测要慢好几秒；
pywebview + WebView2 的原生 dll 在 onefile 下还容易找不到。
onedir 的代价只是一个文件夹，换来的是「双击就开」。

用法
----
    C:\\Users\\q3153\\AppData\\Local\\Programs\\Python\\Python311\\python.exe  tools\\build_exe.py

⚠ 必须用系统 Python（它装着 pywebview + pythonnet + PyInstaller）。
  venv 里没有 pywebview —— 往 venv 装会去编译 pythonnet，实测卡了 9 分钟没动静。

产物
----
    dist\\奇迹工坊\\奇迹工坊.exe      ← 双击这个
    dist\\奇迹工坊\\projects\\        ← 项目数据落这里（跟 exe 同级，换了机器也能带走）

⚠ 打包后启动器不再拉 `python -m uvicorn` 子进程（exe 里没有 python.exe），
   而是走 launcher/galapp.py 里的 FROZEN 分支：同进程起 uvicorn + 把 ui/ 挂上去。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = '奇迹工坊'
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'
# exe 图标：粉色 Q 版头像，多尺寸 ico（16 缩起来也认得出）
ICON = ROOT / 'ui' / 'brand' / '奇迹工坊.ico'

SPEC = '''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = r"{root}"

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
    (ROOT + r"\\ui", 'ui'),
    # agent 的 skill 是运行期按文件读的，不打进去模型就变成"没有技能"
    (ROOT + r"\\app\\skills", 'app/skills'),
] + pn_datas + cl_datas
binaries = pn_bins + cl_bins

a = Analysis(
    [ROOT + r"\\launcher\\galapp.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[ROOT + r"\\launcher\\pyi_rth_bootlog.py",
                   ROOT + r"\\launcher\\pyi_rth_pythonnet.py"],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='{name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=r"{icon}",
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='{name}',
)
'''


def fix_pythonnet(out):
    """把 pythonnet 的运行时文件按它**期望的目录**摆好。

    PyInstaller 把 .dll 当二进制处理，落到别的目录（实测 ClrLoader.dll 跑到
    `clr_loader\\ffi\\dlls\\amd64\\`），运行时就报：
      Failed to resolve Python.Runtime.Loader.Initialize from
      ...\\_internal\\pythonnet\\runtime\\Python.Runtime.dll
    而 `pythonnet\\runtime\\` 里只有 dll 没有 ClrLoader，netfx 加载器就找不到入口。
    打包后按原样复制一遍，比在 spec 里跟 PyInstaller 的 binary 归类较劲可靠。
    """
    import shutil

    import clr_loader
    import pythonnet

    source = Path(pythonnet.__file__).parent / 'runtime'
    target = out / '_internal' / 'pythonnet' / 'runtime'
    target.mkdir(parents=True, exist_ok=True)

    copied = []
    if source.is_dir():
        for item in source.iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
                copied.append(item.name)

    loader_dir = Path(clr_loader.__file__).parent
    for candidate in (loader_dir / 'ffi' / 'dlls' / 'amd64' / 'ClrLoader.dll',
                      loader_dir / 'ClrLoader.dll'):
        if candidate.is_file():
            shutil.copy2(candidate, target / 'ClrLoader.dll')
            copied.append('ClrLoader.dll（来自 clr_loader）')
            break
    return copied


def main():
    # 用「跑这个脚本的解释器」打包，不自作主张换解释器。
    # 本机现实：venv 里有 fastapi/uvicorn 但没有 pywebview；
    # 系统 Python 里有 pywebview + pythonnet + PyInstaller。
    # 所以打包要用系统 Python 来跑这个脚本（见文件头的用法）。
    python = Path(sys.executable)

    try:
        import PyInstaller  # noqa: F401
        import webview  # noqa: F401
    except ImportError as exc:
        print(f'缺依赖：{exc}')
        print('先跑：app\\.venv\\Scripts\\python.exe -m pip install pyinstaller pywebview')
        return 1

    BUILD.mkdir(exist_ok=True)
    spec = BUILD / f'{NAME}.spec'
    spec.write_text(SPEC.format(root=str(ROOT).replace('\\', '\\\\'), name=NAME,
                               icon=str(ICON).replace('\\', '\\\\')), 'utf-8')
    print(f'[1/3] spec 已写：{spec}')

    command = [str(python), '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--distpath', str(DIST), '--workpath', str(BUILD / 'work'),
               str(spec)]
    print('[2/3] 开始打包（第一次约 1~3 分钟）…')
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        print(f'打包失败，退出码 {result.returncode}')
        return result.returncode

    out = DIST / NAME
    target = out / f'{NAME}.exe'
    if not target.is_file():
        print(f'打包没报错，但没找到 {target}')
        return 1

    # 项目数据目录：放在 exe 旁边，不塞进 _internal，
    # 这样换电脑时把整个文件夹拷走，项目跟着走。
    (out / 'projects').mkdir(exist_ok=True)

    copied = fix_pythonnet(out)
    print(f'      pythonnet 运行时补齐 {len(copied)} 个文件')
    for name in copied:
        print(f'        · {name}')

    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
    print(f'[3/3] 完成：{target}')
    print(f'      目录大小 {size / 1024 / 1024:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
