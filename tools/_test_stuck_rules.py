"""验证「判定卡死」的两条规则已经放宽 / 给了可执行修复。

样本：p_30620485（主人实测卡死的项目），c04 有一个孤儿 n68。
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools      # noqa: E402
from app.agents.loop import WriterLoop       # noqa: E402

SOURCE = ROOT / 'projects' / 'p_30620485f9924bd688fb74e82b8b9c64' / 'graph.json'
TMP = ROOT / 'projects' / '_test_stuck_rules'


class FakePipeline:
    def __init__(self, graph):
        self.graph = graph
        self.providers = {}
        self.params = {'node_count': 64, 'branch_depth': 2}


async def main():
    graph = json.loads(SOURCE.read_text(encoding='utf-8'))
    TMP.mkdir(parents=True, exist_ok=True)

    async def save(value):
        TMP.joinpath('graph.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    tools = AgentTools(graph, dict(params={'node_count': 64}), TMP, providers={}, save_graph=save)
    loop = WriterLoop(FakePipeline(graph), tools)

    # ① 分支数放宽：计划 1 个、实际 2 个，不该再拦（这是卡死的直接原因）
    print('① c04 分支数 2 / 计划 1 →', loop.chapter_branch_error('c04', 1))
    assert loop.chapter_branch_error('c04', 1) is None, '多写一个分支被拦了'
    # 计划 0 个时也允许
    assert loop.chapter_branch_error('c04', 0) is None, '计划 0 个分支却拦了'
    # 写飞了（实际比计划多 2 个以上）仍然要拦：c04 有 2 个 choice，按计划 0 会拦吗？
    # 不会（high = max(0+1, 2) = 2）—— 这个边界是刻意留的：一章 2 个分支永远算合理。
    print('   写飞边界：c04 实际 2 个，计划 0 也不拦（high=2）—— 刻意留的宽松')

    # ② 孤儿建议：错误里必须给出可直接执行的 connect_nodes(..., replace=true)
    chapter = tools._chapter('c04')
    errs = tools._structure_errors(chapter)
    orphans = [e for e in errs if e['code'] == 'ORPHAN_NODE']
    print('② c04 孤儿:', [(e['node_id'], e['message']) for e in orphans])
    assert orphans, '样本里应该有孤儿节点'
    msg = orphans[0]['message']
    assert 'replace=true' in msg and 'connect_nodes' in msg, '孤儿错误里没给可执行的修复动作'

    print('\n全部通过 ✔')


asyncio.run(main())
