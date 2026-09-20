"""BLE 终端：执行手机发来的 Python 代码，返回输出。

为什么需要它：设备调一个 bug 要插 USB、开 mpremote、还可能打断系统；
走蓝牙的终端不插线就能查状态（`gc.mem_free()`、`lcd.fill(...)`、
翻 `/apps` 目录、试一行 API）。

**它不是"第二条 REPL"，语义上有三条硬限制**，用它之前必须知道：

1. **同步执行**。代码在 BLE 主循环里跑完才返回。`while True:` 或
   `time.sleep(10)` 会把界面、按键、看门狗**一起卡住** —— 只能断电重开。
2. **只捕获 `print` 的输出**。异步/中断里产生的输出不在捕获范围内。
3. **权限等同于系统本身**。`machine.reset()` 会重启，大面积改文件会破坏系统。

留一个命名空间在会话之间共享，这样才像交互模式（`x = 1` 之后能用 `x`）。
"""

import gc
import sys
import time

# 单次输出上限。响应要分片走 BLE（每片 180 字节、片间隔 50 ms），
# 4 KB 就要 23 片、一秒多，手机上体验很差，而且很容易撞上内存上限。
MAX_OUT = 2048


class _Sink:
    """接住 `print` 的输出。

    自己写而不用 `io.StringIO`：MicroPython 各移植版对 `io.StringIO` 的支持
    不一致，而这个类只要 `write`/`flush` 两个方法，没有任何不确定性。
    """

    __slots__ = ("_parts", "_n", "_full")

    def __init__(self):
        self._parts = []
        self._n = 0
        self._full = False

    def write(self, s):
        if self._full:
            return
        if not isinstance(s, str):
            s = str(s)
        self._parts.append(s)
        self._n += len(s)
        # 超限就停止累积：一个 `for i in range(10**6): print(i)` 不该把堆吃光
        if self._n >= MAX_OUT:
            self._full = True

    def flush(self):
        pass

    def text(self):
        t = "".join(self._parts)
        if self._full or len(t) > MAX_OUT:
            t = t[:MAX_OUT] + "\n... [输出超过 %d 字节，已截断]" % MAX_OUT
        return t


_FRAME_MARK = 'File "<ble-console>", line '


def _lineno_from_text(text):
    """从 traceback 文本里抠出**最内层** `<ble-console>` 帧的行号。

    为什么要解析文本而不是读 `exc.__traceback__`：真机实测
    （MicroPython v1.29.0 / ESP32-C3）异常对象上**根本没有**
    `__traceback__` 属性，但 `sys.print_exception(exc, file)` 能打出
    完整 traceback（含 `File "<ble-console>", line N`）。
    所以文本是唯一两个平台都拿得到的来源。rfind 取最内层帧 = 真正出错那行。
    """
    idx = text.rfind(_FRAME_MARK)
    if idx < 0:
        return None
    i = idx + len(_FRAME_MARK)
    digits = ""
    while i < len(text) and text[i].isdigit():
        digits += text[i]
        i += 1
    try:
        return int(digits)
    except ValueError:
        return None


def _traceback_text(exc):
    """拿到异常的完整 traceback 文本，两个平台都要能用。

    这里踩过两个坑，都只有真机才暴露：

    1. `exc.__traceback__` 在 MicroPython（v1.29.0 / ESP32-C3）上**根本不存在**，
       所以"自己走 tb_next 数行号"这条路是死的。
    2. `sys.print_exception(exc, file)` 的 `file` **必须是原生流**
       （`io.StringIO`）。传自定义的 write/flush 对象时它会退化成
       "只打 `NameError: ...` 一行" —— 没有文件名、没有行号，正是终端里最需要
       的那两条信息。这个差异非常隐蔽：输出"看起来是对的"，只是信息少了一半。
    """
    printer = getattr(sys, "print_exception", None)
    if printer is not None:
        try:
            import io
            buf = io.StringIO()
            printer(exc, buf)
            text = buf.getvalue()
            if text and text.strip():
                return text
        except Exception:                                 # noqa: BLE001
            pass
        try:                                              # 退一步：自定义 sink
            buf = _Sink()
            printer(exc, buf)
            text = buf.text()
            if text and text.strip():
                return text
        except Exception:                                 # noqa: BLE001
            pass
    try:                                                  # CPython
        import traceback
        buf = _Sink()
        traceback.print_exception(type(exc), exc,
                                  getattr(exc, "__traceback__", None),
                                  file=buf)
        return buf.text()
    except Exception:                                     # noqa: BLE001
        return "%s: %s\n" % (type(exc).__name__, exc)


