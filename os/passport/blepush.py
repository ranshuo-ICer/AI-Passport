"""BLE 小程序推送服务（GATT peripheral）。

手机侧（Web Bluetooth / Android）通过这里把小程序源码传进来、列目录、删除、运行。
协议细节见 docs/ble-protocol.md。

两个特征值：
    CMD (写)   —— 没有上传任务时，载荷是 UTF-8 的 JSON 命令；
                  处于上传任务时，载荷就是 app.py 的原始字节，直接追加写文件。
                  这样省掉了 hex/base64 的 2 倍膨胀。
    RSP (通知) —— 设备 → 手机，UTF-8 JSON。超过单包 MTU 时用 '~'/'!' 分片：
                  '{...}'  单包完整
                  '~...'   分片未完，手机累积
                  '!...'   末片，手机累积后解析

流控：手机每发一个数据片都等设备回 {"t":"ack"} 再发下一片，
      这样设备端永远不会积压，也就不会 OOM。
"""

import gc
import json
import time

import bluetooth

from . import config as C
from . import apps

# ESP32 端口的 IRQ 事件号（esp32/modbluetooth.c）。用 getattr 兜底，
# 万一以后固件把常量暴露出来也能自动跟上。
def _irq(name, default):
    return getattr(bluetooth, name, default)

_IRQ_CENTRAL_CONNECT = _irq("_IRQ_CENTRAL_CONNECT", 1)
_IRQ_CENTRAL_DISCONNECT = _irq("_IRQ_CENTRAL_DISCONNECT", 2)
_IRQ_GATTS_WRITE = _irq("_IRQ_GATTS_WRITE", 3)
_IRQ_MTU_EXCHANGED = _irq("_IRQ_MTU_EXCHANGED", 21)

_F_NOTIFY = getattr(bluetooth, "FLAG_NOTIFY", 0x10)
_F_WRITE = getattr(bluetooth, "FLAG_WRITE", 0x08)
_F_WRITE_NR = getattr(bluetooth, "FLAG_WRITE_NO_RESPONSE", 0x04)

_CHUNK_LIMIT = 180          # 单包通知的最大 JSON 长度，留足余量给 MTU
# 单次 GATT 写能带的 JSON 上限（实测硬上限 512，见 _handle_cmd 的 py 分支）
MAX_CMD_WRITE = 512
# 终端源码的总上限。分片累积超过它就拒绝 —— 否则一个手滑的粘贴会把堆吃光。
_MAX_CONSOLE_SRC = 8192
# ★ 两片通知之间必须留出时间让控制器把 ACL TX 队列排空。
#
#   实测（2026-08，Windows + bleak，`ls` 响应 932 字节 → 6 片）：
#   连续调 6 次 gatts_notify，**一次都不报错**，但只有第 1 片真的到了客户端，
#   后 5 片被协议栈静默丢弃 —— ESP-IDF Bluedroid 在 TX 队列满时
#   esp_ble_gatts_send_indicate 照样返回 ESP_OK，包却没了。
#   客户端那边表现为"命令发出去、永远等不到回应"，设备侧却一片安静，
#   是最难查的一类故障。
#
#   这也解释了为什么它是"后来才坏的"：小程序少的时候响应不足 180 字节、
#   单片就发完了，一旦 app 数量涨上去、响应超过一包，就必然踩到。
_FRAG_GAP_MS = 50


