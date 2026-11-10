# -*- coding: utf-8 -*-
r"""逐帧抓取奇迹工坊启动过程，判断有没有白闪、多久才出内容。

用法：python _probe_frames.py [秒数]
产出：tools\frames\NNN.png + 控制台时间线
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

EXE = r'C:\Users\q3153\Desktop_new\奇迹工坊.exe'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frames')
TITLE_PART = '奇迹工坊'

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

PW_RENDERFULLCONTENT = 2


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


def find_window():
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if TITLE_PART in buf.value:
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 600 and h > 400:
                hits.append((hwnd, buf.value, w, h))
        return True

    user32.EnumWindows(cb, 0)
    return hits


def grab_screen(x, y, w, h):
    """从桌面 DC 抓区域 —— WebView2 用 PrintWindow 抓不到内容，只能抓屏幕。"""
    src = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, src, x, y, 0x00CC0020)  # SRCCOPY

    class BMIH(ctypes.Structure):
        _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long),
                    ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                    ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                    ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD),
                    ('biClrImportant', wt.DWORD)]

    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth = w
    bi.biHeight = -h          # 负数 = 从上到下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0      # BI_RGB

    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, src)
    if not got:
        return None
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(arr[:, :, ::-1])  # BGR → RGB


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith('.png'):
            os.remove(os.path.join(OUT, f))

    # 先清掉还在跑的实例 —— 按「拥有该窗口的 PID」精确杀，不做名字/cmdline 匹配
    for hwnd, title, w, h in find_window():
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        print('清理旧实例 pid=%s %r' % (pid.value, title))
        subprocess.run(['taskkill', '/F', '/PID', str(pid.value)], capture_output=True)
    time.sleep(1.0)
    left = find_window()
    if left:
        print('⚠ 旧窗口没清干净：%r —— 结果会串，先停' % (left,))
        return

    t0 = time.time()
    subprocess.Popen([EXE], shell=False)
    print('exe 已拉起 t=0.000')

    frames = []
    hwnd_used = None
    deadline = t0 + secs
    first_seen = None
    while time.time() < deadline:
        ts = time.time() - t0
        if hwnd_used is None or not user32.IsWindow(hwnd_used):
            hits = find_window()
            if not hits:
                time.sleep(0.03)
                continue
            hwnd_used, title, w, h = hits[0]
            first_seen = ts
            print('窗口出现 t=%.3f  %dx%d  %r' % (ts, w, h, title))
            # 置顶 + 前台，避免被 DSH 挡住（抓屏幕必须真的在最上面）
            user32.SetWindowPos(hwnd_used, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
            user32.SetForegroundWindow(hwnd_used)
        r = RECT()
        user32.GetWindowRect(hwnd_used, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            continue
        img = grab_screen(r.left, r.top, w, h)
        if img is None:
            time.sleep(0.02)
            continue
        name = '%03d_%05d.png' % (len(frames), int((time.time() - t0) * 1000))
        img.save(os.path.join(OUT, name))
        frames.append((time.time() - t0, name, img))

    print('\n共抓 %d 帧，窗口首次出现 t=%.3f' % (len(frames), first_seen or -1))
    print('\n%-9s %-22s %-8s %-8s %-8s' % ('t(s)', 'file', '平均色', '亮度', '纯白占比'))
    prev_sig = None
    for ts, name, img in frames:
        a = np.asarray(img.resize((80, 50)), dtype=np.float32)
        mean = a.reshape(-1, 3).mean(axis=0)
        lum = mean.mean()
        white = float((a.min(axis=2) > 245).mean())
        sig = (round(lum / 6), round(white, 2))
        mark = ''
        if prev_sig is not None and sig != prev_sig:
            mark = '  <== 画面变化'
        prev_sig = sig
        print('%8.3f %-22s #%02x%02x%02x %7.1f %7.1f%%%s'
              % (ts, name, int(mean[0]), int(mean[1]), int(mean[2]), lum, white * 100, mark))


if __name__ == '__main__':
    main()
