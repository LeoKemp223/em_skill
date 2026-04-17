---
name: flash-openocd
description: 当需要使用已探测或显式指定的产物与探针配置，通过 OpenOCD 烧录嵌入式固件时使用。
---

# OpenOCD 烧录

## 适用场景

- 工作区已经具备可用固件产物，且用户希望给硬件烧录程序。
- 已探测或用户指定的探针与 OpenOCD 兼容。
- 团队需要一条标准化的 OpenOCD 烧录流程，并可顺畅交接到串口观察或调试。

## 必要输入

- 固件产物路径，或包含 `artifact_path` 的 `Project Profile`。
- OpenOCD 配置信息：显式 `-f` 配置列表、现有 profile 数据，或工作区中唯一明确的配置线索。
- 可选的复位行为和校验偏好。默认开启校验。

## 自动探测

- 按 `ELF > HEX > BIN` 选择固件产物。
- 配置优先级依次为：显式用户输入、现有 `Project Profile`、仓库中的 `openocd*.cfg`、IDE 启动配置线索、唯一明确的板卡或接口组合。
- 若产物为 `BIN`，必须从工作区或用户输入中获得明确的烧录基地址。
- 不要拼接多个“部分匹配”的配置；这种情况应返回 `ambiguous-context`。

## 执行步骤

1. 先确认 `openocd` 可用，且产物路径存在。
2. 按共享优先级规则解析 OpenOCD 配置列表。
3. 选择烧录命令。对 `ELF` 和 `HEX`，优先使用 `program <artifact> verify reset exit`。
4. 对 `BIN`，仅在已知烧录基地址时执行，并显式带上该地址。
5. 在输出结果中记录接口、目标配置、产物路径，以及是否执行校验和复位。
6. 若烧录成功，根据用户意图推荐 `serial-monitor` 查看启动日志，或推荐 `debug-gdb-openocd` 做后续调试。

## 失败分流

- 当 `openocd` 不可用时，返回 `environment-missing`。
- 当无法安全解析到产物，或 `BIN` 缺少烧录基地址时，返回 `artifact-missing`。
- 当 OpenOCD 无法发现探针或目标板时，返回 `connection-failure`。
- 当所选配置文件无效时，返回 `project-config-error`。
- 当烧录开始了，但校验、停核或复位失败时，返回 `target-response-abnormal`。
- 当存在多个同样合理的配置集合或目标时，返回 `ambiguous-context`。

## 平台说明

- 探针访问失败在所有宿主平台上都可能表现为 USB 或驱动问题，只是具体报错文本会随操作系统不同而变化。
- 输出中应保留完整 OpenOCD 配置列表，方便在其他宿主环境中复现同一会话。

## 输出约定

- 输出 OpenOCD 命令、所选配置文件、产物路径，以及是否要求校验和复位。
- 在 `Project Profile` 中保留或更新 `artifact_path`、`artifact_kind`、`openocd_config`。
- 根据用户意图推荐下一步 skill：做启动验证后推荐 `serial-monitor`，需要调试时推荐 `debug-gdb-openocd`。

## 交接关系

- 当下一步要看运行日志时，将成功烧录结果交给 `serial-monitor`。
- 当用户需要断点、停核控制或崩溃分析时，将成功或部分成功的烧录结果交给 `debug-gdb-openocd`。
- 只有当工作区元数据本身缺失或互相矛盾时，才将阻塞结果回交给 `project-intake`。
