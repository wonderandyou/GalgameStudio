# -*- coding: utf-8 -*-
"""经理验收：web 导出是否真的用上了界面预设。"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.engine.export_mock import export_web   # noqa: E402

PROJECT = ROOT / 'projects' / 'p_63b2b758e0ab46d1bf4201629468eb36'
OUT_BASE = ROOT / 'tmp' / 'web_check'


def main():
    graph = json.loads((PROJECT / 'graph.json').read_text(encoding='utf-8'))
    # export_web 固定写到 <项目>/export/web，不接受输出目录参数
    out = PROJECT / 'export' / 'web'
    ok_all = True
    for pid in ('ink-classic', 'cinema-dark', 'paper-indie', 'theater-cold'):
        if out.exists():
            shutil.rmtree(out)
        export_web(PROJECT, graph, preset=pid)
        html = (out / 'index.html').read_text(encoding='utf-8')
        hit = re.search(r'ui_preset:\s*([a-z\-]+)', html)
        mark = hit.group(1) if hit else '无'
        play = (out / 'play.html').is_file()
        same = mark == pid
        ok_all = ok_all and same and play
        print(f'{pid:14s} 标记={mark:14s} 一致={same}  play.html={play}  {len(html)} 字节')
    print('结果:', 'PASS' if ok_all else 'FAIL')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
