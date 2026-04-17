---
name: project-intake
description: 当需要检查嵌入式固件工作区、识别工程形态，并为构建、烧录、调试或串口监视准备上下文时使用。
---

# 工程识别

## 适用场景

- 当前工作区还没有完成工程画像识别。
- 用户想知道仓库使用的板卡、探针、构建系统或固件产物。
- 下游 skill 在安全执行前，需要一份标准化的工程上下文。

## 必要输入

- 工作区路径。若用户未指定，则默认使用当前仓库根目录。
- 可选提示，例如 MCU 名称、板卡、调试探针或期望的构建目录。

## 自动探测

- 检查 `CMakeLists.txt`、`CMakePresets.json`、`Makefile`、`.vscode/launch.json`、`*.ioc`、`sdkconfig`、`openocd*.cfg` 等根目录线索。
- 先识别构建系统，再识别工具链线索，最后识别目标芯片与调试探针线索。
- 在现有构建目录中搜索 `*.elf`、`*.hex`、`*.bin`。
- 将 README、启动配置和工具链文件作为辅助证据使用。
- 如果存在多个同样合理的板卡、探针或产物候选，返回 `ambiguous-context` 并停止猜测。

## 执行步骤

1. 将工作区根路径和宿主操作系统规范化写入 `Project Profile`。
2. 通过构建文件、链接脚本、HAL 或 SDK 标记以及固件产物，判断仓库是否属于嵌入式工程。
3. 确定主构建系统。首版中只要存在 `cmake` 路径，就优先将其视为支持主线。
4. 从工具链文件、编译器名称和构建预设中提取工具链线索。
5. 从 OpenOCD 配置、IDE 启动文件、板卡名称和文档中提取目标与探针线索。
6. 收集产物候选，并按 `ELF > HEX > BIN` 选择首选产物。
7. 输出标准化 `Project Profile`，并根据用户意图推荐下一步 skill。

## 失败分流

- 当工作区看起来是嵌入式工程，但构建元数据损坏或互相矛盾时，返回 `project-config-error`。
- 当下游流程需要固件产物，但无法安全解析到任何产物时，返回 `artifact-missing`。
- 当探测得到多个有效板卡、探针、预设或产物时，返回 `ambiguous-context`。
- 不要猜测目标板卡或探针，应明确以 `blocked` 停止并等待补充信息。

## 平台说明

- 路径格式和可执行文件后缀规则遵循 [platform-compatibility.md](/home/leo/work/open-git/em_skill/shared/platform-compatibility.md)。
- 只有当工作区或宿主探测足以明确串口信息时，才记录串口名称。

## 输出约定

- 无论信息是否完整，都要返回一份 `Project Profile`。
- 对每个不明显字段给出证据来源，例如推断出探针或产物的文件路径。
- 在 `build-cmake`、`flash-openocd`、`debug-gdb-openocd`、`serial-monitor` 中推荐一个下一步 skill。

## 交接关系

- 面向构建的成功识别结果交给 `build-cmake`。
- 已有有效产物且带有 OpenOCD 线索的结果交给 `flash-openocd` 或 `debug-gdb-openocd`。
- 已明确串口或波特率的结果交给 `serial-monitor`。
