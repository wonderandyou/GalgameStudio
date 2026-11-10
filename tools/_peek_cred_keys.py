"""只打印凭据文件的键名结构，绝不打印任何值。"""
import re
import pathlib

p = pathlib.Path.home() / '.dsh' / '.credentials.yaml'
print('文件:', p, '存在:', p.exists())
lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
print('总行数:', len(lines))
print('-' * 40)
for i, ln in enumerate(lines, 1):
    if not ln.strip() or ln.strip().startswith('#'):
        continue
    m = re.match(r'^(\s*)(-\s+)?([A-Za-z0-9_.\-]+)\s*:(.*)$', ln)
    if m:
        indent = len(m.group(1))
        rest = m.group(4).strip()
        shape = '空' if not rest else ('内联值' if not rest.startswith('|') and not rest.startswith('>') else '块')
        print('%4d %s%s   [%s]' % (i, '  ' * (indent // 2), m.group(3), shape))
    else:
        print('%4d <非键值行 len=%d>' % (i, len(ln)))
