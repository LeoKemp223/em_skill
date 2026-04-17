---
name: serial-monitor
description: 当需要识别正确串口、选择安全的监视命令，并观察固件运行日志时使用。
---

# 串口监视

## 适用场景

- 用户需要查看目标板的启动日志、断言输出或交互式 UART 输出。
- 烧录或复位刚完成，下一步需要观察运行期行为。
- 工作区或 profile 中已经有明确的 UART 端口或波特率。

## 必要输入

- 一个串口，或一份足以完成串口探测的 `Project Profile`。
- 可选的波特率、换行偏好和监视工具偏好。

## 自动探测

- 串口优先级为：显式用户输入、`Project Profile`、新发现的唯一串口，否则阻塞。
- 波特率优先级为：显式用户输入、`Project Profile`、工作区文档或代码常量，最后才回落到 `115200`。
- 若可用，优先使用 `python -m serial.tools.miniterm`，因为它可以跨三种宿主平台工作。
- 若存在多个合理串口候选，返回 `ambiguous-context` 并列出候选列表。

## 执行步骤

1. 识别宿主操作系统，并依据共享平台规则列出串口候选。
2. 按优先级规则解析串口和波特率。
3. 选择监视工具。优先 `python -m serial.tools.miniterm`，必要时再回退到已安装的宿主平台专用工具。
4. 在连接前先告诉用户交互式退出方式。
5. 抓取第一段有价值的运行窗口，总结关键日志模式，并标注复位、断言、Fault 或无输出等现象。
6. 当日志表明需要进一步定位时，推荐下一个 skill。

## 失败分流

- 当没有可用的串口监视工具时，返回 `environment-missing`。
- 当选中的串口无法打开或在监视过程中消失时，返回 `connection-failure`。
- 当宿主机没有权限访问串口设备时，返回 `permission-problem`。
- 当存在多个合理串口候选，或工作区中隐含互相冲突的波特率时，返回 `ambiguous-context`。
- 当串口可访问，但运行行为明显表现为启动失败或重复复位时，返回 `target-response-abnormal`。

## 平台说明

- Linux 与 macOS 的设备命名不同，具体模式应复用 [platform-compatibility.md](/home/leo/work/open-git/em_skill/shared/platform-compatibility.md)，不要在此重复维护。
- Windows 输出中要保留准确的 `COM` 端口名，以及 shell 需要的命令行引号形式。

## 输出约定

- 输出选中的串口、波特率、监视命令以及对观察到日志的简洁总结。
- 当串口和波特率被明确后，用 `serial_port` 和 `baud_rate` 更新 `Project Profile`。
- 当日志表明程序崩溃或卡死时，推荐 `debug-gdb-openocd` 或未来的 `fault-triage`。

## 交接关系

- 当日志显示需要断点或回溯时，将成功监视结果交给 `debug-gdb-openocd`。
- 只有当工作区元数据过弱，无法安全选择设备时，才将串口选择阻塞结果回交给 `project-intake`。
