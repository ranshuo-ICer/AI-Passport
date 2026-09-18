#!/usr/bin/env python3
"""复现：上传小程序期间，手机端心跳被写进 app.py（KNOWN_ISSUES #1）。

背景
----
手机端每 10 秒发一次 `{"t":"ping"}` 给设备的 25 秒空闲看门狗续命。
上传期间心跳**没有停**，而设备进入数据模式后只把
`abort` / `end` / `put` / `stop` 识别为控制命令（`blepush._CTRL_DURING_UPLOAD`），
其余写入一律当源码追加。于是心跳这 12 字节：

  1. 被原样写进 `/apps/<n>/app.py`，插进某行代码中间
  2. 同时计入 `got`，让设备**提前 12 字节**认为收满 → 末尾 12 字节真实源码被丢弃

而设备照常回 `{"t":"done"}` —— 手机显示"推送完成"，装上去却跑不起来。

本脚本做什么
------------
用**生产代码本身**（`passport.blepush.AppLink`）走真实的
`_irq()` → `poll()` 路径，只把 `bluetooth` / `machine` 换成桩。
不碰硬件、不连蓝牙、不改仓库。

退出码语义（当成回归测试用）
----------------------------
    1  = 复现成功，缺陷仍在          ← 修复前应当是红的
    0  = 无法复现，缺陷已修          ← 修复后应当是绿的

用法
----
    python tools/repro_ping_corruption.py

⚠ 本脚本只检验**设备端**是否对"上传期间的非控制命令"免疫。
App 侧另需在上传期间 `stopHeartbeat()`；两者是互补的双层防御 ——
只修 App 的话这个脚本仍然会红，这是刻意的：设备端不该依赖客户端守规矩。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))
sys.path.insert(0, HERE)

import test_protocol as TP          # 复用现成的桩（bluetooth / machine / time.ticks_*）

PING = b'{"t":"ping"}'

# 一份结构清晰的小程序源码，末尾放个哨兵便于判断有没有被截断
SOURCE = (
    b"# mini program\n"
    + b"x = 1  # padding padding padding\n" * 12
    + b"TAIL_SENTINEL_ABCDEF\n"
)


class _Host:
    """AppLink 需要的宿主接口（真实实现是 passport.ui.Shell）。"""

    app_list = []

    def launch(self, name):
        return True

    def stop_app(self):
        pass

    def current_app(self):
        return None

    def refresh_apps(self):
        pass


def _ok(msg):
    print("  [ OK ] %s" % msg)


def _bad(msg):
    print("  [FAIL] %s" % msg)


def main():
    print("=" * 66)
    print("复现：上传期间心跳污染 app.py   （KNOWN_ISSUES #1）")
    print("=" * 66)

    TP.install_stubs()               # 把 MicroPython 的 bluetooth/machine/time 顶上

    tmp = tempfile.mkdtemp(prefix="passport-repro-")
    try:
        from passport import config as C
        from passport import apps as apps_mod
        C.APPS_DIR = os.path.join(tmp, "apps")
        apps_mod.C.APPS_DIR = C.APPS_DIR

        from passport.blepush import AppLink

        link = AppLink(_Host(), log=lambda m: None)
        link.start()
        ble = link.ble
        ble._irq(1, (1, 0, b""))     # 模拟手机连上，否则设备不回通知

        def phone_write(payload):
            """模拟手机往 CMD 特征写一段数据，并让设备主循环消费掉。"""
            ble._last = payload
            ble._irq(3, (1, 10))     # _IRQ_GATTS_WRITE
            link.poll()

        total = len(SOURCE)
        print("\n原始源码: %d 字节" % total)
        print("  末尾哨兵: %r" % SOURCE[-22:])
        print("  声明长度: %d" % total)

        print("\n[1] put 命令，进入数据模式")
        phone_write(json.dumps(
            {"t": "put", "n": "demo", "s": total, "title": "Demo"}).encode())

        half = total // 2
        print("\n[2] 发送前半段 (%d 字节)" % half)
        phone_write(SOURCE[:half])

        print("\n[3] ★ 模拟上传超过 10 秒、手机心跳到期")
        print("      写入: %r  (%d 字节)" % (PING, len(PING)))
        phone_write(PING)

        print("\n[4] 继续发送后半段 (%d 字节)" % (total - half))
        phone_write(SOURCE[half:])

        written = open(os.path.join(C.APPS_DIR, "demo", "app.py"), "rb").read()

        # 设备回了什么？—— 这决定了它有没有"报警"
        replies = []
        for frame in TP.reassemble(ble.sent):
            try:
                replies.append(json.loads(frame))
            except ValueError:
                pass
        types = [r.get("t") for r in replies]
        said_done = "done" in types

        print("\n" + "-" * 66)
        print("设备回复序列: %s" % (types or "(无)"))
        print("落盘大小: %d 字节 (声明 %d)" % (len(written), total))

        corrupted = written != SOURCE
        injected_at = written.find(PING) if corrupted else -1

        print("-" * 66)

        if not corrupted:
            _ok("落盘内容与原始源码完全一致 —— 缺陷已修")
            print("\n结论: 无法复现。设备端已能正确忽略上传期间的非控制命令。")
            return 0

        # ---- 复现成功，把证据摆全 ----
        _bad("落盘内容与原始源码不一致")
        if injected_at >= 0:
            _bad("心跳被写进了源码，位置第 %d 字节" % injected_at)
            print("         上下文: %r" % written[max(0, injected_at - 18):injected_at + 30])
        _bad("末尾哨兵完整: %s" % written.endswith(b"TAIL_SENTINEL_ABCDEF\n"))

        lost = [i for i in range(min(len(written), total))
                if i >= injected_at >= 0 and written[i:i + len(PING)] == PING]
        if lost:
            _bad("原始源码末尾 %d 字节被丢弃" % len(PING))

        try:
            compile(written, "app.py", "exec")
            print("\n  语法检查: 通过（这次没被破坏到语法层面）")
        except SyntaxError as exc:
            _bad("语法检查: SyntaxError: %s (第 %s 行)" % (exc.msg, exc.lineno))

        if said_done:
            _bad("**设备回了 done（成功）**，客户端会显示\"推送完成\" —— 静默损坏")
        print("-" * 66)
        print("\n结论: 复现成功，KNOWN_ISSUES #1 仍然存在。")
        print("      修法见 docs/KNOWN_ISSUES.md #1（设备端把 ping 补进")
        print("      _CTRL_DURING_UPLOAD；App 侧上传期间 stopHeartbeat）。")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
