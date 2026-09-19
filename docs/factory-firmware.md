# 原厂固件实测档案

> 2026-09-19 在实体设备上直接抓取，**非推测**。
> 这份档案不可再生 —— 烧掉原厂固件就没了，所以先记录下来。
>
> 注：设备 MAC / `sn` / 公钥指纹已替换为占位符（见 [`../NOTICE.md`](../NOTICE.md)）。
> 日志的**结构与字段含义是真实的**，标识值不具备可追溯性。

---

## 1. 固件身份

```
app_init: Project name:     trae_card
app_init: App version:      1.0.0
app_init: Compile time:     Aug 11 2026 18:33:41
app_init: ESP-IDF:          v5.5.3
boot:     Loaded app from partition at offset 0x10000
spi_flash: flash io: dio
```

启动后堆只剩 **66 KB 可用**（启动时 188 KB），LVGL 峰值占用 46,784 字节。

---

## 2. 真实分区表（从 8 MB 备份解析）

| 分区 | 类型 | 偏移 | 大小 |
| --- | --- | --- | --- |
| nvs | data/nvs | `0x009000` | 24 KB |
| phy_init | data/phy | `0x00F000` | 4 KB |
| **factory** | app | `0x010000` | **3 MB** |
| imgstore | data | `0x310000` | 128 KB |
| imgframe | data | `0x330000` | 152 KB |
| **cardid** | data/nvs | `0x356000` | 144 KB |
| audio | data | `0x37A000` | 512 KB |
| imgava | data | `0x3FA000` | 1 MB |

最后分区在 `0x4FA000` 结束，之后是空白。

> 表中每行的大小都等于「下一分区偏移 − 本分区偏移」，末地址也与解析结果吻合。
> （早期版本把 `cardid` 写成 16 KB，与 `audio` 的起始偏移对不上，已更正。）

**注意与开发基线的差别**：官方 upstream 仓库的 `partitions.csv` 只有
`nvs` + `phy_init` + 一个 8 MB 的 `factory`，那是**开发基线**；
这里是**出厂产品固件**的表，多了 imgstore / imgframe / cardid / audio / imgava
五个业务分区。

各分区用途（从日志推断）：

- `audio`（512 KB）：预置乐谱/提示音，日志有 `app: 音频模式=乐谱播放`
- `imgava`（1 MB）：8 个头像，日志有 `avatar_store: AVA1 ready: 8 avatars, 129288 bytes`
- `imgstore` / `imgframe`：图像资源与帧缓冲
- `cardid`：设备身份（卡号/密钥/公钥），`+TEST:ID` 校验的就是它

---

## 3. 硬件自检（`AT+TEST?`）

固件内置**产线自检**，一条命令跑完全部外设：

```
+TEST:INFO,ver=1.0.0,sn=4c11aexxxxxx,heap=76960,rst=11,PASS
+TEST:I2C,es8311=1,cw2017=1,PASS
+TEST:AUDIO,ret=0,tone_mag=3422,peak=5261,PASS
+TEST:BATT,present=1,soc=85,mv=4026,PASS
+TEST:BLE,started=1,sn=4c11aexxxxxx,PASS
+TEST:ID,sn=1,key=1,pk=1,hw=1,pk_fp=xxxxxxxx,PASS
+TEST:RESULT,PASS,fails=0
```

| 项 | 含义 |
| --- | --- |
| I2C | ES8311(0x18) 与 CW2017(0x63) 都在线 |
| AUDIO | 1000 Hz 回环：`tone_mag=3422`，静音基线 mag=1 → **音频通路完好** |
| BATT | SOC 85%，4026 mV，电池在位 |
| BLE | 控制器已启动 |
| ID | 身份分区完整，含公钥指纹 |

> 这张表**推翻了「音频/电池未验证」的不确定性** —— 硬件是好的，
> 之前 PassportOS 里没实现音频纯粹是软件没写。

---

## 4. 音频初始化参数（写音频驱动时的对照基准）

自检时打印的实际 I²S 配置：

```
I2S_IF: STD: TX, data_bit: 16, slot_bit: 16, ws_width: 16, slot_mode: MONO, slot_mask: 0x1
I2S_IF: STD: TX, sample_rate_hz: 8000, mclk_multiple: 256, clk_src: 6
I2S_IF: STD: RX, data_bit: 16, slot_bit: 16, ws_width: 16, slot_mode: MONO, slot_mask: 0x1
I2S_IF: STD: RX, sample_rate_hz: 8000, mclk_multiple: 256, clk_src: 6
Adev_Codec: Open codec device OK
ES8311: Work in Slave mode
```

要点：

