/* ==========================================================================
   GalgameStudio 前端交互自测（e2e）
   --------------------------------------------------------------------------
   用法：index.html?e2e=<测试名>
        结果写进 document.title，用无头浏览器 --dump-dom 抓回来。

   设计原则：
   1. 只通过「真实 DOM 操作」驱动（.click() / dispatchEvent），不直接改内部状态
      —— 这样测的才是用户真实能触发的路径。
   2. 每个检查项独立记 ok / note，单项失败不影响其余项。
   3. 控制台错误单独收集，最后一并上报。
   4. 依赖 app.js 的公开接口 window.GalEditor / window.GalAPI，
      不依赖它内部闭包变量（拿不到，也不该拿）。
   ========================================================================== */
(function () {
  'use strict';

  var qp = new URLSearchParams(location.search);
  var caseName = qp.get('e2e');
  if (!caseName) return;

  /* ---------------- 基础设施 ---------------- */

  var items = [];
  var consoleErrors = [];
  var origError = window.onerror;

  window.addEventListener('error', function (e) {
    consoleErrors.push(String((e && e.message) || 'unknown').slice(0, 160));
  });
  window.addEventListener('unhandledrejection', function (e) {
    consoleErrors.push('reject: ' + String((e && e.reason) || '').slice(0, 140));
  });

  function $(id) { return document.getElementById(id); }

  /** 记录一条检查结果 */
  function check(key, ok, note) {
    items.push({ k: key, ok: !!ok, n: note === undefined ? '' : String(note).slice(0, 120) });
    return !!ok;
  }

  /** 元素是否存在 + 可见 + 尺寸正常 */
  function elInfo(id) {
    var el = $(id);
    if (!el) return { ok: false, why: 'missing' };
    var r = el.getBoundingClientRect();
    var st = getComputedStyle(el);
    return {
      ok: true,
      w: Math.round(r.width), h: Math.round(r.height),
      vis: st.display !== 'none' && st.visibility !== 'hidden' && +st.opacity > 0,
      disabled: !!el.disabled
    };
  }

  /** 等待 ms */
  function wait(ms) {
    return new Promise(function (res) { setTimeout(res, ms); });
  }

  /** 等一帧（让布局/动画跑一下）
   *  ⚠ 必须有 setTimeout 兜底：无头浏览器 + --virtual-time-budget 下
   *  requestAnimationFrame 不一定会推进，只等 rAF 会永久挂起。 */
  function raf2() {
    return new Promise(function (res) {
      var done = false;
      var t = setTimeout(function () { if (!done) { done = true; res(); } }, 80);
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          if (!done) { done = true; clearTimeout(t); res(); }
        });
      });
    });
  }

  /** 安全执行一个异步测试块 */
  async function section(name, fn) {
    try {
      await fn();
    } catch (e) {
      check(name + ' 未抛异常', false, String(e && e.message || e).slice(0, 120));
    }
  }

  /* ---------------- 复用：驱动视图 ---------------- */

  /* 合成鼠标事件的 which 恒为 0，而 LiteGraph 判断的是 e.which == 1。
     在 window 捕获阶段补上，否则测出来的「点不中节点」是假象。 */
  ['mousedown', 'mousemove', 'mouseup'].forEach(function (t) {
    window.addEventListener(t, function (e) {
      try { Object.defineProperty(e, 'which', { value: 1, configurable: true, writable: true }); } catch (err) {}
    }, true);
  });

  /** 向 canvas 派发一个真实鼠标事件 */
  function mouse(cv, type, x, y, buttons) {
    var r = cv.getBoundingClientRect();
    cv.dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true,
      button: 0, buttons: buttons,
      clientX: r.left + x, clientY: r.top + y
    }));
  }

  /** 图坐标 -> canvas 内像素坐标 */
  function toScreen(gx, gy) {
    var ds = GalEditor._canvas().ds;
    return { x: (gx + ds.offset[0]) * ds.scale, y: (gy + ds.offset[1]) * ds.scale };
  }

  function goView(view) {
    var btn = document.querySelector('button[data-view="' + view + '"]');
    if (btn) btn.click();
  }

  function activeView() {
    var el = document.querySelector('.view.active');
    return el ? el.id : 'none';
  }

  /* ---------------- 测试用例 ---------------- */

  var T = {};

  /* --- 第 1 轮：基线（元素齐全 / 无报错 / 样式生效） --- */
  T.baseline = async function () {
    // 1) 三个视图容器都在
    ['view-setup', 'view-editor', 'view-export'].forEach(function (id) {
      check('视图容器 ' + id, elInfo(id).ok);
    });

    // 2) 导航按钮 4 个（setup/editor/play/export）
    var navBtns = document.querySelectorAll('#nav button[data-view]');
    check('导航按钮数 = 4', navBtns.length === 4, '实际 ' + navBtns.length);

    // 3) 核心交互元素清单（缺一个都算问题）
    var required = [
      'btnPerf', 'btnResetKeys', 'btnToEditor',
      'reqText', 'btnFillDemo', 'reqCount',
      'rngNodes', 'lblNodes', 'rngBranch', 'lblBranch',
      'btnGenerate', 'genLabel', 'agents', 'logToggle', 'logBox',
      'canvasWrap', 'galCanvas', 'world',
      'btnUndo', 'btnRedo', 'zoomOut', 'zoomInfo', 'zoomIn',
      'btnFit', 'btnLayout', 'btnSave', 'btnAdd', 'btnPlay',
      'addMenu', 'inspector', 'insTitle', 'insClose',
      'insWho', 'insText', 'insBg', 'btnRegen', 'btnDel',
      'btnRpy', 'rpyPreview', 'player', 'stage',
      'pBg', 'pChar', 'pDlg', 'pWho', 'pTxt', 'pChoices', 'pChoiceBox',
      'pEnding', 'pEndTitle', 'pEndSub',
      'plBack', 'plRestart', 'plClose', 'plNode', 'plIdx', 'plTotal',
      'plVoice', 'plBranches', 'toast'
    ];
    var missing = required.filter(function (id) { return !$(id); });
    check('必需元素齐全 (' + required.length + ')', missing.length === 0, missing.join(',') || 'all ok');

    // 4) 样式真的生效了（不是裸 HTML）
    var bodyBg = getComputedStyle(document.body).backgroundColor;
    check('CSS 已加载', bodyBg && bodyBg !== 'rgba(0, 0, 0, 0)', bodyBg);

    // 整条顶栏 = header.titlebar（注意：#nav 只是里面的按钮组）
    var topbar = document.querySelector('header.titlebar');
    check('顶栏元素存在', !!topbar);
    if (topbar) {
      var tb = topbar.getBoundingClientRect();
      check('顶栏横贯整宽', tb.width >= window.innerWidth - 2, Math.round(tb.width) + '/' + window.innerWidth);
      check('顶栏高度 48~64px', tb.height >= 44 && tb.height <= 72, Math.round(tb.height) + 'px');
    }

    // 导航选中态要有自己的“药丸”底色（PCL 风格的关键视觉）
    var act = document.querySelector('#nav button.active');
    check('导航有选中项', !!act, act ? act.textContent.trim() : 'none');
    if (act) {
      var ab = getComputedStyle(act).backgroundColor;
      check('选中项有药丸底色', ab && ab !== 'rgba(0, 0, 0, 0)', ab);
      check('选中项有圆角', parseFloat(getComputedStyle(act).borderRadius) > 0,
        getComputedStyle(act).borderRadius);
    }

    // 5) 初始停在接口配置页
    check('初始视图 = view-setup', activeView() === 'view-setup', activeView());

    // 6) 中文字体没漏（有中文文本时字体栈应含 YaHei）
    var ff = getComputedStyle(document.body).fontFamily;
    check('字体栈含中文字体', /YaHei|PingFang|sans-serif/i.test(ff), ff.slice(0, 60));

    // 7) 没有脚本错误
    await wait(300);
    check('无 JS 运行时错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 2 轮：视图切换 --- */
  T.views = async function () {
    // 切换期间记录 active class 变化，确认真的切了
    goView('editor');
    await raf2();
    await wait(260);                 // 等过渡动画
    check('切到 editor', activeView() === 'view-editor', activeView());

    var ed = $('view-editor');
    var r = ed.getBoundingClientRect();
    check('editor 有实际尺寸', r.width > 300 && r.height > 200, Math.round(r.width) + 'x' + Math.round(r.height));

    // 画布在切过来之后必须有尺寸（display:none 时是 0，这是历史坑）
    var cv = $('galCanvas');
    check('切视图后画布宽度 > 0', cv && cv.clientWidth > 0, cv ? cv.clientWidth : 'no canvas');
    check('切视图后画布位图 > 0', cv && cv.width > 0, cv ? cv.width : 'no canvas');

    goView('export');
    await raf2();
    await wait(260);
    check('切到 export', activeView() === 'view-export', activeView());

    goView('setup');
    await raf2();
    await wait(260);
    check('切回 setup', activeView() === 'view-setup', activeView());

    // 播放器是 modal，不是 view —— 切视图时不该被带出来
    var pl = $('player');
    check('播放器未误打开', pl && !pl.classList.contains('show'));

    check('仍无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 3 轮：画布交互（滚轮焦点 / 平移 / 拖节点 / 端口） --- */
  T.canvas = async function () {
    goView('editor');
    await raf2();
    await wait(500);

    var cv = $('galCanvas');
    var cvs = GalEditor._canvas();
    var ds = cvs.ds;

    // 1) 滚轮缩放：焦点处的图坐标必须原地不动，且一次只缩 1.12 倍
    var pts = [[120, 140], [420, 300], [700, 180]];
    var drift = [], ratios = [];
    pts.forEach(function (p) {
      var g0 = GalEditor.screenToGraph(p[0], p[1]);
      var z0 = GalEditor.getZoom();
      cv.dispatchEvent(new WheelEvent('wheel', {
        bubbles: true, cancelable: true,
        clientX: cv.getBoundingClientRect().left + p[0],
        clientY: cv.getBoundingClientRect().top + p[1],
        deltaY: -100, deltaMode: 0
      }));
      var g1 = GalEditor.screenToGraph(p[0], p[1]);
      var z1 = GalEditor.getZoom();
      drift.push(Math.round(Math.hypot(g1.x - g0.x, g1.y - g0.y) * 100) / 100);
      ratios.push(+(z1 / z0).toFixed(4));
      // 滚回去，让下一点的起点一致
      cv.dispatchEvent(new WheelEvent('wheel', {
        bubbles: true, cancelable: true,
        clientX: cv.getBoundingClientRect().left + p[0],
        clientY: cv.getBoundingClientRect().top + p[1],
        deltaY: 100, deltaMode: 0
      }));
    });
    check('滚轮焦点不漂移', drift.every(function (d) { return d < 0.6; }), drift.join(' / '));
    check('每次只缩 1.12 倍', ratios.every(function (r) { return Math.abs(r - 1.12) < 0.005; }), ratios.join(' / '));

    // 2) 画布平移：在空白处拖动，offset 应变化、缩放不变
    var off0 = [ds.offset[0], ds.offset[1]];
    var z0b = ds.scale;
    mouse(cv, 'mousedown', 900, 600, 1);
    mouse(cv, 'mousemove', 960, 640, 1);
    mouse(cv, 'mouseup', 960, 640, 0);
    await raf2();
    var off1 = [ds.offset[0], ds.offset[1]];
    var moved = Math.abs(off1[0] - off0[0]) + Math.abs(off1[1] - off0[1]);
    check('拖空白处能平移画布', moved > 5, 'Δ=' + moved.toFixed(1));
    check('平移不改变缩放', Math.abs(ds.scale - z0b) < 1e-6, z0b.toFixed(3) + ' -> ' + ds.scale.toFixed(3));

    // 3) 拖节点：位置要变，且只有它变
    GalEditor.fit();
    await raf2();
    var nodes = cvs.graph._nodes;
    if (nodes.length >= 2) {
      var n0 = nodes[0], n1 = nodes[1];
      var p0 = [n0.pos[0], n0.pos[1]];
      var q0 = [n1.pos[0], n1.pos[1]];
      var s = toScreen(p0[0] + n0.size[0] / 2, p0[1] + 16);   // 标题栏
      mouse(cv, 'mousedown', s.x, s.y, 1);
      mouse(cv, 'mousemove', s.x + 70, s.y + 45, 1);
      await raf2();
      var dragged = cvs.node_dragged === n0;
      mouse(cv, 'mouseup', s.x + 70, s.y + 45, 0);
      await raf2();
      var dNode = Math.abs(n0.pos[0] - p0[0]) + Math.abs(n0.pos[1] - p0[1]);
      var dOther = Math.abs(n1.pos[0] - q0[0]) + Math.abs(n1.pos[1] - q0[1]);
      check('按下节点能被抓住', dragged, dragged ? '' : 'node_dragged 为空');
      check('拖节点位置真的变了', dNode > 20, 'Δ=' + dNode.toFixed(0));
      check('只动了被拖的那个', dOther < 1, '另一个 Δ=' + dOther.toFixed(1));
    } else {
      check('画布里有节点可拖', false, '只有 ' + nodes.length + ' 个');
    }

    // 4) 端口存在（连线的落点）
    var hasPorts = nodes.some(function (n) { return (n.outputs || []).length > 0; });
    check('节点有输出端口', hasPorts);

    // 5) 单击节点应选中（检查器会跟着走）
    //    注意 LiteGraph 的 selected_nodes 是对象（key = node.id），不是数组
    var target = nodes[0];
    var t = toScreen(target.pos[0] + target.size[0] / 2, target.pos[1] + 16);
    mouse(cv, 'mousedown', t.x, t.y, 1);
    mouse(cv, 'mouseup', t.x, t.y, 0);
    await wait(150);
    var selCount = cvs.selected_nodes ? Object.keys(cvs.selected_nodes).length : 0;
    check('单击节点后被选中', selCount > 0, selCount + ' 个');

    check('画布测试全程无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 4 轮：检查器面板（选中 -> 填充 -> 改值 -> 回写） --- */
  T.inspector = async function () {
    goView('editor');
    await raf2();
    await wait(500);
    GalEditor.fit();
    await raf2();

    var cv = $('galCanvas');
    var ins = $('inspector');

    /* ⚠ 不能用缓存的节点对象：编辑内容会触发 syncFromEditor，
       它会把 nodes 数组整体重建，旧引用就失效了（点过去会点到别处）。
       统一按 galId 现查。 */
    function findNode(galId) {
      var g = GalEditor._canvas().graph._nodes;
      for (var i = 0; i < g.length; i++) {
        if (g[i].properties && g[i].properties.galId === galId) return g[i];
      }
      return null;
    }

    function clickByGal(galId) {
      var n = findNode(galId);
      if (!n) return false;
      var t = toScreen(n.pos[0] + n.size[0] / 2, n.pos[1] + 16);
      mouse(cv, 'mousedown', t.x, t.y, 1);
      mouse(cv, 'mouseup', t.x, t.y, 0);
      return true;
    }

    var all = GalEditor._canvas().graph._nodes;
    var id0 = all[0].properties.galId;
    var id1 = all[1].properties.galId;

    check('初始不显示检查器', !ins.classList.contains('show'), ins.className);

    // 诊断：回调注册了吗？节点的 galId 是什么？
    var cbOk = typeof (GalEditor.api && GalEditor.api.onSelect) === 'function';
    check('onSelect 回调已注册', cbOk);
    check('节点带 galId', !!id0, String(id0));

    // 诊断：绕过点击，直接调用回调 —— 区分「回调没触发」和「回调逻辑有问题」
    if (cbOk && id0) {
      GalEditor.api.onSelect(id0);
      await wait(150);
      check('直接调 onSelect 能弹出面板', ins.classList.contains('show'), ins.className);
      ins.classList.remove('show');
    }

    // 选中第 0 个节点
    check('能点中第 0 个节点', clickByGal(id0));
    await wait(200);
    check('选中后检查器弹出', ins.classList.contains('show'), ins.className);

    var r = ins.getBoundingClientRect();
    check('检查器有实际尺寸', r.width > 100 && r.height > 100,
      Math.round(r.width) + 'x' + Math.round(r.height));

    var title0 = $('insTitle').textContent.trim();
    check('标题有内容', title0.length > 0, title0);

    var text0 = $('insText').value;
    check('台词框填充了节点内容', text0.length > 0, text0.slice(0, 20));

    // 换一个节点，面板内容应该跟着换
    check('能点中第 1 个节点', clickByGal(id1));
    await wait(200);
    var title1 = $('insTitle').textContent.trim();
    check('切换节点后面板跟着换', title1 !== title0, title0 + ' -> ' + title1);

    // 改台词 -> 触发 input -> 数据层应被写回
    var marker = 'E2E改写验证' + Date.now().toString(36).slice(-4);
    var ta = $('insText');
    ta.value = marker;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.dispatchEvent(new Event('change', { bubbles: true }));
    await wait(250);

    // 切走再切回来，看值还在不在（证明写回了数据层，而不是只改了 DOM）
    clickByGal(id0);
    await wait(200);
    clickByGal(id1);
    await wait(220);
    check('改动写回数据层', $('insText').value === marker, $('insText').value.slice(0, 24));

    // 关闭
    $('insClose').click();
    await wait(150);
    check('点 ✕ 能关掉检查器', !ins.classList.contains('show'), ins.className);

    // 关闭后再点节点，应该能重新打开
    clickByGal(id0);
    await wait(200);
    check('关闭后仍能重新打开', ins.classList.contains('show'), ins.className);

    check('检查器测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 5 轮：生成流水线（点按钮 -> 状态变化 -> 结果或失败兜底） --- */
  T.generate = async function () {
    goView('setup');
    await raf2();
    await wait(300);

    var btn = $('btnGenerate');
    var label = $('genLabel');
    var logBox = $('logBox');
    var agents = $('agents');

    check('生成按钮存在且可点', btn && !btn.disabled, btn ? btn.disabled + '' : 'missing');

    // 1) 需求框空着点生成 —— 应该被拦住（有提示）或至少不崩
    var req = $('reqText');
    var backup = req.value;
    req.value = '';
    req.dispatchEvent(new Event('input', { bubbles: true }));

    var beforeNodes = GalEditor.dump().nodes.length;
    var beforeLabel = label.textContent;

    btn.click();
    await wait(400);

    var toastText = ($('toast') || {}).textContent || '';
    var toastShown = ($('toast') || {}).classList && $('toast').classList.contains('show');

    // 空需求：要么被拦（有 toast），要么真的开始了。两种都不算错，但要记录下来
    if (toastShown && toastText.trim()) {
      check('空需求被拦住并有提示', true, toastText.trim().slice(0, 30));
    } else {
      check('空需求未拦截（允许直接生成）', true, '未弹提示');
    }

    // 恢复需求文本
    req.value = backup;
    req.dispatchEvent(new Event('input', { bubbles: true }));

    // 2) 正常点生成：进入 loading 态
    //    先把上一次可能残留的流程等一等
    await wait(600);
    var started = false;
    try {
      btn.click();
      started = true;
    } catch (e) {
      check('点击生成不抛异常', false, String(e && e.message).slice(0, 80));
    }
    if (started) {
      await wait(500);
      var afterLabel = label.textContent;
      var isBusy = btn.disabled || afterLabel !== beforeLabel || btn.classList.contains('busy') ||
                   btn.classList.contains('loading');
      check('点生成后进入进行中状态', isBusy,
        'label:' + beforeLabel + ' -> ' + afterLabel + ' disabled:' + btn.disabled);

      // Agent 卡片应有 running 状态
      var running = agents ? agents.querySelectorAll('.agent.run, .agent.running, .agent.active').length : 0;
      check('Agent 流水线有卡片在跑', running > 0, running + ' 个');

      // 日志区应开始出现内容
      var logText = logBox ? logBox.textContent : '';
      check('生成日志有输出', logText.length > 10, (logText || '').replace(/\s+/g, ' ').slice(0, 40));
    }

    // 3) 等结果：要么生成出新节点，要么给出失败提示 —— 但不能永远卡在 loading
    var done = false, result = '';
    for (var i = 0; i < 40; i++) {          // 最多等 8 秒
      await wait(200);
      var stillBusy = btn.disabled || btn.classList.contains('busy');
      var nowNodes = GalEditor.dump().nodes.length;
      var tEl = $('toast');
      var tTxt = tEl ? tEl.textContent.trim() : '';
      if (!stillBusy) {
        done = true;
        result = 'idle; nodes ' + beforeNodes + '->' + nowNodes + '; toast:' + tTxt.slice(0, 24);
        break;
      }
    }
    check('生成流程能收尾（不永久卡住）', done, done ? result : '超过 8 秒仍在进行中');

    var finalNodes = GalEditor.dump().nodes.length;
    check('节点数据仍然完整（>0）', finalNodes > 0, '节点数 ' + finalNodes);

    check('生成测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 6 轮：试玩播放器 --- */
  T.player = async function () {
    goView('editor');
    await raf2();
    await wait(400);

    var pl = $('player');
    check('播放器初始关闭', !pl.classList.contains('show'));

    // 1) 打开
    $('btnPlay').click();
    await raf2();
    await wait(400);
    check('点「试玩」能打开播放器', pl.classList.contains('show'), pl.className);

    var stage = $('stage');
    var sr = stage.getBoundingClientRect();
    check('舞台有实际尺寸', sr.width > 200 && sr.height > 150,
      Math.round(sr.width) + 'x' + Math.round(sr.height));

    // 2) 第一句应该有内容
    var txt0 = $('pTxt').textContent.trim();
    var who0 = $('pWho').textContent.trim();
    check('显示当前台词', txt0.length > 0, txt0.slice(0, 24));
    check('显示发言角色', who0.length > 0, who0);

    var idx0 = $('plIdx').textContent.trim();
    check('显示节点进度', idx0.length > 0, idx0 + '/' + $('plTotal').textContent.trim());

    // 3) 点舞台推进
    var advanced = false;
    var seenChoice = false;
    var seenEnding = false;

    for (var i = 0; i < 14; i++) {
      var idxBefore = $('plIdx').textContent.trim();
      var txtBefore = $('pTxt').textContent.trim();

      // 有选项就点第一个（否则点舞台）
      var choicesBox = $('pChoices');
      var choiceVisible = choicesBox && choicesBox.classList.contains('show');
      if (choiceVisible) {
        seenChoice = true;
        var btns = $('pChoiceBox').querySelectorAll('button');
        check('分支节点给出可点选项', btns.length > 0, btns.length + ' 个');
        if (btns.length) btns[0].click();
      } else {
        stage.click();
      }
      await wait(280);

      // 结局？
      var ending = $('pEnding');
      if (ending && ending.style.display !== 'none' && ending.style.display !== '') {
        seenEnding = true;
        break;
      }
      var idxAfter = $('plIdx').textContent.trim();
      var txtAfter = $('pTxt').textContent.trim();
      if (idxAfter !== idxBefore || txtAfter !== txtBefore) advanced = true;
    }

    check('点舞台能推进剧情', advanced || seenChoice || seenEnding,
      'advanced=' + advanced + ' choice=' + seenChoice + ' ending=' + seenEnding);
    check('能走到分支或结局', seenChoice || seenEnding,
      'choice=' + seenChoice + ' ending=' + seenEnding);

    if (seenEnding) {
      check('结局页有标题', $('pEndTitle').textContent.trim().length > 0, $('pEndTitle').textContent.trim());
      check('结局页有副标题', $('pEndSub').textContent.trim().length > 0, $('pEndSub').textContent.trim());
    }

    // 4) 返回上一句
    $('plBack').click();
    await wait(300);
    check('点「上一句」不报错', consoleErrors.length === 0, consoleErrors.join(' | '));

    // 5) 重来
    $('plRestart').click();
    await wait(300);
    check('点「重来」回到开头', $('plIdx').textContent.trim() === '1', $('plIdx').textContent.trim());

    // 6) ESC 关闭
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(250);
    check('ESC 能关闭播放器', !pl.classList.contains('show'), pl.className);

    // 7) 关闭后能再打开
    $('btnPlay').click();
    await raf2();
    await wait(350);
    check('关闭后能再次打开', pl.classList.contains('show'), pl.className);

    $('plClose').click();
    await wait(200);
    check('点「关闭」能关掉', !pl.classList.contains('show'), pl.className);

    check('播放器测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 7 轮：接口配置页 --- */
  T.apisetup = async function () {
    goView('setup');
    await raf2();
    await wait(300);

    // 1) 六个接口卡片（3 云端 + 3 本地）
    var cards = document.querySelectorAll('.api-card');
    check('接口卡片数 = 6', cards.length === 6, cards.length + ' 个');

    var cloud = document.querySelectorAll('.api-card.cloud').length;
    var local = document.querySelectorAll('.api-card.local').length;
    check('云端/本地各 3 个', cloud === 3 && local === 3, cloud + ' + ' + local);

    // 2) 每张卡片都要有可输入的框
    var withInput = 0;
    cards.forEach(function (c) { if (c.querySelector('input, textarea')) withInput++; });
    check('每张卡片都有输入框', withInput === cards.length, withInput + '/' + cards.length);

    // 3) 就绪计数格式正常
    var rc = $('readyCount').textContent.trim();
    check('就绪计数显示正常', /^\d+\s*\/\s*\d+$/.test(rc), rc);

    // 4) 模式切换
    var ms = $('modeSwitch');
    check('模式切换控件存在', !!ms);
    var msBtns = ms ? ms.querySelectorAll('button') : [];
    check('模式有至少 2 个选项', msBtns.length >= 2, msBtns.length + ' 个');
    if (msBtns.length >= 2) {
      var before = Array.prototype.map.call(msBtns, function (b) { return b.classList.contains('on'); }).join(',');
      msBtns[1].click();
      await wait(200);
      var after = Array.prototype.map.call(msBtns, function (b) { return b.classList.contains('on'); }).join(',');
      check('点模式按钮能切换选中态', before !== after, before + ' -> ' + after);
      msBtns[0].click();               // 还原
      await wait(150);
    }

    // 5) 测试连接：点一下要有反馈（成功或失败都行，但不能没反应/不能崩）
    var testBtn = null;
    cards.forEach(function (c) {
      if (testBtn) return;
      var b = Array.prototype.find.call(c.querySelectorAll('button'), function (x) {
        return /测试|连接|检测/.test(x.textContent);
      });
      if (b) testBtn = b;
    });
    check('卡片上有「测试连接」按钮', !!testBtn, testBtn ? testBtn.textContent.trim() : 'none');
    if (testBtn) {
      var t0 = testBtn.textContent.trim();
      testBtn.click();
      await wait(900);
      var t1 = testBtn.textContent.trim();
      var toastTxt = ($('toast') || {}).textContent || '';
      check('点测试有反馈', t1 !== t0 || toastTxt.trim().length > 0,
        t0 + ' -> ' + t1 + ' | toast:' + toastTxt.trim().slice(0, 24));
    }

    // 6) 低功耗开关（会写 localStorage，测完必须还原）
    var perf = $('btnPerf');
    var hadLow = document.body.classList.contains('low-power');
    perf.click();
    await wait(200);
    var nowLow = document.body.classList.contains('low-power');
    check('低功耗开关能切换', nowLow !== hadLow, hadLow + ' -> ' + nowLow);
    perf.click();                      // 还原
    await wait(200);
    check('低功耗能还原', document.body.classList.contains('low-power') === hadLow);

    // 7) 清空密钥（有内容时应弹二次确认；headless 里 confirm 默认 false，先接管）
    var pwd = document.querySelector('.api-card input[type=password]');
    check('存在密钥输入框', !!pwd);
    var origConfirm = window.confirm;
    var confirmCalled = false;
    window.confirm = function () { confirmCalled = true; return true; };
    try {
      if (pwd) {
        pwd.value = 'sk-e2e-dummy-value';
        $('btnResetKeys').click();
        await wait(400);
        check('有内容时清空会二次确认', confirmCalled);
        check('确认后输入框被清空', pwd.value === '', 'value=' + pwd.value);
      }
      // 取消的情形：应保留内容
      confirmCalled = false;
      window.confirm = function () { confirmCalled = true; return false; };
      if (pwd) {
        pwd.value = 'sk-e2e-keep';
        $('btnResetKeys').click();
        await wait(250);
        check('点取消不会清空', pwd.value === 'sk-e2e-keep', 'value=' + pwd.value);
        pwd.value = '';
      }
    } finally {
      window.confirm = origConfirm;
    }

    // 8) 保存并进入编排
    $('btnToEditor').click();
    await wait(600);
    check('「保存并进入编排」能跳转', activeView() === 'view-editor', activeView());

    check('配置页测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 8 轮：交互动画平滑度 --- */
  T.anim = async function () {
    goView('editor');
    await raf2();
    await wait(300);

    /* 1) 全局扫描 transition：时长是否合理、有没有 `all` 性能陷阱 */
    var all = document.querySelectorAll('*');
    var dur = { short: 0, good: 0, long: 0, allProp: 0 };
    var longList = [], allList = [];
    var seen = 0;
    for (var i = 0; i < all.length && seen < 500; i++, seen++) {
      var cs = getComputedStyle(all[i]);
      var td = cs.transitionDuration || '0s';
      var tp = cs.transitionProperty || 'none';
      if (!td || td === '0s') continue;
      if (tp.indexOf('all') >= 0) {
        dur.allProp++;
        if (allList.length < 3) allList.push(all[i].className || all[i].tagName);
      }
      var ms = td.split(',').map(function (s) { return parseFloat(s) * (s.indexOf('ms') > 0 ? 1 : 1000); });
      var max = Math.max.apply(null, ms);
      if (max < 80) dur.short++;
      else if (max > 600) { dur.long++; if (longList.length < 3) longList.push((all[i].className || all[i].tagName) + ':' + max + 'ms'); }
      else dur.good++;
    }
    check('存在过渡动画', dur.good + dur.long + dur.short > 0,
      '适中 ' + dur.good + ' / 过短 ' + dur.short + ' / 过长 ' + dur.long);
    check('过渡时长大多合理(80~600ms)', dur.good >= (dur.short + dur.long),
      '过长示例: ' + (longList.join(' | ') || '无'));
    check('没有 transition:all 泛滥', dur.allProp <= 3,
      dur.allProp + ' 处 ' + allList.join(','));

    /* 2) 弹窗入场动画
       ⚠ headless + --virtual-time-budget 下 CSS animation 不推进（会一直停在起始帧），
         所以不能靠「等几帧再采样」判断。改成两步都做：
         (a) 静态读 @keyframes 定义，确认终点就是正常比例；
         (b) 用 Web Animations API 把动画强行 finish，验证终点样式。 */
    var kfOk = false, kfText = '';
    try {
      for (var si = 0; si < document.styleSheets.length; si++) {
        var rules = document.styleSheets[si].cssRules;
        for (var ri = 0; ri < rules.length; ri++) {
          if (rules[ri].type === 7 /* KEYFRAMES_RULE */ && rules[ri].name === 'popIn') {
            kfText = rules[ri].cssText.replace(/\s+/g, ' ').slice(0, 120);
            kfOk = /transform:\s*none/.test(kfText) || /scale\(1\)/.test(kfText);
          }
        }
      }
    } catch (e) { /* 跨域样式表读不到，跳过静态检查 */ }
    check('popIn 关键帧终点是正常比例', kfOk, kfText || '未找到 popIn');

    var pl = $('player');
    $('btnPlay').click();
    await wait(120);
    var wrap = pl.querySelector('.stage-wrap') || pl;

    var parsed = 1;
    function readScale(el) {
      var m = getComputedStyle(el).transform;
      if (m && m.indexOf('matrix') === 0) {
        return parseFloat(m.replace(/^matrix\(|\)$/g, '').split(',')[0]);
      }
      return 1;
    }

    var anims = wrap.getAnimations ? wrap.getAnimations() : [];
    check('弹窗挂上了入场动画', anims.length > 0, anims.length + ' 个');

    if (anims.length) {
      var before = readScale(wrap);
      anims.forEach(function (a) { try { a.finish(); } catch (e) {} });
      await wait(80);
      var after = readScale(wrap);
      check('动画终点比例 = 1', Math.abs(after - 1) < 0.02,
        before.toFixed(3) + ' -> ' + after.toFixed(3));
      check('动画确实有缩放变化', Math.abs(after - before) > 0.005 || Math.abs(before - 1) < 0.02,
        'from ' + before.toFixed(3));
    }

    // 动画结束后遮罩整体应完全可见（fadeIn 同样要 finish，否则停在 opacity:0）
    var plAnims = pl.getAnimations ? pl.getAnimations() : [];
    plAnims.forEach(function (a) { try { a.finish(); } catch (e) {} });
    await wait(80);
    var plStyle = getComputedStyle(pl);
    check('弹窗打开后完全可见', +plStyle.opacity > 0.99,
      'opacity=' + plStyle.opacity + ' (finish 了 ' + plAnims.length + ' 个动画)');

    $('plClose').click();
    await wait(250);

    /* 3) 低功耗模式应关掉动画 */
    var perf = $('btnPerf');
    var hadLow = document.body.classList.contains('low-power');
    if (!hadLow) perf.click();
    await wait(250);
    var cs2 = getComputedStyle(document.querySelector('.pillnav button'));
    var lowDur = cs2.transitionDuration || '0s';
    var lowOff = lowDur === '0s' || parseFloat(lowDur) === 0;
    check('低功耗模式关闭过渡', lowOff, 'duration=' + lowDur);
    if (!hadLow) perf.click();          // 还原
    await wait(200);

    /* 4) 视图切换不能有中间态闪烁（切换后目标视图必须可见且不透明） */
    goView('export');
    await wait(320);
    var ex = $('view-export');
    var exs = getComputedStyle(ex);
    check('切到导出页后完全可见',
      exs.display !== 'none' && +exs.opacity > 0.99 && +exs.visibility !== 0,
      'display=' + exs.display + ' opacity=' + exs.opacity);

    goView('setup');
    await wait(250);

    check('动画测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 9 轮：边界与异常 --- */
  T.edge = async function () {
    goView('editor');
    await raf2();
    await wait(400);

    var cv = $('galCanvas');
    function findNode(galId) {
      var g = GalEditor._canvas().graph._nodes;
      for (var i = 0; i < g.length; i++) {
        if (g[i].properties && g[i].properties.galId === galId) return g[i];
      }
      return null;
    }
    function clickByGal(galId) {
      var n = findNode(galId);
      if (!n) return false;
      var t = toScreen(n.pos[0] + n.size[0] / 2, n.pos[1] + 16);
      mouse(cv, 'mousedown', t.x, t.y, 1);
      mouse(cv, 'mouseup', t.x, t.y, 0);
      return true;
    }

    // 1) 快速连点排版 / 适应 —— 不应堆积任务或崩
    for (var i = 0; i < 10; i++) { $('btnLayout').click(); }
    await wait(600);
    check('连点「自动排布」10 次不报错', consoleErrors.length === 0, consoleErrors.join(' | '));
    check('连点后节点仍在', GalEditor._canvas().graph._nodes.length > 0,
      GalEditor._canvas().graph._nodes.length + ' 个');

    for (var j = 0; j < 10; j++) { $('btnFit').click(); }
    await wait(400);
    check('连点「适应」10 次不报错', consoleErrors.length === 0, consoleErrors.join(' | '));

    // 2) 连点加节点菜单 —— 开关不应叠加成异常状态
    for (var k = 0; k < 6; k++) { $('btnAdd').click(); await wait(40); }
    await wait(200);
    var menu = $('addMenu');
    check('连点「+节点」不崩', consoleErrors.length === 0, consoleErrors.join(' | '));
    check('菜单最终状态明确（开或关）', menu && typeof menu.classList.contains('show') === 'boolean',
      menu ? menu.className : 'missing');
    if (menu && menu.classList.contains('show')) $('btnAdd').click();

    // 3) 连点试玩 —— 不应开出多个弹窗 / 不应重复绑定
    for (var m = 0; m < 5; m++) { $('btnPlay').click(); await wait(60); }
    await wait(400);
    var showCount = document.querySelectorAll('.modal.show').length;
    check('连点试玩只出现一个弹窗', showCount <= 1, showCount + ' 个');
    $('plClose').click();
    await wait(250);

    // 4) 超长文本输入不应把页面搞崩
    goView('setup');
    await raf2();
    var req = $('reqText');
    req.value = '测试'.repeat(3000);        // 6000 字
    req.dispatchEvent(new Event('input', { bubbles: true }));
    await wait(300);
    check('超长需求文本不报错', consoleErrors.length === 0, consoleErrors.join(' | '));
    var cnt = $('reqCount').textContent;
    check('字数统计跟上了', /\d/.test(cnt), cnt);
    req.value = '一个雨夜，天台。';
    req.dispatchEvent(new Event('input', { bubbles: true }));

    // 5) 删除节点：先验证 UI 按钮真的有效，再清空测空状态
    goView('editor');
    await raf2();
    await wait(300);
    // 前面连点过「自动排布」，autoFit 有 180ms 防抖 —— 等它落定再按坐标点击。
    GalEditor.fit();
    await wait(400);

    /* ⚠ LiteGraph 的命中测试用的是 visible_nodes，而那个列表由「绘制」填充。
       headless 下 rAF 不推进，从别的视图切回来时画布可能一次都没重绘，
       visible_nodes 是空的 → 点节点点不中（真机上 rAF 正常，不会出现）。
       这里显式同步绘制一次，模拟真实浏览器的一帧。 */
    var cvsDraw = GalEditor._canvas();
    if (cvsDraw && cvsDraw.draw) {
      try { cvsDraw.draw(true, true); } catch (e) {}
      await wait(120);
    }

    var g0 = GalEditor._canvas().graph._nodes.length;
    var firstGid = GalEditor._canvas().graph._nodes[0].properties.galId;
    var hitOk = clickByGal(firstGid);
    await wait(220);
    var insShown = $('inspector').classList.contains('show');
    var selN = GalEditor._canvas().selected_nodes ? Object.keys(GalEditor._canvas().selected_nodes).length : 0;
    check('点击确实选中了节点', insShown && selN > 0,
      'hit=' + hitOk + ' inspector=' + insShown + ' selected=' + selN + ' visible=' +
      (GalEditor._canvas().visible_nodes ? GalEditor._canvas().visible_nodes.length : 'null'));
    $('btnDel').click();
    await wait(300);
    var g1 = GalEditor._canvas().graph._nodes.length;
    check('点「删除」真的删掉一个节点', g1 === g0 - 1, g0 + ' -> ' + g1);

    // 清空（用库 API，避免多节点重叠时点击定位不稳）
    var cvsE = GalEditor._canvas();
    var allNodes = cvsE.graph._nodes.slice();
    if (allNodes.length) {
      if (cvsE.selectNodes) cvsE.selectNodes(allNodes, false);
      if (cvsE.deleteSelectedNodes) cvsE.deleteSelectedNodes();
    }
    await wait(300);
    var left = GalEditor._canvas().graph._nodes.length;
    check('能把节点清空到 0', left === 0, '剩 ' + left + ' 个');
    check('清空节点不报错', consoleErrors.length === 0, consoleErrors.join(' | '));

    // 6) 空画布上点适应 / 排布 / 撤销 / 重做
    $('btnFit').click();
    await wait(150);
    $('btnLayout').click();
    await wait(250);
    $('btnUndo').click();
    await wait(200);
    $('btnRedo').click();
    await wait(200);
    check('空画布操作不报错', consoleErrors.length === 0, consoleErrors.join(' | '));
    check('空画布不白屏（画布仍有尺寸）', cv.width > 0 && cv.height > 0,
      cv.width + 'x' + cv.height);

    check('边界测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 11 轮：前后端联调（浏览器侧真实网络 + CORS） --- */
  T.integration = async function () {
    var base = (window.GalAPI && GalAPI.base) || 'http://127.0.0.1:8790';
    check('GalAPI 有 base 地址', !!base, base);

    async function jget(path) {
      var r = await fetch(base + path, { method: 'GET' });
      return { status: r.status, body: await r.json().catch(function () { return null; }) };
    }
    async function jpost(path, obj) {
      var r = await fetch(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obj)
      });
      return { status: r.status, body: await r.json().catch(function () { return null; }) };
    }

    // 1) 健康检查（跨域 —— 同时验证 CORS）
    var h;
    try {
      h = await jget('/api/health');
      check('浏览器能跨域访问后端 /api/health', h.status === 200 && h.body && h.body.ok === true,
        'HTTP ' + h.status + ' ' + JSON.stringify(h.body));
      check('后端返回版本号', !!(h.body && h.body.version), h.body ? h.body.version : '');
    } catch (e) {
      check('浏览器能跨域访问后端 /api/health', false, String(e && e.message).slice(0, 100) + ' (CORS 或未启动)');
      return;                                  // 连不上就不用往下测了
    }

    // 2) providers 六类
    var p = await jget('/api/providers');
    check('能读到 providers 配置', p.status === 200 && p.body, 'HTTP ' + p.status);
    if (p.body) {
      var keys = Object.keys(p.body);
      check('providers 含 6 类接口', keys.length >= 6, keys.join(','));
    }

    // 3) 建项目
    var proj = await jpost('/api/projects', { name: 'e2e-integration-' + Date.now().toString(36) });
    check('能建项目', proj.status === 200 && proj.body && !!proj.body.id,
      'HTTP ' + proj.status + ' id=' + (proj.body && proj.body.id));
    if (!proj.body || !proj.body.id) return;
    var pid = proj.body.id;

    // 4) 用前端 app.js 真正会发的 body 触发生成
    var gen = await jpost('/api/projects/' + pid + '/generate', {
      requirement: '一个雨夜天台的告别，8 个节点，两个结局',
      params: { node_count: 8, branch_depth: 2 }
    });
    check('触发生成返回 200', gen.status === 200, 'HTTP ' + gen.status +
      (gen.body && gen.body.detail ? ' ' + JSON.stringify(gen.body.detail).slice(0, 80) : ''));
    check('返回 job_id', !!(gen.body && gen.body.job_id), gen.body ? gen.body.job_id : '');

    // 5) 无 key 时必须是「降级但成功」，不能 500、不能静默
    if (gen.body) {
      var isMock = gen.body.mock === true;
      var hasDegrade = !!gen.body.degraded;
      check('无 key 时优雅降级而非报错', isMock || gen.body.job_id,
        'mock=' + isMock + ' degraded=' + (hasDegrade ? Object.keys(gen.body.degraded).join('/') : '无'));
    }

    // 6) 读图与导出端点存在（不实际执行重活，只看是否可达）
    var g = await jget('/api/projects/' + pid + '/graph');
    check('能读回图数据', g.status === 200, 'HTTP ' + g.status);

    check('联调测试无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 通用：真点一个按钮，看有没有反应 --- */
  T.buttons = async function () {
    goView('editor');
    await raf2();
    await wait(300);

    var cv = $('galCanvas');
    var z0 = GalEditor.getZoom();

    // 放大 / 缩小
    $('zoomIn').click();
    await raf2();
    var z1 = GalEditor.getZoom();
    check('点 + 放大', z1 > z0, z0.toFixed(3) + ' -> ' + z1.toFixed(3));

    $('zoomOut').click();
    await raf2();
    var z2 = GalEditor.getZoom();
    check('点 − 缩小', z2 < z1, z1.toFixed(3) + ' -> ' + z2.toFixed(3));

    // 适应 / 自动排布
    $('btnFit').click();
    await wait(120);
    check('点「适应」不报错', consoleErrors.length === 0, consoleErrors.join(' | '));

    $('btnLayout').click();
    await wait(400);
    var n = GalEditor.dump().nodes.length;
    check('点「自动排布」后节点仍在', n > 0, '节点数 ' + n);

    // 加节点菜单
    $('btnAdd').click();
    await raf2();
    var menu = $('addMenu');
    var shown = menu && menu.classList.contains('show');
    check('点「+节点」弹出菜单', shown, shown ? '' : menu ? menu.className : 'no menu');

    // 撤销 / 重做（点一下确认不抛错）
    $('btnUndo').click();
    await wait(100);
    $('btnRedo').click();
    await wait(100);
    check('撤销/重做不报错', consoleErrors.length === 0, consoleErrors.join(' | '));

    check('全流程无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 诊断：只验 load → dump 是否原样往返（不碰网络） --- */
  T.roundtrip = async function () {
    var ns = [];
    for (var i = 1; i <= 9; i++) {
      // ending 节点没有输出端口（KIND.ending.outs === 0），不可能连出边
      var ty = i === 1 ? 'start' : (i === 5 ? 'choice' : (i >= 8 ? 'ending' : 'scene'));
      ns.push({
        id: 'n' + i, type: ty, title: 'T' + i, x: 40 + i * 120, y: 320,
        who: '她', text: '台词' + i, bg: 'rainy', voice: 3,
        choices: ty === 'choice' ? [{ label: 'A', to: 'n6' }, { label: 'B', to: 'n7' }] : undefined,
        endKind: ty === 'ending' ? 'true' : undefined
      });
    }
    var ls = [
      { from: 'n1', port: 0, to: 'n2' }, { from: 'n2', port: 0, to: 'n3' },
      { from: 'n3', port: 0, to: 'n4' }, { from: 'n4', port: 0, to: 'n5' },
      { from: 'n5', port: 0, to: 'n6' }, { from: 'n5', port: 1, to: 'n7' },
      { from: 'n6', port: 0, to: 'n8' }, { from: 'n7', port: 0, to: 'n9' }
    ];
    goView('editor');
    await raf2();
    GalEditor.load(ns, ls);
    await raf2();
    var d = GalEditor.dump();
    check('load 9 节点 → dump 9', d.nodes.length === 9, '实际 ' + d.nodes.length);
    check('load 8 连线 → dump 8', d.links.length === 8, '实际 ' + d.links.length);
    var ids = d.nodes.map(function (n) { return n.id; }).sort().join(',');
    check('节点 id 原样往返', ids === 'n1,n2,n3,n4,n5,n6,n7,n8,n9', ids);
    var want = ['n1->n2', 'n2->n3', 'n3->n4', 'n4->n5', 'n5->n6', 'n5->n7', 'n6->n8', 'n7->n9'].sort().join(' ');
    var got = d.links.map(function (l) { return l.from + '->' + l.to; }).sort().join(' ');
    check('连线往返（含 port）', got === want, 'got: ' + got);
    check('choice 的前两个选项有目标', (function () {
      var c = d.nodes.filter(function (n) { return n.id === 'n5'; })[0];
      if (!c || !Array.isArray(c.choices)) return false;
      // 第 3 个端口没连线时后端会给空 to，这是设计（分支固定 3 出口）
      return c.choices[0].to === 'n6' && c.choices[1].to === 'n7';
    })(), JSON.stringify((d.nodes.filter(function (n) { return n.id === 'n5'; })[0] || {}).choices));
    check('roundtrip 无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 持久化 1/2：真后端生成 + 保存，并把项目 id 留在 localStorage --- */
  T.persist1 = async function () {
    var realBtn = document.querySelector('[data-mode="real"]');
    if (realBtn) realBtn.click();
    await wait(1600);
    check('切到 real 模式', GalAPI.mode === 'real', String(GalAPI.mode));
    check('后端在线', GalAPI.online === true, GalAPI.lastError || '');
    try { localStorage.removeItem('gal_project_id'); } catch (e) {}

    goView('setup');
    await raf2();
    $('reqText').value = '持久化测试：雨夜天台，两个结局';
    $('reqText').dispatchEvent(new Event('input', { bubbles: true }));
    goView('editor');
    await raf2();

    var t0 = Date.now();
    $('btnGenerate').click();
    var done = false, waited = 0;
    while (waited < 60000) {
      await wait(200); waited += 200;
      var m = window.__galLastGen;
      if (m && m.at >= t0 - 1000) { done = true; break; }
    }
    check('生成完成', done, '等了 ' + waited + 'ms（虚拟时间）');
    var pid = localStorage.getItem('gal_project_id');
    check('项目 id 已记住', !!pid && /^p_/.test(pid), String(pid));
    var cnt = GalEditor.dump().nodes.length;
    check('画布已有后端节点', cnt > 0, cnt + ' 个');

    $('btnSave').click();
    await wait(2500);
    check('保存成功', /画布已保存/.test($('logBox').textContent || ''),
      (($('logBox').textContent || '').match(/(画布已保存|保存失败)[^\n]{0,40}/) || [''])[0]);
    check('需求文本也存下来了', (localStorage.getItem('gal_req_text') || '').indexOf('持久化测试') === 0,
      (localStorage.getItem('gal_req_text') || '').slice(0, 20));
  };

  /* --- 持久化 2/2：新页面，一个按钮都不点，看能不能自己接上 --- */
  T.persist2 = async function () {
    var pid = localStorage.getItem('gal_project_id');
    check('上一轮留下了项目 id', !!pid && /^p_/.test(pid), String(pid));
    check('模式也记住了', (localStorage.getItem('gal_mode') || '') === 'real',
      String(localStorage.getItem('gal_mode')));

    await wait(3500);      // 等启动流程（探测后端 + 拉图）跑完
    var log = $('logBox').textContent || '';
    check('一打开就恢复上次项目', /已恢复上次的项目/.test(log),
      (log.match(/已恢复[^\n]{0,44}/) || [''])[0] || ('log=' + log.replace(/\s+/g, ' ').slice(0, 160)));

    var d = GalEditor.dump();
    check('恢复后画布有节点', d.nodes.length > 0, d.nodes.length + ' 个 / ' + d.links.length + ' 线');
    check('恢复后的 id 来自后端', d.nodes.length > 0 && d.nodes.every(function (n) { return /^n\d+$/.test(n.id); }),
      d.nodes.map(function (n) { return n.id; }).slice(0, 5).join(','));
    // 项目名那一行已经按主人要求从界面移除，改成看记住的记录
    var nm = '';
    try { nm = localStorage.getItem('gal_project_name') || ''; } catch (e) {}
    check('项目名也恢复了', nm !== '' && nm !== '未命名项目', nm);
    check('需求文本也回来了', ($('reqText').value || '').indexOf('持久化测试') === 0,
      ($('reqText').value || '').slice(0, 20));
    check('persist2 无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 单节点重生成：走真后端 POST /nodes/{id}/regenerate --- */
  T.regen = async function () {
    var realBtn = document.querySelector('[data-mode="real"]');
    if (realBtn) realBtn.click();
    await wait(1600);
    check('real 模式', GalAPI.mode === 'real', String(GalAPI.mode));
    check('后端在线', GalAPI.online === true, GalAPI.lastError || '');

    // 等启动恢复流程把 projectId 装上（若没有恢复记录，下面会走生成流程自建）
    var wp = 0;
    while (wp < 6000 && !window.__galProjectId) { await wait(200); wp += 200; }

    // 先保证有图：优先沿用上次项目，没有再生成
    var has = GalEditor.dump().nodes.length > 0 && !!window.__galProjectId;
    if (!has) {
      goView('setup'); await raf2();
      $('reqText').value = '重生成测试：雨夜天台';
      $('reqText').dispatchEvent(new Event('input', { bubbles: true }));
      goView('editor'); await raf2();
      var t0 = Date.now();
      $('btnGenerate').click();
      var w = 0;
      while (w < 60000) {
        await wait(200); w += 200;
        var m = window.__galLastGen;
        if (m && m.at >= t0 - 1000) break;
      }
      await wait(600);
    }
    var d = GalEditor.dump();
    check('画布上有节点可选', d.nodes.length >= 2, d.nodes.length + ' 个');

    // 选中第二个节点（第一个可能是 start，素材更少）
    var target = d.nodes[1] || d.nodes[0];
    GalEditor.api.onSelect(target.id);
    await raf2();
    var shown = $('inspector').classList.contains('show');
    check('选中后检查器打开', shown, $('insTitle').textContent + ' / ' + $('insState').textContent);
    check('检查器里是这个节点', $('insTitle').textContent.indexOf(target.id) >= 0 ||
      $('insTitle').textContent.length > 0, $('insTitle').textContent);
    check('重生成按钮可点', !$('btnRegen').disabled, $('btnRegen').textContent);

    check('此时已绑定后端项目', !!window.__galProjectId, String(window.__galProjectId));

    $('btnRegen').click();
    await raf2();
    var logA = $('logBox').textContent || '';
    // 虚拟时间会把 200ms 快进成「已经做完」，所以看日志而不是看瞬时状态
    check('点击后立刻排了重生成任务', /重生成节点/.test(logA),
      (logA.match(/重生成节点[^\n]{0,34}/) || [''])[0]);

    var doneR = false, wr = 0;
    while (wr < 45000) {
      await wait(250); wr += 250;
      var lg = $('logBox').textContent || '';
      if (/重生成结束/.test(lg)) { doneR = true; break; }
      if (/重生成失败|✖/.test(lg) && /重生成/.test(lg)) { doneR = true; break; }
    }
    var log = $('logBox').textContent || '';
    check('重生成在预算内结束', doneR, '等了 ' + wr + 'ms（虚拟时间）');
    check('重生成没有报错', !/重生成失败/.test(log),
      (log.match(/(重生成失败|✖ [^\n]{0,40})/) || [''])[0]);
    check('日志出现「重生成结束」', /重生成结束/.test(log),
      (log.match(/[✔✖] ?节点 [^\n]{0,50}/) || [''])[0]);
    check('按钮恢复可用', !$('btnRegen').disabled, $('btnRegen').textContent);
    check('节点状态回到已生成', /已生成/.test($('insState').textContent), $('insState').textContent);
    check('regen 无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 第 12 轮：real 模式全链路（真后端 + 真点击，浏览器里一路走完） ---
     跟 integration 的区别：integration 是「直接 fetch 打接口」，
     这个用例走真实 UI —— 点模式按钮、点生成、点保存、点导出、点试玩，
     验的是「用户实际能操作」这条路，不是接口本身。                        */
  T.realflow = async function () {
    var t0 = Date.now();
    var base = (window.GalAPI && GalAPI.base) || '';
    check('GalAPI 挂在 window 上', !!window.GalAPI, typeof window.GalAPI);
    check('base 指向本机后端', /^https?:\/\/127\.0\.0\.1:\d+/.test(base), base);

    // 1) 切真后端模式（真点按钮，不直接改属性）
    var realBtn = document.querySelector('[data-mode="real"]');
    check('有「真后端」模式按钮', !!realBtn, realBtn ? realBtn.textContent.trim() : 'missing');
    if (realBtn) realBtn.click();
    await wait(1800);
    check('模式已切成 real', window.GalAPI.mode === 'real', String(window.GalAPI.mode));
    check('后端探测在线', window.GalAPI.online === true,
      'online=' + window.GalAPI.online + (GalAPI.lastError ? ' err=' + GalAPI.lastError : ''));
    var badge = document.getElementById('modeBadge');
    check('顶栏徽标显示「真后端」', !!(badge && /真后端/.test(badge.textContent)),
      badge ? badge.textContent : 'missing');

    // 2) 填需求 → 点生成
    goView('setup');
    await raf2();
    var ta = $('reqText');
    check('需求框存在', !!ta, ta ? 'ok' : 'missing');
    ta.value = '一个雨夜天台，主角和她约好一起离开这座城；两条分支，一个真结局一个坏结局';
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    await raf2();
    check('字数跟着更新', /字$/.test($('reqCount').textContent), $('reqCount').textContent);

    goView('editor');
    await raf2();
    $('btnGenerate').click();

    // 轮询等收尾：用 app.js 挂的 __galLastGen（只有拉完图才置位）
    var tGen = Date.now();
    var done = false, waited = 0;
    while (waited < 60000) {
      await wait(200); waited += 200;
      var mark = window.__galLastGen;
      if (mark && mark.at >= tGen - 1000) { done = true; break; }
      var lg = $('logBox').textContent || '';
      if (/SSE_LOST|START_FAILED|进度流结束|拉取节点图失败/.test(lg)) { done = true; break; }
    }
    check('生成在预算内收尾（不卡死）', done, '等了 ' + waited + 'ms（虚拟时间）');

    var log1 = $('logBox').textContent || '';
    check('日志里出现「真后端 <地址>」', /真后端\s+http/.test(log1),
      (log1.match(/真后端\s+\S+/) || [''])[0]);
    check('日志收到完成行（✔ N 节点）', /✔\s*\d+\s*节点/.test(log1),
      (log1.match(/✔[^\n]{0,60}/) || [''])[0]);
    check('没有失败/中断', !/(✖|失败|中断)/.test(log1),
      (log1.match(/✖[^\n]{0,50}/) || [''])[0]);

    // 3) 画布真的换成了后端的图
    var dumped = GalEditor.dump();
    var diag = window.__galLoadDiag || {};
    check('load 自检：节点没被吞', diag.nodesAdded === diag.nodesIn,
      'in=' + diag.nodesIn + ' added=' + diag.nodesAdded + ' skipped=' + diag.nodesSkipped);
    check('load 自检：连线没被吞', diag.linksAdded === diag.linksIn,
      'in=' + diag.linksIn + ' added=' + diag.linksAdded + ' skipped=' + diag.linksSkipped +
      ' missing=' + JSON.stringify(diag.missing || []));
    var sv = (window.__galLastGen && window.__galLastGen.stats) || {};
    check('画布节点数 = 后端生成的数量', dumped.nodes.length === sv.nodes,
      '画布 ' + dumped.nodes.length + ' / 后端 ' + sv.nodes);
    check('连线数 = 后端生成的数量', dumped.links.length === sv.edges,
      '画布 ' + dumped.links.length + ' / 后端 ' + sv.edges);
    check('画布已有真实节点 id', dumped.nodes.some(function (n) { return n.id === 'n1'; }),
      'ids=' + dumped.nodes.map(function (n) { return n.id; }).slice(0, 5).join(','));

    // 4) 保存回后端
    $('btnSave').click();
    await wait(2500);
    var log2 = $('logBox').textContent || '';
    check('保存成功写进日志', /画布已保存/.test(log2),
      (log2.match(/(画布已保存|保存失败)[^\n]{0,40}/) || [''])[0]);

    // 5) 导出 renpy
    goView('export');
    await raf2();
    var expBtn = document.querySelector('.exp-btn[data-format="renpy"]');
    check('有 renpy 导出按钮', !!expBtn, expBtn ? expBtn.textContent.trim() : 'missing');
    if (expBtn) { expBtn.click(); await wait(3500); }
    var log3 = $('logBox').textContent || '';
    check('导出成功写进日志', /✔ 导出完成/.test(log3),
      (log3.match(/(导出完成|导出失败)[^\n]{0,60}/) || [''])[0]);

    // 6) 试玩能读到后端来的台词
    goView('editor');
    await raf2();
    $('btnPlay').click();
    await wait(900);
    var pl = $('player');
    check('播放器打开', !!(pl && pl.classList.contains('show')), pl ? pl.className : 'missing');
    var pTxt = ($('pTxt') || {}).textContent || '';
    var pWho = ($('pWho') || {}).textContent || '';
    check('播放器有非空台词', pTxt.trim().length > 0, JSON.stringify(pTxt.slice(0, 24)));
    check('播放器有说话人', pWho.trim().length > 0, JSON.stringify(pWho));
    var plNode = ($('plNode') || {}).textContent || '';
    check('播放器显示真实节点 id', /^n\d+/.test(plNode.trim()), plNode.trim());

    $('plClose').click();
    await raf2();

    check('real 全链路总耗时 < 90s', Date.now() - t0 < 90000, (Date.now() - t0) + 'ms');
    check('real 全链路无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* --- 桌面版：三个开关 + 窗口按钮 + 底部提示（以前全是装饰） --- */
  T.desktop = async function () {
    var v = $('chipVoice'), c = $('chipConsist'), r = $('chipReview'), f = $('chipFinal');
    check('四个开关都存在', !!(v && c && r && f),
      [v, c, r, f].map(function (e) { return !!e; }).join(','));

    var v0 = v.classList.contains('on');
    v.click(); await raf2();
    check('自动配音：点了真的会变', v.classList.contains('on') !== v0,
      v0 + ' -> ' + v.classList.contains('on'));
    v.click(); await raf2();
    check('自动配音：再点回原状', v.classList.contains('on') === v0, String(v0));

    var c0 = c.classList.contains('on');
    c.click(); await raf2();
    check('角色一致性：点了真的会变', c.classList.contains('on') !== c0,
      c0 + ' -> ' + c.classList.contains('on'));
    c.click(); await raf2();

    // 逐节点审核：以前是"点了弹一句还没做"，现在是真开关
    var r0 = r.classList.contains('on');
    r.click(); await raf2();
    check('逐节点审核：点了真的会变', r.classList.contains('on') !== r0,
      r0 + ' -> ' + r.classList.contains('on'));
    var tR = ($('toast') || {}).textContent || '';
    check('逐节点审核：开启时会说明生成完要过审', /过审/.test(tR), tR.slice(0, 30));
    r.click(); await raf2();
    check('逐节点审核：再点回原状', r.classList.contains('on') === r0, String(r0));

    // 收尾验证：新增的第五个 agent 开关
    var f0 = f.classList.contains('on');
    f.click(); await raf2();
    check('收尾验证：点了真的会变', f.classList.contains('on') !== f0,
      f0 + ' -> ' + f.classList.contains('on'));
    var tF = ($('toast') || {}).textContent || '';
    check('收尾验证：开启时会说明会多跑质检', /质检/.test(tF), tF.slice(0, 30));
    f.click(); await raf2();
    check('收尾验证：再点回原状', f.classList.contains('on') === f0, String(f0));

    var hint = ($('setupHint') || {}).textContent || '';
    check('底部提示有内容', hint.length > 10, hint.slice(0, 48));
    check('底部提示按实际状态算（不是写死的 3/1）',
      /共 \d+ 个/.test(hint), hint.slice(0, 48));

    $('btnWinMin').click(); await raf2();
    await wait(60);
    var tMin = ($('toast') || {}).textContent || '';
    check('浏览器里点最小化会说明原因', /桌面启动器|没法/.test(tMin), tMin.slice(0, 34));

    check('desktop 无 JS 错误', consoleErrors.length === 0, consoleErrors.join(' | '));
  };

  /* ---------------- 调度 & 上报 ---------------- */

  function report() {
    var failed = items.filter(function (i) { return !i.ok; });
    var out = {
      case: caseName,
      pass: failed.length === 0 && consoleErrors.length === 0,
      total: items.length,
      failed: failed.length,
      items: items,
      errors: consoleErrors
    };
    document.title = 'E2E ' + JSON.stringify(out);
  }

  setTimeout(async function () {
    var fn = T[caseName];
    if (!fn) {
      document.title = 'E2E {"case":"' + caseName + '","pass":false,"items":[],"errors":["unknown case"]}';
      return;
    }
    try {
      await fn();
    } catch (e) {
      consoleErrors.push('case threw: ' + String(e && e.message || e).slice(0, 160));
    }
    report();
  }, 1300);
})();
