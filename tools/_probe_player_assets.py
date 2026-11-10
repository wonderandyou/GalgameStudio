# -*- coding: utf-8 -*-
r"""试玩页真实素材验收：背景图 / 立绘 / 逐句语音 / 音画同步。

用系统 Python（有 pywebview）跑：
  C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe tools\_probe_player_assets.py

结果写 tools\player_assets.json，截图 tools\player_assets.png。
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
OUT_JSON = os.path.join(HERE, 'player_assets.json')
OUT_PNG = os.path.join(HERE, 'player_assets.png')
TITLE = 'player-assets'
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/'

u = ctypes.windll.user32
g = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

holder = {}

# 第 0 步：挑节点最多的项目，写进 localStorage（走真实「恢复上次项目」路径）
PREP_JS = r"""
(async function () {
  try {
    GalAPI.mode = 'real';
    localStorage.setItem('gal_mode', 'real');
    var ps = await GalAPI.listProjects();
    if (!ps || !ps.length) { window.__p0 = 'ERR: 没有项目'; return; }
    ps.sort(function (a, b) { return (b.node_count||0) - (a.node_count||0); });
    var p = ps[0];
    localStorage.setItem('gal_project_id', p.id);
    localStorage.setItem('gal_project_name', p.name || '');
    window.__p0 = JSON.stringify({ project: p.name, projectId: p.id, nodes: p.node_count });
  } catch (e) {
    window.__p0 = 'ERR: ' + (e && (e.stack || e.message) || e);
  }
})();
"""

# 第 1 步：页面重载后确认 projectId 已绑定、试玩元素齐备
LOAD_JS = r"""
(function () {
  try {
    var els = ['btnPlay', 'pBg', 'pChars', 'pVoiceAudio', 'pTxt'];
    var missing = els.filter(function (id) { return !document.getElementById(id); });
    window.__p1 = JSON.stringify({
      projectId: window.__galProjectId || null,
      mode: GalAPI.mode,
      missing: missing,
      nodeCount: window.GalFrontendTest.state().nodes
    });
  } catch (e) {
    window.__p1 = 'ERR: ' + (e && (e.stack || e.message) || e);
  }
})();
"""

# 第 2 步：点「试玩」，马上采样（句 1）
OPEN_JS = r"""
(function () {
  document.getElementById('btnPlay').click();
  return 'clicked';
})();
"""

# 第 3 步：立刻读状态（句 1 刚起）
SAMPLE1_JS = r"""
(function () {
  var bg = document.getElementById('pBg');
  var imgs = Array.prototype.slice.call(document.querySelectorAll('#pChars img'));
  var au = document.getElementById('pVoiceAudio');
  window.__s1 = JSON.stringify({
    bgImage: bg.style.backgroundImage || '',
    bgClass: bg.className,
    rainDisplay: document.getElementById('pRain').style.display,
    charCount: imgs.length,
    charSrcs: imgs.map(function (i) { return i.currentSrc || i.src; }),
    charLoaded: imgs.map(function (i) { return i.naturalWidth > 0; }),
    txt: (document.getElementById('pTxt').textContent || '').slice(0, 60),
    who: document.getElementById('pWho').textContent,
    voiceLabel: document.getElementById('plVoice').textContent,
    audioSrc: au.currentSrc || au.src || '',
    audioTime: au.currentTime,
    audioPaused: au.paused,
    audioDur: au.duration,
    nodeId: document.getElementById('plNode').textContent
  });
  return 'ok';
})();
"""

# 第 4 步：等超过第一句 dur（4.6s）后读，验证「逐句推进」
SAMPLE2_JS = r"""
(function () {
  var au = document.getElementById('pVoiceAudio');
  var imgs = Array.prototype.slice.call(document.querySelectorAll('#pChars img'));
  window.__s2 = JSON.stringify({
    txt: (document.getElementById('pTxt').textContent || '').slice(0, 60),
    voiceLabel: document.getElementById('plVoice').textContent,
    audioSrc: au.currentSrc || au.src || '',
    audioTime: au.currentTime,
    audioPaused: au.paused,
    bgImage: document.getElementById('pBg').style.backgroundImage || '',
    charCount: imgs.length,
    nodeId: document.getElementById('plNode').textContent
  });
  return 'ok';
})();
"""

# 第 5 步：点舞台两次（跳过剩余句 / 进下一节点），看节点是否推进、背景是否还在
NEXT_JS = r"""
(function () {
  var st = document.getElementById('stage');
  st.click();
  return 'clicked';
})();
"""

SAMPLE3_JS = r"""
(function () {
  var au = document.getElementById('pVoiceAudio');
  window.__s3 = JSON.stringify({
    nodeId: document.getElementById('plNode').textContent,
    txt: (document.getElementById('pTxt').textContent || '').slice(0, 60),
    bgImage: document.getElementById('pBg').style.backgroundImage || '',
    charCount: document.querySelectorAll('#pChars img').length,
    audioSrc: au.currentSrc || au.src || '',
    audioTime: au.currentTime,
    hist: (document.getElementById('plIdx') || {}).textContent
  });
  return 'ok';
})();
"""

SAMPLE4_JS = r"""
(function () {
  var au = document.getElementById('pVoiceAudio');
  window.__s4 = JSON.stringify({
    nodeId: document.getElementById('plNode').textContent,
    txt: (document.getElementById('pTxt').textContent || '').slice(0, 60),
    who: document.getElementById('pWho').textContent,
    voiceLabel: document.getElementById('plVoice').textContent,
    audioSrc: (au.currentSrc || au.src || '').split('/').pop(),
    audioTime: au.currentTime,
    audioPaused: au.paused,
    charCount: document.querySelectorAll('#pChars img').length
  });
  return 'ok';
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


def wait_js(win, key, timeout=20):
    for _ in range(int(timeout * 5)):
        time.sleep(0.2)
        try:
            v = win.evaluate_js('window.%s || ""' % key)
        except Exception:
            continue
        if v:
            return v
    return ''


def main():
    log = {}

    def run():
        time.sleep(3.5)
        win = holder['win']

        # 0) 选项目 + 写 localStorage
        win.evaluate_js(PREP_JS)
        log['p0'] = wait_js(win, '__p0', 25)
        print('准备:', log['p0'], flush=True)

        # 1) 重载，走真实「恢复上次项目」路径（projectId 才会被赋值）
        try:
            win.evaluate_js("location.reload(); 'ok'")
        except Exception as e:
            print('reload 注入异常（忽略）:', repr(e), flush=True)
        time.sleep(6.0)

        win.evaluate_js(LOAD_JS)
        log['p1'] = wait_js(win, '__p1', 20)
        print('加载:', log['p1'], flush=True)

        win.evaluate_js(OPEN_JS)
        time.sleep(1.2)                       # 让打字机跑一会、语音起播
        win.evaluate_js(SAMPLE1_JS)
        log['s1'] = wait_js(win, '__s1', 6)
        print('句1:', log['s1'], flush=True)

        time.sleep(5.0)                       # n2 第一句 4.444s，跨过自动推进
        win.evaluate_js(SAMPLE2_JS)
        log['s2'] = wait_js(win, '__s2', 6)
        print('句2:', log['s2'], flush=True)

        # n1 只有 1 句（1.556s）：先点一次补打字机，等它自然播完，再点进 n2
        win.evaluate_js(NEXT_JS)
        time.sleep(2.6)
        win.evaluate_js(NEXT_JS)
        time.sleep(0.9)
        win.evaluate_js(SAMPLE3_JS)
        log['s3'] = wait_js(win, '__s3', 6)
        print('进 n2 第一句:', log['s3'], flush=True)

        # 等过第一句 dur(4.444s)，不点击，看是否自动接第二句
        time.sleep(4.2)
        win.evaluate_js(SAMPLE4_JS)
        log['s4'] = wait_js(win, '__s4', 6)
        print('自动进第二句:', log['s4'], flush=True)

        with open(OUT_JSON, 'w', encoding='utf-8') as fh:
            json.dump(log, fh, ensure_ascii=False, indent=2)

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
