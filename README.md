# 嵌入式代理技能仓库

这个仓库用于沉淀可复用的 Codex skill，服务嵌入式软件开发场景。目标用户是跨宿主平台交付 MCU 固件的内部团队，首版聚焦主干流程：识别工程、构建固件、烧录硬件、附着调试器以及查看串口日志。

## 目标

- 保持每个 skill 小而清晰，便于组合使用。
- 用统一的工程画像定义 skill 之间的交接上下文。
- 同时支持 Linux、macOS、Windows 宿主工作流，而不是把平台说明复制进每个 skill。
- 当板卡、探针、串口或产物存在歧义时，优先做清晰分流，而不是继续乐观猜测。

## 仓库结构

```text
.
├── skills/
│   ├── project-intake/
│   ├── build-cmake/
│   ├── flash-openocd/
│   ├── serial-monitor/
│   └── debug-gdb-openocd/
├── shared/
│   ├── contracts.md
│   ├── failure-taxonomy.md
│   ├── platform-compatibility.md
│   └── references/
├── templates/
│   └── skill-template/
└── scripts/
    └── validate_repo.py
```

## V1 技能列表

- `project-intake`：识别工程形态、目标线索、调试探针线索以及构建产物。
- `build-cmake`：配置并构建基于 CMake 的 MCU 固件工程。
- `flash-openocd`：通过 OpenOCD 烧录 ELF/HEX/BIN 产物。
- `serial-monitor`：选择串口并抓取运行日志。
- `debug-gdb-openocd`：通过 OpenOCD 附着 GDB，支持下载后调试、仅附着和崩溃现场排查。

## 共享约定

所有 skill 都围绕同一套核心上下文进行输入与输出：

- `Project Profile`：工作区、目标、构建系统、探针和产物的标准化元数据。
- `Skill Handoff Contract`：下游 skill 可以直接继承的上下文。
- `Command Outcome Schema`：成功、失败或阻塞结果的统一格式。
- `Failure Taxonomy`：标准失败分类及推荐后续动作。

开始新增或修改 skill 前，先看 [shared/contracts.md](/home/leo/work/open-git/em_skill/shared/contracts.md) 和 [shared/failure-taxonomy.md](/home/leo/work/open-git/em_skill/shared/failure-taxonomy.md)。

## 编写规则

- 每个 skill 独占 `skills/` 下的一个目录。
- `SKILL.md` 保持简洁，强调动作和决策规则。
- 可复用背景材料放进 `shared/references/`，不要在多个 skill 中重复维护。
- 不要为单个 skill 私自发明新的输出格式；如有必要，应扩展共享约定。
- 当错误的烧录目标可能浪费时间或影响硬件时，应明确要求用户补充信息，而不是静默猜测。

## 校验

修改后执行结构校验：

```bash
python3 scripts/validate_repo.py
```

这个校验器会检查所有 skill 必需文件、frontmatter 和章节标题是否齐全。

设计新 skill 的验收范围时，可结合 [shared/references/acceptance-scenarios.md](/home/leo/work/open-git/em_skill/shared/references/acceptance-scenarios.md) 和 [templates/skill-template/SCENARIOS.md](/home/leo/work/open-git/em_skill/templates/skill-template/SCENARIOS.md)。

## 后续扩展

当前仓库结构已经为后续扩展预留空间，例如 `flash-jlink`、`flash-pyocd`、`vendor-tools`、`fault-triage`、`rtos-debug`、`trace-analysis`，且不需要改动核心约定。
