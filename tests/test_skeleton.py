"""验证骨架在接触填充和素材之前挡住断头、孤岛及无法汇合的分支。"""
from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from app.agents.tools import AgentTools
from app.engine.graph import edges_from_nodes, validate_graph
from app.engine.skeleton import Skeleton, SkeletonNode, skeleton_branch_depth, skeleton_layout, validate_skeleton
from app.mock_data import skeleton as sample_project


def sample_skeleton():
    """用先汇合、后分出结局的八个节点覆盖最常用的骨架形状。"""
    return Skeleton([
        SkeletonNode('k1', 'start', '雨夜', to=['k2']),
        SkeletonNode('k2', 'choice', '怎么开口', to=['k3', 'k4'],
                     labels=['先问好', '递一把伞'], branch_kind='flavor'),
        SkeletonNode('k3', 'scene', '问候', to=['k5']),
        SkeletonNode('k4', 'scene', '递伞', to=['k5']),
        SkeletonNode('k5', 'scene', '聊到过去', to=['k6']),
        SkeletonNode('k6', 'choice', '给出答案', to=['k7', 'k8'],
                     labels=['解释误会', '保留秘密'], branch_kind='key'),
        SkeletonNode('k7', 'ending', '雨停了', end_kind='true'),
        SkeletonNode('k8', 'ending', '约定再见', end_kind='good'),
    ], logline='雨夜偶遇故人', meta={'target_nodes': 8, 'depth': 1})


