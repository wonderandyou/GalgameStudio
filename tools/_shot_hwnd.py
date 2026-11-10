"""截指定窗口（按 pid），不靠抢焦点。

为什么要 PrintWindow 而不是全屏截图：
全屏截图会被别的窗口挡住（实测截到的是主人的 QQ），而且 SetForegroundWindow
不总是成功。PrintWindow 直接让目标窗口把自己画到内存里，挡不挡都截得到。

跑：python tools\\_shot_hwnd.py <pid> <输出路径>
"""
import ctypes
from ctypes import wintypes
import sys

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002


def main(target, out):
    keyword = None if str(target).isdigit() else str(target)
    pid_wanted = int(target) if keyword is None else 0
    matches = []

    def callback(h, _):
        if not user32.IsWindowVisible(h):
            return True
        length = user32.GetWindowTextLengthW(h)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(h, buffer, length + 1)
        title = buffer.value
        if keyword is not None:
            hit = keyword in title
        else:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
            hit = owner.value == pid_wanted
        if hit:
            rect = wintypes.RECT()
            user32.GetWindowRect(wintypes.HWND(h), ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            matches.append((area, h, title))
        return True

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
    user32.EnumWindows(proc, 0)
    if not matches:
        print('没找到窗口')
        return 1
    # 同一个标题会命中任务栏按钮（只有 158x26）和真正的主窗口，取面积最大的。
    matches.sort(reverse=True)
    for area, _, title in matches:
        print(f'候选：{area:>9} px²  {title!r}')
    _, hwnd, _ = matches[0]

    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        print(f'窗口尺寸异常 {width}x{height}')
        return 1

    hdc = user32.GetWindowDC(wintypes.HWND(hwnd))
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    ok = user32.PrintWindow(wintypes.HWND(hwnd), memdc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height          # 负数 = 自上而下，否则图是倒的
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    # 用 PIL 落盘（系统 Python 里没有，退回手写 BMP）
    try:
        from PIL import Image
        image = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1)
        image.convert('RGB').save(out)
    except ImportError:
        row = width * 4
        padding = (4 - (row % 4)) % 4
        with open(out, 'wb') as handle:
            size = 14 + 40 + (row + padding) * height
            handle.write(b'BM' + size.to_bytes(4, 'little') + b'\0\0\0\0' + (54).to_bytes(4, 'little'))
            handle.write((40).to_bytes(4, 'little') + width.to_bytes(4, 'little', signed=True)
                         + (-height).to_bytes(4, 'little', signed=True) + (1).to_bytes(2, 'little')
                         + (32).to_bytes(2, 'little') + (0).to_bytes(4, 'little')
                         + (row * height).to_bytes(4, 'little') + (2835).to_bytes(4, 'little') * 2
                         + (0).to_bytes(4, 'little') * 2)
            handle.write(buffer.raw[:row * height])

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(wintypes.HWND(hwnd), hdc)
    print(f'{"PrintWindow成功" if ok else "PrintWindow返回0"} hwnd={hwnd} {width}x{height} -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
