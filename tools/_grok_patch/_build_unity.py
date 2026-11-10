"""一键：Unity 项目 → 全屏 exe。可命令行跑，也可 import 调用。

前置：
  1. Unity Editor 已装好（D:\\Unity\\6000.0.85f2\\Editor\\Unity.exe）
  2. 已用一次 Unity Editor 登录并激活个人版许可（这一步必须人来点，脚本代不了）
  3. 项目已导出过 unity 资源（app\\engine\\export_unity.py）

流程（build_exe 内部）：
  Unity.exe -batchmode -quit -createProject <工程>     ← 工程不存在时才建
  复制 Scripts\\Builder.cs → Assets\\Editor\\，其余 *.cs → Assets\\
  复制 StreamingAssets\\* → Assets\\StreamingAssets\\（先清旧的）
  Unity.exe -batchmode -quit -projectPath <工程> -executeMethod Builder.BuildWindows

产物：<工程>\\Build\\鲸落工坊.exe

⚠ Unity 首次创建工程要导入内置包，可能 3~10 分钟；构建再要 2~5 分钟。
⚠ 日志一律写文件（-logFile + stdout 重定向到另一个文件），不用 capture_output（会死锁）。

命令行：app\\.venv\\Scripts\\python.exe tools\\_build_unity.py [项目id]
import： from tools._build_unity import build_exe
         build_exe(project_root)   # project_root = projects\\<id>，需先 export_unity
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITY_ROOT = Path(r'D:\Unity\6000.0.85f2\Editor')
UNITY_EXE = UNITY_ROOT / 'Unity.exe'
PROJECT = Path(r'D:\UnityProject')
PRODUCT_NAME = '鲸落工坊.exe'
LOG_DIR = ROOT / 'tools' / 'unity_logs'
DEFAULT_PROJECT_ID = 'p_63b2b758e0ab46d1bf4201629468eb36'
TAIL_LINES = 40


class UnityBuildError(RuntimeError):
    """构建失败。message 里带步骤名和 Unity 日志尾部；属性 step / log / log_tail 方便调用方取。"""

    def __init__(self, step, message, log=None, log_tail=''):
        self.step, self.log, self.log_tail = step, log, log_tail
        text = f'[Unity {step}] {message}'
        if log:
            text += f'\n日志：{log}'
        if log_tail:
            text += f'\n---- Unity 日志尾部 ----\n{log_tail}'
        super().__init__(text)


def find_unity(unity_exe=None):
    if unity_exe and Path(unity_exe).is_file():
        return Path(unity_exe)
    if UNITY_EXE.is_file():
        return UNITY_EXE
    # 兜底：扫 D:\Unity / C:\Program Files\Unity 下所有 Editor\Unity.exe
    for base in (Path('D:/Unity'), Path('C:/Program Files/Unity')):
        if base.is_dir():
            for candidate in base.rglob('Editor/Unity.exe'):
                return candidate
    return None


def log_tail(path, lines=TAIL_LINES):
    try:
        text = Path(path).read_text('utf-8', errors='replace')
    except OSError:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


def _run(step, command, log_path, timeout, echo):
    """跑 Unity：Unity 自己写 -logFile，stdout/stderr 另落一个文件，不走管道。"""
    log_path = Path(log_path)
    stdout_path = log_path.with_suffix('.stdout.log')
    for p in (log_path, stdout_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    echo(f'  → [{step}] {Path(command[0]).name} {" ".join(str(c) for c in command[1:5])}…（超时 {timeout:.0f} 秒）')
    started = time.time()
    try:
        with open(stdout_path, 'wb') as handle:
            result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                                    timeout=max(1, timeout))
    except subprocess.TimeoutExpired:
        raise UnityBuildError(step, f'超时（{timeout:.0f} 秒），Unity 已被终止', log_path,
                              log_tail(log_path) or log_tail(stdout_path))
    elapsed = time.time() - started
    echo(f'  ← [{step}] 退出码 {result.returncode}，耗时 {elapsed:.0f} 秒，日志 {log_path}')
    return result.returncode, elapsed


def _copy_into_project(source, project, echo):
    assets = project / 'Assets'
    # 脚本必须分开放：
    #   Builder.cs 引用 UnityEditor → 只能放 Assets/Editor/（Editor 程序集）
    #   其余 .cs 是运行时逻辑      → 放 Assets/ 根（Assembly-CSharp）
    # 放错了会报 CS0234，或者出现两份同名类导致编译冲突。
    (assets / 'Editor').mkdir(parents=True, exist_ok=True)
    copied = 0
    for source_file in (source / 'Scripts').glob('*.cs'):
        target = assets / 'Editor' / source_file.name if source_file.name == 'Builder.cs' \
            else assets / source_file.name
        shutil.copy2(source_file, target)
        copied += 1
    streaming = assets / 'StreamingAssets'
    if streaming.exists():          # 先清，避免上一个项目的素材混进来
        shutil.rmtree(streaming)
    shutil.copytree(source / 'StreamingAssets', streaming)
    items = len(list(streaming.rglob('*')))
    echo(f'      脚本 {copied} 个（Builder.cs → Assets/Editor/，其余 → Assets/），资源 {items} 项')
    return copied, items


def build_exe(project_root, unity_project=PROJECT, unity_exe=None, timeout=1800, *,
              log_dir=None, echo=print):
    """export/unity → Unity 工程 → Windows exe。

    project_root：项目目录（含 export/unity/{StreamingAssets,Scripts}，先跑 export_unity）。
    timeout：建工程 + 构建的总预算（秒）。
    成功返回 {ok, out, unity_project, build_log, log_file, seconds, exe_bytes, build_dir_bytes}。
    失败抛 UnityBuildError（RuntimeError 子类），消息含步骤名 + Unity 日志尾部。
    """
    started = time.time()
    unity_project = Path(unity_project)
    log_dir = Path(log_dir) if log_dir else LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    unity = find_unity(unity_exe)
    if unity is None:
        raise UnityBuildError('定位', f'没找到 Unity.exe（试过 {unity_exe or UNITY_EXE}）')
    echo(f'Unity: {unity}')

    source = Path(project_root) / 'export' / 'unity'
    if not (source / 'StreamingAssets' / 'graph.json').is_file():
        raise UnityBuildError('准备', f'{source} 下没有 StreamingAssets/graph.json，先跑 export_unity')

    # 1) 建工程
    create_log = log_dir / 'create.log'
    if not (unity_project / 'Assets').is_dir():
        echo('[1/3] 创建 Unity 工程…')
        code, _ = _run('建工程', [str(unity), '-batchmode', '-quit', '-createProject',
                                 str(unity_project), '-logFile', str(create_log)],
                       create_log, timeout - (time.time() - started), echo)
        if code != 0 or not (unity_project / 'Assets').is_dir():
            raise UnityBuildError('建工程', f'退出码 {code}', create_log, log_tail(create_log))
    else:
        echo('[1/3] 工程已存在，跳过创建')

    # 2) 搬脚本和资源
    echo('[2/3] 复制脚本与 StreamingAssets…')
    _copy_into_project(source, unity_project, echo)

    # 3) 构建（先删旧 exe，防止“构建失败但旧产物还在”被误判为成功）
    target = unity_project / 'Build' / PRODUCT_NAME
    if target.exists():
        target.unlink()
    echo('[3/3] 命令行构建 Windows exe…')
    build_log = log_dir / 'build.log'
    code, _ = _run('构建', [str(unity), '-batchmode', '-quit', '-projectPath', str(unity_project),
                           '-executeMethod', 'Builder.BuildWindows', '-logFile', str(build_log)],
                   build_log, timeout - (time.time() - started), echo)
    tail = log_tail(build_log)
    if code != 0 or not target.is_file():
        raise UnityBuildError('构建', f'退出码 {code}，产物{"存在" if target.is_file() else "不存在"}',
                              build_log, tail)

    build_dir_bytes = sum(f.stat().st_size for f in target.parent.rglob('*') if f.is_file())
    seconds = round(time.time() - started, 1)
    echo(f'✅ 构建成功：{target}（exe {target.stat().st_size} 字节，Build 目录 '
         f'{build_dir_bytes / 1024 / 1024:.1f} MB，{seconds} 秒）')
    return dict(ok=True, out=str(target), unity_project=str(unity_project), build_log=tail,
                log_file=str(build_log), seconds=seconds, exe_bytes=target.stat().st_size,
                build_dir_bytes=build_dir_bytes)


def main(project_id=DEFAULT_PROJECT_ID):
    try:
        build_exe(ROOT / 'projects' / project_id)
    except UnityBuildError as exc:
        print()
        print('❌', exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT_ID))
