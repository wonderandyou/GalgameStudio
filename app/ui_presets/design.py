"""界面设计：把「用户的文字要求 + 参考图」翻译成一套可渲染的界面预设。

为什么不让模型从零画一套界面
--------------------------
界面最容易崩的地方不是「做不出来」，而是**每做一个界面就换一套视觉语言**。
所以设计走的是「生成预设 → 现有 render.py 渲染」这条路：

- 设计只决定**参数**（配色 / 字体气质 / 排版开关 / 立绘处理）；
- 骨架仍复用四套成熟预设之一（`preset['base']`），六块界面天然同源；
- 想改随时回 `tweak_ui_preset`，不会一改就得重画。

三段式
------
1. `analyze()`  要求文字 → 气质词 + 最贴近的 base 预设；参考图 → 主色板
2. `compose()`  参数合成一套预设 dict（带对比度修正，保证可读）
3. `save()`     落盘到 `designed/<id>.json`，之后 `get()` 和 `render()` 都能用

参考图只用来**取色和判断气质**，原图不落盘、不进成品
（原图有版权，而且写实照片当 UI 背景和插画界面会打架）。
"""
import base64
import colorsys
import hashlib
import io
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESIGNED_DIR = HERE / 'designed'

#: 参考图张数上限。和 providers.image 的上限保持一致，避免前端能传、后端不认。
MAX_REFERENCES = 6

#: 单张参考图字节上限（解码后）。前端会先压到 1280 长边，正常在 300KB 内。
MAX_REFERENCE_BYTES = 6 * 1024 * 1024

#: 取色时每通道量化档位。8 档 = 最多 512 个桶，够分出主色又不会碎成一堆。
_QUANT = 8

#: 比对度下限（WCAG AA 正文标准）。低于这个值界面会「看着有颜色但读不清」。
MIN_CONTRAST = 4.5

#: 文字要求里能直接落成排版参数的硬开关。
#: 每条 = (触发词, 目标字段, 变化方式)；变化方式为 int 时是增量，为 str 时直接赋值。
#: 只有**渲染层真的认**的字段才放这里 —— 见下面的 `_UNSUPPORTED_RULES`。
_TYPO_RULES = (
    (('大标题', '标题要大', '标题大', '字大', '粗黑'), 'title_size', 16),
    (('标题小', '标题小一点', '小标题'), 'title_size', -14),
    (('字距拉开', '疏朗', '字距大', '呼吸感'), 'letter_spacing', '0.18em'),
    (('紧凑', '紧密', '字距小'), 'letter_spacing', '0.04em'),
)

#: 立绘开关的词根。单独判断，不放进 `_TYPO_RULES`：
#: 「不要立绘」里包含子串「要立绘」，两条规则按顺序互相覆盖时总有一个方向判错。
_CHARACTER_WORDS = ('立绘', '人物图', '纯文字')

#: 立绘开 / 关的写法。前两条正则负责「说不说的都算上」（不 要 立绘 / 别 放 带 立绘）。
_CHARACTER_OFF = re.compile(r'(不|别|没|无)[要放带加]\S{0,2}(立绘|人物图)|纯文字')
_CHARACTER_ON = re.compile(r'(要|放|带|加|有)\S{0,2}(立绘|人物图)')

#: 能听懂、但**当前渲染层还做不到**的偏好。
#: 四套骨架的菜单排列是写死在 CSS 里的（`layout.menu` 目前是死参数，`tweak_ui_preset`
#: 的 menu 参数同样落不了地），所以这几条只在说明里如实告诉用户，**不假装生效**。
#: 骗用户比不做更糟：他会以为改了，结果成品没变。
_UNSUPPORTED_RULES = (
    (('竖排', '竖着', '竖列', '纵向排列'), '菜单竖排'),
    (('横排', '横向', '一行排开'), '菜单横排'),
    (('编号', '序号', '数字排'), '菜单带编号'),
)

#: 气质词 → 主题明暗。先命中的先落，后面的同字段覆盖前面的。
#: 只影响「取哪个方向」，真正的色值仍以参考图为准（参考图优先级最高）。
_DARK_WORDS = ('暗', '夜', '黑', '深色', '冷', '孤独', '压抑', '悬疑', '恐怖', '阴雨',
               '沉默', '黄昏后', '深空', '赛博', '废土', 'noir')
_LIGHT_WORDS = ('亮', '白', '浅色', '清新', '治愈', '温暖', '明亮', '校园', '日常',
                '纸', '留白', 'soft', '清爽', '阳光')

