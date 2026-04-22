---
name: build-idf
description: 当需要配置或构建基于 ESP-IDF 的固件工程，调用自带脚本执行 idf.py build 并定位固件产物时使用。
---

# ESP-IDF 编译

## 适用场景

- `Project Profile` 中标明 `build_system: idf` 或工作区包含 ESP-IDF 工程结构。
- 用户希望对 ESP-IDF 工程执行目标芯片设置、编译或确认固件产物。
- 烧录或调试流程需要新的固件二进制文件。
- 需要在构建前确认 ESP-IDF 环境是否就绪。

## 必要输入

- 工作区路径，或一份已有的 `Project Profile`。
- 目标芯片（esp32、esp32s2、esp32s3、esp32c3、esp32c6、esp32h2 等）。

## 首次参数确认

首次调用时，必须向用户确认以下参数，不得跳过或自动使用探测值：

- **目标芯片**：即使 `sdkconfig` 中已有 `CONFIG_IDF_TARGET`，首次也必须向用户确认。
- **IDF_PATH**：若环境变量已设置，向用户展示当前值并确认是否正确。

当 `Project Profile` 中已记录过上述参数（即非首次），可直接复用，无需再次询问。

## 自动探测

- 检查 `idf.py` 是否可用（`IDF_PATH` 已设置且环境已激活）。
  - ESP-IDF v5.x：通过 `source $IDF_PATH/export.sh` 激活。
  - ESP-IDF v6.0+：通过 EIM（ESP-IDF Installation Manager）激活，如 `source ~/.espressif/tools/activate_idf_vX.Y.Z.sh`。
- 读取 `sdkconfig` 中的 `CONFIG_IDF_TARGET` 作为参考（首次仍需用户确认）。
- 检查 `CMakeLists.txt` 和 `main/` 目录确认 ESP-IDF 工程结构。
- 若已有成功的构建目录且与当前意图一致，优先复用。
- 若目标芯片未设置且 `sdkconfig` 不存在，必须先执行 `set-target`。

## 执行步骤

1. 先阅读 [references/usage.md](references/usage.md)，确认本次是环境探测、设置目标、执行构建，还是仅扫描产物。
2. 若不确定环境是否就绪，先运行自带脚本 [scripts/idf_builder.py](scripts/idf_builder.py) 的 `--detect` 模式确认。
3. 若需要设置或切换目标芯片，使用 `--set-target <chip>` 执行。
4. 使用 `--build --project <path>` 执行构建。
5. 若用户需要修改 sdkconfig，提示手动运行 `idf.py menuconfig`（交互式命令，不可自动执行）。
6. 读取脚本输出的构建结果和产物扫描报告。
7. 将构建目录、产物路径、目标芯片信息写回 `Project Profile`，并在需要时交给下游 skill。

## 失败分流

- 当 `idf.py` 不可用或 `IDF_PATH` 未设置时，返回 `environment-missing`，提示用户手动安装 ESP-IDF。
- 当 `CMakeLists.txt` 或 `main/` 目录缺失时，返回 `project-config-error`。
- 当编译错误（语法错误、链接失败）时，返回 `project-config-error`。
- 当构建成功但未找到固件产物时，返回 `artifact-missing`。
- 当存在多个可能的目标芯片且用户未指定时，返回 `ambiguous-context`。

## 平台说明

- `idf.py` 是跨平台的 Python 脚本，在所有宿主平台上行为一致。
- 自带脚本通过 subprocess 调用 `idf.py`，需要确保 ESP-IDF 环境变量已激活。
- 构建目录默认为工程根目录下的 `build/`。

## 输出约定

- 输出构建命令、构建目录、目标芯片、IDF 版本和首选产物路径。
- 用 `artifact_path`、`artifact_kind`、`idf_target`、`idf_version` 更新 `Project Profile`。
- 成功后推荐 `flash-idf`。

## 交接关系

- 当下一步意图是给硬件烧录程序时，将成功构建结果交给 `flash-idf`。
- 当 ESP-IDF 环境未安装时，提示用户手动安装 ESP-IDF。
