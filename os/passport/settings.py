"""全局设置 —— 跨小程序、跨重启都要保留的那几个值。

目前只有一项：背光亮度。

**为什么单独开一个文件**，而不是塞进某个小程序的 kv：小程序的 kv 是
`/apps/<名字>/kv.json`，属于那个小程序的私有数据；而背光是**硬件状态** ——
小程序退出之后它还留着，Shell 下次开机又得把它读回来。让 Shell 去读某个小程序
的私有文件是错的层次，所以单开一个全局的。

读写都是懒的：只有真的改过才写盘（`flush()`），没改过就一个字节都不动。
文件损坏/不存在一律退回默认值，绝不让它影响开机。

    from passport import settings
    settings.get("bl", 75)
    settings.set("bl", 60)
    settings.flush()
"""

import json

PATH = "/settings.json"

_cache = None
_dirty = False


def load(force=False):
    """读出设置字典（带缓存）。永远返回 dict —— 读不出来就是空的。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    data = None
    try:
        with open(PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    _cache = data if isinstance(data, dict) else {}
    return _cache


def get(key, default=None):
    return load().get(key, default)


def set(key, value):
    global _dirty
    load()[key] = value
    _dirty = True


def flush():
    """把改动写回磁盘。没改过就什么都不做。返回是否真的写了。"""
    global _dirty
    if not _dirty:
        return False
    try:
        with open(PATH, "w") as f:
            json.dump(_cache, f)
    except OSError:
        return False
    _dirty = False
    return True


def reset_cache():
    """丢掉内存缓存（测试用；也用于强制下次重新读盘）。"""
    global _cache, _dirty
    _cache = None
    _dirty = False
