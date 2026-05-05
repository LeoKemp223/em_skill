[![English](https://img.shields.io/badge/lang-English-blue.svg)](README_EN.md)
[![简体中文](https://img.shields.io/badge/lang-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-red.svg)](README.md)

# Fully Automated AI-Driven Hardware Product Development, Debugging, and Testing

A skill set for AI coding assistants, providing LLMs with full-lifecycle MCU firmware development capabilities. Covers multi-toolchain builds (Keil / IAR / CMake / PlatformIO), flashing, GDB debugging, serial monitoring, Modbus / CAN / VISA protocol debugging, peripheral driver adaptation, and pipeline orchestration — supporting Linux, macOS, and Windows.

<img width="1922" height="1091" alt="image" src="https://github.com/user-attachments/assets/6b23bfb1-8755-4f28-b510-abb7cc80d18f" />

## One-Click Install

In any LLM chat that supports skills, enter:

```
Install skills from https://github.com/LeoKemp223/embed-ai-tool.git
```

The LLM will automatically clone the repository and run the installation script.

## Script Installation (Recommended)

### Prerequisites

- Python 3.8+ (no third-party dependencies required)
- Git

### Install All Skills

```bash
git clone https://github.com/LeoKemp223/embed-ai-tool.git
python3 embed-ai-tool/scripts/install.py /path/to/your-project
```

### Install Specific Skills

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --skills build-cmake flash-openocd serial-monitor
```

### Update Installed Skills

```bash
cd embed-ai-tool && git pull
python3 scripts/install.py /path/to/your-project --force
```

### Auto-Detect Tool Paths

Append `--detect` during installation to automatically scan PATH for embedded tools and write them to the workspace config:

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --detect
```

### Check Installation Status

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --status
```

### Uninstall

```bash
python3 embed-ai-tool/scripts/install.py /path/to/your-project --uninstall
```

### List Available Skills

```bash
python3 embed-ai-tool/scripts/install.py --list
```

### Manual Tool Path Configuration

Some skills depend on external tools (OpenOCD, Keil, arm-none-eabi-gcc, etc.). In addition to `--detect`, you can manually configure them:

```bash
# Set tool path (workspace level)
python3 scripts/em_config.py set openocd /usr/bin/openocd

# Set global tool path
python3 scripts/em_config.py set uv4 "C:\Keil_v5\UV4\UV4.exe" --global

# View configured tools
python3 scripts/em_config.py list

# View config file location
python3 scripts/em_config.py path
```

## Skill List

| Skill | Description |
|-------|-------------|
| `build-cmake` | Configure and build CMake-based MCU firmware projects |
| `build-keil` | Configure and build Keil MDK firmware projects |
| `build-iar` | Configure and build IAR EWARM firmware projects |
| `build-platformio` | Configure and build PlatformIO firmware projects |
| `build-makefile` | Configure and build bare Makefile embedded projects |
| `flash-keil` | Flash firmware via Keil MDK built-in debugger |
| `flash-openocd` | Flash ELF/HEX/BIN artifacts via OpenOCD |
| `flash-platformio` | Flash firmware via PlatformIO upload mechanism |
| `flash-idf` | Flash firmware via ESP-IDF toolchain with JTAG debug support |
| `flash-jlink` | Flash firmware via SEGGER J-Link with RTT log capture |
| `debug-gdb-openocd` | Attach GDB via OpenOCD — supports post-flash debug, attach-only, and crash triage |
| `debug-jlink` | On-chip debugging and crash analysis via J-Link GDB Server |
| `debug-platformio` | Debug via PlatformIO built-in GDB |
| `serial-monitor` | Select serial port and capture runtime logs |
| `modbus-debug` | Modbus RTU/TCP register read/write, slave scanning, and continuous monitoring |
| `can-debug` | CAN bus frame monitoring, sending, and node scanning |
| `visa-debug` | VISA instrument SCPI communication, waveform capture, and screenshots |
| `peripheral-driver` | Search and adapt open-source BSP peripheral drivers to target projects |
| `stm32-hal-development` | STM32 HAL library development guidance and best practices |
| `workflow` | Pipeline orchestration chaining multiple skills (build + flash + monitor/debug) |
| `build-idf` | Configure target chip and build ESP-IDF firmware projects |
| `memory-analysis` | Parse .map files or ELF to generate memory usage reports and symbol size rankings |
| `rtos-debug` | FreeRTOS/RT-Thread/Zephyr thread-aware debugging, stack watermark, and deadlock detection |
| `static-analysis` | cppcheck/clang-tidy/GCC analyzer static analysis with MISRA-C compliance |

## LLM Usage Examples

After installing skills, trigger them with natural language or direct skill commands in your chat.

### Natural Language

```
👤 Build and flash
🤖 Detected Keil project Project.uvprojx, target chip STM32F103C8.
   ✅ Build succeeded, artifact: build/Project.axf (48.2 KB)
   Detected ST-Link V2, flashing...
   ✅ Flash succeeded, verified and reset.

👤 Check serial output
🤖 Detected COM3 (115200), capturing logs:
   [00:00.000] System Start
   [00:00.012] UART initialized

👤 Debug UART
🤖 Breakpoint set at HAL_UART_Transmit entry.
   Breakpoint hit: huart->Init.BaudRate = 9600, expected 115200.
   Suggest checking the baud rate setting in UART initialization code.

```

### Skill Commands

```bash
# Keil project: build + flash + serial
/build-keil
/flash-keil
/serial-monitor

# CMake project: build + flash + debug
/build-cmake
/flash-openocd
/debug-gdb-openocd

# ESP-IDF project: build + flash
/build-idf
/flash-idf

# One-click pipeline (build → flash → monitor)
/workflow
```

## Repository Structure

```text
.
├── skills/                     # Skill modules
│   ├── build-cmake/            # CMake build
│   ├── build-keil/             # Keil build
│   ├── build-iar/              # IAR build
│   ├── build-platformio/       # PlatformIO build
│   ├── flash-keil/             # Keil flash
│   ├── flash-openocd/          # OpenOCD flash
│   ├── flash-platformio/       # PlatformIO flash
│   ├── debug-gdb-openocd/      # GDB debug
│   ├── debug-platformio/       # PlatformIO debug
│   ├── serial-monitor/         # Serial monitor
│   ├── modbus-debug/           # Modbus debug
│   ├── can-debug/              # CAN bus debug
│   ├── visa-debug/             # VISA instrument debug
│   ├── peripheral-driver/      # Peripheral driver adaptation
│   ├── stm32-hal-development/  # STM32 HAL development
│   ├── workflow/               # Pipeline orchestration
│   ├── build-idf/              # ESP-IDF build
│   ├── flash-idf/              # ESP-IDF flash
│   ├── flash-jlink/            # J-Link flash
│   ├── debug-jlink/            # J-Link GDB debug
│   ├── memory-analysis/        # Firmware memory analysis
│   ├── rtos-debug/             # RTOS debug
│   └── static-analysis/        # Static analysis
├── shared/                     # Shared conventions
│   ├── contracts.md            # Context handoff contracts
│   ├── failure-taxonomy.md     # Failure taxonomy
│   ├── platform-compatibility.md
│   ├── project_detect.py       # Unified project detection module
│   └── references/
├── templates/                  # Skill templates
│   └── skill-template/
└── scripts/
    ├── install.py              # Install / uninstall / status check
    ├── validate_repo.py        # Structure validation
    └── em_config.py            # Tool path config CLI
```

<img width="2955" height="1955" alt="PixPin_2026-04-26_22-31-41" src="https://github.com/user-attachments/assets/e62e3118-929e-494c-8d24-c9dcebec22c3" />


## Shared Conventions

All skills share a common set of core context for input and output:

- **Project Profile** — Standardized metadata for workspace, target, build system, debug probe, and artifacts
- **Skill Handoff Contract** — Context that downstream skills can directly inherit
- **Command Outcome Schema** — Unified format for success, failure, or blocked results
- **Failure Taxonomy** — Standard failure classification with recommended follow-up actions

See [shared/contracts.md](shared/contracts.md) and [shared/failure-taxonomy.md](shared/failure-taxonomy.md).

## Validation

After modifications, run structure validation:

```bash
python3 scripts/validate_repo.py
```

The validator checks that all skills have the required files, frontmatter, and section headings.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Use the [templates/skill-template/](templates/skill-template/) template when creating new skills.

## Future Extensions

The repository structure is designed for future expansion — for example, `flash-pyocd`, `vendor-tools`, `fault-triage`, `trace-analysis` — without changes to core conventions.


Thanks to the LinuxDo community for their support!
[![LinuxDo](https://img.shields.io/badge/LinuxDo-Community_Support-blue)](https://linux.do/)
