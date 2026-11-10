"""生成「奇迹工坊」的两个图标：软件内（界面 logo）与软件外（exe 图标）。

粉色主调 + Q 版二次元。百炼 qwen-image 不支持原生透明底，
所以先出纯白背景，再由本脚本抠图（复用项目自己的 cutout）。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.store import Credentials                        # noqa: E402
from app.providers.image import DashScopeImageProvider    # noqa: E402
from app.assets.cutout import remove_white_background     # noqa: E402

OUT = ROOT / 'ui' / 'brand'
OUT.mkdir(parents=True, exist_ok=True)

# 软件内：细节多一点，适合 128~512px 显示
INNER = (
    'Q版二次元风格，粉色主色调。一个可爱的魔法少女，粉色双马尾，大大的眼睛闪着高光，'
    '戴着白色小圆帽，帽檐上缀着一颗粉色星星，脸颊有淡淡的红晕，开心地微笑，'
    '双手捧着一颗发光的粉色宝石。圆润柔和的线条，赛璐璐上色，'
    '纯白色背景，正面居中构图，四周留出均匀留白。'
    '适合做软件图标，画面中不要出现任何文字、字母、水印、边框。'
)

# 软件外：缩到 16px 还要认得出来，主体更大、造型更简
OUTER = (
    'Q版二次元风格应用图标，粉色主色调。一个魔法少女的头像特写，占满整个画面，'
    '粉色双马尾，又大又亮的眼睛，造型简化干净，轮廓清晰，对比强烈，'
    '纯白色背景，正面。适合做桌面快捷方式的图标，'
    '画面中不要出现任何文字、字母、水印、边框、多余装饰。'
)


async def main():
    cfg = Credentials().read()['qwen_image']
    provider = DashScopeImageProvider(cfg)
    for name, prompt in (('软件内', INNER), ('软件外', OUTER)):
        print(f'生成{name}图标…', flush=True)
        raw = await provider.call(prompt, 1024, 1024)
        original = OUT / f'{name}-原图.png'
        original.write_bytes(raw)
        cut = await asyncio.to_thread(remove_white_background, raw)
        cut_path = OUT / f'{name}-透明.png'
        cut_path.write_bytes(cut)
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(raw)) as a, Image.open(BytesIO(cut)) as b:
            print(f'  {name}: 原图 {a.size} {a.mode} → 透明 {b.size} {b.mode}', flush=True)
        print(f'  存到 {original.name} / {cut_path.name}', flush=True)


asyncio.run(main())
