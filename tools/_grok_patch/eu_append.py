

def export_unity_and_build(root, graph, *, job_dir=None, timeout=1800,
                           unity_project=None, unity_exe=None, echo=None) -> dict:
    """一步到位：graph → StreamingAssets + Scripts → Unity 命令行构建 → .exe

    返回：
      {"ok": True, "out": "D:\\\\UnityProject\\\\Build\\\\鲸落工坊.exe",
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
