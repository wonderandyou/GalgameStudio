"""从立绘里提取人物，输出透明 PNG。

三层，按质量从高到低：

1. ★ **原生透明直通**：生图供应商若真给了带 alpha 的 PNG（或透明调色板），
   直接原样返回 —— 比任何后处理都干净，发丝边缘不会有算法痕迹。
   目前百炼 qwen-image 系列实测输出 RGB（无 alpha），这一层只是**预留能力**，
   换成本地带透明输出的模型时立刻生效，不需要改调用方。
2. ★ **isnet-anime 抠图模型**（rembg + onnxruntime），模型放项目里
   `models/isnet-anime.onnx`（167.9 MB），不依赖用户目录，拷走项目就带着走。
   动漫立绘专用，头发边缘不会留白描边。
3. **兜底：自适应色差阈值法**。原来的实现假定「背景是纯白 236+」，
   背景一旦偏灰 / 偏暖 / 有一点渐变就抠不干净（主人实测发现小瑕疵）。
   现在改成：先**从四边估计真实背景色**，再用「与该背景色的最大通道差」
   做 Otsu 自适应阈值 + 从四边 floodfill 定背景，最后按估计出的背景色
   反解边缘混色（去白边/去色溢）。背景是白、浅灰、淡蓝都能处理。

⚠ 为什么背景仍然要求「纯色」而不是让模型直接出透明底：见
`app/providers/image.py` 的说明 —— 百炼这条通道没有透明背景参数。
所以**提示词约束纯色背景 + 这里自适应抠图**才是当前能落地的路。
"""
from io import BytesIO
import os
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[2] / 'models' / 'isnet-anime.onnx'
MODEL_NAME = 'isnet-anime'

_session = None
_session_failed = False

# 判定「已经是透明图」的最小不透明像素占比：全透明的空图不算可用结果。
_MIN_OPAQUE_RATIO = 0.02


def _get_session():
    """懒加载并缓存模型会话；加载失败只记一次，之后直接走兜底。

    ★ rembg 2.0.87 的 `new_session` **没有** `model_path` 参数（签名是
      `new_session(model_name, *args, sess_opts=None, **kwargs)`，多出来的
      kwargs 只会一路传到 `BaseSession.download_models`），而模型路径是
      由 `rembg_home()` 决定的：**只认 `U2NET_HOME` / `REMBG_HOME` / `~/.rembg`**。
      所以以前写 `new_session(MODEL_NAME, model_path=str(MODEL_PATH))` 等于没说 ——
      项目里明明躺着 `models/isnet-anime.onnx`（176 MB），它还是去 GitHub 下，
      下不动就静默降级成阈值抠图（白底图边缘泛白）。
      现在把 `U2NET_HOME` **临时**指到项目 `models/` 目录：rembg 的 legacy 平铺候选
      正好是 `<U2NET_HOME>/isnet-anime.onnx`，一次命中，不联网。
      加载完立刻还原，不改用户机器的环境。
    """
    global _session, _session_failed
    if _session is not None:
        return _session
    if _session_failed:
        return None
    previous = os.environ.get('U2NET_HOME')
    try:
        from rembg import new_session
        if MODEL_PATH.is_file():
            os.environ['U2NET_HOME'] = str(MODEL_PATH.parent)
        try:
            _session = new_session(MODEL_NAME)
        finally:
            if previous is None:
                os.environ.pop('U2NET_HOME', None)
            else:
                os.environ['U2NET_HOME'] = previous
        return _session
    except Exception:                                          # noqa: BLE001
        # 模型不在 / 依赖没装 / onnxruntime 报错都归到这里，交给阈值法兜底
        if previous is not None:
            os.environ['U2NET_HOME'] = previous
        _session_failed = True
        return None


def has_alpha(data):
    """这张图是不是真的带透明区域（而不是只有名字叫 PNG）。"""
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            return _alpha_usable(image)
    except Exception:                                          # noqa: BLE001
        return False


def _alpha_usable(image):
    """判断已打开的图是否含可用透明区域。

    「全透明」不算可用：那是把主体也抠没了，比抠不干净更糟。
    用直方图统计而不是逐像素遍历 —— 1920×1080 逐像素要 0.3 秒以上。
    """
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        alpha = image.convert('RGBA').getchannel('A')
        lo, hi = alpha.getextrema()
        if lo < 250:
            histogram = alpha.histogram()
            opaque = sum(histogram[129:])
            if opaque >= alpha.width * alpha.height * _MIN_OPAQUE_RATIO:
                return True
    return False