class AppLink:
    def __init__(self, host, log=None):
        self.host = host
        # 默认打到串口。以前默认是空函数，导致 BLE 起不来时串口上一点痕迹都没有。
        self.log = log or (lambda m: print("[BLE]", m))
        self.ble = None
        self.conn = None
        self.mtu = 23
        self.cmd_handle = None
        self.rsp_handle = None
        self.ready = False

        self._rx = []              # IRQ 里塞进来，主循环消费
        self._drop = 0
        self._last_rx = time.ticks_ms()   # 看门狗用：最后一次收到写入的时间
        self._pending_abort = False       # IRQ 里只挂标记，文件 I/O 留给主循环
        self._up = None            # 上传中的任务
        self._pending = b""        # 拆包用的输入缓冲（命令模式下不用）
        self._rsp_buf = []
        self._notify_fail = 0
        self._con = None              # BLE 终端的执行环境（惰性建）
        self._cbuf = ""               # 终端源码的分片累积缓冲

    # ------------------------------------------------------------------ 启动
    def start(self):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        try:
            self.ble.config(mtu=C.BLE_MTU)
        except (ValueError, TypeError, OSError):
            pass                        # 协商失败就用默认 MTU，协议自适应
        self.ble.irq(self._irq)
        services = (
            (
                bluetooth.UUID(C.UUID_SERVICE),
                (
                    (bluetooth.UUID(C.UUID_CMD), _F_WRITE | _F_WRITE_NR),
                    (bluetooth.UUID(C.UUID_RSP), _F_NOTIFY),
                ),
            ),
        )
        result = self.ble.gatts_register_services(services)
        self.log("gatts_register_services 返回: %r" % (result,))
        handles = result[0]
        self.log("服务 0 的特征值句柄: %r（应有 2 个）" % (handles,))
        self.cmd_handle = handles[0]
        self.rsp_handle = handles[1] if len(handles) > 1 else None
        if self.rsp_handle is None:
            self.log("!! 严重：通知特征值没注册上（句柄不足 2 个）")

        # ★ 必须显式扩大特征值缓冲。
        # MicroPython 的 GATT 特征值默认最大长度只有 20 字节
        # （= 默认 ATT MTU 23 - 3）。超过这个长度的写入会被【静默截断】，
        # 表现为设备收到半截 JSON，回 "命令不是合法 JSON"。
        # 实测：客户端写 69 字节，设备只收到 20 字节。
        try:
            self.ble.gatts_set_buffer(self.cmd_handle, C.BLE_ATTR_MAX_LEN, False)
            self.log("CMD 特征值缓冲已设为 %d 字节" % C.BLE_ATTR_MAX_LEN)
        except Exception as exc:                          # noqa: BLE001
            self.log("gatts_set_buffer 失败: %s: %s（长命令会被截断）"
                     % (type(exc).__name__, exc))
        if self.rsp_handle is not None:
            try:
                self.ble.gatts_set_buffer(self.rsp_handle, 64, False)
            except Exception:                             # noqa: BLE001
                pass
        self.ready = True
        self._advertise()
        self.log("BLE 已启动: %s  cmd=%s rsp=%s"
                 % (C.BLE_NAME, self.cmd_handle, self.rsp_handle))

    def _advertise(self):
        if not self.ready:
            self.log("广播被跳过：BLE 还没就绪")
            return
        name = C.BLE_NAME.encode()
        # 主广播放 flags + 128bit 服务 UUID（21 字节，刚好塞进 31）
        uuid_le = bytes(reversed(bytes.fromhex(C.UUID_SERVICE.replace("-", ""))))
        adv = bytes((2, 0x01, 0x06)) + bytes((17, 0x07)) + uuid_le
        # 扫描响应放设备名
        resp = bytes((len(name) + 1, 0x09)) + name
        self.log("广播载荷 adv=%d 字节 resp=%d 字节" % (len(adv), len(resp)))
        try:
            self.ble.gap_advertise(200000, adv_data=adv, resp_data=resp)
            self.log("广播已启动（含名称）")
            return
        except Exception as exc:                          # noqa: BLE001
            self.log("带 resp_data 广播失败: %s: %s" % (type(exc).__name__, exc))
        try:
            self.ble.gap_advertise(200000, adv_data=adv)
            self.log("广播已启动（仅 adv_data，无名称）")
        except Exception as exc:                          # noqa: BLE001
            self.log("广播彻底失败: %s: %s" % (type(exc).__name__, exc))

    # ------------------------------------------------------------------ 中断
    def _irq(self, event, data):
        # ⚠ 这个回调在调度上下文里跑，只做最小动作：拷贝 + 入队。
        if event == _IRQ_CENTRAL_CONNECT:
            self.conn = data[0]
            self._last_rx = time.ticks_ms()
            self.log("手机已连接")
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self.conn = None
            # ⚠ 不能在这里 _abort_upload()：它要删文件、刷新菜单（一堆 os.* 调用），
            #   而这是个 BLE 事件回调，本函数开头就写明"只做最小动作"。
            #   挂个标记，让主循环 poll() 去干。
            self._pending_abort = True
            self._advertise()
            self.log("手机已断开")
        elif event == _IRQ_GATTS_WRITE:
            conn, attr = data
            self._last_rx = time.ticks_ms()
            try:
                payload = self.ble.gatts_read(attr)
            except (OSError, ValueError):
                return
            if attr != self.cmd_handle:
                if C.DEBUG_BLE:
                    self.log("非 CMD 写入 attr=%s len=%d %r"
                             % (attr, len(payload), bytes(payload)[:24]))
                return
            if C.DEBUG_BLE:
                self.log("CMD 写入 len=%d %r" % (len(payload), bytes(payload)[:70]))
            if len(self._rx) < 32:          # 背压：满了就丢，手机等不到 ack 会重试
                self._rx.append(bytes(payload))
            else:
                self._drop += 1
        elif event == _IRQ_MTU_EXCHANGED:
            self.mtu = data[1]
            self.log("MTU 协商为 %d" % self.mtu)

    # ------------------------------------------------------------------ 发送
    def _write_rsp(self, text):
        if self.conn is None or self.rsp_handle is None:
            return False
        data = text.encode()
        n = len(data)
        # 通知单包上限 = ATT MTU - 3。
        # ⚠ MTU 还没协商出来时是默认值 23，此时上限只有 20 字节 ——
        #   这里必须走 20 这个分支。之前写成 `if self.mtu > 23`，
        #   恰好把 MTU==23 这种情况漏掉，算出 180 并原样发出去，
        #   结果被协议栈截断/发送失败，客户端永远停在半个分片上。
        if not self.mtu or self.mtu <= 23:
            limit = 20
        else:
            limit = min(_CHUNK_LIMIT, self.mtu - 3)
        if limit < 20:
            limit = 20
        if n <= limit:
            frames = (data,)
        else:
            frames = []
            i = 0
            while i < n:
                part = data[i:i + limit]
                i += limit
                frames.append((b"~" if i < n else b"!") + part)
        ok = True
        # 多片响应一定要留日志：notify 失败以前是【完全静默】的（只加个计数器），
        # 客户端那边表现为"命令发出去了、永远等不到回应"，而设备侧一片安静 ——
        # 这种故障查起来毫无抓手。列表命令就是唯一会发多片响应的那个。
        if len(frames) > 1:
            self.log("响应 %d 字节 → %d 片（单包上限 %d）" % (n, len(frames), limit))
        for idx, fr in enumerate(frames):
            try:
                self.ble.gatts_notify(self.conn, self.rsp_handle, fr)
            except (OSError, ValueError) as exc:
                ok = False
                self._notify_fail += 1
                self.log("!! notify 失败 第 %d/%d 片（%d 字节）: %s: %s"
                         % (idx + 1, len(frames), len(fr),
                            type(exc).__name__, exc))
                break
            if idx + 1 < len(frames) and _FRAG_GAP_MS:
                time.sleep_ms(_FRAG_GAP_MS)
        return ok

    def send(self, obj):
        return self._write_rsp(json.dumps(obj))

    def log_line(self, msg):
        """把日志同时打到串口和手机。

        必须打串口 —— 之前只在"已连接"时才发给手机，结果 BLE 启动失败这种
        关键错误在串口上完全看不到，排障时两眼一抹黑。
        """
        try:
            print("[PassportOS]", msg)
        except Exception:                                 # noqa: BLE001
            pass
        if self.conn is not None:
            self.send({"t": "log", "m": str(msg)[:200]})

    # ------------------------------------------------------------------ 主循环
    def poll(self):
        """在主循环里调用：消费 IRQ 排队的写入，并检查连接看门狗。"""
        if self._pending_abort:          # 断开事件在 IRQ 里挂的标记，这里补做清理
            self._pending_abort = False
            self._abort_upload(silent=True)
        while self._rx:
            payload = self._rx.pop(0)
            try:
                self._handle(payload)
            except Exception as e:                       # noqa: BLE001
                # 报告也放进 try 里：发 err 本身再出错（内存紧张时 json.dumps /
                # 分片都可能失败）以前会直接穿出 poll()，把整个 OS 带走。
                try:
                    self._abort_upload(silent=True)
                    self.send({"t": "err", "m": "%s: %s" % (type(e).__name__, e)})
                except Exception as exc:                 # noqa: BLE001
                    self.log("发送错误响应也失败: %s: %s" % (type(exc).__name__, exc))
        self._check_idle()

    def _force_disconnect(self, reason=""):
        """由设备端发起断开，并立刻恢复广播。

        实测：中心设备（Windows）在客户端调用 disconnect() 之后仍会抓着链路，
        设备侧 60 秒内都收不到断开事件 —— 期间不广播，表现为"再也扫不到设备"。
        所以正常的收尾流程和看门狗都走这里。
        """
        if self.conn is None:
            self._advertise()
            return
        self.log("主动断开（%s）" % reason)
        try:
            self.ble.gap_disconnect(self.conn)
        except Exception as exc:                          # noqa: BLE001
            self.log("gap_disconnect 失败: %s" % exc)
        # 不等断开事件，直接按"已断开"处理并重新广播，双保险
        self.conn = None
        self._abort_upload(silent=True)
        self._advertise()

    def _check_idle(self):
        """连接空闲太久就主动断开，把广播恢复回来（崩溃/关标签页的兜底）。

        正常收尾靠客户端的 {"t":"bye"}；这里只兜底那些没来得及说再见的场景。
        """
        limit = C.BLE_IDLE_TIMEOUT_MS
        if not limit or self.conn is None:
            return
        if time.ticks_diff(time.ticks_ms(), self._last_rx) < limit:
            return
        self._force_disconnect("空闲超过 %d 秒" % (limit // 1000))

    def _handle(self, payload):
        if self._up is not None:
            self._handle_data(payload)
            return
        if not payload:
            return
        try:
            cmd = json.loads(payload.decode())
        except (ValueError, UnicodeError):
            self.log("JSON 解析失败 原始 %d 字节: %r" % (len(payload), bytes(payload)[:80]))
            self.send({"t": "err", "m": "命令不是合法 JSON"})
            return
        self._handle_cmd(cmd)

    # ------------------------------------------------------------------ 命令
    def _handle_cmd(self, cmd):
        t = cmd.get("t")

        if t == "hello":
            self.send({
                "t": "hi",
                "os": "PassportOS",
                "name": C.BLE_NAME,
                "apps": len(apps.list_apps()),
                "free": apps.free_space(),
                "mtu": self.mtu,
            })

        elif t == "ls":
            self.send({"t": "ls", "apps": apps.list_apps()})

        elif t == "put":
            name = cmd.get("n") or ""
            size = int(cmd.get("s") or 0)
            if self._up is not None:
                # 上一个上传还没收尾（手机重试/换文件），先清理再开新的，
                # 否则旧的文件句柄会泄漏，半截文件也留在 Flash 上。
                self._abort_upload(silent=True)
            if not apps.valid_name(name):
                self.send({"t": "err", "m": "应用名只允许 a-z0-9_- ，且不超过 16 字符"})
                return
            if size <= 0 or size > C.MAX_APP_SIZE:
                self.send({"t": "err", "m": "体积超限（上限 %d 字节）" % C.MAX_APP_SIZE})
                return
            if len(apps.list_apps()) >= C.MAX_APPS and name not in \
                    [a["n"] for a in apps.list_apps()]:
                self.send({"t": "err", "m": "应用数量已达上限 %d" % C.MAX_APPS})
                return
            path, f = apps.make_app(name)
            self._up = {
                "n": name,
                "f": f,
                "path": path,
                "size": size,
                "got": 0,
                "title": cmd.get("title") or name,
            }
            gc.collect()
            self.send({"t": "put", "n": name, "s": size})

        elif t == "end":
            self._finish_upload()

        elif t == "abort":
            self._abort_upload()

        elif t == "run":
            name = cmd.get("n") or ""
            ok = self.host.launch(name)
            self.send({"t": "run", "n": name, "ok": ok})

        elif t == "stop":
            self.host.stop_app()
            self.send({"t": "stop", "ok": True})

        elif t == "rm":
            name = cmd.get("n") or ""
            ok = apps.delete_app(name)
            if ok:
                self._apps_changed("删除 %s" % name)
            self.send({"t": "rm", "n": name, "ok": ok})

        elif t == "state":
            self.send({"t": "state", "app": self.host.current_app()})

        elif t == "time":
            # 手机把当前时间送进来校准 RTC。epoch+tz 直接写成"当地墙钟时间"，
            # 这样小程序里 time.localtime() 拿到的就是本地时间（ESP32 无网络对时）。
            epoch = int(cmd.get("epoch") or 0)
            tz = int(cmd.get("tz") or 0)
            if epoch < 1600000000:
                self.send({"t": "time", "ok": False, "m": "epoch 不合理"})
            else:
                try:
                    import time as _time
                    import machine as _machine
                    lt = _time.localtime(epoch + tz)
                    _machine.RTC().datetime(
                        (lt[0], lt[1], lt[2], lt[6] + 1, lt[3], lt[4], lt[5], 0))
                    self.send({"t": "time", "ok": True})
                except Exception as exc:                  # noqa: BLE001
                    self.send({"t": "time", "ok": False, "m": str(exc)})

        elif t == "ping":
            self.send({"t": "pong"})

        elif t == "py":
            # BLE 终端：执行手机发来的一段 Python。真机上的用途是"不插线查状态"。
            # 语义与三条硬限制见 passport/console.py 的模块 docstring。
            #
            # 源码分片：单次 GATT 写最多只能带 **512 字节 JSON**（实测：
            # payload 512 成功、522 被拒，ATT 0x0D Invalid Attribute Value
            # Length），而 gatts_set_buffer 声明的 2048 是够不着的。
            # 所以长源码由客户端拆成多片、带 "more": true 逐片发，最后一片
            # 不带 more 才真正执行。
            src = cmd.get("c") or ""
            if cmd.get("more"):
                if len(self._cbuf) + len(src) > _MAX_CONSOLE_SRC:
                    self._cbuf = ""
                    self.send({"t": "py", "ok": False, "ms": 0,
                               "out": "源码过长（上限 %d 字节）"
                                      % _MAX_CONSOLE_SRC})
                    return
                self._cbuf += src
                self.send({"t": "py", "ok": True, "out": "", "ms": 0,
                           "buffered": len(self._cbuf)})
                return
            full = self._cbuf + src
            self._cbuf = ""
            r = self._console().run(full)
            r["t"] = "py"
            self.send(r)

        elif t == "pyreset":
            self._cbuf = ""
            self._console().reset()
            self.send({"t": "pyreset", "ok": True})

        elif t == "bye":
            # 客户端要走了，**由设备端主动断开**。
            # 为什么必须这样：实测客户端调用 disconnect() 之后，
            # 主机（Windows）仍然抓着 BLE 链路不放，设备 60 秒都收不到断开事件，
            # 期间不广播 —— 下一次就连不上了。设备主动断是唯一可靠的办法。
            self.send({"t": "bye", "ok": True})
            self._force_disconnect("客户端请求")

        else:
            self.send({"t": "err", "m": "未知命令 %r" % (t,)})

    def _console(self):
        """惰性建 BLE 终端的执行环境。

        惰性 + 只建一次：命名空间要在多次 `py` 命令之间存活（这样才像交互
        模式），但不连终端的人不该为它付内存代价。
        """
        con = getattr(self, "_con", None)
        if con is None:
            from .console import Console
            con = Console(self.host)
            self._con = con
        return con

    # ------------------------------------------------------------------ 上传
    #
    # ⚠ ping 必须在这里！客户端有 10 秒心跳，长上传（32KB = 205 个分片）期间
    #   一定会插进来一条 {"t":"ping"}。不在白名单里的话它会走"追加写文件"分支：
    #     · 源码里混进一行 {"t":"ping"} → SyntaxError
    #     · 这 12 字节还算进 got，设备提前 12 字节认为收满
    #       → 每次心跳丢掉 12 字节真实源码，**而且照常回 done**，完全静默。
    #   这个坑由 tools/repro_ping_corruption.py 复现并守着。
    _CTRL_DURING_UPLOAD = ("abort", "end", "put", "stop", "ping")

    def _handle_data(self, payload):
        # 先看是不是控制命令。代价是 app.py 里如果在正好一个分片边界上
        # 出现一模一样的短 JSON 会被误判 —— 概率极低，而且后果只是这次上传作废。
        if payload[:1] == b"{" and len(payload) < 64:
            ctrl = None
            try:
                ctrl = json.loads(payload.decode())
            except (ValueError, UnicodeError):
                ctrl = None
            if isinstance(ctrl, dict) and ctrl.get("t") in self._CTRL_DURING_UPLOAD:
                self._handle_cmd(ctrl)
                return

        up = self._up
        f = up["f"]
        remain = up["size"] - up["got"]
        if len(payload) > remain:
            payload = payload[:remain]
        f.write(payload)
        up["got"] += len(payload)
        if up["got"] >= up["size"]:
            self._finish_upload()
        else:
            self.send({"t": "ack", "g": up["got"]})

    def _apps_changed(self, why=""):
        """应用集合变了 → 让 UI 重新读一遍 /apps 并重画菜单。

        以前漏了这一步：推完/删完只动了文件系统，菜单上还是旧列表，
        得重启设备才看得到。用户反馈的"推送或删除 app 后主界面没刷新"就是这个。
        """
        fn = getattr(self.host, "refresh_apps", None)
        if fn is None:
            return
        try:
            fn()
            self.log("菜单已刷新（%s），共 %d 个应用"
                     % (why, len(getattr(self.host, "app_list", []))))
        except Exception as exc:                          # noqa: BLE001
            self.log("刷新菜单失败: %s: %s" % (type(exc).__name__, exc))

    def _finish_upload(self):
        up = self._up
        if up is None:
            self.send({"t": "err", "m": "没有正在进行的上传"})
            return
        try:
            up["f"].close()
        except OSError:
            pass
        self._up = None
        apps.write_meta(up["n"], up["title"], up["got"])
        gc.collect()
        self._apps_changed("安装 %s" % up["n"])
        self.send({"t": "done", "n": up["n"], "s": up["got"]})

    def _abort_upload(self, silent=False):
        up = self._up
        if up is None:
            return
        try:
            up["f"].close()
        except OSError:
            pass
        self._up = None
        # 半截的小程序是垃圾，留着只会在菜单里变成一个点不动的条目。
        # 代价：如果是覆盖上传，旧版本也会一起没了 —— 这是有意的。
        apps.delete_app(up["n"])
        self._apps_changed("放弃上传 %s" % up["n"])
        if not silent:
            self.send({"t": "abort", "n": up["n"]})

    # ------------------------------------------------------------------ 状态
    @property
    def connected(self):
        return self.conn is not None

    @property
    def uploading(self):
        return self._up is not None

    def progress_key(self):
        """`status_text()` 的廉价指纹 —— 它变了，状态栏才可能变。

        状态栏每 tick（50 Hz）都会重算一次，而 99.99% 的时间内容根本没变；
        拼一次字符串实测 157 µs，先比这个元组就能整段省掉。
        ⚠ 必须覆盖 `status_text()` 读的**每一个**字段，否则状态栏会卡住不刷新。
        """
        if self.conn is None:
            return 0
        if self._up is None:
            return 1
        return (2, self._up["n"], self._up["got"], self._up["size"])

    def status_text(self):
        if self.conn is None:
            return "BLE 等待连接"
        if self._up is not None:
            pct = self._up["got"] * 100 // max(1, self._up["size"])
            return "接收 %s %d%%" % (self._up["n"], pct)
        return "已连接"
