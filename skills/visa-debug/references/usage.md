# VISA 仪器调试 Skill 用法

## 基础用法

```bash
# 探测环境和 VISA 资源
python scripts/visa_tool.py --detect

# 查询仪器标识
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --idn

# 发送 SCPI 查询
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --query ":MEAS:VOLT?"

# 发送 SCPI 写入命令
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --write ":OUTP ON"

# 读取测量值（解析为数值）
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --read-register ":MEAS:FREQ?"

# 捕获示波器波形（ASCII 模式）
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --waveform --output wave.csv

# 捕获波形（二进制模式，指定通道）
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --waveform --wav-format byte --wav-channel CHAN2

# 捕获仪器截图
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --screenshot --output screen.png

# 持续监控测量值
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --monitor ":MEAS:FREQ?" --interval 2

# JSON 格式输出
python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --query ":MEAS:VOLT?" --format json

# 使用 pyvisa-py 后端
python scripts/visa_tool.py --detect --backend "@py"
```

## 参数说明

### 模式参数

| 参数 | 说明 |
| --- | --- |
| `--detect` | 探测 pyvisa 环境和 VISA 资源 |
| `--idn` | 查询 *IDN? 仪器标识 |
| `--query` | 发送 SCPI 查询命令 |
| `--write` | 发送 SCPI 写入命令 |
| `--read-register` | 读取测量值（解析为数值） |
| `--waveform` | 捕获示波器波形数据 |
| `--screenshot` | 捕获仪器屏幕截图 |
| `--monitor` | 持续监控 SCPI 查询值 |

### 连接参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--resource` | — | VISA 资源字符串 |
| `--timeout` | 5000 | 超时毫秒 |
| `--backend` | 自动 | PyVISA 后端（@py 或 @ivi） |

### 输出参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--format` | table | 输出格式：table、raw、json |
| `--output` | — | 波形 CSV 或截图文件保存路径 |

### 波形参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--wav-format` | ascii | 波形数据格式：ascii、byte |
| `--wav-channel` | CHAN1 | 波形通道 |

### 监控参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--interval` | 1.0 | 监控间隔秒数 |
| `--duration` | 0 | 监控持续秒数（0=无限） |

## 常见 VISA 资源字符串

| 接口 | 资源字符串示例 | 说明 |
| --- | --- | --- |
| TCP/IP | TCPIP::192.168.1.100::INSTR | LAN 连接仪器 |
| USB | USB0::0x1AB1::0x04CE::DS1ZA1234::INSTR | USB-TMC 仪器 |
| GPIB | GPIB0::1::INSTR | GPIB 总线仪器 |
| Serial | ASRL3::INSTR | 串口仪器（COM3） |

## 返回码

- `0`：操作成功
- `1`：连接失败、命令错误或参数错误
