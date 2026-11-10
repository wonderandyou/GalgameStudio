"""逐套截启动界面 + 拼 2×2 总览。

用法：
    python tools/_shot_titles.py

前置：ui/title-lab.html 已存在；Edge 在固定路径。
输出：ui/assets/title/shot_v{1..4}.png  +  shot_all.png
"""
import pathlib
import subprocess
import sys
import time

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
UI = pathlib.Path(r'D:\软件\GalgameStudio\ui')
OUT = UI / 'assets' / 'title'
PAGE = UI / 'title-lab.html'
W, H, SCALE = 1600, 900, 2

NAMES = {1: '方案A · 水墨直系', 2: '方案B · 暗色电影感',
         3: '方案C · 明亮 indie', 4: '方案D · 冷色剧场'}


def shot(v):
    out = OUT / f'shot_v{v}.png'
    if out.exists():
        out.unlink()
    prof = OUT / f'_prof_v{v}'
    url = f'file:///{str(PAGE).replace(chr(92), "/")}?v={v}&hud=0'
    cmd = [EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
           '--no-default-browser-check', '--hide-scrollbars',
           f'--force-device-scale-factor={SCALE}',
           '--virtual-time-budget=4000',
           f'--window-size={W},{H}', f'--user-data-dir={prof}',
           f'--screenshot={out}', url]
    t0 = time.time()
    # 关键：绝不能用 capture_output —— Edge 输出多，管道满会死锁卡死
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=120)
    ok = out.exists() and out.stat().st_size > 20000
    print(f"[{'OK  ' if ok else 'FAIL'}] v{v} {NAMES[v]:<16} "
          f"{(out.stat().st_size/1024 if out.exists() else 0):.0f}KB "
          f"{time.time()-t0:.1f}s rc={proc.returncode}")
    return ok


def collage():
    from PIL import Image
    tiles = []
    for v in (1, 2, 3, 4):
        p = OUT / f'shot_v{v}.png'
        if not p.exists():
            print(f'[拼图] 缺 {p.name}，跳过')
            return False
        tiles.append(Image.open(p).convert('RGB'))
    tw = 1280
    th = int(tiles[0].height * tw / tiles[0].width)
    small = [t.resize((tw, th), Image.LANCZOS) for t in tiles]
    gap, pad, head = 18, 22, 46
    cw = tw * 2 + gap + pad * 2
    ch = (th + head) * 2 + gap + pad * 2
    canvas = Image.new('RGB', (cw, ch), (24, 26, 30))
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyhbd.ttc', 26)
    except OSError:
        font = ImageFont.load_default()
    for i, (img, v) in enumerate(zip(small, (1, 2, 3, 4))):
        col, row = i % 2, i // 2
        x = pad + col * (tw + gap)
        y = pad + row * (th + head + gap)
        draw.text((x + 2, y + 8), NAMES[v], fill=(226, 230, 236), font=font)
        canvas.paste(img, (x, y + head))
        draw.rectangle([x - 1, y + head - 1, x + tw, y + head + th],
                       outline=(70, 76, 86), width=1)
    path = OUT / 'shot_all.png'
    canvas.save(path, quality=92)
    print(f"[OK] 总览 {path}  {canvas.width}×{canvas.height}  "
          f"{path.stat().st_size/1024:.0f}KB")
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f'[开始] 截图 {W}×{H} @{SCALE}x')
    ok = all([shot(v) for v in (1, 2, 3, 4)])
    if not ok:
        print('[警告] 有截图失败')
    collage()
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
