# -*- coding: utf-8 -*-
r"""静态界面服务：多线程 + 长缓存。

为什么不用 `python -m http.server`：

1. **它是单线程的**。浏览器并行请求（litegraph.js 就 1 MB，加 5 个 js + 3 个 css 约 1.3 MB）
   被串行处理，实测页面 loaded 要 1.2 秒；ThreadingHTTPServer 并发之后明显快。
2. **它不发 Cache-Control**，配合 index.html 里的 `?v=` 内容指纹就没法做长缓存，
   于是每次启动都重新下载这 1.3 MB。

用法：python ui_server.py <端口> <静态目录>
"""
import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    server_version = 'GalUI/1.0'

    def end_headers(self):
        # index.html 自己必须每次校验：它的内容（含 ?v= 指纹）会变，
        # 而且它带着 ?mode=real&base=... 这种固定查询串，一旦被长缓存就永远拿旧的。
        bare = self.path.split('?', 1)[0]
        if bare.endswith('/') or bare.endswith('index.html') or bare == '':
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        else:
            # 其它静态资源都带 ?v=<内容指纹>，文件不变指纹不变 → 安心长缓存
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass          # 别把每个请求都灌进控制台


def main():
    if len(sys.argv) < 3:
        print('用法：ui_server.py <端口> <静态目录>', file=sys.stderr)
        return 2
    port = int(sys.argv[1])
    root = os.path.abspath(sys.argv[2])
    if not os.path.isdir(root):
        print('目录不存在：%s' % root, file=sys.stderr)
        return 2

    handler = partial(Handler, directory=root)
    srv = ThreadingHTTPServer(('127.0.0.1', port), handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
