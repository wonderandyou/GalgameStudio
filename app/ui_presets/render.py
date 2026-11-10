"""把界面预设渲染成可直接打开的 HTML。

设计
----
* 预设只给**参数**（配色、字号、排列），CSS 骨架按预设 id 分派 ——
  四套的差异是结构级的（竖排/横排/斜切/居中），硬参数化反而更难维护。
* `title` 用完整模板；其余五块皮（dialog/choice/save/settings/hud）
  用 `surfaces` 里的 token 派生，保证「同一套预设换界面不换视觉语言」。
* 用 `string.Template` 而不是 f-string：CSS 里全是花括号，f-string 会被吃掉。

用法
----
    from app.ui_presets import get, render
    html = render(get('ink-classic'), 'title', title='奇迹工坊')
    Path('out.html').write_text(html, encoding='utf-8')
"""
from pathlib import Path
from string import Template

FONTS = {
    'brush': '"KaiTi","STKaiti","楷体",serif',
    'sans': '"Microsoft YaHei","PingFang SC",sans-serif',
    'serif': '"SimSun","宋体",serif',
    'mono': 'Consolas,"Courier New",monospace',
}

SCREENS = ('title', 'dialog', 'choice', 'save', 'settings', 'hud')

DEFAULT_MENU = ('开始游戏', '继续游戏', '读取存档', '设置')

# 冷色剧场的菜单是「两字一词」的冷峻写法，标准四项有固定缩写，
# 不能粗暴截前两个字（「读取存档」→「读取」读起来是断的）
SHORT_MENU = {
    '开始游戏': '开始', '继续游戏': '继续', '读取存档': '读档',
    '设置': '设置', '退出游戏': '退出', '画廊': '画廊', '回想': '回想',
}


def _font(key):
    """按预设字体生成 CSS font-family 串。"""
    return FONTS.get(str(key or 'sans'), FONTS['sans'])


def _css_path(value):
    """把图片路径变成浏览器能直接吃的写法。"""
    text = str(value or '').strip()
    if not text:
        return ''
    if text.startswith(('file:', 'http:', 'https:', 'data:')):
        return text
    path = Path(text)
    if path.is_absolute():
        return 'file:///' + str(path).replace('\\', '/')
    return text.replace('\\', '/')


def _esc(text):
    """HTML 转义。"""
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


