"""commit D：按 agent_state.skeleton 修复坏图（app/engine/repair.py + API + tools CLI）。

全部在临时目录里跑，不碰 projects/ 下的任何数据。
"""
import asyncio
import copy
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main, mock_api
from app.engine import repair
from app.engine.graph import GraphRejected, apply_graph, structure_problems
from app.store import atomic_json, read_json

ROOT = Path(__file__).resolve().parent.parent


def node(nid, kind, choices=(), *, end_kind=None):
    """满足契约的节点；choices 是 [(label, to), ...]。"""
    data = dict(
        id=nid, type=kind, title=nid, status='done', end_kind=end_kind,
        bg=dict(asset='assets/bg/mock.png', prompt='占位', seed=0, provider='mock'),
        chars=[], lines=[dict(who='', text='一句台词', voice=None, dur=1.0)],
        pos=dict(x=0, y=0), gen=dict(model='test', at='2026-11-10T00:00:00+00:00', cost=0.0))
    if choices:
        data['choices'] = [dict(label=label, to=to, condition=None) for label, to in choices]
    return data


def skeleton_graph():
    """n1 → n2(choice) ─┬─ n3 ─┐
                        └─ n4 ─┴─ n5 → n6(true)，带 skeleton 和 nid→ref 映射。"""
    nodes = [
        node('n1', 'start', [('继续', 'n2')]),
        node('n2', 'choice', [('看清日期', 'n3'), ('先收起来', 'n4')]),
        node('n3', 'scene', [('继续', 'n5')]),
        node('n4', 'scene', [('继续', 'n5')]),
        node('n5', 'scene', [('继续', 'n6')]),
        node('n6', 'ending', end_kind='true'),
    ]
    sk = [dict(ref='k001', type='start', to=['k002']),
          dict(ref='k002', type='choice', to=['k003', 'k004'], labels=['看清日期', '先收起来'],
               effects=['', 'kept']),
          dict(ref='k003', type='scene', to=['k005']),
          dict(ref='k004', type='scene', to=['k005']),
          dict(ref='k005', type='scene', to=['k006']),
          dict(ref='k006', type='ending', to=[], end_kind='true')]
    state = dict(skeleton=dict(nodes=sk),
                 nodes={'n%d' % i: dict(ref='k%03d' % i, chapter_id='c1') for i in range(1, 7)})
    return dict(version=1, project=dict(id='p_' + '0' * 32, name='测试'), characters=[],
                nodes=nodes, edges=[], agent_state=state)


def corrupt(graph):
    """模拟旧前端章节合并：choice 选项累加、start 出口重复、n4 断头。"""
    bad = copy.deepcopy(graph)
    by_id = {n['id']: n for n in bad['nodes']}
    by_id['n1']['choices'] = by_id['n1']['choices'] * 3
    by_id['n2']['choices'] = [dict(label='看清日期', to='n4', condition=None),        # 目标被串
                              dict(label='先收起来', to='n4', condition=None, effects={'kept': True}),
                              dict(label='选项 C', to='n3', condition=None),
                              dict(label='选项 D', to='n5', condition=None),
                              dict(label='选项 E', to='n5', condition=None)]
    by_id['n4'].pop('choices')
    return bad


def labels(graph, nid):
    return [c['label'] for c in next(n for n in graph['nodes'] if n['id'] == nid)['choices']]