#: 气质词 → 标题字体家族（render.py 的 FONTS 键）。
#: 中文标题的「气质」八成靠字体定，配色只能加一点。
_FONT_RULES = (
    (('水墨', '宣纸', '古风', '毛笔', '书法', '江湖', '武侠', '仙侠', '民国', '年代'), 'brush'),
    (('冷峻', '克制', '诗', '文学', '纯文字', '书', '印刷'), 'serif'),
    (('科技', '赛博', '机甲', '未来', '极简', '现代', '终端', '数据'), 'sans'),
)

#: 气质词 → 菜单骨架（和 _TYPO_RULES 的 menu 有过重叠时以 _TYPO_RULES 为准）。
#: 这里只给「没明说但气质明显」的情况一个默认。
_MENU_HINTS = {
    'brush': 'vertical',
    'serif': 'numbered',
    'sans': 'vertical',
}#: 几何词 → 圆角。四套预设的 surfaces 里圆角各不相同，这里统一覆盖。
_RADIUS_RULES = (
    (('圆角', '柔和', '软', '可爱', '治愈', '温暖'), '10px'),
    (('直角', '硬朗', '方正', '锐利', '冷硬'), '0px'),
)

#: 颜色词 → (色相0~360, 饱和度0~1, 亮度0~1)。
#: **长词必须排在短词前面**：「冷蓝」要排在「蓝」前面，否则会被拆成普通蓝。
#: 以前一张颜色词表都没有 —— 用户写「冷蓝」「霓虹紫」完全无效，配色永远来自
#: 参考图或 base 预设，于是「怎么写都同一套」（2026-11-11 主人反馈后补上）。
_COLOR_WORDS = (
    (('午夜蓝', '藏蓝', '海军蓝', '冷蓝', '深蓝'), 214, 0.55, 0.40),
    (('天空蓝', '天蓝', '浅蓝', '淡蓝', '水蓝', '湖蓝', 'baby蓝'), 200, 0.52, 0.70),
    (('蓝紫', '靛'), 240, 0.50, 0.48),
    (('紫罗兰', '霓虹紫', '赛博紫', '电光紫'), 278, 0.70, 0.60),
    (('丁香', '薰衣草', '粉紫'), 285, 0.38, 0.72),
    (('紫', '紫色'), 278, 0.45, 0.50),
    (('勃艮第', '酒红', '暗红', '血红'), 352, 0.58, 0.30),
    (('珊瑚', '砖红', '朱红'), 8, 0.62, 0.52),
    (('红', '赤'), 0, 0.62, 0.48),
    (('森林绿', '墨绿', '暗绿', '军绿'), 150, 0.48, 0.26),
    (('薄荷', '青柠', '浅绿'), 155, 0.42, 0.70),
    (('绿', '翠'), 145, 0.50, 0.44),
    (('孔雀蓝', '蓝绿', '青碧'), 182, 0.50, 0.46),
    (('青', '天青'), 185, 0.42, 0.58),
    (('香槟', '琥珀', '黄铜', '古铜'), 40, 0.55, 0.60),
    (('金', '黄金', '金色'), 43, 0.62, 0.55),
    (('柠檬', '鹅黄', '米黄'), 50, 0.70, 0.74),
    (('暖黄', '暖金', '杏色', '蜜色', '奶黄'), 38, 0.52, 0.72),
    (('黄',), 52, 0.72, 0.62),
    (('橙', '橘', '南瓜'), 26, 0.70, 0.54),
    (('樱花', '蜜桃', '桃粉'), 340, 0.45, 0.76),
    (('粉', '玫瑰'), 335, 0.50, 0.68),
    (('咖啡', '木色', '牛皮', '可可'), 28, 0.38, 0.34),
    (('棕', '褐色'), 28, 0.38, 0.32),
    (('银灰', '金属灰'), 210, 0.05, 0.62),
    (('灰', '银', '石灰'), 210, 0.06, 0.52),
    (('奶油', '米白', '象牙'), 42, 0.18, 0.92),
    (('米色', '米'), 40, 0.18, 0.86),
)

#: 颜色的修饰词。只调相应通道，不换色相。
_DEEPEN = re.compile(r'(深|暗|浓|重|压暗)')
_LIGHTEN = re.compile(r'(浅|淡|亮|轻|提亮)')
_MUTED = re.compile(r'(低饱和|灰调|素|柔|莫兰迪|雾)')
_NEON = re.compile(r'(霓虹|电光|赛博|荧光)')

#: 生成的名字前缀（按 base 预设换，一眼知道是从哪套长出来的）。
_NAME_PREFIX = {
    'ink-classic': '水墨',
    'cinema-dark': '冷幕',
    'paper-indie': '纸感',
    'theater-cold': '剧场',
}


# ------------------------------------------------------------------ 工具函数

