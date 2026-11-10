"""导出真实可打开的占位素材游戏，不把路径存在当作导出成功。"""
import json
import re
import shutil
from html import escape
from pathlib import Path
from ..providers.base import ProviderError
from ..store import atomic_bytes
from ..mock_data import png, silent_wav
from .graph import safe_asset


def _ident(value, fallback='x'):
    """把任意字符串洗成 Ren'Py 合法的 ASCII 标识符/图片名。"""
    name = re.sub(r'[^0-9A-Za-z_]+', '_', str(value)).strip('_').lower()
    if not name:
        name = fallback
    if name[0].isdigit():
        name = '_' + name
    return name


_MISSING_ASSETS = []


def ensure_assets(root, graph):
    """导出前把「图里写着有、磁盘上却没有」的素材补成占位，返回补了哪些。

    降级路径（没配 key、art/voice 阶段没跑）会留下这种图。
    validate_graph 本身是严格校验，会先一步把导出拦死；所以必须在校验之前补。
    """
    filled = []
    for node in graph.get('nodes') or []:
        values = [(node.get('bg') or {}).get('asset')]
        values += [c.get('pose') for c in node.get('chars') or []]
        for value in values:
            if not value:
                continue
            try:
                target = safe_asset(root, value)
            except ValueError:
                continue
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_bytes(target, png(64, 36, (35, 50, 75, 255)))
                filled.append(value)
        for line in node.get('lines') or []:
            value = line.get('voice')
            if not value:
                continue
            try:
                target = safe_asset(root, value)
            except ValueError:
                continue
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                seconds = max(0.5, float(line.get('dur') or 1))
                atomic_bytes(target, silent_wav(seconds))
                filled.append(value)
    return filled


def take_missing_assets():
    """取走「补过占位图」的资产清单（每次导出后由调用方读走并提示用户）。"""
    out = list(_MISSING_ASSETS)
    _MISSING_ASSETS.clear()
    return out


def _png_source(root, value):
    """把 graph 里的 POSIX 相对路径解析成项目内 PNG，并防止路径越界。

    契约 §3.6 规定 asset 是 .png；非 PNG 无法保证 Ren'Py 与 tools/verify_rpy.py
    都认得出，所以这里宁可明确报错，也不静默导出一个打不开的工程。

    但「文件不存在」不该让整份导出失败：降级路径（没配 key、没跑 art 阶段）
    会让图里写着 assets/bg/xxx.png 而磁盘上没有。这时补一张占位图，
    并把这条记进 take_missing_assets()，让界面能告诉用户哪张是占位。
    """
    source = safe_asset(root, value)
    if source.suffix.lower() != '.png':
        raise ProviderError('EXPORT_ASSET_UNSUPPORTED',
                            f"导出 Ren'Py 只支持 PNG 素材，收到 {value}", 'export.renpy', 422)
    if not source.is_file():
        source.parent.mkdir(parents=True, exist_ok=True)
        atomic_bytes(source, png(64, 36, (35, 50, 75, 255)))
        _MISSING_ASSETS.append(value)
    return source


def _escape(text):
    """禁止用户文本被 Ren'Py 当作插值或文本标签执行。"""
    return json.dumps(text.replace('[', '[[').replace('{', '{{'), ensure_ascii=False)