- ES8311 工作在 **Slave 模式**（MCLK/BCLK/WS 由 ESP32-C3 主出）
- 标准 I²S（STD），16 bit，**MONO**，slot_mask 0x1
- 8000 Hz 是自检采样率，`mclk_multiple = 256`
- 自检走**回环**：TX 发 1000 Hz 正弦，RX 收回来量幅度

> 这套参数可以照搬到 [`../os/passport/audio.py`](../os/passport/audio.py)，
> 省掉大量试错。**现状**：该模块已实现播放（真机出声），采样率用 16000 Hz、
> 走 BCLK 倍频而非外部 MCLK —— 后者存疑，见
> [`known-issues.md`](known-issues.md) #11。

---

## 5. AT 指令接口（串口 115200）

固件在 USB 串口上暴露了一套 AT 命令，**不需要烧录就能驱动设备**。

启动日志：

```
prov: 产线指令就绪: AT+CARDID=/? / AT+TEST? / at+config=? / at+command=? / at+reboot
```

### 5.1 命令清单（`at+command=?` 原样返回）

```
+COMMAND: at+config=? | at+config=common,volume,<0-100> | at+config=common,standby_time,<秒>
        | at+command=restart,now | at+reboot | at+config=common,guide_count,<0-100>
        | AT+CARDID?/= | AT+TEST?/=<项>
```

| 命令 | 作用 |
| --- | --- |
| `AT+TEST?` | 跑完整产线自检 |
| `AT+TEST?=<项>` | 只跑某一项自检 |
| `at+config=?` | 读当前配置 |
| `at+config=common,volume,<0-100>` | **设音量** |
| `at+config=common,standby_time,<秒>` | **设空闲深睡时间**（0 = 永不） |
| `at+config=common,guide_count,<0-100>` | 设引导次数 |
| `at+command=restart,now` | 重启应用 |
| `at+reboot` | 重启 |
| `AT+CARDID?` / `AT+CARDID=<值>` | 读写设备卡号（只写参数会返 `+ERR=missing_param`） |

### 5.2 配置读取实测（`at+config=?`）

```
+CONFIG: sn=4c11aexxxxxx,key=<已隐去>,hw=v1.0.0,provisioned=1,
         volume=95,standby_time=300,guide_count=2,guide_remaining=0,name=<已隐去>
```

- `standby_time=300` → 空闲 5 分钟进深睡（与启动日志一致）
- `volume=95` → 出厂默认偏大
- `name` / `key` 是**通过手机小程序写入的个人信息**，本文件不记录具体值

---

## 6. 其他启动期事实

| 日志 | 含义 |
| --- | --- |
| `adc_button: Calibration Success` / `IoT Button Version: 4.2.0` | **按键 ADC 走 IDF 的校准曲线**，不是裸阈值 |
| `disp_bl: 背光 LEDC 就绪 gpio=21` / `set_backlight 100% duty=1023/1023` | 背光 10 bit PWM，满值 1023 |
| `cw2017: profile '4.2v_520mah' 已在芯片中(UPDATE_FLAG 置位)` | 电池 profile **出厂已烧进 CW2017** |
| `avatar_store: AVA1 ready: 8 avatars, 129288 bytes` | `imgava` 分区放了 8 个头像 |
| `app: 音频模式=乐谱播放` | 原厂定位是「乐谱/音乐播放」 |
| `app: token 广播接收 已启用` | 有 BLE 广播令牌机制 |
| `png: PNG 解码 96x156 type=6 → ARGB8888` | 头像走 PNG 解码 |

> `cw2017` 那条很重要：官方 BSP 里那段「写入 80 字节 profile」的代码，
> **在出厂设备上其实已经是置位状态**，所以
> [`../os/passport/battery.py`](../os/passport/battery.py) 的 `init_profile()`
> 可以安全调用，但并非必需。
>
> ⚠ 注意它**不是幂等的**：每次都无条件重写 80 字节 profile、写
> `REG_CONFIG=0x00`、并重设 `UPDATE_FLAG`（会触发一次 SOC 重算）。
> 无害，但别把它当成「只在首次生效」的空操作。

---

## 7. 怎么重新抓这些信息

```sh
python tools/serial_probe.py          # 见 tools.md
```

或者用任意串口助手（115200 8N1）连上，发：

```
AT+TEST?
at+config=?
```

---

## 8. 恢复原厂固件

```sh
python tools/esp.py --chip esp32c3 --port COM3 --baud 460800 write_flash 0x0 backup/passport_original_8MB.bin
```

备份 SHA-256 记录在 [`../README.md`](../README.md)。

⚠ 这块板**没有 recovery 分区**（实测 `0x700000` 为空白），
所以这份备份是唯一退路，**务必留好副本**。
