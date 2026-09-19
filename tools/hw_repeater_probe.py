"""验证 KNOWN_ISSUES #25：repeater 录音流程会不会把共享音频弄死。

机制（见 miniapps/repeater.py）：
    录音前 `_release(ctx)` 会 `deinit()` 掉**共享的** shell.audio（它的 `.ok`
    从此为 False），然后 `_mk_buf()` 尽可能吃满堆当录音缓冲；退出时
    `teardown()` 再 `Audio()` 造一个新实例塞回 ctx.shell.audio。
    一旦那次重建失败，shell.audio 仍是那个死实例 —— 全系统没声音，只能重启。

本脚本用**真实的 repeater 函数**跑这条路径（不是自己重写一遍），
每轮报告重建是否成功以及堆状态。

    python -m mpremote connect COM3 run tools/hw_repeater_probe.py
"""

import gc

import passport.apps as apps
from passport.audio import Audio

print("=" * 68)
print("验证 #25：repeater 录音 -> 退出 -> 共享 Audio 重建")
print("=" * 68)

ns = apps.load_module("repeater")        # 返回的是命名空间 dict，不是模块对象
_release = ns["_release"]
_mk_rx = ns["_mk_rx"]
_mk_buf = ns["_mk_buf"]
_endrx = ns["_endrx"]
print("  已载入真实 repeater 模块 (REC_RATE=%d PART=%d RESERVE=%d)"
      % (ns["REC_RATE"], ns["PART"], ns["RESERVE"]))


class _LcdStub:
    """_squeeze() 只碰 ctx.lcd._fb_cache。"""
    _fb_cache = {}


class _ShellStub:
    audio = None


class _Ctx:
    pass


def new_ctx(a):
    c = _Ctx()
    c.shell = _ShellStub()
    c.shell.audio = a
    c.audio = a
    c.lcd = _LcdStub()
    c.owner = None
    c.parts = []
    c.rx = None
    c.moved = False
    c.got = 0
    c.wi = 0
    c.wo = 0
    c.cap = 0
    c.state = "idle"
    return c


shell_audio = Audio()
if not shell_audio.ok:
    print("  初始 Audio() 就失败: %s" % shell_audio.error)
    raise SystemExit(1)
print("  初始共享 Audio() ok=True  free=%d" % gc.mem_free())

# 真实 OS 里还常驻着显示缓存、BLE 缓冲、按钮/电量对象、repeater 自己的字节码……
# 上面裸跑时堆有 127 KB 空闲，比真机宽松得多，所以永远重建得成功。
# 这里用"压舱物"把堆压到不同水位，找重建开始失败的那个点。
BALLAST_KB = (0, 20, 40, 50, 60, 70)
TRIALS = 3

print("")
print("  压舱  | 重建前free | 重建成功 | 失败时的错误")
print("  -------+------------+----------+-------------------------------")

fail_total = 0
dead_total = 0
attempts = 0

for os_kb in BALLAST_KB:
    ballast = []
    for _ in range(os_kb // 4):
        try:
            ballast.append(bytearray(4096))
        except MemoryError:
            break
    gc.collect()

    for _t in range(TRIALS):
        attempts += 1
        ctx = new_ctx(shell_audio)

        ctx.owner = ctx.audio
        _release(ctx)
        if not shell_audio.ok:
            dead_total += 1

        rx_info = _mk_rx(ctx)
        _mk_buf(ctx)
        parts = len(ctx.parts)
        part_bytes = sum(len(p) for p in ctx.parts)

        if ctx.rx is not None:
            try:
                ctx.rx.readinto(bytearray(1024))
            except Exception:                                 # noqa: BLE001
                pass

        _endrx(ctx)
        gc.collect()
        free_before = gc.mem_free()

        # teardown 的重建：**此时录音缓冲还活着**（与真实顺序一致）
        fresh_ok = False
        err = None
        try:
            gc.collect()
            fresh = Audio()
            fresh_ok = fresh.ok
            if fresh.ok:
                shell_audio = fresh
                ctx.shell.audio = fresh
            else:
                err = fresh.error
        except Exception as exc:                              # noqa: BLE001
            err = "%s: %s" % (type(exc).__name__, exc)

        if not fresh_ok:
            fail_total += 1
        # 重建之后才释放录音缓冲（repeater.py:475）
        ctx.parts = []
        ctx.cap = 0
        gc.collect()

        print("  %4dKB | %10d | %8s | %s"
              % (len(ballast) * 4, free_before, "OK" if fresh_ok else "FAIL",
                 err or "-"))
        if not fresh_ok and not shell_audio.ok:
            # 重建失败 => shell.audio 停在死实例上，全系统哑
            pass

    del ballast
    gc.collect()

print("")
print("=" * 68)
print("  共享 Audio 被 deinit 的轮次: %d/%d   <- 机制确实存在"
      % (dead_total, attempts))
print("  重建失败的轮次:            %d/%d" % (fail_total, attempts))
if fail_total:
    print("  => #25 **可复现**：这些轮次里 shell.audio 会停在死实例上，全系统哑，")
    print("     只有重启能救。上表可以看出发作所需的堆水位。")
else:
    print("  => 到 70 KB 压舱都没能触发重建失败，见 known-issues #25 的结论")
print("=" * 68)

if not shell_audio.ok:
    print("  收尾：共享 Audio 已死，重建一个")
    gc.collect()
    shell_audio = Audio()
    print("  收尾重建 ok=%s" % shell_audio.ok)
