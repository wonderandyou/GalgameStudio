# -*- coding: utf-8 -*-
r"""用 pywebview 单独打开某个 URL 并截真实的窗口画面（不经过 galapp）。

用来验证页面改动，不依赖启动器那套。
用法：python _shot_view.py "<url>" [等待秒数] [标题]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

import numpy as np
from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'view.png')
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


def find_by_title(mark):
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
        if mark in b.value:
            r = RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 400:
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
    a = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(a[:, :, ::-1])


def main():
    url = sys.argv[1]
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    title = sys.argv[3] if len(sys.argv) > 3 else 'shot-view'
    js = sys.argv[4] if len(sys.argv) > 4 else ''
    js_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'view_js.txt')

    import webview

    holder = {}

    def run_js_then_shoot():
        time.sleep(wait)
        if js:
            try:
                res = holder['win'].evaluate_js(js)
                with open(js_out, 'w', encoding='utf-8') as fh:
                    fh.write('JS 结果: %r\n' % (res,))
                time.sleep(3.5)          # 留时间让异步点击/跳转/渲染跑完
                try:
                    ttl = holder['win'].evaluate_js('document.title')
                    with open(js_out, 'a', encoding='utf-8') as fh:
                        fh.write('title: %s\n' % ttl)
                except Exception as exc2:
                    with open(js_out, 'a', encoding='utf-8') as fh:
                        fh.write('读 title 失败: %r\n' % (exc2,))
            except Exception as exc:
                with open(js_out, 'w', encoding='utf-8') as fh:
                    fh.write('JS 失败: %r\n' % (exc,))
        hits = find_by_title(title)
        if not hits:
            with open(js_out, 'a', encoding='utf-8') as fh:
                fh.write('没找到窗口\n')
            os._exit(1)
        hwnd, x, y, w, h = hits[0]
        with open(js_out, 'a', encoding='utf-8') as fh:
            fh.write('窗口 (%d,%d) %dx%d\n' % (x, y, w, h))
        u.SetForegroundWindow(hwnd)
        time.sleep(0.6)
        img = grab(x, y, w, h)
        if img is None:
            os._exit(1)
        img.save(OUT)
        with open(js_out, 'a', encoding='utf-8') as fh:
            fh.write('saved %s\n' % OUT)
        os._exit(0)

    def start():
        time.sleep(0.8)
        threading.Thread(target=run_js_then_shoot, daemon=True).start()

    holder['win'] = webview.create_window(title, url, width=1400, height=880)
    threading.Thread(target=start, daemon=True).start()
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()