class RebuildTests(unittest.TestCase):
    def test_corrupt_graph_rejected_then_repaired_passes_strict_gate(self):
        bad = corrupt(skeleton_graph())
        with self.assertRaises(GraphRejected):
            apply_graph(copy.deepcopy(bad), source='test')
        before = copy.deepcopy(bad)
        built, stat = repair.rebuild(bad)
        self.assertEqual(bad, before, 'rebuild 不能改入参')
        self.assertEqual(structure_problems(built), [])
        apply_graph(copy.deepcopy(built), source='test')               # 严格闸不抛
        self.assertEqual(repair.dead_ends(bad), ['n4'])
        self.assertEqual(repair.dead_ends(built), [])
        self.assertEqual(labels(built, 'n2'), ['看清日期', '先收起来'])
        self.assertEqual([c['to'] for c in built['nodes'][1]['choices']], ['n3', 'n4'])
        self.assertEqual(stat['lost_visible'], [], '保留下来的选项文字不该丢')
        self.assertEqual(len(built['edges']), 6)

    def test_user_edited_label_kept_and_skeleton_label_fallback(self):
        bad = corrupt(skeleton_graph())
        choice = next(n for n in bad['nodes'] if n['id'] == 'n2')
        choice['choices'][0]['label'] = '主人改过的文字'
        choice['choices'][1]['label'] = '  '
        built, stat = repair.rebuild(bad)
        self.assertEqual(labels(built, 'n2'), ['主人改过的文字', '先收起来'])
        self.assertEqual(stat['from_skeleton'], 1)
        self.assertEqual(labels(built, 'n4'), ['继续'])

    def test_effects_follow_target_and_kind_recomputed(self):
        built, _ = repair.rebuild(corrupt(skeleton_graph()))
        first, second = built['nodes'][1]['choices']
        # 旧 port0 的目标是 n4（被串），新目标 n3 → 不沿用；skeleton effects[0] 为空
        self.assertNotIn('effects', first)
        # 旧 port1 目标 n4 与新目标一致 → 沿用旧 effects
        self.assertEqual(second['effects'], {'kept': True})
        self.assertEqual(second['kind'], 'chapter')
        last = next(n for n in built['nodes'] if n['id'] == 'n5')['choices'][0]
        self.assertEqual(last['kind'], 'true')

    def test_lost_visible_counts_dropped_texts(self):
        bad = corrupt(skeleton_graph())
        next(n for n in bad['nodes'] if n['id'] == 'n2')['choices'][4]['label'] = '只在坏图里的文字'
        _, stat = repair.rebuild(bad)
        self.assertIn(('n2', '只在坏图里的文字'), stat['lost_visible'])

    def test_no_skeleton_not_triggered(self):
        bad = corrupt(skeleton_graph())
        bad['agent_state'] = {}
        self.assertFalse(repair.has_skeleton(bad))
        self.assertIsNone(repair.repair_hint(bad))
        built, stat = repair.rebuild(bad)
        self.assertIsNone(built)
        self.assertIn('skeleton', stat['reason'])

    def test_valid_graph_gets_no_hint(self):
        self.assertIsNone(repair.repair_hint(skeleton_graph()))
        self.assertIsNotNone(repair.repair_hint(corrupt(skeleton_graph())))


class RepairProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.project = self.tmp / 'projects' / ('p_' + 'a' * 32)
        self.project.mkdir(parents=True)
        self.backup = self.tmp / '_backup_projects'

    def write(self, graph):
        atomic_json(self.project / 'graph.json', graph)
        return (self.project / 'graph.json').read_bytes()

    def test_dry_run_writes_nothing(self):
        raw = self.write(corrupt(skeleton_graph()))
        info = repair.repair_project(self.project, backup_root=self.backup)
        self.assertTrue(info['repairable'])
        self.assertFalse(info['applied'])
        self.assertEqual((self.project / 'graph.json').read_bytes(), raw)
        self.assertFalse(self.backup.exists())

    def test_apply_backs_up_then_writes_strict_valid_graph(self):
        raw = self.write(corrupt(skeleton_graph()))
        info = repair.repair_project(self.project, backup_root=self.backup, apply=True, stamp='T1')
        self.assertTrue(info['applied'])
        backup = self.backup / 'T1' / (self.project.name + '_graph.json')
        self.assertEqual(Path(info['backup']), backup)
        self.assertEqual(backup.read_bytes(), raw, '备份必须是修复前的原文件')
        disk = read_json(self.project / 'graph.json')
        self.assertEqual(structure_problems(disk), [])
        apply_graph(disk, source='test')
        self.assertEqual(labels(disk, 'n2'), ['看清日期', '先收起来'])

    def test_apply_refused_when_still_invalid(self):
        bad = corrupt(skeleton_graph())
        bad['agent_state']['skeleton']['nodes'][5]['type'] = 'scene'    # 骨架本身有问题也救不回
        bad['nodes'][5]['type'] = 'scene'
        raw = self.write(bad)
        info = repair.repair_project(self.project, backup_root=self.backup, apply=True)
        self.assertFalse(info['repairable'])
        self.assertFalse(info['applied'])
        self.assertEqual((self.project / 'graph.json').read_bytes(), raw)
        self.assertFalse(self.backup.exists())

    def test_no_skeleton_project_untouched_even_with_apply(self):
        bad = corrupt(skeleton_graph())
        bad['agent_state'] = {}
        raw = self.write(bad)
        info = repair.repair_project(self.project, backup_root=self.backup, apply=True)
        self.assertFalse(info['has_skeleton'])
        self.assertFalse(info['applied'])
        self.assertEqual((self.project / 'graph.json').read_bytes(), raw)
        self.assertFalse(self.backup.exists())
        self.assertEqual(repair.scan_hints(self.tmp / 'projects'), {})

    def test_scan_hints_reports_only(self):
        raw = self.write(corrupt(skeleton_graph()))
        hints = repair.scan_hints(self.tmp / 'projects')
        self.assertEqual(list(hints), [self.project.name])
        self.assertTrue(hints[self.project.name]['repairable'])
        self.assertIn('可以按骨架修复', hints[self.project.name]['hint'])
        self.assertEqual((self.project / 'graph.json').read_bytes(), raw)

    def test_cli_dry_run_and_apply(self):
        """tools/repair_from_skeleton.py：默认 dry-run 不写；--apply 才写并备份。"""
        spec = importlib.util.spec_from_file_location('repair_cli', ROOT / 'tools' / 'repair_from_skeleton.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        raw = self.write(corrupt(skeleton_graph()))
        args = ['--projects', str(self.tmp / 'projects'), '--backup', str(self.backup)]
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(args)
        self.assertIn('✓ 可修', out.getvalue())
        self.assertIn('dry-run', out.getvalue())
        self.assertEqual((self.project / 'graph.json').read_bytes(), raw)
        self.assertFalse(self.backup.exists())
        with redirect_stdout(io.StringIO()):
            cli.main(args + ['--apply', '--only', self.project.name])
        self.assertEqual(structure_problems(read_json(self.project / 'graph.json')), [])
        backups = list(self.backup.glob('*/*_graph.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)


class ApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        (self.tmp / 'projects').mkdir()
        self.manager = mock_api.Jobs(self.tmp, projects_root=self.tmp / 'projects')
        for replacement in (patch.object(mock_api, 'jobs', self.manager),
                            patch.object(mock_api, 'MOCK', True),
                            patch.object(self.manager, 'projects_root', lambda: self.tmp / 'projects')):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        response = self.client.post('/api/projects', json={'name': '修复'})
        self.assertEqual(response.status_code, 200, response.text)
        self.project_id = response.json()['id']
        self.root = self.tmp / 'projects' / self.project_id
        graph = corrupt(skeleton_graph())
        graph['project'] = read_json(self.root / 'graph.json')['project']
        atomic_json(self.root / 'graph.json', graph)
        self.raw = (self.root / 'graph.json').read_bytes()

    def test_preview_and_default_post_do_not_write(self):
        preview = self.client.get(f'/api/projects/{self.project_id}/repair-from-skeleton')
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()['repairable'])
        self.assertEqual(preview.json()['dead_ends_before'], 1)
        posted = self.client.post(f'/api/projects/{self.project_id}/repair-from-skeleton', json={})
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertFalse(posted.json()['applied'])
        self.assertEqual((self.root / 'graph.json').read_bytes(), self.raw)
        self.assertFalse(self.manager.backup_root.exists())

    def test_resume_response_carries_hint(self):
        body = self.client.get(f'/api/projects/{self.project_id}/resume').json()
        self.assertIsNotNone(body['repair'])
        self.assertIn('可以按骨架修复', body['repair']['hint'])
        self.assertEqual((self.root / 'graph.json').read_bytes(), self.raw)

    def test_apply_writes_through_gate_with_backup(self):
        posted = self.client.post(f'/api/projects/{self.project_id}/repair-from-skeleton', json={'apply': True})
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertTrue(posted.json()['applied'])
        backups = list(self.manager.backup_root.glob('*/*_graph.json'))
        self.assertEqual([b.read_bytes() for b in backups], [self.raw])
        self.assertEqual(structure_problems(read_json(self.root / 'graph.json')), [])
        self.assertIsNone(self.client.get(f'/api/projects/{self.project_id}/resume').json()['repair'])

    def job(self, repair_on):
        return dict(id='j_' + 'b' * 32, project_id=self.project_id, kind='generate', status='running',
                    events=[], stages=[], body=dict(params=dict(repair_from_skeleton=repair_on)))

    def test_step_switch_off_only_hints(self):
        job = self.job(False)
        result = asyncio.run(self.manager.skeleton_repair_step(self.root, job, read_json(self.root / 'graph.json')))
        self.assertIn('可以按骨架修复', result['hint'])
        self.assertTrue(any('可以按骨架修复' in e['data'].get('text', '') for e in job['events']))
        self.assertEqual((self.root / 'graph.json').read_bytes(), self.raw)
        self.assertFalse(self.manager.backup_root.exists())

    def test_step_switch_on_repairs_with_backup(self):
        job = self.job(True)
        info = asyncio.run(self.manager.skeleton_repair_step(self.root, job, read_json(self.root / 'graph.json')))
        self.assertTrue(info['applied'])
        self.assertEqual(Path(info['backup']).read_bytes(), self.raw)
        self.assertEqual(structure_problems(read_json(self.root / 'graph.json')), [])

    def test_step_not_triggered_without_skeleton(self):
        graph = read_json(self.root / 'graph.json')
        graph['agent_state'] = {}
        atomic_json(self.root / 'graph.json', graph)
        raw = (self.root / 'graph.json').read_bytes()
        job = self.job(True)
        self.assertIsNone(asyncio.run(self.manager.skeleton_repair_step(self.root, job, graph)))
        self.assertEqual(job['events'], [])
        self.assertEqual((self.root / 'graph.json').read_bytes(), raw)
        self.assertFalse(self.manager.backup_root.exists())

    def test_switch_not_in_resume_signature(self):
        a = dict(requirement='r', params=dict(node_count=10, repair_from_skeleton=True))
        b = dict(requirement='r', params=dict(node_count=10))
        self.assertEqual(mock_api.resume_signature(a), mock_api.resume_signature(b))
        self.assertFalse(mock_api.Params().repair_from_skeleton)


if __name__ == '__main__':
    unittest.main()
