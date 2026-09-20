#!/usr/bin/env python3
"""`passport/console.py` 的离线单元测试 —— BLE 终端的行为规范。

这些用例守的是"交互模式"的语义：表达式要回显、print 要接住、状态要跨命令
存活、异常不能把设备带走。最后一条在真机上代价极大（卡住只能断电），
所以必须在这里守住。

    python tools/test_console.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "os"))

# MicroPython 的 time 模块有 ticks_ms/ticks_diff，CPython 没有 —— 补上，
# 这样被测代码不需要为"跑在电脑上"写任何分支。
import time as _time
if not hasattr(_time, "ticks_ms"):
    _time.ticks_ms = lambda: int(_time.monotonic() * 1000) & 0x3FFFFFFF
    _time.ticks_diff = lambda a, b: a - b
    _time.ticks_add = lambda a, b: a + b
    _time.sleep_ms = lambda ms: _time.sleep(ms / 1000.0)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print("  ok   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s   %s" % (name, extra))


def main():
    from passport.console import MAX_OUT, Console

    con = Console(shell=None)

    print("\n[1] 表达式像 REPL 一样回显")
    r = con.run("1 + 1")
    check("1+1 → 2", r["ok"] and r["out"].strip() == "2", repr(r))
    r = con.run("'a' * 3")
    check("字符串带引号回显（repr 而非 str）",
          r["out"].strip() == "'aaa'", repr(r["out"]))
    r = con.run("None")
    check("None 不回显任何东西（和真 REPL 一致）",
          r["ok"] and r["out"] == "", repr(r["out"]))

    print("\n[2] 语句：print 被接住，赋值没有输出")
    r = con.run("print('hello')")
    check("print 被捕获", r["ok"] and r["out"] == "hello\n", repr(r["out"]))
    r = con.run("x = 41")
    check("赋值无输出且不报错", r["ok"] and r["out"] == "", repr(r))

    print("\n[3] 状态跨命令存活（这就是'交互模式'的关键）")
    r = con.run("x + 1")
    check("上一条赋的 x 还在", r["ok"] and r["out"].strip() == "42", repr(r))
    r = con.run("def f(n):\n    return n * 2\nprint(f(21))")
    check("多行函数定义 + 调用", r["ok"] and r["out"].strip() == "42", repr(r))
    r = con.run("f(3)")
    check("函数也留在命名空间里", r["out"].strip() == "6", repr(r))

    print("\n[4] 异常：报错但不把设备带走")
    r = con.run("1 / 0")
    check("ZeroDivisionError 被报成 ok=False", r["ok"] is False, repr(r))
    check("输出里有 traceback", "ZeroDivisionError" in r["out"], repr(r["out"]))
    r = con.run("undefined_name_xyz")
    check("NameError 同样接住", r["ok"] is False and "NameError" in r["out"],
          repr(r["out"]))
    r = con.run("2 + 2")
    check("★ 出错之后终端仍然可用", r["ok"] and r["out"].strip() == "4", repr(r))

    print("\n[4.1] 报错要给出行号和出错那行源码")
    # 真机上 sys.print_exception 对动态编译的代码只给一行 "NameError: ..."，
    # 没有行号 —— 而终端里最要紧的恰恰是"哪一行错了"。
    r = con.run("a = 1\nb = 2\nboom_here")
    check("给出行号 line 3", "line 3" in r["out"], repr(r["out"]))
    check("给出出错那行源码", "boom_here" in r["out"], repr(r["out"]))
    check("异常本身也在", "NameError" in r["out"], repr(r["out"]))
    r = con.run("print('x')\n1/0")
    check("多行里第 2 行出错 → line 2", "line 2" in r["out"], repr(r["out"]))
    check("出错前的 print 仍然保留", r["out"].startswith("x\n"), repr(r["out"]))

    r = con.run("raise SystemExit(0)")
    check("★ exit()/SystemExit 不会穿出去（否则会带走整个 OS）",
          r["ok"] is False, repr(r))
    r = con.run("raise KeyboardInterrupt()")
    check("★ KeyboardInterrupt 也不会穿出去", r["ok"] is False, repr(r))
    r = con.run("print('still alive')")
    check("★ 上面两种之后终端依然可用",
          r["ok"] and r["out"].strip() == "still alive", repr(r))

    print("\n[5] print 会被替换回来（不然整个系统再也打不出日志）")
    real = sys.stdout
    con.run("print('x')")
    check("run() 结束后 sys.stdout 恢复原样", sys.stdout is real,
          repr(sys.stdout))

    print("\n[6] 输出截断")
    r = con.run("print('a' * %d)" % (MAX_OUT * 4))
    check("超长输出被截断", len(r["out"]) <= MAX_OUT + 64,
          "len=%d" % len(r["out"]))
    check("截断处有提示", "已截断" in r["out"], repr(r["out"][-40:]))

    print("\n[7] 空输入")
    for src in ("", "   ", "\n"):
        r = con.run(src)
        check("空源码 %r 直接返回且不报错" % src, r["ok"] and r["out"] == "",
              repr(r))

    print("\n[8] reset 清空命名空间")
    con.run("keepme = 1")
    con.reset()
    r = con.run("keepme")
    check("reset 之后旧变量没了", r["ok"] is False and "NameError" in r["out"],
          repr(r))

    print("\n[9] 预置的名字（不用 import 就能摸硬件）")
    for name in ("gc", "time", "sys", "apps"):
        r = con.run("print(%s is not None)" % name)
        check("命名空间里有 %s" % name, r["out"].strip() == "True", repr(r))
    r = con.run("print(hasattr(gc, 'collect'))")
    check("gc 是真模块（可调用）", r["ok"] and r["out"].strip() == "True", repr(r))

    print("\n[10] 注入的 shell 成员")
    class FakeShell:
        lcd = "LCD"
        audio = None
        battery = "BAT"
        link = "LINK"
        buttons = "BTN"

    con2 = Console(shell=FakeShell())
    r = con2.run("print(lcd, battery, link, buttons)")
    check("shell 的硬件对象已注入",
          r["out"].strip() == "LCD BAT LINK BTN", repr(r))
    r = con2.run("print(audio)")
    check("值为 None 的成员也能访问（不抛 AttributeError）",
          r["ok"] and r["out"].strip() == "None", repr(r))

    print("\n[11] 返回结构")
    r = con.run("1")
    check("返回里有 ok / out / ms",
          set(r) == {"ok", "out", "ms"}, repr(sorted(r)))
    check("ms 是非负整数", isinstance(r["ms"], int) and r["ms"] >= 0, repr(r["ms"]))

    print("\n[12] ★ 真机构建：sys.stdout 存在但【不可赋值】")
    # 真机实测（MicroPython v1.29.0 / ESP32-C3）：
    #     >>> hasattr(sys, "stdout")   ->  True
    #     >>> sys.stdout = S()         ->  AttributeError: ... has no attribute 'stdout'
    # 所以捕获输出**不能**依赖重定向 stdout，必须靠注入命名空间里的 print。
    # 这个用例用假 sys 模块复现那个构建，保证这条退路一直在。
    import passport.console as _console_mod

    class _UnwritableSys:
        """有 stdout（可读），但任何赋值都抛 AttributeError —— 和真机一样。"""

        def __init__(self, real):
            object.__setattr__(self, "_real", real)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_real"), name)

        def __setattr__(self, name, value):
            raise AttributeError(
                "'module' object has no attribute '%s'" % name)

    real_sys = _console_mod.sys
    _console_mod.sys = _UnwritableSys(sys)
    try:
        con3 = Console(shell=None)
        r = con3.run("print('captured anyway')")
        check("★ stdout 不可赋值时 print 仍被捕获",
              r["ok"] and r["out"] == "captured anyway\n", repr(r))
        r = con3.run("1 + 1")
        check("★ 表达式回显也不依赖 stdout",
              r["ok"] and r["out"].strip() == "2", repr(r))
        r = con3.run("print('a', 'b', sep='-')")
        check("注入的 print 支持 sep",
              r["out"] == "a-b\n", repr(r["out"]))
        r = con3.run("print('x', end='')")
        check("注入的 print 支持 end", r["out"] == "x", repr(r["out"]))
        r = con3.run("1 / 0")
        check("★ 异常 traceback 也走注入的 sink",
              r["ok"] is False and "ZeroDivisionError" in r["out"], repr(r))
    finally:
        _console_mod.sys = real_sys

    print("\n[13] ★ 真机行为：sys.print_exception 只对原生流给完整 traceback")
    # 真机实测（MicroPython v1.29.0 / ESP32-C3）：
    #   sys.print_exception(e, io.StringIO())  -> 完整 traceback，带 line N
    #   sys.print_exception(e, <自定义对象>)    -> 只有 "NameError: ..." 一行
    # 这个差异极其隐蔽：输出"看着是对的"，只是少了行号。而在 CPython 上
    # 根本没有 sys.print_exception，这个坑永远不会自己暴露 —— 所以模拟它。
    import io as _io
    import sys as _sysmod

    def _fake_print_exception(exc, file=None):
        if isinstance(file, _io.StringIO):
            file.write('Traceback (most recent call last):\n'
                       '  File "<ble-console>", line 2, in <module>\n'
                       "NameError: name 'boom' isn't defined\n")
        else:
            file.write("NameError: name 'boom' isn't defined\n")

    _sysmod.print_exception = _fake_print_exception
    try:
        con4 = Console(shell=None)
        r = con4.run("ok = 1\nboom")
        check("★ 走原生流拿到带行号的完整 traceback",
              "line 2" in r["out"], repr(r["out"]))
        check("★ 并且回显了出错那行源码（^^^）",
              "^^^ boom" in r["out"], repr(r["out"]))
    finally:
        del _sysmod.print_exception

    print("\n[14] traceback 里没有 <ble-console> 帧时不能崩")
    r = con.run("raise ValueError('x')\n")
    check("普通异常照常报错", r["ok"] is False and "ValueError" in r["out"],
          repr(r["out"]))

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
