"""图的结构不变量（I1–I11）与写入门 apply_graph 的验收测试（规格 §7.1）。"""
import copy
from collections import Counter
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main, mock_api
from app.agents import outline
from app.engine.skeleton import check_rejoin, validate_skeleton
from app.store import atomic_json, read_json
from app.engine.graph import (GraphRejected, apply_graph, edges_from_nodes, normalize_graph,
                              structure_problems)


def node(nid, kind, to=(), *, labels=None, end_kind=None, branch_kind=None):
    """造一个满足契约的节点；to 给出口目标列表。"""
    data = dict(
        id=nid, type=kind, title=nid, status='done', end_kind=end_kind,
        bg=dict(asset='assets/bg/mock.png', prompt='占位', seed=0, provider='mock'),
        chars=[], lines=[dict(who='', text='一句台词', voice=None, dur=1.0)],
        pos=dict(x=0, y=0), gen=dict(model='test', at='2026-11-10T00:00:00+00:00', cost=0.0),
    )
    if branch_kind:
        data['branch_kind'] = branch_kind
    if to:
        names = labels or ['继续'] * len(to)
        data['choices'] = [dict(label=label, to=target, condition=None) for label, target in zip(names, to)]
    return data


def diamond():
    """s001 → c002 ─┬─ s003 ─┐
                    └─ s004 ─┴─ s005 → e006(true)"""
    nodes = [
        node('s001', 'start', ['c002']),
        node('c002', 'choice', ['s003', 's004'], labels=['看清日期', '先收起来'], branch_kind='flavor'),
        node('s003', 'scene', ['s005']),
        node('s004', 'scene', ['s005']),
        node('s005', 'scene', ['e006']),
        node('e006', 'ending', end_kind='true'),
    ]
    return dict(version=1, project=dict(id='p_test', name='t', resolution=[1920, 1080], created_at='x'),
                characters=[], nodes=nodes, edges=edges_from_nodes(nodes))


def strict(graph):
    """挂上 skeleton，模拟新流程产物（写入门对它严格拒绝）。"""
    graph['agent_state'] = dict(skeleton=dict(nodes=[]))
    return graph


def triples(edges):
    return sorted((e['from'], e['port'], e['to']) for e in edges)


class GraphGateTests(unittest.TestCase):
    def test_edges_always_projection(self):
        graph = diamond()
        graph['edges'] = graph['edges'] * 14 + [dict(id='e9', **{'from': 's001'}, port=0, to='s005')]
        fixed = normalize_graph(graph)
        self.assertEqual(fixed['edges'], edges_from_nodes(fixed['nodes']))
        self.assertEqual(structure_problems(fixed), [])

    def test_oversized_choices_rejected(self):
        graph = strict(diamond())
        graph['nodes'][1]['choices'] = graph['nodes'][1]['choices'] * 13      # 26 个选项
        with self.assertRaises(GraphRejected) as caught:
            apply_graph(graph, source='test')
        self.assertTrue(any('c002' in p and '出边 26' in p for p in caught.exception.problems),
                        caught.exception.problems)

    def test_cosmetic_choice_allowed(self):
        graph = diamond()
        graph['nodes'][1]['choices'] = [dict(label='点头', to='s003', condition=None, effects={'nod': True}),
                                        dict(label='沉默', to='s003', condition=None)]
        graph['nodes'] = [n for n in graph['nodes'] if n['id'] != 's004']
        checked = apply_graph(graph, source='test')
        self.assertEqual(triples(checked['edges']), [('c002', 0, 's003'), ('c002', 1, 's003'),
                                                     ('s001', 0, 'c002'), ('s003', 0, 's005'),
                                                     ('s005', 0, 'e006')])

    def test_duplicate_port_rejected(self):
        graph = diamond()
        graph['edges'].append(dict(id='dup', **{'from': 'c002'}, port=0, to='s004'))
        problems = structure_problems(graph)
        self.assertTrue(any('c002' in p and '(from, port)' in p for p in problems), problems)

    def test_all_problems_listed(self):
        graph = strict(diamond())
        graph['nodes'][0]['choices'] = graph['nodes'][0]['choices'] * 27
        graph['nodes'][1]['choices'] = graph['nodes'][1]['choices'] * 13
        with self.assertRaises(GraphRejected) as caught:
            apply_graph(graph, source='test')
        problems = caught.exception.problems
        self.assertTrue(any('s001' in p and '出边 27' in p for p in problems), problems)
        self.assertTrue(any('c002' in p and '出边 26' in p for p in problems), problems)

    def test_legacy_graph_is_normalized_not_rejected(self):
        graph = diamond()                                             # 无 skeleton = 存量旧图
        graph['nodes'][0]['choices'] = graph['nodes'][0]['choices'] * 27
        graph['edges'] = graph['edges'] * 14
        with self.assertLogs('gal.graph', 'WARNING') as logs:
            checked = apply_graph(graph, source='test')
        self.assertEqual(checked['edges'], edges_from_nodes(checked['nodes']))
        self.assertEqual(len(checked['edges']), 27 + 5)
        self.assertEqual(checked['nodes'], graph['nodes'])            # 除 edges 外一字不改
        self.assertTrue(any('出边 27' in line for line in logs.output), logs.output)

    def test_legacy_ids_only_need_uniqueness(self):
        graph = diamond()
        rename = {'s001': 'n1', 'c002': 'n7', 's003': 'n3', 's004': 'n12', 's005': 'n5', 'e006': 'n2'}
        for item in graph['nodes']:
            item['id'] = rename[item['id']]
            for choice in item.get('choices') or []:
                choice['to'] = rename[choice['to']]
        self.assertEqual(structure_problems(normalize_graph(graph)), [])
        graph['nodes'][2]['id'] = 'n5'
        self.assertTrue(any('ID 重复' in p for p in structure_problems(normalize_graph(graph))))

    def test_bad_condition_rejected(self):
        graph = diamond()
        graph['nodes'][1]['choices'][0]['condition'] = 'knows_date && x'
        self.assertTrue(any('条件' in p for p in structure_problems(normalize_graph(graph))))
        graph['nodes'][1]['choices'][0]['condition'] = '!knows_date'
        self.assertEqual(structure_problems(normalize_graph(graph)), [])


class SaveEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.manager = mock_api.Jobs(Path(temporary.name))
        for replacement in (patch.object(mock_api, 'jobs', self.manager), patch.object(mock_api, 'MOCK', True)):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        response = self.client.post('/api/projects', json={'name': '写入门'})
        self.assertEqual(response.status_code, 200, response.text)
        self.project_id = response.json()['id']

    def graph(self):
        return self.client.get(f'/api/projects/{self.project_id}/graph').json()

    def make_strict(self):
        """给磁盘上的图挂 skeleton，模拟新流程产物。"""
        path = self.manager.project_path(self.project_id) / 'graph.json'
        graph = read_json(path)
        graph['agent_state'] = dict(skeleton=dict(nodes=[]))
        atomic_json(path, graph)

    def test_client_edges_ignored(self):
        """存量旧图：客户端 edges 再脏也不参与；start 长出 27 个出口也照存，edges 按 choices 重算。"""
        body = self.graph()
        start = next(n for n in body['nodes'] if n['type'] == 'start')
        body['edges'] = [dict(id=f'e{i}', **{'from': start['id']}, port=i, to=start['choices'][0]['to'])
                         for i in range(27)]
        saved = self.client.put(f'/api/projects/{self.project_id}/graph', json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(len(self.graph()['edges']), 1)
        body = self.graph()
        start = next(n for n in body['nodes'] if n['type'] == 'start')
        start['choices'] = start['choices'] * 27
        body['edges'] = body['edges'] * 9
        saved = self.client.put(f'/api/projects/{self.project_id}/graph', json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        disk = read_json(self.manager.project_path(self.project_id) / 'graph.json')
        self.assertEqual(disk['edges'], edges_from_nodes(disk['nodes']))
        self.assertEqual(len(disk['edges']), 27)

    def test_strict_graph_rejected_with_all_problems(self):
        """有 skeleton 的图：start 27 个出口 → 422，错误里列出「出边」，且不落盘。"""
        self.make_strict()
        body = self.graph()
        start = next(n for n in body['nodes'] if n['type'] == 'start')
        start['choices'] = start['choices'] * 27
        rejected = self.client.put(f'/api/projects/{self.project_id}/graph', json=body)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        error = rejected.json()['error']
        self.assertEqual(error['code'], 'GRAPH_REJECTED')
        self.assertTrue(any('出边' in p for p in error['problems']), error)
        self.assertEqual(len(self.graph()['edges']), 1)

    def test_oversized_choices_rejected_by_api(self):
        self.make_strict()
        body = self.graph()
        start = next(n for n in body['nodes'] if n['type'] == 'start')
        ending = next(n for n in body['nodes'] if n['type'] == 'ending')
        choice = copy.deepcopy(start)
        choice.update(id='c900', type='choice', branch_kind='key',
                      choices=[dict(label=f'选项{i}', to=ending['id'], condition=None) for i in range(26)])
        start['choices'] = [dict(label='继续', to='c900', condition=None)]
        body['nodes'].append(choice)
        rejected = self.client.put(f'/api/projects/{self.project_id}/graph', json=body)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertTrue(any('c900' in p for p in rejected.json()['error']['problems']))


class GeneratorTests(unittest.TestCase):
    """§7.1 后半：新模板（180 节点默认参数）产出的整图满足 I1–I11。"""

    @classmethod
    def setUpClass(cls):
        cls.skeleton = outline.build_template(target_nodes=180, depth=2, requirement='雨夜的信')
        validate_skeleton(cls.skeleton)
        cls.graph = cls.skeleton.to_graph_stub([], dict(id='demo', name='demo'))
        cls.nodes = cls.graph['nodes']

    def test_structure_problems_empty(self):
        self.assertEqual(len(self.nodes), 180)
        self.assertEqual(structure_problems(normalize_graph(self.graph)), [])

    def test_every_branch_rejoins(self):
        by_ref = {node.ref: node for node in self.skeleton.nodes}
        indegree = Counter(t for node in self.skeleton.nodes for t in node.to)
        for choice in (node for node in self.skeleton.nodes if node.type == 'choice'):
            self.assertEqual(check_rejoin(by_ref, choice, indegree, outline.REJOIN_MAX), [], choice.ref)

    def test_zero_dead_ends(self):
        for item in self.nodes:
            exits = len(item.get('choices') or [])
            if item['type'] == 'ending':
                self.assertEqual(exits, 0, item['id'])
            else:
                self.assertTrue(exits == 1 or 2 <= exits <= 4, item['id'])

    def test_choice_density(self):
        choices = sum(item['type'] == 'choice' for item in self.nodes)
        self.assertLessEqual(choices / len(self.nodes), outline.CHOICE_DENSITY * 1.5)
        self.assertEqual(choices, round(180 * outline.CHOICE_DENSITY))

    def test_start_exactly_one_exit(self):
        starts = [item for item in self.nodes if item['type'] == 'start']
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(starts[0]['choices']), 1)

    def test_no_cycles(self):
        indegree = Counter(edge['to'] for edge in self.graph['edges'])
        queue = [item['id'] for item in self.nodes if not indegree[item['id']]]
        out = {item['id']: [c['to'] for c in item.get('choices') or []] for item in self.nodes}
        seen = 0
        while queue:
            current = queue.pop()
            seen += 1
            for target in out[current]:
                indegree[target] -= 1
                if not indegree[target]:
                    queue.append(target)
        self.assertEqual(seen, len(self.nodes))

    def test_all_reachable(self):
        out = {item['id']: [c['to'] for c in item.get('choices') or []] for item in self.nodes}
        start = next(item['id'] for item in self.nodes if item['type'] == 'start')
        seen, stack = set(), [start]
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(out[current])
        self.assertEqual(len(seen), len(self.nodes))

    def test_endings_only_from_trunk_end(self):
        """§4.5：结局只挂在主干末尾的同一个最终分歧（key）上；默认 4 个，含 true。"""
        parents = {c['to']: item for item in self.nodes for c in item.get('choices') or []}
        endings = [item for item in self.nodes if item['type'] == 'ending']
        self.assertEqual([item['end_kind'] for item in endings], ['true', 'good', 'bad', 'alt'])
        self.assertEqual(len({parents[item['id']]['id'] for item in endings}), 1)
        self.assertEqual({parents[item['id']]['branch_kind'] for item in endings}, {'key'})

    def test_key_choices_carry_declared_flags(self):
        flagged = [c for item in self.nodes for c in item.get('choices') or [] if c.get('effects')]
        self.assertTrue(flagged)
        for choice in flagged:
            self.assertTrue(set(choice['effects']) <= set(self.graph['flags']))

    def test_generator_rejects_unjoined_branch(self):
        """I11：侧枝 3–6 步才汇合，把 rejoin_max 收到 2 → validate_skeleton 拒绝（写入门不管这条）。"""
        skeleton = copy.deepcopy(self.skeleton)
        skeleton.meta['rejoin_max'] = 2
        with self.assertRaisesRegex(ValueError, 'I11 .*走 2 步仍未汇合'):
            validate_skeleton(skeleton)

    def test_generator_rejects_density(self):
        skeleton = copy.deepcopy(self.skeleton)
        skeleton.meta['choice_density'] = 0.02
        with self.assertRaisesRegex(ValueError, 'I9 '):
            validate_skeleton(skeleton)

    def test_cosmetic_exits_allowed_in_skeleton(self):
        """两个出口指向同一节点（cosmetic）不再被骨架单节点校验拦下。"""
        from app.engine.skeleton import _validate_node
        choice = copy.deepcopy(next(node for node in self.skeleton.nodes if node.type == 'choice'))
        choice.to[1] = choice.to[0]
        self.assertIsNone(_validate_node(choice))


if __name__ == '__main__':
    unittest.main()
