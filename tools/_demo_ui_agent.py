# -*- coding: utf-8 -*-
"""界面 Agent 完整链路演示：关键词 → 选预设 → 微调 → 渲染 → 截图。

这一步是「agent 能不能真的按关键词做出界面」的证据：
不只调工具，还要把每一步的判断和产物都打印出来，最后截图。
"""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools   # noqa: E402
from app.mock_data import skeleton        # noqa: E402

WORK = ROOT / 'tmp' / 'ui_demo'
SHOTS = ROOT / 'ui' / 'presets' / '_shots'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
ASSETS = ROOT / 'ui' / 'assets' / 'title'

DEMO = [
    dict(keywords='江南 雨夜 旧事 青瓷', tweak=dict(accent='#2f6f6a'), title='雨落旧街'),
    dict(keywords='都市 雨夜 港口 硬派 交易', tweak=None, title='潮汐契约'),
    dict(keywords='校园 青春 治愈 纯爱', tweak=None, title='放学后的信'),
    dict(keywords='科幻 孤独 压抑 末日', tweak=None, title='最后一班'),
]
BG = {'ink-classic': '', 'cinema-dark': str(ASSETS / 'bg_port.png'),
      'paper-indie': str(ASSETS / 'bg_city.png'),
      'theater-cold': str(ASSETS / 'bg_city.png')}


async def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)

    project = dict(id='p_demo', name='demo', resolution=(1600, 900), created_at='x')
    graph = skeleton(project)
    graph.setdefault('agent_state', {}).setdefault('nodes', {})

    async def save_graph(value):
        pass

    async def save_asset(rel, data):
        target = WORK / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    tools = AgentTools(graph=graph, body=dict(params={}), root=WORK,
                       providers={}, save_graph=save_graph, save_asset=save_asset)

    failures = 0
    for case in DEMO:
        print('=' * 62)
        print(f"关键词：{case['keywords']}")
        r = await tools.dispatch('ui', 'match_ui_preset', {'keywords': case['keywords']})
        top = (r.get('matches') or [{}])[0]
        print(f"  匹配：{top.get('id')} ({top.get('score')}, {top.get('strength')}) "
              f"命中「{top.get('reason')}」")
        print(f"  说明：{r.get('note')}")
        if not top.get('id'):
            failures += 1
            print('  ✗ 没有预设可接')
            continue

        if case['tweak']:
            r = await tools.dispatch('ui', 'tweak_ui_preset',
                                     dict(preset=top['id'], **case['tweak']))
            print(f"  微调：{json.dumps(r.get('tweaks'), ensure_ascii=False)}")

        r = await tools.dispatch('ui', 'render_ui', {
            'preset': top['id'], 'screen': 'title', 'title': case['title'],
            'subtitle': 'WHALE FALL WORKSHOP',
            'menu': ['开始游戏', '继续游戏', '读取存档', '设置'],
            'version': 'v0.1.0',
            'bg': BG.get(top['id'], ''),
            'char_left': str(ASSETS / 'char_l.png'),
            'char_right': str(ASSETS / 'char_r.png'),
        })
        if not r['ok']:
            failures += 1
            print('  ✗ 渲染失败：', json.dumps(r, ensure_ascii=False)[:200])
            continue
        html = Path(r['path'])
        # 复制一份到 ui/presets/（HTML 里的图是 file:/// 绝对路径，挪位置不影响加载），
        # 截图交给 pwsh 做 —— python subprocess 起 Edge 会卡死（实测两次都超时）
        shown = ROOT / 'ui' / 'presets' / f"demo-{top['id']}.html"
        shutil.copyfile(html, shown)
        print(f"  渲染：{shown.name}  {r['bytes']} 字节  用背景={r['used_bg']}")

    r = await tools.dispatch('ui', 'check_ui', {})
    print('=' * 62)
    print(f"check_ui issues：{json.dumps(r.get('issues'), ensure_ascii=False)}")
    print('结果:', 'PASS' if (not failures and not r.get('issues')) else 'FAIL')
    return 0 if (not failures and not r.get('issues')) else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
