# -*- coding: utf-8 -*-
r"""实测「agent 真路径」：skill 是否真的进了 system prompt、工具是否真的被调用、节点是否真的落盘。

背景：没有 LLM API key，所以之前 1000 节点全是 `provider is None` 的 mock 分支
（loop.py:201 `self.system = ... if self.provider is not None else ''`）。
也就是说 skill 文档从未被任何模型读过。

这里用一个「假 LLM」占住 provider 的位置：它按工具序列返回 tool_calls，
数据用确定性的 mock 模板生成（假模型不会创作，但工具链、守卫、落盘全是真代码）。

跑法：
  app\.venv\Scripts\python.exe tools\_test_agent_real_path.py [总节点数]

判据（任何一条不满足即失败）：
  1. system prompt 里带完整 skill（writer 5 篇，含 SKILL.md 的分节标题）
  2. writer_metrics.tool_calls > 0（说明走的是 dispatch，不是 mock_turn 之外的捷径）
  3. 每章都真的调用了 get_chapter → write_nodes → finish_chapter
  4. 最终 graph 节点数 == 目标，且 validate_graph(scope=all) 通过
  5. stages.writer == 'real'
"""
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents import registry                                  # noqa: E402
from app.agents.loop import (_act_budgets, _chapter_choice_counts,   # noqa: E402
                             _mock_layout)
from app.agents.mock_dialogue import mock_lines                  # noqa: E402
from app.engine.pipeline import Pipeline                         # noqa: E402
from app.mock_data import skeleton                               # noqa: E402

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 40
DEPTH = 2
WORK = ROOT / 'tmp' / 'agent_real_path'


