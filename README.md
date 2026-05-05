[English](README_EN.md) | [中文](README.md)

# 打通AI开发硬件产品的研发、调试、测试全自动化流程。

面向 AI 编程助手的嵌入式开发技能集，为大模型提供 MCU 固件开发全流程能力。涵盖多工具链构建（Keil / IAR / CMake / PlatformIO）、烧录、GDB 调试、串口监视、Modbus / CAN / VISA 协议调试、外设驱动适配及流水线编排，支持 Linux、macOS、Windows 三平台。

<img width="1922" height="1091" alt="image" src="https://github.com/user-attachments/assets/6b23bfb1-8755-4f28-b510-abb7cc80d18f" />

## 一键安装

在任意支持 skill 的大模型对话中输入：

```
帮我安装 https://github.com/LeoKemp223/embed-ai-tool.git 的 skill
```

大模型会自动克隆仓库、运行安装脚本完成配置。

## 脚本安装（推荐）

### 前置条件

- Python 3.8+（无需第三方依赖）
- Git

### 安装所有 skill

```bash
git clone https://github.com/LeoKemp223/embed-ai-tool.git
python3 embed-ai-tool/scripts/install.py /path/to/your-project
```

### 安装指定 skill

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --skills build-cmake flash-openocd serial-monitor
```

### 更新已安装的 skill

```bash
cd embed-ai-tool && git pull
python3 scripts/install.py /path/to/your-project --force
```

### 自动探测工具路径

安装时附加 `--detect`，自动扫描 PATH 中的嵌入式工具并写入工作区配置：

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --detect
```

### 查看安装状态

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --status
```

### 卸载

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --uninstall
```

### 列出可用 skill

```bash
python3 embed-ai-tool/scripts/install.py --list
```

### 手动工具路径配置

部分 skill 依赖外部工具（OpenOCD、Keil、arm-none-eabi-gcc 等），除 `--detect` 外也可手动配置：

```bash
# 设置工具路径（工作区级别）
python3 scripts/em_config.py set openocd /usr/bin/openocd

# 设置全局工具路径
python3 scripts/em_config.py set uv4 "C:\Keil_v5\UV4\UV4.exe" --global

# 查看已配置的工具
python3 scripts/em_config.py list

# 查看配置文件位置
python3 scripts/em_config.py path
```

## 技能列表

| 技能 | 说明 |
|------|------|
| `build-cmake` | 配置并构建基于 CMake 的 MCU 固件工程 |
| `build-keil` | 配置并构建基于 Keil MDK 的固件工程 |
| `build-iar` | 配置并构建基于 IAR EWARM 的固件工程 |
| `build-platformio` | 配置并构建基于 PlatformIO 的固件工程 |
| `flash-keil` | 通过 Keil MDK 内置调试器烧录固件 |
| `flash-openocd` | 通过 OpenOCD 烧录 ELF/HEX/BIN 产物 |
| `flash-platformio` | 通过 PlatformIO 上传机制烧录固件 |
| `debug-gdb-openocd` | 通过 OpenOCD 附着 GDB，支持下载后调试、仅附着和崩溃现场排查 |
| `debug-platformio` | 通过 PlatformIO 内置 GDB 调试 |
| `serial-monitor` | 选择串口并抓取运行日志 |
| `modbus-debug` | Modbus RTU/TCP 寄存器读写、从站扫描和持续监控 |
| `can-debug` | CAN 总线帧监听、发送和节点扫描 |
| `visa-debug` | VISA 仪器 SCPI 通信、波形捕获和截图 |
| `peripheral-driver` | 搜索并适配开源 BSP 外设驱动到目标工程 |
| `stm32-hal-development` | STM32 HAL 库开发指导与最佳实践 |
| `workflow` | 串联多个 skill 的流水线编排（编译+烧录+监控/调试） |
| `build-idf` | 配置目标芯片并构建 ESP-IDF 固件工程 |
| `flash-idf` | 通过 ESP-IDF 工具链烧录固件并支持 JTAG 调试 |
| `flash-jlink` | 通过 SEGGER J-Link 烧录固件，支持 RTT 日志捕获 |
| `debug-jlink` | 通过 J-Link GDB Server 进行固件在线调试和崩溃分析 |
| `memory-analysis` | 解析 .map 文件或 ELF，生成内存使用报告和符号大小排名 |
| `rtos-debug` | FreeRTOS/RT-Thread/Zephyr 线程感知调试，栈水位和死锁检测 |
| `static-analysis` | cppcheck/clang-tidy/GCC analyzer 静态分析，MISRA-C 合规 |