def _model_cutout(data):
    """模型抠图。返回 bytes；不可用或结果异常返回 None。"""
    if not MODEL_PATH.is_file():
        return None
    session = _get_session()
    if session is None:
        return None
    try:
        from rembg import remove
        out = remove(data, session=session)
    except Exception:                                          # noqa: BLE001
        return None
    if not out:
        return None
    # 结果必须真的带透明区域，否则说明模型没起作用，交给兜底
    try:
        from PIL import Image
        with Image.open(BytesIO(out)) as check:
            if check.mode != 'RGBA':
                return None
            if not _alpha_usable(check):
                return None
    except Exception:                                          # noqa: BLE001
        return None
    return out


def estimate_background(pixels, width, height, step=1):
    """从图片四边估计背景色（RGB 三元组）。

    取四边像素每个通道的**中位数**而不是均值：
    主体偶尔会贴边（半身立绘肩膀顶到画面边），均值会被拉偏，中位数不受影响。
    """
    reds, greens, blues = [], [], []
    step = max(1, int(step))
    for x in range(0, width, step):
        for y in (0, height - 1):
            r, g, b, a = pixels[x, y]
            if a > 8:
                reds.append(r); greens.append(g); blues.append(b)
    for y in range(0, height, step):
        for x in (0, width - 1):
            r, g, b, a = pixels[x, y]
            if a > 8:
                reds.append(r); greens.append(g); blues.append(b)
    if not reds:
        return (255, 255, 255)

    def median(values):
        values = sorted(values)
        return values[len(values) // 2]

    return (median(reds), median(greens), median(blues))


def _color_distance(r, g, b, background):
    """与背景色的最大单通道差。

    用 Chebyshev（最大通道差）而不是欧氏距离：背景偏黄时，人物皮肤在欧氏距离上
    可能离背景很近，但蓝色通道差得很开 —— 取最大通道差能把这类主体保住。
    """
    br, bg_, bb = background
    return max(abs(r - br), abs(g - bg_), abs(b - bb))


def _otsu_threshold(histogram, low=10, high=110):
    """按距离直方图求分割阈值（Otsu），并夹到 [low, high]。"""
    total = sum(histogram)
    if total <= 0:
        return low
    total_sum = sum(i * histogram[i] for i in range(256))
    weight_bg = 0
    sum_bg = 0.0
    best, best_value = low, -1.0
    for threshold in range(256):
        weight_bg += histogram[threshold]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += threshold * histogram[threshold]
        mean_bg = sum_bg / weight_bg
        mean_fg = (total_sum - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_value:
            best_value = between
            best = threshold
    return max(low, min(high, best))


def _threshold_cutout(data):
    """自适应色差阈值法（兜底）。

    与旧实现的差别：背景色不再写死白色，而是**现场估计**；
    阈值不再写死 236，而是按这张图自己的距离直方图求 Otsu。
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

    try:
        with Image.open(BytesIO(data), formats=('PNG', 'JPEG')) as image:
            rgba = image.convert('RGBA')
    except Image.DecompressionBombError as exc:
        raise ValueError('立绘像素尺寸过大') from exc

    if _alpha_usable(rgba):
        out = BytesIO()
        rgba.save(out, format='PNG')
        return out.getvalue()

    width, height = rgba.size
    pixels = rgba.load()
    background = estimate_background(pixels, width, height,
                                     step=max(1, min(width, height) // 48))

    # 1) 距离图：每个像素离背景色有多远（0=就是背景色）
    distance = Image.new('L', (width, height))
    target = distance.load()
    histogram = [0] * 256
    for y in range(height):
        for x in range(width):
            value = min(255, _color_distance(*pixels[x, y][:3], background))
            target[x, y] = value
            histogram[value] += 1
    threshold = _otsu_threshold(histogram)

    # 2) 只在「从四边能连过来的背景区域」内判背景：
    #    人物内部的白衣、眼白即使离背景色很近也不会被抠掉。
    near_background = distance.point(lambda value: 255 if value <= threshold else 0)
    padded = ImageOps.expand(near_background, border=1, fill=255)
    ImageDraw.floodfill(padded, (0, 0), 128)
    alpha = padded.crop((1, 1, width + 1, height + 1)).point(
        lambda value: 0 if value == 128 else 255)
    if alpha.getextrema() != (0, 255):
        # 整张图都被判成背景（或都没判成）：说明主体与背景对比不足
        raise ValueError('立绘需要可分离的纯色背景和人物主体')

    # 3) 形态学开运算（先缩后扩）：吃掉轮廓外沿那 1~2 px 的背景色描边，
    #    又不会让人物整体变小。
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))

    # 4) 边缘去色溢：只在紧贴背景的 1–2 px 里做。
    #    用**估计出的背景色**反解，而不是写死白色 —— 背景偏灰/偏暖时也能去干净。
    band = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(5)))
    local_distance = distance.filter(ImageFilter.MinFilter(5))
    darkest = local_distance.load()
    edge = band.load()
    rgba.putalpha(alpha)
    pixels = rgba.load()
    br, bg_, bb = background
    for y in range(height):
        for x in range(width):
            if not edge[x, y]:
                continue
            local = darkest[x, y]
            if local < 3:
                continue
            # coverage：主体在这块边缘像素里占的比例（越靠近背景色越小）
            coverage = 255 * local / max(3, local + threshold)
            coverage = max(1, min(255, round(coverage)))
            pixel = pixels[x, y]
            ratio = 255 / coverage
            rgb = tuple(max(0, min(255, round((value - bgv * (255 - coverage) / 255) * ratio)))
                        for value, bgv in zip(pixel[:3], (br, bg_, bb)))
            pixels[x, y] = (*rgb, coverage)

    # 5) 羽化 0.8 px：深色背景上不出现硬锯齿（0.6 对发丝略生硬）
    #    ★ P2-1：羽化会把 alpha 往外扩 1–2 px，落到原本 alpha=0 的背景像素上 ——
    #      这些像素的 RGB 仍是背景色（白底就是纯白）。以前直接 blur 等于把第 4 步刚去掉的
    #      白边又混了回来（256px 白底红圆实测：边缘 1112 个纯白半透明像素，叠黑底一圈粉白光环）。
    #      现在：羽化后的新 alpha 不变，但「羽化新长出来的像素」改用邻近前景的颜色上色
    #      （_bleed_foreground_color），原本就在前景里的像素颜色不动（已在第 4 步反解过）。
    hard = rgba.getchannel('A')
    soft = hard.filter(ImageFilter.GaussianBlur(0.8))
    _bleed_foreground_color(rgba, hard, soft)
    rgba.putalpha(soft)

    out = BytesIO()
    rgba.save(out, format='PNG')
    return out.getvalue()


def _bleed_foreground_color(rgba, hard, soft, radius=3):
    """给「羽化后才变成半透明」的像素换上邻近前景的颜色（就地修改 rgba 和 soft）。

    hard：羽化前的 alpha；soft：羽化后的 alpha。
    只处理 hard == 0 且 soft > 0 的像素 —— 它们原本是背景，RGB 是背景色，
    按 alpha 反解也解不出前景色（I == B 时 (I - B·(1-a))/a 仍等于 B），
    所以取 radius 内 hard > 0 的前景像素、按 hard 加权平均它们的 RGB。
    radius 内一个前景像素都没有（理论上 0.8 px 羽化不会出现）就把 soft 归零，
    宁可少一圈羽化也不留背景色。
    """
    width, height = rgba.size
    hard_px = hard.load()
    soft_px = soft.load()
    pixels = rgba.load()
    hard_data = hard.tobytes()            # L 模式一像素一字节；getdata() 在新 Pillow 里已弃用
    soft_data = soft.tobytes()
    for index, (h, s) in enumerate(zip(hard_data, soft_data)):
        if h or not s:
            continue
        y, x = divmod(index, width)
        total = red = green = blue = 0
        for ny in range(max(0, y - radius), min(height, y + radius + 1)):
            for nx in range(max(0, x - radius), min(width, x + radius + 1)):
                weight = hard_px[nx, ny]
                if weight:
                    r, g, b, _a = pixels[nx, ny]
                    total += weight
                    red += r * weight; green += g * weight; blue += b * weight
        if total:
            pixels[x, y] = (round(red / total), round(green / total),
                            round(blue / total), pixels[x, y][3])
        else:
            soft_px[x, y] = 0


def remove_white_background(data):
    """抠出人物（无背景）。

    顺序：原生透明直通 → 模型抠图 → 自适应色差阈值法。
    任何一层失败都往下走，保证「没有模型也能出图」。
    """
    if has_alpha(data):
        return data
    result = _model_cutout(data)
    if result is not None:
        return result
    return _threshold_cutout(data)
