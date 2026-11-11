"""2026-11-10 待修复清单（给 Grok）回归测试：P2-1 抠图光晕 / P2-3 气氛分支 / P2-4 时长报错定位。"""
import io
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

from app.agents import outline
from app.assets import cutout
from app.engine.graph import structure_problems, validate_graph
from app.engine.skeleton import validate_skeleton

REQUIREMENT = '雨夜的天台上，两个人沿着一封信查明往事'
PROJECT = dict(id='demo', name='雨夜', resolution=[1920, 1080], created_at='2026-01-01T00:00:00Z')


def red_circle_png():
    image = Image.new('RGB', (256, 256), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 48, 208, 208), fill=(200, 30, 40))
    draw.ellipse((100, 100, 156, 156), fill=(255, 255, 255))   # 前景内部的白
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class CutoutHaloTest(unittest.TestCase):
    """P2-1：羽化不能把已经去掉的白底重新混回半透明边缘。"""

    def test_feathered_edge_has_no_background_white(self):
        out = Image.open(io.BytesIO(cutout._threshold_cutout(red_circle_png()))).convert('RGBA')
        px = out.load()
        edge = [px[x, y] for x in range(256) for y in range(256) if 0 < px[x, y][3] < 255]
        self.assertGreater(len(edge), 1000)                       # 羽化还在
        self.assertEqual(sum(1 for p in edge if p[:3] == (255, 255, 255)), 0)
        mean = [sum(p[i] for p in edge) / len(edge) for i in range(3)]
        self.assertGreater(mean[0], 150)
        self.assertLess(mean[1], 80)                              # 偏红，不是 (228,144,149) 的粉白
        inner = [px[x, y] for x in range(118, 139) for y in range(118, 139)]
        self.assertTrue(all(p == (255, 255, 255, 255) for p in inner))
        self.assertEqual(px[2, 2][3], 0)

    def test_existing_alpha_passes_through(self):
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((8, 8, 56, 56), fill=(10, 200, 30, 255))
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        self.assertEqual(cutout.remove_white_background(buffer.getvalue()), buffer.getvalue())


class FlavorBranchesTest(unittest.IsolatedAsyncioTestCase):
    """P2-3：骨架模式里「气氛分支」真的改变图，且不改结局数、不破 I1–I11、不超节点预算。"""

    def build(self, target, flavor, key=2):
        return outline.mock_skeleton(target_nodes=target, depth=2, endings=key, requirement=REQUIREMENT,
                                     key_branches=key, flavor_branches=flavor)

    def test_flavor_changes_graph_but_not_endings(self):
        for target in (60, 100, 180, 510):
            with self.subTest(target=target):
                low, high = self.build(target, 0), self.build(target, 120)
                for skeleton in (low, high):
                    self.assertEqual(len(skeleton.nodes), target)
                    validate_skeleton(skeleton, target_nodes=target, depth=2)
                    self.assertEqual(structure_problems(skeleton.to_graph_stub([], PROJECT)), [])
                choices = lambda s: [n for n in s.nodes if n.type == 'choice']
                self.assertLess(len(choices(low)), len(choices(high)))
                self.assertEqual(sum(n.branch_kind == 'flavor' for n in choices(low)), 0)
                self.assertGreater(sum(n.branch_kind == 'flavor' for n in choices(high)), 0)
                self.assertEqual([n.end_kind for n in low.nodes if n.type == 'ending'],
                                 [n.end_kind for n in high.nodes if n.type == 'ending'])
                self.assertTrue(any(w.startswith('气氛分支计划 120 个') for w in high.meta['structure_warnings']))

    def test_flavor_count_honoured_when_budget_allows(self):
        skeleton = self.build(2000, 120)
        self.assertEqual(sum(n.branch_kind == 'flavor' for n in skeleton.nodes if n.type == 'choice'), 120)
        self.assertEqual([w for w in skeleton.meta.get('structure_warnings', []) if w.startswith('气氛')], [])

    def test_none_keeps_density_behaviour(self):
        skeleton = outline.mock_skeleton(target_nodes=180, depth=2, endings=4, requirement=REQUIREMENT)
        self.assertEqual(sum(n.type == 'choice' for n in skeleton.nodes), round(180 * outline.CHOICE_DENSITY))

    async def test_pipeline_params_reach_template_and_log_warning(self):
        self.assertEqual(outline.template_params({'flavor_branches': 5})['flavor_branches'], 5)
        pipeline = SimpleNamespace(mock=True, params={'key_branches': 2, 'flavor_branches': 120},
                                   providers={}, log=AsyncMock())
        with patch.object(outline, 'MOCK', True):
            skeleton = await outline.generate_skeleton(pipeline, SimpleNamespace(graph={'characters': []}),
                                                       target_nodes=100, depth=2, requirement=REQUIREMENT)
        self.assertEqual(sum(n.type == 'ending' for n in skeleton.nodes), 2)
        self.assertTrue(any('气氛分支计划 120 个' in call.args[0] and call.args[1] == 'warn'
                            for call in pipeline.log.await_args_list))


class VoiceDurationMessageTest(unittest.TestCase):
    """P2-4：时长不一致要说清是哪个节点的第几句、期望多少、实际多少。"""

    def test_message_names_node_line_and_durations(self):
        skeleton = outline.mock_skeleton(target_nodes=8, depth=1, requirement=REQUIREMENT)
        graph = skeleton.to_graph_stub([], PROJECT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            voice = root / 'assets' / 'voice' / 'one_second.wav'
            voice.parent.mkdir(parents=True)
            with wave.open(str(voice), 'wb') as handle:
                handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
                handle.writeframes(b'\x00\x00' * 16000)
            node = graph['nodes'][0]          # 第一个节点：在它的资产检查之前就先查到这句
            node['lines'].append(dict(who='', text='这一幕的台词正在准备', voice='assets/voice/one_second.wav', dur=2.0))
            with self.assertRaises(ValueError) as caught:
                validate_graph(graph, root, False)
        message = str(caught.exception)
        self.assertIn('语音实际时长不一致', message)
        self.assertIn(f'节点 {node["id"]}', message)
        self.assertIn('第 2 句', message)
        self.assertIn('lines[1]', message)
        self.assertIn('期望 dur=2.00 秒', message)
        self.assertIn('实际 1.00 秒', message)


if __name__ == '__main__':
    unittest.main()
