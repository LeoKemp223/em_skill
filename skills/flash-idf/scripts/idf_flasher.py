#!/usr/bin/env python3
"""ESP-IDF 烧录调试工具。

为 flash-idf skill 提供可重复调用的执行入口，支持：
- 探测串口设备和 idf.py 可用性
- 执行 idf.py flash
- 擦除 Flash
- 检测 JTAG 调试配置
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _SCRIPT_DIR.parent.parent
for _candidate in [_SKILLS_DIR / "shared", _SKILLS_DIR.parent / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
from tool_config import get_tool_path, set_tool_path
from idf_env import get_idf_env

@dataclass
class FlashResult:
    status: str
    summary: str
    flash_cmd: str | None = None
    port: str | None = None
    baud: int | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


def detect_serial_ports() -> list[str]:
    ports: list[str] = []
    system = platform.system()
    if system == "Linux":
        for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
            ports.extend(sorted(glob.glob(pattern)))
    elif system == "Darwin":
        for pattern in ["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.usbmodem*"]:
            ports.extend(sorted(glob.glob(pattern)))
    elif system == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command", "[System.IO.Ports.SerialPort]::GetPortNames()"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                ports = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        except Exception:
            for i in range(1, 20):
                ports.append(f"COM{i}")
    return ports


def _check_port_permission(port: str) -> bool:
    if platform.system() != "Linux":
        return True
    try:
        return os.access(port, os.R_OK | os.W_OK)
    except Exception:
        return False


def _has_build_artifacts(project_dir: Path) -> bool:
    build_dir = project_dir / "build"
    if not build_dir.exists():
        return False
    flasher_args = build_dir / "flasher_args.json"
    return flasher_args.exists()


def flash_project(project_dir: Path, port: str | None, baud: int = 460800,
                   verbose: bool = False) -> FlashResult:
    idf_env = get_idf_env()
    if not idf_env:
        return FlashResult(status="failure", summary="idf.py 不可用",
                           failure_category="environment-missing")

    if not _has_build_artifacts(project_dir):
        return FlashResult(status="failure", summary="构建产物缺失，请先执行构建",
                           failure_category="artifact-missing",
                           evidence=[f"未找到 {project_dir / 'build' / 'flasher_args.json'}"])

    cmd = idf_env.idf_py_cmd + ["-b", str(baud)]
    if port:
        cmd.extend(["-p", port])
    cmd.append("flash")
    if verbose:
        cmd.append("-v")
    cmd_str = " ".join(cmd)
    print(f"⚡ 烧录命令: {cmd_str}")

    if port and platform.system() == "Linux" and not _check_port_permission(port):
        return FlashResult(status="failure", summary=f"无权访问 {port}",
                           flash_cmd=cmd_str, port=port, baud=baud,
                           failure_category="permission-problem",
                           evidence=["建议执行: sudo usermod -aG dialout $USER 并重新登录"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                cwd=str(project_dir), env=idf_env.env)
    except subprocess.TimeoutExpired:
        return FlashResult(status="failure", summary="烧录超时（120 秒）",
                           flash_cmd=cmd_str, port=port, baud=baud,
                           failure_category="target-response-abnormal")

    evidence: list[str] = []
    output = (result.stdout + "\n" + result.stderr).strip()

    if result.returncode != 0:
        last_lines = output.split("\n")[-15:]
        evidence.extend(last_lines)
        category = "connection-failure"
        if "Permission denied" in output:
            category = "permission-problem"
        elif "No serial ports found" in output or "could not open port" in output.lower():
            category = "connection-failure"
        return FlashResult(status="failure", summary="烧录失败", flash_cmd=cmd_str,
                           port=port, baud=baud, failure_category=category, evidence=evidence)

    print("✅ 烧录成功")
    return FlashResult(status="success", summary="烧录成功", flash_cmd=cmd_str,
                       port=port, baud=baud, evidence=evidence)


def erase_flash(port: str | None, baud: int = 460800) -> FlashResult:
    idf_env = get_idf_env()
    if not idf_env:
        return FlashResult(status="failure", summary="idf.py 不可用",
                           failure_category="environment-missing")

    cmd = idf_env.idf_py_cmd + ["-b", str(baud)]
    if port:
        cmd.extend(["-p", port])
    cmd.append("erase-flash")
    cmd_str = " ".join(cmd)
    print(f"🗑️ 擦除命令: {cmd_str}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                env=idf_env.env)
    except subprocess.TimeoutExpired:
        return FlashResult(status="failure", summary="擦除超时",
                           flash_cmd=cmd_str, failure_category="target-response-abnormal")

    if result.returncode != 0:
        return FlashResult(status="failure", summary="擦除失败", flash_cmd=cmd_str,
                           failure_category="connection-failure",
                           evidence=(result.stderr or result.stdout).strip().split("\n")[-10:])

    print("✅ Flash 擦除成功")
    return FlashResult(status="success", summary="Flash 擦除成功", flash_cmd=cmd_str,
                       port=port, baud=baud)


def detect_debug_config(project_dir: Path) -> dict[str, Any]:
    openocd = shutil.which("openocd")
    sdkconfig = project_dir / "sdkconfig"
    jtag_hint = None
    if sdkconfig.exists():
        try:
            content = sdkconfig.read_text(encoding="utf-8", errors="ignore")
            if "CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y" in content:
                jtag_hint = "usb_serial_jtag"
        except OSError:
            pass
    return {
        "openocd_available": openocd is not None,
        "openocd_path": openocd,
        "jtag_hint": jtag_hint,
    }


def print_detect_report(ports: list[str], idf_available: bool, debug_info: dict | None) -> None:
    print("\n📊 ESP-IDF 烧录环境探测结果：")
    status = "✅" if idf_available else "❌"
    print(f"  {status} idf.py: {'可用' if idf_available else '不可用'}")

    if ports:
        print(f"\n  🔌 检测到 {len(ports)} 个串口设备：")
        for p in ports:
            perm = ""
            if platform.system() == "Linux":
                perm = " ✅" if _check_port_permission(p) else " ⚠️ 无权限"
            print(f"    {p}{perm}")
    else:
        print("\n  ⚠️ 未检测到串口设备")

    if debug_info:
        ocd = "✅" if debug_info["openocd_available"] else "❌"
        print(f"\n  {ocd} OpenOCD: {debug_info.get('openocd_path', '未找到')}")
        if debug_info.get("jtag_hint"):
            print(f"  JTAG 提示: {debug_info['jtag_hint']}")


def print_flash_result(result: FlashResult) -> None:
    status_icon = {"success": "✅", "failure": "❌"}.get(result.status, "❓")
    print(f"\n📊 烧录结果: {status_icon} {result.summary}")
    if result.flash_cmd:
        print(f"  烧录命令: {result.flash_cmd}")
    if result.port:
        print(f"  串口设备: {result.port}")
    if result.baud:
        print(f"  波特率:   {result.baud}")
    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:15]:
            print(f"  {line}")
    if result.failure_category:
        print(f"\n  失败分类: {result.failure_category}")
    if result.status == "success":
        print(f"\n💡 下一步：查看串口输出请手动执行：")
        port_arg = f" -p {result.port}" if result.port else ""
        print(f"  idf.py{port_arg} monitor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ESP-IDF 烧录调试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --flash --project /repo/fw
  %(prog)s --flash --project /repo/fw --port /dev/ttyUSB0 --baud 921600
  %(prog)s --erase-flash --port /dev/ttyUSB0
  %(prog)s --debug --project /repo/fw
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测环境和串口设备")
    parser.add_argument("--flash", action="store_true", help="执行烧录")
    parser.add_argument("--project", help="ESP-IDF 工程目录路径")
    parser.add_argument("--port", help="串口设备路径")
    parser.add_argument("--baud", type=int, default=460800, help="烧录波特率（默认 460800）")
    parser.add_argument("--erase-flash", action="store_true", help="擦除整片 Flash")
    parser.add_argument("--debug", action="store_true", help="检测 JTAG 调试配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.detect:
        idf_env = get_idf_env()
        ports = detect_serial_ports()
        debug_info = None
        if args.project:
            debug_info = detect_debug_config(Path(args.project).resolve())
        print_detect_report(ports, idf_env is not None, debug_info)
        return 0

    if args.erase_flash:
        result = erase_flash(args.port, args.baud)
        print_flash_result(result)
        return 0 if result.status == "success" else 1

    if args.debug:
        if not args.project:
            print("❌ 请通过 --project 指定工程目录")
            return 1
        info = detect_debug_config(Path(args.project).resolve())
        print("\n📊 JTAG 调试配置：")
        if info["openocd_available"]:
            print(f"  ✅ OpenOCD: {info['openocd_path']}")
            print(f"\n💡 启动调试服务请手动执行：")
            print(f"  idf.py openocd")
            print(f"  # 然后在另一个终端：")
            print(f"  idf.py gdb")
        else:
            print("  ❌ OpenOCD 未安装")
        return 0 if info["openocd_available"] else 1

    if args.flash:
        if not args.project:
            print("❌ 请通过 --project 指定工程目录")
            return 1
        project_dir = Path(args.project).resolve()
        port = args.port
        if not port:
            ports = detect_serial_ports()
            if ports:
                print(f"⚠️ 检测到以下串口设备，请通过 --port 指定：")
                for p in ports:
                    print(f"  {p}")
            else:
                print("⚠️ 未检测到串口设备，请通过 --port 手动指定")
            return 1
        result = flash_project(project_dir, port, args.baud, verbose=args.verbose)
        print_flash_result(result)
        return 0 if result.status == "success" else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