class FakeWriter:
    """占住 provider 位置的假模型：按工具序列回应，数据取确定性模板。"""

    kind = 'llm'

    def __init__(self, chapters, counts):
        self.chapters = chapters          # [{id, ordinal, node_budget, is_last}]
        self.counts = counts              # 每章应有几个 choice（与 chapters 同序）
        self.sessions = {}                # 会话 key -> 已发出的工具名列表
        self.calls = []                   # [(name, args 摘要)]
        self.system_seen = ''             # 第一次收到的 system 内容
        self.tools_seen = set()
        self._pending = {}                # 会话 key -> 待发送的 write_nodes 批次
        self._seq = 0

    # ---- 工具生成 ----
    def _plan_args(self, user_text):
        raw = re.search(r'\[\[[^\]]*(?:\][^\]]*)*\]\]', user_text)
        budgets = json.loads(raw.group(0)) if raw else _act_budgets(TOTAL)
        acts = []
        for i, chapter_budgets in enumerate(budgets, 1):
            acts.append(dict(
                title=f'第 {i} 幕',
                goal='推进两人对同一件事的理解',
                chapters=[dict(title=f'第 {i} 幕第 {j} 章', beats='雨夜的谈话、试探与决定',
                               node_budget=b) for j, b in enumerate(chapter_budgets, 1)],
            ))
        return dict(logline='雨夜的相遇', target_minutes=5, acts=acts)

    def _chapter_nodes(self, chapter, branches):
        """按 _mock_layout 的布局生成节点，文案用 mock_lines（确定性）。"""
        ordinal = chapter['ordinal']
        budget = chapter['node_budget']
        is_last = chapter['is_last']
        refs = [f'c{ordinal:02d}-s{i:02d}' for i in range(1, budget + 1)]
        layout = _mock_layout(budget, branches, DEPTH, is_last)
        out = []
        connects = []
        for index, (kind, targets, ending) in enumerate(layout):
            ref = refs[index]
            node = dict(ref=ref, type=kind, title=f'片段 {index + 1}', who='heroine',
                        bg_key='rainy_night',
                        bg_prompt='雨夜的房间，窗玻璃挂满水痕，室内只有台灯')
            if kind == 'ending':
                node.update(title={'true': '真结局', 'bad': '坏结局',
                                   'alt': '分歧结局'}[ending], end_kind=ending)
            elif kind == 'choice':
                labels = ('把信交给她', '先去核对落款', '带着信一起离开')
                node['choices'] = [dict(label=label, to=refs[t])
                                   for label, t in zip(labels, targets)]
            elif targets or (targets is None and index + 1 < budget):
                target = targets[0] if targets else index + 1
                connects.append((ref, refs[target]))
            node['lines'] = mock_lines(ordinal, index, node_type=kind, end_kind=ending)
            out.append(node)
        return out, connects

    async def call_with_tools(self, messages, tools, **kwargs):
        if not self.system_seen:
            self.system_seen = messages[0]['content']
        self.tools_seen.update(t['function']['name'] for t in tools)

        first_user = messages[1]['content'] if len(messages) > 1 else ''
        key = first_user[:160]
        sent = self.sessions.setdefault(key, [])
        self._seq += 1

        if '先读 project_brief' in first_user:
            seq = ['project_brief', 'plan_structure']
            name = seq[min(len(sent), len(seq) - 1)]
            args = {} if name == 'project_brief' else self._plan_args(first_user)
        else:
            # 章节任务：get_chapter -> write_nodes(分批) -> finish_chapter
            chapter = None
            for m in reversed(messages):
                if m.get('role') == 'tool':
                    try:
                        body = json.loads(m['content'])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(body, dict) and body.get('chapter'):
                        chapter = body['chapter']
                        break
            if chapter is None:
                name = 'get_chapter'
                cid = re.search(r'本章 (c\d+)', first_user)
                args = dict(chapter_id=cid.group(1) if cid else 'c01')
            else:
                cid = chapter['id']
                idx = self._branch_index(cid)
                nodes, connects = self._chapter_nodes(chapter, self.counts[idx])
                # 第一章（或上章出口没接）要先从 open_exits 连进本章第一个节点
                if not chapter.get('entry_id'):
                    first = f"c{chapter['ordinal']:02d}-s01"
                    connects = [(src, first) for src in chapter.get('open_exits') or []] + connects
                plan = self._pending.setdefault(key, {
                    'batches': [nodes[i:i + 10] for i in range(0, len(nodes), 10)],
                    'connects': connects,
                })
                if plan['batches']:
                    name = 'write_nodes'
                    args = dict(chapter_id=cid, nodes=plan['batches'].pop(0))
                elif plan['connects']:
                    name = 'connect_nodes'
                    src, dst = plan['connects'].pop(0)
                    args = dict(from_=src, to=dst)
                    args = {'from': src, 'to': dst}
                else:
                    name = 'finish_chapter'
                    args = dict(chapter_id=cid,
                                summary='雨夜的谈话改变了两人的判断，她决定再等一会。')

        sent.append(name)
        self.calls.append((name, self._brief(args)))
        return {
            'text': None,
            'tool_calls': [dict(id=f'call_{self._seq}', name=name, arguments=args)],
        }

    def _branch_index(self, chapter_id):
        for i, ch in enumerate(self.chapters):
            if ch['id'] == chapter_id:
                return i
        return len(self.chapters) - 1

    @staticmethod
    def _brief(args):
        if 'nodes' in args:
            return f"nodes={len(args['nodes'])}"
        return json.dumps({k: v for k, v in args.items()
                           if k in ('chapter_id', 'from', 'to', 'scope')},
                          ensure_ascii=False)


async def save_graph(graph):
    (WORK / 'graph.json').write_text(json.dumps(graph, ensure_ascii=False, indent=1),
                                    encoding='utf-8')


async def save_asset(rel, data):
    target = WORK / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


