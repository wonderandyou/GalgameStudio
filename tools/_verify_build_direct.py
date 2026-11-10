r"""经理验收：用系统 python 直接跑 Unity 构建（不经过后端，不涉沙箱限制）。

背景：DSH 会话里 .venv 的 python 被限制在工作区内写入，Unity 子进程还写不了
%LOCALAPPDATA%\Temp，所以从后端调用必然失败。用系统 python 跑能验证「构建链路
本身对不对」—— 这跟产品能不能用是两件事。

跑：C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe tools\_verify_build_direct.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

PROJECT = ROOT / 'projects' / 'p_63b2b758e0ab46d1bf4201629468eb36'


def main():
    graph = json.loads((PROJECT / 'graph.json').read_text(encoding='utf-8'))
    from app.engine.export_unity import export_unity_and_build

    print(f'[跑] 项目 {PROJECT.name}（{len(graph.get("nodes", []))} 节点）')
    print('[跑] export_unity_and_build(...) 首次建工程可能 3~10 分钟')
    try:
        result = export_unity_and_build(PROJECT, graph, timeout=1800)
    except Exception as exc:                                   # noqa: BLE001
        print(f'[FAIL] {type(exc).__name__}: {str(exc)[:1500]}')
        return 2

    out = Path(result.get('out') or '')
    print(f"[OK] ok={result.get('ok')}  耗时 {result.get('seconds')} 秒")
    print(f'[OK] exe: {out}  存在={out.is_file()}  大小={out.stat().st_size if out.is_file() else 0}')
    print(f"[OK] 工程: {result.get('unity_project')}")
    ok = bool(result.get('ok') and out.is_file() and out.stat().st_size > 300_000)
    print('结果:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
