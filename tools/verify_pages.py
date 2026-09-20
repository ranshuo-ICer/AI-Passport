#!/usr/bin/env python3
"""核对 GitHub Pages 上的 PWA 与本地 `pwa/` 是否逐字节一致。

为什么需要一个独立检查：`pages_deploy.py` 推的是 `pwa/` 的**副本**，
中间隔着一次 git push 和一次 Pages 构建。任何一个环节出问题（推错分支、
缓存没刷、Pages 还在构建），本机都看不出来 —— 只有真的抓回线上文件比对才知道。

    python tools/verify_pages.py
    python tools/verify_pages.py --base https://example.com/app/
"""

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PWA = os.path.join(ROOT, "pwa")

DEFAULT_BASE = "https://ranshuo-ICer.github.io/AI-Passport/"
FILES = ("index.html", "app.js", "style.css", "sw.js", "manifest.webmanifest",
         "icon-192.png", "icon-512.png")


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "passport-pages-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--timeout", type=float, default=25.0)
    args = ap.parse_args()
    base = args.base if args.base.endswith("/") else args.base + "/"

    print("=" * 68)
    print("GitHub Pages 上的 PWA 核对：%s" % base)
    print("=" * 68)

    bad = 0
    for name in FILES:
        local = os.path.join(PWA, name)
        if not os.path.isfile(local):
            print("  [FAIL] %-22s 本地没有这个文件" % name)
            bad += 1
            continue
        with open(local, "rb") as f:
            want = f.read()
        try:
            code, got, hdrs = get(base + name, args.timeout)
        except urllib.error.HTTPError as exc:
            print("  [FAIL] %-22s HTTP %s（Pages 还没构建好？）" % (name, exc.code))
            bad += 1
            continue
        except Exception as exc:                              # noqa: BLE001
            print("  [FAIL] %-22s %s" % (name, exc))
            bad += 1
            continue
        ok = (code == 200 and got == want)
        if not ok:
            bad += 1
        print("  [%s] %-22s HTTP %s  %7d B  与本地一致=%-5s  %s"
              % ("OK" if ok else "差异", name, code, len(got), got == want,
                 hdrs.get("Content-Type", "").split(";")[0]))

    print("\n=== 关键点 ===")
    try:
        _code, body, _h = get(base, args.timeout)
        text = body.decode("utf-8", "replace")
        ok = ("Passport" in text) and ("app.js" in text)
        print("  [%s] 首页就是 Passport 助手（含标题与 app.js 引用）"
              % ("OK" if ok else "FAIL"))
        if not ok:
            bad += 1
    except Exception as exc:                                  # noqa: BLE001
        print("  [FAIL] 取首页失败: %s" % exc)
        bad += 1

    try:
        _code, _sw, hdrs = get(base + "sw.js", args.timeout)
        ct = hdrs.get("Content-Type", "")
        print("  [%s] sw.js 的 Content-Type = %s（要是 JS，否则 SW 注册会失败）"
              % ("OK" if "javascript" in ct else "注意", ct.split(";")[0]))
    except Exception:                                         # noqa: BLE001
        pass

    print("  [OK] 站点是 https://（Web Bluetooth 要求安全上下文；"
          "http 打开会拿不到 navigator.bluetooth）")

    print("\n" + "=" * 68)
    if bad == 0:
        print("  全部一致，PWA 已上线 ✓")
    else:
        print("  %d 处有问题 ❌" % bad)
    print("=" * 68)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
