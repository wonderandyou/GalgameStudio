import copy
import json
import tempfile
import unittest
from pathlib import Path

from app.engine.contracts import stage_report
from app.engine.pipeline import Pipeline
from app.mock_data import silent_wav, skeleton
from app.providers.base import ProviderError


class PartialTTS:
    """首句是真通道交付，之后模拟通道耗尽。"""
    def __init__(self):
        self.calls = 0

    async def call(self, text, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise ProviderError('ALL_CHANNELS_FAILED', '测试通道不可用', 'test.tts')
        return {'data': silent_wav(len(text) / 4.5)}


class BrokenArtPipeline(Pipeline):
    async def art(self):
        self.graph['nodes'][0]['bg']['asset'] = 'assets/bg/missing.png'


class UnfinishedWriterPipeline(Pipeline):
    async def writer(self):
        await super().writer()
        self.graph['nodes'][0]['status'] = 'pending'


class StageContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.graph = skeleton(dict(id='p_test', name='测试', resolution=(320, 180),
                                   created_at='2026-01-01T00:00:00Z'))
        self.graph['agent_state'] = {}
        self.events = []
        self.saved = None
        self.body = dict(requirement='雨夜的故事', params=dict(
            node_count=8, branch_depth=1, auto_voice=True, final_check=True, resolution=(320, 180)))

    async def emit(self, name, data):
        self.events.append((name, data))

    async def save_graph(self, graph):
        self.saved = copy.deepcopy(graph)

    async def save_asset(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def pipeline(self, cls=Pipeline, **kwargs):
        return cls(self.graph, self.body, self.root, emit=self.emit, save_graph=self.save_graph,
                   save_asset=self.save_asset, **kwargs)

    async def test_mock_and_partial_fallback_are_visible_to_consumers(self):
        pipeline = self.pipeline(providers={'voice': PartialTTS()})
        stats = await pipeline.run()
        for stage in ('writer', 'art', 'voice'):
            self.assertEqual(stats['stages'][stage], 'mock')
            self.assertTrue(stats['impacts'][stage])
        self.assertEqual(stats['dependencies']['art']['writer']['status'], 'mock')
        self.assertEqual(stats['dependencies']['voice']['writer']['status'], 'mock')
        for stage in ('writer', 'art', 'voice'):
            self.assertEqual(stats['dependencies']['build'][stage]['impact'], stats['impacts'][stage])
        voice = stats['gates']['voice']['evidence']
        self.assertEqual(voice['silent'], voice['lines'] - 1)
        self.assertEqual(stats['stages']['build'], 'real')
        self.assertEqual(stats['stages']['review'], 'real')
        report = self.saved['agent_state']['review']['final']
        self.assertEqual({item['where'] for item in report['items'] if item['code'] == 'STAGE_DEGRADED'},
                         {'writer', 'art', 'voice'})
        self.assertEqual(json.loads(json.dumps(stage_report(self.saved)))['impacts'], stats['impacts'])

    async def test_failed_art_gate_is_saved_and_blocks_build(self):
        pipeline = self.pipeline(BrokenArtPipeline, providers={}, mock=True)
        with self.assertRaises(ProviderError) as failure:
            await pipeline.run()
        self.assertEqual(failure.exception.payload['code'], 'STAGE_GATE_FAILED')
        state = self.saved['agent_state']['pipeline']
        self.assertEqual(state['stages']['art'], 'failed')
        self.assertEqual(state['gates']['art']['status'], 'failed')
        self.assertEqual(state['completed'], ['writer'])
        self.assertNotIn('build', state['stages'])
        self.assertFalse(any(name == 'done' for name, _ in self.events))

        # 模拟服务重启，从已提交的编剧阶段继续补美术。
        self.graph = self.saved
        recovered = await self.pipeline(providers={}, mock=True).run()
        self.assertEqual(recovered['stages']['art'], 'mock')
        self.assertEqual(recovered['stages']['build'], 'real')
        self.assertNotIn('art', recovered['failures'])

    async def test_disabled_voice_is_an_explicit_build_input(self):
        self.body['params'].update(auto_voice=False, final_check=False)
        stats = await self.pipeline(providers={}, mock=True).run()
        self.assertEqual(stats['dependencies']['build']['voice']['status'], 'off')
        self.assertEqual(stats['gates']['voice']['status'], 'off')
        self.assertEqual(stats['stages']['review'], 'off')

    async def test_writer_cannot_hand_off_an_unfilled_frozen_graph(self):
        with self.assertRaisesRegex(ProviderError, '骨架仍有节点未填充'):
            await self.pipeline(UnfinishedWriterPipeline, providers={}, mock=True).run()
        state = self.saved['agent_state']['pipeline']
        self.assertEqual(state['stages']['writer'], 'failed')
        self.assertEqual(state['completed'], [])
        self.assertNotIn('art', state['stages'])

    async def test_art_refreshes_pose_plan_after_base_generation(self):
        pipeline = self.pipeline(providers={})
        first, second = self.graph['nodes']
        first['chars'][0].update(stance='sit', mood='微笑')
        second['chars'][0].update(stance='lean', mood='悲伤')
        await pipeline.ensure_style()
        self.assertEqual((await pipeline.tool('art', 'list_pending_assets', {}))['poses'], [])
        report = await pipeline.art_poses({})
        self.assertEqual(report['total'], 3)
        self.assertEqual(report['failed'], [])
        paths = [node['chars'][0]['pose'] for node in self.graph['nodes']]
        base = self.graph['characters'][0]['ref_image']
        self.assertEqual(len(set(paths + [base])), 3)
        self.assertEqual((await pipeline.tool('art', 'list_pending_assets', {}))['poses'], [])
