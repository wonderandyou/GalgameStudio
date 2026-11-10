/* 前端验收与千节点压测。仅在 URL 带 frontend-e2e / frontend-shot 时运行。 */
(function () {
  'use strict';
  var q = new URLSearchParams(location.search);
  var e2e = q.has('frontend-e2e');
  var shot = q.get('frontend-shot');
  if (!e2e && !shot) return;
  var errors = [];
  window.addEventListener('error', function (e) { errors.push(e.message); });
  window.addEventListener('unhandledrejection', function (e) { errors.push(String(e.reason)); });
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function frame() { return new Promise(function (r) { requestAnimationFrame(r); }); }
  function fakeGraph(withChapters) {
    var g = { version: 1, project: { id: 'perf', name: '雨夜 · 千节点实测', resolution: [1920, 1080], created_at: new Date().toISOString() }, characters: [], nodes: [], edges: [] };
    for (var i = 0; i < 800; i++) {
      var id = 'n' + (i + 1);
      g.nodes.push({ id: id, type: i === 0 ? 'start' : (i % 40 === 39 ? 'ending' : i % 13 === 0 ? 'choice' : 'scene'), title: (i % 40 === 0 ? '雨落' : '场景') + ' · ' + String(i + 1).padStart(3, '0'), status: 'done', bg: { asset: '', prompt: '雨夜城市', seed: 0, provider: 'test' }, chars: [], lines: [{ who: '她', text: '雨声覆盖了站台广播，我们在沉默里等下一班车。', voice: null, dur: 3.5 }], pos: { x: 40 + (i % 20) * 330, y: 40 + Math.floor(i / 20) * 215 }, gen: { model: 'test', at: new Date().toISOString(), cost: 0 } });
      if (i && (i % 40 !== 0)) g.edges.push({ id: 'e' + i, from: 'n' + i, port: 0, to: id });
    }
    if (withChapters) {
      g.chapters = [];
      for (var c = 0; c < 20; c++) {
        g.chapters.push({ id: 'c' + (c + 1), act_title: c < 10 ? '第一幕 雨夜' : '第二幕 离开', title: String(c + 1).padStart(2, '0') + ' ' + ['天台相遇', '两年前的雨', '那张车票', '车站'][c % 4], status: c < 10 ? 'done' : c === 10 ? 'writing' : 'pending', node_ids: g.nodes.slice(c * 40, c * 40 + 40).map(function (n) { return n.id; }) });
      }
    }
    return g;
  }
  function mouse(cv, type, x, y, buttons) {
    var r = cv.getBoundingClientRect();
    var ev = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, buttons: buttons, clientX: r.left + x, clientY: r.top + y });
    try { Object.defineProperty(ev, 'which', { value: 1 }); } catch (_) {}
    cv.dispatchEvent(ev);
  }
  async function measure() {
    var c = GalEditor._canvas();
    GalEditor.setZoom(0.30);
    var times = [];
    for (var i = 0; i < 90; i++) {
      var t = performance.now();
      c.draw(true, true);
      times.push(performance.now() - t);
      await frame();
    }
    times.sort(function (a, b) { return a - b; });
    var cv = c.canvas, intervals = [], eventCosts = [], last = performance.now();
    mouse(cv, 'mousedown', 650, 350, 1);
    for (var j = 0; j < 120; j++) {
      await frame();
      var now = performance.now(); intervals.push(now - last); last = now;
      var s = performance.now();
      mouse(cv, 'mousemove', 650 + (j % 50) * 3, 350 + (j % 30) * 2, 1);
      GalEditor.setZoom(0.30 + (j % 8) * 0.003);
      eventCosts.push(performance.now() - s);
    }
    mouse(cv, 'mouseup', 800, 400, 0);
    intervals.shift(); intervals.sort(function (a, b) { return a - b; });
    eventCosts.sort(function (a, b) { return a - b; });
    return { renderedFrames: 120, averageFps: +(1000 / (intervals.reduce(function (a, b) { return a + b; }, 0) / intervals.length)).toFixed(1), frameP95Ms: +intervals[Math.floor(intervals.length * .95)].toFixed(2), fullDrawMedianMs: +times[45].toFixed(2), fullDrawP95Ms: +times[85].toFixed(2), inputDispatchMedianMs: +eventCosts[60].toFixed(3), inputDispatchP95Ms: +eventCosts[114].toFixed(3) };
  }
  async function run() {
    await wait(150);
    document.querySelector('[data-view="editor"]').click();
    GalFrontendTest.applyGraph(fakeGraph(true));
    await wait(350);
    GalEditor.fit();
    if (shot === 'chapters') { window.__frontendShotReady = true; return; }
    if (shot === 'inspector') {
      // 验收节点属性面板：造一个带背景/两句台词/两个立绘/结局类型/分支性质的节点
      var gi = fakeGraph(false);
      gi.characters = [
        { id: 'heroine', name: '她', color: '#d946ef', ref_image: '', voice_id: '', look: '' },
        { id: 'him', name: '他', color: '#2563eb', ref_image: '', voice_id: '', look: '' }
      ];
      var pick = function (type, nth) {
        return gi.nodes.filter(function (n) { return n.type === type; })[nth || 0];
      };
      var scene = pick('scene', 2);
      scene.chars = [
        { id: 'heroine', pose: '', pos: 'left', mood: '平静', stance: 'stand' },
        { id: 'him', pose: '', pos: 'right', mood: '勉强笑', stance: 'stand' }
      ];
      scene.lines = [
        { who: 'heroine', text: '你等了多久', voice: 'assets/voice/v1.wav', dur: 2.2 },
        { who: 'him', text: '雨声把回答盖住了', voice: null, dur: 1.8 }
      ];
      scene.bg = { asset: 'assets/bg/b1.png', prompt: '雨夜天台，铁栏积水',
                   seed: 0, provider: 'qwen_image', place: '天台', time: '夜', weather: '雨' };
      var endNode = pick('ending', 0);
      if (endNode) endNode.end_kind = 'good';
      var choiceShot = pick('choice', 0);
      if (choiceShot) choiceShot.branch_kind = 'flavor';

      GalFrontendTest.applyGraph(gi);
      await wait(320);
      var showAll = document.getElementById('btnShowAll');
      if (showAll) showAll.click();          // 关掉章节过滤，保证目标节点在画布上
      await wait(200);
      GalEditor.fit();
      var cvShot = GalEditor._canvas();
      var target = cvShot.graph._nodes.filter(function (n) {
        return n.properties.galId === scene.id;
      })[0];
      if (!target) {
        throw new Error('画布上找不到 ' + scene.id + '，当前节点数 ' + cvShot.graph._nodes.length +
          '，全部 id 前五个 ' + cvShot.graph._nodes.slice(0, 5).map(function (n) {
            return n.properties.galId;
          }).join(','));
      }
      // 必须走 processNodeSelected：selectNode 只改选中状态，不会触发 onNodeSelected，
      // 属性面板挂在后者上（踩过：用 selectNode 截图，面板永远是空的）
      cvShot.processNodeSelected(target, null);
      await wait(400);
      window.__frontendShotReady = true;
      return;
    }
    if (shot === 'chapter') { document.querySelector('[data-chapter="c3"]').click(); await wait(350); GalEditor.fit(); window.__frontendShotReady = true; return; }
    if (shot === 'progress') {
      GalFrontendTest.beginProgress(980);
      GalFrontendTest.event('jobstart', { job_id: 'shot-job' });
      GalFrontendTest.event('agent', { agent: 'writer', state: 'run', progress: 0.44, msg: '正在写第 3 章：那张车票' });
      for (var n = 1; n <= 128; n++) GalFrontendTest.event('node', { node_id: 'n' + n, patch: { status: 'done' } });
      document.getElementById('generationProgress').scrollIntoView({ block: 'center' });
      window.__frontendShotReady = true;
      return;
    }
    var results = {};
    results.open = { title: document.title, canvasNodes: GalEditor._canvas().graph._nodes.length, errors: errors.slice() };
    results.navigation = [];
    ['projects', 'setup', 'editor', 'play', 'export'].forEach(function (v) {
      document.querySelector('[data-view="' + v + '"]').click();
      results.navigation.push({ view: v, active: document.querySelector('.view.active').id, player: document.getElementById('player').classList.contains('show') });
      if (v === 'play') document.getElementById('plClose').click();
    });
    document.querySelector('[data-view="editor"]').click();
    results.chapterTree = { acts: document.querySelectorAll('.act-row').length, chapters: document.querySelectorAll('.chapter-row').length, visible: !document.getElementById('chapterPanel').hidden };
    document.querySelector('[data-act="第一幕 雨夜"]').click();
    results.chapterTree.collapsed = document.querySelectorAll('.act-chapters.collapsed').length;
    document.querySelector('[data-act="第一幕 雨夜"]').click();
    document.querySelector('[data-chapter="c3"]').click();
    await wait(100);
    results.singleChapter = { visible: GalFrontendTest.visible(), litegraphNodes: GalEditor._canvas().graph._nodes.length, activeRows: document.querySelectorAll('.chapter-row.active').length };
    document.getElementById('btnShowAll').click();
    await wait(100);
    results.showAll = GalFrontendTest.visible();
    var slider = document.getElementById('rngNodes'); slider.value = 300; slider.dispatchEvent(new Event('input', { bubbles: true }));
    results.slider = { nodes: document.getElementById('lblNodes').textContent, estimate: document.getElementById('lblEstimate').textContent, nodeMax: document.getElementById('rngNodes').max };
    var originalRun = GalAPI.runGeneration, captured = null;
    GalAPI.runGeneration = async function (req) { captured = req; GalFrontendTest.event('done', { stats: { nodes: 800, edges: 799 } }); };
    document.getElementById('btnGenerate').click();
    await wait(100);
    results.generatePayload = captured && captured.params;
    GalAPI.runGeneration = originalRun;
    var originalCancel = GalAPI.cancel, cancelledJob = null;
    GalAPI.cancel = async function (id) { cancelledJob = id; };
    GalFrontendTest.beginProgress(980);
    GalFrontendTest.event('jobstart', { job_id: 'job-cancel-check' });
    document.getElementById('btnCancelGeneration').click();
    await wait(50);
    results.cancellation = { requestedJobId: cancelledJob, panelHidden: document.getElementById('generationProgress').hidden };
    GalAPI.cancel = originalCancel;
    results.performance = await measure();
    GalFrontendTest.applyGraph(fakeGraph(false));
    await wait(100);
    results.noChapters = { hidden: document.getElementById('chapterPanel').hidden, visible: GalFrontendTest.visible() };
    results.finalErrors = errors;
    var pre = document.createElement('pre'); pre.id = 'frontend-results'; pre.textContent = JSON.stringify(results, null, 2);
    document.body.appendChild(pre); document.title = 'FRONTEND_E2E_DONE';
  }
  window.addEventListener('load', function () { run().catch(function (e) { var p = document.createElement('pre'); p.id = 'frontend-results'; p.style.cssText = 'position:fixed;left:8px;top:60px;z-index:99999;max-width:90vw;max-height:80vh;overflow:auto;background:#fee;color:#900;font-size:13px;padding:10px;white-space:pre-wrap;border:1px solid #c00'; p.textContent = JSON.stringify({ fatal: String(e.stack || e), errors: errors }); document.body.appendChild(p); document.title = 'FRONTEND_E2E_DONE'; }); });
})();
