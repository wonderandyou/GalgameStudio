"""导出真实可打开的占位素材游戏，不把路径存在当作导出成功。"""
import json
import shutil
from pathlib import Path
from ..store import atomic_bytes

def export_game(root, graph, format):
    output = root / 'export' / format
    output.mkdir(parents=True, exist_ok=True)
    if format == 'renpy':
        game = output / 'game'
        game.mkdir(exist_ok=True)
        shutil.copytree(root / 'assets', game / 'assets', dirs_exist_ok=True)
        def quote(text):
            # 禁止用户文本被 Ren'Py 当作插值或文本标签执行。
            return json.dumps(text.replace('[', '[[').replace('{', '{{'), ensure_ascii=False)
        lines = ['# M2 mock 工程：占位图片与静音语音', 'label start:', '    jump node_n1', '']
        for node in graph['nodes']:
            lines += [f'label node_{node["id"]}:', f'    scene expression {quote(node["bg"]["asset"])}']
            for character in node['chars']:
                lines += [f'    show expression {quote(character["pose"])} at center']
            for line in node['lines']:
                if line['voice']:
                    lines += [f'    voice {quote(line["voice"])}']
                lines += ['    ' + quote(line['text'])]
            if node['type'] == 'ending':
                lines += ['    return']
            elif node['type'] == 'choice':
                lines += ['    menu:']
                for choice in node['choices']:
                    lines += ['        ' + quote(choice['label']) + ':', f'            jump node_{choice["to"]}']
            else:
                lines += [f'    jump node_{node["choices"][0]["to"]}']
            lines += ['']
        atomic_bytes(game / 'script.rpy', '\n'.join(lines).encode('utf-8'))
        atomic_bytes(game / 'options.rpy', b'define config.name = "Galgame Studio Mock"\ndefine config.version = "0.1.0"\ndefine config.screen_width = 1280\ndefine config.screen_height = 720\n')
    else:
        shutil.copytree(root / 'assets', output / 'assets', dirs_exist_ok=True)
        # 内嵌 JSON，file:// 打开时不需要 fetch 本地 graph.json。
        data = json.dumps(graph, ensure_ascii=False).replace('<', '\\u003c')
        page = '''<!doctype html><meta charset="utf-8"><title>Galgame Studio Mock</title>
<style>body{background:#172132;color:white;font:24px sans-serif;margin:5%}#bg{width:50%;image-rendering:pixelated}button{padding:16px;margin:8px}#pose{height:200px}</style>
<h1>M2 Mock 试玩</h1><img id="bg"><img id="pose"><h2 id="title"></h2><p id="line"></p><div id="choices"></div><audio id="voice" controls></audio>
<script>const graph=GRAPH;let node,index=0;const $=id=>document.getElementById(id);
function show(id){node=graph.nodes.find(n=>n.id===id);index=0;$('title').textContent=node.title;$('bg').src=node.bg.asset;$('pose').src=node.chars[0]?.pose||'';render()}
function button(text,fn){const b=document.createElement('button');b.textContent=text;b.onclick=fn;$('choices').append(b)}
function render(){$('line').textContent=node.lines[index].text;$('choices').replaceChildren();$('voice').pause();if(node.lines[index].voice){$('voice').src=node.lines[index].voice;$('voice').play().catch(()=>{})}else{$('voice').removeAttribute('src')}
if(index<node.lines.length-1)button('下一句',()=>{index++;render()});else if(node.type==='ending')button('重新开始',()=>show('n1'));else for(const c of node.choices)button(c.label,()=>show(c.to))}show('n1');</script>'''.replace('GRAPH', data)
        atomic_bytes(output / 'index.html', page.encode('utf-8'))
    return output
