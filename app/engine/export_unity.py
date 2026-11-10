"""把项目导出成 Unity 工程能直接吃的 StreamingAssets。

Unity 那边的 `GalgameFlow.cs` 会从 `Application.streamingAssetsPath` 同步读：

    StreamingAssets/graph.json          ← 剧情图（和网页版同一份结构）
    StreamingAssets/assets/bg/*.png     ← 背景
    StreamingAssets/assets/char/*.png   ← 立绘
    StreamingAssets/assets/voice/*.wav  ← 配音

产物目录：

    export/unity/StreamingAssets/...
    export/unity/Scripts/...            ← GameData.cs / GalgameFlow.cs / Builder.cs

然后 tools\\_build_unity.py 会把这两块搬进 Unity 工程并调命令行构建。
"""
import json
import shutil
from pathlib import Path

VOICE_SUFFIXES = {'.wav', '.mp3', '.ogg'}


def export_unity(root, graph):
    root = Path(root)
    output = root / 'export' / 'unity'
    streaming = output / 'StreamingAssets'
    scripts = output / 'Scripts'

    if streaming.exists():
        shutil.rmtree(streaming)
    if scripts.exists():
        shutil.rmtree(scripts)
    streaming.mkdir(parents=True)
    scripts.mkdir(parents=True)

    # 1) 剧情图：只要公开字段，agent_state 那些私有状态不带
    public = {key: graph[key] for key in
              ('version', 'project', 'characters', 'nodes', 'edges') if key in graph}
    (streaming / 'graph.json').write_text(
        json.dumps(public, ensure_ascii=False, indent=1), 'utf-8')

    # 2) 资源：整个 assets 目录搬过去（Unity 同步读文件，路径要和 graph 里一致）
    source_assets = root / 'assets'
    if source_assets.is_dir():
        shutil.copytree(source_assets, streaming / 'assets', dirs_exist_ok=True)

    # 3) 成品信息：游戏名 + 图标。Builder.cs 构建前会读它去设 PlayerSettings，
    #    这样导出的 .exe 的窗口标题/文件名/图标就是用户在「打包」页填的。
    project = graph.get('project') or {}
    product = dict(
        title=str(project.get('game_title') or project.get('name') or ''),
        icon=str(project.get('icon') or ''),
    )
    (streaming / 'product.json').write_text(
        json.dumps(product, ensure_ascii=False, indent=1), 'utf-8')

    # 4) C# 脚本（本文件在 app/engine/ 下，项目根是 parents[2]）
    script_dir = Path(__file__).resolve().parents[2] / 'tools' / 'unity_scripts'
    copied = []
    if script_dir.is_dir():
        for item in script_dir.glob('*.cs'):
            shutil.copy2(item, scripts / item.name)
            copied.append(item.name)

    images = len(list((streaming / 'assets').rglob('*.png'))) if (streaming / 'assets').is_dir() else 0
    voices = sum(1 for f in (streaming / 'assets').rglob('*')
                 if f.suffix.lower() in VOICE_SUFFIXES) if (streaming / 'assets').is_dir() else 0
    return dict(output=output, nodes=len(public.get('nodes', [])),
                images=images, voices=voices, scripts=copied)


def export_unity_and_build(root, graph, *, job_dir=None, timeout=1800,
                           unity_project=None, unity_exe=None, echo=None) -> dict:
    """一步到位：graph → StreamingAssets + Scripts → Unity 命令行构建 → .exe

    返回：
      {"ok": True, "out": "D:\\\\UnityProject\\\\Build\\\\奇迹工坊.exe",
       "unity_project": "D:\\\\UnityProject", "build_log": "…最后 40 行…",
       "seconds": 512.3, "log_file": ..., "exe_bytes": ..., "build_dir_bytes": ...,
       "export": {nodes, images, voices, scripts}}
    失败：缺 graph/节点 → ValueError；Unity 建工程/构建失败或超时 → RuntimeError
    （tools._build_unity.UnityBuildError），消息里带步骤名和 Unity 日志尾部。
    job_dir：Unity 日志目录（默认 tools/unity_logs）。
    """
    import sys
    import time
    started = time.time()
    root = Path(root)
    if not isinstance(graph, dict) or not graph.get('nodes'):
        raise ValueError('graph 为空或没有 nodes，无法导出 Unity')
    studio = Path(__file__).resolve().parents[2]
    if str(studio) not in sys.path:
        sys.path.insert(0, str(studio))
    from tools._build_unity import PROJECT, build_exe   # 唯一一份构建逻辑

    info = export_unity(root, graph)
    result = build_exe(root, unity_project or PROJECT, unity_exe, timeout,
                       log_dir=job_dir, echo=echo or (lambda *_: None))
    result['seconds'] = round(time.time() - started, 1)
    result['export'] = dict(nodes=info['nodes'], images=info['images'],
                            voices=info['voices'], scripts=info['scripts'])
    return result


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if project is None:
        print('用法：python -m app.engine.export_unity <项目目录>')
        raise SystemExit(2)
    data = json.loads((project / 'graph.json').read_text('utf-8'))
    info = export_unity(project, data)
    print(f"导出到 {info['output']}")
    print(f"  节点 {info['nodes']}  图 {info['images']}  语音 {info['voices']}")
    print(f"  脚本 {', '.join(info['scripts']) or '（没找到）'}")