class SkeletonTest(unittest.TestCase):
    """用小图定位各项结构约束，再让大图检查生成器能否遵守同一套规则。"""

    def test_valid_skeleton(self):
        """连续分支经过汇合后可以再次开启，幕元数据可以缺省。"""
        skeleton = sample_skeleton()
        self.assertIs(validate_skeleton(skeleton), skeleton)
        self.assertEqual(Skeleton.from_dict(skeleton.to_dict()), skeleton)
        self.assertEqual(skeleton_branch_depth(skeleton), 1)

    def test_non_ending_without_exit(self):
        """错误点名断头场景，让编剧能直接定位缺掉的出边。"""
        skeleton = sample_skeleton()
        skeleton.nodes[2].to.clear()
        with self.assertRaisesRegex(ValueError, 'k3.*断头'):
            validate_skeleton(skeleton)

    def test_cycle(self):
        """即使环上每个节点都有出口也必须拒绝。"""
        skeleton = sample_skeleton()
        skeleton.nodes[4].to[:] = ['k2']
        with self.assertRaisesRegex(ValueError, '有环'):
            validate_skeleton(skeleton)

    def test_unreachable_node(self):
        """有出口的孤立节点同样不能进图。"""
        skeleton = sample_skeleton()
        skeleton.nodes.append(SkeletonNode('isolated', 'scene', '孤岛', to=['k5']))
        with self.assertRaisesRegex(ValueError, 'isolated.*不可达'):
            validate_skeleton(skeleton, target_nodes=9)

    def test_dangling_reference(self):
        """检查出口引用，防止临时编号不能映射成正式节点。"""
        skeleton = sample_skeleton()
        skeleton.nodes[3].to[:] = ['missing']
        with self.assertRaisesRegex(ValueError, 'k4.*missing'):
            validate_skeleton(skeleton)

    def test_duplicate_reference(self):
        """重复引用会让编号映射覆盖原条目，必须在分配编号前拒绝。"""
        skeleton = sample_skeleton()
        skeleton.nodes[3].ref = 'k3'
        with self.assertRaisesRegex(ValueError, '重复.*k3'):
            validate_skeleton(skeleton)

    def test_start_must_come_first(self):
        """固定首项才能确保起点始终分到正式编号 n1。"""
        skeleton = sample_skeleton()
        skeleton.nodes.reverse()
        with self.assertRaisesRegex(ValueError, 'start.*第一项'):
            validate_skeleton(skeleton)

    def test_choice_exit_count(self):
        """一个出口的假分支与五个出口的超限分支都无效。"""
        for targets in (['k3'], ['k3', 'k4', 'k5', 'k6', 'k7']):
            with self.subTest(exit_count=len(targets)):
                skeleton = sample_skeleton()
                skeleton.nodes[1].to = targets
                skeleton.nodes[1].labels = ['选项'] * len(targets)
                with self.assertRaisesRegex(ValueError, 'k2.*2–3'):
                    validate_skeleton(skeleton)

    def test_choice_labels_match_exits(self):
        """选项文案缺失不能让填充阶段自行补出新的结构。"""
        skeleton = sample_skeleton()
        skeleton.nodes[1].labels.pop()
        with self.assertRaisesRegex(ValueError, 'k2.*标签'):
            validate_skeleton(skeleton)

    def test_ending_exit(self):
        """结局必须结束路线，不能悄悄跳到另一个节点。"""
        skeleton = sample_skeleton()
        skeleton.nodes[6].to = ['k8']
        with self.assertRaisesRegex(ValueError, 'k7.*ending.*出口'):
            validate_skeleton(skeleton)

    def test_true_ending_required(self):
        """即使其他结局合法，也不能缺少作品的真结局。"""
        skeleton = sample_skeleton()
        skeleton.nodes[6].end_kind = 'alt'
        with self.assertRaisesRegex(ValueError, 'true 结局'):
            validate_skeleton(skeleton)

    def test_flavor_must_join_on_every_path(self):
        """两条选项都能到同一结局还不够，其中一条不能另开结局。"""
        skeleton = sample_skeleton()
        skeleton.nodes[2].type = 'choice'
        skeleton.nodes[2].branch_kind = 'key'
        skeleton.nodes[2].to.append('k8')
        skeleton.nodes[2].labels = ['继续聊', '回家']
        with self.assertRaisesRegex(ValueError, 'k2.*flavor.*汇合'):
            validate_skeleton(skeleton, depth=3)

    def test_nested_branch_depth(self):
        """尚未汇合的必要分支在进入下一个分支时仍应占一层。"""
        skeleton = sample_skeleton()
        skeleton.nodes[1].branch_kind = 'key'
        skeleton.nodes[3].to = ['k8']
        with self.assertRaisesRegex(ValueError, 'k6.*深度 2'):
            validate_skeleton(skeleton)
        self.assertIs(validate_skeleton(skeleton, depth=2), skeleton)
        self.assertEqual(skeleton_branch_depth(skeleton), 2)

    def test_legacy_model_checkpoint_keeps_depth_warning(self):
        """已冻结的旧模型骨架可按原参数续跑，警告不会重复累积。"""
        skeleton = sample_skeleton()
        skeleton.meta['source'] = 'model'
        skeleton.nodes[1].branch_kind = 'key'
        skeleton.nodes[3].to = ['k8']
        validate_skeleton(skeleton)
        restored = Skeleton.from_dict(skeleton.to_dict())
        validate_skeleton(restored)
        self.assertEqual(restored.meta['structure_warnings'], skeleton.meta['structure_warnings'])
        self.assertTrue(any('深度 2 超过上限 1' in text
                            for text in restored.meta['structure_warnings']))

    def test_target_size_tolerance(self):
        """节点数只做宽底线 + 软提示，不再精确卡 ±10%。

        为什么改：模型没法精确命中数字。主人实测「目标 100 → 模型给 150」，
        结构完全正常却直接报错，整轮台词和美术全废。
        现在只拦「不到目标一半」（那才是真的没展开），多了只记一条提示。
        """
        # 8 个节点，目标 6 / 10 都在容忍范围内 → 通过
        for target in (6, 10):
            with self.subTest(target=target):
                skeleton = sample_skeleton()
                self.assertIs(validate_skeleton(skeleton, target_nodes=target), skeleton)
                self.assertNotIn('nodes_warning', skeleton.meta)
        # 目标 30：8 个节点不到一半 → 才算没展开，必须拦
        with self.assertRaisesRegex(ValueError, '不到目标'):
            validate_skeleton(sample_skeleton(), target_nodes=30)
        # 目标 4：8 个节点是两倍，超得很多但结构完整 → 只记软提示，不拦
        skeleton = sample_skeleton()
        self.assertIs(validate_skeleton(skeleton, target_nodes=4), skeleton)
        self.assertIn('nodes_warning', skeleton.meta)
        self.assertIn('不阻塞', skeleton.meta['nodes_warning'])

    def test_graph_stub(self):
        """真实 Graph 契约能读取待填充的全图，且出口文案和编号固定。"""
        skeleton = validate_skeleton(sample_skeleton())
        project = dict(id='demo', name='雨夜', resolution=[1920, 1080], created_at='2026-01-01T00:00:00Z')
        graph = skeleton.to_graph_stub([], project)
        self.assertIs(validate_graph(graph, auto_voice=False), graph)
        self.assertEqual([node['id'] for node in graph['nodes']], [f'n{i}' for i in range(1, 9)])
        self.assertTrue(all(node['status'] == 'pending' for node in graph['nodes']))
        self.assertEqual(graph['edges'], edges_from_nodes(graph['nodes']))
        # 每个出口都带上「去哪」：这里是继续下一段，所以是 chapter
        self.assertEqual(graph['nodes'][1]['choices'][0],
                         dict(label='先问好', to='n3', condition=None, kind='chapter'))
        # 指向结局的出口，kind 应当等于那个结局的类型
        last_choices = graph['nodes'][5].get('choices') or []
        self.assertTrue(last_choices, 'k6 应该有两个通向结局的出口')
        self.assertEqual([choice['kind'] for choice in last_choices], ['true', 'good'])
        self.assertNotIn('choices', graph['nodes'][-1])

    def test_layout_uses_shortest_path(self):
        """存在长短两种路线时，同一节点固定在更近的层上。"""
        skeleton = sample_skeleton()
        skeleton.nodes[1].to[1] = 'k5'
        skeleton.nodes[2].to = ['k4']
        positions = skeleton_layout(validate_skeleton(skeleton))
        self.assertEqual(positions['k1'], {'x': 0, 'y': 320})
        self.assertEqual(positions['k5'], {'x': 640, 'y': 540})
        self.assertEqual(positions['k4']['x'], 960)

    def test_layout_wraps_wide_layer(self):
        """第六个节点换列后，不能占用下一层或其他节点的同一坐标。"""
        nodes = [
            SkeletonNode('s', 'start', '开场', to=['root']),
            SkeletonNode('root', 'choice', '选择', to=['left', 'right'], labels=['左', '右'], branch_kind='key'),
            SkeletonNode('left', 'choice', '左侧', to=['a', 'b', 'c'], labels=['一', '二', '三'], branch_kind='key'),
            SkeletonNode('right', 'choice', '右侧', to=['d', 'e', 'f'], labels=['四', '五', '六'], branch_kind='key'),
            *(SkeletonNode(ref, 'scene', '一幕', to=['end']) for ref in 'abcdef'),
            SkeletonNode('end', 'ending', '收尾', end_kind='true'),
        ]
        positions = skeleton_layout(validate_skeleton(Skeleton(nodes)))
        self.assertEqual(positions['a'], {'x': 960, 'y': 320})
        self.assertEqual(positions['e'], {'x': 960, 'y': 1200})
        self.assertGreater(positions['f']['x'], positions['a']['x'])
        self.assertEqual(positions['f']['y'], positions['a']['y'])
        self.assertEqual(len({(p['x'], p['y']) for p in positions.values()}), len(nodes))

    def test_mock_skeleton_at_scale(self):
        """不用模型验证两档真实规模，并单独计数确认没有断头。"""
        from app.agents.outline import mock_skeleton

        for target in (140, 510):
            with self.subTest(target=target):
                skeleton = mock_skeleton(target_nodes=target, depth=2, endings=3, requirement='雨夜天台')
                self.assertIs(validate_skeleton(skeleton, target_nodes=target, depth=2), skeleton)
                self.assertEqual(len(skeleton.nodes), target)
                self.assertEqual([node.ref for node in skeleton.nodes if node.type != 'ending' and not node.to], [])


