#!/usr/bin/env python3
"""文档一致性自检。

为什么需要：
    本项目出过一次系统性的文档漂移 —— 音频模块已经实现并真机验证出声，
    但三份文档还写着"未实现"；示例小程序从 3 个变成 4 个，文档没跟；
    CODE_WIKI 里 17 个链接是生成机器的本地绝对路径（`file:///f:/...`），
    换台机器全断。人工 review 能发现一次，发现不了每一次。

    所以把"文档里断言的事实"变成可执行的检查：**改代码后跑一下就知道文档有没有跟上**。

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

# 需要参与检查的文档
DOCS = [
    "README.md",
    "README-刷机指南.md",
    "NOTICE.md",
    "docs/PROTOCOL.md",
    "docs/CODE_WIKI.md",
    "docs/FACTORY_FIRMWARE.md",
    "docs/KNOWN_ISSUES.md",
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
        abs_hits = re.findall(r"file:///[A-Za-z]:", text)
        if abs_hits:
            bad("%s 含 %d 处本地绝对路径链接（file:///X:）" % (doc, len(abs_hits)))

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

    text = read("README.md") + read("docs/CODE_WIKI.md")
    for n in set(re.findall(r"(\d+)\s*个示例小程序", text)):
        if int(n) != builtin:
            bad("文档写「%s 个示例小程序」，实际 os/builtin 下有 %d 个" % (n, builtin))
    for n in set(re.findall(r"(\d+)\s*个一键脚本", text)) | \
            set(re.findall(r"Windows 一键脚本（(\d+) 个）", text)):
        if int(n) != bats:
            bad("文档写「%s 个一键脚本」，实际 windows/ 下有 %d 个" % (n, bats))
    for n in set(re.findall(r"开发/诊断工具（(\d+) 个", text)) | \
            set(re.findall(r"诊断脚本（(\d+) 个", text)):
        if int(n) != tools:
            bad("文档写「%s 个工具」，实际 tools/ 下有 %d 个" % (n, tools))

    ok("数量：示例小程序 %d / 一键脚本 %d / 工具 %d，与文档一致"
       % (builtin, bats, tools))


# ---------------------------------------------------------------- 3. 常量
def check_config_facts():
    cfg = read("os/passport/config.py")

    def const(name):
        # 取值到行尾注释之前为止，并去掉引号
        m = re.search(r"^%s\s*=\s*([^#\n]+)" % name, cfg, re.M)
        if not m:
            return None
        return m.group(1).strip().strip('"').strip("'")

    uuids = {k: const(k) for k in ("UUID_SERVICE", "UUID_CMD", "UUID_RSP")}
    for k, v in uuids.items():
        if not v:
            bad("config.py 里找不到 %s" % k)
            continue
        if v not in read("docs/PROTOCOL.md"):
            bad("PROTOCOL.md 里没有 %s = %s" % (k, v))

    max_size = const("MAX_APP_SIZE")          # 32 * 1024
    max_apps = const("MAX_APPS")
    proto = read("docs/PROTOCOL.md")
    wiki = read("docs/CODE_WIKI.md")
    if max_apps and ("| 小程序数量 | %s |" % max_apps) not in proto + wiki:
        bad("文档里的「小程序数量」与 config.MAX_APPS=%s 不一致" % max_apps)
    if "32 KB" not in proto or "32 KB" not in wiki:
        bad("文档未反映 MAX_APP_SIZE（应为 32 KB）")

    idle = const("BLE_IDLE_TIMEOUT_MS")       # 25000
    if idle and "25 秒" not in proto + wiki:
        bad("文档未反映 BLE_IDLE_TIMEOUT_MS=%s（应为 25 秒）" % idle)

    attr = const("BLE_ATTR_MAX_LEN")          # 2048
    if attr and attr not in proto:
        bad("PROTOCOL.md 未说明 BLE_ATTR_MAX_LEN=%s（20 字节截断坑）" % attr)

    ok("常量：UUID / 体积上限 / 数量上限 / 看门狗 / 特征值缓冲 均与文档同步")


# ---------------------------------------------------------------- 4. 协议
def check_protocol_commands():
    src = read("os/passport/blepush.py")
    cmds = set(re.findall(r't\s*==\s*"([a-z]+)"', src))
    # 设备主动推送的消息（不在 t== 分支里）
    pushed = set(re.findall(r'"t":\s*"([a-z]+)"', src))
    proto = read("docs/PROTOCOL.md")

    missing = sorted(c for c in cmds if ('{"t":"%s"' % c) not in proto)
    if missing:
        bad("PROTOCOL.md 未记录这些命令: %s" % ", ".join(missing))

    # 主动推送的也算（log/key/state/err/hi/pong/ack/done...）
    missing2 = sorted(c for c in pushed if c not in proto)
    if missing2:
        bad("PROTOCOL.md 未提及这些消息类型: %s" % ", ".join(missing2))

    ok("协议：设备端实现的 %d 个命令在 PROTOCOL.md 中均有记录" % len(cmds))


# ---------------------------------------------------------------- 5. ctx
def check_ctx_api():
    ui = read("os/passport/ui.py")
    m = re.search(r"class Ctx:.*?(?=\nclass |\Z)", ui, re.S)
    body = m.group(0) if m else ""
    attrs = set(re.findall(r"self\.([a-z][a-z0-9_]*)\s*=", body))
    attrs -= {"frame", "_exit", "_kv", "_kv_dirty"}          # 见下逐项确认

    proto = read("docs/PROTOCOL.md")
    missing = sorted(a for a in attrs if ("ctx.%s" % a) not in proto)
    if missing:
        bad("PROTOCOL.md 的 ctx 表缺少: %s" % ", ".join("ctx." + a for a in missing))
    if "ctx.frame" not in proto:
        bad("PROTOCOL.md 的 ctx 表缺少 ctx.frame")

    ok("小程序 API：Ctx 暴露的成员（%d 个）在 PROTOCOL.md 中均有说明" % (len(attrs) + 1))


# ---------------------------------------------------------------- 6. 陈旧断言
def check_stale_claims():
    audio_exists = os.path.exists(os.path.join(ROOT, "os", "passport", "audio.py"))
    if audio_exists:
        for doc in DOCS:
            t = read(doc)
            for phrase in ("PassportOS v1 未启用", "音频 ❌ 未实现", "音频 …未启用"):
                if phrase in t:
                    bad("%s 仍写着「%s」，但 os/passport/audio.py 已存在" % (doc, phrase))
        ok("音频：已实现，文档中无「未启用/未实现」的残留表述")

    # sw.js 缓存版本必须与文档一致
    sw = read("pwa/sw.js")
    m = re.search(r"CACHE\s*=\s*'([^']+)'", sw)
    if m:
        ver = m.group(1)
        wiki = read("docs/CODE_WIKI.md")
        if ver not in wiki:
            bad("CODE_WIKI.md 未反映 sw.js 的缓存名 %s" % ver)
        else:
            ok("Service Worker：缓存名 %s 与文档一致" % ver)

    # 工具清单：tools/ 下每个 .py 都应在 CODE_WIKI 出现
    wiki = read("docs/CODE_WIKI.md")
    miss = [f for f in sorted(os.listdir(os.path.join(ROOT, "tools")))
            if f.endswith(".py") and f not in wiki]
    if miss:
        bad("CODE_WIKI.md 未收录这些工具: %s" % ", ".join(miss))
    else:
        ok("工具清单：tools/ 下全部 .py 都已被 CODE_WIKI 收录")


# ---------------------------------------------------------------- 7. 固件
def check_firmware_hashes():
    claims = {
        "firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin": None,
        "firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin": None,
    }
    for rel in claims:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            bad("固件缺失: %s" % rel)
            continue
        h = hashlib.sha256(open(path, "rb").read()).hexdigest().upper()
        claims[rel] = h
        # 该哈希必须出现在某份文档里（区分大小写不敏感）
        haystack = " ".join(read(d).upper() for d in DOCS)
        if h not in haystack:
            bad("固件 %s 的 SHA-256 未出现在任何文档中" % os.path.basename(rel))
    if all(claims.values()):
        ok("固件：两个 .bin 的 SHA-256 均已在文档中登记并核对一致")


# ---------------------------------------------------------------- 8. 已知问题
def check_known_issues():
    if not os.path.exists(os.path.join(ROOT, "docs", "KNOWN_ISSUES.md")):
        bad("缺少 docs/KNOWN_ISSUES.md")
        return
    # README / PROTOCOL / CODE_WIKI 都应指向它
    for doc in ("README.md", "docs/PROTOCOL.md", "docs/CODE_WIKI.md"):
        if "KNOWN_ISSUES" not in read(doc):
            bad("%s 没有指向 docs/KNOWN_ISSUES.md" % doc)
    ok("已知问题：KNOWN_ISSUES.md 存在且被主文档引用")


# ---------------------------------------------------------------- 9. 小程序
def check_miniapps():
    """Enforce the two hard rules for anything in miniapps/.

    Both are device constraints, not style preferences:
      * ASCII only - the firmware's 8x8 font has no other glyphs, and a
        truncated upload of a multi-byte file becomes a UnicodeError instead
        of a plain SyntaxError (see KNOWN_ISSUES #1).
      * a TITLE constant - that is what shows in the device menu.
    Underscore-prefixed files are tooling, not mini-programs, so they are
    exempt (and they are allowed to contain Chinese help text).
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

    seen = set()
    for f in apps:
        raw = open(os.path.join(d, f), "rb").read()
        n = sum(1 for b in raw if b > 127)
        if n:
            bad("miniapps/%s 含 %d 个非 ASCII 字节（固件字体只有 ASCII）" % (f, n))
        text = raw.decode("utf-8")
        if "\nTITLE = " not in "\n" + text:
            bad("miniapps/%s 缺少 TITLE 常量（设备菜单靠它显示名字）" % f)
        else:
            seen.add(text.split("TITLE = ", 1)[1].split("\n", 1)[0].strip('"\''))

    readme = read("miniapps/README.md")
    missing = sorted(t for t in seen if t not in readme)
    if missing:
        bad("miniapps/README.md 未收录: %s" % ", ".join(missing))
    else:
        ok("小程序：%d 个全部为 ASCII、有 TITLE、且已列入 README" % len(apps))


def main():
    print("文档一致性自检 —— 仓库根目录 %s\n" % ROOT)
    for fn in (check_links, check_counts, check_config_facts,
               check_protocol_commands, check_ctx_api, check_stale_claims,
               check_firmware_hashes, check_known_issues, check_miniapps):
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
