# 上游 C 工程与官方开发仓库

本仓库的 [`../upstream/`](../upstream/) 是官方开发仓库
[FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)（MIT）
**main 分支的源码快照**（无 `.git`、无其它分支）。

它是本项目的**硬件事实来源**：`os/passport/config.py` 里的引脚、按键电压窗口、
ST7789P3 厂商序列、CW2017 寄存器与电池 profile 全部取自它的 `components/bsp/`。

> ⚠ **不是路由 A 的固件源码**。路线 A 用的是预编译的
> [`../firmware/`](../firmware/) 镜像，由另一个仓库
> [folo-ai-passport-xiaozhi](https://github.com/FoloToy/folo-ai-passport-xiaozhi) 构建。

---

## 1. 小智固件

- 文件：`firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin`
- 基于 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 2.4.2 适配
- SHA-256：`E19B35AD5EE7D8F9CFA9A22C51A69F25835EC90CE092A798D875915F23B275BA`
- 烧录地址：`0x0`（完整合并镜像）
- 烧录脚本：[windows/3-烧录小智固件.bat](../windows/3-烧录小智固件.bat)

使用：开机后屏幕显示 Wi-Fi 热点名 → 手机连接 → 配网 → 说「你好小智」唤醒。

## 2. 上游 C 工程 `upstream/`

官方开发仓库 [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)（MIT），
基于 **ESP-IDF 5.5.3 + LVGL**。

### 2.1 构建

```sh
cd upstream
idf.py set-target esp32c3
idf.py build
```

### 2.2 目录结构

```
upstream/
├── main/                      ← demo 应用
│   ├── main.c                 ← app_main：初始化 + 菜单 + 按键分发
│   ├── demo.h                 ← demo_entry_t 接口定义
│   ├── demo_navigation.{c,h}  ← 导航状态机
│   ├── demo_*.c               ← 各演示页（display/button/audio/battery/wifi/ble/low_power）
│   └── ui_pixel.{c,h}         ← 像素风 UI 组件（screen/panel/mascot）
├── components/bsp/            ← 板级支持包
│   ├── include/               ← bsp_pins.h（硬件事实）/ bsp_display.h / bsp_button.h / bsp_audio.h / bsp_battery.h / bsp_i2c.h
│   └── src/                   ← 对应实现
├── tests/                     ← 主机测试（Python）+ C 单元测试
├── tools/                     ← validate.sh / check_repo.py / verify_firmware.py 等
└── docs/                      ← 官方开发文档
```

### 2.3 `main.c` 架构

`app_main()` 流程：
1. `bsp_i2c_init()` + 扫描
2. `bsp_display_init()` + `bsp_lvgl_init()`（失败则退出）
3. `demo_navigation_init()`
4. 逐项初始化外设，结果存 `s_ok[]`（失败项菜单标 `[FAIL]`）
5. 建菜单，启动按键分发任务

按键分发：`bsp_button` 回调（在 esp_timer 任务）→ 入队 → `input_task` 消费 → `process_input` → `demo_navigation_handle`。

### 2.4 `demo_entry_t` 接口

```c
typedef struct {
    const char *name;
    void (*enter)(void);          // 持 LVGL 锁创建页面
    void (*exit)(void);           // 停止服务后持锁删除页面
    void (*key)(btn, ev);         // 按键回调
    esp_err_t (*start)(void);     // 可选：启动慢服务（不持锁）
    esp_err_t (*stop)(void);      // 可选：停止 producer（不持锁）
} demo_entry_t;
```

7 个 demo：Display / Button / Audio / Battery / Wi-Fi / BLE / Low Power。

### 2.5 BSP 模块

| 模块 | 头文件 | 职责 |
| --- | --- | --- |
| pins | `bsp_pins.h` | 所有 GPIO / 电压窗口 / I2C 地址（单一事实来源） |
| display | `bsp_display.h` | ST7789P3 SPI 初始化 + 厂商序列 + LEDC 背光 + LVGL 接入 |
| button | `bsp_button.h` | 三键 ADC 读取 + 消抖 + 长按检测 |
| audio | `bsp_audio.h` | ES8311 I2S 全双工 |
| battery | `bsp_battery.h` | CW2017 电量计 |
| i2c | `bsp_i2c.h` | I2C 总线初始化与扫描 |

### 2.6 关键约束（来自 `AGENTS.md`）

- ESP32-C3，8MB Flash，无 PSRAM，ESP-IDF 5.5.3
- LVGL 非线程安全：非 LVGL 任务操作 lv_* 对象必须持 `bsp_lvgl_lock()`
- 按键回调必须非阻塞，慢操作放 worker 任务
- demo 退出前必须停止所有能访问 UI 的任务/定时器/回调
- 中文 UI 文字需自备字库（默认 Montserrat 无中文字形）

---

---

## 3. 本地构建

### 10.4 上游 C 工程构建

```sh
cd upstream
idf.py set-target esp32c3
idf.py build
idf.py -p COMx flash monitor
```
