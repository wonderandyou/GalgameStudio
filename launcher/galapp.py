# -*- coding: utf-8 -*-
"""奇迹工坊 · Galgame Studio —— 桌面启动器。

一个窗口搞定：
  1. 探测/拉起后端（FastAPI，127.0.0.1:8790）
  2. 探测/拉起界面静态服务（127.0.0.1:8799，只服务 ui/ 目录）
  3. 用 pywebview 开一个原生窗口，不是浏览器
  4. 关窗口时，只收掉「本进程自己起的」子进程；已经在跑的不动

日志：%LOCALAPPDATA%\\GalgameStudio\\launcher.log
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = bool(getattr(sys, 'frozen', False))

if FROZEN:
    # 打包后没有源码目录：exe 在哪，数据（projects/、library/）就放哪；
    # 只读资源（ui/）在 PyInstaller 的解包目录里。
    ROOT = Path(sys.executable).resolve().parent
    RES = Path(getattr(sys, '_MEIPASS', str(ROOT)))
else:
    ROOT = Path(__file__).resolve().parent.parent
    RES = ROOT

UI = RES / 'ui'
VENV_PY = ROOT / 'app' / '.venv' / 'Scripts' / 'python.exe'

# ★ app/config.py 在 import 那一刻就把 ROOT 定死，必须**先**设环境变量再 import app，
#   否则打包后项目数据会去 C 盘的临时解包目录里写，退出就没了。
if FROZEN:
    os.environ.setdefault('GAL_ROOT', str(ROOT))
    if str(RES) not in sys.path:
        sys.path.insert(0, str(RES))

# ★ 凭据目录必须和 scripts\run-backend.ps1 用同一份。
#   不设的话 config.py 会退到 %LOCALAPPDATA%\GalgameStudio，而界面保存的 key
#   落在项目 .state 里 —— 两边不是同一份，结果就是「界面显示已连接、生成却说未启用」（踩过）。
os.environ.setdefault('GAL_STATE_DIR', str(ROOT / '.state'))

BACKEND_PORT = 8790
SERVE_PORT = 8799
# 打包模式没有独立的静态服务：ui/ 直接挂在后端根路径上，省掉第二个端口。
PAGE_PORT = BACKEND_PORT if FROZEN else SERVE_PORT

APP_TITLE = '奇迹工坊 · Galgame Studio'

_log_lock = threading.Lock()
_children = []


def _log_dir():
    """日志目录：%LOCALAPPDATA% → %TEMP% → 项目根。
    注意回退到项目根时不要再套一层 GalgameStudio，否则会多出个同名子目录。"""
    for base in (os.environ.get('LOCALAPPDATA'), os.environ.get('TEMP')):
        if not base:
            continue
        try:
            d = Path(base) / 'GalgameStudio'
            d.mkdir(parents=True, exist_ok=True)
            probe = d / '.writable'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
            return d
        except OSError:
            continue
    return ROOT


LOG_DIR = ROOT
LOG_FILE = ROOT / 'launcher.log'   # 固定写项目根：LOCALAPPDATA 那套探测在这台机器上不可靠


_T0 = time.time()


def brand_icon_path():
    """品牌 ico 的路径；找不到返回 None。"""
    icon = ROOT / 'ui' / 'brand' / '奇迹工坊.ico'
    return icon if icon.is_file() else None


def log(msg):
    # 带相对启动的毫秒数 —— 排查"启动慢"只能靠这个分解，光有秒看不出谁在耗时
    el = int((time.time() - _T0) * 1000)
    line = '%s %7dms  %s' % (time.strftime('%H:%M:%S'), el, msg)
    with _log_lock:
        try:
            with LOG_FILE.open('a', encoding='utf-8') as fh:
                fh.write(line + '\n')
        except OSError as exc:
            # 写不下来必须喊出来，静默吞掉就变成"日志空白、无从排查"
            try:
                sys.stderr.write('LOGFAIL %s: %r\n' % (LOG_FILE, exc))
                sys.stderr.flush()
            except Exception:
                pass


def port_open(port, host='127.0.0.1', timeout=0.4):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(timeout)
        return sk.connect_ex((host, port)) == 0


def http_ok(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_until(predicate, seconds, step=0.35):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def pick_python():
    """跑后端的解释器：优先项目 venv，退回到当前解释器。"""
    if VENV_PY.is_file():
        return str(VENV_PY)
    return sys.executable


def spawn(args, cwd, name):
    """起一个后台子进程（无黑框），返回 Popen。"""
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    log('启动子进程[%s]: %s' % (name, ' '.join(str(a) for a in args)))
    proc = subprocess.Popen(
        [str(a) for a in args],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    _children.append((name, proc))
    return proc


_inprocess = {'server': None}


def ensure_streams():
    """windowed（无控制台）打包模式下，sys.stdout / sys.stderr 是 None。

    uvicorn 配 logging 时会对它们调 .isatty()，直接 AttributeError 崩掉
    （报错原文：`'NoneType' object has no attribute 'isatty'`）。
    这里给它们兜到同一个日志文件，顺带把 uvicorn 的输出也留下来。
    """
    if not FROZEN:
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        handle = open(ROOT / 'app.log', 'a', encoding='utf-8', buffering=1)
    except OSError:
        handle = open(os.devnull, 'a', encoding='utf-8')
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, handle)


def start_inprocess_backend():
    """打包模式：后端和界面跑在**同一个进程**里。

    为什么打包后不能再拉子进程：exe 里没有 python.exe，
    `python -m uvicorn` 必然 FileNotFoundError。
    所以这里直接在后台线程起 uvicorn，再把 ui/ 挂到它的根路径上 ——
    界面和 /api 同源，连第二个端口都省了。
    """
    from fastapi.staticfiles import StaticFiles
    from app.main import app as wrapped
    # main.py 末尾把 app 换成了 CORSMiddleware 实例（为了连 500 也带 CORS），
    # 那个对象没有 mount()。静态文件要挂到它包着的内层 FastAPI 上。
    inner = getattr(wrapped, 'app', wrapped)
    if UI.is_dir():
        # 挂载必须在 /api 等路由注册之后，FastAPI 按注册顺序匹配。
        inner.mount('/', StaticFiles(directory=str(UI), html=True), name='ui')
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(wrapped, host='127.0.0.1',
                                          port=BACKEND_PORT, log_level='warning'))
    threading.Thread(target=server.run, daemon=True, name='backend').start()
    _inprocess['server'] = server
    return server


def ensure_backend():
    """返回 (ready, started_by_us)。"""
    if port_open(BACKEND_PORT):
        ok = http_ok('http://127.0.0.1:%d/api/health' % BACKEND_PORT)
        log('后端端口已占用，健康检查=%s' % ok)
        return ok, False
    if FROZEN:
        log('打包模式：同进程启动后端 + 界面')
        start_inprocess_backend()
        ready = wait_until(
            lambda: http_ok('http://127.0.0.1:%d/api/health' % BACKEND_PORT, 1.2), 25)
        log('后端就绪=%s' % ready)
        return ready, True
    spawn([pick_python(), '-m', 'uvicorn', 'app.main:app',
           '--host', '127.0.0.1', '--port', str(BACKEND_PORT)], ROOT, 'backend')
    ready = wait_until(
        lambda: http_ok('http://127.0.0.1:%d/api/health' % BACKEND_PORT, 1.2), 25)
    log('后端就绪=%s' % ready)
    return ready, True


def ensure_ui_server():
    """返回 (ready, started_by_us)。只服务 ui/ 目录。

    ★ 用 launcher/ui_server.py（ThreadingHTTPServer + 长缓存头），不是 -m http.server：
      · http.server 是单线程的，1.3 MB 静态资源被串行传输（实测页面 loaded 1.2 秒）
      · 它不发 Cache-Control，配 ?v= 指纹也没法缓存，每次启动都重下这 1.3 MB
    ★ 这个进程【故意常驻】：关窗口时不杀。下次启动端口已在监听直接复用，
      省掉实测 887ms 的子进程启动 + 端口等待。它只是静态文件服务，常驻几乎不占资源。
    """
    if FROZEN:
        # 打包模式没有独立的静态服务：ui/ 已经挂在后端的根路径上。
        log('打包模式：界面与后端同端口 %d' % BACKEND_PORT)
        return True, False
    if port_open(SERVE_PORT):
        log('界面服务已在跑，直接复用')
        return True, False
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    log('启动界面服务：ui_server.py %d' % SERVE_PORT)
    subprocess.Popen(
        [sys.executable, str(ROOT / 'launcher' / 'ui_server.py'),
         str(SERVE_PORT), str(UI)],
        cwd=str(UI),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    ready = wait_until(lambda: port_open(SERVE_PORT), 12)
    log('界面服务就绪=%s' % ready)
    return ready, True


def stop_children():
    for name, proc in reversed(_children):
        try:
            if proc.poll() is None:
                log('关闭子进程[%s] pid=%s' % (name, proc.pid))
                if os.name == 'nt':
                    # taskkill 是**控制台程序**：不指定 CREATE_NO_WINDOW 时，
                    # 系统会给它新建一个控制台窗口 —— 表现就是关闭奇迹工坊时
                    # 闪一下黑框（主人报的）。后端/界面服务本身都是后台进程，
                    # 不需要控制台。
                    _flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    result = subprocess.run(
                        ['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                        capture_output=True, text=True, creationflags=_flags)
                    # 以前这里把 stdout/stderr 全丢掉，杀不掉也不留痕迹。
                    # 「Access denied」这类失败必须记下来，否则界面关了、
                    # 后端还活着，下次启动会复用这个僵尸进程，很难查。
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout or '').strip()[:200]
                        log('关闭[%s] taskkill 失败 rc=%s：%s' % (name, result.returncode, detail))
                else:
                    proc.terminate()
        except Exception as exc:
            log('关闭[%s]失败：%s' % (name, exc))
    _children.clear()


# ---------------------------------------------------------------- 单实例
# ★ 踩过的坑（2026-11-08，主人看到界面写着「读取项目列表失败：Failed to fetch」）：
#   连点两次图标 → 两个启动器同时活着，但后端只有一个（第二个走「端口已占用」复用了第一个的）。
#   关掉先开的那个 → 它按「自己起的子进程」把后端杀了 → 后面那个立刻连不上后端，
#   整个界面全是 Failed to fetch。加锁以后不会有两个实例抢同一个后端。
_SINGLE_HANDLE = None


def acquire_single_instance():
    """本进程是不是唯一实例。拿不到锁返回 False（说明已经有窗口在跑）。"""
    global _SINGLE_HANDLE
    if os.name != 'nt':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        # Local\ 前缀：只在本登录会话内互斥，别的用户开一份互不影响
        handle = k32.CreateMutexW(None, True, 'Local\\GalgameStudio.single.instance.v1')
        if not handle:
            log('单实例锁：拿不到句柄（照常启动）')
            return True
        if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
            k32.CloseHandle(handle)
            return False
        _SINGLE_HANDLE = handle      # ★ 必须留到进程结束；局部变量会被回收、锁就失效了
        return True
    except Exception as exc:
        log('单实例锁失败（照常启动）：%r' % exc)
        return True


def release_single_instance():
    """放掉本进程持有的单实例锁。

    ★★ 为什么必须放（2026-11-11，第三轮验证 P2-B）：
      窗口关闭后进程要常驻保住后端（说明书 S5 的承诺），可 mutex 一直被它握着。
      用户再双击时，新进程 CreateMutexW 拿到 ERROR_ALREADY_EXISTS → 判定「已有实例」
      → 转去找窗口 → 窗口早就关了，找不到 → 直接退出。
      **用户看到的就是「双击一下，什么都没发生」** —— 历次报「启动器打不开」的根因。

      窗口没了就该把锁让出来：下一次双击能正常拿到锁，去复用已有的后端、开新窗口。
      注意 mutex 只能由持有者自己释放，别的进程 CloseHandle 救不了。
    """
    global _SINGLE_HANDLE
    if not _SINGLE_HANDLE:
        return False
    try:
        import ctypes
        ctypes.WinDLL('kernel32').CloseHandle(_SINGLE_HANDLE)
        log('单实例锁已释放（窗口关了，让下次双击能开新窗口）')
    except Exception as exc:
        log('释放单实例锁失败：%r' % exc)
    _SINGLE_HANDLE = None
    return True


def activate_existing_window():
    """把已经开着的**我们自己的**窗口调到最前面（被单实例锁拦下来时用）。

    ★ 不能用 FindWindowW(None, APP_TITLE)（2026-11-11 实测）：
      它命中的是 explorer.exe 的 `Windows.Internal.Shell.TabProxyWindow`，
      大小 135×37 —— 一个资源管理器的代理窗口，不是我们那个 1400×900 的真窗口。
      点它当然什么都不会发生。所以改成按句柄挨个试 `_app_window_handles()`。
    """
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        for hwnd in _app_window_handles():
            user32.ShowWindow(hwnd, 9)              # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            log('已把现有窗口调到前面（hwnd=%s）' % hwnd)
            return True
    except Exception as exc:
        log('前台激活旧窗口失败：%r' % exc)
    return False


# ---------------------------------------------------------------- 后端自愈
# 复用别人的后端时，对方可能正在关闭并把它杀掉（单实例锁已经挡住主要路径，
# 但手动结束任务管理器里的实例、或别的方式仍可能出现）。这里留一道保险：
# 页面加载后如果徽标显示「离线」，就自己重起后端并重载页面，只试一次。
_recovery_done = False


def recover_backend():
    global _recovery_done
    if _recovery_done:
        return False
    _recovery_done = True
    health = 'http://127.0.0.1:%d/api/health' % BACKEND_PORT
    if http_ok(health, 1.2):
        return True
    log('后端离线，尝试自愈：重新拉起后端')
    try:
        if FROZEN:
            start_inprocess_backend()
        else:
            spawn([pick_python(), '-m', 'uvicorn', 'app.main:app',
                   '--host', '127.0.0.1', '--port', str(BACKEND_PORT)], ROOT, 'backend')
    except Exception as exc:
        log('自愈启动失败：%r' % exc)
        return False
    ok = wait_until(lambda: http_ok(health, 1.2), 25)
    log('自愈结果：后端就绪=%s' % ok)
    return ok


GA_ROOT = 2            # GetAncestor 的 GA_ROOT
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010

_USER32 = None


def _u32():
    """统一声明过 argtypes/restype 的 user32。

    ★ 64 位下必须显式声明：不声明时 ctypes 把参数和返回值都当 32 位 c_int，
      HWND 指针会被截断 —— 拿到的是垃圾句柄，SetWindowPos 挪不动、
      WM_SETICON 也设不上去。本轮的「拖不动 / 图标不换」就出在这上面。
    """
    global _USER32
    if _USER32 is not None:
        return _USER32
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL('user32', use_last_error=True)

    def sig(name, argtypes, restype):
        fn = getattr(u, name)
        fn.argtypes = argtypes
        fn.restype = restype

    sig('GetAncestor', [wintypes.HWND, wintypes.UINT], wintypes.HWND)
    sig('GetWindowRect', [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL)
    sig('SetWindowPos', [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL)
    sig('IsWindow', [wintypes.HWND], wintypes.BOOL)
    sig('IsWindowVisible', [wintypes.HWND], wintypes.BOOL)
    sig('GetWindowTextLengthW', [wintypes.HWND], ctypes.c_int)
    sig('GetWindowTextW', [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
    sig('GetClassNameW', [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
    sig('EnumWindows', None, wintypes.BOOL)
    sig('SendMessageW', [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        ctypes.c_void_p)
    sig('LoadImageW', [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                       ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.HANDLE)
    sig('DestroyIcon', [wintypes.HANDLE], wintypes.BOOL)
    sig('BringWindowToTop', [wintypes.HWND], wintypes.BOOL)
    _USER32 = u
    return u


class WindowApi:
    """页面右上角两个按钮 + 标题栏拖动，都走这里控制真窗口。"""

    def minimize(self):
        try:
            import webview
            win = webview.windows[0] if webview.windows else None
            if win:
                win.minimize()
                log('窗口已最小化')
        except Exception as exc:
            log('最小化失败：%r' % exc)

    def close(self):
        try:
            import webview
            win = webview.windows[0] if webview.windows else None
            if win:
                log('窗口被页面的 × 关闭')
                win.destroy()
        except Exception as exc:
            log('关闭失败：%r' % exc)

    def _hwnd(self):
        """顶层窗口句柄。

        ★ 三个实测坑，别再踩：
          1. 这里必须 import webview。上一轮改代码时把它删了 → NameError →
             被 except 吞成 0 → 日志只写「拿不到句柄」，看着像系统不给句柄。
          2. 必须是【顶层】窗口，不能拿 WebView2 子控件：子控件上 SetWindowPos
             只是挪窗口内部一块区域、WM_SETICON 任务栏根本不认。用 GetAncestor 校正。
          3. FindWindowW(None, 标题) 在本项目实测返回 0，别依赖它；
             兜底按标题 EnumWindows。
        """
        try:
            import webview
            from ctypes import wintypes
            win = webview.windows[0] if webview.windows else None
            native = getattr(win, 'native', None) if win else None
            hwnd = 0
            if native is not None and hasattr(native, 'Handle'):
                try:
                    hwnd = int(native.Handle)
                except Exception:
                    hwnd = 0
            if hwnd:
                u = _u32()
                if not u.IsWindow(wintypes.HWND(hwnd)):
                    hwnd = 0
                else:
                    root = u.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
                    if root:
                        hwnd = int(root)
            if not hwnd:
                hwnd = self._find_by_title()
            return int(hwnd or 0)
        except Exception as exc:
            log('_hwnd 失败：%r' % exc)
            return 0

    @staticmethod
    def _find_by_title():
        """EnumWindows 按标题找顶层窗口（FindWindowW 在这里不灵时的兜底）。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            hit = []

            def _cb(hwnd, _lp):
                try:
                    if not u.IsWindowVisible(hwnd):
                        return True
                    n = u.GetWindowTextLengthW(hwnd)
                    if n <= 0:
                        return True
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(hwnd, buf, n + 1)
                    if APP_TITLE in buf.value:
                        hit.append(int(hwnd))
                        return False
                except Exception:
                    pass
                return True

            u.EnumWindows(ENUMPROC(_cb), 0)
            return hit[0] if hit else 0
        except Exception as exc:
            log('按标题找窗口失败：%r' % exc)
            return 0

    def log_window_info(self):
        """把句柄/类名/根祖先/矩形打进日志，方便判断拿的是顶层还是子控件。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            h = self._hwnd()
            if not h:
                log('窗口句柄自查：拿不到句柄')
                return
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(wintypes.HWND(h), cls, 256)
            root = u.GetAncestor(wintypes.HWND(h), GA_ROOT)
            rect = wintypes.RECT()
            u.GetWindowRect(wintypes.HWND(h), ctypes.byref(rect))
            log('窗口句柄自查 hwnd=0x%X 类名=%r 根祖先=0x%X 矩形=(%d,%d)-(%d,%d)'
                % (h, cls.value, int(root or 0),
                   rect.left, rect.top, rect.right, rect.bottom))
        except Exception as exc:
            log('窗口句柄自查失败：%r' % exc)


    def apply_window_icon(self):
        """给窗口换任务栏/标题栏图标。

        pywebview 的 create_window **没有 window_icon 参数**（实测签名里就没有，
        传了会直接 TypeError 把整个启动器打挂）。只能窗口建好后用 Win32 换：
        LoadImageW 从 .ico 读，再 WM_SETICON 给小图标和大图标各设一次。
        """
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            hwnd = self._hwnd()
            if not hwnd:
                log('设置窗口图标：拿不到句柄')
                return False
            icon = brand_icon_path()
            if icon is None:
                log('设置窗口图标：找不到 ui/brand/奇迹工坊.ico')
                return False
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            hicon = u.LoadImageW(None, str(icon), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if not hicon:
                log('设置窗口图标：LoadImageW 失败 err=%d' % ctypes.get_last_error())
                return False
            hp = wintypes.WPARAM(int(hicon))
            old_small = u.SendMessageW(wintypes.HWND(hwnd), WM_SETICON,
                                       wintypes.WPARAM(ICON_SMALL), wintypes.LPARAM(hp.value))
            old_big = u.SendMessageW(wintypes.HWND(hwnd), WM_SETICON,
                                     wintypes.WPARAM(ICON_BIG), wintypes.LPARAM(hp.value))
            # 留住引用：图标句柄一旦被释放，任务栏会回退成 pythonw 的时钟图标
            self._hicon = hicon
            log('窗口图标已设置 hwnd=0x%X 原小图标=%s 原大图标=%s'
                % (hwnd, '有' if old_small else '无', '有' if old_big else '无'))
            return True
        except Exception as exc:
            log('设置窗口图标失败：%r' % exc)
            return False

    def open_folder(self, path):
        """在资源管理器里定位一个文件或打开一个文件夹。

        和 open_url 同理：必须在这个进程里做 —— uvicorn 是后台子进程，
        由它拉起 explorer 也不可靠（本机实测过）。
        """
        try:
            import os
            target = str(path or '').strip()
            if not target:
                return False
            if os.path.isfile(target):
                import subprocess
                # explorer 是 GUI 程序、本来不会弹控制台；这里仍然显式给 flags，
                # 是因为它偶尔会转手拉起 shell 层的东西，统一规格更稳。
                subprocess.Popen(['explorer', '/select,', target],
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                log('open_folder：定位文件 %s' % target)
                return True
            if os.path.isdir(target):
                os.startfile(target)
                log('open_folder：打开目录 %s' % target)
                return True
            # 路径不存在时退回打开父目录，至少让用户看到附近
            parent = os.path.dirname(target)
            if parent and os.path.isdir(parent):
                os.startfile(parent)
                log('open_folder：原路径不存在，打开上级 %s' % parent)
                return True
            log('open_folder：路径不存在 %s' % target)
            return False
        except Exception as exc:                   # noqa: BLE001
            log('open_folder 失败：%r' % exc)
            return False

    def open_url(self, url):
        """用系统默认浏览器打开外链。

        ★ 必须在【这个进程】里做，不能交给后端 uvicorn：
          uvicorn 是被 CREATE_NO_WINDOW 起的后台子进程，在它里面调 shell 拉起浏览器
          会「命令成功返回、浏览器进程根本不出现」——webbrowser / os.startfile /
          ShellExecuteW / Popen(msedge) / cmd start / explorer / powershell 中转
          七种方式全试过，全灭（tasklist 里浏览器进程数始终 0）。
          这里是 pywebview 的宿主进程、带前台窗口，是正常交互进程，起得来。
        """
        try:
            import webbrowser
            target = str(url or '').strip()
            if not target.startswith(('http://', 'https://')):
                log('open_url：拒绝非 http(s) 链接 %r' % target[:60])
                return False
            ok = webbrowser.open(target)
            log('open_url：%s → %s' % (target, 'ok' if ok else 'False'))
            return bool(ok)
        except Exception as exc:                   # noqa: BLE001
            log('open_url 失败：%r' % exc)
            return False

    # ★ 这里以前还有一个 native_drag() / start_drag()（ReleaseCapture +
    #   WM_NCLBUTTONDOWN(HTCAPTION)）。两个都已删除：实测系统不认注入出来的鼠标状态，
    #   消息发出去了窗口纹丝不动；而 native_drag 又排在 move_by 前面并 return，
    #   把唯一能走的路短路了 —— 窗口干脆拖不动。拖动只走下面的 SetWindowPos，别再往回加。

    def _h(self):
        """带缓存的窗口句柄（拖动时每帧都要用，别每次都 import webview 查祖先）。"""
        try:
            from ctypes import wintypes
            h = getattr(self, '_h_cached', 0)
            if h and _u32().IsWindow(wintypes.HWND(h)):
                return h
            h = self._hwnd()
            self._h_cached = h
            return h
        except Exception:
            return self._hwnd()

    def get_window_pos(self):
        """返回 {'x','y','dpr'}：窗口左上角【物理像素】坐标 + 当前 DPI 缩放。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            hwnd = self._h()
            if not hwnd:
                return None
            rect = wintypes.RECT()
            if not u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
            return {'x': int(rect.left), 'y': int(rect.top), 'dpr': dpi_scale()}
        except Exception as exc:
            log('get_window_pos 失败：%r' % exc)
            return None

    def set_window_pos(self, x, y):
        """把窗口左上角挪到绝对物理坐标 (x, y)。

        拖动主路径：页面按下时记住 (鼠标, 窗口) 两个原点，之后每帧只发绝对坐标。
        比发增量稳 —— 丢帧、rAF 合并都不会累积漂移。
        """
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            hwnd = self._h()
            if not hwnd:
                return False
            ok = bool(u.SetWindowPos(
                wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
            self._drag_n = getattr(self, '_drag_n', 0) + 1
            if self._drag_n == 1:
                log('拖动开始：set_window_pos hwnd=0x%X → (%d,%d) ok=%s'
                    % (hwnd, int(x), int(y), ok))
            elif self._drag_n % 20 == 0:
                log('拖动中：第 %d 次 → (%d,%d)' % (self._drag_n, int(x), int(y)))
            return ok
        except Exception as exc:
            log('set_window_pos 失败：%r' % exc)
            return False

    def move_by(self, dx, dy):
        """按增量移动窗口（旧接口，保留兼容；页面现在走 set_window_pos）。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = _u32()
            hwnd = self._hwnd()
            if not hwnd:
                return False
            rect = wintypes.RECT()
            if not u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return False
            x = rect.left + int(dx)
            y = rect.top + int(dy)
            u.SetWindowPos(wintypes.HWND(hwnd), None, x, y, 0, 0,
                           SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            return True
        except Exception as exc:
            log('move_by 失败：%r' % exc)
            return False


def set_app_user_model_id():
    """给本进程一个独立的 AppUserModelID（任务栏分组身份）。

    ★ 这是「窗口图标怎么换都没用」的真正原因（实测确认）：
      Windows 任务栏按 **exe 路径** 分组。本机和「开关机记录」等程序都是
      同一个 pythonw.exe 起的 → 任务栏把它们并进同一个按钮，名字和图标
      沿用先启动那个（实测显示成「⏱️ 开关机记录」+ pythonw 的时钟图标）。
      WM_SETICON 已经把窗口图标换掉了（WM_GETICON 返回非 0），
      但那个任务栏按钮根本不属于这个窗口的 AUMID，所以看起来「图标没变」。
    给进程一个显式 AUMID 之后，任务栏会单独开一个按钮，显示自己的名字和图标。
    必须在创建任何窗口之前调用。
    """
    try:
        import ctypes
        ok = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'MiracleWorkshop.GalgameStudio')
        return 'SetCurrentProcessExplicitAppUserModelID ok' if ok == 0 else 'hr=0x%X' % (ok & 0xFFFFFFFF)
    except Exception as exc:
        return '失败：%r' % exc


def make_dpi_aware():
    """必须在创建任何窗口之前调用。

    不声明 DPI 感知时，GetDpiForSystem() 会返回 96（虚拟化），而 WebView2 子进程
    按真实缩放渲染（dpr=1.5）—— 结果窗口按 96 算尺寸、内容按 1.5 渲染，
    右边和底部被裁进窗口外（− × 和底部那条都点不到）。
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
            return 'shcore.SetProcessDpiAwareness(2)'
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return 'user32.SetProcessDPIAware'
        except Exception:
            pass
    except Exception:
        pass
    return '未设置'


def dpi_scale():
    """系统缩放倍数（150% → 1.5）。

    pywebview 的 width/height 在这台机器上被当成**物理像素**，而 WebView2 会按
    devicePixelRatio 渲染 CSS 像素 —— 实测传 1440 得到窗口 1425 物理像素，
    视口却是 1426 CSS px、dpr=1.5，等于内容需要 2139 物理像素，
    右边整整三分之一（含 − × 和底部那条）被裁在窗口外。
    所以这里要按缩放倍数放大窗口尺寸。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        return max(1.0, min(3.0, dpi / 96.0))
    except Exception:
        return 1.0


def _ui_signature():
    """UI 资源的「内容指纹」：文件没动 → 指纹不变 → 浏览器缓存永远命中。

    ★ 这是启动速度的关键一步。
      以前是「每次启动都把 ?v= 刷成当前时间」，于是每次都是全新 URL、缓存永远失效，
      1.3 MB 的 litegraph.js 每次都要重新下载（实测页面 loaded 花 1.2 秒）。
      现在按 mtime+size 算指纹：只有真的改了文件才换戳，日常启动全部走磁盘缓存。
    """
    total = 0
    newest = 0
    for path in UI.rglob('*'):
        if not path.is_file():
            continue
        if path.name == 'index.html':      # 自己会被改写，必须排除，否则戳永远在变
            continue
        if path.suffix.lower() not in ('.js', '.css', '.png', '.svg', '.woff2', '.ttf'):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        newest = max(newest, int(st.st_mtime))
    # 14 位纯数字，兼容原来的 \?v=\d{12,14} 正则
    return '%010d%04d' % (newest % 10 ** 10, total % 10000)


def refresh_asset_stamp():
    """按内容指纹刷新 index.html 里所有 ?v= 缓存戳。

    踩过好几次：改了 app.js / app.css 忘了刷戳，WebView2 用缓存 → 界面还是旧的，
    人对着旧界面喊"没修好"。所以让启动器自己管。
    但戳必须【只在文件真变时才变】，否则缓存永远用不上，启动就慢。
    """
    idx = UI / 'index.html'
    try:
        text = idx.read_text(encoding='utf-8')
    except OSError as exc:
        log('刷缓存戳失败（读）：%r' % exc)
        return
    stamp = _ui_signature()
    # ★ 必须匹配任意 ?v= 值，不能只匹配 12~14 位纯数字。
    #   踩过（2026-11-10）：app.js?v=20261109b / app.css?v=20261109b / api.js?v=20261109b
    #   是「9 位数字 + 字母」，旧的 \d{12,14} 匹配不上 → 这三个文件的手写戳永不刷新。
    #   再配合 ui_server.py 的 max-age=31536000, immutable，浏览器会一直用旧 app.js：
    #   实测后果是改完前端在真机上完全看不到效果，还按旧逻辑播成 mock 数据。
    new_text, n = re.subn(r'\?v=[^"\s>]+', '?v=' + stamp, text)
    if n == 0 or new_text == text:
        log('缓存戳未变（%s，%d 处）' % (stamp, n))
        return
    try:
        idx.write_text(new_text, encoding='utf-8')
        log('缓存戳已更新为 %s（%d 个资源）' % (stamp, n))
    except OSError as exc:
        log('刷缓存戳失败（写）：%r' % exc)


READY_EVENT = 'Local\\GalgameStudio_Ready'


def signal_ready():
    """告诉启动器（那个 C++ exe）「主窗口已经画好了，把启动画面撤掉」。

    用命名事件而不是文件/端口：exe 在 ShellExecuteW 之前 CreateEvent，
    这里 OpenEvent + SetEvent，双方谁先谁后都不会卡。
    exe 不在（比如直接跑 python 脚本调试）时 OpenEvent 返回 0，忽略即可。
    """
    try:
        import ctypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenEventW.restype = ctypes.c_void_p
        k.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.SetEvent.argtypes = [ctypes.c_void_p]
        k.SetEvent.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenEventW(0x0002, False, READY_EVENT)   # EVENT_MODIFY_STATE
        if not h:
            return
        k.SetEvent(h)
        k.CloseHandle(h)
        log('已通知启动器：主窗口就绪，可以撤掉启动画面')
    except Exception as exc:
        log('通知启动器失败（不影响使用）：%r' % exc)


def find_our_hwnd(title=APP_TITLE):
    """按标题找我们自己的顶层窗口（FindWindowW 在这台机器上不可靠，用 EnumWindows）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.WinDLL('user32')
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, b, n + 1)
        if b.value == title:
            found.append(hwnd)
            return False
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else 0


def bring_on_screen(title=APP_TITLE):
    """把主窗口从屏幕外挪到屏幕中央。

    ★ 为什么这么做：窗口只要显示出来，WebView2 才肯渲染；
      但窗口一显示，用户就先看到一个**还没画完的空白框** —— 那就是"启动闪一下"。
      所以让它先在屏幕外（x=-20000）正常加载、正常渲染，
      页面 loaded 之后（遮罩已经画好）再挪回屏幕中央。
      用户第一次看到这个窗口时，里面已经是"奇迹工坊 + 进度条"了。
    """
    import ctypes
    u = ctypes.WinDLL('user32')

    class RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

    try:
        hwnd = find_our_hwnd(title)
        if not hwnd:
            log('挪窗口失败：找不到窗口句柄')
            return False
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2)
        # ★ 只用 NOSIZE(0x1) + NOZORDER(0x4)，【不要】加 SWP_SHOWWINDOW：
        #   窗口本来已经是可见状态，再给它发一次 show 会让系统重绘/重排一次，
        #   肉眼就是"界面消失一下又回来"（实测踩过）。
        u.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x1 | 0x4)
        log('窗口已挪到屏幕中央：%dx%d @ (%d,%d)' % (w, h, x, y))
        return True
    except Exception as exc:
        log('挪窗口失败：%r' % exc)
        return False


