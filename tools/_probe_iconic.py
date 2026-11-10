# -*- coding: utf-8 -*-
r"""启动全过程监控：记录窗口何时出现、位置、有没有被最小化过。

用来验证「界面出来了，然后消失一下又回来」是否还在
（那个现象=启动自检里最小化→恢复了一次）。

用法：python _probe_iconic.py [秒数]
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

EXE = r'C:\Users\q3153\Desktop_new\奇迹工坊.exe'
u = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


def find_window():
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n <= 0:
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if '奇迹工坊' in b.value:
            r = RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 600:
                hits.append(h)
        return True

    u.EnumWindows(cb, 0)
    return hits


# 清理旧实例
for h in find_window():
    pid = wt.DWORD(0)
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    subprocess.run(['taskkill', '/F', '/PID', str(pid.value)], capture_output=True)
    print('清理旧实例 %s' % pid.value)
time.sleep(1.0)

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
t0 = time.time()
subprocess.Popen([EXE])
print('exe 已拉起')

hwnd = None
events = []
last_state = None
while time.time() - t0 < secs:
    ts = time.time() - t0
    if hwnd is None or not u.IsWindow(hwnd):
        hs = find_window()
        if hs:
            hwnd = hs[0]
            events.append((ts, '窗口出现'))
    if hwnd:
        r = RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        iconic = bool(u.IsIconic(hwnd))
        off = r.left < -1000 or r.top < -1000
        state = '最小化' if iconic else ('屏幕外' if off else '正常显示@(%d,%d)' % (r.left, r.top))
        if state != last_state:
            events.append((ts, state))
            last_state = state
    time.sleep(0.04)

print('\n事件时间线：')
for ts, s in events:
    print('  %6.2fs  %s' % (ts, s))

minimized = any('最小化' in s for _, s in events)
print('\n有没有被最小化过：%s' % ('有（这就是"消失一下又回来"）' if minimized else '没有 ✓'))
