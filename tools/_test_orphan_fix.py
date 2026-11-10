"""回归：孤儿节点必须能被修好。

真实样本 p_dab6316cdc6141a9b4b4eb4d32232cbc 的 c02：
    n22 → n23 → n24 ──→ n26(choice) → n27/n28
                  n25 ──→ n26          ← n25「修灯」没有任何入边
模型想做成两条路汇合，却把 n24 直接连到 n26。旧代码里它救不了：
connect_nodes 对已有出口直接拒（EXIT_OCCUPIED），write_nodes 又改不动 scene 出口
→ 连续 3 次结构校验不过，判卡死。

修法：connect_nodes(..., replace=true) 把 n24 的出口改到 n25。
"""
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools, ToolInputError      # noqa: E402

SOURCE = ROOT / 'projects' / 'p_dab6316cdc6141a9b4b4eb4d32232cbc' / 'graph.json'
TMP = ROOT / 'projects' / '_test_orphan_fix'


def load():
    return json.loads(SOURCE.read_text(encoding='utf-8'))


async def main():
    graph = load()
    TMP.mkdir(parents=True, exist_ok=True)

    async def save(value):
        TMP.joinpath('graph.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    tools = AgentTools(graph, {}, TMP, providers={}, save_graph=save)
    chapter = tools._chapter('c02')

    before = tools._structure_errors(chapter)
    print('修复前:', [(e['code'], e['node_id']) for e in before])
    assert any(e['code'] == 'ORPHAN_NODE' and e['node_id'] == 'n25' for e in before), \
        '样本里应有 n25 孤儿节点'

    # ⓪ get_chapter 必须主动把孤岛列出来（别等校验报错才知道）
    view = await tools.get_chapter({'chapter_id': 'c02'}, 'c02')
    print('⓪ get_chapter.orphans =', view['chapter']['orphans'])
    assert 'n25' in view['chapter']['orphans'], 'get_chapter 没报出孤儿节点'

    # ① 不传 replace → 必须被拒（默认不许偷偷改出口）
    try:
        no_rep = await tools.connect_nodes({'from': 'n24', 'to': 'n25'}, 'c02')
    except ToolInputError as exc:                       # 真实调用会由 dispatch 转成 failure
        no_rep = {'ok': False, 'error': exc.code, 'message': str(exc), 'hint': exc.hint}
    print('① 不传 replace: ok=%s error=%s  %s' % (no_rep['ok'], no_rep.get('error'), no_rep.get('message')))
    print('   hint:', no_rep.get('hint'))
    assert not no_rep['ok'] and no_rep['error'] == 'EXIT_OCCUPIED'

    # ② 传 replace → 改出口
    fixed = await tools.connect_nodes({'from': 'n24', 'to': 'n25', 'replace': True}, 'c02')
    print('② 传 replace  : ok=%s replaced=%s orphaned=%s' % (fixed['ok'], fixed.get('replaced'), fixed.get('orphaned')))
    print('   note:', fixed.get('note'))
    assert fixed['ok'] and fixed.get('replaced') == 'n26'

    # ③ 修完必须零错误
    after = tools._structure_errors(chapter)
    print('③ 修复后:', [(e['code'], e['node_id'], e['message'][:40]) for e in after] or '零错误 ✔')
    assert not after, '修完还有错误：%r' % after

    print('\n全部通过 ✔')


asyncio.run(main())
