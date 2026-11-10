# -*- coding: utf-8 -*-
r"""抓「双击到主界面」的全过程：直接录屏幕中心区域，不依赖窗口句柄。

这样能看到：桌面 → 原生启动画面 → 主窗口，以及中间有没有白闪。

用法：python _probe_boot.py [秒数]
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frames_boot')

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
CW, CH = 1700, 1150                      # 抓中心区域，够覆盖 splash 和主窗口中部
CX, CY = (SW - CW) // 2, (SH - CH) // 2


class BMIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


def grab(x, y, w, h):
    src = user32.GetDC(0)
    mdc = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, src, x, y, 0x00CC0020)
    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(0, src)
    if not got:
        return None
    a = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(a[:, :, ::-1])


def find_gal_windows():
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
        if '奇迹工坊' in buf.value:
            r = (ctypes.c_long * 4)()
            user32.GetWindowRect(hwnd, r)
            if (r[2] - r[0]) > 600:
                hits.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hits


def kill_stale():
    """清掉上一轮留下的实例；否则抓到的全是旧窗口。"""
    for hwnd in find_gal_windows():
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        print('清理旧实例 pid=%s' % pid.value)
        subprocess.run(['taskkill', '/F', '/PID', str(pid.value)], capture_output=True)
    time.sleep(1.0)
    if find_gal_windows():
        print('⚠ 旧窗口没清干净，结果会串')
        return False
    return True


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith('.png'):
            os.remove(os.path.join(OUT, f))

    if not kill_stale():
        return 1

    print('屏幕 %dx%d，抓中心区域 %dx%d @ (%d,%d)' % (SW, SH, CW, CH, CX, CY))
    t0 = time.time()
    subprocess.Popen([EXE], shell=False)
    print('exe 已拉起 t=0.000')

    frames = []
    while time.time() - t0 < secs:
        img = grab(CX, CY, CW, CH)
        if img is None:
            continue
        ts = time.time() - t0
        name = '%03d_%05d.png' % (len(frames), int(ts * 1000))
        img.save(os.path.join(OUT, name))
        frames.append((ts, name, img))

    print('\n共 %d 帧\n' % len(frames))
    print('%-8s %-20s %-9s %-7s %-8s %s' % ('t(s)', 'file', '主色', '亮度', '纯白%', '浅灰%'))
    prev = None
    for ts, name, img in frames:
        a = np.asarray(img.resize((100, 68)), dtype=np.float32)
        mean = a.reshape(-1, 3).mean(axis=0)
        lum = mean.mean()
        flat = a.reshape(-1, 3)
        white = float((flat.min(axis=1) > 245).mean())
        grey = float(((flat.min(axis=1) > 228) & (flat.max(axis=1) < 252)).mean())
        sig = (round(lum / 5), round(white, 2))
        mark = '' if prev is None or sig == prev else '  <== 变化'
        prev = sig
        print('%7.3f %-20s #%02x%02x%02x %7.1f %6.1f%% %6.1f%%%s'
              % (ts, name, int(mean[0]), int(mean[1]), int(mean[2]),
                 lum, white * 100, grey * 100, mark))


if __name__ == '__main__':
    main()
