#!/usr/bin/env python
"""通用嵌入式串口监视工具。

这个脚本为 `serial-monitor` skill 提供可重复调用的执行入口，支持：

- 列出可用串口并做设备类型提示
- 自动选择最可能的串口
- 抓取定长日志、等待关键字符串、持续监视
- 保存日志、显示时间戳、简单运行状态分析
- 先监听再复位的启动日志捕获
- 可选通过 OpenOCD 执行自动复位
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import serial as pyserial
    from serial.tools import list_ports

    SERIAL_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - depends on host env
    pyserial = None
    list_ports = None
    SERIAL_IMPORT_ERROR = exc


DEFAULT_BAUD = 115200
DEFAULT_DURATION = 3
ANSI_ESCAPE = re.compile(r"\033\[[0-9;]*m")
ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\[error\]",
        r"\berror\b",
        r"\bfault\b",
        r"\bpanic\b",
        r"\bassert\b",
        r"\bexception\b",
        r"\bfail(?:ed)?\b",
    ]
]
WARNING_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\[warn(?:ing)?\]",
        r"\bwarning\b",
        r"\bwarn\b",
    ]
]
STARTUP_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"system start",
        r"\bboot\b",
        r"reset reason",
        r"firmware version",
        r"build:",
        r"starting",
    ]
]
KEYWORDS = [
    "I2C",
    "SPI",
    "GPIO",
    "UART",
    "CAN",
    "USB",
    "BLE",
    "WiFi",
    "Temperature",
    "Heartbeat",
    "OK",
    "FAIL",
]
INTERFACE_CONFIGS = {
    "stlink": "interface/stlink.cfg",
    "cmsis-dap": "interface/cmsis-dap.cfg",
    "daplink": "interface/cmsis-dap.cfg",
    "jlink": "interface/jlink.cfg",
}
INTERFACE_PRIORITY = ["stlink", "cmsis-dap", "jlink"]


@dataclass
class LogEntry:
    raw: str
    clean: str
    timestamp: float


def require_pyserial() -> bool:
    if pyserial is not None:
        return True

    print("❌ 未安装 pyserial，请先安装：")
    print("   pip install pyserial")
    if SERIAL_IMPORT_ERROR is not None:
        print(f"   导入错误: {SERIAL_IMPORT_ERROR}")
    return False


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def canonical_interface(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    if lowered == "daplink":
        return "cmsis-dap"
    return lowered


def detect_device_label(description: str) -> str:
    desc = description.upper()
    if "CH340" in desc or "CH341" in desc:
        return "USB 转串口"
    if "CMSIS-DAP" in desc or "DAPLINK" in desc or "DAP-LINK" in desc:
        return "CMSIS-DAP"
    if "STLINK" in desc or "ST-LINK" in desc:
        return "ST-Link"
    if "J-LINK" in desc or "JLINK" in desc:
        return "J-Link"
    if "CP210" in desc:
        return "CP210x"
    if "USB SERIAL" in desc or "USB-SERIAL" in desc:
        return "USB 串口"
    return ""


def port_priority(description: str) -> int:
    desc = description.upper()
    if "CH340" in desc or "CH341" in desc or "CP210" in desc:
        return 1
    if "CMSIS-DAP" in desc or "DAPLINK" in desc or "STLINK" in desc or "J-LINK" in desc:
        return 2
    if "USB" in desc:
        return 3
    return 9


def list_serial_ports() -> list[str]:
    if not require_pyserial():
        return []

    ports = list(list_ports.comports())
    if not ports:
        print("❌ 未找到可用串口")
        return []

    print("📡 可用串口：")
    devices: list[str] = []
    for index, port in enumerate(ports, start=1):
        label = detect_device_label(port.description)
        extra = f" [{label}]" if label else ""
        print(f"  {index}. {port.device}: {port.description}{extra}")
        devices.append(port.device)
    return devices


def auto_detect_port() -> str | None:
    if not require_pyserial():
        return None

    candidates = [
        (port_priority(port.description), port.device)
        for port in list_ports.comports()
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def open_serial(port: str, baudrate: int):
    if not require_pyserial():
        return None

    try:
        serial_handle = pyserial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=0.1,
            bytesize=pyserial.EIGHTBITS,
            parity=pyserial.PARITY_NONE,
            stopbits=pyserial.STOPBITS_ONE,
        )
        time.sleep(0.1)
        return serial_handle
    except pyserial.SerialException as exc:
        print(f"❌ 无法打开串口 {port}: {exc}")
        return None


def check_openocd() -> bool:
    return shutil.which("openocd") is not None


def detect_available_debuggers() -> list[str]:
    if not check_openocd():
        return []

    detected: list[str] = []
    seen: set[str] = set()
    for interface in INTERFACE_PRIORITY:
        interface_cfg = INTERFACE_CONFIGS[interface]
        try:
            result = subprocess.run(
                ["openocd", "-f", interface_cfg, "-c", "init; exit"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue

        combined = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 or "cmsis-dap" in combined or "st-link" in combined or "j-link" in combined:
            if interface not in seen:
                seen.add(interface)
                detected.append(interface)
            continue

        if "open failed" in combined or "no device found" in combined or "unable to find" in combined:
            continue

    return detected


def choose_debugger_interface(explicit_interface: str | None, no_detect: bool) -> str | None:
    canonical = canonical_interface(explicit_interface)
    if canonical:
        return canonical

    if no_detect:
        return None

    available = detect_available_debuggers()
    if not available:
        return None

    chosen = available[0]
    if len(available) > 1:
        joined = ", ".join(available)
        print(f"ℹ️ 检测到多个调试器接口: {joined}，默认选择 {chosen}")
    else:
        print(f"ℹ️ 自动检测到调试器接口: {chosen}")
    return chosen


def build_openocd_command(
    interface: str | None,
    extra_configs: Iterable[str],
    target_configs: Iterable[str],
    command: str,
) -> list[str] | None:
    configs: list[str] = []

    if interface:
        interface_cfg = INTERFACE_CONFIGS.get(interface)
        if not interface_cfg:
            print(f"❌ 不支持的调试器接口: {interface}")
            return None
        configs.append(interface_cfg)

    configs.extend(extra_configs)
    configs.extend(target_configs)

    if not configs:
        print("❌ 自动复位需要 OpenOCD 配置。")
        print("   请提供 `--openocd-config` 或 `--openocd-target`。")
        return None

    command_line = ["openocd"]
    for cfg in configs:
        command_line.extend(["-f", cfg])
    command_line.extend(["-c", command])
    return command_line


def reset_device(
    interface: str | None,
    extra_configs: list[str],
    target_configs: list[str],
    command: str,
) -> bool:
    if not check_openocd():
        print("❌ 未找到 OpenOCD，请先安装并确保 `openocd` 在 PATH 中。")
        return False

    openocd_command = build_openocd_command(interface, extra_configs, target_configs, command)
    if openocd_command is None:
        return False

    interface_label = interface or "custom-config"
    print(f"🔄 使用 OpenOCD 复位设备 ({interface_label})...")
    try:
        result = subprocess.run(
            openocd_command,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        print("❌ OpenOCD 执行超时")
        return False
    except FileNotFoundError:
        print("❌ 未找到 OpenOCD 命令")
        return False
    except Exception as exc:  # pragma: no cover - unexpected host env issue
        print(f"❌ OpenOCD 执行失败: {exc}")
        return False

    if result.returncode == 0:
        print("✅ 设备复位成功")
        return True

    print("❌ 设备复位失败：")
    if result.stderr.strip():
        print(result.stderr.strip())
    elif result.stdout.strip():
        print(result.stdout.strip())
    return False


def wait_for_reset(
    ser,
    auto_reset: bool,
    interface: str | None,
    no_detect: bool,
    extra_configs: list[str],
    target_configs: list[str],
    openocd_command: str,
) -> None:
    if ser.in_waiting > 0:
        ser.reset_input_buffer()
        time.sleep(0.05)

    print("⏳ 等待复位模式：已打开串口并开始监听，请触发复位...")

    resolved_interface = choose_debugger_interface(interface, no_detect)
    if auto_reset:
        success = reset_device(resolved_interface, extra_configs, target_configs, openocd_command)
        if not success:
            print("⚠️ 自动复位失败，请手动复位目标板。")
    else:
        print("💡 请现在按下复位键，或通过烧录/调试工具触发复位。")

    deadline = time.time() + 30
    while time.time() < deadline:
        if ser.in_waiting > 0:
            print("✅ 检测到串口数据，开始记录启动日志...")
            return
        time.sleep(0.01)

    print("⚠️ 30 秒内未检测到新的串口数据，将继续读取当前会话输出。")


def read_serial(
    ser,
    duration: int | None,
    clear_first: bool,
    wait_pattern: str | None,
    monitor_mode: bool,
    save_file: str | None,
    show_timestamp: bool,
    keep_buffer: bool,
    wait_reset: bool,
    auto_reset: bool,
    interface: str | None,
    no_detect: bool,
    extra_configs: list[str],
    target_configs: list[str],
    openocd_command: str,
) -> list[LogEntry]:
    if clear_first and not keep_buffer and not wait_reset:
        ser.reset_input_buffer()
        print("🗑️ 已清空串口接收缓冲区")
        time.sleep(0.05)
    elif keep_buffer and ser.in_waiting > 0:
        print(f"📦 保留已有缓冲区数据 ({ser.in_waiting} 字节)")

    if wait_reset:
        wait_for_reset(
            ser,
            auto_reset=auto_reset,
            interface=interface,
            no_detect=no_detect,
            extra_configs=extra_configs,
            target_configs=target_configs,
            openocd_command=openocd_command,
        )

    logs: list[LogEntry] = []
    start_time = time.time()
    line_buffer = ""
    save_handle = None

    if save_file:
        save_path = Path(save_file)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_handle = save_path.open("a", encoding="utf-8")

    if monitor_mode:
        print("📖 交互模式已启动，按 Ctrl+C 结束。")
    else:
        duration_text = "不限时" if duration is None else f"{duration} 秒"
        print(f"📖 开始读取串口 {ser.port}，持续 {duration_text}。")
    print("=" * 70)

    try:
        while True:
            if duration is not None and time.time() - start_time >= duration:
                break

            if ser.in_waiting > 0:
                try:
                    data = ser.read(ser.in_waiting)
                except pyserial.SerialException as exc:
                    raise RuntimeError(f"串口读取失败: {exc}") from exc

                text = data.decode("utf-8", errors="ignore")
                for char in text:
                    if char == "\r":
                        continue
                    if char == "\n":
                        if not line_buffer:
                            continue

                        clean_line = strip_ansi(line_buffer)
                        if show_timestamp:
                            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            display_line = f"[{stamp}] {clean_line}"
                        else:
                            display_line = clean_line

                        print(display_line)
                        logs.append(LogEntry(raw=line_buffer, clean=clean_line, timestamp=time.time()))

                        if save_handle is not None:
                            save_handle.write(clean_line + "\n")
                            save_handle.flush()

                        if wait_pattern and wait_pattern in clean_line:
                            print(f"\n✅ 检测到关键字符串: {wait_pattern}")
                            return logs

                        line_buffer = ""
                        continue

                    line_buffer += char

            if duration is None and not monitor_mode and wait_pattern is None:
                break

            time.sleep(0.002)
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断监视")
        raise
    finally:
        if save_handle is not None:
            save_handle.close()

    print("=" * 70)
    return logs


def matches_any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def parse_log_output(logs: list[LogEntry]) -> dict[str, object]:
    if not logs:
        return {
            "status": "unknown",
            "message": "未接收到任何串口数据",
            "errors": [],
            "warnings": [],
            "info_count": 0,
            "debug_count": 0,
            "has_startup": False,
            "keywords": {},
            "stats": {"total_lines": 0, "error_count": 0, "warning_count": 0},
        }

    errors: list[str] = []
    warnings: list[str] = []
    info_count = 0
    debug_count = 0
    has_startup = False
    keywords: dict[str, int] = {}

    for entry in logs:
        line = entry.clean
        upper_line = line.upper()

        for keyword in KEYWORDS:
            if keyword.upper() in upper_line:
                keywords[keyword] = keywords.get(keyword, 0) + 1

        if matches_any(ERROR_PATTERNS, line):
            errors.append(line)
        elif matches_any(WARNING_PATTERNS, line):
            warnings.append(line)

        if "[INFO]" in upper_line or upper_line.startswith("INFO:"):
            info_count += 1
        if "[DEBUG]" in upper_line or upper_line.startswith("DEBUG:"):
            debug_count += 1
        if matches_any(STARTUP_PATTERNS, line):
            has_startup = True

    if errors:
        status = "error"
        message = f"❌ 检测到 {len(errors)} 条错误相关日志"
    elif warnings:
        status = "warning"
        message = f"⚠️ 检测到 {len(warnings)} 条警告相关日志"
    elif has_startup or info_count > 0 or debug_count > 0:
        status = "success"
        message = f"✅ 串口日志看起来正常（共 {len(logs)} 行）"
    else:
        status = "unknown"
        message = "⚠️ 收到日志，但未识别到明确的健康信号"

    return {
        "status": status,
        "message": message,
        "errors": errors,
        "warnings": warnings,
        "info_count": info_count,
        "debug_count": debug_count,
        "has_startup": has_startup,
        "keywords": keywords,
        "stats": {
            "total_lines": len(logs),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
    }


def print_report(result: dict[str, object], verbose: bool) -> None:
    print(f"\n📊 分析结果: {result['message']}")

    if verbose or result["status"] != "success":
        stats = result["stats"]
        print("\n📈 统计信息:")
        print(f"  总行数: {stats['total_lines']}")
        print(f"  INFO:   {result['info_count']}")
        print(f"  DEBUG:  {result['debug_count']}")
        print(f"  WARN:   {stats['warning_count']}")
        print(f"  ERROR:  {stats['error_count']}")

        keywords = result["keywords"]
        if keywords:
            print("\n🔍 关键词统计:")
            for key, count in sorted(keywords.items(), key=lambda item: (-item[1], item[0]))[:8]:
                print(f"  {key}: {count}")

    errors = result["errors"]
    warnings = result["warnings"]
    if errors:
        print("\n❌ 错误样例:")
        for line in errors[:5]:
            print(f"  {line}")
        if len(errors) > 5:
            print(f"  ... 还有 {len(errors) - 5} 条错误")

    if warnings:
        print("\n⚠️ 警告样例:")
        for line in warnings[:5]:
            print(f"  {line}")
        if len(warnings) > 5:
            print(f"  ... 还有 {len(warnings) - 5} 条警告")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嵌入式串口监视工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --list
  %(prog)s --auto --duration 5
  %(prog)s --port /dev/ttyACM0 --wait "System Start"
  %(prog)s --port COM7 --monitor --timestamp
  %(prog)s --auto --wait-reset --duration 5 --save logs/startup.log

自动复位示例:
  %(prog)s --auto --wait-reset --auto-reset --interface stlink \\
      --openocd-target target/stm32f4x.cfg
  %(prog)s --port /dev/ttyUSB0 --wait-reset --auto-reset \\
      --openocd-config board/st_nucleo_f4.cfg
        """,
    )
    parser.add_argument("--port", help="串口名称，例如 COM7 或 /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="波特率，默认 115200")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="读取时长（秒）")
    parser.add_argument("--list", action="store_true", help="列出可用串口")
    parser.add_argument("--auto", action="store_true", help="自动选择最可能的串口")
    parser.add_argument("--clear", action="store_true", help="读取前清空缓冲区")
    parser.add_argument("--wait", help="等待特定字符串出现后结束")
    parser.add_argument("--monitor", action="store_true", help="持续监视，直到 Ctrl+C")
    parser.add_argument("--save", help="将日志保存到文件")
    parser.add_argument("--timestamp", action="store_true", help="显示时间戳")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细分析")
    parser.add_argument("--keep", action="store_true", help="保留已有缓冲区内容")
    parser.add_argument("--wait-reset", action="store_true", help="先监听，再等待目标复位后抓启动日志")
    parser.add_argument("--auto-reset", action="store_true", help="在等待复位模式下使用 OpenOCD 自动复位")
    parser.add_argument(
        "--interface",
        choices=["stlink", "cmsis-dap", "daplink", "jlink"],
        help="OpenOCD 调试接口；未指定时可自动探测",
    )
    parser.add_argument("--no-detect", action="store_true", help="禁止自动探测调试接口")
    parser.add_argument(
        "--openocd-config",
        action="append",
        default=[],
        help="额外的 OpenOCD -f 配置，可重复传入",
    )
    parser.add_argument(
        "--openocd-target",
        action="append",
        default=[],
        help="OpenOCD target/board 配置，可重复传入",
    )
    parser.add_argument(
        "--openocd-command",
        default="init; reset run; exit",
        help="自动复位时执行的 OpenOCD 命令",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.auto_reset and not args.wait_reset:
        print("❌ `--auto-reset` 必须与 `--wait-reset` 一起使用。")
        return 1

    if args.list:
        list_serial_ports()
        return 0

    port = args.port
    if args.auto or not port:
        port = auto_detect_port()
        if not port:
            print("❌ 无法自动检测串口，请先执行 `--list` 查看候选串口。")
            list_serial_ports()
            return 1
        print(f"✅ 自动检测到串口: {port}")

    serial_handle = open_serial(port, args.baud)
    if serial_handle is None:
        available = list_serial_ports()
        if available:
            print(f"\n💡 提示：可尝试 `--port {available[0]}`。")
        return 1

    print(f"✅ 串口已打开: {serial_handle.port} @ {serial_handle.baudrate} baud")

    try:
        duration = None if args.monitor or args.wait else args.duration
        logs = read_serial(
            serial_handle,
            duration=duration,
            clear_first=args.clear,
            wait_pattern=args.wait,
            monitor_mode=args.monitor,
            save_file=args.save,
            show_timestamp=args.timestamp,
            keep_buffer=args.keep,
            wait_reset=args.wait_reset,
            auto_reset=args.auto_reset,
            interface=args.interface,
            no_detect=args.no_detect,
            extra_configs=args.openocd_config,
            target_configs=args.openocd_target,
            openocd_command=args.openocd_command,
        )
        result = parse_log_output(logs)
        print_report(result, verbose=args.verbose)
        return 0 if result["status"] in {"success", "unknown"} else 1
    except KeyboardInterrupt:
        return 1
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1
    finally:
        serial_handle.close()
        print(f"🔌 串口已关闭: {serial_handle.port}")


if __name__ == "__main__":
    sys.exit(main())
