"""PWA 静态一致性检查。

检查项（任何一项失败都让退出码非 0，供 CI / 提交前门禁使用）：
  1. app.js 里 $('...') 引用的 DOM id 在 index.html 里存在
  2. manifest.webmanifest 里列出的图标文件真实存在
  3. app.js 里用到的静态资源（style.css / sw.js 等）存在
  4. sw.js 的缓存版本号是 passport-pwa-vN 形式

路径统一以【本脚本所在位置】为基准推导，所以在任何 cwd 下执行都行
（以前是 cwd 相对路径，换个目录跑就是 FileNotFoundError）。
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PWA = os.path.join(ROOT, "pwa")

fails = []


def check(name, cond, extra=""):
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("   " + extra) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def read(rel):
    path = os.path.join(PWA, rel)
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    html = read("index.html")
    js = read("app.js")
    sw = read("sw.js")

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r"\$\('([^']+)'\)", js))

    missing = sorted(js_ids - html_ids)
    check("JS 引用的 DOM id 都在 HTML 里", not missing,
          "缺失: %s" % ", ".join(missing))
    check("「换设备」按钮 btnPick 就位", "btnPick" in html_ids)

    # manifest 图标必须真实存在，且缺失要让退出码非 0
    man = json.loads(read("manifest.webmanifest"))
    icons = man.get("icons", [])
    check("manifest 至少有一个图标", bool(icons))
    for icon in icons:
        p = os.path.join(PWA, icon["src"])
        check("图标存在: %s" % icon["src"], os.path.isfile(p))

    # index.html 里引用的本地资源
    for ref in re.findall(r'(?:href|src)="([^"#:?]+)"', html):
        if ref.startswith("http") or ref.startswith("//"):
            continue
        check("index.html 引用的资源存在: %s" % ref,
              os.path.isfile(os.path.join(PWA, ref)))

    # Service Worker 缓存版本（改了前端资源必须升版本，否则浏览器一直用旧缓存）
    m = re.search(r"CACHE\s*=\s*'passport-pwa-v(\d+)'", sw)
    check("sw.js 缓存版本是 passport-pwa-vN 形式", bool(m),
          "未匹配到 CACHE 常量")

    print("\n%s（%d 项失败）" % ("全部通过" if not fails else "存在问题",
                                 len(fails)))
    if fails:
        for f in fails:
            print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