# ---------------------------------------------------------------- 公共外壳
_BASE = Template(r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>$page_title</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:$bg}
body{font-family:$body_font;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.stage{position:relative;width:100vw;height:100vh;overflow:hidden;user-select:none}
.ver{position:absolute;font-size:12px;letter-spacing:.14em;opacity:.55;font-family:Consolas,monospace}
.corner{position:absolute;display:flex;gap:16px;align-items:center}
.corner button{background:none;border:0;cursor:pointer;font-size:19px;line-height:1;
  opacity:.62;transition:opacity .18s,transform .18s;padding:4px}
.corner button:hover{opacity:1;transform:scale(1.12)}
$extra_css
</style></head><body><div class="stage">$body</div></body></html>
""")


def _corner(color):
    """生成圆角 CSS。"""
    return (f'<div class="corner" style="right:4.4%;bottom:4.2%">'
            f'<button style="color:{color}" title="设置">⚙</button>'
            f'<button style="color:{color}" title="退出">✕</button></div>')


def _ver(text, style):
    """版本号，拼静态资源 URL 时防盗缓存用。"""
    return f'<div class="ver" style="{style}">{_esc(text)}</div>'


# ---------------------------------------------------------------- 四套 title
_TITLE_CSS = {
    'ink-classic': Template(r"""
.v1{background:$bg}
.v1 .paper{position:absolute;inset:0;
  background:radial-gradient(120% 80% at 50% 0%,$panel 0%,$bg 55%,#ddd3bd 100%),
    repeating-linear-gradient(90deg,rgba(120,100,70,.035) 0 1px,transparent 1px 3px)}
.v1 .paper::after{content:"";position:absolute;inset:0;opacity:.5;
  background-image:radial-gradient(rgba(90,75,50,.10) .5px,transparent .5px);background-size:3px 3px}
.v1 .ink{position:absolute;top:0;bottom:0;width:34%;pointer-events:none;
  background:radial-gradient(60% 70% at 50% 60%,rgba(38,44,40,.20),transparent 70%);filter:blur(18px)}
.v1 .ink.l{left:-6%} .v1 .ink.r{right:-6%}
.v1 .char{position:absolute;bottom:0;height:58%;object-fit:contain;
  mix-blend-mode:multiply;opacity:$char_opacity;
  filter:contrast(1.04) saturate(.78) brightness(1.05);
  -webkit-mask-image:linear-gradient(to top,transparent 0%,#000 30%,#000 100%);
  mask-image:linear-gradient(to top,transparent 0%,#000 30%,#000 100%)}
.v1 .char.l{left:1.4%} .v1 .char.r{right:1.4%;transform:scaleX(-1);opacity:.60}
.v1 .logo{position:absolute;left:50%;top:6.5%;transform:translateX(-50%) skewX($skew);
  display:flex;flex-direction:column;align-items:center;gap:10px}
.v1 .cn{writing-mode:vertical-rl;text-orientation:upright;
  font-family:$title_font;font-weight:$title_weight;font-size:${title_size}px;
  line-height:1.04;letter-spacing:$letter_spacing;color:$fg;
  text-shadow:0 3px 0 rgba(255,255,255,.5),0 6px 22px rgba(60,50,30,.22)}
.v1 .seal{margin-top:14px;width:38px;padding:10px 0;border-radius:3px;background:$accent;
  color:$bg;font-family:$title_font;font-size:16px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
  writing-mode:vertical-rl;text-orientation:upright;white-space:nowrap;
  letter-spacing:.14em;line-height:1;transform:skewX(4deg);
  box-shadow:0 2px 8px rgba(150,50,40,.3)}
.v1 .menu{position:absolute;left:25%;top:30%;display:flex;
  flex-direction:row-reverse;align-items:flex-start;gap:44px}
.v1 .menu a{writing-mode:vertical-rl;text-orientation:upright;white-space:nowrap;
  text-decoration:none;color:$fg;position:relative;font-family:$body_font;
  font-size:25px;line-height:1.02;letter-spacing:.24em;padding:12px 0;
  transition:color .2s,text-shadow .2s}
.v1 .menu a:hover{color:$accent;text-shadow:0 0 18px rgba(184,68,58,.35)}
"""),
    'cinema-dark': Template(r"""
.v2{background:$bg}
.v2 .bg{position:absolute;inset:0;background-size:cover;background-position:center;
  filter:saturate(.86) brightness(.74);transform:scale(1.03)}
.v2 .veil{position:absolute;inset:0;
  background:linear-gradient(to top,rgba(6,8,11,.97) 0%,rgba(6,8,11,.72) 26%,
    rgba(6,8,11,.10) 62%,rgba(6,8,11,.42) 100%),
    radial-gradient(110% 70% at 78% 34%,rgba(255,196,120,.13),transparent 62%)}
.v2 .logo{position:absolute;left:7.2%;bottom:31%}
.v2 .cn{display:block;font-family:$title_font;font-weight:$title_weight;
  font-size:${title_size}px;color:$fg;letter-spacing:$letter_spacing;
  text-shadow:0 4px 30px rgba(0,0,0,.72)}
.v2 .en{display:block;margin-top:14px;font-family:Consolas,monospace;font-size:13px;
  letter-spacing:.62em;color:$muted;text-transform:uppercase}
.v2 .rule{position:absolute;left:7.4%;bottom:25.4%;width:190px;height:2px;
  background:linear-gradient(90deg,$accent,transparent)}
.v2 .menu{position:absolute;left:7.2%;bottom:9.5%;display:flex;align-items:center;
  gap:30px;font-family:$body_font;font-size:16px;letter-spacing:.16em;color:$muted}
.v2 .menu a{text-decoration:none;color:inherit;padding:8px 2px;position:relative;transition:color .2s}
.v2 .menu a:hover{color:$accent}
.v2 .menu a::after{content:"";position:absolute;left:0;bottom:0;width:0;height:1px;
  background:$accent;transition:width .24s}
.v2 .menu a:hover::after{width:100%}
.v2 .menu .dot{width:4px;height:4px;border-radius:50%;background:$accent;opacity:.55}
"""),
    'paper-indie': Template(r"""
.v3{background:$bg}
.v3 .left{position:absolute;left:0;top:0;bottom:0;width:58%;
  background:linear-gradient(160deg,$panel 0%,$bg 100%)}
.v3 .left::after{content:"";position:absolute;inset:0;background-image:
  radial-gradient(rgba(120,105,80,.07) .5px,transparent .5px);background-size:4px 4px}
.v3 .slab{position:absolute;right:0;top:0;bottom:0;width:46%;
  clip-path:polygon(22% 0,100% 0,100% 100%,0 100%);
  background-size:cover;background-position:center;filter:saturate(.95)}
.v3 .slab::after{content:"";position:absolute;inset:0;
  background:linear-gradient(120deg,rgba(247,245,240,.72),rgba(247,245,240,0) 46%)}
.v3 .logo{position:absolute;left:8%;top:17%}
.v3 .cn{font-family:$title_font;font-weight:$title_weight;font-size:${title_size}px;
  color:$fg;letter-spacing:$letter_spacing;line-height:1}
.v3 .tag{margin-top:18px;font-family:Consolas,monospace;font-size:13px;
  letter-spacing:.34em;color:$muted}
.v3 .hair{margin-top:26px;width:66px;height:3px;background:$fg}
.v3 .menu{position:absolute;left:8%;top:47%;display:flex;flex-direction:column;gap:2px}
.v3 .menu a{text-decoration:none;color:$fg;display:flex;align-items:baseline;gap:16px;
  padding:11px 0;font-family:$body_font;font-size:21px;letter-spacing:.1em;
  transition:color .2s,padding-left .2s}
.v3 .menu a .no{font-family:Consolas,monospace;font-size:11px;letter-spacing:.1em;
  color:$muted;min-width:20px}
.v3 .menu a:hover{color:$accent;padding-left:7px}
.v3 .menu a:hover .no{color:$accent}
"""),
    'theater-cold': Template(r"""
.v4{background:$bg}
.v4 .bg{position:absolute;inset:0;background-size:cover;background-position:center;
  filter:brightness(.34) saturate(.55) hue-rotate(-8deg)}
.v4 .glow{position:absolute;inset:0;
  background:radial-gradient(50% 42% at 50% 46%,rgba(96,158,255,.16),transparent 70%),
    radial-gradient(120% 100% at 50% 120%,rgba(4,6,12,.96),transparent 62%)}
.v4 .frame{position:absolute;inset:5.4% 6%;pointer-events:none}
.v4 .frame i{position:absolute;background:rgba(150,185,235,.20)}
.v4 .frame i.t{left:0;right:0;top:0;height:1px}
.v4 .frame i.b{left:0;right:0;bottom:0;height:1px}
.v4 .frame i.l{top:0;bottom:0;left:0;width:1px;
  background:linear-gradient(180deg,transparent,$accent,transparent);opacity:.42}
.v4 .frame i.r{top:0;bottom:0;right:0;width:1px;
  background:linear-gradient(180deg,transparent,$accent,transparent);opacity:.42}
.v4 .logo{position:absolute;left:50%;top:24%;transform:translateX(-50%);text-align:center}
.v4 .cn{display:block;font-family:$title_font;font-weight:$title_weight;
  font-size:${title_size}px;color:$fg;letter-spacing:$letter_spacing;
  text-indent:$letter_spacing;text-shadow:0 0 46px rgba(120,175,255,.42)}
.v4 .en{display:block;margin-top:20px;font-family:Consolas,monospace;font-size:11px;
  letter-spacing:.72em;text-indent:.72em;color:$muted}
.v4 .menu{position:absolute;left:50%;top:56%;transform:translateX(-50%);
  display:flex;flex-direction:column;align-items:center;gap:19px}
.v4 .menu a{text-decoration:none;color:$muted;font-family:$body_font;font-size:16px;
  letter-spacing:.42em;text-indent:.42em;padding:4px 0;
  transition:color .24s,text-shadow .24s,letter-spacing .24s}
.v4 .menu a:hover{color:$accent;letter-spacing:.52em;text-indent:.52em;
  text-shadow:0 0 22px $accent;opacity:1}
"""),
}


def _skeleton_id(preset):
    """这套预设该用哪一套骨架。

    内置四套（`*.json`）用自己；用户按要求和参考图设计出来的预设（`design.py`）
    用 `base` 指向的那套骨架 —— 配色、字体、字号、字距全换掉，但标题位置、
    菜单排列、纸纹光晕这些**结构**仍是经过验收的那一套，六块界面才不会散架。
    万一 base 也不认识，退回水墨（四套里最中性的一套）。
    """
    known = ('ink-classic', 'cinema-dark', 'paper-indie', 'theater-cold')
    for key in (preset.get('id'), preset.get('base')):
        if str(key or '') in known:
            return str(key)
    return 'ink-classic'


def _title_body(preset, *, title, subtitle, menu, version, bg, char_left, char_right):
    """渲染启动页标题区的 HTML。"""
    pid = _skeleton_id(preset)
    lay = preset['layout']
    menu_items = list(menu or DEFAULT_MENU)
    ver_style = {'ink-classic': 'left:4.2%;bottom:4.6%;color:#6a6153;font-size:11.5px',
                 'cinema-dark': 'left:7.4%;bottom:4%;color:rgba(246,241,230,.45)',
                 'paper-indie': 'left:8%;bottom:6%;color:#9a8f7d',
                 'theater-cold': 'left:50%;bottom:8%;transform:translateX(-50%);'
                                 'color:rgba(175,200,240,.40)'}.get(pid, 'left:5%;bottom:5%')
    corner_color = {'ink-classic': '#4a4238', 'cinema-dark': 'rgba(246,241,230,.75)',
                    'paper-indie': '#fdfcf9', 'theater-cold': 'rgba(200,220,250,.6)'}.get(pid, '#888')

    if pid == 'ink-classic':
        seal = '<div class="seal">奇迹工坊</div>' if lay.get('seal') else ''
        chars = ''
        if lay.get('show_characters'):
            if char_left:
                chars += f'<img class="char l" src="{_css_path(char_left)}" alt="">'
            if char_right:
                chars += f'<img class="char r" src="{_css_path(char_right)}" alt="">'
        links = ''.join(f'<a href="#">{_esc(m)}</a>' for m in menu_items)
        return (f'<div class="v1"><div class="paper"></div>'
                f'<div class="ink l"></div><div class="ink r"></div>{chars}'
                f'<div class="logo"><div class="cn">{_esc(title)}</div>{seal}</div>'
                f'<div class="menu">{links}</div>'
                f'{_ver(version, ver_style)}{_corner(corner_color)}</div>')

    if pid == 'cinema-dark':
        links = ''.join(
            (f'<span class="dot"></span>' if i else '') + f'<a href="#">{_esc(m)}</a>'
            for i, m in enumerate(menu_items))
        bg_css = f"background-image:url('{_css_path(bg)}')" if bg else ''
        return (f'<div class="v2"><div class="bg" style="{bg_css}"></div>'
                f'<div class="veil"></div>'
                f'<div class="logo"><span class="cn">{_esc(title)}</span>'
                f'<span class="en">{_esc(subtitle)}</span></div>'
                f'<div class="rule"></div>'
                f'<div class="menu">{links}</div>'
                f'{_ver(version, ver_style)}{_corner(corner_color)}</div>')

    if pid == 'paper-indie':
        links = ''.join(f'<a href="#"><span class="no">0{i + 1}</span>{_esc(m)}</a>'
                        for i, m in enumerate(menu_items))
        bg_css = f"background-image:url('{_css_path(bg)}')" if bg else ''
        return (f'<div class="v3"><div class="left"></div>'
                f'<div class="slab" style="{bg_css}"></div>'
                f'<div class="logo"><div class="cn">{_esc(title)}</div>'
                f'<div class="tag">{_esc(subtitle)}</div><div class="hair"></div></div>'
                f'<div class="menu">{links}</div>'
                f'{_ver(version, ver_style)}{_corner(corner_color)}</div>')

    short = [SHORT_MENU.get(m, m[:2] if len(m) > 2 else m) for m in menu_items]
    links = ''.join(f'<a href="#">{_esc(m)}</a>' for m in short)
    bg_css = f"background-image:url('{_css_path(bg)}')" if bg else ''
    return (f'<div class="v4"><div class="bg" style="{bg_css}"></div>'
            f'<div class="glow"></div>'
            f'<div class="frame"><i class="t"></i><i class="b"></i><i class="l"></i><i class="r"></i></div>'
            f'<div class="logo"><span class="cn">{_esc(title)}</span>'
            f'<span class="en">{_esc(subtitle)}</span></div>'
            f'<div class="menu">{links}</div>'
            f'{_ver(version, ver_style)}{_corner(corner_color)}</div>')


# ------------------------------------------------------- 其余五块皮（token 派生）
_SURFACE_CSS = Template(r"""
.s{position:absolute;inset:0;background:$bg;display:flex;padding:46px;gap:28px}
.frame-tag{position:absolute;left:46px;top:30px;font-family:Consolas,monospace;
  font-size:11.5px;letter-spacing:.22em;color:$accent;opacity:.85}
.card{position:relative;border-radius:$radius}
.dialog-wrap{position:absolute;left:4%;right:4%;bottom:4%;height:$dialog_h;
  background:$dialog_bg;border:$dialog_border;border-radius:$radius;
  display:flex;flex-direction:column;justify-content:center;gap:14px;padding:26px 34px}
.who{font-family:$body_font;font-size:15px;letter-spacing:.16em;color:$accent}
.say{font-family:$body_font;font-size:25px;line-height:1.5;color:$dialog_fg;letter-spacing:.03em}
.choices{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  display:flex;flex-direction:column;gap:14px;width:min(620px,52%)}
.opt{background:$choice_bg;border:$choice_border;border-radius:$radius;
  padding:18px 26px;font-family:$body_font;font-size:19px;letter-spacing:.08em;
  color:$choice_fg;transition:background .2s}
.opt.on{background:$choice_hover}
.slots{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  display:grid;grid-template-columns:1fr 1fr;gap:18px;width:min(880px,74%)}
.slot{background:$save_bg;border:1px solid $save_accent;border-radius:$radius;
  padding:20px 22px;font-family:$body_font;color:$save_fg}
.slot b{display:block;font-size:17px;letter-spacing:.06em;margin-bottom:8px}
.slot span{font-family:Consolas,monospace;font-size:11.5px;color:$save_accent;opacity:.85}
.row{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:min(760px,64%);display:flex;flex-direction:column;gap:2px}
.ri{display:flex;justify-content:space-between;align-items:center;padding:17px 4px;
  border-bottom:1px solid $save_accent;font-family:$body_font;font-size:17px;color:$save_fg}
.ri em{font-style:normal;color:$save_accent;font-family:Consolas,monospace;font-size:13px}
.hudbar{position:absolute;left:0;right:0;top:0;height:$hud_h;background:$hud_bg;
  display:flex;align-items:center;padding:0 34px;gap:26px;
  font-family:$body_font;font-size:14px;letter-spacing:.12em;color:$hud_fg}
.hudbar b{font-weight:700;font-size:15px;letter-spacing:.06em}
.hudbar .sp{flex:1}
""")


def _surface_body(preset, screen, *, lines, choices_top, bg):
    """渲染游戏内主界面的 HTML。"""
    pal = preset['palette']
    surf = preset.get('surfaces', {})
    tokens = dict(
        bg='transparent', accent=pal['accent'],
        radius=str((surf.get(screen) or {}).get('radius') or '4px'),
        body_font=_font(preset['typeface'].get('body')),
        dialog_h=str((surf.get('dialog') or {}).get('height') or '30%'),
        dialog_bg=(surf.get('dialog') or {}).get('bg') or 'rgba(0,0,0,.8)',
        dialog_fg=(surf.get('dialog') or {}).get('fg') or pal['fg'],
        dialog_border=(surf.get('dialog') or {}).get('border') or '1px solid transparent',
        choice_bg=(surf.get('choice') or {}).get('bg') or pal['panel'],
        choice_fg=(surf.get('choice') or {}).get('fg') or pal['fg'],
        choice_hover=(surf.get('choice') or {}).get('hover') or 'transparent',
        choice_border=(surf.get('choice') or {}).get('border') or '1px solid transparent',
        save_bg=(surf.get('save') or {}).get('bg') or pal['panel'],
        save_fg=(surf.get('save') or {}).get('fg') or pal['fg'],
        save_accent=(surf.get('save') or {}).get('accent') or pal['accent'],
        hud_bg=(surf.get('hud') or {}).get('bg') or pal['panel'],
        hud_fg=(surf.get('hud') or {}).get('fg') or pal['fg'],
        hud_h=str((surf.get('hud') or {}).get('height') or '96px'),
    )
    bg_css = f"background-image:url('{_css_path(bg)}');background-size:cover;background-position:center" if bg else ''
    tag = f'<div class="frame-tag">{_esc(preset["name"])} · {screen}</div>'
    body_bg = f'<div style="position:absolute;inset:0;{bg_css};opacity:.45"></div>'

    if screen == 'dialog':
        text = lines[0] if lines else '雨停之前，我不会离开这条码头。'
        who = '旁白' if not lines else '角色'
        inner = (f'<div class="dialog-wrap"><div class="who">{_esc(who)}</div>'
                 f'<div class="say">{_esc(text)}</div></div>')
    elif screen == 'choice':
        opts = list(choices_top or ('跟她走', '先回仓库看看', '把伞留给她'))
        marks = ''.join(f'<div class="opt{" on" if i == 0 else ""}">{_esc(o)}</div>'
                        for i, o in enumerate(opts))
        inner = f'<div class="choices">{marks}</div>'
    elif screen == 'save':
        slots = ''.join(
            f'<div class="slot"><b>存档 {i}</b><span>第 {i} 章 · 雨夜码头 · {i * 7 + 3} 分钟</span></div>'
            for i in range(1, 5))
        inner = f'<div class="slots">{slots}</div>'
    elif screen == 'settings':
        rows = ''.join(
            f'<div class="ri"><span>{label}</span><em>{value}</em></div>'
            for label, value in (('文字速度', '中 ▸'), ('自动播放间隔', '1.2 秒'),
                                 ('回声 / 环境音', '开'), ('字号缩放', '100%'),
                                 ('跳过已读', '关')))
        inner = f'<div class="row">{rows}</div>'
    else:  # hud
        inner = ('<div class="hudbar"><b>奇迹工坊</b><span>第一章 · 雨夜码头</span>'
                 '<span class="sp"></span><span>⏸  ⚙</span></div>')

    return f'<div class="s">{body_bg}{tag}{inner}</div>'


# ---------------------------------------------------------------- 入口
def render(preset, screen='title', *, title='奇迹工坊', subtitle='WHALE FALL WORKSHOP',
           menu=DEFAULT_MENU, version='v0.1.0 build 2026.11', bg='', char_left='',
           char_right='', lines=(), choices_top=None, page_title=None):
    """渲染一套界面，返回完整 HTML 字符串。"""
    screen = str(screen or 'title')
    if screen not in SCREENS:
        raise ValueError(f'screen 必须是 {SCREENS} 之一，收到 {screen!r}')
    pal = preset['palette']
    lay = preset['layout']
    typeface = preset['typeface']

    if screen == 'title':
        body = _title_body(preset, title=title, subtitle=subtitle, menu=menu,
                           version=version, bg=bg, char_left=char_left, char_right=char_right)
        extra = _TITLE_CSS[_skeleton_id(preset)].substitute(
            bg=pal['bg'], fg=pal['fg'], accent=pal['accent'], muted=pal.get('muted', pal['fg']),
            panel=pal.get('panel', pal['bg']),
            title_font=_font(typeface.get('title')), body_font=_font(typeface.get('body')),
            title_size=lay.get('title_size', 74), title_weight=lay.get('title_weight', 700),
            letter_spacing=lay.get('letter_spacing', '0.1em'),
            skew=lay.get('skew', '0deg'),
            char_opacity=lay.get('character_opacity', 0.66),
        )
    else:
        body = _surface_body(preset, screen, lines=list(lines), choices_top=choices_top, bg=bg)
        extra = _SURFACE_CSS.substitute(
            bg=pal['bg'], accent=pal['accent'],
            radius=str((preset.get('surfaces', {}).get(screen) or {}).get('radius') or '4px'),
            body_font=_font(typeface.get('body')),
            dialog_h=str((preset['surfaces'].get('dialog') or {}).get('height') or '30%'),
            dialog_bg=(preset['surfaces'].get('dialog') or {}).get('bg') or 'rgba(0,0,0,.8)',
            dialog_fg=(preset['surfaces'].get('dialog') or {}).get('fg') or pal['fg'],
            dialog_border=(preset['surfaces'].get('dialog') or {}).get('border') or 'none',
            choice_bg=(preset['surfaces'].get('choice') or {}).get('bg') or pal['panel'],
            choice_fg=(preset['surfaces'].get('choice') or {}).get('fg') or pal['fg'],
            choice_hover=(preset['surfaces'].get('choice') or {}).get('hover') or 'transparent',
            choice_border=(preset['surfaces'].get('choice') or {}).get('border') or 'none',
            save_bg=(preset['surfaces'].get('save') or {}).get('bg') or pal['panel'],
            save_fg=(preset['surfaces'].get('save') or {}).get('fg') or pal['fg'],
            save_accent=(preset['surfaces'].get('save') or {}).get('accent') or pal['accent'],
            hud_bg=(preset['surfaces'].get('hud') or {}).get('bg') or pal['panel'],
            hud_fg=(preset['surfaces'].get('hud') or {}).get('fg') or pal['fg'],
            hud_h=str((preset['surfaces'].get('hud') or {}).get('height') or '96px'),
        )

    return _BASE.substitute(
        page_title=_esc(page_title or f"{title} · {preset['name']} · {screen}"),
        bg=pal['bg'], body_font=_font(typeface.get('body')),
        extra_css=extra, body=body,
    )


def write(preset, path, **kwargs):
    """渲染并落盘，返回路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(preset, **kwargs), encoding='utf-8')
    return target
