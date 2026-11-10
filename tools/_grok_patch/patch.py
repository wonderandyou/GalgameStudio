# 在用户机器上运行：把 eu_append 插到 export_unity.py 的 __main__ 之前；替换 export_web
from pathlib import Path
S = Path(r'D:\软件\GalgameStudio')
HERE = Path(__file__).parent
eu = S / 'app/engine/export_unity.py'
t = eu.read_text('utf-8')
if 'def export_unity_and_build' not in t:
    add = (HERE / 'eu_append.py').read_text('utf-8')
    i = t.index("\n\nif __name__ == '__main__':")
    t = t[:i] + add.rstrip('\n') + '\n' + t[i:]
    eu.write_text(t, 'utf-8')
m = S / 'app/engine/export_mock.py'
t = m.read_text('utf-8')
if '_pick_preset' not in t:
    a = t.index('def export_web(root, graph):')
    b = t.index('def export_game(root, graph, format):')
    new = (HERE / 'web_new.py').read_text('utf-8')
    old = t[a:b]
    old = old.replace('def export_web(root, graph):', 'def export_web(root, graph, preset=None):', 1)
    old = old.replace('''    page = page.replace('TITLE', name).replace('START', json.dumps(start)).replace('GRAPH', data)
    atomic_bytes(output / 'index.html', page.encode('utf-8'))
    return output''', '''    page = page.replace('TITLE', name).replace('START', json.dumps(start)).replace('GRAPH', data)
    # 播放器挪到 play.html；index.html 是按预设渲染的标题页，「开始游戏」跳 play.html
    atomic_bytes(output / 'play.html', page.encode('utf-8'))
    atomic_bytes(output / 'index.html', _title_page(graph, preset).encode('utf-8'))
    return output''')
    assert 'play.html' in old
    t = t[:a] + new + '\n\n' + old + t[b:]
    m.write_text(t, 'utf-8')
print('patched')
