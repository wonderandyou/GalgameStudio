# -*- coding: utf-8 -*-
r"""纯观察：打开试玩后不做任何点击，每 0.5s 采样一次，看节点/句子是否自己乱跳。"""
import ctypes
import json
import os
import sys
import threading
import time

import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'player_timeline.json')
TITLE = 'player-timeline'
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/'

PREP_JS = r"""
(async function () {
  try {
    GalAPI.mode = 'real';
    localStorage.setItem('gal_mode', 'real');
    var ps = await GalAPI.listProjects();
    ps.sort(function (a, b) { return (b.node_count||0) - (a.node_count||0); });
    var p = ps[0];
    localStorage.setItem('gal_project_id', p.id);
    localStorage.setItem('gal_project_name', p.name || '');
    window.__p0 = p.id;
  } catch (e) { window.__p0 = 'ERR:' + e; }
})();
"""

SNAP_JS = r"""
(function () {
  var au = document.getElementById('pVoiceAudio');
  return JSON.stringify({
    t: performance.now().toFixed(0),
    node: document.getElementById('plNode').textContent,
    txt: (document.getElementById('pTxt').textContent || '').slice(0, 24),
    aud: (au.currentSrc || '').split('/').pop(),
    at: Number(au.currentTime.toFixed(2)),
    pause: au.paused,
    timerOn: !!document.getElementById('player').classList.contains('show')
  });
})();
"""

holder = {}


def main():
    def run():
        time.sleep(3.5)
        win = holder['win']
        win.evaluate_js(PREP_JS)
        time.sleep(1.0)
        win.evaluate_js("location.reload(); 'ok'")
        time.sleep(6.0)

        # 打开试玩，之后完全不点击
        win.evaluate_js("document.getElementById('btnPlay').click(); 'ok'")

        rows = []
        for i in range(28):
            time.sleep(0.5)
            # 第 2.5s：n1（1.556s 单句）已播完停住，点一次进入 n2；之后不再点击
            if i == 4:
                win.evaluate_js("document.getElementById('stage').click(); 'ok'")
            try:
                r = win.evaluate_js(SNAP_JS)
            except Exception:
                r = ''
            rows.append(r)
            print('%4.1fs %s' % ((i + 1) * 0.5, r), flush=True)

        with open(OUT, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        os._exit(0)

    def start():
        time.sleep(0.8)
        threading.Thread(target=run, daemon=True).start()

    holder['win'] = webview.create_window(TITLE, URL, width=1400, height=880)
    threading.Thread(target=start, daemon=True).start()
    webview.start(gui='edgechromium')


if __name__ == '__main__':
    main()
