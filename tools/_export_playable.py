"""把一个项目导出成「双击就能全屏玩」的文件夹。

产出：
    <项目>\export\web\index.html      ← 页面 + 资源（内嵌 JSON，file:// 直接能跑）
    <项目>\export\web\播放.exe         ← 本机编译的全屏启动器（kiosk 模式）

为什么这么发：这台机器开着 Smart App Control，PyInstaller 的 exe 会被静默拦；
本机 g++ 编译的 exe 直接放行（桌面那些启动器都是这么来的）。

跑：app\\.venv\\Scripts\\python.exe tools\\_export_playable.py <project_id>
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.export_mock import export_game  # noqa: E402


def main(project_id):
    project = ROOT / 'projects' / project_id
    graph = json.loads((project / 'graph.json').read_text('utf-8'))
    output = export_game(project, graph, 'web')
    print(f'导出到：{output}')

    player = ROOT / 'tools' / 'player' / 'galplayer.exe'
    if player.is_file():
        target = output / '播放.exe'
        shutil.copy2(player, target)
        print(f'播放器：{target}（{target.stat().st_size // 1024} KB）')
    else:
        print('⚠ 没找到 tools\\player\\galplayer.exe，先编译它')

    page = output / 'index.html'
    print(f'页面：{page}（{page.stat().st_size // 1024} KB）')
    print()
    print('双击「播放.exe」= 全屏无边框玩；双击 index.html = 浏览器里玩')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'p_63b2b758e0ab46d1bf4201629468eb36'))
