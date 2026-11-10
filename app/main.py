import asyncio
import os
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from .config import DEFAULTS, MOCK, PORT, VERSION
from .providers.base import ProviderError, redact
from .providers.probe import test_provider
from .store import Credentials

app = FastAPI(title='奇迹工坊 Galgame Studio', version=VERSION)
# 最外层 CORS 在文件末尾统一安装，包含异常响应。
credentials = Credentials()

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    """把内部异常转成统一的 HTTP 错误响应。"""
    return JSONResponse({'error': dict(code='HTTP_ERROR', message=str(exc.detail), where='api.routing')}, status_code=exc.status_code)

@app.exception_handler(Exception)
async def internal_error(request, exc):
    """兜底 500：把未预期异常包成标准错误体，不泄露堆栈给前端。"""
    return JSONResponse({'error': dict(code='INTERNAL_ERROR', message='内部处理失败，请检查本机凭据文件和运行环境。', where='api.internal')}, status_code=500)

@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    """ProviderError → HTTP 响应（状态码取错误自带的）。"""
    try:
        configs = credentials.read()
    except ProviderError:
        configs = {}
    payload = dict(exc.payload, message=redact(exc.payload['message'], configs))
    return JSONResponse({'error': payload}, status_code=exc.status)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # 不回显请求体，避免校验错误泄露输入的 key。
    """参数校验失败 → 422 响应，带出错字段。"""
    first = exc.errors()[0]
    field = '.'.join(str(x) for x in first['loc'] if x != 'body') or 'body'
    return JSONResponse({'error': dict(code='INVALID_REQUEST', message='请求参数格式错误', where='api.validation', field=field)}, status_code=422)

@app.get('/api/health')
async def health():
    """健康检查：后端是否活着 + 当前是 real 还是 mock。"""
    return dict(ok=True, version=VERSION, mock=MOCK)

@app.get('/api/providers')
async def providers():
    """列出已配置的 provider（凭据打码后再返回）。"""
    return await asyncio.to_thread(credentials.masked)

@app.put('/api/providers')
async def save_providers(body: dict):
    """保存 provider 配置（凭据走 DPAPI 加密存储）。"""
    for kind, cfg in body.items():
        if kind not in DEFAULTS or not isinstance(cfg, dict):
            raise ProviderError('INVALID_CONFIG', '未知 provider 或配置格式错误', 'providers.save', 422, field=kind)
        for key, value in cfg.items():
            if key not in DEFAULTS[kind]:
                raise ProviderError('INVALID_CONFIG', f'未知配置字段 {kind}.{key}', 'providers.save', 422, field=f'{kind}.{key}')
            default = DEFAULTS[kind][key]
            if type(value) is not type(default) and not (type(default) is float and type(value) in (float, int)):
                raise ProviderError('INVALID_CONFIG', f'字段类型错误 {kind}.{key}', 'providers.save', 422, field=f'{kind}.{key}')
        if 'base_url' in cfg:
            try:
                url = urlsplit(cfg['base_url'])
                url.port
            except ValueError as exc:
                raise ProviderError('INVALID_CONFIG', 'base_url 格式错误', 'providers.save', 422, field=f'{kind}.base_url') from exc
            # bailian_tts 同时支持两条路：CosyVoice 的 wss://，和 qwen3-tts-flash 的 https。
            # 只认 ws/wss 会让「https + qwen3-tts-flash」这种能用的配置被 422 挡在设置页外（踩过）。
            schemes = (('wss', 'ws', 'https', 'http') if kind == 'bailian_tts'
                       else ('https', 'http'))
            if url.scheme not in schemes or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ProviderError('INVALID_CONFIG', 'base_url 必须是不含凭据、查询串的服务地址', 'providers.save', 422, field=f'{kind}.base_url')
        if cfg.get('concurrency', 1) != 1:
            raise ProviderError('INVALID_CONFIG', '本地生图并发必须为 1', 'providers.save', 422, field=f'{kind}.concurrency')
        if 'speed' in cfg and not 0.5 <= cfg['speed'] <= 2:
            raise ProviderError('INVALID_CONFIG', 'speed 必须在 0.5 到 2 之间', 'providers.save', 422, field=f'{kind}.speed')
    # ui/api.js 的 saveProviders 用 `r.ok` 判成败，这里必须带 ok（原 provider 字典保留，向后兼容）。
    saved = await asyncio.to_thread(credentials.write, body)
    return dict(saved, ok=True)

