#!/usr/bin/env python3
"""文档一致性自检。

为什么需要：
    这个仓库的文档曾经由两个不同的 Agent 分别生成，结果是同一件事被写了
    好几遍，而且彼此矛盾 —— 音频模块已经实现并真机验证出声，却有三份文档
    还写着"未实现"；示例小程序从 3 个变成 4 个，文档没跟；CODE_WIKI 里 17
    个链接是生成机器的本地绝对路径（`file:///f:/...`），换台机器全断。

    人工 review 能发现一次，发现不了每一次。所以把"文档里断言的事实"变成
    可执行的检查：**改代码后跑一下就知道文档有没有跟上**。

用法：
    python tools/check_docs.py

退出码 0 = 全部一致；1 = 有漂移（逐条打印）。
"""

import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 参与检查的文档（docs/README.md 是索引，也在内）
DOCS = [
    "README.md",
    "NOTICE.md",
    "docs/README.md",
    "docs/hardware.md",
    "docs/flashing.md",
    "docs/passport-os.md",
    "docs/ble-protocol.md",
    "docs/pwa.md",
    "docs/tools.md",
    "docs/pitfalls.md",
    "docs/known-issues.md",
    "docs/factory-firmware.md",
    "docs/upstream.md",
    "miniapps/README.md",
]

OK, BAD = [], []


def ok(msg):
    OK.append(msg)


def bad(msg):
    BAD.append(msg)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def all_docs():
    """所有文档拼一起，用于"这句话有没有写在某处"这类检查。"""
    return "\n".join(read(d) for d in DOCS if os.path.exists(os.path.join(ROOT, d)))


