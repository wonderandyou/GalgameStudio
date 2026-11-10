# -*- coding: utf-8 -*-
"""界面 Agent 端到端实测：5 个工具全部走 dispatch 真路径。

判据：
  1. list_ui_presets 返回 4 套，字段够 agent 判断
  2. match_ui_preset 按关键词命中正确的预设
  3. 接不住的关键词返回 hit=False（不许硬套）
  4. 不存在的预设 / 非法 screen 必须被拒
  5. render_ui 真产出 HTML 文件
  6. tweak_ui_preset 改色后，重新渲染的 HTML 里出现新色
  7. 非法色值 / 越界字号被拒
  8. check_ui 干净时 issues 为空
  9. 文件被删后 check_ui 必须报出来
 10. 越权调用（writer 调 render_ui）被拒
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

WORK = ROOT / 'tmp' / 'ui_agent_test'
checks = []


def judge(label, passed, detail=''):
    checks.append((label, bool(passed), str(detail)[:200]))


def dump(value):
    return json.dumps(value, ensure_ascii=False)


async def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    project = dict(id='p_ui', name='ui test', resolution=(1280, 720), created_at='x')
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

    # 1. 列预设
    r = await tools.dispatch('ui', 'list_ui_presets', {})
    presets = r.get('presets') or []
    ids = [p['id'] for p in presets]
    judge('list_ui_presets 返回四套', r['ok'] and len(presets) == 4
          and ids == ['ink-classic', 'cinema-dark', 'paper-indie', 'theater-cold'], dump(ids))
    judge('预设带 fit（告诉 agent 何时用）', all(p.get('fit') for p in presets), '')

    # 2. 关键词命准
    cases = {
        '江南 雨夜 旧事 水墨': 'ink-classic',
        '雨夜 港口 都市 硬派交易': 'cinema-dark',
        '校园 青春 治愈 纯爱': 'paper-indie',
        '科幻 孤独 压抑 末日': 'theater-cold',
    }
    for text, want in cases.items():
        r = await tools.dispatch('ui', 'match_ui_preset', {'keywords': text})
        top = (r.get('matches') or [{}])[0].get('id')
        judge(f'match「{text}」→ {want}', r['ok'] and top == want, dump(r.get('matches')))

    # 3. 接不住的关键词 / 只沾边缘词
    r = await tools.dispatch('ui', 'match_ui_preset',
                             {'keywords': '蒸汽波 粉紫 迪斯科 磁带 复古老电视'})
    judge('完全无命中时 hit=False 且不硬套', r['ok'] and r.get('hit') is False
          and not r.get('matches'), dump(r))
    r = await tools.dispatch('ui', 'match_ui_preset', {'keywords': '赛博'})
    judge('只沾一个边缘词时标 weak', r['ok'] and r.get('strength') == 'weak',
          dump(r.get('matches')))
    r = await tools.dispatch('ui', 'match_ui_preset', {'keywords': '   '})
    judge('空关键词被拒', not r['ok'] and r.get('error') == 'EMPTY_KEYWORDS', dump(r))

    # 4. 非法入参
    r = await tools.dispatch('ui', 'render_ui', {'preset': 'no-such-preset', 'screen': 'title'})
    judge('不存在的预设被拒', not r['ok'] and r.get('error') == 'PRESET_NOT_FOUND', dump(r))
    r = await tools.dispatch('ui', 'render_ui', {'preset': 'ink-classic', 'screen': 'nope'})
    judge('非法 screen 被拒', not r['ok'], dump(r))

    # 5. 渲染启动页
    r = await tools.dispatch('ui', 'render_ui', {
        'preset': 'ink-classic', 'screen': 'title',
        'title': '奇迹工坊', 'subtitle': 'WHALE FALL WORKSHOP',
        'menu': ['开始游戏', '继续游戏', '读取存档', '设置'],
        'version': 'v0.1.0',
    })
    path = Path(r.get('path') or '')
    body = path.read_text(encoding='utf-8') if path.is_file() else ''
    judge('render_ui 产出 HTML 文件', r['ok'] and path.is_file() and len(body) > 2000,
          f'{path.name if path.name else "?"} {len(body)} 字节')
    judge('渲染结果落进 graph.agent_state.ui_style',
          graph.get('agent_state', {}).get('ui_style', {}).get('title', {}).get('preset') == 'ink-classic',
          '')
    judge('HTML 无残留占位符', '$bg' not in body and '${' not in body, '')

    # 6. 微调后重渲，新色必须出现
    r = await tools.dispatch('ui', 'tweak_ui_preset',
                             {'preset': 'ink-classic', 'accent': '#2f6f6a', 'title_size': 68})
    judge('tweak 成功', r['ok'] and r['tweaks'].get('accent') == '#2f6f6a', dump(r.get('tweaks')))
    r = await tools.dispatch('ui', 'render_ui',
                             {'preset': 'ink-classic', 'screen': 'title', 'title': '奇迹工坊'})
    body = Path(r.get('path') or '_missing_').read_text(encoding='utf-8') if r['ok'] else ''
    judge('微调后的强调色出现在 HTML', '#2f6f6a' in body.lower(), '')
    judge('微调后的字号出现在 HTML', '68px' in body, '')

    # 7. 非法微调
    r = await tools.dispatch('ui', 'tweak_ui_preset', {'preset': 'ink-classic', 'accent': 'red'})
    judge('非法色值被拒', not r['ok'] and r.get('error') == 'INVALID_COLOR', dump(r))
    r = await tools.dispatch('ui', 'tweak_ui_preset', {'preset': 'ink-classic', 'title_size': 300})
    judge('越界字号被拒', not r['ok'] and r.get('error') == 'INVALID_SIZE', dump(r))

    # 8. 其余五块皮同预设渲染
    for screen in ('dialog', 'choice', 'save', 'settings', 'hud'):
        r = await tools.dispatch('ui', 'render_ui',
                                 {'preset': 'ink-classic', 'screen': screen})
        judge(f'{screen} 渲染成功', r['ok'] and Path(r['path']).is_file(), '')

    # 9. check_ui 干净
    r = await tools.dispatch('ui', 'check_ui', {})
    judge('check_ui 干净时 issues 为空', r['ok'] and r.get('issues') == [], dump(r.get('issues')))

    # 10. 删文件后必须报出来
    victim = Path((graph['agent_state']['ui_style']['dialog']['path']))
    (WORK / victim).unlink()
    r = await tools.dispatch('ui', 'check_ui', {})
    caught = any('不存在' in (i.get('problem') or '') for i in (r.get('issues') or []))
    judge('文件被删后 check_ui 抓到', r['ok'] and caught, dump(r.get('issues'))[:160])

    # 11. 越权
    r = await tools.dispatch('writer', 'render_ui', {'preset': 'ink-classic'})
    judge('writer 无权调 render_ui', not r['ok'] and r.get('error') == 'TOOL_FORBIDDEN', dump(r))

    ok_all = all(passed for _, passed, _ in checks)
    print('=' * 60)
    for label, passed, detail in checks:
        print(('  ✓ ' if passed else '  ✗ ') + label + (f'  [{detail}]' if not passed else ''))
    print('=' * 60)
    print(f'结果: {"PASS" if ok_all else "FAIL"}  ({sum(1 for _, p, _ in checks if p)}/{len(checks)})')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