@app.post('/api/providers/{kind}/test')
async def probe(kind: str):
    """测试某个 provider 通不通（发一次最小请求）。"""
    if kind not in DEFAULTS:
        raise ProviderError('UNKNOWN_PROVIDER', '未知 provider', 'providers.test', 404)
    configs = await asyncio.to_thread(credentials.read)
    return await test_provider(kind, configs[kind], configs)


def _bring_browser_forward():
    """把已经在跑的浏览器窗口提到前台（尽力而为，失败不影响打开）。

    为什么要这一步：从后台进程调 ShellExecuteW 打开链接时，浏览器可能把新标签
    放在后台、窗口也不跳到前台，用户看到的就是「点了没反应」（本机实测）。
    """
    import ctypes
    import time
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    targets = []

    def _cb(hwnd, _lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if any(b in title for b in ('Microsoft Edge', 'Google Chrome', 'Mozilla Firefox',
                                        'Microsoft Edge', 'Brave', 'Vivaldi', '- Edge')):
                # 排除我们自己的 pywebview 窗口标题
                if '奇迹工坊' not in title:
                    targets.append(hwnd)
        except Exception:
            pass
        return True

    try:
        # 浏览器是异步起来的：ShellExecuteW 刚返回时窗口还没创建，
        # 所以这里要重试几轮，别一次枚举不到就放弃（实测 0ms 时必然找不到）。
        for _attempt in range(8):
            targets.clear()
            user32.EnumWindows(ENUMPROC(_cb), 0)
            if targets:
                break
            time.sleep(0.35)
    except Exception:
        return False
    if not targets:
        return False
    # SWP_NOSIZE|SWP_NOMOVE|SWP_SHOWWINDOW
    for hwnd in targets[:2]:
        try:
            kernel32.SetThreadPriority  # noqa: B018  保持引用，避免静态检查误报未使用
            user32.ShowWindow(hwnd, 9)              # SW_RESTORE
            user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0040)   # HWND_TOPMOST → 再取消
            user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0040)   # HWND_NOTOPMOST
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            continue
    return True


def _default_browser_exe():
    """从注册表取默认浏览器的可执行文件路径（取不到返回 None）。"""
    import os
    import winreg
    try:
        key = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            prog_id = winreg.QueryValueEx(handle, 'ProgId')[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r'\shell\open\command') as handle:
            command = winreg.QueryValueEx(handle, '')[0]
    except OSError:
        return None
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            return None
        exe = text[1:end]
    else:
        exe = text.split(' ')[0]
    return exe if os.path.isfile(exe) else None


def _browser_alive():
    """数一下主流浏览器进程，判断浏览器到底起没起来。"""
    import subprocess
    try:
        out = subprocess.run(['tasklist'], capture_output=True, text=True,
                             timeout=12, errors='ignore').stdout.lower()
    except Exception:                              # noqa: BLE001
        return False
    return any(name in out for name in ('msedge.exe', 'chrome.exe', 'firefox.exe',
                                        'brave.exe', 'vivaldi.exe', 'opera.exe'))


