# -*- coding: utf-8 -*-
r"""看布局：加载 p_63b2b758e0ab46d1bf4201629468eb36（200 节点），截「全图」和「单章」两张。

跑法（要用带 pywebview 的系统 Python）：
  C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe tools\_shot_layout.py
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
OUT_JSON = os.path.join(HERE, 'layout_shot.json')
OUT_ALL = os.path.join(HERE, 'layout_all.png')
OUT_CH = os.path.join(HERE, 'layout_chapter.png')
TITLE = 'layout-shot'
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/'

u = ctypes.windll.user32
g = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

holder = {}

LOAD_JS = r"""
(async function () {
  try {
    GalAPI.mode = 'real';
    localStorage.setItem('gal_mode', 'real');
    await new Promise(function (r) { setTimeout(r, 200); });
    var gg = await GalAPI.getGraph('p_63b2b758e0ab46d1bf4201629468eb36');
    if (!gg) { window.__r = 'ERR: getGraph 返回 null'; return; }
    document.querySelector('[data-view="editor"]').click();
    await new Promise(function (r) { setTimeout(r, 300); });
    window.GalFrontendTest.applyGraph(gg);
    await new Promise(function (r) { setTimeout(r, 900); });
    var xs = gg.nodes.map(function (n) { return n.pos.x; });
    var ys = gg.nodes.map(function (n) { return n.pos.y; });
    window.__r = JSON.stringify({
      nodes: gg.nodes.length,
      chapters: (gg.chapters || []).length,
      xMin: Math.min.apply(null, xs), xMax: Math.max.apply(null, xs),
      yMin: Math.min.apply(null, ys), yMax: Math.max.apply(null, ys),
      nodeRows: document.querySelectorAll('.gal-node').length,
      chapterRows: document.querySelectorAll('.chapter-row').length
    });
  } catch (e) {
    window.__r = 'ERR: ' + (e && (e.stack || e.message) || e);
  }
})();
"""

PICK_CHAPTER_JS = r"""
(function () {
  var rows = document.querySelectorAll('.chapter-row');
  if (!rows.length) return 'no-chapter-rows';
  rows[0].click();
  return 'clicked:' + rows.length;
})();
"""

STATE_JS = r"""
(function () {
  var st = window.GalFrontendTest.state();
  return JSON.stringify({ nodes: st.nodes, chapter: st.activeChapterId,
                          visible: window.__galVisibleGraph || null,
                          domNodes: document.querySelectorAll('.gal-node').length });
})();
"""


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


def wait_js(win, key, timeout=25):
    for _ in range(int(timeout * 5)):
        time.sleep(0.2)
        try:
            v = win.evaluate_js('window.%s || ""' % key)
        except Exception:
            continue
        if v:
            return v
    return ''


def shoot(name):
    hits = find_by_title(TITLE)
    if not hits:
        return
    hwnd, x, y, w, h = hits[0]
    img = grab_window(hwnd, w, h)
    if img is not None:
        img.save(name)
        print('截图:', name, flush=True)


def main():
    log = {}

    def run():
        time.sleep(3.5)
        win = holder['win']
        win.evaluate_js(LOAD_JS)
        log['all'] = wait_js(win, '__r', 30)
        print('全图:', log['all'], flush=True)
        time.sleep(1.2)
        shoot(OUT_ALL)

        r = win.evaluate_js(PICK_CHAPTER_JS)
        print('点章节:', r, flush=True)
        time.sleep(1.2)
        log['chapter'] = win.evaluate_js(STATE_JS)
        log['fitlog'] = win.evaluate_js('JSON.stringify(window.__fitLog||{})')
        print('fit:', log['fitlog'], flush=True)
        print('单章状态:', log['chapter'], flush=True)
        shoot(OUT_CH)

        with open(OUT_JSON, 'w', encoding='utf-8') as fh:
            json.dump(log, fh, ensure_ascii=False, indent=1)
        os._exit(0)

    def start():
        time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    holder['win'] = webview.create_window(TITLE, URL, width=1500, height=940)
    threading.Thread(target=start, daemon=True).start()
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()


