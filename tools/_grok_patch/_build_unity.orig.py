"""一键：Unity 项目 → 全屏 exe。

前置：
  1. Unity Editor 已装好（D:\\Unity\\6000.0.85f2\\Editor\\Unity.exe）
  2. 已用一次 Unity Editor 登录并激活个人版许可（这一步必须人来点，脚本代不了）
  3. 项目已导出过 unity 资源（app\\engine\\export_unity.py）

流程：
  Unity.exe -batchmode -quit -createProject <工程>     ← 建空工程
  复制 Scripts\\*.cs → Assets\\Scripts\\
  复制 StreamingAssets\\* → Assets\\StreamingAssets\\
  Unity.exe -batchmode -quit -executeMethod Builder.BuildWindows

产物：<工程>\\Build\\鲸落工坊.exe

⚠ Unity 首次创建工程要导入内置包，可能 3~10 分钟；构建再要 2~5 分钟。
   全程盯日志，卡住就报出来，不要干等。

跑：app\\.venv\\Scripts\\python.exe tools\\_build_unity.py [项目id]
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITY_ROOT = Path(r'D:\Unity\6000.0.85f2\Editor')
UNITY_EXE = UNITY_ROOT / 'Unity.exe'
PROJECT = Path(r'D:\UnityProject')
DEFAULT_PROJECT_ID = 'p_63b2b758e0ab46d1bf4201629468eb36'


def find_unity():
    if UNITY_EXE.is_file():
        return UNITY_EXE
    # 兜底：扫 D:\Unity 下所有 Editor\Unity.exe
    for candidate in Path('D:/Unity').rglob('Editor/Unity.exe'):
        return candidate
    for candidate in Path('C:/Program Files/Unity').rglob('Editor/Unity.exe'):
        return candidate
    return None


def run(command, log_path, timeout=1800):
    """跑 Unity，日志同时落盘并回显尾部。"""
    print(f'  → {Path(command[0]).name} {" ".join(str(c) for c in command[1:4])}…')
    started = time.time()
    with open(log_path, 'wb') as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                                timeout=timeout)
    elapsed = time.time() - started
    print(f'  ← 退出码 {result.returncode}，耗时 {elapsed:.0f} 秒，日志 {log_path}')
    if result.returncode != 0:
        text = log_path.read_text('utf-8', errors='replace')
        for line in text.splitlines()[-25:]:
            print('     ' + line)
    return result.returncode


def main(project_id):
    unity = find_unity()
    if unity is None:
        print('没找到 Unity.exe。先装 Unity Editor（D:\\Unity\\<版本>\\Editor\\Unity.exe）')
        return 1
    print(f'Unity: {unity}')

    source = ROOT / 'projects' / project_id / 'export' / 'unity'
    if not (source / 'StreamingAssets' / 'graph.json').is_file():
        print(f'先导出资源：python -m app.engine.export_unity projects\\{project_id}')
        return 1

    logs = ROOT / 'tools' / 'unity_logs'
    logs.mkdir(parents=True, exist_ok=True)

    # 1) 建工程
    if not (PROJECT / 'Assets').is_dir():
        print('[1/3] 创建 Unity 工程…')
        code = run([str(unity), '-batchmode', '-quit', '-createProject', str(PROJECT),
                    '-logFile', str(logs / 'create.log')], logs / 'create.log')
        if code != 0:
            return code
    else:
        print('[1/3] 工程已存在，跳过创建')

    # 2) 搬脚本和资源
    print('[2/3] 复制脚本与 StreamingAssets…')
    assets = PROJECT / 'Assets'

    # 脚本必须分开放：
    #   Builder.cs 引用 UnityEditor → 只能放 Assets/Editor/（Editor 程序集）
    #   其余 .cs 是运行时逻辑      → 放 Assets/ 根（Assembly-CSharp）
    # 放错了会报 CS0234「UnityEditor 里没有 Build / SceneManagement」，
    # 或者出现两份同名类导致编译冲突。
    script_dir = assets / 'Editor'
    script_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source_file in (source / 'Scripts').glob('*.cs'):
        target = script_dir / source_file.name if source_file.name == 'Builder.cs' \
                 else assets / source_file.name
        shutil.copy2(source_file, target)
        copied += 1
    shutil.copytree(source / 'StreamingAssets', assets / 'StreamingAssets', dirs_exist_ok=True)
    print(f'      脚本 {copied} 个（Builder.cs → Assets/Editor/，其余 → Assets/）'
          f'，资源 {len(list((assets / "StreamingAssets").rglob("*")))} 项')

    # 3) 构建
    print('[3/3] 命令行构建 Windows exe…')
    code = run([str(unity), '-batchmode', '-quit', '-projectPath', str(PROJECT),
                '-executeMethod', 'Builder.BuildWindows',
                '-logFile', str(logs / 'build.log')], logs / 'build.log')

    target = PROJECT / 'Build' / '鲸落工坊.exe'
    if target.is_file():
        print()
        print(f'✅ 构建成功：{target}')
        print(f'   大小 {target.stat().st_size // 1024 // 1024} MB')
        print(f'   整个 Build 目录 {sum(f.stat().st_size for f in target.parent.rglob("*") if f.is_file()) // 1024 // 1024} MB')
        return 0
    print()
    print('❌ 没找到产物，看 build.log 末尾')
    return code or 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT_ID))
