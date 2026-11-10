"""把 Unity 那些「不可见但存在」的对话框强制显示到屏幕中央。

背景：这台机器上 Unity 的模态对话框（License error / Opening project…）
会创建出来但 IsWindowVisible=False，用户看不到，等一会儿进程就自己退了。
这里按 pid 枚举它的所有窗口，对不可见的 ShowWindow + 搬到屏幕中间，
好在真机上点"Sign in"。

跑：python tools\\_force_show_windows.py <pid> [--shot 输出.png]
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
SW_SHOW = 5
SW_RESTORE = 9


def enum_windows(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ''
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append(dict(hwnd=hwnd, title=title, cls=cls.value,
                          visible=bool(user32.IsWindowVisible(hwnd)),
                          rect=(rect.left, rect.top,
                                rect.right - rect.left, rect.bottom - rect.top)))
        return True

    user32.EnumWindows(callback, 0)
    return found


def main(pid):
    pid = int(pid)
    windows = enum_windows(pid)
    print(f'pid={pid} 共 {len(windows)} 个窗口')
    shown = 0
    for item in windows:
        x, y, w, h = item['rect']
        print(f"  visible={item['visible']} ({x},{y}) {w}x{h} class={item['cls']!r} title={item['title']!r}")
        # 只要看起来像对话框的（有标题、尺寸合理）
        if item['title'] and w >= 200 and h >= 100:
            hwnd = item['hwnd']
            if not item['visible']:
                user32.ShowWindow(hwnd, SW_SHOW)
                shown += 1
            user32.ShowWindow(hwnd, SW_RESTORE)
            # 搬到屏幕中央偏上
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            nx = max(0, (screen_w - w) // 2)
            ny = max(0, (screen_h - h) // 3)
            user32.SetWindowPos(hwnd, None, nx, ny, w, h, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE
            user32.SetForegroundWindow(hwnd)
            print(f'     → 已显示并移到 ({nx},{ny})')
    print(f'强制显示 {shown} 个')
    if shown:
        time.sleep(1)
        print('现在再看屏幕，对话框应该出来了')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
