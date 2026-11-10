# -*- coding: utf-8 -*-
r"""端到端：前端加载一个真实生成的项目，检查章节树是否按真实数据渲染。

用法：python _run_real_project_e2e.py
结果写 tools\real_project_e2e.json，截图 tools\real_project_e2e.png。
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
OUT_JSON = os.path.join(HERE, 'real_project_e2e.json')
OUT_PNG = os.path.join(HERE, 'real_project_e2e.png')
TITLE = 'real-project'
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
    GalAPI.mode = 'real';          // 前端默认 mock，不切的话 getGraph 直接返回 null
    var ps = await GalAPI.listProjects();
    if (!ps || !ps.length) { window.__real = 'ERR: 没有项目'; return; }
    var p = ps.sort(function (a, b) { return (b.node_count||0) - (a.node_count||0); })[0];
    var gg = await GalAPI.getGraph(p.id);
    document.querySelector('[data-view="editor"]').click();
    await new Promise(function (r) { setTimeout(r, 250); });
    window.GalFrontendTest.applyGraph(gg);
    await new Promise(function (r) { setTimeout(r, 500); });
    var acts = {};
    document.querySelectorAll('.act-row').forEach(function (el) { acts[el.textContent.trim()] = 1; });
    window.__real = JSON.stringify({
      project: p.name,
      projectId: p.id,
      nodesInGraph: (gg.nodes || []).length,
      chaptersInGraph: (gg.chapters || []).length,
      state: window.GalFrontendTest.state(),
      actRows: document.querySelectorAll('.act-row').length,
      chapterRows: document.querySelectorAll('.chapter-row').length,
      actTitles: Object.keys(acts).slice(0, 4),
      panelHidden: document.getElementById('chapterPanel').hidden,
      firstChapterText: (document.querySelector('.chapter-row') || {}).textContent || ''
    });
  } catch (e) {
    window.__real = 'ERR: ' + (e && (e.stack || e.message) || e);
  }
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


def main():
    def run():
        time.sleep(3.5)
        win = holder['win']
        try:
            win.evaluate_js(LOAD_JS)
        except Exception as e:
            print('注入失败:', repr(e), flush=True)

        result = None
        for _ in range(60):
            time.sleep(1.0)
            try:
                r = win.evaluate_js('window.__real || ""')
            except Exception:
                continue
            if r:
                result = r
                break

        print('结果:', result, flush=True)
        with open(OUT_JSON, 'w', encoding='utf-8') as fh:
            fh.write(result if isinstance(result, str) else '(空)')

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
