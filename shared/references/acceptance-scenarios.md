# 验收场景

在评审新增或更新的 skill 时，优先使用以下场景类型。

## 每个技能的最小覆盖

每个 skill 至少应定义以下场景：

- `happy-path`：预期中的嵌入式流程能够在目标明确的前提下成功执行
- `missing-dependency`：缺少必需宿主工具或运行时
- `ambiguous-context`：工作区或宿主环境同时呈现多个合理目标

## 核心嵌入式链路覆盖

这个仓库应持续覆盖以下串联流程：

1. `project-intake -> build-cmake -> flash-openocd -> serial-monitor`
2. `project-intake -> build-cmake -> debug-gdb-openocd`

## 需要保留的失败用例

- 构建报告成功，但没有发现任何固件产物
- OpenOCD 配置文件存在，但与实际目标不匹配
- 探针不存在，或已被其他进程占用
- 串口存在，但无法访问
- 只有 `BIN`，且没有已知烧录基地址
- 用户请求调试，但找不到匹配的 `ELF`

## 评审问题

- skill 是否输出了共享失败分类，而不是自定义标签？
- 当硬件选择存在歧义时，skill 是否选择阻塞而不是继续猜测？
- skill 是否保留了足够证据，以便下一个 skill 继续执行？
- 除非确实改变执行流，平台相关逻辑是否都沉淀在共享参考中？
