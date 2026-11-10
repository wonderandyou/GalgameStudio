"""回归：结局四分类 + 分支两分类（key/flavor）。

主人 2026-11-08 定：
  结局 true(真) / good(好) / bad(坏) / alt(分歧)
  分支 key(必要，决定结局) / flavor(非必要，汇合回主线，不影响结局)

样本用第六轮生成成功的 p_e6998082（28 节点、两章、结构干净）。
"""
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools          # noqa: E402
from app.contract import Node                    # noqa: E402

SOURCE = ROOT / 'projects' / 'p_e699808204d042ea97fbdf33baf49488' / 'graph.json'
TMP = ROOT / 'projects' / '_test_node_kinds'


def load():
    return json.loads(SOURCE.read_text(encoding='utf-8'))


def ending_sets(node, by_id):
    """这个分支的每条腿分别能到达哪些结局。"""
    legs = []
    for choice in node.get('choices') or []:
        found, seen, todo = set(), set(), [choice['to']]
        while todo:
            cur = todo.pop()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            node_cur = by_id[cur]
            if node_cur['type'] == 'ending':
                found.add(cur)
            else:
                todo.extend(c['to'] for c in node_cur.get('choices') or [])
        legs.append(frozenset(found))
    return legs


async def main():
    base = load()
    by_id = {n['id']: n for n in base['nodes']}
    print('所有分支的结局分布：')
    key_choice = flavor_choice = None
    for n in base['nodes']:
        if n['type'] != 'choice' or not n.get('choices'):
            continue
        legs = ending_sets(n, by_id)
        differs = len(set(legs)) > 1
        print(f"  {n['id']} -> {[c['to'] for c in n['choices']]}  结局集 {[sorted(x) for x in legs]}  "
              f"{'必要' if differs else '非必要'}")
        if differs and key_choice is None:
            key_choice = n
        if not differs and flavor_choice is None:
            flavor_choice = n

    assert key_choice is not None, '样本里要有一个真正影响结局的分支'
    assert flavor_choice is not None, '样本里要有一个汇合回主线的分支'
    target_id, target_node = key_choice['id'], key_choice
    print(f'\n用来测「标错」的必要分支: {target_id}')

    # ① 契约：good 必须被接受
    ok = copy.deepcopy(target_node)
    ok.update(type='ending', end_kind='good', id='n_test_good')
    ok.pop('choices', None)
    Node.model_validate(ok)
    print('① end_kind=good 契约通过 ✔')

    # ② 契约：branch_kind 必须被接受
    flav = copy.deepcopy(target_node)
    flav['branch_kind'] = 'flavor'
    Node.model_validate(flav)
    print('② branch_kind=flavor 契约通过 ✔')

    # ③ 乱填必须被拒
    for bad, field in (('happy', 'end_kind'), ('optional', 'branch_kind')):
        trial = copy.deepcopy(target_node)
        trial[field] = bad
        try:
            Node.model_validate(trial)
            raise AssertionError(f'{field}={bad} 居然通过了')
        except Exception:                            # noqa: BLE001
            print(f'③ {field}={bad} 被拒 ✔')

    TMP.mkdir(parents=True, exist_ok=True)

    async def save(value):
        TMP.joinpath('graph.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    # ④ 把「能到不同结局」的分支标成 flavor → 必须报 FLAVOR_AFFECTS_ENDING
    graph = load()
    for n in graph['nodes']:
        if n['id'] == target_id:
            n['branch_kind'] = 'flavor'
    tools = AgentTools(graph, {}, TMP, providers={}, save_graph=save)
    label = tools.node_meta.get(target_id, {}).get('chapter_id')
    errs = tools._structure_errors(tools._chapter(label) if label else None)
    codes = [e['code'] for e in errs]
    print(f'④ {target_id} 标 flavor 后:', codes)
    assert 'FLAVOR_AFFECTS_ENDING' in codes, '标错的 flavor 没被抓出来'

    # ⑤ 反方向：真正汇合回主线的分支标 flavor → 不能报错
    graph2 = load()
    for n in graph2['nodes']:
        if n['id'] == target_id:
            n['branch_kind'] = 'key'
        if n['id'] == flavor_choice['id']:
            n['branch_kind'] = 'flavor'
    tools2 = AgentTools(graph2, {}, TMP, providers={}, save_graph=save)
    errs2 = tools2._structure_errors(tools2._chapter(label) if label else None)
    codes2 = [e['code'] for e in errs2]
    print(f"⑤ {flavor_choice['id']} 标 flavor（真汇合）后:", codes2 or '零错误 ✔')
    assert 'FLAVOR_AFFECTS_ENDING' not in codes2, '真汇合的 flavor 被误报了'

    print('\n全部通过 ✔')


asyncio.run(main())
