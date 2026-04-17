---
name: debug-gdb-openocd
description: 当需要通过 OpenOCD 启动或附着 GDB 会话，以完成固件下载、在线调试或崩溃现场检查时使用。
---

# GDB OpenOCD 调试

## 适用场景

- 用户希望通过 OpenOCD 调试 Cortex-M 类目标。
- 工作区中已有 `ELF` 和与 OpenOCD 兼容的探针信息。
- 烧录或串口监视流程表明，需要进一步查看断点、停核控制、寄存器或回溯信息。

## 必要输入

- 一份带符号的 `ELF`，或包含 `artifact_path` 的 `Project Profile`。
- OpenOCD 配置信息，或足以安全解析配置的工作区线索。
- 可选调试模式：`download-and-halt`、`attach-only`、`crash-context`。

## 自动探测

- 默认模式为 `download-and-halt`；只有用户显式要求附着调试或崩溃现场检查时才切换。
- GDB 优先级为：显式用户输入、`Project Profile`、`arm-none-eabi-gdb`、`gdb-multiarch`。
- OpenOCD 配置优先级与 `flash-openocd` 保持一致。
- 做符号级调试必须有 `ELF`。若只有 `HEX` 或 `BIN`，应阻塞并要求提供匹配 `ELF`。

## 执行步骤

1. 确认 `openocd`、兼容的 GDB 可执行文件以及带符号的 `ELF` 均存在。
2. 使用解析出的配置列表启动或复用一个 OpenOCD 会话。
3. 让 GDB 以该 `ELF` 为符号文件连接到 `localhost:3333`。
4. 对 `download-and-halt`，先复位并停核，再加载符号与段，并停在安全的初始状态。
5. 对 `attach-only`，连接后不执行加载步骤，除非用户明确要求，否则不要复位。
6. 对 `crash-context`，先以尽量不打扰现场的方式连接，再在改变目标状态前抓取寄存器、线程信息和回溯。
7. 输出精确使用的命令，以及对下一步调试最有价值的关键观察。

## 失败分流

- 当缺少 `openocd` 或兼容 GDB 时，返回 `environment-missing`。
- 当没有可用的 `ELF` 时，返回 `artifact-missing`。
- 当 OpenOCD 或 GDB 无法连接目标板时，返回 `connection-failure`。
- 当 OpenOCD 配置或符号文件与目标不一致时，返回 `project-config-error`。
- 当会话可以建立，但无法停核、加载或得到可信回溯时，返回 `target-response-abnormal`。
- 当存在多个同样合理的探针、配置或符号文件时，返回 `ambiguous-context`。

## 平台说明

- 输出中应将 OpenOCD 与 GDB 命令分开列出，方便用户在其他 shell 或 IDE 中复现。
- Windows 宿主机可能需要解析 `.exe` 后缀，但逻辑流程与其他平台一致。

## 输出约定

- 输出调试模式、OpenOCD 命令、GDB 可执行文件、`ELF` 路径和第一批可执行的观察结论。
- 在 `Project Profile` 中保留 `artifact_path`、`artifact_kind`、`gdb_executable`、`openocd_config`。
- 当复位后或继续运行后下一步是观察运行行为时，推荐 `serial-monitor`。

## 交接关系

- 当目标恢复运行后，需要继续观察运行期日志时，将成功会话交给 `serial-monitor`。
- 只有当工作区元数据、探针选择或符号解析仍不完整时，才将阻塞结果回交给 `project-intake`。
