# ESP-IDF 烧录调试 Skill 用法

这个 skill 自带了一个可执行脚本 [scripts/idf_flasher.py](../scripts/idf_flasher.py)，适合在需要探测串口、执行烧录、擦除 Flash 或启动调试时直接调用。

## 能力概览

- 检测 `idf.py` 可用性和串口设备
- 执行固件烧录（`idf.py flash`）
- 擦除 Flash（`idf.py erase-flash`）
- 检测 JTAG 调试配置
- 输出结构化的烧录结果报告

## 基础用法

```bash
# 探测环境和串口
python3 skills/flash-idf/scripts/idf_flasher.py --detect

# 烧录固件（必须指定串口）
python3 skills/flash-idf/scripts/idf_flasher.py \
  --flash --project /path/to/project \
  --port /dev/ttyUSB0

# 指定串口和波特率烧录
python3 skills/flash-idf/scripts/idf_flasher.py \
  --flash --project /path/to/project \
  --port /dev/ttyUSB0 --baud 921600

# 擦除 Flash
python3 skills/flash-idf/scripts/idf_flasher.py \
  --erase-flash --port /dev/ttyUSB0

# 检测 JTAG 调试配置
python3 skills/flash-idf/scripts/idf_flasher.py --debug --project /path/to/project
```

## 常见模式

### 1. 环境与串口探测

```bash
python3 skills/flash-idf/scripts/idf_flasher.py --detect
```

输出 `idf.py` 状态和已连接的串口设备列表。

### 2. 标准烧录流程

```bash
# 构建（使用 build-idf skill）
python3 skills/build-idf/scripts/idf_builder.py --build --project /repo/fw

# 烧录
python3 skills/flash-idf/scripts/idf_flasher.py --flash --project /repo/fw

# 查看串口输出（用户手动执行）
# idf.py -p /dev/ttyUSB0 monitor
```

### 3. 擦除后重新烧录

```bash
python3 skills/flash-idf/scripts/idf_flasher.py --erase-flash --port /dev/ttyUSB0
python3 skills/flash-idf/scripts/idf_flasher.py --flash --project /repo/fw --port /dev/ttyUSB0
```

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--detect` | 探测环境和串口设备 |
| `--flash` | 执行烧录 |
| `--project` | ESP-IDF 工程目录路径 |
| `--port` | 串口设备路径（如 `/dev/ttyUSB0`、`COM3`） |
| `--baud` | 烧录波特率（默认 460800） |
| `--erase-flash` | 擦除整片 Flash |
| `--debug` | 检测 JTAG 调试配置并启动 OpenOCD |
| `-v`, `--verbose` | 详细输出 |

## 串口设备参考

| 平台 | 常见设备路径 | 芯片 |
|------|-------------|------|
| Linux | `/dev/ttyUSB0` | CP2102、CH340 |
| Linux | `/dev/ttyACM0` | ESP32-S2/S3 USB |
| macOS | `/dev/cu.usbserial-*` | CP2102 |
| macOS | `/dev/cu.wchusbserial-*` | CH340 |
| Windows | `COM3` | 所有 |

## 故障排查

### 串口权限（Linux）

```bash
sudo usermod -aG dialout $USER
# 需要重新登录生效
```

### 串口被占用

关闭其他占用串口的程序（其他 monitor 实例、串口调试工具）。

### 烧录超时

1. 检查 USB 连接
2. 尝试降低波特率：`--baud 115200`
3. 按住 BOOT 按钮再烧录（部分开发板需要手动进入下载模式）

## 返回码

- `0`：操作成功
- `1`：参数非法、环境缺失、串口失败或烧录失败

## 与 Skill 的配合方式

在 `flash-idf` skill 中，推荐工作流是：

1. 先用 `--detect` 确认环境和串口状态
2. 若 `idf.py` 不可用，提示用户手动安装 ESP-IDF
3. 首次使用时，列出探测到的串口设备供用户选择，确认波特率
4. 若构建产物缺失，推荐 `build-idf`
5. 使用用户确认的串口和波特率执行烧录
6. 整理结果摘要，更新 `Project Profile`，交给 `serial-monitor` 或 `debug-gdb-openocd`