def _clamp(value, low, high):
    """把数值夹到区间内。"""
    return max(low, min(high, value))


def _hex_to_rgb(value):
    """`#rrggbb` / `#rgb` → (r, g, b)；格式不对返回 None。"""
    text = str(value or '').strip().lstrip('#')
    if len(text) == 3:
        text = ''.join(char * 2 for char in text)
    if len(text) != 6 or not re.fullmatch(r'[0-9a-fA-F]{6}', text):
        return None
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def _rgb_to_hex(rgb):
    """(r, g, b) → `#rrggbb`。"""
    red, green, blue = (int(_clamp(round(channel), 0, 255)) for channel in rgb)
    return '#%02x%02x%02x' % (red, green, blue)


def _relative_luma(rgb):
    """WCAG 相对亮度（0~1）。不用简单平均 —— 人眼对绿色最敏感，简单平均会算错对比度。"""
    channels = []
    for channel in rgb:
        value = channel / 255.0
        channels.append(value / 12.92 if value <= 0.04045
                        else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(one, other):
    """两色的 WCAG 对比度（1~21）。"""
    luma_one, luma_other = _relative_luma(one), _relative_luma(other)
    high, low = max(luma_one, luma_other), min(luma_one, luma_other)
    return (high + 0.05) / (low + 0.05)


def _saturation(rgb):
    """HSV 里的 S（0~1）。用来挑「强调色」—— 强调色要饱和，背景色不该饱和。"""
    red, green, blue = (channel / 255.0 for channel in rgb)
    return colorsys.rgb_to_hsv(red, green, blue)[1]


def _mix(one, other, ratio):
    """按比例混两色（0 = 全 one，1 = 全 other）。"""
    return tuple(one[index] + (other[index] - one[index]) * ratio for index in range(3))


def _shift_to_luma(rgb, target_luma):
    """把颜色往黑或白推，直到相对亮度接近 target_luma。

    用二分而不是线性插值：亮度是伽马空间的非线性量，同样的混合比例不等于同样的亮度差。
    方向只看目标比当前亮还是暗，所以「推亮」和「推暗」是同一条代码路径。
    """
    target = _clamp(target_luma, 0.0, 1.0)
    current = _relative_luma(rgb)
    going_up = target > current
    direction = (255.0, 255.0, 255.0) if going_up else (0.0, 0.0, 0.0)
    low, high = 0.0, 1.0
    for _ in range(24):
        middle = (low + high) / 2
        probe = _relative_luma(_mix(rgb, direction, middle))
        if abs(probe - target) <= 1e-4:
            return _mix(rgb, direction, middle)
        # 混合比例越大，亮度越贴近目标方向 —— 用「还差多少」决定收哪半边
        if (going_up and probe < target) or (not going_up and probe > target):
            low = middle
        else:
            high = middle
    return _mix(rgb, direction, (low + high) / 2)


def _ensure_contrast(foreground, background, minimum=MIN_CONTRAST):
    """前景和背景对比度不够时把前景往反方向推，推到达标为止。

    返回 (fg_rgb, ratio)。推的方向跟背景亮度有关：深底推亮、浅底推暗。
    """
    fg, bg = tuple(foreground), tuple(background)
    if _contrast_ratio(fg, bg) >= minimum:
        return fg, _contrast_ratio(fg, bg)
    target = 0.92 if _relative_luma(bg) < 0.5 else 0.10
    for step in range(12):
        fg = _mix(fg, (255.0, 255.0, 255.0) if target > 0.5 else (0.0, 0.0, 0.0), 0.08)
        ratio = _contrast_ratio(fg, bg)
        if ratio >= minimum:
            return fg, ratio
        if step == 5 and ratio < minimum:
            # 半路还不够就直接跳到目标亮度，别在中间磨
            fg = _shift_to_luma(fg, target)
            if _contrast_ratio(fg, bg) >= minimum:
                return fg, _contrast_ratio(fg, bg)
    return _shift_to_luma(fg, target), _contrast_ratio(_shift_to_luma(fg, target), bg)


# ------------------------------------------------------------------ 参考图取色

def _decode_reference(item):
    """把一张参考图（dataURL / base64 / 原始字节）解成 bytes；解不出返回 None。"""
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)
    text = str(item or '').strip()
    if not text:
        return None
    if text.startswith('data:'):
        head, _, payload = text.partition(',')
        if ';base64' not in head:
            return None
        text = payload
    text = re.sub(r'\s+', '', text)
    try:
        raw = base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        return None
    return raw if len(raw) <= MAX_REFERENCE_BYTES else None


