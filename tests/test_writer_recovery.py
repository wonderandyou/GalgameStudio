"""Writer recovery regressions using the real tool dispatcher."""

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agents.loop import WriterLoop
from app.agents.tools import AgentTools
from app.mock_data import skeleton


CHAPTER_ID = 'c01'
OPENING = 'c01-s01'
MIDDLE = 'c01-s02'
INSERT = 'c01-s03'
ENDING = 'c01-s04'
READ = ('get_chapter', {'chapter_id': CHAPTER_ID})
VALIDATE = ('validate_graph', {'scope': 'chapter'})
FINISH = ('finish_chapter', {'chapter_id': CHAPTER_ID, 'summary': '读完来信。'})


def connect(source, target, **options):
    return 'connect_nodes', {'from': source, 'to': target, **options}


class RecoveryWriter:
    cfg = {'model': 'test-writer'}

    def __init__(self):
        self.waiting_turns = 0
        self.recovery_messages = None
        self.turn = 0

    async def call_with_tools(self, messages, tools, **kwargs):
        self.turn += 1
        repairing = any(message['role'] == 'user' and '结构修复' in message['content']
                        for message in messages)
        if not repairing:
            self.waiting_turns += 1
            if self.waiting_turns > 4:
                raise AssertionError('Writer never entered structural recovery')
            calls = [READ] + [('project_brief', {})] * 9
        elif self.recovery_messages is None:
            self.recovery_messages = copy.deepcopy(messages)
            calls = [READ]
        else:
            calls = [
                connect(INSERT, MIDDLE),
                connect(OPENING, INSERT, replace=True),
                VALIDATE,
                FINISH,
            ]
        return dict(text=None, tool_calls=[
            dict(id=f'call-{self.turn}-{index}', name=name, arguments=args)
            for index, (name, args) in enumerate(calls)
        ])


class WriterRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        project = dict(id='p_' + '0' * 32, name='修复测试', resolution=[1920, 1080],
                       created_at='2026-01-01T00:00:00+00:00')
        self.body = dict(requirement='雨夜收到一封信', params=dict(node_count=5, branch_depth=1))
        self.graph = skeleton(project)
        start = dict(self.graph['nodes'][0], status='pending', choices=[])
        self.graph.update(nodes=[start], edges=[], agent_state={})
        self.save_graph = AsyncMock()
        self.tools = AgentTools(self.graph, self.body, Path('.'), save_graph=self.save_graph,
                                library=object())
        plan = dict(logline='雨夜来信', target_minutes=60, acts=[
            dict(title='雨夜', chapters=[dict(title='来信', beats='她拆开来信', node_budget=4)]),
        ])
        self.assertTrue((await self.tools.dispatch('writer', 'plan_structure', plan))['ok'])
        nodes = [
            dict(ref=OPENING, title='收到信', type='scene'),
            dict(ref=MIDDLE, title='拆信', type='scene'),
            dict(ref=INSERT, title='认出字迹', type='scene'),
            dict(ref=ENDING, title='读信', type='ending', end_kind='true'),
        ]
        for call in (
            ('write_nodes', dict(chapter_id=CHAPTER_ID, nodes=nodes)),
            connect('n1', OPENING),
            connect(OPENING, MIDDLE),
            connect(MIDDLE, ENDING),
        ):
            result = await self.dispatch(call)
            self.assertTrue(result['ok'], result)

    async def dispatch(self, call):
        return await self.tools.dispatch('writer', *call, chapter_id=CHAPTER_ID)

    async def test_replace_preserves_reachable_subtree(self):
        before = copy.deepcopy(self.graph)
        self.save_graph.reset_mock()
        replace = connect(OPENING, INSERT, replace=True)
        rejected = await self.dispatch(replace)
        self.assertEqual(rejected.get('error'), 'WOULD_ORPHAN', rejected)
        self.assertEqual(self.graph, before)
        self.save_graph.assert_not_awaited()

        for call in (connect(INSERT, MIDDLE), replace, VALIDATE):
            result = await self.dispatch(call)
            self.assertTrue(result['ok'], result)
        self.assertEqual(self.save_graph.await_count, 2)

    async def test_writer_restarts_stalled_chapter_and_finishes(self):
        provider = RecoveryWriter()
        pipeline = SimpleNamespace(
            graph=self.graph, params=self.body['params'], providers={'writer': provider},
            agent=AsyncMock(), log=AsyncMock(),
        )
        await WriterLoop(pipeline, self.tools).model_work('继续写当前章节', CHAPTER_ID)

        self.assertEqual(provider.waiting_turns, 4)
        self.assertIsNotNone(provider.recovery_messages)
        self.assertEqual({message['role'] for message in provider.recovery_messages}, {'system', 'user'})
        self.assertEqual(self.tools.chapters[0]['status'], 'done')
        self.assertTrue((await self.dispatch(VALIDATE))['ok'])


if __name__ == '__main__':
    unittest.main()
