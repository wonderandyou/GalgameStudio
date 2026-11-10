"""诊断 DASHSCOPE_API_KEY 那一行的值形态，不打印值本身。"""
import pathlib
import re

p = pathlib.Path.home() / '.dsh' / '.credentials.yaml'
lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
for i, ln in enumerate(lines, 1):
    if 'DASHSCOPE' not in ln:
        continue
    print('行号', i, '原始长度', len(ln), 'repr(尾部3)', repr(ln[-3:]))
    m = re.match(r'^(\s*)([A-Za-z0-9_]+)\s*:\s*(.*)$', ln)
    if not m:
        print('  不匹配键值格式')
        continue
    val = m.group(3)
    print('  缩进', len(m.group(1)), '键', m.group(2))
    print('  值长度', len(val), '首字符类别', '引号' if val[:1] in '"\' ' else '其他')
    print('  值的前2字符', repr(val[:2]), '后1字符', repr(val[-1:]))
    core = val.strip().strip('"\'')
    print('  去引号后长度', len(core),
          '含空格', ' ' in core,
          '含等号', '=' in core,
          '含点', '.' in core,
          '含注释符', '#' in core)
    print('  字符集是否 [A-Za-z0-9_-]:', bool(re.fullmatch(r'[A-Za-z0-9_\-]+', core)))
