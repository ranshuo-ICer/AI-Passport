# 刷机与部署

两条路线，**先选一条**，再按对应步骤做。两条都从「备份原厂固件」开始。

| | 路线 A | 路线 B |
| --- | --- | --- |
| 得到什么 | 小智 AI 语音助手（能对话） | PassportOS（自制小程序系统） |
| 固件 | ESP-IDF / C，预编译镜像 | MicroPython + Python 源码 |
| 联网 | 需要 Wi-Fi + 小智服务端 | 小程序可完全离线 |
| 详细文档 | 本文 §3 | 本文 §4 + [`passport-os.md`](passport-os.md) |

⚠ 两条路**互斥**：一次只能烧一个固件。互相切换要重新烧，但备份在手随时能回。

---

## 1. 为什么要先备份

**这块板没有板载恢复兜底。** 网上流传的「`0x700000` 有永久 Recovery 分区，
刷坏也能救回来」是**错的** —— 实测 `0x700000` 是纯 `0xFF`，整块 8 MB 有 72% 是空的；
官方 upstream 的 CHANGELOG 也明确写了
*"Removed the obsolete app/test partition at `0x700000`"*。

> **你手里的 8 MB 备份是唯一退路。**

Windows 上双击 `windows\2-备份原厂固件.bat` 即可，它会：

1. `esptool read_flash 0 0x800000` 读全片
2. 用 `certutil` 打印 SHA-256
3. 存到 `backup/passport_original_8MB.bin`

备份后建议**再复制一份到别处**。这个文件含设备特有的 `cardid` 身份分区，
所以**不要公开**（仓库的 `.gitignore` 已排除 `backup/`）。

---

## 2. 通用前提

- Windows 10/11（或 macOS/Linux，把 `.bat` 换成对应命令）
- Python 3，安装时勾选 **Add python.exe to PATH**
- 一根**能传数据**的 Type-C 线（很多充电线不传数据，这是最常见的坑）
- 设备开机（屏幕亮）。**电源键是硬件开关，关机后 USB 不枚举**

---

## 3. 路线 A：刷小智语音助手

按顺序双击 `windows\` 下的脚本：

| 步骤 | 脚本 | 作用 |
| --- | --- | --- |
| 1 | `1-安装esptool.bat` | 装 `esptool` + `pyserial` |
| 2 | `2-备份原厂固件.bat` | 备份 8 MB 到 `backup/` |
| 3 | `3-烧录小智固件.bat` | **先核对 SHA-256** 再烧 |
| 4 | `4-查看串口日志.bat` | 看启动日志，排错用 |

固件信息（release `v2.4.2-folo.1`，2026-08-23）：

- 文件 `firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin`
- 大小 7,742,498 字节
- SHA-256 `e19b35ad5ee7d8f9cfa9a22c51a69f25835ec90ce092a798d875915f23b275ba`
- 烧录地址 **`0x0`**（完整合并镜像，必须从 0x0 烧）
- 基于 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 2.4.2 适配

**烧完之后**：设备重启 → 屏幕显示一个 Wi-Fi 热点名 → 手机连上它 →
配网页面一般会自动弹出，没弹出就用浏览器开 `http://192.168.4.1` → 选 Wi-Fi、
填密码、按提示填小智服务端地址 → 说「**你好小智**」唤醒。

按键：**OK** 开始/停止对话，**UP / DOWN** 音量 ±10，开机时按住 OK 进配网。

**更省事的替代**：不用装 Python，用官方网页刷机工具
[tool.folotoy.com](https://tool.folotoy.com/)（Chrome/Edge，走 Web Serial）。
优点是不用装环境、能实时看日志；缺点是**手机浏览器用不了**
（Android Chrome 不支持 Web Serial）。

---

## 4. 路线 B：烧 PassportOS

双击 `windows\5-烧录PassportOS.bat`，它会依次做四件事：

1. 装依赖（`esptool` / `pyserial` / `mpremote`）
2. **擦除 Flash**，写入 MicroPython v1.29.0
3. 用 `tools/deploy.py` 上传 `os/` 下的系统文件
4. 装上内置示例小程序并复位

固件信息：

- 文件 `firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin`
- 大小 1,754,736 字节
- SHA-256 `bf72ed9eb88ad3a8f49d02c3d371f9ea34c90a8a303aeb9e324d8efd4a2a655a`
- 来源 <https://micropython.org/download/ESP32_GENERIC_C3/>

看到设备屏幕出现 **Passport** 主菜单就成了。

> **注意**：路线 B 会**完全擦除**原厂固件。之后想回路线 A 就直接烧小智固件，
> 想回原厂就烧备份。

### 日常更新（不用重新烧录）

**PassportOS 本身就是设备文件系统上的一堆 `.py`**。改完源码只要重新部署：

```sh
python tools/deploy.py                 # 自动找端口；会回读校验每个文件
python tools/deploy.py COM21 --clean   # 先清空 /passport 和 /apps 再传
```

MicroPython 固件本身不动，所以**这不是「重新烧录」**。只有换固件（小智 ↔
MicroPython）才需要 esptool。

---

## 5. 手动命令

如果不想用 `.bat`：

```sh
# 统一走 tools/esp.py，它会自动适配 esptool v4(下划线) / v5(连字符) 两套命令名
python tools/esp.py --chip esp32c3 --baud 460800 erase_flash
python tools/esp.py --chip esp32c3 --baud 460800 write_flash -z 0x0 firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin

# 部署系统文件
python tools/deploy.py

# 小智固件（路线 A）
python tools/esp.py --chip esp32c3 --baud 460800 write_flash --flash_mode dio --flash_freq 80m --flash_size 8MB 0x0 firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin
```

---

## 6. 回退

| 想回到 | 怎么做 |
| --- | --- |
| 原厂固件 | `python tools/esp.py --chip esp32c3 --port COM3 --baud 460800 write_flash 0x0 backup/passport_original_8MB.bin` |
| 小智固件 | 重跑 `windows\3-烧录小智固件.bat` |
| PassportOS | 重跑 `windows\5-烧录PassportOS.bat` |

---

## 7. 出问题了

常见症状（找不到串口、卡在 `Connecting...`、烧完黑屏、COM 口消失等）
以及**每条症状背后的原因**，见 [`pitfalls.md`](pitfalls.md) 的
「症状速查」一节。

---

## 附录：为什么不能用手机刷

实测：DSHA 所在的 Android App 沙箱**看不到 `/dev/bus/usb`**，`ls /dev`
直接被拒，桥接接口 `/app/*` 也不提供任何 USB / 串口 / BLE 能力。
Android 应用的 USB 访问必须走 Java `UsbManager` 授权，沙箱内无法绕过。

**所以刷机这一步必须在电脑上做**（理论上 root + Termux 可以，但性价比极低）。
