# -*- coding: utf-8 -*-
"""内置 agent 的 skill 体检：检查「文档说的」和「注册表给的」有没有对不上。

为什么要有这个
--------------
skill 是直接拼进系统提示的文本。它和代码一旦脱节，后果是**模型去调一个
它根本没有的工具**，或者以为自己在做 A 实际在做 B —— 而这类错误不会抛异常，
只会表现为「模型怎么老是跑偏」，极难排查。

三条检查（都是机器能判死的）：

1. **工具名串台**：SKILL 里提到的工具，必须在该 agent 的 tools 白名单里。
   把所有 agent 的工具名并集当词表，扫文本 —— 不在词表里的词（bg_key /
   end_kind 这些）自动跳过，所以不会误报。
2. **工具没写进文档**：白名单里有、但 SKILL 里一次没提，模型不知道它存在。
3. **skill 文件缺失**：注册表指向的目录 / SKILL.md 不存在。

用法（在项目根跑）：
    app\\.venv\\Scripts\\python.exe tools\\_audit_skills.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents import registry, tool_schema  # noqa: E402


def all_tool_names():
    """所有 agent 工具名的并集。用它当词表，非工具词自然被过滤掉。"""
    names = set()
    for agent in registry.AGENTS:
        names.update(tool_schema.tool_names(agent))
    return names


def read_skill_text(agent):
    """把某个 agent 的全部 skill 分篇拼成一段文本。"""
    paths = registry.skill_files(agent)
    return '\n'.join(path.read_text(encoding='utf-8') for path in paths), [p.name for p in paths]


# 出现在工具名**同一行**前面的否定语，说明这句话是在讲「这个工具不能用」，
# 不是在教模型去调它。不加这层，文档里写「这里没有 list_characters」会被误判成串台。
NEGATIONS = ('没有', '不许', '不要', '不能用', '不在', '调不了', '不存在', '禁用')


def is_negated(line, name):
    """判断工具名 name 在这一行里是不是被否定语修饰着。

    窗口取 40 字符：文档里常写成「没有 `list_characters` / `upsert_character`」，
    第二个名字离否定语有二三十个字符（中间还夹着反引号和斜杠），窗口太小会漏判。
    """
    index = line.find(name)
    if index < 0:
        return False
    window = line[max(0, index - 40):index]
    return any(word in window for word in NEGATIONS)


def audit():
    vocabulary = all_tool_names()
    problems = []
    rows = []
    for agent, meta in registry.AGENTS.items():
        allowed = set(tool_schema.tool_names(agent))
        directory = registry.skill_dir(agent)
        if not directory.is_dir():
            problems.append((agent, 'MISSING_DIR', f'skill 目录不存在：{directory}'))
            continue
        if not (directory / 'SKILL.md').is_file():
            problems.append((agent, 'MISSING_HEAD', '缺少 SKILL.md 入口'))
        text, files = read_skill_text(agent)
        # 逐行扫：同一行里被否定语修饰的工具名不算「文档让模型去调」。
        mentioned = set()
        for line in text.splitlines():
            for name in vocabulary:
                if re.search(r'\b' + name + r'\b', line) and not is_negated(line, name):
                    mentioned.add(name)
        foreign = sorted(mentioned - allowed)
        silent = sorted(allowed - mentioned)
        for name in foreign:
            owners = [a for a in registry.AGENTS if name in tool_schema.tool_names(a)]
            problems.append((agent, 'FOREIGN_TOOL',
                             f'文档提到 {name}，但它属于 {",".join(owners)}，本 agent 调不了'))
        rows.append(dict(agent=agent, files=len(files), chars=len(text),
                         tools=len(allowed), mentioned=len(mentioned & allowed),
                         foreign=len(foreign), silent=len(silent),
                         silent_names=silent))
    return rows, problems


def main():
    rows, problems = audit()
    print('%-8s %5s %7s %5s %8s %7s %6s' % ('agent', 'files', 'chars', 'tools', 'covered', 'foreign', 'silent'))
    for row in rows:
        print('%-8s %5d %7d %5d %8d %7d %6d' % (
            row['agent'], row['files'], row['chars'], row['tools'],
            row['mentioned'], row['foreign'], row['silent']))
    print()
    for row in rows:
        if row['silent_names']:
            print(f"[{row['agent']}] 白名单里有但文档没提：{'、'.join(row['silent_names'])}")
    print()
    if problems:
        print('=== 问题 %d 条 ===' % len(problems))
        for agent, code, message in problems:
            print(f'  {agent:8s} {code:14s} {message}')
        return 1
    print('=== 没有硬问题 ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
