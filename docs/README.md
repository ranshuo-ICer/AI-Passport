# 文档索引

本目录是 AI Passport 工具箱的全部技术文档。每篇只讲**一件事**，
交叉引用的地方给链接而不是复制粘贴 —— 复制出来的第二份一定会漂移。

---

## 按「我想干什么」找

| 我想… | 看这篇 |
| --- | --- |
| 搞清楚这个项目是什么、从哪开始 | [`../README.md`](../README.md) |
| 知道这块板子到底有什么硬件 | [`hardware.md`](hardware.md) |
| 把设备刷成小智语音助手，或刷成 PassportOS | [`flashing.md`](flashing.md) |
| 搞懂 PassportOS 内部怎么组织的 | [`passport-os.md`](passport-os.md) |
| 写一个自己的小程序 | [`ble-protocol.md`](ble-protocol.md) 的「小程序 API」一节 |
| 搞清楚手机端 App 怎么工作的 | [`pwa.md`](pwa.md) |
| 用一个具体工具（部署/刷写/测试/BLE 调试） | [`tools.md`](tools.md) |
| **遇到问题 / 想少踩坑** | [`pitfalls.md`](pitfalls.md) |
| 知道还有哪些已知缺陷没修 | [`known-issues.md`](known-issues.md) |
| 看原厂固件的实测参数（AT 指令、自检、I2S 配置） | [`factory-firmware.md`](factory-firmware.md) |
| 看官方 C 工程（上游 BSP、目录结构、构建方式） | [`upstream.md`](upstream.md) |
| 找小程序的玩法与写新程序的约束 | [`../miniapps/README.md`](../miniapps/README.md) |

---

## 文档清单

| 文档 | 回答什么问题 | 面向谁 |
| --- | --- | --- |
| [`hardware.md`](hardware.md) | 引脚、外设、按键电压窗口、I²C 地址、实测参数 | 要改硬件或写驱动的人 |
| [`flashing.md`](flashing.md) | 两条路线各怎么刷、怎么备份、怎么回退 | 第一次上手的人 |
| [`passport-os.md`](passport-os.md) | 模块划分、启动流程、各文件职责、依赖关系 | 要改 OS 的人 |
| [`ble-protocol.md`](ble-protocol.md) | BLE 命令、分帧、上传流程、小程序 API 与限制 | 要写小程序或客户端的人 |
| [`pwa.md`](pwa.md) | 手机端 App 的结构、连接状态机、推送流程 | 要改手机端的人 |
| [`tools.md`](tools.md) | 每个脚本干什么、怎么用、依赖什么 | 日常使用者 |
| [`pitfalls.md`](pitfalls.md) | **踩过的坑**：现象 → 根因 → 教训，附症状速查 | 所有人，尤其排错时 |
| [`known-issues.md`](known-issues.md) | 尚未修复的缺陷，按严重程度分级 | 所有人 |
| [`factory-firmware.md`](factory-firmware.md) | 原厂固件实测档案（不可再生，烧掉就没了） | 需要对照原厂参数的人 |
| [`upstream.md`](upstream.md) | 官方 C 工程的目录结构、架构与构建方式 | 要动硬件 BSP 或编译 C 固件的人 |
| [`reference/es8311/`](reference/es8311/) | ES8311 官方驱动源码副本（寄存器序列出处） | 要改音频的人 |

---

## 写作约定

为了让文档不再漂移，本仓库的文档遵守这几条：

1. **一件事只写一遍。** 需要引用时用链接，不要复制。
2. **区分「事实」与「推测」。** 真机实测的写清楚是实测值；没验证的必须标
   「未验证」，不能写成好像已经确认了。
3. **改动代码后跑 `python tools/check_docs.py`。** 它会检查链接、数量、
   常量、协议命令、小程序约束是否和代码对得上。它已经抓出过多次漂移。
4. **不写对话体。** 文档是给人查的参考资料，不是过程记录 —— 探索过程和
   失败尝试属于 [`pitfalls.md`](pitfalls.md)，且要写成可复用的教训。
