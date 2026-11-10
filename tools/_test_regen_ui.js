(async function () {
  var out = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var pid = 'p_e699808204d042ea97fbdf33baf49488';

  var info = await GalFrontendTest.boot(pid);
  out.nodes = info.nodes;
  out.links = info.links;

  var flat = GalEditor.dump().nodes;
  var scene = flat.filter(function (n) { return n.type === 'scene'; })[0];
  out.pickType = scene && scene.type;
  var nid = scene.id;
  out.nid = nid;

  GalFrontendTest.select(nid);
  await sleep(250);
  var before = GalFrontendTest.inspect(nid);
  out.beforeBg = (before.bg || {}).asset;
  out.beforeVoice = ((before.lines || [])[0] || {}).voice;
  out.panelShown = document.querySelectorAll('.inspector.show').length;

  var btn = document.getElementById('btnRegen');
  out.btnText = btn.textContent;
  btn.click();
  out.clicked = true;

  // 等按钮恢复可用（重生成里会拉整图 + 渲染，别按固定秒数猜）
  for (var i = 0; i < 90; i += 1) {
    await sleep(1000);
    if (!btn.disabled && i > 3) break;
  }
  out.waited = i + 1;
  out.btnDisabledAtEnd = btn.disabled;

  // 重生成是整图拉回（pullGraphFromBackend），从画布对象读最新
  var row = GalEditor.dump().nodes.filter(function (n) {
    return n.id === nid;
  })[0];
  out.afterBgDump = row && row.bg;
  out.stateText = (document.querySelector('[data-state]') || {}).textContent;
  var logs = Array.prototype.slice.call(document.querySelectorAll('.log-line, #logBody div'));
  out.lastLog = logs.length ? logs[logs.length - 1].textContent.slice(0, 120) : null;
  out.afterBg = (GalFrontendTest.inspect(nid) || {}).bg ? GalFrontendTest.inspect(nid).bg.asset : null;
  out.changed = out.afterBg !== out.beforeBg;

  // 直接问服务端，区分「后端没变」和「前端没刷新」
  var fresh = await GalAPI.getGraph(pid);
  var fn = (fresh.nodes || []).filter(function (n) { return n.id === nid; })[0];
  out.serverAfter = fn && fn.bg ? fn.bg.asset : null;
  out.serverVoice = fn && fn.lines && fn.lines[0] ? fn.lines[0].voice : null;

  return JSON.stringify(out);
})()
