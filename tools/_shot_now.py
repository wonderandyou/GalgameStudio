# -*- coding: utf-8 -*-
r"""一条命令内完成：启动 → 等待 → 找到窗口 → 截真实画面 → 存图。

命令行启动的 exe 会在命令结束时被 job runner 清掉，所以必须同一个进程里做完。
用法：python _shot_now.py [等待秒数]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import Image

EXE = r'C:\Users\q3153\Desktop_new\奇迹工坊.exe'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'now.png')

u = ctypes.windll.user32
g = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class BMIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


def find_window():
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if '奇迹工坊' in b.value:
            r = RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 600:
                hits.append((h, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(cb, 0)
    return hits


def grab(x, y, w, h):
    src = u.GetDC(0)
    mdc = g.CreateCompatibleDC(src)
    bmp = g.CreateCompatibleBitmap(src, w, h)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, w, h, src, x, y, 0x00CC0020)
    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    got = g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(0, src)
    if not got:
        return None
    import numpy as np
    a = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(a[:, :, ::-1])


# 清掉旧实例
for hwnd, x, y, w, h in find_window():
    pid = wt.DWORD(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    subprocess.run(['taskkill', '/F', '/PID', str(pid.value)], capture_output=True)
    print('清理旧实例 %s' % pid.value)
time.sleep(1.0)

wait = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
subprocess.Popen([EXE])
print('已拉起，等 %.1f 秒…' % wait)
time.sleep(wait)

hits = find_window()
if not hits:
    print('没找到窗口')
    sys.exit(1)
hwnd, x, y, w, h = hits[0]
print('窗口 (%d,%d) %dx%d' % (x, y, w, h))
u.SetForegroundWindow(hwnd)
time.sleep(0.8)

img = grab(x, y, w, h)
if img is None:
    print('截图失败')
    sys.exit(1)
img.save(OUT)
print('saved %s' % OUT)
