"""导出真实可打开的占位素材游戏，不把路径存在当作导出成功。"""
import base64
import json
import re
import shutil
from html import escape
from pathlib import Path
from ..providers.base import ProviderError
from ..store import atomic_bytes, atomic_json
from ..mock_data import png, silent_wav
from .contracts import stage_report
from .graph import public_graph, safe_asset


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
        """给导出的图片生成稳定的文件名。"""
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
    project = graph.get('project') or {}
    atomic_bytes(game / 'script.rpy', '\n'.join(lines).encode('utf-8'))
    atomic_bytes(game / 'options.rpy',
                 (f'define config.name = {json.dumps(_display_title(project), ensure_ascii=False)}\n'
                  f'define config.version = "0.1.0"\n'
                  f'define config.screen_width = {int(width)}\n'
                  f'define config.screen_height = {int(height)}\n').encode('utf-8'))
    _write_icon(project, game)
    return output


def _display_title(project):
    """成品的显示名：优先用「成品设置」里填的游戏名，其次项目名。

    以前这里写死 "Galgame Studio Mock"，导出出来的游戏名永远是那串 ——
    现在由用户在画布工具栏的「成品设置」里定。
    """
    project = project or {}
    return str(project.get('game_title') or project.get('name') or '奇迹工坊')[:60]


def _write_icon(project, folder):
    """把「成品设置」里的图标（data URL）解码写成文件；没有就跳过。"""
    raw = str((project or {}).get('icon') or '')
    if not raw.startswith('data:image/'):
        return None
    try:
        head, payload = raw.split(',', 1)
        data = base64.b64decode(payload)
        if len(data) > 512 * 1024:
            return None
        suffix = '.png' if 'png' in head else ('.ico' if 'ico' in head else '.jpg')
        target = folder / ('icon' + suffix)
        atomic_bytes(target, data)
        return target
    except Exception:                              # noqa: BLE001
        return None


def _pick_preset(graph, preset=None):
    """选界面预设：导参 > graph.style / project.ui_preset / project.style（id 或关键词）> 默认第一套。"""
    from ..ui_presets import all_presets, match
    presets = {p['id']: p for p in all_presets()}
    project = graph.get('project') or {}
    style = graph.get('style')
    if isinstance(style, dict):
        style = style.get('preset') or style.get('id') or style.get('ui_preset')
    for want in (preset, style, project.get('ui_preset'), project.get('style')):
        if not want:
            continue
        want = str(want)
        if want in presets:
            return presets[want]
        hits = match(want, limit=1)
        if hits:
            return presets[hits[0]['id']]
    return all_presets()[0]


def _title_page(graph, preset=None):
    """用 app.ui_presets 的四套预设之一渲染标题页；菜单「开始游戏/继续游戏」→ play.html。"""
    from ..ui_presets import render
    chosen = _pick_preset(graph, preset)
    project = graph.get('project') or {}
    title = _display_title(project)
    nodes = graph.get('nodes') or []
    bg = next(((n.get('bg') or {}).get('asset') for n in nodes if (n.get('bg') or {}).get('asset')), '')
    poses = [c.get('pose') for n in nodes for c in (n.get('chars') or []) if c.get('pose')]
    poses = list(dict.fromkeys(poses))
    html = render(chosen, 'title', title=title,
                  subtitle=str(project.get('subtitle') or 'WHALE FALL WORKSHOP'),
                  bg=bg or '', char_left=poses[0] if poses else '',
                  char_right=poses[1] if len(poses) > 1 else '')
    hook = ('<script>document.querySelectorAll("a").forEach(function(a){'
            'var t=a.textContent;if(/开始|继续|START|Start|^0?1/.test(t)||a===document.querySelector("a"))'
            '{a.href="play.html"}});document.documentElement.dataset.preset=' +
            json.dumps(chosen['id']) + ';</script>')
    marker = f'<!-- ui_preset: {chosen["id"]} -->'
    return (html.replace('</body>', hook + '</body>') if '</body>' in html else html + hook) \
        .replace('<head>', '<head>' + marker, 1) if '<head>' in html else marker + html + hook


def export_web(root, graph, preset=None):
    """导出 web 版工程。"""
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
<script>const graph=GRAPH;let node,index=0;const flags={};const $=id=>document.getElementById(id);
function reset(){for(const k of Object.keys(flags))delete flags[k];for(const [k,f] of Object.entries(graph.flags||{}))flags[k]=f&&f.default}
function pass(c){if(!c.condition)return true;const neg=c.condition[0]==='!';const v=!!flags[neg?c.condition.slice(1):c.condition];return neg?!v:v}
function apply(c){for(const [k,v] of Object.entries(c.effects||{}))flags[k]=typeof v==='number'?(Number(flags[k])||0)+v:v}
function show(id){node=graph.nodes.find(n=>n.id===id);if(!node){$('line').textContent='（这个分支还没写内容）';$('choices').replaceChildren();return}index=0;$('title').textContent=node.title;$('bg').src=node.bg&&node.bg.asset||'';$('pose').src=(node.chars&&node.chars[0]&&node.chars[0].pose)||'';render()}
function button(text,fn){const b=document.createElement('button');b.textContent=text;b.onclick=fn;$('choices').append(b)}
function render(){$('line').textContent=node.lines[index].text;$('choices').replaceChildren();$('voice').pause();if(node.lines[index].voice){$('voice').src=node.lines[index].voice;$('voice').play().catch(()=>{})}else{$('voice').removeAttribute('src')}
if(index<node.lines.length-1)button('下一句',()=>{index++;render()});else if(node.type==='ending'){button('重新开始',()=>{reset();show(START)})}else if(node.choices&&node.choices.length){const all=node.choices.slice(0,4),shown=all.filter(pass);for(const c of (shown.length?shown:all))button(c.label,()=>{apply(c);show(c.to)})}else button('重新开始',()=>{reset();show(START)})}reset();show(START);</script>'''
    page = page.replace('TITLE', name).replace('START', json.dumps(start)).replace('GRAPH', data)
    # 播放器挪到 play.html；index.html 是按预设渲染的标题页，「开始游戏」跳 play.html
    atomic_bytes(output / 'play.html', page.encode('utf-8'))
    # 「成品设置」里选的图标：解码写进 web 目录，并挂到 index.html 的 favicon 上
    icon_file = _write_icon(graph.get('project') or {}, output)
    index_html = _title_page(graph, preset)
    if icon_file is not None:
        link = '<link rel="icon" href="' + icon_file.name + '">'
        index_html = (index_html.replace('</head>', link + '</head>')
                      if '</head>' in index_html else link + index_html)
    atomic_bytes(output / 'index.html', index_html.encode('utf-8'))
    return output


def export_game(root, graph, format, preset=None):
    """导出可玩工程，另存生成阶段的交付和降级情况。"""
    report = stage_report(graph)
    failed = [stage for stage in ('writer', 'art', 'voice', 'build')
              if report['stages'].get(stage) == 'failed']
    if failed:
        raise ProviderError('GRAPH_NOT_READY', f'生成阶段未通过验收：{", ".join(failed)}',
                            'export.validate', 409)
    public = public_graph(graph)
    if format == 'renpy':
        result = output = export_renpy(root, public)
    elif format == 'unity':
        # 延迟导入：只有真要打包 Unity 时才需要这份依赖
        from .export_unity import export_unity_and_build
        result = export_unity_and_build(root, public, timeout=1800)
        output = Path(result['out']).parent
    else:
        result = output = export_web(root, public, preset=preset)
    atomic_json(output / 'export_report.json', dict(format=format, **report))
    return result
