"""验证模板拓扑和模型文案的边界。"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import outline
from app.engine.graph import validate_graph
from app.engine.skeleton import skeleton_branch_depth, validate_skeleton
from app.providers.base import ProviderError


REQUIREMENT = '雨夜的天台上，两个人沿着一封信查明往事'
PROJECT = dict(id='demo', name='雨夜', resolution=[1920, 1080], created_at='2026-01-01T00:00:00Z')
CHARACTERS = [dict(id='heroine', name='林晚', look='黑色短发', gender='female')]


class FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def call_json(self, messages, *, temperature, max_tokens):
        context = json.loads(messages[-1]['content'])
        self.calls.append((context, max_tokens))
        return self.reply(context)


def story_reply(context, by_ref):
    """按实际请求的槽位模拟正常模型内容。"""
    nodes = []
    for slot in context['slots']:
        ref = slot['ref']
        node = dict(ref=ref, title=f'剧情{ref}', summary=f'{ref} 的新故事沿着现场线索推进。')
        labels = by_ref[ref].labels
        if labels is not None:
            node['labels'] = [f'选择走法 {index}' for index in range(1, len(labels) + 1)]
        nodes.append(node)
    return dict(nodes=nodes, handoff=f'第 {context["batch"]} 批的前情')


def topology(skeleton):
    return [
        (node.ref, node.type, node.act, node.chapter, node.to, node.branch_kind,
         node.end_kind, node.exit_kinds, node.effects)
        for node in skeleton.nodes
    ]


class OutlineTemplateTest(unittest.IsolatedAsyncioTestCase):
    def assert_valid_topology(self, skeleton, target, depth, endings):
        nodes = skeleton.nodes
        self.assertEqual(len(nodes), target)
        self.assertEqual([node.ref for node in nodes if node.type == 'start'], [nodes[0].ref])
        self.assertEqual(len({node.ref for node in nodes}), target)
        order = {node.ref: index for index, node in enumerate(nodes)}
        self.assertEqual([node.end_kind for node in nodes if node.type == 'ending'],
                         list(outline.ENDING_KINDS[:endings]))
        reached = {nodes[0].ref}
        for node in nodes:
            self.assertIn(node.ref, reached)
            self.assertEqual(bool(node.to), node.type != 'ending')
            for target_ref in node.to:
                self.assertLess(order[node.ref], order[target_ref])
                reached.add(target_ref)
        always_passed = {}
        for node in reversed(nodes):
            common = set.intersection(*(always_passed[ref] for ref in node.to)) if node.to else set()
            if node.branch_kind == 'flavor':
                self.assertTrue(common, node.ref)
            always_passed[node.ref] = common | {node.ref}
        self.assertLessEqual(skeleton_branch_depth(skeleton), depth)
        self.assertIs(validate_skeleton(skeleton), skeleton)

    def test_template_topology_and_mock_at_all_scales(self):
        for target, depth, requested, actual in [
            (2, 2, 4, 1), (3, 1, 4, 1), (6, 0, 4, 1), (8, 1, 4, 4),
            (140, 2, 3, 3), (180, 2, 4, 4), (510, 8, 4, 4),
        ]:
            with self.subTest(target=target, depth=depth):
                params = dict(target_nodes=target, depth=depth, endings=requested,
                              requirement=REQUIREMENT, key_branches=2, flavor_branches=8)
                skeleton = outline.build_template(**params)
                self.assert_valid_topology(skeleton, target, depth, actual)
                self.assertEqual(outline.mock_skeleton(**params), skeleton)

    async def run_model(self, target, reply, *, mock=False):
        provider = FakeProvider(reply)
        pipeline = SimpleNamespace(
            mock=mock, params={}, providers={'writer': provider}, log=AsyncMock(),
        )
        tools = SimpleNamespace(graph={'characters': CHARACTERS})
        with patch.object(outline, 'MOCK', False):
            skeleton = await outline.generate_skeleton(
                pipeline, tools, target_nodes=target, depth=2, requirement=REQUIREMENT,
            )
        return skeleton, provider.calls, pipeline.log

    async def test_model_fills_slots_and_hands_off_story_and_cast(self):
        target = 64
        template = outline.build_template(target_nodes=target, depth=2, requirement=REQUIREMENT)
        by_ref = {node.ref: node for node in template.nodes}

        def reply(context):
            raw = story_reply(context, by_ref)
            raw['logline'] = '谜底在天台'
            raw['characters'] = [
                dict(id='heroine', name='恶意覆盖'),
                dict(id='new_role', name='小祝', look='银发'),
                dict(id='new_role', name='重复角色'),
                dict(id='???', name='非法角色'),
                dict(id='new_tail', name='其他角色'),
            ] if context['batch'] == 1 else [dict(id='late', name='后批角色')]
            return raw

        skeleton, calls, logs = await self.run_model(target, reply)
        self.assertEqual(skeleton.meta['source'], 'model')
        self.assertEqual(skeleton.logline, '谜底在天台')
        self.assertEqual([c['id'] for c in skeleton.meta['characters']], ['new_role', 'new_tail'])
        self.assertEqual(skeleton.nodes[0].title, '剧情k001')
        choice = next(node for node in skeleton.nodes if node.type == 'choice')
        self.assertEqual(choice.labels[0], '选择走法 1')
        self.assertEqual(topology(skeleton), topology(template))
        self.assertEqual(len(calls), 4)
        self.assertEqual([tokens for _, tokens in calls], [8192] * 4)
        self.assertEqual([context['prior_story'] for context, _ in calls],
                         ['', '第 1 批的前情', '第 2 批的前情', '第 3 批的前情'])
        slots = [slot for context, _ in calls for slot in context['slots']]
        self.assertEqual([slot['ref'] for slot in slots], [node.ref for node in template.nodes])
        self.assertTrue(all(set(slot) == {'ref', 'kind', 'hint'} for slot in slots))
        self.assertEqual(calls[1][0]['characters'], CHARACTERS + skeleton.meta['characters'])
        logs.assert_not_awaited()

    async def test_180_nodes_nine_batches_survive_seven_model_faults(self):
        target = 180
        template = outline.build_template(target_nodes=target, depth=2, requirement=REQUIREMENT)
        by_ref = {node.ref: node for node in template.nodes}

        def reply(context):
            batch = context['batch']
            if batch == 4:
                # ⑦ 截断的 JSON，连续三次都坏，只能保留该批模板。
                raise ProviderError('INVALID_JSON', 'LLM 未返回合法 JSON：结尾截断', 'test.provider')
            raw = story_reply(context, by_ref)
            rows = raw['nodes']
            if batch == 1:
                # ① 同 ref 多次；② 自环；③ 断头；④ 错误结局标签；⑤ 深度超限。
                rows[0].update(type='choice', to=[rows[0]['ref']], exit_kinds=['bad'], branch_depth=3)
                rows[1].update(type='scene', to=[])
                rows.append(dict(rows[0], title='重复文案'))
                rows.extend([dict(ref='k999', title='多给的节点'), dict(ref=[]), None])
            if batch == 9:
                # ⑥ 少给十个节点，加上失效的一批，只剩约 150 个有效槽位。
                del rows[5:15]
            return raw

        skeleton, calls, logs = await self.run_model(target, reply)
        # §4.3 默认结局数 4（1 true + good/bad + alt），原先跟 key_branches+1=3 走。
        self.assert_valid_topology(skeleton, target, 2, 4)
        self.assertEqual(topology(skeleton), topology(template))
        self.assertEqual([context['batch'] for context, _ in calls], [1, 2, 3, 4, 4, 4, 5, 6, 7, 8, 9])
        self.assertTrue(all(context['batches'] == 9 and len(context['slots']) == 20 for context, _ in calls))
        self.assertIn('截断', calls[4][0]['previous_error'])
        self.assertEqual(logs.await_count, 3)
        self.assertEqual(skeleton.nodes[0].title, '剧情k001')
        self.assertEqual(skeleton.nodes[60].title, template.nodes[60].title)
        self.assertEqual(skeleton.nodes[80].title, '剧情k081')
        warnings = skeleton.meta['structure_warnings']
        self.assertTrue(any('1 条重复' in msg and '结构字段' in msg for msg in warnings))
        self.assertTrue(any('第 4 批无可用回复' in msg for msg in warnings))
        self.assertTrue(any('第 9 批有 10 个节点' in msg for msg in warnings))
        graph = skeleton.to_graph_stub([], PROJECT)
        validate_graph(graph, auto_voice=False)
        targets = {node['id']: node for node in graph['nodes']}
        for node in graph['nodes']:
            for choice in node.get('choices', []):
                self.assertEqual(choice['kind'], targets[choice['to']].get('end_kind') or 'chapter')

    async def test_bad_field_types_and_missing_text_keep_safe_defaults(self):
        template = outline.build_template(target_nodes=40, depth=2, requirement=REQUIREMENT)
        branch = next(node for node in template.nodes if node.type == 'choice')

        def reply(context):
            raw = story_reply(context, {node.ref: node for node in template.nodes})
            if context['batch'] == 1:
                row = next(node for node in raw['nodes'] if node['ref'] == branch.ref)
                row.update(title='故事' * 10 + '\ud800', summary={'bad': True}, labels=[[], '新的选项'])
                raw.update(logline=42, characters=[dict(id='unsafe', name='\ud800')])
            return raw

        skeleton, calls, _ = await self.run_model(40, reply)
        patched = next(node for node in skeleton.nodes if node.ref == branch.ref)
        self.assertEqual(patched.title, '故事' * 4)
        self.assertEqual(patched.summary, branch.summary)
        self.assertEqual(patched.labels, [branch.labels[0], '新的选项'])
        self.assertEqual(skeleton.logline, REQUIREMENT)
        self.assertNotIn('characters', skeleton.meta)
        self.assertEqual(len(calls), 2)
        json.dumps(skeleton.to_dict(), ensure_ascii=False).encode('utf-8')

    async def test_unavailable_provider_keeps_earlier_batches(self):
        template = outline.build_template(target_nodes=60, depth=2, requirement=REQUIREMENT)
        by_ref = {node.ref: node for node in template.nodes}

        def reply(context):
            if context['batch'] == 2:
                raise ProviderError('PROVIDER_UNREACHABLE', '连不上模型服务', 'test.provider')
            return story_reply(context, by_ref)

        skeleton, calls, _ = await self.run_model(60, reply)
        self.assertEqual(topology(skeleton), topology(template))
        self.assertEqual(skeleton.nodes[0].title, '剧情k001')
        self.assertEqual(skeleton.nodes[20].title, template.nodes[20].title)
        self.assertEqual(len(calls), 2)
        self.assertEqual(skeleton.meta['degraded_reason'], '连不上模型服务')

    async def test_mock_mode_does_not_call_model(self):
        def reply(context):
            self.fail(f'MOCK 调用了第 {context["batch"]} 批模型')

        skeleton, calls, _ = await self.run_model(40, reply, mock=True)
        self.assertEqual(skeleton, outline.mock_skeleton(target_nodes=40, depth=2, requirement=REQUIREMENT))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
