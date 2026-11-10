# -*- coding: utf-8 -*-
r"""跑 Kimi 自带的 frontend-verify.js 验收，读回真实结果。

用法：python _run_frontend_e2e.py [url]
结果写 tools\frontend_e2e.json，截图 tools\frontend_e2e.png。
"""
import ctypes
import json
import os
import sys
import threading
import time

import numpy as np
from PIL import Image

import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shot_view import BMIH, find_by_title          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, 'frontend_e2e.json')
OUT_PNG = os.path.join(HERE, 'frontend_e2e.png')
TITLE = 'fe-verify'
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/?frontend-e2e=1'

u = ctypes.windll.user32
g = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

holder = {}


def grab_window(hwnd, w, h):
    src = u.GetWindowDC(hwnd)
    if not src:
        return None
    mdc = g.CreateCompatibleDC(src)
    bmp = g.CreateCompatibleBitmap(src, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)
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
    u.ReleaseDC(hwnd, src)
    if not ok or not got:
        return None
    a = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))[:, :, :3]
    return Image.fromarray(a[:, :, ::-1])


def main():
    def run():
        time.sleep(3.0)
        win = holder['win']

        def ev(js):
            try:
                return win.evaluate_js(js)
            except Exception as e:
                return '__ERR__%r' % (e,)

        # 先确认新代码有没有被加载（防止命中旧缓存）
        probe = ev("JSON.stringify({hasChapterPanel: !!document.getElementById('chapterPanel'),"
                   " hasVerify: !!window.GalFrontendTest,"
                   " appLen: (document.querySelector('script[src*=app\\.js]')||{}).src || ''})")
        print('probe:', probe, flush=True)

        deadline = time.time() + 120
        title = ''
        while time.time() < deadline:
            t = ev('document.title')
            if t == 'FRONTEND_E2E_DONE':
                title = t
                break
            time.sleep(1.0)

        result = ev("(function(){var p=document.getElementById('frontend-results');"
                    "return p ? p.textContent : '__NO_RESULTS__';})()")
        print('title:', title, flush=True)
        with open(OUT_JSON, 'w', encoding='utf-8') as fh:
            fh.write(result if isinstance(result, str) else repr(result))
        print('结果已写:', OUT_JSON, flush=True)

        hits = find_by_title(TITLE)
        if hits:
            hwnd, x, y, w, h = hits[0]
            img = grab_window(hwnd, w, h)
            if img is not None:
                img.save(OUT_PNG)
                print('截图:', OUT_PNG, flush=True)
        os._exit(0)

    def start():
        time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    holder['win'] = webview.create_window(TITLE, URL, width=1400, height=880)
    threading.Thread(target=start, daemon=True).start()
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()