def _show_exc(exc, file, src_lines=None):
    """把异常打成"人能用"的形态：完整 traceback + 出错那行的源码回显。

    回显源码这一步是有必要的：traceback 只给行号，而终端是一行一行敲进去的，
    对着行号数第几行很容易数错。
    """
    text = _traceback_text(exc)
    file.write(text)
    line = _lineno_from_text(text)
    if line and src_lines and 0 < line <= len(src_lines):
        bad = src_lines[line - 1].strip()
        if bad:
            file.write("      ^^^ %s\n" % bad)


class Console:
    """一个跨命令存活的执行环境。"""

    def __init__(self, shell=None):
        self.shell = shell
        self._sink = None
        self.ns = {}
        self.reset()

    def _print(self, *args, sep=" ", end="\n"):
        """注入到命名空间里的 print —— 捕获输出的**主要**手段。

        为什么不用 `sys.stdout = sink`：真机实测（MicroPython v1.29.0 /
        ESP32-C3）`sys.stdout` **存在但不可赋值**：

            >>> hasattr(sys, "stdout")
            True
            >>> sys.stdout = S()
            AttributeError: 'module' object has no attribute 'stdout'

        所以重定向 stdout 这条路在这个构建上是死的。往命名空间里放一个同名
        的 `print` 会遮蔽内置的 `print`，效果一样可靠，而且不依赖任何构建选项。
        （`sys.stdout` 那条路仍然保留为"能成就成"的补充，见 run()。）
        """
        if self._sink is None:
            return
        self._sink.write(sep.join([str(a) for a in args]))
        self._sink.write(end)

    def reset(self):
        """重建命名空间。预置常用模块和硬件对象，省掉每次 `import`。"""
        ns = {"__name__": "__ble_console__"}
        # gc / time / sys 是模块级 import，直接引用即可
        ns["gc"] = gc
        ns["time"] = time
        ns["sys"] = sys
        ns["print"] = self._print
        try:
            from . import apps as _apps
            ns["apps"] = _apps
        except Exception:                                 # noqa: BLE001
            pass
        if self.shell is not None:
            ns["shell"] = self.shell
            for attr in ("lcd", "audio", "battery", "link", "buttons"):
                try:
                    ns[attr] = getattr(self.shell, attr, None)
                except Exception:                         # noqa: BLE001
                    ns[attr] = None
        self.ns = ns

    def run(self, src):
        """执行一段源码，返回 {"ok", "out", "ms"}。

        先按**表达式**编译（这样 `1+1` 会像 REPL 一样回显 `2`），
        编译不过再按**语句块**编译。这个顺序是有意的：反过来会让所有
        表达式都变成"没有输出"。
        """
        if not src or not src.strip():
            return {"ok": True, "out": "", "ms": 0}

        sink = _Sink()
        saved = getattr(sys, "stdout", None)
        redirected = False
        try:
            sys.stdout = sink                             # 能成最好（库里的 print 也能收到）
            redirected = True
        except Exception:                                 # noqa: BLE001
            pass
        self._sink = sink
        t0 = time.ticks_ms()
        ok = True
        try:
            try:
                code = compile(src, "<ble-console>", "eval")
            except SyntaxError:
                code = compile(src, "<ble-console>", "exec")
            else:
                val = eval(code, self.ns)                 # noqa: S307
                if val is not None:
                    self._print(repr(val))
                code = None
            if code is not None:
                exec(code, self.ns)                       # noqa: S102
        except BaseException as exc:                      # noqa: BLE001
            # 连 KeyboardInterrupt / SystemExit 一起接：终端里 `exit()` 或
            # Ctrl-C 都不该把设备带走，只应该显示成一条错误。
            ok = False
            try:
                _show_exc(exc, sink, src.split("\n"))
            except Exception:                             # noqa: BLE001
                sink.write("%s: %s\n" % (type(exc).__name__, exc))
        finally:
            self._sink = None
            if redirected:
                # ⚠ 必须恢复：stdout 被留成 sink 的话，整个系统从此再也打不出
                #   串口日志，排障会直接失去唯一的信息来源。
                try:
                    sys.stdout = saved
                except Exception:                         # noqa: BLE001
                    pass

        try:
            dt = time.ticks_diff(time.ticks_ms(), t0)
        except Exception:                                 # noqa: BLE001
            dt = 0
        # 执行期间可能产生大量垃圾（尤其一次大 print），顺手收一次
        gc.collect()
        return {"ok": ok, "out": sink.text(), "ms": dt}
