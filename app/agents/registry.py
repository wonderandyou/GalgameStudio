"""内置 Agent 注册表：把「身份、skill 文档、可用工具」绑在一起。

一个 Agent = 一份 skill（它该怎么干活）+ 一组工具（它能动什么）。
skill 正文在 `app/skills/<agent>/SKILL.md`，细则在同一目录下的分篇 markdown。

约定
----
- `skill` 目录名与 agent 名一致；`SKILL.md` 是入口，其余分篇按 `01-` `02-` 前缀排序。
- `tools` 必须与 `tool_schema.TOOLS[agent]` 完全一致；`check()` 会校验。
- `stage` 对应流水线阶段名（`engine/pipeline.py` 里的 writer/art/voice/build）。
"""
from pathlib import Path

from . import tool_schema

SKILL_ROOT = Path(__file__).resolve().parent.parent / 'skills'

AGENTS = {
    'writer': {
        'name': '编剧 Agent',
        'role': '把一句需求变成可玩上千节点的完整剧情结构',
        'stage': 'writer',
        'skill': 'writer',
        'model_kind': 'llm',
        'tools': tool_schema.tool_names('writer'),
        'outputs': ['全图骨架', '节点与台词', '分支与结局', '前情摘要'],
        'guardrails': [
            '完整拓扑通过校验后一次发布，每个非结局节点都有后继',
            '填充每批最多 20 个节点，保留骨架分配的节点和出口',
            '恢复任务时按节点完成状态续写',
        ],
    },
    'art': {
        'name': '美术 Agent',
        'role': '按 bg_key 归并后批量出图，保证同一角色跨节点视觉一致',
        'stage': 'art',
        'skill': 'art',
        'model_kind': 'image',
        'tools': tool_schema.tool_names('art'),
        'outputs': ['背景图', '角色立绘', '素材一致性报告'],
        'guardrails': [
            '同一 bg_key 只生成一次，不许逐节点重复生图',
            '角色立绘必须带外形特征描述，不许只写名字',
        ],
    },
    'voice': {
        'name': '配音 Agent',
        'role': '逐句合成语音，并把真实时长写回 dur',
        'stage': 'voice',
        'skill': 'voice',
        'model_kind': 'bailian_tts',
        'tools': tool_schema.tool_names('voice'),
        'outputs': ['语音文件', '每句真实时长'],
        'guardrails': [
            '合成后必须把真实时长写回 dur，否则节点节奏会错',
            '超过 600 字的台词先切句（provider 硬限制）',
        ],
    },
    'build': {
        'name': '装配 Agent',
        'role': '结构校验 + 素材完备性检查 + 导出',
        'stage': 'build',
        'skill': 'build',
        'model_kind': None,
        'tools': tool_schema.tool_names('build'),
        'outputs': ['校验报告', '可玩工程包'],
        'guardrails': [
            '有 error 不许导出，只允许 warning 放行',
            '导出前必须跑一次 scope=all 的结构校验',
        ],
    },
    # ★ 质检 Agent：流水线的最后一关，只在用户开了「收尾验证」时跑。
    #   刻意不让它碰任何写操作（工具只有 inspect_* 和 file_report）——
    #   检查者不能是修改者，否则它会顺手把问题「改」掉，报告就失去意义了。
    'review': {
        'name': '质检 Agent',
        'role': '成品交付前独立查一遍：结构、文本、素材、一致性、规模，给出判定和分数',
        'stage': 'review',
        'skill': 'review',
        'model_kind': None,
        'tools': tool_schema.tool_names('review'),
        'outputs': ['质检报告（verdict / score / 问题清单）'],
        'guardrails': [
            '只报事实，不猜；没查到的项不许写「应该没问题」',
            '不许修改任何节点，发现问题只写进报告',
            '每条问题必须带 code，能定位到节点的还要带 node_id',
            '没真跑过检查不许写报告',
        ],
    },
    # 界面 Agent 不挂在自动流水线上（PIPELINE_ORDER 里没有它）：
    # 界面是「用户给一句氛围词、想马上看到东西」的交互式活儿，
    # 不适合塞进 writer→art→voice→build 那条一次性跑完的流水线。
    'ui': {
        'name': '界面 Agent',
        'role': '按关键词从预设里挑一套界面风格，渲染启动页与游戏内界面并自检',
        'stage': 'ui',
        'skill': 'ui',
        'model_kind': 'llm',
        'tools': tool_schema.tool_names('ui'),
        'outputs': ['界面风格方案（选型理由）', '渲染好的界面 HTML', '界面自检报告'],
        'guardrails': [
            '不许从零画界面：必须先 list_ui_presets，再挑一套预设',
            '关键词一个预设都不沾时如实说，不许硬套气质不对的',
            '渲染完必须自己截图核对，不能只看工具返回 ok',
            '同一部作品的所有界面只能用同一套预设',
        ],
    },
}