def _open_external_url(url):
    """在系统默认浏览器里打开 URL，并**验证浏览器真的起来了**。

    ★ 这一整轮都是被「调用返回成功但浏览器根本没出现」坑出来的：
      · webbrowser.open、os.startfile、ShellExecuteW、Popen(msedge)、
        cmd /c start、explorer url、powershell Start-Process —— 从 uvicorn 这个
        被 CREATE_NO_WINDOW 起的子进程里调，全都「返回成功 / 返回码 0」，
        但 tasklist 里浏览器进程数始终是 0。
      · 同样调用在独立 python 或 pwsh 里是能起来的，所以问题在进程环境
        （很可能是安全软件对「后台进程拉起浏览器」的保护）。
    ★ 所以这里不再相信返回值：起完等几轮，真看到浏览器进程才算成功；
      看不到就返回失败，让前端如实告诉用户（而不是假装打开了）。
    ★ rundll32 url.dll,FileProtocolHandler 是走 shell 协议处理器的老路，
      对某些安全软件的拦截比直接 CreateProcess 更容易过。
    """
    import subprocess
    import time

    CREATE_NO_WINDOW = 0x08000000
    tried = []
    plans = [
        ('rundll32', lambda: subprocess.Popen(
            ['rundll32.exe', 'url.dll,FileProtocolHandler', url],
            creationflags=CREATE_NO_WINDOW)),
        ('shell', lambda: subprocess.Popen(
            ['cmd', '/c', 'start', '', url], creationflags=CREATE_NO_WINDOW)),
    ]
    for name, launch in plans:
        try:
            launch()
            tried.append(name)
        except Exception as exc:                   # noqa: BLE001
            tried.append(f'{name}(抛错:{type(exc).__name__})')
            continue
        # 每个方案给 2 秒窗口，看到浏览器进程就收工
        for _ in range(8):
            time.sleep(0.25)
            if _browser_alive():
                return True, f'{name}（已确认浏览器进程出现）'
    return False, ('试过 ' + '、'.join(tried) +
                   '，命令都执行了但 4 秒内没看到浏览器进程 —— '
                   '大概率被安全软件（火绒等）拦住了「后台程序拉起浏览器」')


@app.post('/api/open-url')
async def open_url(body: dict):
    """用系统默认浏览器打开一个外链。

    前端在 pywebview 里 window.open 不一定能出去（webview 会自己拦），
    所以「前往网站」这类按钮走后端开浏览器。只放 http/https。
    """
    url = str(body.get('url') or '').strip()
    if not url.startswith(('http://', 'https://')):
        raise ProviderError('INVALID_URL', '只允许 http/https 链接', 'open_url', 422, field='url')

    ok, detail = await asyncio.to_thread(_open_external_url, url)
    if not ok:
        raise ProviderError('OPEN_FAILED', f'打开浏览器失败：{detail}', 'open_url', 500)
    # 不管走哪条路都把浏览器窗口提到前台 —— 窗口在后台等于「点了没反应」。
    # 注意：这一步只保证「浏览器在最前面」，不保证「你看到的就是刚打开的那个标签」，
    # 已开着的浏览器可能把新标签放后台（浏览器自己的行为，外部强行激活标签不可靠）。
    raised = await asyncio.to_thread(_bring_browser_forward)
    return dict(ok=True, opened=True, raised=raised, detail=detail, url=url)

from .mock_api import router, lifespan
from .api_assets import router as assets_router
from .api_characters import router as characters_router
app.include_router(router)
app.include_router(assets_router)
app.include_router(characters_router)
app.router.lifespan_context = lifespan
# 必须包在 FastAPI 的 ServerErrorMiddleware 外层，连未处理异常的 500 也带 CORS。
api = app
app = CORSMiddleware(api, allow_origins=['*'], allow_credentials=False,
                    # DELETE / PATCH 必须在这里 —— 它们属于"非简单请求"，前端会先发 OPTIONS 预检，
                    # 不在白名单里浏览器就直接拒绝，前端只能拿到 Failed to fetch（踩过两次：
                    # 一次是 DELETE，一次是 PATCH —— 参考图改名和语音标注都因此静默失效）
                    allow_methods=['GET', 'PUT', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
                    allow_headers=['Content-Type', 'Last-Event-ID'])

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=PORT)