## LLM 使用示例

安装 skill 后，在对话中用自然语言即可触发，也可以直接调用 skill 命令。

### 自然语言

```
👤 编译烧录
🤖 检测到 Keil 工程 Project.uvprojx，目标芯片 STM32F103C8。
   ✅ 编译成功，产物: build/Project.axf (48.2 KB)
   检测到 ST-Link V2，烧录中...
   ✅ 烧录成功，已校验并复位。

👤 看串口
🤖 检测到 COM3 (115200)，抓取日志：
   [00:00.000] System Start
   [00:00.012] UART initialized

👤 调试下 UART
🤖 已在 HAL_UART_Transmit 入口设置断点。
   断点命中: huart->Init.BaudRate = 9600，与预期 115200 不符。
   建议检查 UART 初始化代码中的波特率设置。

```

### Skill 命令

```bash
# Keil 工程：编译 + 烧录 + 串口
/build-keil
/flash-keil
/serial-monitor

# CMake 工程：编译 + 烧录 + 调试
/build-cmake
/flash-openocd
/debug-gdb-openocd

# ESP-IDF 工程：编译 + 烧录
/build-idf
/flash-idf

# 一键流水线（编译 → 烧录 → 监控）
/workflow
```

## 仓库结构

```text
.
├── skills/                     # 技能模块
│   ├── build-cmake/            # CMake 构建
│   ├── build-keil/             # Keil 构建
│   ├── build-iar/              # IAR 构建
│   ├── build-platformio/       # PlatformIO 构建
│   ├── flash-keil/             # Keil 烧录
│   ├── flash-openocd/          # OpenOCD 烧录
│   ├── flash-platformio/       # PlatformIO 烧录
│   ├── debug-gdb-openocd/      # GDB 调试
│   ├── debug-platformio/       # PlatformIO 调试
│   ├── serial-monitor/         # 串口监视
│   ├── modbus-debug/           # Modbus 调试
│   ├── can-debug/              # CAN 总线调试
│   ├── visa-debug/             # VISA 仪器调试
│   ├── peripheral-driver/      # 外设驱动适配
│   ├── stm32-hal-development/  # STM32 HAL 开发
│   ├── workflow/               # 流水线编排
│   ├── build-idf/              # ESP-IDF 构建
│   ├── flash-idf/              # ESP-IDF 烧录
│   ├── flash-jlink/            # J-Link 烧录
│   ├── debug-jlink/            # J-Link GDB 调试
│   ├── memory-analysis/        # 固件内存分析
│   ├── rtos-debug/             # RTOS 调试
│   └── static-analysis/        # 静态分析
├── shared/                     # 共享约定
│   ├── contracts.md            # 上下文交接合约
│   ├── failure-taxonomy.md     # 失败分类
│   ├── platform-compatibility.md
│   ├── project_detect.py       # 统一项目探测模块
│   └── references/
├── templates/                  # Skill 模板
│   └── skill-template/
└── scripts/
    ├── install.py              # 安装 / 卸载 / 状态检查
    ├── validate_repo.py        # 结构校验
    └── em_config.py            # 工具路径配置 CLI
```

<img width="2955" height="1955" alt="PixPin_2026-04-26_22-31-41" src="https://github.com/user-attachments/assets/e62e3118-929e-494c-8d24-c9dcebec22c3" />


## 共享约定

所有 skill 围绕同一套核心上下文进行输入与输出：

- **Project Profile** — 工作区、目标、构建系统、探针和产物的标准化元数据
- **Skill Handoff Contract** — 下游 skill 可直接继承的上下文
- **Command Outcome Schema** — 成功、失败或阻塞结果的统一格式
- **Failure Taxonomy** — 标准失败分类及推荐后续动作

详见 [shared/contracts.md](shared/contracts.md) 和 [shared/failure-taxonomy.md](shared/failure-taxonomy.md)。

## 校验

修改后执行结构校验：

```bash
python3 scripts/validate_repo.py
```

校验器会检查所有 skill 必需文件、frontmatter 和章节标题是否齐全。

## 贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。新 skill 请基于 [templates/skill-template/](templates/skill-template/) 模板创建。

## 后续扩展

仓库结构已为后续扩展预留空间，例如 `flash-pyocd`、`vendor-tools`、`fault-triage`、`trace-analysis`，无需改动核心约定。


感谢 LinuxDo 社区的支持！
[![LinuxDo](https://img.shields.io/badge/LinuxDo-社区支持-blue)](https://linux.do/)


