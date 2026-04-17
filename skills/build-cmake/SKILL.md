---
name: build-cmake
description: 当需要配置或构建基于 CMake 的嵌入式固件工程，并识别构建产物时使用。
---

# 构建 CMake 工程

## 适用场景

- `Project Profile` 中标明 `build_system: cmake`。
- 用户希望对 CMake MCU 工程执行配置、重编译或确认固件产物。
- 烧录或调试流程需要新的 `ELF`、`HEX` 或 `BIN`。

## 必要输入

- 工作区路径，或一份已有的 `Project Profile`。
- 可选的构建预设、构建目录、目标名、生成器和构建类型。

## 自动探测

- 若存在 `CMakePresets.json`，优先使用。
- 否则检查 `CMakeLists.txt`、已有构建目录和工具链文件。
- 若已有成功的构建目录且与当前意图一致，优先复用。
- 生成器优先级为 `Ninja`，其次是宿主机上已安装的原生 Makefile 工具。
- 对调试导向请求默认使用 `Debug`，否则默认使用 `RelWithDebInfo`。

## 执行步骤

1. 若已有 `Project Profile`，优先复用，并确认工程支持 `cmake`。
2. 选择配置方式：若存在匹配预设则使用 `cmake --preset <name>`，否则使用 `cmake -S <src> -B <build_dir>`。
3. 根据可用性和宿主兼容性选择生成器，所有平台均优先 `Ninja`。
4. 当需要工具链文件且预设中未隐含该信息时，显式传入工具链文件。
5. 构建指定目标；若未指定，则构建当前预设或目录下的默认固件目标。
6. 在构建输出中搜索 `ELF`、`HEX`、`BIN`，并记录所有候选产物。
7. 按 `ELF > HEX > BIN` 提升首选产物，并更新 `Project Profile`。

## 失败分流

- 当缺少 `cmake` 或所需生成器时，返回 `environment-missing`。
- 当配置或构建因预设损坏、缺失工具链文件或目标名无效而失败时，返回 `project-config-error`。
- 当构建看似成功但未找到可烧录或可调试产物时，返回 `artifact-missing`。
- 当存在多个同样合理的预设或固件目标，且任意选择都不安全时，返回 `ambiguous-context`。

## 平台说明

- 在 Windows 上，除非工作区明确要求特定 Visual Studio shell，否则优先 `Ninja`，避免依赖特定开发者命令环境。
- 输出中的构建目录应保持为绝对路径，方便下游烧录和调试 skill 直接复用。

## 输出约定

- 输出配置命令、构建命令、构建目录、所选生成器和首选产物路径。
- 用 `artifact_path`、`artifact_kind` 和探测到的工具链细节更新 `Project Profile`。
- 成功后推荐 `flash-openocd` 或 `debug-gdb-openocd`。

## 交接关系

- 当下一步意图是给硬件烧录程序时，将成功构建结果交给 `flash-openocd`。
- 当下一步需要符号信息或调试会话时，将成功构建结果交给 `debug-gdb-openocd`。
- 只有当构建过程暴露出互相矛盾的工程元数据时，才将部分结果回交给 `project-intake`。