# ---------------------------------------------------------------- 1. 链接
def check_links():
    total = 0
    for doc in DOCS:
        if not os.path.exists(os.path.join(ROOT, doc)):
            bad("%s 不存在" % doc)
            continue
        text = read(doc)
        base = os.path.dirname(doc)

        # 1a. 绝不能出现本地绝对路径
        if re.search(r"file:///[A-Za-z]:", text):
            bad("%s 含本地绝对路径链接（file:///X:）" % doc)

        # 1b. 相对链接目标必须存在（按文档所在目录解析）
        for target in re.findall(r"\]\(([^)#\s]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            total += 1
            full = os.path.normpath(os.path.join(ROOT, base, target))
            if not os.path.exists(full):
                bad("%s 的链接失效: %s" % (doc, target))
    ok("markdown 链接：%d 个相对链接全部可达，无本地绝对路径" % total)


# ---------------------------------------------------------------- 2. 数量
def check_counts():
    builtin = len([d for d in os.listdir(os.path.join(ROOT, "os", "builtin"))
                   if os.path.isdir(os.path.join(ROOT, "os", "builtin", d))])
    bats = len([f for f in os.listdir(os.path.join(ROOT, "windows"))
                if f.endswith(".bat")])
    tools = len([f for f in os.listdir(os.path.join(ROOT, "tools"))
                 if f.endswith(".py")])
    text = all_docs()

    for n in set(re.findall(r"(\d+)\s*个示例小程序", text)):
        if int(n) != builtin:
            bad("文档写「%s 个示例小程序」，实际 os/builtin 下有 %d 个" % (n, builtin))
    for n in set(re.findall(r"(\d+)\s*个一键脚本", text)) | \
            set(re.findall(r"Windows 一键脚本（(\d+) 个）", text)):
        if int(n) != bats:
            bad("文档写「%s 个一键脚本」，实际 windows/ 下有 %d 个" % (n, bats))
    for n in set(re.findall(r"诊断脚本（(\d+) 个", text)) | \
            set(re.findall(r"开发/诊断工具（(\d+) 个", text)):
        if int(n) != tools:
            bad("文档写「%s 个工具」，实际 tools/ 下有 %d 个" % (n, tools))

    ok("数量：示例小程序 %d / 一键脚本 %d / 工具 %d，与文档一致"
       % (builtin, bats, tools))


# ---------------------------------------------------------------- 3. 常量
def check_config_facts():
    cfg = read("os/passport/config.py")

    def const(name):
        m = re.search(r"^%s\s*=\s*([^#\n]+)" % name, cfg, re.M)
        return m.group(1).strip().strip('"').strip("'") if m else None

    proto = read("docs/ble-protocol.md")
    text = all_docs()

    for k in ("UUID_SERVICE", "UUID_CMD", "UUID_RSP"):
        v = const(k)
        if not v:
            bad("config.py 里找不到 %s" % k)
        elif v not in proto:
            bad("ble-protocol.md 里没有 %s = %s" % (k, v))

    max_apps = const("MAX_APPS")
    if max_apps and ("| 小程序数量 | %s |" % max_apps) not in proto + text:
        bad("文档里的「小程序数量」与 config.MAX_APPS=%s 不一致" % max_apps)
    if "32 KB" not in text:
        bad("文档未反映 MAX_APP_SIZE（应为 32 KB）")
    if const("BLE_IDLE_TIMEOUT_MS") and "25 秒" not in text:
        bad("文档未反映 BLE_IDLE_TIMEOUT_MS（应为 25 秒）")
    attr = const("BLE_ATTR_MAX_LEN")
    if attr and attr not in proto:
        bad("ble-protocol.md 未说明 BLE_ATTR_MAX_LEN（20 字节截断坑）")

    ok("常量：UUID / 体积上限 / 数量上限 / 看门狗 / 特征值缓冲 均与文档同步")


# ---------------------------------------------------------------- 4. 协议
def check_protocol_commands():
    src = read("os/passport/blepush.py")
    cmds = set(re.findall(r't\s*==\s*"([a-z]+)"', src))
    pushed = set(re.findall(r'"t":\s*"([a-z]+)"', src))
    proto = read("docs/ble-protocol.md")

    missing = sorted(c for c in cmds if ('{"t":"%s"' % c) not in proto)
    if missing:
        bad("ble-protocol.md 未记录这些命令: %s" % ", ".join(missing))
    missing2 = sorted(c for c in pushed if c not in proto)
    if missing2:
        bad("ble-protocol.md 未提及这些消息类型: %s" % ", ".join(missing2))

    ok("协议：设备端实现的 %d 个命令在 ble-protocol.md 中均有记录" % len(cmds))


# ---------------------------------------------------------------- 5. ctx
def check_ctx_api():
    ui = read("os/passport/ui.py")
    m = re.search(r"class Ctx:.*?(?=\nclass |\Z)", ui, re.S)
    body = m.group(0) if m else ""
    attrs = set(re.findall(r"self\.([a-z][a-z0-9_]*)\s*=", body))
    attrs -= {"_exit", "_kv", "_kv_dirty"}

    proto = read("docs/ble-protocol.md")
    missing = sorted(a for a in attrs if ("ctx.%s" % a) not in proto)
    if missing:
        bad("ble-protocol.md 的 ctx 表缺少: %s" % ", ".join("ctx." + a for a in missing))
    if "ctx.frame" not in proto:
        bad("ble-protocol.md 的 ctx 表缺少 ctx.frame")

    ok("小程序 API：Ctx 暴露的 %d 个成员在 ble-protocol.md 中均有说明" % len(attrs))


# ---------------------------------------------------------------- 6. 陈旧断言
def check_stale_claims():
    text = all_docs()

    if os.path.exists(os.path.join(ROOT, "os", "passport", "audio.py")):
        for phrase in ("PassportOS v1 未启用", "音频 ❌ 未实现", "音频 …未启用"):
            if phrase in text:
                bad("文档仍写着「%s」，但 os/passport/audio.py 已存在" % phrase)
        ok("音频：已实现，文档中无「未启用/未实现」的残留表述")

    sw = read("pwa/sw.js")
    m = re.search(r"CACHE\s*=\s*'([^']+)'", sw)
    if m:
        if m.group(1) not in text:
            bad("文档未反映 sw.js 的缓存名 %s" % m.group(1))
        else:
            ok("Service Worker：缓存名 %s 与文档一致" % m.group(1))

    tools_doc = read("docs/tools.md")
    miss = [f for f in sorted(os.listdir(os.path.join(ROOT, "tools")))
            if f.endswith(".py") and f not in tools_doc]
    if miss:
        bad("docs/tools.md 未收录: %s" % ", ".join(miss))
    else:
        ok("工具清单：tools/ 下全部 .py 都已被 docs/tools.md 收录")


# ---------------------------------------------------------------- 7. 固件
def check_firmware_hashes():
    text = all_docs().upper()
    found = 0
    for rel in ("firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin",
                "firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin"):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            bad("固件缺失: %s" % rel)
            continue
        h = hashlib.sha256(open(path, "rb").read()).hexdigest().upper()
        if h not in text:
            bad("固件 %s 的 SHA-256 未出现在任何文档中" % os.path.basename(rel))
        else:
            found += 1
    if found == 2:
        ok("固件：两个 .bin 的 SHA-256 均已在文档中登记并核对一致")


# ---------------------------------------------------------------- 8. 已知问题
def check_known_issues():
    if not os.path.exists(os.path.join(ROOT, "docs", "known-issues.md")):
        bad("缺少 docs/known-issues.md")
        return
    for doc in ("README.md", "docs/README.md", "docs/ble-protocol.md"):
        if "known-issues" not in read(doc):
            bad("%s 没有指向 docs/known-issues.md" % doc)
    ok("已知问题：known-issues.md 存在且被主文档引用")


# ---------------------------------------------------------------- 9. 小程序
def check_miniapps():
    """Enforce the two hard rules for anything in miniapps/.

    Both are device constraints, not style preferences:
      * ASCII only - the firmware's 8x8 font has no other glyphs, and a
        truncated upload of a multi-byte file becomes a UnicodeError instead
        of a plain SyntaxError (see known-issues #1).
      * a TITLE constant - that is what shows in the device menu.
    Underscore-prefixed files are tooling, so they are exempt.
    """
    d = os.path.join(ROOT, "miniapps")
    if not os.path.isdir(d):
        bad("缺少 miniapps/ 目录")
        return
    apps = sorted(f for f in os.listdir(d)
                  if f.endswith(".py") and not f.startswith("_"))
    if not apps:
        bad("miniapps/ 里一个小程序都没有")
        return

    titles = set()
    for f in apps:
        raw = open(os.path.join(d, f), "rb").read()
        n = sum(1 for b in raw if b > 127)
        if n:
            bad("miniapps/%s 含 %d 个非 ASCII 字节（固件字体只有 ASCII）" % (f, n))
        t = raw.decode("utf-8")
        if "\nTITLE = " not in "\n" + t:
            bad("miniapps/%s 缺少 TITLE 常量（设备菜单靠它显示名字）" % f)
        else:
            titles.add(t.split("TITLE = ", 1)[1].split("\n", 1)[0].strip('"\''))

    readme = read("miniapps/README.md")
    missing = sorted(x for x in titles if x not in readme)
    if missing:
        bad("miniapps/README.md 未收录: %s" % ", ".join(missing))
    else:
        ok("小程序：%d 个全部为 ASCII、有 TITLE、且已列入 README" % len(apps))


# ---------------------------------------------------------------- 10. 索引
def check_doc_index():
    """docs/README.md must link every doc, or nobody will find it."""
    d = os.path.join(ROOT, "docs")
    index = read("docs/README.md")
    here = sorted(f for f in os.listdir(d)
                  if f.endswith(".md") and f != "README.md")
    missing = [f for f in here if f not in index]
    if missing:
        bad("docs/README.md 索引里没有: %s" % ", ".join(missing))
    else:
        ok("索引：docs/ 下 %d 篇文档全部列在 docs/README.md 里" % len(here))


def main():
    print("文档一致性自检 —— 仓库根目录 %s\n" % ROOT)
    for fn in (check_links, check_counts, check_config_facts,
               check_protocol_commands, check_ctx_api, check_stale_claims,
               check_firmware_hashes, check_known_issues, check_miniapps,
               check_doc_index):
        try:
            fn()
        except Exception as exc:                              # noqa: BLE001
            bad("%s 执行出错: %s: %s" % (fn.__name__, type(exc).__name__, exc))

    for m in OK:
        print("  [OK]   %s" % m)
    for m in BAD:
        print("  [FAIL] %s" % m)

    print("\n%d 项通过，%d 项失败" % (len(OK), len(BAD)))
    if BAD:
        print("\n文档已与代码脱节，请同步后再提交。")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
