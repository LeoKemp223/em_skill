# Makefile 构建 Skill 用法

这个 skill 自带了一个可执行脚本 [scripts/makefile_builder.py](../scripts/makefile_builder.py)，适合在需要自动探测工程配置、执行 Make 构建和定位固件产物时直接调用。

## 能力概览

- 自动探测 make / gmake / mingw32-make 和交叉编译器是否可用
- 查找工作区中的 Makefile / makefile / GNUmakefile
- 解析 Makefile 变量（CROSS_COMPILE、CC、TARGET、MCU、CFLAGS 等）
- 从 CROSS_COMPILE 前缀推断工具链家族（gnu-arm、gnu-riscv、gnu-esp 等）
- 列出 Makefile 中可用的 make 目标
- 执行 make 构建并定位 ELF、HEX、BIN 产物
- 检测 CMake 生成的 Makefile 并提示使用 build-cmake skill

## 基础用法

```bash
# 探测构建环境
python3 skills/build-makefile/scripts/makefile_builder.py --detect

# 解析 Makefile 变量
python3 skills/build-makefile/scripts/makefile_builder.py --parse-makefile --source /path/to/project

# 列出可用目标
python3 skills/build-makefile/scripts/makefile_builder.py --list-targets --source /path/to/project

# 执行构建
python3 skills/build-makefile/scripts/makefile_builder.py --source /path/to/project

# 仅扫描已有产物
python3 skills/build-makefile/scripts/makefile_builder.py --scan-artifacts /path/to/project/build
```

## 常见模式

### 1. 环境探测

```bash
python3 skills/build-makefile/scripts/makefile_builder.py --detect
```

输出 make 版本、交叉编译器等信息，适合在构建前确认环境就绪。

### 2. 解析 Makefile

```bash
python3 skills/build-makefile/scripts/makefile_builder.py \
  --parse-makefile --source /repo/fw
```

输出 CROSS_COMPILE、TARGET、MCU 等变量和推断的工具链信息。

### 3. 执行构建

```bash
python3 skills/build-makefile/scripts/makefile_builder.py \
  --source /repo/fw
```

默认执行 `make all`，构建完成后自动扫描产物。

### 4. 指定目标和并行构建

```bash
python3 skills/build-makefile/scripts/makefile_builder.py \
  --source /repo/fw \
  --target firmware \
  -j 4
```

### 5. 清理后重新构建

```bash
python3 skills/build-makefile/scripts/makefile_builder.py \
  --source /repo/fw \
  --clean
```

### 6. 传递额外变量

```bash
python3 skills/build-makefile/scripts/makefile_builder.py \
  --source /repo/fw \
  --extra-args CROSS_COMPILE=arm-none-eabi- \
  --extra-args MCU=STM32F407
```

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--detect` | 探测构建环境（make、交叉编译器） |
| `--source` | Makefile 源码目录 |
| `--makefile` | 显式指定 Makefile 路径（覆盖自动探测） |
| `--target` | Make 目标名称（默认：all） |
| `--list-targets` | 列出 Makefile 中的可用目标 |
| `--parse-makefile` | 解析并显示 Makefile 变量（不构建） |
| `--build-dir` | 覆盖产物扫描目录 |
| `--scan-artifacts` | 仅扫描指定目录中的固件产物 |
| `--clean` | 构建前执行 make clean |
| `--extra-args` | 传递给 make 的额外参数，可重复 |
| `-v`, `--verbose` | 详细构建输出（V=1） |
| `-j`, `--jobs` | 并行构建任务数 |
| `--save-config` | 探测成功后保存工具路径到配置 |

## 返回码

- `0`：构建成功并找到产物，或探测/解析/列表操作成功
- `1`：参数非法、依赖缺失、构建失败、或未找到产物

## 与 Skill 的配合方式

在 `build-makefile` skill 中，推荐工作流是：

1. 先根据用户输入或 `Project Profile` 决定源码目录
2. 若不确定环境是否就绪，先用 `--detect` 确认
3. 用 `--parse-makefile` 了解工程配置和工具链
4. 选择合适的 make 目标执行构建
5. 将脚本输出的产物路径和构建信息整理成简洁摘要，更新 `Project Profile`，交给 `flash-openocd` 或 `debug-gdb-openocd`
