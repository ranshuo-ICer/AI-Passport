#!/usr/bin/env python3
"""在电脑上本地托管「Passport 助手」App。

Web Bluetooth 要求安全上下文（https 或 localhost），所以不能直接双击 index.html。
本脚本把 pwa/ 目录挂在 http://127.0.0.1:8790/，然后用默认浏览器打开。

用法：
    python tools/serve.py            # 默认 8790
    python tools/serve.py 9000
"""

import http.server
import os
import socketserver
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PWA_DIR = os.path.join(os.path.dirname(HERE), "pwa")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PWA_DIR, **kw)

    def end_headers(self):
        # 开发期别让浏览器缓存住旧文件
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    if not os.path.isdir(PWA_DIR):
        print("找不到 pwa 目录: %s" % PWA_DIR)
        return 1
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        url = "http://127.0.0.1:%d/" % port
        print("Passport 助手 App 已启动： %s" % url)
        print("（保持本窗口开着；按 Ctrl+C 停止）")
        print("注意：必须用 Chrome 或 Edge，且 Web Bluetooth 只在 localhost/https 下可用。")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