def palette_from_images(images, limit=5):
    """从参考图里提主色板。

    做法：缩到 64×64 解压色 → 每通道量化 8 档归桶 → 按像素数排序 →
    丢掉接近纯黑/纯白的极端色（它们是阴影和高光，不是「这套图的主色」）→
    把剩下的按饱和度分成「底色候选」和「强调色候选」两类再合并。

    返回 `[{hex, weight, luma, saturation}]`，weight 是像素占比（0~1），按大到小。
    解不出图时返回空列表 —— 调用方要能接受「没有参考图」这条路。
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 是硬依赖，装不上就不能抠图
        return []

    buckets = {}
    total = 0
    used = 0
    step = 256 // _QUANT
    for item in list(images or [])[:MAX_REFERENCES]:
        raw = _decode_reference(item)
        if not raw:
            continue
        try:
            image = Image.open(io.BytesIO(raw)).convert('RGB')
        except (OSError, ValueError):
            continue
        image.thumbnail((64, 64))
        used += 1
        # 用 tobytes 逐像素过一遍，不用 getdata（Pillow 14 会移除它，避免日志刷警告）
        pixels = image.tobytes()
        for index in range(0, len(pixels), 3):
            red, green, blue = pixels[index], pixels[index + 1], pixels[index + 2]
            key = (red // step, green // step, blue // step)
            entry = buckets.get(key)
            if entry is None:
                # [像素数, r和, g和, b和] —— 存和而不是存中心，
                # 出来的是这个桶里**真实的平均色**（存中心会把米白算成粉色，踩过）
                buckets[key] = [1, red, green, blue]
            else:
                entry[0] += 1
                entry[1] += red
                entry[2] += green
                entry[3] += blue
            total += 1

    if not buckets or not total:
        return []

    pick = []
    for entry in buckets.values():
        count, sum_red, sum_green, sum_blue = entry
        rgb = (sum_red / count, sum_green / count, sum_blue / count)
        # 判「是不是纯黑/纯白」要看通道极值，不能用亮度阈值：
        # 深蓝夜空 #161c28 的相对亮度只有 0.013，按亮度判会被当成纯黑删掉 ——
        # 而它恰恰是整张图里最该当底色的那个颜色（踩过）。
        if max(rgb) <= 10 or min(rgb) >= 246:
            continue  # 纯黑 / 纯白：几乎都是边缘和噪点
        pick.append(dict(rgb=rgb, hex=_rgb_to_hex(rgb), weight=count / total,
                         luma=_relative_luma(rgb), saturation=_saturation(rgb)))

    if not pick:
        return []

    pick.sort(key=lambda item: item['weight'], reverse=True)
    # 饱和度和「像底色」是反着来的：底色要中性，强调色要艳。
    neutral = [item for item in pick if item['saturation'] <= 0.32]
    vivid = [item for item in pick if item['saturation'] > 0.32]
    ordered = neutral + vivid
    result = []
    for item in ordered:
        # 太接近的颜色合并掉 —— 五个几乎一样的灰对设计没有意义
        if any(abs(item['luma'] - seen['luma']) < 0.06
               and abs(item['saturation'] - seen['saturation']) < 0.10
               for seen in result):
            continue
        result.append(dict(hex=item['hex'], weight=round(item['weight'], 4),
                           luma=round(item['luma'], 4),
                           saturation=round(item['saturation'], 4)))
        if len(result) >= limit:
            break
    return result


# ------------------------------------------------------------------ 要求解读

def _keywords(text):
    """从要求里提取能落地的关键词（去重保序）。"""
    clean = str(text or '').strip()
    if not clean:
        return []
    found = []
    for group in _TYPO_RULES + _UNSUPPORTED_RULES:
        for word in group[0]:
            if word in clean and word not in found:
                found.append(word)
    for group in _FONT_RULES + _RADIUS_RULES:
        for word in group[0]:
            if word in clean and word not in found:
                found.append(word)
    for word in _DARK_WORDS + _LIGHT_WORDS + _CHARACTER_WORDS:
        if word in clean and word not in found:
            found.append(word)
    # 颜色词也算关键词，而且**放到最前面**：预设名字要能体现「冷蓝」「霓虹紫」，
    # 否则用户改的那两套全叫「纸感定制」，分不清差在哪（2026-11-11）。
    for names, _hue, _saturation, _lightness in _COLOR_WORDS:
        hit = next((name for name in names if name in clean), '')
        if hit and hit not in found:
            found.insert(0, hit)
            break                                   # 名字里带一个颜色名就够
    return found


def _wants_dark(text):
    """要求里有没有明确要暗色。没有任何明暗词时返回 None（表示「看参考图」）。"""
    clean = str(text or '')
    dark = any(word in clean for word in _DARK_WORDS)
    light = any(word in clean for word in _LIGHT_WORDS)
    if dark and not light:
        return True
    if light and not dark:
        return False
    return None


def _pick_font(text):
    """按气质词选标题字体键。"""
    clean = str(text or '')
    for words, font in _FONT_RULES:
        if any(word in clean for word in words):
            return font
    return ''


def _pick_radius(text):
    """按几何词选圆角。"""
    clean = str(text or '')
    for words, radius in _RADIUS_RULES:
        if any(word in clean for word in words):
            return radius
    return ''


def _pick_base(matches, text):
    """挑一套骨架：关键词命中最强的优先，没有命中就给中性纸感。

    **只在内置四套里挑。** 用户设计出来的预设也带着 keywords（从上次的要求里抽的），
    把它们放进候选，每次设计都会在上一次的产物上再长一层 ——
    骨架越用越偏，最后连「这是从水墨改出来的」都说不清。
    """
    from . import ORDER  # 延迟导入，原因同 analyze

    builtin = [item for item in matches if item.get('id') in ORDER]
    strong = [item for item in builtin if item.get('strength') == 'strong']
    if strong:
        return strong[0]['id']
    if builtin:
        return builtin[0]['id']
    # 一个词都没命中：纸感最中性，换成别的都会先入为主
    return 'paper-indie'


def _character_toggle(text):
    """从要求里判断要不要立绘。返回 True / False / None（没提就不动）。

    为什么单独拎出来：「不要立绘」包含子串「要立绘」，「不要带立绘」包含「带立绘」。
    如果把它当作普通规则塞进表里按顺序覆盖，总有一个写法会被判反 ——
    这里先找否定写法，否定没命中才看肯定写法。
    """
    clean = str(text or '')
    if not any(word in clean for word in _CHARACTER_WORDS):
        return None
    if _CHARACTER_OFF.search(clean):
        return False
    if _CHARACTER_ON.search(clean):
        return True
    return None


def _apply_typo(layout, text):
    """把文字要求里的硬开关落到 layout 上。返回 (新 layout, 落地的规则名列表)。"""
    data = dict(layout)
    applied = []
    clean = str(text or '')
    for words, field, change in _TYPO_RULES:
        if not any(word in clean for word in words):
            continue
        # bool 必须排在 int 前面：Python 里 bool 是 int 的子类，
        # 反过来的话 `show_characters=False` 会走进加法分支，算出一个数字（踩过：41）。
        if isinstance(change, bool):
            data[field] = change
        elif isinstance(change, int):
            current = int(data.get(field) or 74)
            data[field] = _clamp(current + change, 40, 140)
        else:
            data[field] = change
        applied.append(words[0])
    return data, applied


# ------------------------------------------------------------------ 合成预设

def _color_card(rgb, weight):
    """把 0~1 的 RGB 元组变成和参考图取色同构的色卡。"""
    hex_value = _rgb_to_hex(tuple(max(0, min(255, int(round(value * 255)))) for value in rgb))
    parsed = _hex_to_rgb(hex_value) or (0, 0, 0)
    return dict(hex=hex_value, weight=float(weight),
                luma=_relative_luma(parsed), saturation=_saturation(parsed))


def _text_colors(text):
    """从文字要求里认出「点名要的颜色」，转成和参考图同构的色卡。

    返回 `(色卡列表, 命中的颜色词)`。最多认两个颜色 —— 三个以上就不像一套配色了。

    ★ 为什么转成色卡而不是直接给一个 hex：
      `_palette_from_colors` 那套逻辑（最暗做底、最鲜做强调、对比度兜底）
      已经很成熟，喂同样的结构就能复用，不用另写一套配色推导。

    ★ 为什么一个颜色要生成三档（主色 / 暗版 / 亮版）：
      只给一个中蓝的话，那套逻辑里 darkest 和 lightest 是同一个色，
      底色会直接变成一片艳蓝、界面糊掉。给三档它才挑得出来该用哪个当底。
    """
    clean = str(text or '')
    if not clean:
        return [], [], None
    picked, words = [], []
    suggest_dark = None
    for names, hue, saturation, lightness in _COLOR_WORDS:
        hit = next((name for name in names if name in clean), '')
        # ★ 被已命中的长词**包含**就跳过：「霓虹紫」命中后，「紫」不该再来一遍。
        #   原来是 `hit in words`（列表成员判断），只能挡完全相同的词。
        if not hit or any(hit in word for word in words):
            continue
        words.append(hit)
        # 修饰词只在颜色名本身不带这个修饰时才叠，避免「深蓝」被加深两层
        if _DEEPEN.search(clean) and not _DEEPEN.search(hit):
            lightness = max(0.08, lightness - 0.14)
        if _LIGHTEN.search(clean) and not _LIGHTEN.search(hit):
            lightness = min(0.95, lightness + 0.14)
        if _NEON.search(clean):
            saturation = min(0.95, saturation + 0.18)
        if _MUTED.search(clean):
            saturation = max(0.04, saturation * 0.55)
        picked.append((hue, saturation, lightness))
        if suggest_dark is None:
            # ★ 用**感知亮度**判明暗，不用 _relative_luma：
            #   后者带 gamma 校正，粉色 / 浅紫这类色天然偏低（樱花算出来 0.461），
            #   会把「粉嫩可爱」判成暗色，配出一片暗红底（2026-11-11 实测踩到）。
            suggest_dark = lightness < 0.5
        if len(picked) >= 2:
            break

    cards = []
    for index, (hue, saturation, lightness) in enumerate(picked):
        weight = 1.0 if index == 0 else 0.62
        hue_fraction = (hue % 360) / 360.0
        cards.append(_color_card(colorsys.hls_to_rgb(hue_fraction, lightness, saturation), weight))
        # 暗版：给深色界面当底，压得很深但保留一点色相
        cards.append(_color_card(colorsys.hls_to_rgb(
            hue_fraction, max(0.07, lightness * 0.36), saturation * 0.55), weight * 0.72))
        # 亮版：给浅色界面当底，几乎退成「带一点色味的白」
        cards.append(_color_card(colorsys.hls_to_rgb(
            hue_fraction, min(0.97, lightness + 0.38), saturation * 0.26), weight * 0.72))
    return cards, words, suggest_dark


def _main_colors(colors, min_ratio=0.15, floor=0.02):
    """挑出「够面积」的主色，用来定底色和强调色。

    缩放时块与块的边界会混出一批中间色（权重通常只有 1~2%）。如果拿它们当底色，
    出来的界面是「说不清什么颜色」的脏色。所以只保留权重跟最大色同一档的色。
    """
    ordered = sorted(colors, key=lambda item: item['weight'], reverse=True)
    if not ordered:
        return []
    threshold = max(ordered[0]['weight'] * min_ratio, floor)
    keep = [item for item in ordered if item['weight'] >= threshold]
    return keep or ordered[:1]


def _palette_from_colors(colors, base_palette, dark=None):
    """用参考图主色组一套配色；没有主色就沿用 base 的配色。"""
    fallback = dict(base_palette or {})
    if not colors:
        data = dict(fallback)
        if dark is True:
            data['bg'] = '#14161a'
            data['fg'] = '#e8e4dc'
            data['panel'] = '#1d2026'
            data['accent'] = fallback.get('accent') or '#b8443a'
            data['muted'] = '#8a857c'
        return data, dict(contrast=None, source='preset')

    main = _main_colors(colors)
    ordered = sorted(main, key=lambda item: item['luma'])
    darkest, lightest = ordered[0], ordered[-1]
    if dark is None:
        # 没有明暗词时跟着参考图的整体亮度走：深色图配深色 UI 才不违和
        average = sum(item['luma'] * item['weight'] for item in colors) / max(
            sum(item['weight'] for item in colors), 1e-6)
        dark = average < 0.46

    if dark:
        bg_rgb = _hex_to_rgb(darkest['hex'])
        fg_rgb = _shift_to_luma(bg_rgb, 0.90)
    else:
        bg_rgb = _hex_to_rgb(lightest['hex'])
        fg_rgb = _shift_to_luma(bg_rgb, 0.12)
    bg_rgb = bg_rgb or (36, 38, 42)
    fg_rgb = fg_rgb or (232, 228, 220)

    # 强调色：饱和度最高的那个；参考图全是灰时给一个稳妥的暖红，不要硬造蓝
    vivid = sorted(main, key=lambda item: item['saturation'], reverse=True)
    accent_rgb = _hex_to_rgb(vivid[0]['hex']) if vivid and vivid[0]['saturation'] > 0.24 else None
    if accent_rgb is None:
        accent_rgb = _hex_to_rgb(fallback.get('accent') or '#b8443a') or (184, 68, 58)
    # 贴在同系底色上时对比度不够 —— 往亮/暗推，但**只推到刚好够**。
    # 早先固定推到亮度 0.72，暗红强调色会被推成灰白，等于把强调色弄丢了（踩过）。
    accent_rgb, accent_ratio = _ensure_contrast(accent_rgb, bg_rgb, minimum=2.2)

    fg_rgb, ratio = _ensure_contrast(fg_rgb, bg_rgb)
    panel_rgb = _mix(bg_rgb, fg_rgb, 0.10)
    muted_rgb = _mix(fg_rgb, bg_rgb, 0.42)

    palette = dict(
        bg=_rgb_to_hex(bg_rgb), fg=_rgb_to_hex(fg_rgb), accent=_rgb_to_hex(accent_rgb),
        muted=_rgb_to_hex(muted_rgb), panel=_rgb_to_hex(panel_rgb),
    )
    return palette, dict(contrast=round(ratio, 2), accent_contrast=round(accent_ratio, 2),
                         source='reference', dark=bool(dark))


def _surfaces(palette, radius, dark):
    """按配色重算六块皮的色值。透明底一律带 alpha，界面才不会糊住背景。"""
    bg = palette['bg']
    fg = palette['fg']
    accent = palette['accent']
    panel = palette['panel']
    alpha_bg = 'rgba(%s,.94)' % ','.join(str(value) for value in _hex_to_rgb(bg))
    alpha_panel = 'rgba(%s,.96)' % ','.join(str(value) for value in _hex_to_rgb(panel))
    border = '1px solid rgba(%s,.30)' % ','.join(
        str(value) for value in _hex_to_rgb(fg))
    return dict(
        dialog=dict(bg=alpha_bg, fg=fg, border=border, radius=radius, height='30%'),
        choice=dict(bg=alpha_panel, fg=fg, hover='rgba(%s,.14)' % ','.join(
            str(value) for value in _hex_to_rgb(accent)), border=border, radius=radius),
        save=dict(bg=bg, fg=fg, accent=accent),
        settings=dict(bg=bg, fg=fg, accent=accent),
        hud=dict(bg=alpha_panel, fg=fg, height='88px'),
    )


def _design_id(base_id, requirement, colors):
    """生成稳定的预设 id：同样的要求和参考图 → 同一个 id（重复设计不会堆垃圾文件）。"""
    seed = '|'.join([str(base_id), str(requirement or ''),
                     ','.join(item['hex'] for item in colors)])
    return 'designed-' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]


def analyze(requirement='', references=(), title=''):
    """把要求与参考图拆成设计依据。纯计算，不落盘、不改全局状态。"""
    from . import match  # 延迟导入：design 被 __init__ 反向导入，模块级导入会成环

    text = str(requirement or '').strip()
    matches = match(text) if text else []
    image_colors = palette_from_images(references)
    text_cards, color_words, color_dark = _text_colors(text)
    # ★ 文字点名的颜色排在参考图**前面**（2026-11-11）：
    #   用户明确说「冷蓝」时，不该被参考图里偶然那片暖色盖过去。
    colors = text_cards + image_colors
    base_id = _pick_base(matches, text)
    dark = _wants_dark(text)
    if dark is None and color_dark is not None:
        # 点了颜色但没说深/浅：跟着那个颜色自己的感知亮度走。
        # 不然「午夜蓝」会被配成白底浅蓝、「粉嫩樱花」会被配成暗红底。
        dark = color_dark
    return dict(
        requirement=text,
        title=str(title or '').strip(),
        keywords=_keywords(text),
        matches=matches,
        base=base_id,
        colors=colors,
        color_words=color_words,
        image_colors=image_colors,
        dark=dark,
        font=_pick_font(text),
        radius=_pick_radius(text),
        references_used=sum(1 for item in list(references or [])[:MAX_REFERENCES]
                            if _decode_reference(item)),
    )


def compose(requirement='', references=(), base_id='', title=''):
    """合成一套预设 dict（还没落盘）。

    `base_id` 给了就强制用那套骨架（用户在界面上手动指定预设时用）；
    没给就按要求和参考图自动挑。返回的 dict 字段与普通预设一致，
    额外多 `base` / `design` 两个字段说明「从哪套骨架长出来的、依据是什么」。
    """
    from . import get  # 延迟导入，原因同 analyze

    info = analyze(requirement=requirement, references=references, title=title)
    explicit = str(base_id or '').strip()
    chosen = explicit or info['base']
    try:
        base = get(chosen)
    except KeyError:
        if explicit:
            # 用户指定了骨架、但它不存在 —— 必须报错。
            # 静默换成别的骨架比报错更糟：他以为在 A 上改，实际渲染的是 B。
            raise
        # 自动挑的骨架万一失配（比如预设文件被删），退回最中性的那套，别让整条路断掉
        base = get('paper-indie')
        chosen = 'paper-indie'

    base_palette = dict(base['palette'])
    palette, contrast = _palette_from_colors(info['colors'], base_palette, dark=info['dark'])

    layout = dict(base['layout'])
    layout, typo_hits = _apply_typo(layout, info['requirement'])
    toggle = _character_toggle(info['requirement'])
    if toggle is not None:
        layout['show_characters'] = toggle
        typo_hits.append('要立绘' if toggle else '不要立绘')
    font = info['font'] or base['typeface'].get('title') or 'sans'
    # 菜单排列（竖排/横排/编号）目前渲染层不认，不去改 layout['menu'] ——
    # 改了不生效还写进定义里，就是留下一个「看着配好了、渲染没变」的假参数。
    unsupported = [label for words, label in _UNSUPPORTED_RULES
                   if any(word in info['requirement'] for word in words)]

    radius = info['radius'] or str(
        (base.get('surfaces', {}).get('dialog') or {}).get('radius') or '4px')

    if 'title_size' in layout:
        layout['title_size'] = _clamp(int(layout['title_size']), 40, 140)

    # 名字只用「气质词」，不用参数词（标题要大 / 字距拉开 / 竖排 这些是开关，不是名字）
    param_words = {word for group in _TYPO_RULES + _UNSUPPORTED_RULES
                   for word in group[0]} | set(_CHARACTER_WORDS)
    name_parts = [_NAME_PREFIX.get(chosen, '定制')]
    for keyword in info['keywords']:
        if keyword in param_words or keyword in name_parts:
            continue
        name_parts.append(keyword)
        if len(name_parts) >= 3:
            break
    name = ''.join(name_parts) or '定制界面'

    vibe_bits = []
    if info['color_words']:
        vibe_bits.append('你点名的颜色：%s' % '、'.join(info['color_words']))
    if info['image_colors']:
        vibe_bits.append('参考图取色 %d 个' % len(info['image_colors']))
    if info['keywords']:
        vibe_bits.append('、'.join(info['keywords'][:3]))
    vibe = '，'.join(vibe_bits) or (base.get('vibe') or '按默认气质生成')

    notes = [
        '骨架沿用 %s（%s），改成 %s 后六块界面仍同一套视觉语言。' % (
            base['id'], base.get('name') or '', name),
        '参考图只用于取色和判断气质，原图不进成品。',
    ]
    if info['color_words']:
        notes.append('配色按你点名的「%s」算出来的；参考图只用来补明暗。'
                     % '、'.join(info['color_words']))
        if contrast.get('dark') is False:
            notes.append('浅色底上浅色强调色读不清，主色会自动调深一点保证可读——'
                         '想要更艳的版本，要求里写「深色底」或「暗色风」。')
    if contrast.get('contrast'):
        notes.append('正文对比度 %.2f:1（WCAG AA 要求 ≥ %.1f）。'
                     % (contrast['contrast'], MIN_CONTRAST))
    if typo_hits:
        notes.append('按你的要求改了：%s。' % '、'.join(typo_hits))
    if unsupported:
        notes.append('没做的部分：%s —— 菜单排列写死在四套骨架的 CSS 里，'
                     '这轮没动骨架，所以没换。' % '、'.join(unsupported))

    preset = dict(
        id=_design_id(chosen, info['requirement'], info['colors']),
        name=name,
        vibe=vibe,
        keywords=[str(item).lower() for item in info['keywords']][:12]
                  or list(base.get('keywords') or [])[:8],
        fit=['按用户要求定制'] + [item['name'] for item in info['matches'][:2]],
        palette=palette,
        typeface=dict(base['typeface'], title=font),
        layout=layout,
        surfaces=_surfaces(palette, radius, bool(info['dark'])),
        base=chosen,
        design=dict(
            requirement=info['requirement'],
            title=info['title'],
            colors=[item['hex'] for item in info['colors']],
            color_words=list(info['color_words']),
            image_colors=[item['hex'] for item in info['image_colors']],
            matched=[dict(id=item['id'], score=item['score'])
                     for item in info['matches'][:3]],
            base=chosen,
            contrast=contrast,
            references_used=info['references_used'],
            typo=typo_hits,
            unsupported=unsupported,
        ),
        notes=' '.join(notes),
    )
    return preset


def save(preset):
    """把设计好的预设落盘到 `designed/`，返回文件路径。

    同名会覆盖 —— id 是按内容和参考图算的，同样的输入重复设计只有一份文件。
    落盘后立刻塞进进程内缓存，否则 `get()` 还是拿旧的那份（缓存是首次加载时灌满的）。
    """
    from . import _cache  # 延迟导入：design 被 __init__ 反向导入

    DESIGNED_DIR.mkdir(parents=True, exist_ok=True)
    path = DESIGNED_DIR / ('%s.json' % preset['id'])
    path.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding='utf-8')
    _cache[preset['id']] = preset
    return path


def design(requirement='', references=(), base_id='', title=''):
    """一步到位：合成 + 落盘。返回 (预设, 文件路径)。"""
    preset = compose(requirement=requirement, references=references,
                     base_id=base_id, title=title)
    return preset, save(preset)