def _shell_execute(program, parameters):
    """用 ShellExecuteEx 拉起程序，返回进程句柄（失败返回 0）。

    和 subprocess.Popen 的区别：这个走 explorer 的 shell 层，
    和用户双击启动等价 —— 新进程不会被关在调用方的作业(job)里。
    Edge 的多进程架构在受限 job 里渲染进程起不来，页面就是白的（实测）。
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('fMask', ctypes.c_ulong),
            ('hwnd', wintypes.HWND),
            ('lpVerb', wintypes.LPCWSTR),
            ('lpFile', wintypes.LPCWSTR),
            ('lpParameters', wintypes.LPCWSTR),
            ('lpDirectory', wintypes.LPCWSTR),
            ('nShow', ctypes.c_int),
            ('hInstApp', wintypes.HINSTANCE),
            ('lpIDList', ctypes.c_void_p),
            ('lpClass', wintypes.LPCWSTR),
            ('hkeyClass', wintypes.HKEY),
            ('dwHotKey', wintypes.DWORD),
            ('hIcon', wintypes.HANDLE),
            ('hProcess', wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040          # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = 'open'
    info.lpFile = program
    info.lpParameters = parameters
    info.lpDirectory = str(ROOT)
    info.nShow = 1
    try:
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
            return 0
    except Exception as exc:
        log('ShellExecuteEx 异常：%r' % exc)
        return 0
    return info.hProcess


def _visible_titles():
    """枚举所有可见窗口的标题。用来判断 Edge app 窗口到底有没有出来。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                titles.append(buffer.value)
        return True

    user32.EnumWindows(callback, 0)
    return titles


