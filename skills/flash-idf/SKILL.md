---
name: flash-idf
description: 当需要通过 ESP-IDF 工具链烧录固件到 ESP32 系列芯片，或启动调试会话时使用。
---

# ESP-IDF 烧录调试

## 适用场景

- 工作区已经具备 ESP-IDF 构建产物，且用户希望烧录到目标板。
- 用户需要擦除 Flash 或重新烧录固件。
- 用户需要通过 JTAG 启动 OpenOCD 调试会话。
- 团队需要一条标准化的 ESP-IDF 烧录流程，并可顺畅交接到串口监视或调试。

## 必要输入

- 工作区路径（含已构建的 `build/` 目录），或包含 `artifact_path` 的 `Project Profile`。
- 串口设备路径和波特率。
- 可选的调试探针配置（用于 JTAG 调试模式）。

## 首次参数确认

首次调用时，必须向用户确认以下参数，不得跳过或自动使用探测值：

- **串口设备**：列出探测到的串口设备供用户选择，即使只有一个也不得自动选择。
- **波特率**：向用户展示默认值 460800 并确认是否需要修改。

当 `Project Profile` 中已记录过上述参数（即非首次），可直接复用，无需再次询问。

## 自动探测

- 扫描系统串口设备：Linux `/dev/ttyUSB*`、`/dev/ttyACM*`，macOS `/dev/cu.usbserial*`、`/dev/cu.wchusbserial*`，Windows `COM*`。
- 读取 `sdkconfig` 中的 Flash 大小和分区表配置。
- 检查 `build/` 目录中是否存在有效的烧录产物（`*.bin`、`flasher_args.json`）。
- 探测结果仅作为候选列表展示给用户，不自动选择。

## 执行步骤

1. 先阅读 [references/usage.md](references/usage.md)，确认本次是环境探测、烧录、擦除，还是启动调试。
2. 若不确定环境状态，先运行自带脚本 [scripts/idf_flasher.py](scripts/idf_flasher.py) 的 `--detect` 模式确认。
3. 使用 `--flash --project <path>` 执行烧录，可通过 `--port` 和 `--baud` 指定串口参数。
4. 若用户需要擦除 Flash，使用 `--erase-flash`。
5. 若用户需要烧录后立即查看串口输出，提示手动运行 `idf.py flash monitor`（交互式长运行命令）。
6. 若用户需要 JTAG 调试，使用 `--debug` 检测并启动 OpenOCD 服务。
7. 读取脚本输出的烧录结果报告，重点关注烧录状态和失败分类。
8. 将烧录配置和结果写回 `Project Profile`。

## 失败分流

- 当 `idf.py` 不可用时，返回 `environment-missing`，提示用户手动安装 ESP-IDF。
- 当串口设备不存在或被占用时，返回 `connection-failure`。
- 当 Linux 用户无串口访问权限时，返回 `permission-problem`，建议添加 `dialout` 组。
- 当 `build/` 目录不存在或产物缺失时，返回 `artifact-missing`，推荐 `build-idf`。
- 当烧录过程中芯片无响应时，返回 `target-response-abnormal`。
- 当存在多个串口设备且无法确定目标时，返回 `ambiguous-context`。

## 平台说明

- 串口设备命名因平台而异：Linux `/dev/ttyUSB0`，macOS `/dev/cu.usbserial-*`，Windows `COM3`。
- Linux 用户可能需要将自己添加到 `dialout` 组：`sudo usermod -aG dialout $USER`。
- ESP32 系列通常使用 USB-UART 桥接芯片（CP2102、CH340），需要安装对应驱动。
- 自带脚本通过 subprocess 调用 `idf.py`，烧录调度路径本身是跨平台的。

## 输出约定

- 输出烧录命令、串口设备、波特率、Flash 大小和烧录状态。
- 在 `Project Profile` 中保留或更新 `serial_port`、`baud_rate`、`idf_target`。
- 根据用户意图推荐下一步 skill：查看运行日志推荐 `serial-monitor`，需要调试推荐 `debug-gdb-openocd`。

## 交接关系

- 当下一步要看运行日志时，将成功烧录结果交给 `serial-monitor`。
- 当用户需要断点调试或崩溃分析时，将结果交给 `debug-gdb-openocd`。
- 当固件产物缺失时，推荐用户先使用 `build-idf` 编译。