def export_renpy(root, graph):
    """生成可被 Ren'Py 直接 Launch 的工程。

    契约要求 `scene bg X` 能对上 `images/bg/X.png` 或 `images/X.png`，
    所以背景/立绘复制进 game/images/ 并用显式 image 语句定义；
    语音留在 game/assets/ 下，voice 路径保持 graph 里的相对路径。
    """
    output = root / 'export' / 'renpy'
    game = output / 'game'
    game.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / 'assets', game / 'assets', dirs_exist_ok=True)

    # 资产 → Ren'Py 图片名；同一文件共用，不同文件撞名时加序号（按 bg/char 分别命名空间）。
    names = {}
    used = {'bg': set(), 'char': set()}

    def image_name(asset, kind):
        key = (kind, asset)
        if key in names:
            return names[key]
        base = _ident(Path(asset).stem, kind)
        taken = used[kind]
        name, index = base, 2
        while name in taken:
            name = f'{base}_{index}'
            index += 1
        taken.add(name)
        names[key] = name
        return name

    def register(asset, kind):
        """登记并复制到 game/images/<kind>/<name>.png。"""
        name = image_name(asset, kind)
        target = game / 'images' / kind / (name + '.png')
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_png_source(root, asset), target)
        return name

    for node in graph['nodes']:
        register(node['bg']['asset'], 'bg')
        for character in node['chars']:
            register(character['pose'], 'char')

    # 角色变量名必须唯一且是合法标识符。
    char_ident = {}
    used_ident = set()
    for character in graph['characters']:
        base = _ident(character['id'], 'char')
        ident, index = base, 2
        while ident in used_ident:
            ident = f'{base}_{index}'
            index += 1
        used_ident.add(ident)
        char_ident[character['id']] = ident

    lines = ["# 自动生成：M2 mock 工程（纯色 PNG + 静音 WAV，仅用于协议联调）", ""]
    for character in graph['characters']:
        ident = char_ident[character['id']]
        lines.append(f'define {ident} = Character({_escape(character["name"])}, '
                     f'color={_escape(character["color"])})')
    if graph['characters']:
        lines.append("")
    for (kind, asset), name in names.items():
        lines.append(f'image {kind} {name} = "images/{kind}/{name}.png"')
    if names:
        lines.append("")

    start_id = next(n['id'] for n in graph['nodes'] if n['type'] == 'start')
    lines += ['label start:', f'    jump node_{_ident(start_id, "node")}', '']

    for node in graph['nodes']:
        lines.append(f'label node_{_ident(node["id"], "node")}:')
        lines.append(f'    scene bg {names[("bg", node["bg"]["asset"])]}')
        for character in node['chars']:
            pos = character.get('pos') if character.get('pos') in ('left', 'center', 'right') else 'center'
            lines.append(f'    show char {names[("char", character["pose"])]} at {pos}')
        for line in node['lines']:
            if line.get('voice'):
                lines.append(f'    voice {_escape(line["voice"])}')
            who = char_ident.get(line.get('who'))
            text = _escape(line['text'])
            lines.append(f'    {who} {text}' if who else f'    {text}')
        if node['type'] == 'ending':
            lines.append('    return')
        elif node['type'] == 'choice':
            lines.append('    menu:')
            for choice in node['choices'] or []:
                lines.append('        ' + _escape(choice['label']) + ':')
                lines.append(f'            jump node_{_ident(choice["to"], "node")}')
        else:
            lines.append(f'    jump node_{_ident(node["choices"][0]["to"], "node")}')
        lines.append("")

    width, height = graph['project'].get('resolution') or [1280, 720]
    atomic_bytes(game / 'script.rpy', '\n'.join(lines).encode('utf-8'))
    atomic_bytes(game / 'options.rpy',
                 (f'define config.name = "Galgame Studio Mock"\n'
                  f'define config.version = "0.1.0"\n'
                  f'define config.screen_width = {int(width)}\n'
                  f'define config.screen_height = {int(height)}\n').encode('utf-8'))
    return output


def export_web(root, graph):
    output = root / 'export' / 'web'
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / 'assets', output / 'assets', dirs_exist_ok=True)
    # 内嵌 JSON，file:// 打开时不需要 fetch 本地 graph.json。
    data = json.dumps(graph, ensure_ascii=False).replace('<', '\\u003c')
    name = escape(str((graph.get('project') or {}).get('name') or 'Galgame'))[:60]
    # 起点不能写死 n1：换个项目/项目被重排过就会一打开就空白。
    nodes = graph.get('nodes') or []
    start = next((n.get('id') for n in nodes if n.get('type') == 'start'), None)
    if not start:
        start = nodes[0].get('id') if nodes else ''
    page = '''<!doctype html><meta charset="utf-8"><title>TITLE</title>
<style>body{background:#172132;color:white;font:20px/1.6 "Microsoft YaHei",sans-serif;margin:0;padding:6vh 8%}#bg{position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-2;opacity:.55}#pose{position:fixed;right:8%;bottom:0;height:70vh;z-index:-1}#title{font-size:15px;opacity:.6;margin:0 0 8px}p#line{font-size:26px;min-height:3em;background:rgba(0,0,0,.35);padding:20px;border-radius:12px}button{font:16px "Microsoft YaHei",sans-serif;padding:12px 22px;margin:8px 10px 0 0;border:0;border-radius:10px;background:#2b3a55;color:#fff;cursor:pointer}button:hover{background:#3b4f72}audio{margin-top:20px;width:320px;opacity:.7}</style>
<h2 id="title"></h2><p id="line"></p><div id="choices"></div><audio id="voice"></audio><img id="bg" alt=""><img id="pose" alt="">
<script>const graph=GRAPH;let node,index=0;const $=id=>document.getElementById(id);
function show(id){node=graph.nodes.find(n=>n.id===id);if(!node){$('line').textContent='（这个分支还没写内容）';$('choices').replaceChildren();return}index=0;$('title').textContent=node.title;$('bg').src=node.bg&&node.bg.asset||'';$('pose').src=(node.chars&&node.chars[0]&&node.chars[0].pose)||'';render()}
function button(text,fn){const b=document.createElement('button');b.textContent=text;b.onclick=fn;$('choices').append(b)}
function render(){$('line').textContent=node.lines[index].text;$('choices').replaceChildren();$('voice').pause();if(node.lines[index].voice){$('voice').src=node.lines[index].voice;$('voice').play().catch(()=>{})}else{$('voice').removeAttribute('src')}
if(index<node.lines.length-1)button('下一句',()=>{index++;render()});else if(node.type==='ending'){button('重新开始',()=>show(START))}else if(node.choices&&node.choices.length)for(const c of node.choices)button(c.label,()=>show(c.to));else button('重新开始',()=>show(START))}show(START);</script>'''
    page = page.replace('TITLE', name).replace('START', json.dumps(start)).replace('GRAPH', data)
    atomic_bytes(output / 'index.html', page.encode('utf-8'))
    return output


def export_game(root, graph, format):
    if format == 'renpy':
        return export_renpy(root, graph)
    return export_web(root, graph)