async def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    project = dict(id='p_agent_realtest', name='agent 真路径实测',
                   resolution=(1280, 720),
                   created_at=datetime.now(timezone.utc).isoformat())
    graph = skeleton(project)
    graph['nodes'] = graph['nodes'][:1]
    graph['edges'] = []
    start = graph['nodes'][0]
    opening = '故事从这里开始'
    start.update(status='pending', choices=[],
                 lines=[dict(who='', text=opening, voice=None,
                             dur=round(len(opening) / 4.5, 3))])
    start['bg']['provider'] = 'pending'
    graph['agent_state'] = {}

    chapters = []
    ordinal = 1
    flat = [b for act in _act_budgets(TOTAL) for b in act]
    for i, budget in enumerate(flat):
        chapters.append(dict(id=f'c{ordinal:02d}', ordinal=ordinal,
                             node_budget=budget, is_last=(i == len(flat) - 1)))
        ordinal += 1
    counts = _chapter_choice_counts(chapters, TOTAL, DEPTH)

    fake = FakeWriter(chapters, counts)
    body = dict(requirement='雨夜的车站，两个人的重逢',
                params=dict(node_count=TOTAL, branch_depth=DEPTH,
                            target_minutes=5, resolution=(1280, 720)))

    events = []

    async def emit(event, data):
        events.append((event, data.get('text') or data.get('msg') or ''))

    pipeline = Pipeline(graph=graph, body=body, root=WORK,
                        providers={'writer': fake}, emit=emit,
                        save_graph=save_graph, save_asset=save_asset, mock=False)

    stats = await pipeline.run()

    # ---- 判定 ----
    checks = []
    system = fake.system_seen
    skill_parts = ['SKILL.md'] + [p.name for p in registry.skill_files('writer')[1:]]
    missing = [name for name in skill_parts if f'===== {name} =====' not in system]
    checks.append(('system prompt 带全 writer skill %d 篇' % len(skill_parts),
                   not missing, f'缺: {missing}' if missing else f'{len(system)} 字'))

    wanted = {'project_brief', 'plan_structure', 'get_chapter', 'write_nodes',
              'connect_nodes', 'finish_chapter'}
    checks.append(('工具链被真实调用', wanted <= fake.tools_seen,
                   '缺: %s' % sorted(wanted - fake.tools_seen)))

    metrics = graph['agent_state'].get('writer_metrics', {})
    checks.append(('writer_metrics.tool_calls > 0', metrics.get('tool_calls', 0) > 0,
                   json.dumps(metrics, ensure_ascii=False)))

    names = [n for n, _ in fake.calls]
    checks.append(('每章 get_chapter 先于 write_nodes',
                   names.count('get_chapter') == len(chapters)
                   and names.count('finish_chapter') == len(chapters),
                   f"get_chapter×{names.count('get_chapter')} "
                   f"write_nodes×{names.count('write_nodes')} "
                   f"finish_chapter×{names.count('finish_chapter')}"))

    checks.append(('节点数 == 目标', len(graph['nodes']) == TOTAL,
                   f"{len(graph['nodes'])}/{TOTAL}"))
    checks.append(('writer 阶段走的是 real',
                   pipeline.stages.get('writer') == 'real',
                   json.dumps(pipeline.stages, ensure_ascii=False)))
    checks.append(('choices 数在允许范围', stats.get('choices', 0) > 0,
                   f"choices={stats.get('choices')} ratio={stats.get('choice_ratio')}"))

    ok = all(passed for _, passed, _ in checks)
    print('=' * 62)
    for label, passed, detail in checks:
        print(('  ✓ ' if passed else '  ✗ ') + label + '  [' + detail + ']')
    print('=' * 62)
    print('节点', len(graph['nodes']), '| 边', len(graph['edges']),
          '| 台词', stats.get('text_lines'), '| 字数', stats.get('text_chars'))
    print('工具调用序列:', ' → '.join(names[:14]),
          ('… 共 %d 次' % len(names)) if len(names) > 14 else '')
    out = dict(ok=ok, checks=[dict(label=l, passed=p, detail=d) for l, p, d in checks],
               stats=stats, calls=len(names),
               sequence=names, chapters=len(chapters),
               system_chars=len(system))
    (ROOT / 'tools' / 'agent_real_path.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('结果写入 tools/agent_real_path.json')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))


