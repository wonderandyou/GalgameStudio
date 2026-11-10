r"""经理验收：走接线后的真实路径验证 Unity 导出。

⚠ 必须用**系统 python** 跑（不是 .venv 的）：
   .venv\Scripts\python.exe 在当前 DSH 会话里写 D:\UnityProject 会 PermissionError，
   系统 python 可以。产品实际运行时（用户双击 launcher）不受此限制。

跑：
  C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe tools\_verify_unity_export.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

PROJECT = ROOT / 'projects' / 'p_63b2b758e0ab46d1bf4201629468eb36'


def main():
    if not (PROJECT / 'graph.json').is_file():
        print(f'[FAIL] 找不到项目图：{PROJECT / "graph.json"}')
        return 2
    graph = json.loads((PROJECT / 'graph.json').read_text(encoding='utf-8'))
    print(f'[OK] 项目 {PROJECT.name}  节点 {len(graph.get("nodes", []))}')

    # 走接线后的入口，不直接调底层函数
    from app.engine.export_mock import export_game
    print('[跑] export_game(format="unity") ...（工程已存在时约 20 秒，首次建工程 3~10 分钟）')

    try:
        result = export_game(PROJECT, graph, 'unity')
    except Exception as exc:                                   # noqa: BLE001
        print(f'[FAIL] {type(exc).__name__}: {exc}')
        return 3

    if not isinstance(result, dict):
        print(f'[FAIL] 返回值不是 dict：{type(result).__name__}')
        return 4

    out = Path(result.get('out') or '')
    build_dir = out.parent if out else None
    print(f'[OK] ok={result.get("ok")}  耗时 {result.get("seconds")} 秒')
    print(f'[OK] exe: {out}')
    print(f'     exe 存在: {out.is_file()}  大小: {out.stat().st_size if out.is_file() else 0} 字节')
    if build_dir and build_dir.is_dir():
        total = sum(f.stat().st_size for f in build_dir.rglob('*') if f.is_file())
        print(f'     Build 目录总计: {total / 1024 / 1024:.1f} MB')
    touched = (out.parent / f'{out.stem}_Data') if out else None
    print(f'     Data 目录: {touched} 存在={touched.is_dir() if touched else False}')

    log = result.get('build_log') or ''
    print('--- Unity 日志尾部 ---')
    print('\n'.join(log.strip().splitlines()[-6:]))

    ok = bool(result.get('ok') and out.is_file() and out.stat().st_size > 300_000)
    print('结果:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
