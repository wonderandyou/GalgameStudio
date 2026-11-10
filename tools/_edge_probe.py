"""对照实验：从 Python 进程里用 Popen 拉起 Edge app 窗口，看会不会白屏。

如果这个也白屏，说明问题跟 PyInstaller 无关，是「从 Python 父进程起 Edge」本身的问题；
如果这个正常，那问题就在 exe 的打包环境里。

跑：python tools\\_edge_probe.py
"""
import os
import subprocess
import sys
import tempfile
import time

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
URL = 'http://127.0.0.1:8790/'


def windows():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq msedge.exe', '/FO', 'CSV'],
                         capture_output=True, text=True).stdout
    return out.count('msedge.exe') - 1


def main():
    if not os.path.isfile(EDGE):
        print('没装 Edge')
        return 1
    profile = os.path.join(tempfile.gettempdir(), 'gal-pytest')
    print(f'profile={profile}')
    proc = subprocess.Popen([EDGE, '--app=' + URL, '--window-size=1200,800',
                             '--user-data-dir=' + profile, '--no-first-run'])
    print(f'拉起 pid={proc.pid}')
    time.sleep(10)
    # 用 pymsg 无关，直接查窗口标题需要 Win32 —— 简单点：进程还活着就认为窗口开着
    print(f'10 秒后进程存活={proc.poll() is None}')
    print('请人工确认窗口标题是「奇迹工坊」还是 `127.0.0.1_/`')
    return 0


if __name__ == '__main__':
    sys.exit(main())
