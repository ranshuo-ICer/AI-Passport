"""小程序存储与加载。

目录结构：
    /apps/<name>/app.py      小程序源码
    /apps/<name>/meta.json   {"title": "...", "version": "...", "bytes": N}

小程序契约（全部可选，只写你需要的）：
    TITLE = "Clock"

    def setup(ctx):            # 启动一次
    def loop(ctx):             # 主循环反复调用（约 50fps，即 20ms 一帧）
    def on_key(ctx, key):      # key ∈ "up" / "down" / "ok"
    def teardown(ctx):         # 退出前调用一次

ctx 提供（完整说明见 docs/ble-protocol.md）：
    ctx.lcd / ctx.w / ctx.h / ctx.frame / ctx.name
    ctx.battery / ctx.audio（可能为 None，用前判空）/ ctx.shell
    ctx.log(m) / ctx.exit()
    ctx.kv_get(k, d) / ctx.kv_set(k, v) / ctx.kv_flush()   （掉电保持）
"""

import gc
import os
import json

from . import config as C

_ALLOWED = "abcdefghijklmnopqrstuvwxyz0123456789_-"


def valid_name(name):
    if not name or len(name) > 16:
        return False
    for ch in name:
        if ch not in _ALLOWED:
            return False
    return True


def ensure_dir():
    try:
        os.mkdir(C.APPS_DIR)
    except OSError:
        pass


def app_dir(name):
    return C.APPS_DIR + "/" + name


def app_file(name):
    return app_dir(name) + "/app.py"


def meta_file(name):
    return app_dir(name) + "/meta.json"


def list_apps():
    """返回 [{"n":name,"title":..,"s":字节数}, ...]，按名字排序。"""
    ensure_dir()
    out = []
    try:
        names = os.listdir(C.APPS_DIR)
    except OSError:
        return out
    for name in names:
        if not valid_name(name):
            continue
        path = app_file(name)
        try:
            size = os.stat(path)[6]
        except OSError:
            continue
        title = name
        try:
            with open(meta_file(name)) as f:
                title = json.load(f).get("title") or name
        except (OSError, ValueError):
            pass
        out.append({"n": name, "title": title, "s": size})
    out.sort(key=lambda a: a["n"])
    return out


def free_space():
    try:
        st = os.statvfs("/")
        return st[0] * st[3]
    except (OSError, AttributeError):
        return -1


def delete_app(name):
    if not valid_name(name):
        return False
    d = app_dir(name)
    for fn in ("app.py", "meta.json", "kv.json"):
        try:
            os.remove(d + "/" + fn)
        except OSError:
            pass
    try:
        os.rmdir(d)
    except OSError:
        return False
    return True


def make_app(name, title=""):
    """给上传做准备，返回 (路径, 文件对象)。同名会覆盖。"""
    if not valid_name(name):
        raise ValueError("非法应用名: %r" % (name,))
    d = app_dir(name)
    try:
        os.mkdir(d)
    except OSError:
        pass
    f = open(app_file(name), "wb")
    return app_file(name), f


def write_meta(name, title, size):
    try:
        with open(meta_file(name), "w") as f:
            json.dump({"title": title or name, "bytes": size}, f)
    except OSError:
        pass


def read_source(name):
    with open(app_file(name)) as f:
        return f.read()


def load_module(name):
    """把 app.py 执行进一个全新的命名空间，返回该命名空间。

    这里有三层讲究，都是被真机逼出来的：

    1. **先编译、再丢掉源码、最后执行**。原来的写法把 src 一直持有到 exec
       结束，等于源码和字节码同时常驻。del + gc.collect() 让源码在 exec 前
       就还回去。

    2. **读文件前先 gc.collect()**。`f.read()` 要一次性要一块和文件等长的
       【连续】内存，而这块板没有 PSRAM、堆常年碎片化 —— 实测出现过
       "还有 78 KB 空闲却分配不出 19 KB" 的情况。collect 能合并空闲块。

    3. **失败就回收重试一次**。碎片是随运行状态波动的：同一个 28 KB 文件，
       有一次能进、下一次就 MemoryError。重试几乎总能成功，比让用户去
       重启设备强得多。
    """
    last = None
    for attempt in (1, 2):
        try:
            gc.collect()
            src = read_source(name)
            code = compile(src, app_file(name), "exec")
            del src
            gc.collect()
            mod = {"__name__": "app_" + name}
            exec(code, mod)
            return mod
        except MemoryError as exc:
            last = exc
            gc.collect()
    raise last
