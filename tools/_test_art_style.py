# -*- coding: utf-8 -*-
"""实测 art 的三个新工具：set_style / draft_prompt / generate_ui。

判据：
  1. 没定风格时 draft_prompt 必须被拒（STYLE_MISSING）
  2. set_style 成功落进 graph.style
  3. 重复 set_style 默认不覆盖
  4. draft_prompt 把画法/光影/色板/负面词注入进去
  5. 立绘与背景用不同规则（半身 vs 无人）
  6. generate_ui 真产出 PNG 文件并登记进 agent_state.ui
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools          # noqa: E402
from app.mock_data import skeleton               # noqa: E402

WORK = ROOT / 'tmp' / 'art_style_test'


async def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    project = dict(id='p_style', name='style test', resolution=(1280, 720), created_at='x')
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

    checks = []

    # 1. 没定风格就要 prompt -> 必须拒绝
    r = await tools.dispatch('art', 'draft_prompt',
                             {'target': 'background', 'brief': '雨夜的天台'})
    checks.append(('未定风格时 draft_prompt 被拒',
                   not r['ok'] and r.get('error') == 'STYLE_MISSING',
                   json.dumps(r, ensure_ascii=False)[:160]))

    # 2. 定风格
    r = await tools.dispatch('art', 'set_style', dict(
        palette=['#1a1a2e', '#16213e', '#e94560', '#f5f5f5'],
        medium='赛璐璐，细描线',
        light='冷调，低对比，侧逆光',
        typeface='标题思源宋体，正文思源黑体',
        negative=['过曝', '写实照片感'],
    ))
    checks.append(('set_style 落进 graph.style',
                   r['ok'] and graph.get('style', {}).get('medium') == '赛璐璐，细描线',
                   json.dumps(r, ensure_ascii=False)[:200]))

    # 3. 重复 set_style 不覆盖
    r2 = await tools.dispatch('art', 'set_style', {'palette': ['#000000'], 'medium': '厚涂'})
    checks.append(('重复 set_style 不覆盖',
                   r2['ok'] and r2.get('existed') is True and len(graph['style']['palette']) == 4,
                   json.dumps(r2, ensure_ascii=False)[:160]))

    # 4. draft_prompt 注入风格
    r = await tools.dispatch('art', 'draft_prompt',
                             {'target': 'background',
                              'brief': '深夜的学校天台，下雨，地面积水反光'})
    ok = r['ok'] and '赛璐璐' in r['prompt'] and '人物' in r['negative'] and '过曝' in r['negative']
    checks.append(('draft_prompt 注入画法/光影/负面词', ok, r.get('prompt', '')[:200]))
    print('背景 prompt:', r.get('prompt'))
    print('背景 negative:', r.get('negative'))

    # 5. 立绘走另一套规则
    r = await tools.dispatch('art', 'draft_prompt',
                             {'target': 'character', 'brief': '短发，米色风衣，低头侧脸'})
    ok = r['ok'] and '半身' in r['prompt'] and '多个人物' in r['negative']
    checks.append(('立绘 prompt 走立绘规则', ok, r.get('prompt', '')[:200]))
    print('立绘 prompt:', r.get('prompt'))

    # 6. generate_ui 产出并登记
    r = await tools.dispatch('art', 'generate_ui',
                             {'surface': 'title', 'prompt': '标题界面，竖排菜单'})
    asset = r.get('asset', '')
    ui = graph.get('agent_state', {}).get('ui', {}).get('title', {})
    ok = bool(r['ok'] and asset and (WORK / asset).is_file() and ui.get('asset') == asset)
    checks.append(('generate_ui 产出文件并登记', ok, json.dumps(r, ensure_ascii=False)[:200]))

    # 7. check_style 要抓到「角色没有外形锚点」
    r = await tools.dispatch('art', 'check_style', {})
    issues = r.get('issues') or []
    caught = any(i.get('where', '').startswith('character:') for i in issues)
    checks.append(('check_style 抓到 look 为空', bool(r['ok'] and caught),
                   json.dumps(issues, ensure_ascii=False)[:240]))
    print('check_style issues:', json.dumps(issues, ensure_ascii=False))

    ok_all = all(passed for _, passed, _ in checks)
    print('=' * 56)
    for label, passed, detail in checks:
        print(('  ✓ ' if passed else '  ✗ ') + label + '  [' + detail + ']')
    print('=' * 56)
    print('结果:', 'PASS' if ok_all else 'FAIL')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
