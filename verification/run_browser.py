import asyncio, json, subprocess, time, urllib.request, base64
from pathlib import Path
import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'verification'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
BASE = (ROOT / 'ui' / 'index.html').as_uri()
PORT = 9337

async def main():
    OUT.mkdir(exist_ok=True)
    proc = subprocess.Popen([EDGE, '--headless=new', '--disable-frame-rate-limit', '--disable-gpu-vsync', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--allow-file-access-from-files', f'--remote-debugging-port={PORT}', f'--user-data-dir={OUT / "edge-profile"}', '--window-size=1440,960', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pages = None
        for _ in range(60):
            try:
                pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json', timeout=1)); break
            except Exception: await asyncio.sleep(.1)
        if not pages: raise RuntimeError('浏览器调试端口未启动')
        async with websockets.connect(pages[0]['webSocketDebuggerUrl'], max_size=20_000_000) as ws:
            seq = 0
            async def cmd(method, params=None):
                nonlocal seq
                seq += 1; my = seq
                await ws.send(json.dumps({'id': my, 'method': method, 'params': params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get('id') == my:
                        if 'error' in msg: raise RuntimeError(msg['error'])
                        return msg.get('result', {})
            async def evaljs(expr):
                r = await cmd('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
                return r.get('result', {}).get('value')
            await cmd('Page.enable'); await cmd('Runtime.enable')
            await cmd('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 960, 'deviceScaleFactor': 1, 'mobile': False})
            for name, query in [('e2e', 'frontend-e2e=1'), ('chapters', 'frontend-shot=chapters'), ('chapter', 'frontend-shot=chapter'), ('progress', 'frontend-shot=progress')]:
                await cmd('Page.navigate', {'url': BASE + '?' + query})
                if name == 'e2e':
                    value = None
                    for _ in range(300):
                        await asyncio.sleep(.1)
                        value = await evaljs("document.getElementById('frontend-results')?.textContent || null")
                        if value: break
                    if not value:
                        diagnostic = await evaljs("JSON.stringify({title:document.title,url:location.href,ready:document.readyState,body:document.body?.innerText?.slice(0,1000),scripts:[...document.scripts].map(s=>s.src),test:!!window.GalFrontendTest})")
                        shot = await cmd('Page.captureScreenshot', {'format': 'png'})
                        (OUT / 'failure.png').write_bytes(base64.b64decode(shot['data']))
                        raise RuntimeError('验收超时：' + str(diagnostic))
                    (OUT / 'results.json').write_text(value, encoding='utf-8')
                    print(value)
                else:
                    for _ in range(100):
                        await asyncio.sleep(.1)
                        if await evaljs('!!window.__frontendShotReady'): break
                    await cmd('Page.bringToFront')
                    await asyncio.sleep(.8)
                    shot = await cmd('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
                    (OUT / f'{name}.png').write_bytes(base64.b64decode(shot['data']))
                    print('screenshot', name)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()

asyncio.run(main())
