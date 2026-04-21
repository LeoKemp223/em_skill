# ESP-IDF 编译 Skill 用法

这个 skill 自带了一个可执行脚本 [scripts/idf_builder.py](../scripts/idf_builder.py)，适合在需要探测环境、设置目标芯片、执行构建或扫描产物时直接调用。

## 能力概览

- 检测 ESP-IDF 环境和 `idf.py` 可用性
- 设置目标芯片（set-target）
- 执行完整构建流程
- 扫描构建产物（.bin、.elf）
- 执行清理操作
- 输出结构化的构建结果报告

## 基础用法

```bash
# 探测 ESP-IDF 构建环境
python3 skills/build-idf/scripts/idf_builder.py --detect

# 设置目标芯片
python3 skills/build-idf/scripts/idf_builder.py --set-target esp32s3 --project /path/to/project

# 构建工程
python3 skills/build-idf/scripts/idf_builder.py --build --project /path/to/project

# 仅扫描构建产物
python3 skills/build-idf/scripts/idf_builder.py --scan-artifacts /path/to/project/build

# 清理构建目录
python3 skills/build-idf/scripts/idf_builder.py --clean --project /path/to/project
```

## 常见模式

### 1. 环境探测

```bash
python3 skills/build-idf/scripts/idf_builder.py --detect
```

输出 `idf.py` 路径、IDF 版本、支持的目标芯片列表。

### 2. 首次构建

```bash
# 设置目标芯片
python3 skills/build-idf/scripts/idf_builder.py --set-target esp32 --project /repo/fw

# 构建
python3 skills/build-idf/scripts/idf_builder.py --build --project /repo/fw
```

### 3. 重新构建

```bash
python3 skills/build-idf/scripts/idf_builder.py --build --project /repo/fw
```

### 4. 清理后重建

```bash
python3 skills/build-idf/scripts/idf_builder.py --clean --project /repo/fw
python3 skills/build-idf/scripts/idf_builder.py --build --project /repo/fw
```

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--detect` | 探测 ESP-IDF 构建环境 |
| `--build` | 执行构建 |
| `--project` | ESP-IDF 工程目录路径 |
| `--set-target` | 设置目标芯片（esp32、esp32s2、esp32s3、esp32c3、esp32c6、esp32h2） |
| `--clean` | 执行 fullclean |
| `--scan-artifacts` | 仅扫描指定目录中的构建产物 |
| `-v`, `--verbose` | 详细输出 |

## 返回码

- `0`：操作成功
- `1`：参数非法、环境缺失、构建失败或产物缺失

## 与 Skill 的配合方式

在 `build-idf` skill 中，推荐工作流是：

1. 先用 `--detect` 确认 ESP-IDF 环境就绪
2. 若环境未就绪，提示用户手动安装 ESP-IDF
3. 首次使用时，向用户确认目标芯片，即使 `sdkconfig` 中已有值也需确认
4. 用 `--set-target` 设置目标芯片
5. 执行 `--build` 构建
6. 将构建结果整理成简洁摘要
7. 更新 `Project Profile`，交给 `flash-idf`
