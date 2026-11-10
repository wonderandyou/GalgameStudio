/* 用 Chrome DevTools Protocol 打开页面：抓 console/异常 + 可执行一段 JS + 截图。
   比 --screenshot 可靠：能看到页面里到底抛了什么错、DOM 是什么样。

   用法：node _cdp_shot.js <url> <out.png> [等待ms] ["要执行的JS"]
*/
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const url = process.argv[2];
const out = process.argv[3];
const waitMs = Number(process.argv[4] || 9000);
const evalJsArg = process.argv[5] || '';
// 可选：第 6 个参数指定窗口尺寸（默认 1600x1000），用来验窄窗口下的自适应布局
const sizeArg = process.argv[6] || '';
const winSize = /^\d+x\d+$/.test(sizeArg) ? sizeArg : '1600x1000';
// 传 @文件路径 可以避开命令行引号转义问题（PowerShell 会吃掉内层双引号）
const evalJs = evalJsArg.startsWith('@')
  ? fs.readFileSync(evalJsArg.slice(1), 'utf8')
  : evalJsArg;
const port = 9300 + Math.floor(Math.random() * 300);
const prof = path.join(os.tmpdir(), 'cdp_' + Date.now());

const proc = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars',
  '--remote-debugging-port=' + port,
  '--user-data-dir=' + prof,
  '--window-size=' + winSize.replace('x', ','),
  'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(p) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port, path: p }, (r) => {
      let b = '';
      r.on('data', (c) => { b += c; });
      r.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

(async () => {
  let list = null;
  for (let i = 0; i < 80; i++) {
    try {
      list = await getJson('/json/list');
      if (list && list.length) break;
    } catch (_) { /* 还没起来 */ }
    await sleep(250);
  }
  if (!list || !list.length) { console.log('CDP 连不上'); proc.kill(); process.exit(1); }

  const target = list.find((t) => t.type === 'page') || list[0];
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let seq = 0;
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id).resolve(m.result);
      pending.delete(m.id);
      return;
    }
    if (m.method === 'Runtime.consoleAPICalled') {
      logs.push('[console.' + m.params.type + '] ' + m.params.args
        .map((a) => (a.value !== undefined ? a.value : (a.description || a.type))).join(' '));
    } else if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      const desc = (d.exception && (d.exception.description || d.exception.value)) || d.text;
      logs.push('[EXCEPTION] ' + desc);
    } else if (m.method === 'Log.entryAdded') {
      logs.push('[log.' + m.params.entry.level + '] ' + m.params.entry.text);
    }
  };
  await new Promise((r) => { ws.onopen = r; });
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Page.navigate', { url });
  await sleep(waitMs);

  if (evalJs) {
    const r = await send('Runtime.evaluate', {
      expression: evalJs, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) {
      console.log('EVAL 抛错:', JSON.stringify(r.exceptionDetails.exception || r.exceptionDetails.text));
    } else {
      console.log('EVAL:', JSON.stringify(r.result && r.result.value));
    }
  }

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
  console.log('--- 页面日志 ---');
  if (!logs.length) console.log('（无）');
  logs.forEach((l) => console.log(l));
  console.log('saved ' + out);
  proc.kill();
  process.exit(0);
})().catch((e) => { console.log('ERR', e && e.stack || e); process.exit(1); });
