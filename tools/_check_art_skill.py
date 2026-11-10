# -*- coding: utf-8 -*-
"""校验 art 的 skill 体系：7 篇是否都进 system prompt、文档引用的工具是否真实存在。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents import registry, tool_schema   # noqa: E402

files = registry.skill_files('art')
print('篇数:', len(files))
for path in files:
    print('  -', path.name, path.stat().st_size, 'bytes')

text = registry.load_skill('art')
print('拼接字符数:', len(text))

missing_in_prompt = [p.name for p in files if f'===== {p.name} =====' not in text]
print('未进 system prompt:', missing_in_prompt)

names = set(tool_schema.tool_names('art'))
print('art 工具:', sorted(names))

# 文档里用反引号包起来的标识符，凡是像工具名的都得真实存在
mentioned = set(re.findall(r'`([a-z][a-z_]{2,})`', text))
not_tools = {
    'bg_key', 'look', 'place', 'time', 'weather', 'palette', 'medium', 'light',
    'typeface', 'negative', 'overwrite', 'surface', 'neutral', 'stand', 'target',
    'brief', 'reused', 'title', 'dialog', 'choice', 'save', 'settings', 'hud',
    'typography', 'fx', 'agent_state', 'ui', 'ok', 'palette', 'true', 'false',
    'mock',  # provider 的降级标记值，不是工具名
}
suspicious = sorted(mentioned - names - not_tools)
print('文档提到但未定义的工具:', suspicious)

prompt = registry.system_prompt('art')
print('system_prompt 字符数:', len(prompt))
print('含 set_style:', 'set_style' in prompt)
print('含 draft_prompt:', 'draft_prompt' in prompt)
print('含风格圣经:', '风格圣经' in prompt)

ok = (len(files) == 7 and not missing_in_prompt and not suspicious)
print('=' * 50)
print('结果:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
