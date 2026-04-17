# 共享约定

本文定义了这个仓库中所有 skill 的最小共享接口。

## 工程画像（Project Profile）

每个 skill 都应当读取或写入标准化后的 `Project Profile`。输出可以使用 Markdown 或 YAML，但字段名必须保持稳定。

| 字段 | 是否必需 | 含义 |
| --- | --- | --- |
| `workspace_root` | 是 | 固件工作区的绝对路径。 |
| `workspace_os` | 是 | 宿主操作系统：`linux`、`macos` 或 `windows`。 |
| `build_system` | 是 | 主构建系统，例如 `cmake`。 |
| `toolchain` | 否 | 工具链家族，例如 `gnu-arm`、`clang` 或厂商 SDK。 |
| `target_mcu` | 否 | MCU 家族或更精确的芯片型号。 |
| `board` | 否 | 如果工程绑定某块开发板，则记录板卡名称。 |
| `probe` | 否 | 调试探针家族，例如 `stlink`、`jlink`、`cmsis-dap`。 |
| `artifact_path` | 否 | 默认用于烧录或调试的固件产物路径。 |
| `artifact_kind` | 否 | `elf`、`hex` 或 `bin`。 |
| `openocd_config` | 否 | 按顺序排列的 OpenOCD 配置文件或配置片段列表。 |
| `gdb_executable` | 否 | 首选 GDB 可执行文件。 |
| `serial_port` | 否 | 首选串口设备路径或 COM 口。 |
| `baud_rate` | 否 | 首选串口波特率。 |
| `notes` | 否 | 不值得单独增加结构化字段的简短人工备注。 |

## 动作词

以下动作词在所有 skill 中保持统一语义：

| 动作词 | 含义 |
| --- | --- |
| `detect` | 检查工作区或宿主环境，并填充工程画像 |
| `build` | 配置并编译固件产物 |
| `flash` | 将固件烧录到目标设备 |
| `attach` | 在不默认执行加载步骤的前提下连接调试器 |
| `monitor` | 观察串口或运行期输出 |
| `reset` | 通过当前工具链路复位目标设备 |
| `verify` | 确认产物、探针或烧录状态 |

## 决策规则

- 显式用户输入永远优先于自动探测结果。
- 若已有 `Project Profile`，优先复用，而不是每次都从头探测。
- 在下游工具和用户没有明确要求其他格式时，始终优先 `ELF`，其次 `HEX`，最后 `BIN`。
- 不要猜测 `BIN` 的烧录基地址；地址未知时必须阻塞并询问。
- 如果探测后仍存在多个同样合理的板卡、探针或串口候选，应返回阻塞结果并列出候选项。

## 技能交接约定

当一个 skill 将结果交给下一个 skill 时，应尽量保留这些内容：

- 标准化后的 `Project Profile`
- 已执行过的命令
- 重要输出，例如产物路径和探测到的配置
- 若流程中断，对应的失败分类
- 推荐的下一步 skill

## 命令结果结构（Command Outcome Schema）

每个 skill 的结果都应当归入以下状态之一：

- `success`：请求动作已完成。
- `partial_success`：有部分有效进展，但主目标尚未完全达成。
- `blocked`：由于仍存在高风险未知项，skill 主动停止。
- `failure`：在信息已足够的前提下，动作执行失败。

除状态外，还应至少配套这些字段：

- `summary`：一句话说明发生了什么
- `evidence`：最关键的日志、文件或探测证据
- `next_action`：推荐的下一条命令或下一个 skill
- `failure_category`：当状态不是 `success` 时，使用 [failure-taxonomy.md](/home/leo/work/open-git/em_skill/shared/failure-taxonomy.md) 中的分类

## 最小示例

```yaml
status: success
summary: 已使用 CMake 构建 Debug 固件，并生成 ELF 产物。
project_profile:
  workspace_root: /repo/fw
  workspace_os: linux
  build_system: cmake
  toolchain: gnu-arm
  target_mcu: stm32f429zi
  probe: stlink
  artifact_path: /repo/fw/build/debug/app.elf
  artifact_kind: elf
evidence:
  - cmake preset: debug
  - artifact: /repo/fw/build/debug/app.elf
next_action: flash-openocd
```
