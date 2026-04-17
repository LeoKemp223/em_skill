---
name: workflow
description: 当需要串联多个 skill 完成编译+烧录+监控或编译+烧录+调试等流水线任务时使用。
---

# Workflow 流水线编排

## 适用场景

- 需要一键完成编译、烧录、串口监控的完整开发流程。
- 需要编译后自动烧录并启动 GDB 调试。
- 需要在不同构建系统（Keil/CMake/PlatformIO）间使用统一的流水线接口。

## 必要输入

- 构建系统类型（keil、cmake、platformio）。
- 工程路径。
- 可选：构建目标、串口、波特率、烧录参数。

## 依赖

- 对应构建系统的 skill 脚本已存在（build-keil、flash-keil 等）。
- 各 skill 的外部依赖已安装（Keil UV4、CMake、PlatformIO CLI 等）。

## 执行步骤

1. 先阅读 [references/usage.md](references/usage.md)，确认参数。
2. 探测环境：
   ```bash
   python scripts/workflow_runner.py --detect
   ```
3. 查看可用流水线：
   ```bash
   python scripts/workflow_runner.py --list
   ```
4. 执行流水线：
   ```bash
   python scripts/workflow_runner.py --run build-flash-monitor --build-system platformio --project /path/to/project
   ```

## 失败分流

- `environment-missing`：对应 skill 脚本不存在。
- `target-response-abnormal`：某个步骤执行失败（编译错误、烧录失败等）。

## 输出约定

示例输出格式：

```
🚀 执行流水线: build-flash-monitor（编译 → 烧录 → 串口监控）
  构建系统: platformio

==================================================
[1/3] 编译
==================================================
  $ python .../platformio_builder.py --project-dir /path

==================================================
[2/3] 烧录
==================================================
  $ python .../pio_flasher.py --flash --project-dir /path

==================================================
[3/3] 串口监控
==================================================
  $ python .../serial_monitor.py --listen --port COM42

📊 结果: ✅ 流水线完成（3 步）
```

## 交接关系

- 编排 `build-keil` / `build-cmake` / `build-platformio` 的编译步骤。
- 编排 `flash-keil` / `flash-openocd` / `flash-platformio` 的烧录步骤。
- 编排 `serial-monitor` 的监控步骤。
- 编排 `debug-gdb-openocd` / `debug-platformio` 的调试步骤。