def _app_window_handles():
    """收集「像我们那个窗口」的句柄：可见 + 标题含奇迹工坊 + 尺寸够 + 在屏幕内。

    返回句柄集合而不是布尔，是为了让调用方能用**差集**认出自己那一个。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    found = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if '奇迹工坊' not in buffer.value:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        # 屏幕内 + 够大才算数
        if width >= 400 and height >= 300 and rect.left > -1000 and rect.top > -1000:
            found.add(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found


def _app_window_ready(exclude=None):
    """Edge app 窗口的标志：可见 + 尺寸正常 + 在屏幕内。

    ⚠ 只看标题会误判：实测有个 158x26 的任务栏条目标题也是「奇迹工坊」，
    而真正的窗口被摆在 (-21333, -21333) —— 屏幕外，用户看到的就是"什么都没有"。

    ★ `exclude` 是**启动前**就存在的窗口句柄集合。机器上同时开着别的「奇迹工坊」
    窗口（别的 agent 测试、或者用户开了两个）时，以前 0.5 秒就判定"窗口已就绪"，
    然后那个**别人的**窗口一关，这边就记「窗口已关闭」→ 把自己的后端也杀了。
    用差集之后只认「本次启动后新出现」的那一个。
    """
    handles = _app_window_handles()
    if exclude:
        handles -= set(exclude)
    return bool(handles)


def open_in_browser(url):
    """用系统默认浏览器打开界面，打不开就弹框把地址告诉用户。

    这是最后一道兜底。实测在打包后的 exe 里，`webbrowser.open` 有时静默失败
    （windowed 环境没有控制台，出错也看不见），所以：
      1. 先用 os.startfile —— 走 Windows 文件关联，最接近双击
      2. 再退回 webbrowser.open
      3. 都不行就弹框，把地址写清楚让用户自己贴
    宁可多一次点击，也不能让用户对着一片空白不知所以。
    """
    log('打开界面：%s' % url)
    # 顺手写一个 .url 快捷方式：万一自动打开没反应（打包后部分环境会静默失败），
    # 用户双击 exe 同目录下的「打开界面.url」一定能进 —— 双击 URL 不启动程序，
    # 交给系统文件关联，这条路在本机从未失败过。
    try:
        (ROOT / '打开界面.url').write_text(
            '[InternetShortcut]\r\nURL=%s\r\n' % url, encoding='utf-8')
        log('已写快捷方式：%s' % (ROOT / '打开界面.url'))
    except OSError as exc:
        log('写快捷方式失败：%r' % exc)
    try:
        os.startfile(url)                      # noqa: S606 - 我们有意的 shell 打开
        log('已用系统默认浏览器打开')
        return True
    except OSError as exc:
        log('os.startfile 失败：%r' % exc)
    try:
        import webbrowser
        if webbrowser.open(url):
            log('已用 webbrowser 打开')
            return True
    except Exception as exc:
        log('webbrowser.open 失败：%r' % exc)
    message_box('奇迹工坊已启动',
                '界面没能自动打开。请手动在浏览器里访问：\n\n%s\n\n'
                '（这个窗口先别关，关了服务就停了）' % url)
    return False


def message_box(title, text):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)   # MB_ICONINFORMATION


def webview_available():
    """探测 pywebview 到底能不能用。

    打包后 pythonnet 很可能加载不了 .NET 运行时，报：
      Failed to resolve Python.Runtime.Loader.Initialize from .../pythonnet/runtime/Python.Runtime.dll
    这个坑在 PyInstaller 里很深（ClrLoader.dll 的落点、netstandard facade、.NET 版本都可能）。
    先探一次：能用就用原生窗口，不能用就换 Edge 应用模式，别让整个程序直接崩掉。
    """
    try:
        from webview.platforms import edgechromium  # noqa: F401
        return True
    except Exception as exc:
        log('pywebview 不可用：%r' % exc)
        return False


def launch_edge_app(url):
    """用 Edge 的 --app 模式开一个独立窗口（没有地址栏、没有标签页）。

    这是 pywebview 起不来时的兜底：WebView2 是系统自带的，不需要 pythonnet、
    不需要 .NET，绝不会因为打包缺 dll 就开不了窗口。
    代价是页面里的 window.pywebview.api 不存在 —— 前端对右上角按钮做了兜底。
    """
    candidates = [
        Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'))
        / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
        Path(os.environ.get('PROGRAMFILES', r'C:\Program Files'))
        / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
    ]
    edge = next((p for p in candidates if p.is_file()), None)
    if edge is None:
        log('找不到 Edge，用默认浏览器打开')
        import webbrowser
        webbrowser.open(url)
        return 0
    # ⚠ profile 路径有两个坑：
    #   1. 必须 ASCII —— 中文目录下 Edge 会忽略 --app，开成带地址栏的普通窗口
    #   2. 不能用 tempfile.gettempdir() —— PyInstaller 打的 exe 里它可能指向
    #      `_MEIxxxxxx` 那个临时解包目录，进程运行期间被清理，Edge 的 profile
    #      被删就是白屏。这里固定放 %PUBLIC%（ASCII、可写、不会被回收）。
    #   每个进程一个独立目录：共用同一个 profile 时 Edge 会把命令行转交给已有
    #   实例然后自己秒退，--app 在转交中丢掉，同样白屏。
    base = Path(os.environ.get('PUBLIC') or r'C:\Users\Public')
    profile = base / ('GalgameStudio-edge-%d' % os.getpid())
    # ★★ 必须自己先把目录建出来，而且必须是 **ASCII 路径**（2026-11-11 实测：
    #   这一条是「双击没反应」的真根因）：
    #
    #   以前从不建，靠 Edge 自己创建。实测这台机器上打包版进程**建不了**
    #   `C:\Users\Public\GalgameStudio-edge-<pid>`：
    #       [WinError 5] 拒绝访问
    #   → Edge 拿不到 profile → msedge.exe 静默退出 → 30 秒等不到窗口
    #   → 降级到系统默认浏览器。日志里只有「Edge 已拉起」然后就没下文了。
    #
    #   ⚠ 回退位置也必须是 ASCII（注释里早就记着这条，我自己第一版还踩了）：
    #     含中文的路径下 Edge 会**忽略 --app**，开成带地址栏的普通窗口，
    #     标题变成 `127.0.0.1_/`、页面白屏。所以不能用 %TEMP%
    #     （这台机器 TEMP 指向 D:\软件\...，带中文）。
    candidates = []
    local = os.environ.get('LOCALAPPDATA')
    if local and local.isascii():
        candidates.append(Path(local) / 'GalgameStudio' / ('edge-%d' % os.getpid()))
    if str(base).isascii():
        candidates.append(base / ('GalgameStudio-edge-%d' % os.getpid()))
    profile = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            profile = candidate
            break
        except OSError as exc:
            log('profile 目录建不出来（%s）：%r' % (candidate, exc))
    if profile is None:
        # ★ 不要再退到 tempfile.mkdtemp()（2026-11-11 实测踩了两遍）：
        #   这台机器的临时目录是 `D:\软件\...`，**含中文**。而中文路径下
        #   Edge 会忽略 `--app`、开成带地址栏的普通窗口（标题 `127.0.0.1_/`、白屏），
        #   比不传还糟。所以宁可不用独立 profile，直接让 Edge 用默认的
        #   —— 实测 `--app` 单独用能正常开出无地址栏的应用窗口。
        log('⚠ 独立 profile 建不出来（%LOCALAPPDATA% 和 %PUBLIC% 都被拒）'
            ' —— 不传 --user-data-dir，用 Edge 默认 profile')
    log('profile 目录：%s' % (profile or '(不用，走默认 profile)'))
    # ⚠ URL 必须同时满足两条，都是实测踩出来的：
    #   1. 剥掉 `?query&` —— `&` 会被 Edge 当参数分隔，--app 的值被截断成
    #      `127.0.0.1_/index.html`，结果开成白屏的**普通窗口**；
    #   2. 用根路径 `/`（由 StaticFiles(html=True) 映射到 index.html），
    #      带 `/index.html` 反而又会退回普通窗口。
    #   页面参数都有默认值（base 默认 8790、mode 默认 real），不带 query 一样正常。
    # ⚠ 端口必须用 PAGE_PORT，不能写死 BACKEND_PORT。
    #   踩过（2026-11-10）：源码模式（非 FROZEN）界面在 SERVE_PORT=8799，
    #   但这里写死了 BACKEND_PORT=8790 → Edge 打开的是后端 API 的根路径，
    #   返回 404、窗口标题不含「奇迹工坊」→ _app_window_ready() 永远 False
    #   → 等满超时后 kill Edge、退回默认浏览器 → 用户看到"双击没反应"。
    #   打包模式 FROZEN 下 PAGE_PORT 就等于 BACKEND_PORT（界面挂后端根路径），
    #   所以用 PAGE_PORT 两种情况都对。
    bare = 'http://127.0.0.1:%d/' % PAGE_PORT
    log('改用 Edge 应用模式：%s（%s）' % (edge, bare))
    # ★ 必须用 ShellExecuteEx 启动，不能用 subprocess.Popen。
    #   Popen 直接 CreateProcess，Edge 会被关在调用方的作业(job)里，
    #   而 DSH/终端启动本程序时进程带 job 限制，Edge 的渲染子进程起不来 →
    #   开出来是白屏普通窗口、标题只有 `127.0.0.1_/`（实测反复复现）。
    #   ShellExecuteEx 走 explorer 的 shell 层，和用户双击启动等价。
    # ★ 必须显式给 stdin/stdout/stderr 指到 DEVNULL。
    #   windowed（GUI 子系统）的 exe 没有控制台，标准句柄是**无效**的；
    #   不指定的话子进程会继承这几个坏句柄，Edge 的渲染进程初始化失败，
    #   开出来的就是白屏普通窗口（本机实测：同一段代码在 python.exe 里正常，
    #   在打包后的 exe 里必白屏 —— 差别就在这里）。
    # 不指定 --user-data-dir：从打包后的 exe 里指定 profile 目录时，
    # Edge 会因建不出配置目录而秒退（实测：进程还活着=False）。
    # 交给系统里已经跑着的 Edge 实例去开这个 app 窗口，它自己会处理 profile。
    # ★ 必须走 ShellExecuteEx（_shell_execute），不能用 subprocess.Popen。
    #   Popen 直接 CreateProcess，Edge 会被关在调用方的作业(job)里 ——
    #   本程序由 DSH/终端拉起时带 job 限制，Edge 的渲染子进程起不来，
    #   启动器进程本身还会秒退（Chromium 把活转交给已有实例后就退出，这是正常行为）。
    #   实测（2026-11-10）：同样这条命令行，PowerShell 的 Start-Process 能开出窗口，
    #   而这里的 Popen 30 秒都等不到标题 —— 差别就是 job。
    # ★ 启动前先给现有窗口拍一张快照：机器上可能已经开着别的「奇迹工坊」窗口
    #   （用户开了两个、或者别的 agent 正在测），后面只认**这次新出现**的那一个。
    before_windows = _app_window_handles()
    # ★ 必须真的把 --user-data-dir 传给 Edge（算出来了不传，Edge 就会复用默认
    #   profile，撞上「已有实例」把命令行转交过去、自己秒退，--app 在转交中丢掉
    #   → 开出白屏普通窗口 / 标题不对 → 被误判「窗口没出来」）。
    #   ★ 但目录**建不出来**时不能硬传：实测（2026-11-11）打包版进程在某些环境里
    #     连 %LOCALAPPDATA% 都建不了子目录，硬传 → Edge 秒退 → 30 秒没窗口
    #     → 降级到系统默认浏览器。建不出来就退回「不带这个参数」，
    #     实测 `--app` 单独用是能正常出窗口的（代价是共用了用户自己的 Edge profile）。
    # ★★ 参数要给到**最少**（2026-11-11 实测）：
    #   本机唯一 100% 能开出无地址栏应用窗口的组合就是 `--app=<url>` 单独一个。
    #   多带 --window-size / --window-position / --no-first-run 时，通过
    #   ShellExecuteEx 拉起会失败（Edge 秒退、30 秒等不到窗口）。
    #   窗口尺寸/位置交给 Edge 自己的记忆就行，能开出来比好看重要。
    params = ['--app=%s' % bare]
    if profile is not None:
        params.insert(1, '--user-data-dir="%s"' % profile)
    else:
        log('⚠ 不传 --user-data-dir：如果用户自己开着 Edge，--app 可能被转交丢掉')
    sh = _shell_execute(str(edge), ' '.join(params))
    if sh:
        log('Edge 已通过 ShellExecuteEx 拉起（hProcess=%s）' % sh)
    else:
        log('ShellExecuteEx 没能拉起 Edge')

    # ★ 绝不能在这里删 profile —— Edge 正在用它。
    #   之前有一版在 Popen 之后立刻 rmtree，Edge 边跑边被抽走配置目录，
    #   结果就是一片白屏、标题只有 `127.0.0.1_/`（这个 bug 找了好久）。
    #
    # 光「进程起来了」也不等于「窗口好了」：必须等标题变成页面自己的 <title>。
    ready = False
    # ★ 原来的 12 次 × 0.5s = 6 秒太短。实测（2026-11-10）：手动
    #   `msedge --app=...` 冷启动到窗口标题出现正好跨在 6 秒边界上，
    #   系统里没有 Edge 实例时更慢 → 误判成「窗口没出来」→ kill 掉 →
    #   退回系统默认浏览器 → 那条路在 windowed 环境又静默失败 → 用户看到"打不开"。
    #   而这个页面要下 1.3 MB 静态资源 + 渲染整张节点画布，本来就慢。
    for _ in range(60):                 # 最多等 30 秒
        time.sleep(0.5)
        if _app_window_ready(exclude=before_windows):
            ready = True
            break

    if not ready:
        # 不 kill 了：ShellExecuteEx 没有 Popen 对象可 kill，
        # 而且此时系统里可能有用户自己的 Edge 窗口，杀掉会误伤。
        log('Edge app 窗口没出来，改用系统默认浏览器')
        open_in_browser(url)
        log('★ 后端保持运行（页面要靠它）；要停就结束「奇迹工坊.exe」。'
            '再次双击本程序会复用已有后端，不会起第二份。')
        # ★ 没有窗口了，把单实例锁让出来，否则下次双击「什么都没发生」（P2-B）
        release_single_instance()
        while True:
            time.sleep(300)

    log('Edge 应用窗口已就绪，等它关闭…')
    # ⚠ 不能用 proc.wait()：msedge.exe 这个启动器把活交给真正的 browser 进程后
    #   自己就退出了（Chromium 的常规行为），wait() 会立刻返回，
    #   于是程序以为「窗口关了」跟着退出，后端一死窗口就白。
    #   所以改成轮询窗口标题还在不在。
    while _app_window_ready(exclude=before_windows):
        time.sleep(2)
    log('Edge 应用窗口已关闭')

    # 只有窗口真的关了，才清理本次的 profile。
    try:
        import shutil
        if profile is not None:                 # 建不出来时是 None，别拿 None 去 rmtree
            shutil.rmtree(profile, ignore_errors=True)
    except Exception:
        pass
    # ★ 和上面降级路径保持一致：窗口关了**不能把后端带走**。
    #   说明书 S5 承诺「关闭窗口后后端故意常驻」，可这里原来是直接 return 0，
    #   启动器一退就把 backend 子进程一起收掉（实测日志：21:14:09 关闭子进程[backend] pid=35084）。
    #   后果：还开着的浏览器标签全部 Failed to fetch，正在跑的生成/导出直接断。
    #   两条开窗路径行为统一成「窗口是壳，后端独立活着」。
    log('★ 后端保持运行（页面要靠它）；要停就结束「奇迹工坊.exe」。'
        '再次双击本程序会复用已有后端，不会起第二份。')
    # ★ 没有窗口了，把单实例锁让出来，否则下次双击「什么都没发生」（P2-B）
    release_single_instance()
    while True:
        time.sleep(300)


def main():
    ensure_streams()
    log('=' * 56)
    log('%s 启动' % APP_TITLE)
    log('DPI 感知：%s' % make_dpi_aware())
    # ★ 必须在建窗口之前设：任务栏按 AUMID 分组，不设就会和别的 pythonw 程序
    #   并成一个按钮，沿用别人的名字和图标（实测被并到「⏱️ 开关机记录」+ 时钟）。
    log('任务栏身份：%s' % set_app_user_model_id())
    refresh_asset_stamp()

    # 已经有一个窗口在跑就别再开一个：两个实例会抢同一个后端，
    # 关掉其中一个时会把后端带走，另一个立刻到处 Failed to fetch（实测踩过）
    if not acquire_single_instance():
        if activate_existing_window():
            log('已经有一个窗口在运行，已调到前面，本次不再重复打开')
            return 0
        # ★ 锁被占，但一个我们的窗口都找不到 —— 上一个实例只剩后端在常驻、窗口早关了。
        #   以前这里也是直接 return 0，用户看到的就是「双击一下，什么都没发生」
        #   （第三轮验证 P2-B 的原始现象）。这里放行，照常开新窗口。
        log('单实例锁被占，但没找到任何窗口（上一个实例只剩后端），照常开新窗口')

    backend_ok, _ = ensure_backend()
    ui_ok, _ = ensure_ui_server()
    if not ui_ok:
        log('界面服务起不来，退出')
        stop_children()
        return 1

    url = 'http://127.0.0.1:%d/index.html?mode=real&base=%d' % (PAGE_PORT, BACKEND_PORT)
    log('打开窗口：%s（后端=%s）' % (url, '在线' if backend_ok else '离线'))

    if not webview_available():
        # 原生窗口这条路走不通（打包后 pythonnet 常见），换 Edge 应用模式兜底。
        code = launch_edge_app(url)
        stop_children()
        return code

    try:
        import webview
    except ImportError:
        log('没装 pywebview，退回默认浏览器')
        import webbrowser
        webbrowser.open(url)
        return 0

    # ★ 尺寸口径：DPI 感知之后 create_window 的 width/height 是【逻辑像素】，
    #   不要再乘系统缩放，否则窗口会被撑到超出屏幕（实测乘 1.5 后窗口 2160 逻辑
    #   = 3240 物理，被系统裁到 2560，右侧和下侧全出屏）。
    scale = dpi_scale()
    win_w, win_h = 1400, 880
    log('系统缩放 %.2f，窗口 %dx%d（逻辑像素，实际约 %dx%d 物理）'
        % (scale, win_w, win_h, int(win_w * scale), int(win_h * scale)))

    # 窗口图标：pywebview 的 create_window 没有 window_icon 参数（实测签名里就没这一项，
    # 传了直接 TypeError 把启动器打挂），只能等窗口建好再用 Win32 换。见 WindowApi.apply_window_icon。
    api_handle = WindowApi()
    window = webview.create_window(
        APP_TITLE, url,
        width=win_w, height=win_h,
        min_size=(1000, 660),
        # ★ 必须和页面启动遮罩的底色一模一样（#f2f4f8）。
        #   WebView2 在页面首帧之前画的就是这个色，同色衔接就看不到"白一下"。
        background_color='#f2f4f8',
        # 无系统标题栏，用页面自己画的那一条（PCL 风格）。
        # easy_drag 必须关掉：它会把整个窗口做成可拖拽区，
        # 结果画布上拖节点会变成拖窗口。
        frameless=True,
        easy_drag=False,
        js_api=api_handle,
        # ★ 关键：先把窗口丢到屏幕外（-20000）。
        #   窗口只要"可见"，WebView2 才会渲染；但它在屏幕内一露脸，
        #   用户就先看到一个还没画完的空白框 —— 这就是"启动闪一下"。
        #   所以：屏幕外正常加载渲染 → 页面 loaded（遮罩已画好）→ 再挪回屏幕中央。
        x=-20000, y=-20000,
        # ★ hidden=False：创建即"显示"（虽然位置在屏幕外）。
        #   ✗ hidden=True 实测行不通：pywebview + WebView2 在隐藏窗口里根本不加载页面，
        #     等 show() 之后才开始导航 → 双击到看见界面要 4.98 秒（越"优化"越慢）。
        hidden=False,
    )

    def inspect_page():
        """页面加载完立刻自查：白屏/没渲染/模式不对，日志里一眼能看出来。"""
        # ★ 第一件事：告诉 exe「可以撤掉启动画面了」（有启动画面时才生效）。
        signal_ready()
        # ★ 第二件事：把任务栏/窗口角标换成品牌图标（pythonw.exe 的默认图标是个时钟）
        api_handle.log_window_info()
        api_handle.apply_window_icon()
        # ★ 再等 120ms：loaded 只代表资源加载完，浏览器把这一帧真正合成上屏还要一点时间。
        #   多等这一下，窗口移进屏幕时看到的就是最终界面，不会"先出一版再变"。
        #   启动遮罩已经删掉了，这里等的是界面本身画完。
        time.sleep(0.12)
        # ★ 第二件事：页面已经画好了，把窗口从屏幕外挪进来。
        bring_on_screen()
        # ★ 不再调用 window.show()：窗口本来就已经是"显示"状态（只是在屏幕外），
        #   再 show 一次会触发 Activate + 重绘，白白多一次视觉抖动。
        log('页面 loaded 事件到达')
        time.sleep(0.6)          # 等首屏脚本跑完（这里不是 GUI 线程，sleep 不卡窗口）
        # ★ 不要在这里 resize：
        #   create_window 的 width 是【物理像素】，window.resize 的却是【逻辑像素】，
        #   两个一起用会翻倍放大（实测 2160 物理 → resize(2160) → 视口 1724 CSS，
        #   窗口反而超出屏幕）。只靠 DPI 感知 + create_window 的物理像素就够了。
        try:
            title = window.evaluate_js('document.title')
            nodes = window.evaluate_js(
                '(window.GalEditor && GalEditor.dump) ? GalEditor.dump().nodes.length : -1')
            badge = window.evaluate_js(
                "(document.getElementById('modeBadge')||{}).textContent||''")
            canvas = window.evaluate_js(
                "(function(){var c=document.getElementById('galCanvas');"
                "return c ? (c.width+'x'+c.height) : 'none';})()")
            log('页面自查 title=%r 画布节点=%s 画布位图=%s 模式徽标=%r'
                % (title, nodes, canvas, badge))
            # 无边框窗口下页面按钮必须真的能用，这里确认 pywebview.api 暴露出来了
            has_api = window.evaluate_js(
                "(window.pywebview && window.pywebview.api) ? "
                "(typeof pywebview.api.minimize + '/' + typeof pywebview.api.close) : 'none'")
            hint = window.evaluate_js(
                "(document.getElementById('setupHint')||{}).textContent||''")
            log('接口自查 pywebview.api=%s' % has_api)

            # ★ 这里原来有一段"窗口按钮通道实测"：window.minimize() → 停 0.5 秒 → restore()。
            #   它就是主人说的「界面已经出来了，然后消失一下又回来了」的真正原因 ——
            #   启动自检把自己最小化又恢复了一次，用户看到的就是窗口整块消失再出现。
            #   页面 − 按钮的通道早已验证过（pywebview.api 存在 + WindowApi.minimize 可用），
            #   启动流程里【绝不允许再做任何会动窗口的动作】。
            log('底部提示=%r' % hint)

            # 尺寸自查：页面比窗口宽的话，右侧按钮/底部条会被挤出窗口外（用户点不到）
            dims = window.evaluate_js(
                "(function(){"
                "var vw=document.documentElement.clientWidth,"
                "    vh=document.documentElement.clientHeight,"
                "    sw=document.documentElement.scrollWidth,"
                "    sh=document.documentElement.scrollHeight,"
                "    worst=null,max=0,maxTag='';"
                "document.querySelectorAll('*').forEach(function(el){"
                "  var r=el.getBoundingClientRect();"
                "  if(r.right>max){max=r.right;worst=el;maxTag=el.tagName+'.'+String(el.className||'').slice(0,36);}"
                "});"
                "return JSON.stringify({vw:vw,vh:vh,scrollW:sw,scrollH:sh,"
                "dpr:window.devicePixelRatio,maxRight:Math.round(max),widest:maxTag,"
                "bodyScrollW:document.body.scrollWidth});"
                "})()")
            log('尺寸自查 %s' % dims)

            # 关键控件的真实位置：在窗口外＝用户根本点不到
            vis = window.evaluate_js(
                "(function(){"
                "var ids=['btnWinMin','btnWinClose','setupHint','chipVoice','chipConsist','chipReview',"
                "         'btnToEditor','btnResetKeys','modeSwitch'];"
                "return JSON.stringify(ids.map(function(id){"
                "  var el=document.getElementById(id);"
                "  if(!el) return id+':MISSING';"
                "  var r=el.getBoundingClientRect();"
                "  var inView=(r.right<=innerWidth+1 && r.bottom<=innerHeight+1 && r.left>=-1 && r.top>=-1);"
                "  return id+': x'+Math.round(r.left)+'~'+Math.round(r.right)"
                "    +' y'+Math.round(r.top)+'~'+Math.round(r.bottom)"
                "    +' inView='+inView;"
                "}));"
                "})()")
            log('控件可见性 %s' % vis)

            # ★ toast 的启动自检也删了：
            #   它要临时改一遍 textContent 再读计算样式，等于每次启动都强制重排一次 DOM，
            #   而这项检查当初是为了修"提示框没文字"，早已修完并验证过。
            #   启动过程不许再有任何会动到页面的自检。

            # ★ 以前这里会在启动时真弹一个「界面已就绪」的 toast（排查 toast 配色的临时手段），
            #   结果每次打开都在右下角蹦出一个黑色块，和界面出现正好同步 ——
            #   用户说的"启动闪一下"有一部分就是它。已删除，启动时不许再弹任何提示。
            if nodes == 0:
                log('⚠ 画布是空的：可能图没拉回来')
            if badge != '真后端':
                log('⚠ 模式徽标不是「真后端」：%r' % badge)
                # 后端没连上就自己救一次：重起后端 + 重载页面。
                # 典型场景：复用了别的实例的后端，而那个实例刚好退出把它杀了。
                if recover_backend():
                    log('后端已恢复，重载页面')
                    try:
                        window.evaluate_js('location.reload()')
                    except Exception as exc:
                        log('重载页面失败：%r' % exc)
                else:
                    log('后端恢复失败，界面保持离线（不影响已打开的本地数据）')
        except Exception as exc:
            log('页面自查失败：%r' % exc)

    try:
        window.events.loaded += inspect_page
    except Exception as exc:
        log('挂 loaded 事件失败（不影响使用）：%r' % exc)

    try:
        # 用 Edge/WebView2 内核；debug=False 时右键菜单与 F12 都关掉，更像软件
        webview.start(gui='edgechromium')
    finally:
        log('窗口已关闭')
    # ★ 和 Edge 那条路径保持一致：窗口只是壳，**后端要留下**。
    #   以前这里 finally 里直接 stop_children()，关窗就把 8790 带走 ——
    #   还开着的浏览器标签全部 Failed to fetch，正在跑的生成/导出直接断。
    #   本机 venv 没装 pywebview 所以一直走 Edge 路径看不出问题，
    #   但系统 Python 3.11 装了 webview、打包版也可能带，换解释器就命中。
    log('★ 后端保持运行（页面要靠它）；要停就结束「奇迹工坊.exe」。'
        '再次双击本程序会复用已有后端，不会起第二份。')
    # ★ 没有窗口了，把单实例锁让出来，否则下次双击「什么都没发生」（P2-B）
    release_single_instance()
    while True:
        time.sleep(300)


if __name__ == '__main__':
    try:
        code = main()
    except Exception as exc:
        import traceback
        log('崩了：%r' % exc)
        log(traceback.format_exc())
        code = 1
    sys.exit(code)
