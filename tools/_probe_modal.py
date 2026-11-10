# -*- coding: utf-8 -*-
r"""实测：双击节点改台词是否已经走应用内浮层（不再弹 WebView2 原生 prompt）。

用法：python _probe_modal.py [url]
结果写 tools\modal_js.txt，截图 tools\modal.png。
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
OUT = os.path.join(HERE, 'modal.png')
RES = os.path.join(HERE, 'modal_js.txt')
TITLE = 'probe-modal'
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/?view=editor'

u = ctypes.windll.user32
g = ctypes.windll.gdi32

holder = {}
lines = []


def grab_window(hwnd, w, h):
    """直接从窗口自己的 DC 拷像素（PW_RENDERFULLCONTENT），不要求它在最前面。"""
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


def note(tag, payload):
    lines.append('%-8s %s' % (tag, payload))


def main():
    def run():
        time.sleep(4.2)
        win = holder['win']

        def ev(js, tag):
            try:
                r = win.evaluate_js(js)
            except Exception as e:                       # 原生框会直接卡死，这里能捕获到超时
                note(tag, 'EXC %r' % (e,))
                return None
            note(tag, r)
            try:
                return json.loads(r)
            except Exception:
                return r

        # 0) 守卫装上了吗
        ev('JSON.stringify({guard: !!window.__galDialogGuard, editor: !!window.GalEditor,'
           ' promptNative: String(window.prompt).indexOf("native code") >= 0})', 'boot')

        # 1) 走真实入口：LiteGraph 双击节点时调的就是 api.onDouble(node)
        ev('(function(){var d=window.GalEditor.dump();var nid=d.nodes[0].id;'
           'window.GalEditor.api.onDouble({properties:{galId:nid}});'
           'var m=document.querySelector(".gal-modal-mask");var f=m&&m.querySelector(".gm-field");'
           'return JSON.stringify({nid:nid,modal:!!m,'
           'title:m?m.querySelector(".gm-title").textContent:null,'
           'tag:f?f.tagName:null,val:f?f.value:null,'
           'hint:m&&m.querySelector(".gm-hint")?m.querySelector(".gm-hint").textContent:null,'
           'okText:m&&m.querySelector("[data-ok]")?m.querySelector("[data-ok]").textContent:null});'
           '})()', 'open')

        time.sleep(0.9)
        hits = find_by_title(TITLE)
        if hits:
            hwnd, x, y, w, h = hits[0]
            img = grab_window(hwnd, w, h)
            if img is not None:
                img.save(OUT)
                note('shot', 'saved %s (%dx%d) via PrintWindow' % (OUT, w, h))
            else:
                note('shot', 'PrintWindow 失败')
        else:
            note('shot', 'no window')

        # 2) 填值 + 点确定
        ev('(function(){var f=document.querySelector(".gal-modal .gm-field");'
           'f.value="MODAL-TEST-9527";'
           'document.querySelector(".gal-modal .gm-acts [data-ok]").click();'
           'return JSON.stringify({closed:!document.querySelector(".gal-modal-mask")});'
           '})()', 'confirm')
        time.sleep(0.8)

        # 3) 数据真的写进去了吗
        ev('JSON.stringify((function(){var d=window.GalEditor.dump();'
           'var hit=d.nodes.filter(function(x){return x.text==="MODAL-TEST-9527";})[0];'
           'return {found:!!hit,len:hit?hit.text.length:0};})())', 'after')

        # 4) ESC 取消：不能改数据
        ev('(function(){var d=window.GalEditor.dump();'
           'window.GalEditor.api.onDouble({properties:{galId:d.nodes[0].id}});'
           'var opened=!!document.querySelector(".gal-modal-mask");'
           'document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));'
           'return JSON.stringify({opened:opened,'
           'closedAfterEsc:!document.querySelector(".gal-modal-mask")});})()', 'esc')

        # 5) alert / confirm 是否还被接管
        ev('(function(){var a=String(window.alert),c=String(window.confirm);'
           'var r1;try{r1=window.confirm("X");}catch(e){r1="exc";}'
           'return JSON.stringify({alertNative:a.indexOf("native code")>=0,'
           'confirmNative:c.indexOf("native code")>=0,confirmRet:r1});})()', 'guard')

        with open(RES, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        os._exit(0)

    def start():
        time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    holder['win'] = webview.create_window(TITLE, URL, width=1400, height=880)
    threading.Thread(target=start, daemon=True).start()
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()
