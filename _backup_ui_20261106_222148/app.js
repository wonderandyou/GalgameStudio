/* ============================================================
   鲸落工坊 · Galgame Studio — 交互逻辑（纯前端 Demo，无后端）
   ============================================================ */
(function () {
  'use strict';

  /* ---------------- 数据：节点图 ---------------- */
  const NODE_W = 268;
  const TYPE_META = {
    start:  { color: '#2563eb', label: '序章',  bg: 'linear-gradient(90deg,rgba(37,99,235,.13),rgba(37,99,235,.02))' },
    scene:  { color: '#059669', label: '场景',  bg: 'linear-gradient(90deg,rgba(5,150,105,.13),rgba(5,150,105,.02))' },
    choice: { color: '#d97706', label: '选择',  bg: 'linear-gradient(90deg,rgba(217,119,6,.14),rgba(217,119,6,.02))' },
    ending: { color: '#dc2626', label: '结局',  bg: 'linear-gradient(90deg,rgba(220,38,38,.13),rgba(220,38,38,.02))' }
  };

  let nodes = [
    { id: 'n1', type: 'start', idx: '01', title: '序章 · 雨', x: 40, y: 320,
      who: '', text: '十一点四十分，天台的门没锁。雨把整座城市洗成一片模糊的橘色。', bg: 'rainy', voice: 3.8 },
    { id: 'n2', type: 'scene', idx: '02', title: '天台 · 她先到了', x: 360, y: 320,
      who: '她', text: '你迟到了七分钟。我还以为……你不会来了。', bg: 'rainy', voice: 4.6 },
    { id: 'n3', type: 'choice', idx: '03', title: '两张车票', x: 680, y: 320,
      who: '她', text: '票我买好了。凌晨一点，最后一班。你要走吗？', bg: 'rainy', voice: 5.1,
      choices: [
        { label: '「走。现在就走。」', to: 'n4' },
        { label: '「……我们回去吧。」', to: 'n5' },
        { label: '（沉默，看着她的手）', to: 'n6' }
      ] },
    { id: 'n4', type: 'scene', idx: '04', title: '沉默的三十秒', x: 1010, y: 110,
      who: '她', text: '她没说话，只是把票塞进我手里。指尖是凉的，像淋了很久的雨。', bg: 'rainy', voice: 5.4 },
    { id: 'n5', type: 'scene', idx: '05', title: '楼梯间的回声', x: 1010, y: 330,
      who: '她', text: '……好。那我把票退了。她说得很轻，轻得像怕吵醒什么。', bg: 'dusk', voice: 4.9 },
    { id: 'n6', type: 'scene', idx: '06', title: '她摊开的手', x: 1010, y: 550,
      who: '她', text: '她的手心里只有一张票。另一张，从来就不存在。', bg: 'rainy', voice: 4.2 },
    { id: 'n7', type: 'ending', idx: '07', title: '真结局 · 雨停之前', x: 1330, y: 110,
      who: '', text: '凌晨一点，站台的灯一盏盏灭下去。我们谁都没有回头。', bg: 'white', voice: 0, endKind: 'true' },
    { id: 'n8', type: 'ending', idx: '08', title: '坏结局 · 留在原地', x: 1330, y: 330,
      who: '', text: '雨一直下到天亮。第二天，她的座位空着，桌角放着那张退掉的车票。', bg: 'dusk', voice: 0, endKind: 'bad' },
    { id: 'n9', type: 'ending', idx: '09', title: '隐藏结局 · 一张票', x: 1330, y: 550,
      who: '', text: '她笑了。她说：我早就知道你不会走。所以，我只买了一张。', bg: 'rainy', voice: 0, endKind: 'alt' }
  ];

  let links = [
    { from: 'n1', port: 0, to: 'n2' },
    { from: 'n2', port: 0, to: 'n3' },
    { from: 'n3', port: 0, to: 'n4' },
    { from: 'n3', port: 1, to: 'n5' },
    { from: 'n3', port: 2, to: 'n6' },
    { from: 'n4', port: 0, to: 'n7' },
    { from: 'n5', port: 0, to: 'n8' },
    { from: 'n6', port: 0, to: 'n9' }
  ];

  const BG_GRAD = {
    rainy: 'radial-gradient(90% 60% at 50% 8%, rgba(86,116,200,.55), transparent 62%), linear-gradient(180deg,#20243c 0%,#171326 60%,#0d0b14 100%)',
    dusk:  'radial-gradient(80% 55% at 70% 15%, rgba(255,170,90,.55), transparent 62%), linear-gradient(180deg,#3a2438 0%,#241629 55%,#120d18 100%)',
    white: 'radial-gradient(80% 60% at 50% 30%, rgba(255,255,255,.75), transparent 66%), linear-gradient(180deg,#dfe6f2 0%,#aeb8cf 100%)'
  };

  const $ = (id) => document.getElementById(id);
  const world = $('world');
  const wires = $('wires');
  const wrap = $('canvasWrap');

  let zoom = 1, panX = 0, panY = 0;
  let selId = null;
  let selLink = null;     // 选中的连线索引
  let graphProject = null; // 后端返回的 project 元信息（保存时原样回传）
  let graphCharacters = null; // 后端返回的 characters 角色表（PUT 必填）
  let pendingFrom = null; // 连线模式：{from, port}

  /* ---------------- 撤销 / 重做 ---------------- */
  const HISTORY_LIMIT = 60;
  const hist = { undo: [], redo: [] };

  function snap() {
    return JSON.stringify({ nodes: nodes, links: links });
  }

  /** 把「操作前的状态」压栈。data 不传就用当前状态 */
  function pushHistory(label, data) {
    hist.undo.push({ label: label, data: data || snap() });
    if (hist.undo.length > HISTORY_LIMIT) hist.undo.shift();
    hist.redo.length = 0;
    updateHistoryUI();
  }

  function applySnapshot(json) {
    const d = JSON.parse(json);
    nodes = d.nodes;
    links = d.links;
    selId = null;
    pendingFrom = null;
    $('inspector').classList.remove('show');
    render();
  }

  function undo() {
    if (!hist.undo.length) { toast('没有可撤销的操作'); return; }
    const it = hist.undo.pop();
    hist.redo.push({ label: it.label, data: snap() });
    applySnapshot(it.data);
    addLog('↶ 撤销：' + it.label);
    updateHistoryUI();
  }

  function redo() {
    if (!hist.redo.length) { toast('没有可重做的操作'); return; }
    const it = hist.redo.pop();
    hist.undo.push({ label: it.label, data: snap() });
    applySnapshot(it.data);
    addLog('↷ 重做：' + it.label);
    updateHistoryUI();
  }

  function updateHistoryUI() {
    const u = $('btnUndo'), r = $('btnRedo');
    if (u) u.disabled = hist.undo.length === 0;
    if (r) r.disabled = hist.redo.length === 0;
  }

  /* ---------------- 视图切换 ---------------- */
  const views = { setup: $('view-setup'), editor: $('view-editor'), export: $('view-export') };

  function go(view) {
    if (view === 'play') { openPlayer(); return; }
    Object.keys(views).forEach((k) => views[k].classList.toggle('active', k === view));
    document.querySelectorAll('#nav button').forEach((b) => {
      b.classList.toggle('active', b.dataset.view === view);
    });
    if (view === 'editor') {
      render();
      requestAnimationFrame(() => requestAnimationFrame(fitView));
      setTimeout(fitView, 120);   // 兜底：字体/布局稳定后再校正一次
    }
    if (view === 'export') {
      renderRenpy();
    }
  }
  document.querySelectorAll('#nav button').forEach((b) => {
    b.addEventListener('click', () => go(b.dataset.view));
  });
  $('btnToEditor').addEventListener('click', async () => {
    await saveProviders(false);
    go('editor');
  });

  /* ---------------- 渲染节点 ---------------- */
  function waveHTML(seed) {
    const hs = [5, 9, 6, 12, 8, 11, 7, 4, 10, 6, 9, 5];
    let s = '';
    for (let i = 0; i < hs.length; i++) {
      const h = Math.max(3, (hs[(i + seed) % hs.length]));
      s += '<i style="height:' + h + 'px"></i>';
    }
    return s;
  }

  function nodeHTML(n) {
    const m = TYPE_META[n.type] || TYPE_META.scene;
    const outCount = n.type === 'choice' ? n.choices.length : (n.type === 'ending' ? 0 : 1);
    const inCount = n.type === 'start' ? 0 : 1;

    let ports = '';
    if (inCount) ports += '<div class="port in" data-port="in" title="输入"></div>';
    for (let i = 0; i < outCount; i++) {
      const label = n.type === 'choice' ? (i === 0 ? 'A' : i === 1 ? 'B' : i === 2 ? 'C' : 'D') : '';
      const topStyle = n.type === 'choice' ? ' style="top:' + (56 + i * 30) + 'px"' : '';
      ports += '<div class="port out" data-port="' + i + '"' + topStyle + ' title="输出 ' + (i + 1) + '">' +
        (label ? '<span class="pl">' + label + '</span>' : '') + '</div>';
    }

    const isEnding = n.type === 'ending';
    const badge = isEnding
      ? '<div class="badge ' + (n.endKind === 'true' ? '' : n.endKind === 'alt' ? 'alt' : '') + '" style="' +
        (n.endKind === 'bad' ? 'background:linear-gradient(120deg,#64748b,#475569);box-shadow:none;' :
         n.endKind === 'alt' ? 'background:linear-gradient(120deg,#6366f1,#22d3ee);box-shadow:none;' : '') + '">' +
        (n.endKind === 'true' ? 'TRUE END' : n.endKind === 'bad' ? 'BAD END' : 'HIDDEN') + '</div>'
      : '';

    const voiceBlock = isEnding ? '' :
      '<div class="voice' + (n.voice > 0 ? '' : ' pending') + '">' +
        '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 9v6M8 5v14M12 8v8M16 6v12M20 10v4"/></svg>' +
        '<span>' + (n.voice > 0 ? n.voice.toFixed(1) + 's' : '待生成') + '</span>' +
        '<span class="wf">' + waveHTML(n.idx ? parseInt(n.idx, 10) : 0) + '</span>' +
      '</div>';

    const useRealBg = !!(n.bgAsset && GalAPI.mode === 'real');
    const useRealChar = !!(n.charAsset && GalAPI.mode === 'real');
    const bgStyle = useRealBg
      ? 'background-image:url(\'' + GalAPI.assetURL(projectId, n.bgAsset) + '\');background-size:cover;background-position:center'
      : 'background:' + (BG_GRAD[n.bg] || BG_GRAD.rainy);
    const charInner = useRealChar
      ? '<div style="position:absolute;inset:0;background-image:url(\'' + GalAPI.assetURL(projectId, n.charAsset) + '\');background-size:contain;background-position:bottom center;background-repeat:no-repeat"></div>'
      : (isEnding
        ? '<div class="thumb-missing">无立绘</div>'
        : '<div style="position:absolute;left:50%;top:14%;width:30%;height:34%;transform:translateX(-50%);border-radius:50% 50% 44% 44%;background:radial-gradient(60% 60% at 38% 30%,#c6cddb,#e7eaf1 70%)"></div>' +
          '<div style="position:absolute;left:50%;bottom:0;width:76%;height:52%;transform:translateX(-50%);border-radius:46% 46% 0 0/34% 34% 0 0;background:linear-gradient(180deg,#dde2eb,#edf0f5)"></div>');

    return '' +
      '<div class="nh" style="background:' + m.bg + '">' +
        '<span class="kk" style="background:' + m.color + '"></span>' +
        '<span>' + (isEnding ? n.title : n.idx + ' · ' + n.title) + '</span>' +
        '<span class="idx">' + m.label + '</span>' +
      '</div>' +
      badge +
      '<div class="nb">' +
        '<div class="thumbs">' +
          '<div class="thumb-bg" style="' + bgStyle + '">' +
            '<span class="lb">bg · ' + (n.bg === 'rainy' ? '雨夜' : n.bg === 'dusk' ? '黄昏' : '清晨') + '</span>' +
          '</div>' +
          '<div class="thumb-ch" style="background:#f7f8fa">' +
            charInner +
            '<span class="lb">char</span>' +
          '</div>' +
        '</div>' +
        '<div class="line">' + (n.who ? '<span class="who">' + esc(n.who) + '　</span>' : '') + esc(n.text) + '</div>' +
        voiceBlock +
      '</div>' +
      ports;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function render() {
    // 清掉旧节点
    Array.from(world.querySelectorAll('.node')).forEach((el) => el.remove());

    nodes.forEach((n) => {
      const el = document.createElement('div');
      el.className = 'node' + (selId === n.id ? ' sel' : '');
      el.id = 'nd-' + n.id;
      el.style.left = n.x + 'px';
      el.style.top = n.y + 'px';
      el.dataset.id = n.id;
      el.innerHTML = nodeHTML(n);
      world.appendChild(el);

      // 节点拖动 + 选中
      let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0, moved = 0, beforeDrag = '';
      el.addEventListener('pointerdown', (e) => {
        if (e.target.closest('.port')) return;
        dragging = true; moved = 0;
        beforeDrag = snap();
        sx = e.clientX; sy = e.clientY; ox = n.x; oy = n.y;
        el.classList.add('drag');
        el.setPointerCapture(e.pointerId);
        e.stopPropagation();
      });
      el.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        const dx = (e.clientX - sx) / zoom;
        const dy = (e.clientY - sy) / zoom;
        moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
        n.x = Math.round(ox + dx);
        n.y = Math.round(oy + dy);
        el.style.left = n.x + 'px';
        el.style.top = n.y + 'px';
        drawWires();
      });
      el.addEventListener('pointerup', (e) => {
        if (!dragging) return;
        dragging = false;
        el.classList.remove('drag');
        el.releasePointerCapture(e.pointerId);
        if (moved < 4) {
          if (pendingFrom) { tryLink(n.id); }
          else selectNode(n.id);
        } else if (beforeDrag) {
          pushHistory('移动节点', beforeDrag);   // 记录的是拖动前的状态
        }
      });

      // 双击台词：就地编辑
      const lineEl = el.querySelector('.line');
      if (lineEl) {
        lineEl.title = '双击修改台词';
        lineEl.addEventListener('dblclick', (e) => {
          e.stopPropagation();
          startInlineEdit(el, n);
        });
      }

      // 端口：点输出口进入连线模式
      el.querySelectorAll('.port.out').forEach((p) => {
        p.addEventListener('pointerdown', (e) => {
          e.stopPropagation();
          e.preventDefault();
          startLinking(n.id, parseInt(p.dataset.port, 10) || 0, e);
        });
      });
    });

    drawWires();
    updateZoomLabel();
  }

  /* ---------------- 连线绘制 ---------------- */
  function nodeEl(id) { return document.getElementById('nd-' + id); }

  function outPos(n, port) {
    const el = nodeEl(n.id);
    const h = el ? el.offsetHeight : 190;
    if (n.type === 'choice') {
      const top = 62 + port * 30;
      return { x: n.x + NODE_W, y: n.y + Math.min(top, h - 24) };
    }
    return { x: n.x + NODE_W, y: n.y + h / 2 };
  }

  function inPos(n) {
    const el = nodeEl(n.id);
    const h = el ? el.offsetHeight : 190;
    return { x: n.x, y: n.y + h / 2 };
  }

  function drawWires() {
    let html = '<defs>';
    ['#3b82f6', '#22c55e', '#f59e0b', '#f43f5e'].forEach((c, i) => {
      html += '<marker id="ar' + i + '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
        '<path d="M0 0 L10 5 L0 10 z" fill="' + c + '"/></marker>';
    });
    html += '</defs>';

    links.forEach((l, i) => {
      const a = nodes.find((x) => x.id === l.from);
      const b = nodes.find((x) => x.id === l.to);
      if (!a || !b) return;
      const p1 = outPos(a, l.port || 0);
      const p2 = inPos(b);
      const dx = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
      const d = 'M' + p1.x + ',' + p1.y + ' C' + (p1.x + dx) + ',' + p1.y + ' ' + (p2.x - dx) + ',' + p2.y + ' ' + p2.x + ',' + p2.y;
      const color = TYPE_META[a.type].color;
      const mi = Object.keys(TYPE_META).indexOf(a.type);
      const active = pendingFrom && pendingFrom.from === a.id && pendingFrom.port === (l.port || 0);
      const isSel = selLink === i;
      html += '<path class="wire' + (isSel ? ' sel' : active ? ' hl' : '') + '" d="' + d + '" stroke="' + color +
        '" marker-end="url(#ar' + mi + ')" opacity="' + (isSel || active ? 1 : 0.7) +
        '" style="color:' + color + '"></path>';
      // 透明加粗热区：让连线可以点中
      html += '<path class="wire-hit" d="' + d + '" data-i="' + i + '"></path>';
    });

    wires.innerHTML = html;
    // svg 尺寸覆盖所有节点
    let maxX = 0, maxY = 0;
    nodes.forEach((n) => { maxX = Math.max(maxX, n.x + NODE_W + 300); maxY = Math.max(maxY, n.y + 400); });
    wires.setAttribute('width', maxX);
    wires.setAttribute('height', maxY);
  }

  /* ---------------- 连线交互：点选中 / Delete 断开 / 拖拽连接 ---------------- */
  wires.addEventListener('pointerdown', (e) => {
    const hit = e.target.closest ? e.target.closest('.wire-hit') : null;
    if (!hit) return;
    e.stopPropagation();
    e.preventDefault();
    const i = parseInt(hit.getAttribute('data-i'), 10);
    selLink = (selLink === i) ? null : i;
    selId = null;
    document.querySelectorAll('.node').forEach((el) => el.classList.remove('sel'));
    $('inspector').classList.remove('show');
    drawWires();
    if (selLink !== null && links[selLink]) {
      const l = links[selLink];
      const a = nodes.find((x) => x.id === l.from);
      toast('已选中：' + (a ? a.title : l.from) + ' → ' + l.to + ' · 按 Delete 断开');
    }
  });

  function deleteLink() {
    if (selLink === null || !links[selLink]) { toast('先点一下要断开的那条线'); return; }
    const l = links[selLink];
    pushHistory('断开连线');
    links.splice(selLink, 1);
    selLink = null;
    drawWires();
    addLog('断开连线 ' + l.from + ' → ' + l.to);
    toast('已断开（Ctrl+Z 可撤回）');
  }

  let linking = null;

  function clientToWorld(cx, cy) {
    const r = wrap.getBoundingClientRect();
    return { x: (cx - r.left - panX) / zoom, y: (cy - r.top - panY) / zoom };
  }

  /** 从输出口开始拉线：拖动 → 松手在节点上连上；只点一下 → 再点目标节点连 */
  function startLinking(fromId, port, ev) {
    if (linking) return;
    const n = nodes.find((x) => x.id === fromId);
    if (!n) return;
    const p1 = outPos(n, port);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('class', 'wire-temp');
    wires.appendChild(path);

    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    let snapNode = null;
    linking = { from: fromId, port: port };

    const clearSnap = () => {
      if (snapNode) {
        const el = nodeEl(snapNode.id);
        if (el) el.classList.remove('drop-target');
        snapNode = null;
      }
    };

    const onMove = (e) => {
      if (!linking) return;
      if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 4) moved = true;

      // —— 端口吸附：鼠标进到某个节点范围内就吸附它的输入口 ——
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const tEl = under && under.closest ? under.closest('.node') : null;
      const tId = tEl ? tEl.dataset.id : null;
      if (tId && tId !== fromId) {
        if (!snapNode || snapNode.id !== tId) {
          clearSnap();
          snapNode = nodes.find((x) => x.id === tId) || null;
          if (snapNode) {
            const el = nodeEl(snapNode.id);
            if (el) el.classList.add('drop-target');
          }
        }
      } else {
        clearSnap();
      }

      const p2 = snapNode ? inPos(snapNode) : clientToWorld(e.clientX, e.clientY);
      const c = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
      path.setAttribute('d', 'M' + p1.x + ',' + p1.y + ' C' + (p1.x + c) + ',' + p1.y +
        ' ' + (p2.x - c) + ',' + p2.y + ' ' + p2.x + ',' + p2.y);
    };

    const onUp = (e) => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      try { path.remove(); } catch (err) {}
      const target = snapNode;
      clearSnap();
      linking = null;

      if (!moved) {
        pendingFrom = { from: fromId, port: port };
        drawWires();
        toast('再点一下目标节点就连上（也可以直接拖过去）');
        return;
      }

      let toId = target ? target.id : null;
      if (!toId) {
        const under = document.elementFromPoint(e.clientX, e.clientY);
        const t = under && under.closest ? under.closest('.node') : null;
        toId = t ? t.dataset.id : null;
      }
      if (!toId) { toast('松手在节点上才能连接'); drawWires(); return; }
      if (toId === fromId) { toast('不能连自己'); drawWires(); return; }
      pushHistory('连线');
      links = links.filter((l) => !(l.from === fromId && l.port === port));
      links.push({ from: fromId, port: port, to: toId });
      addLog('连线 ' + fromId + ' → ' + toId);
      toast('已连接到「' + ((nodes.find((x) => x.id === toId) || {}).title || toId) + '」');
      drawWires();
    };

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    onMove(ev);
  }

  /* ---------------- Blender 式「一刀切断」（Alt + 拖动） ---------------- */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function bezierPoints(p1, p2, segs) {
    const c = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
    const c1 = { x: p1.x + c, y: p1.y };
    const c2 = { x: p2.x - c, y: p2.y };
    const pts = [];
    for (let i = 0; i <= segs; i++) {
      const t = i / segs, mt = 1 - t;
      pts.push({
        x: mt * mt * mt * p1.x + 3 * mt * mt * t * c1.x + 3 * mt * t * t * c2.x + t * t * t * p2.x,
        y: mt * mt * mt * p1.y + 3 * mt * mt * t * c1.y + 3 * mt * t * t * c2.y + t * t * t * p2.y
      });
    }
    return pts;
  }

  function segIntersect(a, b, c, d) {
    const rx = b.x - a.x, ry = b.y - a.y;
    const sx = d.x - c.x, sy = d.y - c.y;
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-9) return false;
    const t = ((c.x - a.x) * sy - (c.y - a.y) * sx) / den;
    const u = ((c.x - a.x) * ry - (c.y - a.y) * rx) / den;
    return t >= 0 && t <= 1 && u >= 0 && u <= 1;
  }

  function startCut(ev) {
    const line = document.createElementNS(SVG_NS, 'path');
    line.setAttribute('class', 'cut-line');
    wires.appendChild(line);

    const a = clientToWorld(ev.clientX, ev.clientY);
    let b = a;

    const move = (e) => {
      b = clientToWorld(e.clientX, e.clientY);
      line.setAttribute('d', 'M' + a.x + ',' + a.y + ' L' + b.x + ',' + b.y);
    };

    const up = (e) => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      try { line.remove(); } catch (err) {}
      b = clientToWorld(e.clientX, e.clientY);

      const hit = [];
      links.forEach((l, i) => {
        const na = nodes.find((x) => x.id === l.from);
        const nb = nodes.find((x) => x.id === l.to);
        if (!na || !nb) return;
        const pts = bezierPoints(outPos(na, l.port || 0), inPos(nb), 24);
        for (let k = 0; k < pts.length - 1; k++) {
          if (segIntersect(pts[k], pts[k + 1], a, b)) { hit.push(i); break; }
        }
      });

      if (!hit.length) { toast('这一刀没切到线'); return; }
      pushHistory('切断连线');
      hit.sort((x, y) => y - x).forEach((i) => links.splice(i, 1));
      selLink = null;
      drawWires();
      addLog('切断了 ' + hit.length + ' 条连线');
      toast('切断了 ' + hit.length + ' 条线（Ctrl+Z 可撤回）');
    };

    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    move(ev);
  }

  /* ---------------- 画布变换 ---------------- */
  function applyTransform() {
    world.style.transform = 'translate(' + panX + 'px,' + panY + 'px) scale(' + zoom + ')';
    // 网格跟随
    const gx = panX % (24 * zoom);
    const gy = panY % (24 * zoom);
    wrap.style.setProperty('--gx', gx + 'px');
    wrap.style.setProperty('--gy', gy + 'px');
    wrap.style.backgroundSize = '100% 100%, ' + (24 * zoom) + 'px ' + (24 * zoom) + 'px, ' + (24 * zoom) + 'px ' + (24 * zoom) + 'px';
    updateZoomLabel();
  }

  function updateZoomLabel() { $('zoomInfo').textContent = Math.round(zoom * 100) + '%'; }

  function setZoom(z, cx, cy) {
    const nz = Math.min(2.2, Math.max(0.28, z));
    if (cx == null) { cx = wrap.clientWidth / 2; cy = wrap.clientHeight / 2; }
    const k = nz / zoom;
    panX = cx - (cx - panX) * k;
    panY = cy - (cy - panY) * k;
    zoom = nz;
    applyTransform();
  }

  $('zoomIn').addEventListener('click', () => setZoom(zoom * 1.18));
  $('zoomOut').addEventListener('click', () => setZoom(zoom / 1.18));

  wrap.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    setZoom(zoom * (e.deltaY > 0 ? 0.9 : 1.1), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  // 空白处拖动平移
  let panning = false, pSX = 0, pSY = 0, pOX = 0, pOY = 0;
  wrap.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.node') || e.target.closest('.toolbar') || e.target.closest('.inspector') || e.target.closest('.legend') || e.target.closest('.add-menu')) return;

    // Alt + 拖动 = 一刀切断（Blender 手感）
    if (e.button === 0 && e.altKey) {
      e.preventDefault();
      e.stopPropagation();
      startCut(e);
      return;
    }

    const menu = $('addMenu');
    if (menu && menu.classList.contains('show')) menu.classList.remove('show');
    if (selLink !== null) { selLink = null; drawWires(); }
    panning = true; pSX = e.clientX; pSY = e.clientY; pOX = panX; pOY = panY;
    wrap.classList.add('panning');
    wrap.setPointerCapture(e.pointerId);
  });

  // 画布内右键不弹系统菜单（留给我们自己用）
  wrap.addEventListener('contextmenu', (e) => e.preventDefault());
  wrap.addEventListener('pointermove', (e) => {
    if (!panning) return;
    panX = pOX + (e.clientX - pSX);
    panY = pOY + (e.clientY - pSY);
    applyTransform();
  });
  wrap.addEventListener('pointerup', (e) => {
    if (!panning) return;
    panning = false;
    wrap.classList.remove('panning');
    try { wrap.releasePointerCapture(e.pointerId); } catch (err) {}
    if (pendingFrom) { pendingFrom = null; drawWires(); }
  });

  function fitView() {
    if (!nodes.length) return;
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    nodes.forEach((n) => {
      const el = nodeEl(n.id);
      const w = el ? el.offsetWidth : NODE_W;
      const h = el ? el.offsetHeight : 190;
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + w); maxY = Math.max(maxY, n.y + h);
    });

    const pad = 70;
    const w = wrap.clientWidth || 1200;
    const h = wrap.clientHeight || 700;
    const zx = (w - pad * 2) / Math.max(1, maxX - minX);
    const zy = (h - pad * 2) / Math.max(1, maxY - minY);
    zoom = Math.max(0.28, Math.min(zx, zy, 1.1));

    const center = () => {
      panX = (w - (maxX - minX) * zoom) / 2 - minX * zoom;
      panY = (h - (maxY - minY) * zoom) / 2 - minY * zoom;
    };
    center();

    // 兜底：算出来如果仍然越界，继续缩，保证整张图完整可见
    for (let i = 0; i < 5; i++) {
      const right = panX + maxX * zoom;
      const bottom = panY + maxY * zoom;
      const left = panX + minX * zoom;
      const top = panY + minY * zoom;
      if (right <= w - 6 && bottom <= h - 6 && left >= 6 && top >= 6) break;
      zoom *= 0.9;
      center();
    }
    applyTransform();
  }
  $('btnFit').addEventListener('click', fitView);

  // 自动排布：按拓扑层级
  function autoLayout() {
    const level = {};
    nodes.forEach((n) => { level[n.id] = 0; });
    for (let k = 0; k < 12; k++) {
      links.forEach((l) => {
        if (level[l.to] < level[l.from] + 1) level[l.to] = level[l.from] + 1;
      });
    }
    const groups = {};
    nodes.forEach((n) => { (groups[level[n.id]] = groups[level[n.id]] || []).push(n); });
    Object.keys(groups).forEach((lv) => {
      const arr = groups[lv];
      const totalH = arr.length * 210;
      let y = 320 - totalH / 2 + 60;
      arr.forEach((n) => {
        n.x = 40 + lv * 330;
        n.y = Math.round(y);
        y += 220;
      });
    });
    render();
    fitView();
    toast('已按层级自动排布');
  }
  $('btnLayout').addEventListener('click', autoLayout);

  /* 节点 id 必须唯一：同一毫秒内连续创建也不许撞车 */
  let nodeUid = 0;
  function newNodeId() {
    nodeUid += 1;
    return 'n' + Date.now().toString(36) + nodeUid.toString(36);
  }

  /* 新建节点：先选类型 */
  const NODE_DEFAULT_TEXT = {
    start: '故事从这里开始。',
    scene: '在这里写台词……',
    choice: '选择哪一个？',
    ending: '故事结束。'
  };

  function addNodeOfType(type) {
    if (!TYPE_META[type]) type = 'scene';
    pushHistory('添加节点');
    const maxX = nodes.length ? Math.max.apply(null, nodes.map((n) => n.x)) : 0;
    const lastY = nodes.length ? nodes[nodes.length - 1].y : 320;
    const n = {
      id: newNodeId(),
      type: type,
      idx: String(nodes.length + 1).padStart(2, '0'),
      title: type === 'start' ? '新序章' : type === 'choice' ? '新分支' : type === 'ending' ? '新结局' : '新场景',
      x: maxX + 330,
      y: Math.max(120, Math.min(620, lastY)),
      who: '',
      text: NODE_DEFAULT_TEXT[type],
      bg: 'rainy',
      voice: 0
    };
    if (type === 'choice') {
      n.choices = [{ label: '选项 A', to: '' }, { label: '选项 B', to: '' }];
    }
    if (type === 'ending') n.endKind = 'true';
    nodes.push(n);
    render();
    selectNode(n.id);
    drawWires();
    toast('已添加「' + TYPE_META[type].label + '」节点');
  }

  $('btnSave').addEventListener('click', () => { saveGraphToBackend(); });

  $('btnAdd').addEventListener('click', (e) => {
    e.stopPropagation();
    const menu = $('addMenu');
    if (menu.classList.contains('show')) { menu.classList.remove('show'); return; }
    const r = e.currentTarget.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    menu.style.left = Math.round(r.left - wr.left) + 'px';
    menu.style.top = Math.round(r.bottom - wr.top + 6) + 'px';
    menu.classList.add('show');
  });

  $('addMenu').querySelectorAll('button[data-type]').forEach((b) => {
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      $('addMenu').classList.remove('show');
      addNodeOfType(b.dataset.type);
    });
  });

  /* ---------------- 选中 / 属性面板 ---------------- */
  function selectNode(id) {
    selId = id;
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    document.querySelectorAll('.node').forEach((el) => el.classList.toggle('sel', el.dataset.id === id));
    const m = TYPE_META[n.type];
    $('insTitle').textContent = m.label + ' · ' + n.title;
    $('insWho').value = n.who || '';
    $('insText').value = n.text || '';
    $('insBg').value = n.bg || 'rainy';
    $('insVoiceInfo').textContent = n.voice > 0 ? n.voice.toFixed(1) + 's' : '待生成';
    $('insState').textContent = n.voice > 0 ? '已生成' : '未生成';
    $('inspector').classList.add('show');
  }

  /** 双击节点台词 → 就地编辑（Esc 取消 / Ctrl+Enter 保存 / 失焦保存） */
  function startInlineEdit(el, n) {
    if (el.querySelector('.inline-edit')) return;
    const lineEl = el.querySelector('.line');
    if (!lineEl) return;
    const before = snap();
    const ta = document.createElement('textarea');
    ta.className = 'inline-edit';
    ta.value = n.text || '';
    lineEl.replaceWith(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      if (save) {
        n.text = ta.value;
        if (snap() !== before) {
          pushHistory('编辑台词', before);
          addLog('改台词：节点 ' + n.idx);
        }
      }
      el.innerHTML = nodeHTML(n);
      bindPorts(el, n);
      drawWires();
      if (selId === n.id) selectNode(n.id);
    };

    ta.addEventListener('blur', () => finish(true));
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
    });
  }

  $('insClose').addEventListener('click', () => {
    $('inspector').classList.remove('show');
    selId = null;
    document.querySelectorAll('.node').forEach((el) => el.classList.remove('sel'));
  });

  function syncSel() {
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    n.who = $('insWho').value;
    n.text = $('insText').value;
    n.bg = $('insBg').value;
    const el = nodeEl(n.id);
    if (el) {
      el.innerHTML = nodeHTML(n);
      bindPorts(el, n);
    }
    drawWires();
  }
  ['insWho', 'insText', 'insBg'].forEach((id) => {
    const el = $(id);
    el.addEventListener('focus', () => { el.dataset.before = snap(); });   // 记住改之前
    el.addEventListener('input', syncSel);
    el.addEventListener('change', syncSel);
    el.addEventListener('blur', () => {
      if (el.dataset.before && el.dataset.before !== snap()) pushHistory('编辑节点', el.dataset.before);
      delete el.dataset.before;
    });
  });

  function bindPorts(el, n) {
    el.querySelectorAll('.port.out').forEach((p) => {
      p.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        e.preventDefault();
        startLinking(n.id, parseInt(p.dataset.port, 10) || 0, e);
      });
    });
  }

  $('btnRegen').addEventListener('click', () => {
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    $('insState').textContent = '生成中…';
    $('btnRegen').disabled = true;
    addLog('重新生成节点 ' + n.idx + '：排入任务队列');
    setTimeout(() => {
      n.voice = +(3 + Math.random() * 3).toFixed(1);
      const el = nodeEl(n.id);
      if (el) { el.innerHTML = nodeHTML(n); bindPorts(el, n); }
      $('insVoiceInfo').textContent = n.voice.toFixed(1) + 's';
      $('insState').textContent = '已生成';
      $('btnRegen').disabled = false;
      addLog('节点 ' + n.idx + ' 完成：背景 1 张 / 立绘 1 张 / 语音 ' + n.voice.toFixed(1) + 's');
      toast('节点已重新生成');
      drawWires();
    }, 900);
  });

  function deleteSelected() {
    if (!selId) return;
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    pushHistory('删除节点');
    nodes = nodes.filter((x) => x.id !== selId);
    links = links.filter((l) => l.from !== selId && l.to !== selId);
    selId = null;
    $('inspector').classList.remove('show');
    render();
    addLog('删除节点 ' + n.idx);
    toast('已删除（Ctrl+Z 可撤回）');
  }

  function duplicateSelected() {
    if (!selId) return;
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    pushHistory('复制节点');
    const copy = JSON.parse(JSON.stringify(n));
    copy.id = newNodeId();
    copy.idx = String(nodes.length + 1).padStart(2, '0');
    copy.title = n.title + ' 副本';
    copy.x = n.x + 44;
    copy.y = n.y + 44;
    nodes.push(copy);
    render();
    selectNode(copy.id);
    drawWires();
    toast('已复制节点');
  }

  $('btnDel').addEventListener('click', deleteSelected);
  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);

  /* ---------------- Ren'Py 脚本预览（图的唯一真源 → .rpy） ---------------- */
  function bgName(n) {
    if (n.bgAsset) {
      const base = n.bgAsset.split('/').pop().replace(/\.[^.]+$/, '');
      return base.replace(/[^\w\u4e00-\u9fa5]/g, '_');
    }
    return n.bg || 'rainy';
  }

  function voiceOf(n) {
    if (n.voiceAsset) return n.voiceAsset;
    return 'voice/' + n.id + '_01.wav';
  }

  function toRenpy() {
    const out = [];
    out.push('# 由鲸落工坊自动生成 —— 请勿手改，改图后重新导出');
    out.push('# 角色暂用名字字符串；要头像/立绘差分时改成 Character("她", image="heroine")');
    out.push('');
    out.push('label start:');
    const start = nodes.find((x) => x.type === 'start') || nodes[0];
    out.push('    jump ' + (start ? start.id : 'n1'));
    out.push('');

    nodes.forEach((n) => {
      out.push('label ' + n.id + ':');
      out.push('    scene bg ' + bgName(n));
      if (n.charAsset) out.push('    show ' + n.id + '_char at center');

      const text = String(n.text || '').replace(/"/g, '\\"');
      if (n.voice > 0) out.push('    voice "' + voiceOf(n) + '"');
      if (n.who) out.push('    "' + n.who + '" "' + text + '"');
      else if (text) out.push('    "' + text + '"');

      if (n.type === 'choice' && n.choices && n.choices.length) {
        out.push('    menu:');
        n.choices.forEach((c) => {
          out.push('        "' + String(c.label).replace(/"/g, '\\"') + '":');
          out.push('            jump ' + c.to);
        });
      } else if (n.type === 'ending') {
        out.push('    # ' + (n.endKind === 'true' ? '真结局' : n.endKind === 'bad' ? '坏结局' : '隐藏结局'));
        out.push('    return');
      } else {
        const next = links.find((l) => l.from === n.id);
        out.push(next ? '    jump ' + next.to : '    return');
      }
      out.push('');
    });
    return out.join('\n');
  }

  let rpyDirty = true;

  function renderRenpy() {
    const pre = $('rpyPreview');
    if (!pre) return;
    pre.textContent = toRenpy();
    rpyDirty = false;
  }
  $('btnRpy').addEventListener('click', renderRenpy);

  /* ---------------- 键盘快捷键 ---------------- */
  document.addEventListener('keydown', (e) => {
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
    const mod = e.ctrlKey || e.metaKey;
    const k = (e.key || '').toLowerCase();

    if (mod && k === 'z') {
      if (typing) return;
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if (mod && k === 'y') {
      if (typing) return;
      e.preventDefault();
      redo();
      return;
    }
    if (mod && k === 'd') {
      if (typing) return;
      e.preventDefault();
      duplicateSelected();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      saveGraphToBackend();
      return;
    }

    if ((e.key === 'Delete' || e.key === 'Backspace') && !typing) {
      if (selLink !== null) { e.preventDefault(); deleteLink(); return; }
      if (selId) { e.preventDefault(); deleteSelected(); }
      return;
    }
    if (e.key === 'Escape') {
      if ($('player').classList.contains('show')) { $('player').classList.remove('show'); return; }
      if (selId) {
        selId = null;
        $('inspector').classList.remove('show');
        render();
      }
    }
  });

  function tryLink(toId) {
    if (!pendingFrom) return;
    const from = pendingFrom.from;
    const port = pendingFrom.port;
    if (from === toId) { pendingFrom = null; drawWires(); return; }
    const dup = links.some((l) => l.from === from && l.port === port && l.to === toId);
    if (dup) { toast('这条线已经有了'); pendingFrom = null; drawWires(); return; }
    pushHistory('连线');
    links = links.filter((l) => !(l.from === from && l.port === port));
    links.push({ from: from, port: port, to: toId });
    pendingFrom = null;
    drawWires();
    toast('已连接 ' + from + ' → ' + toId);
    addLog('连线 ' + from + ' → ' + toId);
  }

  /* ---------------- toast / 日志 ---------------- */
  let toastTimer = null;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 1900);
  }
  function addLog(msg) {
    const d = new Date();
    const ts = [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, '0')).join(':');
    const box = $('logBox');
    box.textContent += '[' + ts + '] ' + msg + '\n';
    box.scrollTop = box.scrollHeight;
  }

  /** 生成日志默认收起，点标题展开 */
  function toggleLog(open) {
    const box = $('logBox');
    if (!box) return;
    const show = typeof open === 'boolean' ? open : box.style.display === 'none';
    box.style.display = show ? 'block' : 'none';
    const s = $('logState');
    if (s) s.textContent = show ? '收起' : '展开';
  }
  $('logToggle').addEventListener('click', () => toggleLog());

  // 顶栏状态跟卡片实时同步（数量不写死）
  function updateGlobalMeta() {
    const cards = Array.from(document.querySelectorAll('.api-card'));
    const ok = cards.filter((c) => c.querySelector('.dot.ok')).length;
    $('globalMeta').innerHTML = '<span class="tp"></span> ' + ok + ' / ' + cards.length + ' 就绪';
    const rc = $('readyCount');
    if (rc) rc.textContent = ok + ' / ' + cards.length;
  }

  /* ---------------- 生成流程：走 GalAPI，mock/real 同一套事件 ---------------- */
  let generating = false;
  let projectId = null;
  let finishingPromise = null;   // 收尾（含从后端拉图）是异步的，generate 要等它

  function projectName() {
    const el = document.querySelector('.proj-name input');
    return el && el.value ? el.value.trim() : '未命名项目';
  }

  async function ensureProjectId() {
    if (projectId) return projectId;
    if (GalAPI.mode === 'mock') { projectId = 'demo'; return projectId; }
    const p = await GalAPI.ensureProject(projectName());
    projectId = p.id;
    return projectId;
  }

  function setAgent(k, state, label, progress) {
    const el = document.querySelector('.agent[data-k="' + k + '"]');
    if (!el) return;
    el.classList.remove('run', 'done', 'failed');
    if (state && state !== 'idle') el.classList.add(state);
    if (label) el.querySelector('.st').textContent = label;
    if (typeof progress === 'number') {
      el.querySelector('.bar i').style.width = Math.round(Math.max(0, Math.min(1, progress)) * 100) + '%';
    }
  }

  /** 后端 SSE 的 node 事件 → 局部刷新画布 */
  function applyNodePatch(id, patch) {
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    if (Array.isArray(patch.lines)) {
      n.text = patch.lines.map((l) => l.text).join(' ');
      n.who = (patch.lines[0] && patch.lines[0].who) || n.who;
      n.voice = patch.lines.reduce((s, l) => s + (l.dur || 0), 0);
    }
    if (patch.bg && patch.bg.asset) n.bgAsset = patch.bg.asset;
    if (patch.chars && patch.chars[0] && patch.chars[0].pose) n.charAsset = patch.chars[0].pose;
    if (Array.isArray(patch.choices)) n.choices = patch.choices.map((c) => ({ label: c.label, to: c.to }));
    if (patch.status) n.status = patch.status;

    const el = nodeEl(id);
    if (el) { el.innerHTML = nodeHTML(n); bindPorts(el, n); }
    drawWires();
  }

  function onGenEvent(type, ev) {
    if (type === 'agent') {
      setAgent(ev.agent, ev.state, ev.msg || ev.state, ev.progress);
      if (ev.msg) addLog('▸ ' + ev.agent + '：' + ev.msg);
      return;
    }
    if (type === 'log') {
      addLog((ev.level === 'warn' ? '⚠ ' : '· ') + ev.text);
      return;
    }
    if (type === 'node') {
      applyNodePatch(ev.node_id, ev.patch || {});
      return;
    }
    if (type === 'done') {
      finishingPromise = finishGeneration(true, ev.stats);
      return;
    }
    if (type === 'error') {
      addLog('✖ ' + (ev.code || 'ERROR') + '：' + (ev.message || '未知错误'));
      toast('生成失败：' + (ev.message || ev.code));
      finishingPromise = finishGeneration(false);
    }
  }

  async function finishGeneration(ok, stats) {
    generating = false;
    const btn = $('btnGenerate');
    btn.classList.remove('running');
    $('genLabel').textContent = ok ? '重新生成剧情' : '重试生成';
    $('agentSummary').textContent = ok ? '已完成' : '已中断';

    if (!ok) {
      ['writer', 'art', 'voice', 'build'].forEach((k) => {
        const el = document.querySelector('.agent[data-k="' + k + '"].run');
        if (el) setAgent(k, 'failed', '中断');
      });
      return;
    }

    if (GalAPI.mode === 'real') {
      await pullGraphFromBackend();
    } else {
      nodes.forEach((n) => {
        if (n.type !== 'ending' && !(n.voice > 0)) n.voice = +(3.2 + Math.random() * 2.6).toFixed(1);
      });
      render();
    }

    if (stats) {
      addLog('✔ ' + stats.nodes + ' 节点 / ' + stats.edges + ' 连线 / ' + stats.images + ' 张图 / ' +
        stats.voices + ' 条语音 / ' + Math.round(stats.elapsed_s || 0) + 's');
    } else {
      addLog('✔ 生成完成');
    }
    toast('生成完成，可以试玩了');
  }

  /** 真后端：把 graph.json 拉回来替换画布 */
  /**
   * 组装要 PUT 回后端的图。
   * ★ 不能直接把前端 nodes 丢过去：后端一个节点是 lines[] 多条台词，
   *   前端只显示合并后的一句。所以以 _raw 为底，只覆盖前端真正改过的字段，
   *   否则「保存一次 = 把多说压成一句」。
   */
  function buildGraphPayload() {
    return {
      version: 1,
      project: graphProject || { id: projectId, name: '未命名' },
      characters: Array.isArray(graphCharacters) ? graphCharacters : [],
      nodes: nodes.map((n) => {
        const base = n._raw
          ? JSON.parse(JSON.stringify(n._raw))
          : {
            id: n.id, type: n.type, title: n.title, status: 'done',
            bg: { asset: 'images/bg/bg1.png', prompt: '', seed: 0 },
            chars: [], lines: [], pos: { x: 0, y: 0 }
          };

        base.id = n.id;
        base.type = n.type;
        base.title = n.title;
        base.pos = { x: Math.round(n.x), y: Math.round(n.y) };

        if (n.type === 'choice') {
          base.choices = (n.choices || []).map((c, k) => {
            const lk = links.filter((l) => l.from === n.id && (l.port || 0) === k)[0];
            return { label: c.label, to: c.to || (lk ? lk.to : '') };
          });
        } else {
          delete base.choices;
        }

        if (n.type === 'ending') base.end_kind = n.endKind || 'true';
        else delete base.end_kind;

        // 台词：保留原有的多条，只同步第一句（前端只显示这一句）
        if (!Array.isArray(base.lines) || !base.lines.length) {
          base.lines = [{ who: n.who || '', text: n.text || '', voice: '', dur: n.voice || 0 }];
        } else {
          base.lines[0].who = n.who || '';
          base.lines[0].text = n.text || '';
        }
        return base;
      }),
      edges: links.map((l, i) => ({ id: l.id || ('e' + (i + 1)), from: l.from, port: l.port || 0, to: l.to }))
    };
  }

  async function saveGraphToBackend(quiet) {
    if (GalAPI.mode === 'mock') {
      if (!quiet) toast('模拟模式下不用保存（切到真后端才有得存）');
      return false;
    }
    if (!projectId) {
      if (!quiet) toast('还没有项目，先点一次「开始生成剧情」');
      return false;
    }
    const btn = $('btnSave');
    if (btn) btn.disabled = true;
    let res;
    try {
      res = await GalAPI.saveGraph(projectId, buildGraphPayload());
    } catch (e) {
      res = { ok: false, detail: String((e && e.message) || e) };
    }
    if (btn) btn.disabled = false;

    if (res && res.ok) {
      addLog('画布已保存：' + nodes.length + ' 节点 / ' + links.length + ' 连线');
      if (!quiet) toast('已保存 ' + nodes.length + ' 节点 / ' + links.length + ' 连线');
      return true;
    }
    const d = (res && res.detail) || '未知错误';
    addLog('保存失败：' + d);
    if (!quiet) toast('保存失败：' + d);
    return false;
  }

  async function pullGraphFromBackend() {
    try {
      const g = await GalAPI.getGraph(projectId);
      if (!g || !Array.isArray(g.nodes) || !g.nodes.length) {
        addLog('后端没返回节点图，保持当前画布');
        return;
      }
      nodes = g.nodes.map((raw, i) => {
        const lines = raw.lines || [];
        return {
          id: raw.id,
          type: raw.type || 'scene',
          title: raw.title || raw.id,
          idx: raw.idx || String(i + 1).padStart(2, '0'),
          x: raw.pos && typeof raw.pos.x === 'number' ? raw.pos.x : 40 + (i % 5) * 330,
          y: raw.pos && typeof raw.pos.y === 'number' ? raw.pos.y : 320,
          who: (lines[0] && lines[0].who) || '',
          text: lines.map((l) => l.text).join(' ') || raw.text || '',
          bg: guessBg(raw),
          bgAsset: (raw.bg && raw.bg.asset) || '',
          charAsset: (raw.chars && raw.chars[0] && raw.chars[0].pose) || '',
          voice: lines.reduce((s, l) => s + (l.dur || 0), 0),
          choices: (raw.choices || []).map((c) => ({ label: c.label, to: c.to })),
          endKind: raw.end_kind === 'true' ? 'true' : raw.end_kind === 'bad' ? 'bad' : raw.end_kind === 'alt' ? 'alt' : undefined,
          status: raw.status || 'done',
          _raw: raw
        };
      });
      links = (g.edges || []).map((e) => ({ id: e.id, from: e.from, port: e.port || 0, to: e.to }));
      graphProject = g.project || null;
      graphCharacters = Array.isArray(g.characters) ? g.characters : [];

      // choice 节点若后端只给 edges 不给 choices，用 edges 兜底
      nodes.forEach((n) => {
        if (n.type === 'choice' && (!n.choices || !n.choices.length)) {
          n.choices = links.filter((l) => l.from === n.id)
            .sort((a, b) => a.port - b.port)
            .map((l, i) => ({ label: '选项 ' + String.fromCharCode(65 + i), to: l.to }));
        }
      });

      selId = null;
      $('inspector').classList.remove('show');
      render();
      fitView();
      addLog('已载入后端节点图：' + nodes.length + ' 节点 / ' + links.length + ' 连线');
    } catch (e) {
      addLog('✖ 拉取节点图失败：' + (e.message || e));
    }
  }

  function guessBg(raw) {
    const s = ((raw.title || '') + ' ' + ((raw.bg && raw.bg.prompt) || '')).toLowerCase();
    if (/黄昏|dusk|楼梯|走廊/.test(s)) return 'dusk';
    if (/清晨|白|站台|dawn|morning/.test(s)) return 'white';
    return 'rainy';
  }

  async function generate() {
    if (generating) return;
    const req = $('reqText').value.trim();
    if (req.length < 6) { toast('需求太短了，多写一点'); return; }

    generating = true;
    toggleLog(true);            // 开始生成时自动展开日志
    const btn = $('btnGenerate');
    btn.classList.add('running');
    $('genLabel').textContent = '准备中…';
    $('agentSummary').textContent = '运行中';
    ['writer', 'art', 'voice', 'build'].forEach((k) => setAgent(k, 'idle', '待机', 0));

    addLog('任务开始 · 需求 ' + req.length + ' 字 · ' +
      (GalAPI.mode === 'mock' ? '模拟数据' : '真后端 ' + GalAPI.base));

    try {
      await ensureProjectId();
    } catch (e) {
      addLog('✖ 建项目失败：' + (e.message || e));
      finishGeneration(false);
      return;
    }

    const params = {
      node_count: +$('rngNodes').value,
      branch_depth: +$('rngBranch').value,
      auto_voice: true,
      consistency: true,
      resolution: [1920, 1080]
    };

    await GalAPI.runGeneration(
      { projectId: projectId, requirement: req, params: params },
      onGenEvent
    );

    // 兜底：流结束但没收到 done/error（比如 SSE 静默断开）
    if (generating) {
      addLog('✖ 进度流结束，未收到完成事件');
      finishingPromise = finishGeneration(false);
    }

    // 等收尾真正做完（含从后端拉图），否则画布会短暂停在旧图
    if (finishingPromise) {
      await finishingPromise;
      finishingPromise = null;
    }
  }
  $('btnGenerate').addEventListener('click', generate);

  $('btnFillDemo').addEventListener('click', () => {
    $('reqText').value = '一个雨夜，天台。主角和从小一起长大的她说好要一起离开这座城。她先到了，手里攥着两张车票。要求：8 个节点左右，两条分支，一个真结局一个坏结局，对话克制、留白，不要独白太多。';
    updateCount();
    toast('已填入示例需求');
  });

  function updateCount() { $('reqCount').textContent = $('reqText').value.length + ' 字'; }
  $('reqText').addEventListener('input', updateCount);
  updateCount();

  $('rngNodes').addEventListener('input', (e) => { $('lblNodes').textContent = e.target.value; });
  $('rngBranch').addEventListener('input', (e) => { $('lblBranch').textContent = e.target.value; });

  /* ---------------- 试玩播放器 ---------------- */
  const pl = { cur: '', typing: null, text: '', step: 'talk', history: [] };

  function startNodeId() {
    const s = nodes.find((x) => x.type === 'start') || nodes[0];
    return s ? s.id : 'n1';
  }

  function openPlayer() {
    $('player').classList.add('show');
    pl.history = [];
    pl.cur = '';
    showNode(startNodeId());
  }
  $('btnPlay').addEventListener('click', openPlayer);
  $('plClose').addEventListener('click', () => $('player').classList.remove('show'));
  $('plBack').addEventListener('click', (e) => {
    e.stopPropagation();
    const prev = pl.history.pop();
    if (prev) showNode(prev, true);
    else toast('已经是第一句了');
  });
  $('plRestart').addEventListener('click', (e) => {
    e.stopPropagation();
    pl.history = [];
    pl.cur = '';
    showNode(startNodeId());
    toast('从头开始');
  });
  $('player').addEventListener('click', (e) => { if (e.target === $('player')) $('player').classList.remove('show'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('player').classList.remove('show'); });

  function showNode(id, fromBack) {
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    if (!fromBack && pl.cur && pl.cur !== id) pl.history.push(pl.cur);
    pl.cur = id;
    pl.step = 'talk';
    $('pEnding').style.display = 'none';
    $('pChoices').classList.remove('show');
    $('pDlg').style.display = '';

    // 背景
    const bg = $('pBg');
    bg.className = 'bg ' + (n.bg === 'dusk' ? 'dusk' : n.bg === 'white' ? 'white' : 'rainy');
    $('pRain').style.display = n.bg === 'rainy' ? '' : 'none';
    $('pChar').style.display = n.type === 'ending' ? 'none' : '';

    $('pWho').textContent = n.who || '旁白';
    $('pWho').style.display = n.who ? '' : 'none';
    $('plNode').textContent = n.id;
    $('plIdx').textContent = String(nodes.indexOf(n) + 1);
    $('plTotal').textContent = String(nodes.length);
    $('plVoice').textContent = n.voice > 0 ? n.voice.toFixed(1) + 's' : '无';

    typeText($('pTxt'), n.text || '');
  }

  function typeText(el, str) {
    if (pl.typing) { clearInterval(pl.typing); pl.typing = null; }
    pl.text = str;
    el.textContent = '';
    let i = 0;
    pl.typing = setInterval(() => {
      i++;
      el.textContent = str.slice(0, i);
      if (i >= str.length) { clearInterval(pl.typing); pl.typing = null; }
    }, 26);
  }

  function finishTyping() {
    if (!pl.typing) return false;
    clearInterval(pl.typing);
    pl.typing = null;
    $('pTxt').textContent = pl.text;
    return true;
  }

  $('stage').addEventListener('click', (e) => {
    if (e.target.closest('.choices')) return;
    if (finishTyping()) return;

    const n = nodes.find((x) => x.id === pl.cur);
    if (!n) return;

    if (n.type === 'ending') {
      showEnding(n);
      return;
    }

    if (n.type === 'choice' && pl.step === 'talk') {
      pl.step = 'choice';
      showChoices(n);
      return;
    }

    const next = links.find((l) => l.from === n.id);
    if (next) showNode(next.to);
    else toast('这个节点后面没接线了');
  });

  /** choice 节点现在的出口以「连线」为准，choices 只负责选项文字 */
  function choiceTargets(n) {
    const ls = links.filter((l) => l.from === n.id).sort((a, b) => (a.port || 0) - (b.port || 0));
    const labels = (n.choices || []).map((c) => c.label);
    if (ls.length) {
      return ls.map((l, i) => ({
        label: labels[i] || ('选项 ' + String.fromCharCode(65 + i)),
        to: l.to
      }));
    }
    return (n.choices || []).map((c) => ({ label: c.label, to: c.to }));
  }

  function showChoices(n) {
    const box = $('pChoiceBox');
    box.innerHTML = '';
    const targets = choiceTargets(n);
    if (!targets.length) {
      const b = document.createElement('button');
      b.textContent = '（这个分支还没接线）';
      b.addEventListener('click', (e) => e.stopPropagation());
      box.appendChild(b);
    }
    targets.forEach((c) => {
      const b = document.createElement('button');
      b.textContent = c.label;
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        $('pChoices').classList.remove('show');
        if (c.to) showNode(c.to);
        else toast('这个选项还没连到节点');
      });
      box.appendChild(b);
    });
    $('pChoices').classList.add('show');
  }

  function showEnding(n) {
    $('pDlg').style.display = 'none';
    $('pEnding').style.display = 'grid';
    $('pEndTitle').textContent = n.endKind === 'true' ? '真结局' : n.endKind === 'alt' ? '隐藏结局' : '坏结局';
    $('pEndTitle').style.color = n.endKind === 'true' ? '#f8fafc' : n.endKind === 'alt' ? '#a5f3fc' : '#fda4af';
    $('pEndSub').textContent = n.title;
  }

  /* ---------------- 接口配置：表单 ↔ 后端（字段名见 提示词-后端.md §3.3） ---------------- */
  const PROVIDER_BY_TITLE = [
    [/通义千问|千问/, 'qwen_image'],
    [/百炼|CosyVoice/i, 'bailian_tts'],
    [/大语言模型/, 'llm'],
    [/ComfyUI/i, 'comfyui'],
    [/本地 TTS/i, 'local_tts'],
    [/本地 LLM/i, 'local_llm']
  ];

  const FIELD_BY_LABEL = {
    qwen_image:  { 'API Key': 'api_key', '模型': 'model', '默认尺寸': 'size' },
    bailian_tts: { 'API Key': 'api_key', '音色': 'voice', '语速': 'speed' },
    llm:         { 'Base URL': 'base_url', 'API Key': 'api_key', '模型': 'model', '温度': 'temperature' },
    comfyui:     { '服务地址': 'base_url', '工作流': 'workflow_bg', '采样': 'sampler' },
    local_tts:   { '服务地址': 'base_url', '引擎': 'engine', '参考音频': 'ref_audio', '参考文本': 'ref_text' },
    local_llm:   { 'Base URL': 'base_url', '模型': 'model', '用途': 'usage' }
  };

  function providerOf(card) {
    const t = (card.querySelector('.api-head .t') || {}).textContent || '';
    for (const pair of PROVIDER_BY_TITLE) if (pair[0].test(t)) return pair[1];
    return null;
  }

  /** 后端 DEFAULTS 的字段白名单（多一个就 422），见 app/config.py */
  const BACKEND_FIELDS = {
    qwen_image:  ['enabled', 'api_key', 'model', 'base_url'],
    bailian_tts: ['enabled', 'api_key', 'model', 'voice', 'speed', 'base_url'],
    llm:         ['enabled', 'api_key', 'base_url', 'model', 'temperature'],
    comfyui:     ['enabled', 'base_url', 'workflow_bg', 'workflow_char', 'concurrency'],
    local_tts:   ['enabled', 'base_url', 'engine'],
    local_llm:   ['enabled', 'base_url', 'model']
  };

  /** 把表单值洗成后端能收的形状：去中文后缀、数值转 number、跳过掩码假 key */
  function normalizeProvider(kind, raw) {
    const allow = BACKEND_FIELDS[kind] || [];
    const out = {};
    allow.forEach((k) => {
      if (!(k in raw)) return;
      const v = raw[k];
      if (k === 'speed' || k === 'temperature') {
        const n = parseFloat(v);
        if (!isNaN(n)) out[k] = n;
        return;
      }
      if (typeof v === 'string') {
        if (k === 'api_key' && /^sk-[•*]/.test(v)) return;   // 预填的假 key 不写库
        const m = v.match(/^([^（(]+)/);                     // "longxiaochun（女·温柔）" -> "longxiaochun"
        const s = (m ? m[1] : v).trim();
        if ((k === 'base_url' || k === 'api_key' || k === 'model') && s === '') return;  // 空值不发，保留后端默认
        out[k] = s;
      } else {
        out[k] = v;
      }
    });
    return out;
  }

  /** 从卡片读值 → 契约里的 provider 配置对象 */
  function collectProviders() {
    const out = {};
    document.querySelectorAll('.api-card').forEach((card) => {
      const kind = providerOf(card);
      if (!kind) return;
      const map = FIELD_BY_LABEL[kind] || {};
      const obj = {};
      card.querySelectorAll('.field').forEach((f) => {
        const lab = ((f.querySelector('label') || {}).textContent || '').trim();
        const ctrl = f.querySelector('input, select');
        if (!ctrl || !lab) return;
        const hit = Object.keys(map).find((k) => lab.indexOf(k) === 0);
        if (hit) obj[map[hit]] = ctrl.value;
      });
      const NEED_KEY = ['qwen_image', 'bailian_tts', 'llm'];
      obj.enabled = NEED_KEY.indexOf(kind) >= 0
        ? !!(obj.api_key && String(obj.api_key).trim())
        : !!(obj.base_url && String(obj.base_url).trim());
      out[kind] = normalizeProvider(kind, obj);
    });
    return out;
  }

  function applyProviders(cfg) {
    if (!cfg) return;
    document.querySelectorAll('.api-card').forEach((card) => {
      const kind = providerOf(card);
      if (!kind || !cfg[kind]) return;
      const map = FIELD_BY_LABEL[kind] || {};
      card.querySelectorAll('.field').forEach((f) => {
        const lab = ((f.querySelector('label') || {}).textContent || '').trim();
        const ctrl = f.querySelector('input, select');
        if (!ctrl || !lab) return;
        const hit = Object.keys(map).find((k) => lab.indexOf(k) === 0);
        if (!hit) return;
        const v = cfg[kind][map[hit]];
        if (v == null || v === '***') return;   // 后端不回传密钥明文
        if (ctrl.tagName === 'SELECT') {
          const vals = Array.from(ctrl.options).map((o) => o.value);
          if (vals.indexOf(String(v)) < 0) return;   // 后端回来的值不在选项里就不动
        }
        ctrl.value = v;
      });
    });
  }

  async function loadProviders() {
    const cfg = await GalAPI.getProviders();
    if (cfg) { applyProviders(cfg); addLog('已载入接口配置'); }
  }

  async function saveProviders(silent) {
    const cfg = collectProviders();
    const r = await GalAPI.saveProviders(cfg);
    if (!silent) {
      if (r && r.ok) {
        addLog('✔ 接口配置已保存（' + Object.keys(cfg).length + ' 项）');
        toast('接口配置已保存');
      } else {
        addLog('✖ 保存接口配置失败：' + ((r && r.detail) || '未知'));
        toast('保存失败：' + ((r && r.detail) || '未知'));
      }
    }
    return r;
  }

  /* ---------------- 测试连接（mock 模拟 / real 真调） ---------------- */
  const KIND_BY_NAME = {
    '千问生图': 'qwen_image', '百炼 TTS': 'bailian_tts', 'LLM': 'llm',
    'ComfyUI': 'comfyui', '本地 TTS': 'local_tts', '本地 LLM': 'local_llm'
  };

  document.querySelectorAll('.test').forEach((b) => {
    b.addEventListener('click', async () => {
      const card = b.closest('.api-card');
      const hint = card.querySelector('.hint');
      const dot = card.querySelector('.dot');
      const name = b.dataset.name;
      const kind = KIND_BY_NAME[name] || 'llm';

      hint.className = 'hint';
      hint.textContent = '测试中…';
      dot.className = 'dot run';
      addLog('测试连接：' + name + '（' + kind + '）');

      const r = await GalAPI.testProvider(kind);
      if (r && r.ok) {
        dot.className = 'dot ok';
        hint.className = 'hint ok';
        hint.textContent = '连接正常 · ' + (r.latency_ms || 0) + 'ms';
        addLog('✔ ' + name + ' 正常 ' + (r.latency_ms || 0) + 'ms');
        toast(name + ' 连接正常');
      } else {
        dot.className = 'dot';
        hint.className = 'hint bad';
        hint.textContent = (r && r.detail) || '连接失败';
        addLog('✖ ' + name + ' 失败：' + ((r && r.detail) || '未知原因'));
        toast(name + ' 连不上');
      }
      updateGlobalMeta();
    });
  });

  /* ---------------- 模拟 / 真后端 切换 ---------------- */
  function updateModeUI() {
    const badge = $('modeBadge');
    const real = GalAPI.mode === 'real';
    badge.textContent = real ? (GalAPI.online ? '真后端' : '离线') : '模拟';
    badge.className = 'mode-badge' + (real ? (GalAPI.online ? ' real' : ' off') : '');
    document.querySelectorAll('#modeSwitch button').forEach((b) => {
      b.classList.toggle('on', b.dataset.mode === GalAPI.mode);
    });
  }

  document.querySelectorAll('#modeSwitch button').forEach((b) => {
    b.addEventListener('click', async () => {
      const m = b.dataset.mode;
      if (m === GalAPI.mode && m === 'mock') return;
      GalAPI.setMode(m);
      projectId = null;
      updateModeUI();

      if (m === 'real') {
        toast('正在探测 ' + GalAPI.base + ' …');
        addLog('探测后端 ' + GalAPI.base);
        const h = await GalAPI.detect();
        updateModeUI();
        if (h) {
          addLog('✔ 后端已连接 v' + (h.version || '?') + (h.mock ? '（mock 开关开着）' : ''));
          toast('后端已连接 v' + (h.version || '?'));
        } else {
          addLog('✖ 连不上后端 ' + GalAPI.base + (GalAPI.lastError ? '（' + GalAPI.lastError + '）' : ''));
          toast('连不上后端，生成会失败；可切回模拟数据');
        }
      } else {
        addLog('已切回模拟数据模式');
        toast('已切回模拟数据');
      }
    });
  });

  $('btnResetKeys').addEventListener('click', () => {
    document.querySelectorAll('.api-card input[type=password]').forEach((i) => { i.value = ''; });
    try { localStorage.removeItem('gal_providers'); } catch (e) {}
    addLog('已清空本页密钥');
    toast('已清空密钥（后端凭据文件没动，需在设置里删）');
  });

  /* ---------------- 导出 ---------------- */
  document.querySelectorAll('.exp-btn').forEach((b) => {
    b.addEventListener('click', async () => {
      const fmt = b.dataset.format;
      const name = b.textContent;
      b.disabled = true;
      b.textContent = '导出中…';
      addLog('开始导出：' + name + '（' + fmt + '）');
      try {
        await ensureProjectId();
      } catch (e) {
        addLog('✖ 建项目失败：' + (e.message || e));
        toast('导出失败：建项目失败');
        b.disabled = false;
        b.textContent = name;
        return;
      }
      const r = await GalAPI.exportProject(projectId, fmt);
      b.disabled = false;
      b.textContent = name;
      if (r && r.ok) {
        addLog('✔ 导出完成：' + (r.out || fmt));
        toast('导出完成');
      } else {
        addLog('✖ 导出失败：' + ((r && r.detail) || '未知'));
        toast('导出失败：' + ((r && r.detail) || '未知'));
      }
    });
  });

  /* ---------------- 低功耗模式 ---------------- */
  function applyLowPower(on, quiet) {
    document.body.classList.toggle('low-power', on);
    const b = $('btnPerf');
    if (b) b.classList.toggle('on', on);
    try { localStorage.setItem('gal_lowpower', on ? '1' : '0'); } catch (e) {}
    if (!quiet) toast(on ? '低功耗：动画与毛玻璃已关闭' : '已恢复动效');
  }
  $('btnPerf').addEventListener('click', () => {
    applyLowPower(!document.body.classList.contains('low-power'));
  });

  /* ---------------- 启动 ---------------- */
  if (localStorage.getItem('gal_lowpower') === '1') applyLowPower(true, true);
  render();
  applyTransform();
  updateGlobalMeta();
  updateModeUI();
  updateHistoryUI();
  loadProviders();
  if (GalAPI.mode === 'real') {
    GalAPI.detect().then((h) => {
      updateModeUI();
      addLog(h ? '✔ 后端在线 v' + (h.version || '?') : '✖ 后端离线：' + (GalAPI.lastError || GalAPI.base));
    });
  }
  addLog('接口就绪：千问生图 / 百炼 TTS / DeepSeek；本地 ComfyUI 在线，本地 TTS 未启动');
  addLog('项目「雨停之前」已载入：9 节点 / 8 连线');

  // 支持 ?view=editor / ?view=export / &player=1 直接跳到某页（也方便截图自检）
  const qp = new URLSearchParams(location.search);

  if (qp.get('lowpower') === '1') applyLowPower(true, true);
  if (qp.get('mode') === 'real') {
    GalAPI.setMode('real');
    updateModeUI();
    GalAPI.detect().then(() => updateModeUI());
  }

  if (qp.get('modetest') === '1') {
    (async () => {
      const h = await GalAPI.detect();
      updateModeUI();
      const collected = collectProviders();
      let save = null;
      try { save = await saveProviders(true); } catch (e) { save = { ok: false, detail: String(e && e.message || e) }; }
      const back = await GalAPI.getProviders();
      document.title = 'MODETEST ' + JSON.stringify({
        mode: GalAPI.mode,
        badge: ($('modeBadge') || {}).textContent || '',
        health: h ? !!h.ok : null,
        save: save,
        providerKeys: back ? Object.keys(back) : null,
        sentKeys: Object.keys(collected).map((k) => k + ':' + Object.keys(collected[k]).join('|'))
      });
    })();
  }

  if (qp.get('exporttest') === '1') {
    (async () => {
      try {
        await ensureProjectId();
        const r = await GalAPI.exportProject(projectId, 'renpy');
        document.title = 'EXPORTTEST ' + JSON.stringify(r);
      } catch (e) {
        document.title = 'EXPORTTEST {"ok":false,"detail":"' + String(e.message || e).replace(/"/g, "'") + '"}';
      }
    })();
  }

  if (qp.get('selftest') === '1') {
    document.title = 'SELFTEST ' + JSON.stringify({
      providers: collectProviders(),
      nodes: nodes.length,
      links: links.length,
      mode: GalAPI.mode,
      expBtns: document.querySelectorAll('.exp-btn').length,
      cards: document.querySelectorAll('.api-card').length
    });
  }

  if (qp.get('undotest') === '1') {
    const before = nodes.length;
    pushHistory('测试加节点');
    nodes.push({ id: 'ntest', type: 'scene', idx: '99', title: '测试节点', x: 0, y: 0, who: '', text: 't', bg: 'rainy', voice: 1 });
    const afterAdd = nodes.length;
    undo();
    const afterUndo = nodes.length;
    redo();
    const afterRedo = nodes.length;
    document.title = 'UNDOTEST ' + JSON.stringify({
      before: before, afterAdd: afterAdd, afterUndo: afterUndo, afterRedo: afterRedo,
      undoBtnOk: !!document.getElementById('btnUndo'),
      redoBtnOk: !!document.getElementById('btnRedo')
    });
  }

  if (qp.get('layouttest') === '1') {
    setTimeout(() => {
      let worldRight = 0, worldBottom = 0;
      nodes.forEach((n) => {
        const el = nodeEl(n.id);
        worldRight = Math.max(worldRight, n.x + 268);
        worldBottom = Math.max(worldBottom, n.y + (el ? el.offsetHeight : 190));
      });
      const rects = {};
      ['n1', 'n6', 'n7', 'n9'].forEach((id) => {
        const el = nodeEl(id);
        if (!el) { rects[id] = null; return; }
        const r = el.getBoundingClientRect();
        rects[id] = { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left) };
      });
      document.title = 'LAYOUTTEST ' + JSON.stringify({
        zoom: +zoom.toFixed(3), panX: Math.round(panX), panY: Math.round(panY),
        wrapW: wrap.clientWidth, wrapH: wrap.clientHeight,
        worldRight: worldRight, worldBottom: worldBottom,
        screenRight: Math.round(panX + worldRight * zoom),
        screenBottom: Math.round(panY + worldBottom * zoom),
        overflowX: panX + worldRight * zoom > wrap.clientWidth,
        overflowY: panY + worldBottom * zoom > wrap.clientHeight,
        rects: rects,
        rawW: nodeEl('n7') ? nodeEl('n7').offsetWidth : -1
      });
    }, 200);
  }

  if (qp.get('autogen') === '1') {
    setTimeout(async () => {
      try { await generate(); } catch (e) { /* 失败也照常上报 */ }
      const logs = ($('logBox').textContent || '').trim().split('\n').slice(-4);
      document.title = 'AUTOGEN ' + JSON.stringify({
        mode: GalAPI.mode,
        nodes: nodes.length,
        links: links.length,
        genLabel: ($('genLabel') || {}).textContent || '',
        logs: logs
      });
    }, 400);
  }

  if (qp.get('playtest') === '1') {
    setTimeout(() => {
      openPlayer();
      const start = startNodeId();
      const e1 = links.find((l) => l.from === start);
      if (e1) showNode(e1.to);
      const e2 = links.find((l) => l.from === pl.cur);
      if (e2) showNode(e2.to);
      const afterFwd = pl.cur;
      const histLen = pl.history.length;
      $('plBack').click();
      const afterBack = pl.cur;
      $('plRestart').click();
      const afterRestart = pl.cur;
      document.title = 'PLAYTEST ' + JSON.stringify({
        start: start, afterFwd: afterFwd, histLen: histLen,
        afterBack: afterBack, afterRestart: afterRestart,
        backBtn: !!$('plBack'), restartBtn: !!$('plRestart')
      });
    }, 500);
  }

  if (qp.get('edittest') === '1') {
    setTimeout(() => {
      const n = nodes.find((x) => x.type === 'scene') || nodes[0];
      const el = nodeEl(n.id);
      const oldText = n.text;
      const histBefore = hist.undo.length;
      const lineEl = el.querySelector('.line');
      if (lineEl) lineEl.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      const ta = el.querySelector('.inline-edit');
      const hasTa = !!ta;
      if (ta) {
        ta.value = '改过的台词测试';
        ta.dispatchEvent(new Event('blur'));
      }
      const afterText = (nodes.find((x) => x.id === n.id) || {}).text;
      document.title = 'EDITTEST ' + JSON.stringify({
        node: n.id, hasTa: hasTa, oldText: oldText, afterText: afterText,
        histDelta: hist.undo.length - histBefore,
        taGone: !el.querySelector('.inline-edit')
      });
    }, 400);
  }

  if (qp.get('linktest') === '1') {
    setTimeout(() => {
      const before = links.length;

      addNodeOfType('scene');
      const newNode = nodes[nodes.length - 1];
      const newId = newNode.id;
      const nodesAfterAdd = nodes.length;
      const newType = newNode.type;

      links.push({ from: newId, port: 0, to: nodes[1].id });
      drawWires();
      const afterLink = links.length;

      selLink = links.findIndex((l) => l.from === newId);
      const selOk = selLink >= 0;
      deleteLink();
      const afterUnlink = links.length;
      const stillHas = links.some((l) => l.from === newId);

      addNodeOfType('choice');
      const ch = nodes[nodes.length - 1];
      addNodeOfType('ending');
      const end = nodes[nodes.length - 1];

      document.title = 'LINKTEST ' + JSON.stringify({
        linksBefore: before,
        nodesAfterAdd: nodesAfterAdd,
        newType: newType,
        afterLink: afterLink,
        selOk: selOk,
        afterUnlink: afterUnlink,
        stillHas: stillHas,
        choiceOuts: (ch.choices || []).length,
        choicePorts: document.querySelectorAll('#nd-' + ch.id + ' .port.out').length,
        endKind: end.endKind,
        endPorts: document.querySelectorAll('#nd-' + end.id + ' .port.out').length
      });
    }, 400);
  }

  if (qp.get('menu') === '1') {
    setTimeout(() => { $('btnAdd').click(); }, 300);
  }

  if (qp.get('cuttest') === '1') {
    setTimeout(() => {
      const na = nodes.find((x) => x.id === 'n1');
      const nb = nodes.find((x) => x.id === 'n2');
      const p1 = outPos(na, 0), p2 = inPos(nb);
      const pts = bezierPoints(p1, p2, 24);
      const mid = pts[12];
      const hitA = { x: mid.x, y: mid.y - 50 }, hitB = { x: mid.x, y: mid.y + 50 };
      let hit = false;
      for (let k = 0; k < pts.length - 1; k++) {
        if (segIntersect(pts[k], pts[k + 1], hitA, hitB)) { hit = true; break; }
      }
      const misA = { x: mid.x + 400, y: mid.y - 50 }, misB = { x: mid.x + 400, y: mid.y + 50 };
      let miss = false;
      for (let k = 0; k < pts.length - 1; k++) {
        if (segIntersect(pts[k], pts[k + 1], misA, misB)) { miss = true; break; }
      }
      document.title = 'CUTTEST ' + JSON.stringify({ hit: hit, missHit: miss, points: pts.length });
    }, 300);
  }
  if (qp.get('savetest') === '1') {
    setTimeout(async () => {
      const info = { mode: GalAPI.mode, projectId: projectId };
      if (!projectId) {
        document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: '没有项目' }));
        return;
      }
      try {
        await pullGraphFromBackend();
        const n0 = nodes[0];
        if (!n0) {
          document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: '没有节点' }));
          return;
        }
        const beforePos = { x: n0.x, y: n0.y };
        const beforeLines = (n0._raw && Array.isArray(n0._raw.lines)) ? n0._raw.lines.length : 0;
        const targetX = Math.round(beforePos.x) + 77;
        const targetY = Math.round(beforePos.y) + 33;
        n0.x = targetX;
        n0.y = targetY;
        let putRes = null;
        let putErr = '';
        try {
          putRes = await GalAPI.saveGraph(projectId, buildGraphPayload());
        } catch (e2) {
          putErr = String((e2 && e2.message) || e2);
        }
        const ok = !!(putRes && putRes.ok);
        const putDetail = putErr || (putRes && putRes.detail) || '';
        const putStatus = (putRes && putRes.status) || 0;
        const payloadProject = (graphProject && graphProject.id) || null;
        await pullGraphFromBackend();
        const n1 = nodes.filter((x) => x.id === n0.id)[0];
        const afterPos = n1 ? { x: n1.x, y: n1.y } : null;
        const afterLines = (n1 && n1._raw && Array.isArray(n1._raw.lines)) ? n1._raw.lines.length : 0;
        let posMatched = false;
        if (afterPos) {
          posMatched = Math.abs(afterPos.x - targetX) < 2 && Math.abs(afterPos.y - targetY) < 2;
        }
        document.title = 'SAVETEST ' + JSON.stringify({
          saveOk: ok,
          putStatus: putStatus,
          putDetail: putDetail,
          payloadProjectId: payloadProject,
          projectId: projectId,
          beforePos: beforePos,
          afterPos: afterPos,
          targetPos: { x: targetX, y: targetY },
          posMatched: posMatched,
          beforeLines: beforeLines,
          afterLines: afterLines,
          linesKept: afterLines >= beforeLines && afterLines > 0
        });
      } catch (err) {
        document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: String(err && err.message || err) }));
      }
    }, 6500);
  }

  if (qp.get('porttest') === '1') {
    setTimeout(() => {
      const n = nodes[1] || nodes[0];
      const el = nodeEl(n.id);
      const port = el ? el.querySelector('.port.out') : null;
      const info = {
        node: n.id,
        portFound: !!port,
        linkingBefore: !!linking,
        pendingBefore: !!pendingFrom
      };
      if (port) {
        const r = port.getBoundingClientRect();
        info.portSize = { w: Math.round(r.width), h: Math.round(r.height) };
        info.portCenter = { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        info.hitElement = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          if (!e) return 'null';
          return e.className + (e.id ? '#' + e.id : '');
        })();
        // 热区检查：偏离中心 8px 还能不能点中端口
        (function () {
          const cs = getComputedStyle(port, '::before');
          info.before = {
            display: cs.display, position: cs.position,
            inset: cs.inset, width: cs.width, height: cs.height,
            pe: cs.pointerEvents
          };
          const ns = getComputedStyle(el);
          info.nodeOverflow = ns.overflow;
          info.nodeZ = ns.zIndex;
          const portRect = port.getBoundingClientRect();
          info.portRect = { x: Math.round(portRect.x), y: Math.round(portRect.y), w: Math.round(portRect.width), h: Math.round(portRect.height) };
        })();
        info.hitOffset8 = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2 + 8, r.y + r.height / 2);
          if (!e) return 'null';
          return String(e.className || e.tagName);
        })();
        info.hitOffset12 = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2 + 12, r.y + r.height / 2);
          if (!e) return 'null';
          return String(e.className || e.tagName);
        })();
        port.dispatchEvent(new PointerEvent('pointerdown', {
          bubbles: true, cancelable: true, button: 0, pointerId: 1,
          clientX: r.x + r.width / 2, clientY: r.y + r.height / 2
        }));
        info.linkingAfter = !!linking;
        info.tempPaths = document.querySelectorAll('.wire-temp').length;
        // 收尾，避免残留
        document.dispatchEvent(new PointerEvent('pointerup', {
          bubbles: true, button: 0, pointerId: 1,
          clientX: r.x + r.width / 2, clientY: r.y + r.height / 2
        }));
        info.linkingAfterUp = !!linking;
        info.pendingAfterUp = !!pendingFrom;
      }
      document.title = 'PORTTEST ' + JSON.stringify(info);
    }, 500);
  }

  const qv = qp.get('view');
  if (qv && views[qv]) go(qv);
  if (qp.get('player') === '1') {
    openPlayer();
    const nid = qp.get('node');
    if (nid) {
      showNode(nid);
      if (qp.get('choices') === '1') {
        const n = nodes.find((x) => x.id === nid);
        if (n && n.type === 'choice') { pl.step = 'choice'; showChoices(n); }
      }
    }
  }
})();
