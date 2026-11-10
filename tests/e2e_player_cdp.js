/* 7.4 播放器 + 单章视图保存回归（真浏览器，CDP）。
 *
 * 前提：后端（GAL_PORT）和 ui/ 静态服务已起，Edge 以 --remote-debugging-port 打开了 UI 页。
 * 用法：node tests/e2e_player_cdp.js <项目id> <截图目录> [CDP 端口=9333] [页面 url 关键字=8798]
 *
 * 断言：
 *   1. 单章视图（第 2 章，LiteGraph 内部 id 与项目 id 错位）下保存，拓扑一条不变
 *   2. 保存后的图：start 恰 1 出口、scene 1、choice 2–4、ending 0，edges 数 == choices 数
 *   3. 试玩从起点走到结局：每个选择点 ≤4 个按钮、无重复文字、每个按钮都能走到已存在节点、不卡死路
 *   4. 控制台没有「[player] 选项数异常」
 */
const fs = require('fs');
const http = require('http');
const path = require('path');

const [pid, shotDir, portArg, urlKey] = process.argv.slice(2);
const port = Number(portArg || 9333);
const key = urlKey || '8798';
let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? '  OK   ' : '  FAIL ') + name + (ok || detail === undefined ? '' : '  → ' + JSON.stringify(detail).slice(0, 400)));
  if (!ok) failures += 1;
}
function getJson(p) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port, path: p }, (r) => {
      let b = '';
      r.on('data', (c) => { b += c; });
      r.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  let target;
  for (let i = 0; i < 40 && !target; i++) {
    try { target = (await getJson('/json')).find((t) => t.type === 'page' && t.url.includes(key)); } catch (e) {}
    if (!target) await sleep(500);
  }
  if (!target) throw new Error('没找到 UI 页面');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let seq = 0;
  const consoleLog = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.consoleAPICalled') {
      consoleLog.push('[' + m.params.type + '] ' + m.params.args.map((a) => a.value !== undefined ? (typeof a.value === 'object' ? JSON.stringify(a.value) : a.value) : (a.description || a.type)).join(' '));
    } else if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      consoleLog.push('[exception] ' + ((d.exception && d.exception.description) || d.text));
    }
  };
  await new Promise((r) => { ws.onopen = r; });
  const send = (method, params) => new Promise((resolve) => {
    const id = ++seq;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  async function evaluate(expression) {
    const m = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 120000 });
    if (m.result && m.result.exceptionDetails) throw new Error('页面抛错：' + JSON.stringify(m.result.exceptionDetails.exception || m.result.exceptionDetails.text));
    return m.result && m.result.result && m.result.result.value;
  }
  async function shot(name) {
    const m = await send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(shotDir, name);
    fs.writeFileSync(file, Buffer.from(m.result.data, 'base64'));
    console.log('  截图 ' + file);
  }
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
  fs.mkdirSync(shotDir, { recursive: true });
  for (let i = 0; i < 60; i++) {
    if (await evaluate('!!(window.GalFrontendTest && window.GalAPI)')) break;
    await sleep(500);
  }

  console.log('='.repeat(70));
  console.log('1. 单章视图保存：拓扑不变');
  console.log('='.repeat(70));
  const booted = await evaluate(`GalFrontendTest.boot(${JSON.stringify(pid)})`);
  console.log('  boot', JSON.stringify(booted));
  const topo = `(async () => {
    const r = await fetch(GalAPI.base + '/api/projects/' + ${JSON.stringify(pid)} + '/graph');
    const g = await r.json();
    return { edges: (g.edges || []).length, nodes: g.nodes.map((n) => [n.id, n.type, (n.choices || []).map((c) => c.to)]) };
  })()`;
  const before = await evaluate(topo);
  const chapters = await evaluate('GalFrontendTest.chapters()');
  console.log('  章节数 ' + chapters.length);
  const chapter = chapters[1] || chapters[0];
  await evaluate(`GalFrontendTest.selectChapter(${JSON.stringify(chapter)})`);
  await sleep(500);
  console.log('  当前视图', JSON.stringify(await evaluate('GalFrontendTest.visible()')));
  const payload = await evaluate('GalFrontendTest.payload()');
  check('payload 不再带 edges', !('edges' in payload), Object.keys(payload));
  const saved = await evaluate('GalFrontendTest.save()');
  check('单章视图保存成功（PUT 200）', saved === true, saved);
  const after = await evaluate(topo);
  const afterById = Object.fromEntries(after.nodes.map((n) => [n[0], n]));
  const diff = before.nodes.filter((n) => JSON.stringify(n) !== JSON.stringify(afterById[n[0]]));
  check('保存前后每个节点的出口一条不变', !diff.length && before.nodes.length === after.nodes.length,
    diff.slice(0, 3).map((n) => [n, afterById[n[0]]]));
  check('保存前后节点顺序不变', before.nodes.map((n) => n[0]).join() === after.nodes.map((n) => n[0]).join());
  const choiceCount = after.nodes.reduce((s, n) => s + n[2].length, 0);
  check('edges 数 == choices 数（edges 是投影）', after.edges === choiceCount, [after.edges, choiceCount]);
  const bad = after.nodes.filter(([, t, to]) => !(t === 'ending' ? to.length === 0 : t === 'choice' ? to.length >= 2 && to.length <= 4 : to.length === 1));
  check('出口数：start/scene=1、choice 2–4、ending 0', !bad.length, bad.slice(0, 5));
  await shot('01-chapter-view.png');

  console.log('='.repeat(70));
  console.log('2. 试玩：从起点走到结局');
  console.log('='.repeat(70));
  await evaluate(`GalFrontendTest.selectChapter(null)`);
  await evaluate(`(() => {
    window.__walk = {
      async untilStop() {
        for (let i = 0; i < 4000; i++) {
          if (document.getElementById('pChoices').classList.contains('show')) return { stop: 'choice', cur: GalFrontendTest.player.cur() };
          if (document.getElementById('pEnding').style.display === 'grid') return { stop: 'ending', cur: GalFrontendTest.player.cur() };
          document.getElementById('stage').dispatchEvent(new MouseEvent('click', { bubbles: true }));
          await new Promise((r) => setTimeout(r, 15));
        }
        return { stop: 'stuck', cur: GalFrontendTest.player.cur() };
      },
      buttons() { return [...document.querySelectorAll('#pChoiceBox button')].map((b) => b.textContent); },
      pick(k) { document.querySelectorAll('#pChoiceBox button')[k].click(); }
    };
    GalFrontendTest.player.open();
    return true;
  })()`);
  const known = new Set(after.nodes.map((n) => n[0]));
  const outs = Object.fromEntries(after.nodes.map((n) => [n[0], n[2]]));
  let points = 0, shots = 0, stop;
  const visited = [];
  for (let step = 0; step < 200; step++) {
    stop = await evaluate('__walk.untilStop()');
    visited.push(stop.cur.id);
    if (stop.stop !== 'choice') break;
    points += 1;
    const labels = await evaluate('__walk.buttons()');
    const id = stop.cur.id;
    check(`选择点 ${id}：${labels.length} 个按钮 ≤4`, labels.length >= 1 && labels.length <= 4, labels);
    check(`选择点 ${id}：按钮文字不重复`, new Set(labels).size === labels.length, labels);
    check(`选择点 ${id}：每个按钮都指向存在的节点`, outs[id].length === labels.length && outs[id].every((t) => known.has(t)), outs[id]);
    if (shots < 3) { shots += 1; await shot(`0${shots + 1}-choice-${id}.png`); }
    await evaluate(`__walk.pick(${points % labels.length})`);
    await sleep(50);
  }
  check('走到结局（没卡死路）', stop && stop.stop === 'ending', stop);
  console.log(`  经过 ${points} 个选择点，最后停在 ${stop && stop.cur.id}，旗标 ${JSON.stringify(await evaluate('GalFrontendTest.player.flags()'))}`);
  await shot('05-ending.png');

  console.log('='.repeat(70));
  console.log('3. 控制台');
  console.log('='.repeat(70));
  consoleLog.forEach((l) => console.log('  ' + l.slice(0, 300)));
  check('控制台没有「[player] 选项数异常」', !consoleLog.some((l) => l.includes('[player] 选项数异常')));
  check('没有未捕获异常', !consoleLog.some((l) => l.startsWith('[exception]')), consoleLog.filter((l) => l.startsWith('[exception]')));
  console.log('='.repeat(70));
  console.log(failures ? `${failures} 项失败` : '全部通过 ✓');
  ws.close();
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.log('ERR', (e && e.stack) || e); process.exit(2); });
