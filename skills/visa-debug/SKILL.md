---
name: visa-debug
description: 当需要调试 GPIB/USB/TCP/Serial VISA 仪器通信时使用，支持 SCPI 命令收发、波形捕获、截图和持续监控。
---

# VISA 仪器调试

## 适用场景

- 需要识别和探测连接的 VISA 仪器（示波器、万用表、信号源等）。
- 需要发送 SCPI 命令查询或控制仪器。
- 需要从示波器捕获波形数据并保存为 CSV。
- 需要捕获仪器屏幕截图。
- 需要持续监控某个测量值的变化。

## 必要输入

- VISA 资源字符串（如 `TCPIP::192.168.1.100::INSTR`、`USB0::0x1AB1::0x04CE::DS1ZA1234::INSTR`）。
- SCPI 命令（查询/写入/监控模式需要）。

## 依赖

- `pyvisa`（pip install pyvisa）
- `pyvisa-py`（纯 Python 后端，pip install pyvisa-py）或 NI-VISA 驱动

## 执行步骤

1. 先阅读 [references/usage.md](references/usage.md)，确认操作参数。
2. 探测环境：
   ```bash
   python scripts/visa_tool.py --detect
   ```
3. 根据需求执行操作：
   ```bash
   # 查询仪器标识
   python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --idn

   # 发送 SCPI 查询
   python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --query ":MEAS:VOLT?"

   # 捕获波形
   python scripts/visa_tool.py --resource "TCPIP::192.168.1.100::INSTR" --waveform --output wave.csv
   ```

## 失败分流

- `connection-failure`：VISA 资源未找到或无法打开连接。
- `timeout`：仪器未响应。
- `command-error`：SCPI 命令被仪器拒绝。
- `data-error`：波形或截图数据传输失败。

## 输出约定

示例输出格式：

```
结果: ✅ Rigol Technologies,DS1054Z,DS1ZA1234,00.04.04.SP4
  资源: TCPIP::192.168.1.100::INSTR
```

## 交接关系

- 从 `build-keil` / `build-platformio` 烧录固件后，用此 skill 验证硬件输出信号。
- 与 `serial-monitor` 互补：serial-monitor 查看串口调试输出，visa-debug 进行仪器级测量验证。
- 与 `modbus-debug` / `can-debug` 互补：协议调试配合仪器测量。