class SkeletonToolsTest(unittest.IsolatedAsyncioTestCase):
    """从 writer 工具入口检查拓扑冻结和台词提交的原子性。"""

    async def asyncSetUp(self):
        """注入内存落盘回调，以便观察一次操作究竟提交了几次。"""
        directory = self.enterContext(TemporaryDirectory())
        self.graph = sample_project(dict(id='demo', name='雨夜', resolution=[1920, 1080],
                                         created_at='2026-01-01T00:00:00Z'))
        self.commits = []

        async def save_graph(snapshot):
            """记住独立快照，避免后面的内存更新掩盖误提交。"""
            self.commits.append(deepcopy(snapshot))

        async def save_asset(relative, data):
            """隔离素材生成，只检查 writer 的图提交。"""

        self.tools = AgentTools(self.graph, {'params': {'node_count': 8, 'branch_depth': 1}},
                                directory, save_graph=save_graph, save_asset=save_asset)
        self.skeleton = sample_skeleton().to_dict()
        result = await self.tools.dispatch('writer', 'write_skeleton', self.skeleton)
        self.assertEqual(result, dict(ok=True, nodes=8, edges=8, acts=1))
        self.assertEqual(len(self.commits), 1)

    def _update(self, nid):
        """构造一段最小合法台词，其他字段由每个用例单独变更。"""
        return dict(id=nid, lines=[dict(who='', text='天台的门在雨里轻轻合上')])

    async def test_legacy_tools_cannot_replace_frozen_topology(self):
        """重写骨架和两种旧结构入口都应在任何落盘之前拒绝。"""
        before = deepcopy(self.graph)
        for tool, payload in [
            ('write_skeleton', self.skeleton),
            ('write_nodes', dict(chapter_id='c01', nodes=[dict(ref='k3', type='scene', title='改线')])),
            ('connect_nodes', {'from': 'n1', 'to': 'n3', 'replace': True}),
        ]:
            with self.subTest(tool=tool):
                result = await self.tools.dispatch('writer', tool, payload)
                self.assertEqual(result['error'], 'SKELETON_FROZEN')
        self.assertEqual(self.graph, before)
        self.assertEqual(len(self.commits), 1)

    async def test_fill_rejects_choices_and_node_changes(self):
        """非法出边、新节点和漏掉请求节点都会整批回滚。"""
        before = deepcopy(self.graph)
        for payload in [
            [self._update('n1'), dict(self._update('n2'), choices=[])],
            [self._update('n1'), self._update('n999')],
            [self._update('n1')],
        ]:
            with self.subTest(payload=payload):
                result = await self.tools.dispatch('writer', 'fill_segment',
                                                   dict(node_ids=['n1', 'n2'], nodes=payload))
                self.assertEqual(result['error'], 'SKELETON_FROZEN')
        self.assertEqual(self.graph, before)
        self.assertEqual(len(self.commits), 1)

    async def test_invalid_dialogue_does_not_commit_valid_peer(self):
        """一批的后一个节点校验失败时，前面的合法台词也不能提前落盘。"""
        before = deepcopy(self.graph)
        bad = dict(id='n2', lines=[dict(who='', text='太短。')])
        result = await self.tools.dispatch('writer', 'fill_segment',
                                           dict(node_ids=['n1', 'n2'], nodes=[self._update('n1'), bad]))
        self.assertEqual(result['error'], 'INVALID_DIALOGUE')
        self.assertEqual(self.graph, before)
        self.assertEqual(len(self.commits), 1)

    async def test_repeated_fill_keeps_completed_node(self):
        """相同选择器重试不覆盖完成节点，且不重新生成骨架或出口。"""
        payload = dict(node_ids=['n1'], nodes=[self._update('n1')])
        first = await self.tools.dispatch('writer', 'fill_segment', payload)
        self.assertEqual(first, dict(ok=True, nodes=1, node_ids=['n1']))
        self.assertEqual(self.graph['nodes'][0]['status'], 'done')
        self.assertEqual(self.graph['edges'], self.commits[0]['edges'])
        self.assertEqual(self.graph['agent_state']['skeleton'], self.skeleton)
        second = await self.tools.dispatch('writer', 'fill_segment', payload)
        self.assertEqual(second, dict(ok=True, nodes=0, node_ids=[]))
        self.assertEqual(len(self.commits), 2)

    async def test_model_retries_content_but_not_topology_changes(self):
        """内容错误反馈后可重试，越权时立即返回且不落盘。"""
        self.tools.providers['writer'] = object()
        generated = AsyncMock(side_effect=[
            [dict(id='n1', lines=[dict(who='', text='太短')])],
            [self._update('n1')],
            [dict(self._update('n2'), choices=[])],
        ])
        with patch('app.agents.tools.MOCK', False), patch('app.agents.fill.fill_nodes', generated):
            filled = await self.tools.dispatch('writer', 'fill_segment', dict(node_ids=['n1']))
            self.assertEqual(filled['node_ids'], ['n1'])
            self.assertIn('台词须为 8–40 字', generated.call_args_list[1].kwargs['feedback'])
            rejected = await self.tools.dispatch('writer', 'fill_segment', dict(node_ids=['n2']))
            self.assertEqual(rejected['error'], 'SKELETON_FROZEN')
        self.assertEqual(generated.await_count, 3)
        self.assertEqual(len(self.commits), 2)
        self.assertEqual(self.graph['nodes'][1]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
