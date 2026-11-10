"""回归：分支节点的多条 choice 指向同一目标时，写入阶段必须挡住。

起因（2026-11-08 实测，p_9eae638010a344ba818f7274becb5baa）：n12 两条 label
不同、to 都是 n13，非法图直接落盘；validate_graph 只报「分支须有 2–4 个不同
目标」不说节点，writer 不知道改谁，重写 3 次同样内容 → 判卡死。
"""
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools          # noqa: E402
from app.agents.loop import _describe            # noqa: E402

SOURCE = ROOT / 'projects' / 'p_9eae638010a344ba818f7274becb5baa' / 'graph.json'
TMP = ROOT / 'projects' / '_test_dupe_choice'


def build():
    base = json.loads(SOURCE.read_text(encoding='utf-8'))
    base.pop('agent_state', None)
    state = json.loads(SOURCE.read_text(encoding='utf-8'))['agent_state']
    base['agent_state'] = state
    TMP.mkdir(parents=True, exist_ok=True)
    return base


async def main():
    graph = build()

    async def save(value):
        TMP.joinpath('graph.json').write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8')

    tools = AgentTools(graph, {}, TMP, providers={}, save_graph=save)

    # ① 重写 c01-s11（=n12），两条选项都指向 c01-s12（=n13）→ 必须被挡
    bad = await tools.write_nodes({'chapter_id': 'c01', 'nodes': [{
        'ref': 'c01-s11', 'type': 'choice', 'title': '两个人一起',
        'lines': ['雨小了一点'],
        'choices': [{'label': '「那就两个人一起走」', 'to': 'c01-s12'},
                    {'label': '「你把它撕了」', 'to': 'c01-s12'}],
    }]}, 'c01')
    print('① 重复目标：ok=%s error=%s' % (bad['ok'], bad.get('error')))
    print('   message:', bad.get('message'))
    print('   hint   :', bad.get('hint'))
    print('   cands  :', bad.get('candidates'), 'spare=', bad.get('remaining_budget'))
    assert not bad['ok'] and bad['error'] == 'DUPLICATE_CHOICE_TARGET', '重复目标没被挡住！'

    # ② _describe 必须把节点 id 带出来（否则 writer 不知道改谁）
    desc = _describe(bad)
    print('② 续写提示:', desc)
    assert 'n12' in desc, '_describe 丢了节点 id：%r' % desc

    # ③ 结构校验的错误消息必须带节点 id + 重复目标
    graph2 = build()
    tools2 = AgentTools(graph2, {}, TMP, providers={}, save_graph=save)
    errs = tools2._structure_errors(tools2._chapter('c01'))
    print('③ 结构错误:', [(e['code'], e['node_id'], e['message']) for e in errs])
    choice_errs = [e for e in errs if e['code'] == 'INVALID_CHOICE']
    assert choice_errs, '没有 INVALID_CHOICE 错误'
    msg = choice_errs[0]['message']
    assert 'n12' in msg and 'n13' in msg, '结构错误没带节点/重复目标：%r' % msg

    print('\n全部通过 ✔')


asyncio.run(main())
