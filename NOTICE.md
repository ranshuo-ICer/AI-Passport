# 第三方组件与来源声明

本仓库包含若干第三方作品，均按其原始许可证使用。**本仓库自身的代码为 MIT（见 `LICENSE`）。**

---

## 1. FoloToy AI Passport 开发基线（`upstream/`）

- 来源：<https://github.com/FoloToy/ai-passport>
- 许可证：MIT
- 形态：官方 **main 分支的源码快照**（无 `.git`、无其它分支）
- 用途：硬件事实来源。`os/passport/config.py` 里的引脚、按键电压窗口、
  ST7789P3 厂商初始化序列、CW2017 寄存器与电池 profile 全部取自该仓库的
  `components/bsp/`，已在文件头逐项注明出处。

## 2. 小智语音助手固件（`firmware/folo-ai-passport-xiaozhi-*.bin`）

- 来源：<https://github.com/FoloToy/folo-ai-passport-xiaozhi>，release `v2.4.2-folo.1`
- 许可证：MIT
- 上游：基于 <https://github.com/78/xiaozhi-esp32> 2.4.2 适配
- 完整性：7,742,498 字节，
  SHA-256 `e19b35ad5ee7d8f9cfa9a22c51a69f25835ec90ce092a798d875915f23b275ba`
  （与官方发布一致，`windows/3-烧录小智固件.bat` 会先校验再烧）

## 3. MicroPython 固件（`firmware/ESP32_GENERIC_C3-*.bin`）

- 来源：<https://micropython.org/download/ESP32_GENERIC_C3/>，v1.29.0
- 许可证：MIT
- 完整性：1,754,736 字节，
  SHA-256 `bf72ed9eb88ad3a8f49d02c3d371f9ea34c90a8a303aeb9e324d8efd4a2a655a`

## 4. ES8311 驱动源码副本（`docs/reference/es8311/`）

- 来源：Espressif `esp_codec_dev` v1.6.2 的 `device/es8311/es8311.c`
- 许可证：Apache-2.0（Espressif）
- 用途：`os/passport/audio.py` 的寄存器序列逐条照抄自该文件，副本放在这里便于对照与复核

## 5. 参考但未随仓库分发的内容

- <https://github.com/FoloToy/ai-passport> 的 `demo/*` 分支
  （stopwatch / cat-themed-pomodoro-timer / rock-paper-scissors / tetris-game /
  claude-buddy-port）：需要自行 `git clone` 获取，本仓库的快照里没有
- `backup/passport_original_8MB.bin`：**原厂固件备份，不随仓库分发**。
  它包含该设备特有的身份数据（`cardid` 分区），只应保存在本地。

---

## 关于设备标识

`README.md` 与 `docs/FACTORY_FIRMWARE.md` 中出现的 MAC 地址
（`4c:11:ae:32:35:c4`）是**测试用设备的标识**，用于说明实测来源。
如果你要公开发布本仓库，可以按需移除或打码。
`docs/FACTORY_FIRMWARE.md` 中涉及的设备密钥与个人信息字段已做隐去处理。
