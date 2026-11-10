# -*- coding: utf-8 -*-
r"""单独抓原生启动画面（类名 GalSplashWnd），PrintWindow 直接取内容，不受遮挡影响。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

EXE = r'C:\Users\q3153\Desktop_new\奇迹工坊.exe'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'splash.png')

u = ctypes.windll.user32
g = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 先清掉旧实例
hits = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return True
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    if '奇迹工坊' in b.value:
        hits.append(hwnd)
    return True


u.EnumWindows(cb, 0)
for hwnd in hits:
    pid = wt.DWORD(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    subprocess.run(['taskkill', '/F', '/PID', str(pid.value)], capture_output=True)
    print('清理 %s' % pid.value)
time.sleep(1.0)

subprocess.Popen([EXE])
print('已拉起，等启动画面…')

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


def shoot(hwnd):
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)          # PW_RENDERFULLCONTENT
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
    u.ReleaseDC(hwnd, hdc)
    if not got:
        return None
    a = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(a[:, :, ::-1]), (w, h)


splash = None
t0 = time.time()
while time.time() - t0 < 12:
    h = u.FindWindowW('GalSplashWnd', None)
    if h:
        splash = h
        print('找到启动画面 t=%.3f' % (time.time() - t0))
        break
    time.sleep(0.03)

if not splash:
    print('没找到启动画面窗口（可能主窗口已经出来了）')
    sys.exit(1)

time.sleep(0.25)
res = shoot(splash)
if not res:
    print('截取失败')
    sys.exit(1)
img, size = res
img.save(OUT)
print('已存 %s  尺寸 %dx%d' % (OUT, size[0], size[1]))