PIPELINE_ORDER = ['writer', 'art', 'voice', 'build', 'review']

_SKELETON_SKILL = '06-skeleton.md'
_SKELETON_TOOLS = {'project_brief', 'write_skeleton', 'fill_segment',
                   'list_characters', 'upsert_character', 'validate_graph'}
_LEGACY_WRITER_GUARDRAILS = [
    '一次只写一章的一段（≤20 个节点）',
    '写之前先 get_chapter，避免 ref 冲突和剧情断裂',
    '每章收尾必须 finish_chapter 写摘要，后面章节只能看到摘要',
]


def skill_dir(agent):
    return SKILL_ROOT / AGENTS[agent]['skill']


def skill_files(agent):
    """返回该 agent 的 skill 分篇（SKILL.md 在前，其余按文件名排序）。"""
    root = skill_dir(agent)
    if not root.is_dir():
        return []
    head = [root / 'SKILL.md'] if (root / 'SKILL.md').is_file() else []
    rest = sorted(p for p in root.glob('*.md') if p.name != 'SKILL.md')
    return head + rest


def load_skill(agent, max_chars=200_000, *, writer_mode=None):
    """按写作模式选择手册，避免给模型相互冲突的章节和骨架规则。"""
    parts = []
    total = 0
    for path in skill_files(agent):
        if agent == 'writer' and writer_mode:
            is_skeleton_skill = path.name == _SKELETON_SKILL
            if is_skeleton_skill != (writer_mode == 'skeleton'):
                continue
        text = path.read_text(encoding='utf-8')
        block = f'\n\n===== {path.name} =====\n{text}'
        total += len(block)
        if total > max_chars:
            parts.append(f'\n\n[skill 超出 {max_chars} 字上限，后续分篇未加载]')
            break
        parts.append(block)
    return ''.join(parts).strip()


def system_prompt(agent, *, writer_mode='skeleton'):
    """给 LLM 用的系统提示：身份 + 职责 + 硬规则 + 工具清单。"""
    spec = AGENTS[agent]
    names = spec['tools']
    guardrails = spec['guardrails']
    if agent == 'writer':
        if writer_mode == 'legacy':
            names = [name for name in names if name not in ('write_skeleton', 'fill_segment')]
            guardrails = _LEGACY_WRITER_GUARDRAILS
        else:
            names = [name for name in names if name in _SKELETON_TOOLS]
    tools = '\n'.join(f'- {t}' for t in names)
    guard = '\n'.join(f'- {g}' for g in guardrails)
    return (
        f"你是「{spec['name']}」，职责：{spec['role']}。\n\n"
        f'## 你可以调用的工具\n{tools}\n\n'
        f'## 必须守住的红线\n{guard}\n\n'
        '## 工作手册\n'
        f'{load_skill(agent, writer_mode=writer_mode)}'
    )


def check():
    """自检：skill 文件齐全、工具名与 schema 一致、必备字段存在。返回问题列表。"""
    problems = []
    for name, spec in AGENTS.items():
        for key in ('name', 'role', 'stage', 'skill', 'tools', 'guardrails'):
            if not spec.get(key):
                problems.append(f'{name}: 缺字段 {key}')

        files = skill_files(name)
        if not files:
            problems.append(f'{name}: skill 目录不存在或缺 SKILL.md（{skill_dir(name)}）')
        elif files[0].name != 'SKILL.md':
            problems.append(f'{name}: 入口不是 SKILL.md')

        schema_tools = tool_schema.tool_names(name)
        if sorted(spec['tools']) != sorted(schema_tools):
            problems.append(
                f'{name}: 注册表工具 {spec["tools"]} 与 schema 工具 {schema_tools} 不一致')

        if not schema_tools:
            problems.append(f'{name}: 没有任何工具')
        for tool in tool_schema.TOOLS.get(name, []):
            fn = tool.get('function') or {}
            if not fn.get('name'):
                problems.append(f'{name}: 有工具缺 name')
            if not fn.get('description'):
                problems.append(f'{name}/{fn.get("name")}: 缺 description（模型会猜错用法）')

    for name in PIPELINE_ORDER:
        if name not in AGENTS:
            problems.append(f'流水线阶段 {name} 没有对应 agent')
    return problems


if __name__ == '__main__':
    issues = check()
    if issues:
        print(f'✗ {len(issues)} 处问题')
        for i in issues:
            print('  -', i)
        raise SystemExit(1)
    total = sum(len(v['tools']) for v in AGENTS.values())
    print(f'✓ {len(AGENTS)} 个 agent，{total} 个工具，skill 齐全')
    for name in list(PIPELINE_ORDER) + [n for n in AGENTS if n not in PIPELINE_ORDER]:
        tag = '流水线' if name in PIPELINE_ORDER else '按需'
        print(f"  {name:8s} {AGENTS[name]['name']:12s} skill={len(skill_files(name))} 篇 "
              f"tools={len(AGENTS[name]['tools'])}")
