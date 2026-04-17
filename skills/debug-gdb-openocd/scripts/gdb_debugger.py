#!/usr/bin/env python
"""通用嵌入式 GDB + OpenOCD 调试工具。

这个脚本为 `debug-gdb-openocd` skill 提供可重复调用的执行入口，支持：

- 探测调试环境（OpenOCD、GDB、探针）
- 启动 OpenOCD 后台服务并等待端口就绪
- 生成 GDB 初始化脚本并执行调试命令
- 三种调试模式：download-and-halt、attach-only、crash-context
- 输出结构化的调试结果报告
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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


INTERFACE_CONFIGS = {
    "stlink": "interface/stlink.cfg",
    "cmsis-dap": "interface/cmsis-dap.cfg",
    "daplink": "interface/cmsis-dap.cfg",
    "jlink": "interface/jlink.cfg",
}
INTERFACE_PRIORITY = ["stlink", "cmsis-dap", "jlink"]
GDB_CANDIDATES = ["arm-none-eabi-gdb", "gdb-multiarch"]
DEBUG_MODES = ["download-and-halt", "attach-only", "crash-context"]
DEFAULT_GDB_PORT = 3333


@dataclass
class DebugResult:
    status: str  # success, failure, blocked
    summary: str
    mode: str | None = None
    openocd_cmd: str | None = None
    gdb_cmd: str | None = None
    gdb_executable: str | None = None
    elf_path: str | None = None
    observations: list[str] = field(default_factory=list)
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具探测
# ---------------------------------------------------------------------------

def find_tool(name: str, alt_names: list[str] | None = None) -> tuple[str | None, str | None]:
    # 配置文件
    configured = get_tool_path(name)
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            try:
                r = subprocess.run(
                    [configured_path, "--version"], capture_output=True, text=True, timeout=5,
                )
                ver = (r.stdout or r.stderr).strip().split("\n")[0]
            except Exception:
                ver = None
            return configured_path, ver

    for candidate in [name] + (alt_names or []):
        path = shutil.which(candidate)
        if path:
            try:
                r = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=5,
                )
                ver = (r.stdout or r.stderr).strip().split("\n")[0]
            except Exception:
                ver = None
            return path, ver
    return None, None


def find_gdb(explicit: str | None) -> tuple[str | None, str | None]:
    if explicit:
        path = shutil.which(explicit) or explicit
        try:
            r = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5,
            )
            ver = (r.stdout or r.stderr).strip().split("\n")[0]
        except Exception:
            ver = None
        return path, ver

    # 配置文件
    configured = get_tool_path("arm-none-eabi-gdb")
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            try:
                r = subprocess.run(
                    [configured_path, "--version"], capture_output=True, text=True, timeout=5,
                )
                ver = (r.stdout or r.stderr).strip().split("\n")[0]
            except Exception:
                ver = None
            return configured_path, ver

    return find_tool(GDB_CANDIDATES[0], GDB_CANDIDATES[1:])


def canonical_interface(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    return "cmsis-dap" if lowered == "daplink" else lowered


def detect_probes() -> list[str]:
    if not shutil.which("openocd"):
        return []
    detected: list[str] = []
    for interface in INTERFACE_PRIORITY:
        cfg = INTERFACE_CONFIGS[interface]
        try:
            result = subprocess.run(
                ["openocd", "-f", cfg, "-c", "init; exit"],
                capture_output=True, text=True, timeout=4,
            )
        except Exception:
            continue
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 or any(
            kw in combined for kw in ["cmsis-dap", "st-link", "j-link"]
        ):
            if interface not in detected:
                detected.append(interface)
    return detected


def choose_interface(explicit: str | None, no_detect: bool) -> str | None:
    canonical = canonical_interface(explicit)
    if canonical:
        return canonical
    if no_detect:
        return None
    available = detect_probes()
    if not available:
        return None
    chosen = available[0]
    if len(available) > 1:
        print(f"ℹ️ 检测到多个探针: {', '.join(available)}，默认选择 {chosen}")
    else:
        print(f"ℹ️ 自动检测到探针: {chosen}")
    return chosen


def detect_environment(explicit_gdb: str | None) -> dict[str, Any]:
    ocd_path, ocd_ver = find_tool("openocd", ["openocd.exe"])
    gdb_path, gdb_ver = find_gdb(explicit_gdb)
    probes = detect_probes() if ocd_path else []
    return {
        "openocd": {"available": ocd_path is not None, "path": ocd_path, "version": ocd_ver},
        "gdb": {"available": gdb_path is not None, "path": gdb_path, "version": gdb_ver},
        "probes": probes,
    }


# ---------------------------------------------------------------------------
# OpenOCD 服务管理
# ---------------------------------------------------------------------------

def build_openocd_command(
    interface: str | None,
    configs: list[str],
    targets: list[str],
    gdb_port: int,
) -> list[str] | None:
    cmd: list[str] = ["openocd"]

    if interface:
        icfg = INTERFACE_CONFIGS.get(interface)
        if not icfg:
            print(f"❌ 不支持的调试接口: {interface}")
            return None
        cmd.extend(["-f", icfg])

    for cfg in configs:
        cmd.extend(["-f", cfg])
    for tgt in targets:
        cmd.extend(["-f", tgt])

    if not interface and not configs and not targets:
        print("❌ 调试需要 OpenOCD 配置。")
        print("   请提供 --interface + --target，或 --config。")
        return None

    cmd.extend(["-c", f"gdb_port {gdb_port}"])
    return cmd


def wait_for_port(port: int, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def start_openocd(cmd: list[str], gdb_port: int) -> subprocess.Popen | None:
    cmd_str = " ".join(cmd)
    print(f"🔧 启动 OpenOCD: {cmd_str}")

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("❌ 未找到 openocd 命令")
        return None

    if wait_for_port(gdb_port):
        print(f"✅ OpenOCD 已就绪，GDB 端口: {gdb_port}")
        return proc

    # 检查是否已退出
    ret = proc.poll()
    if ret is not None:
        stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
        print(f"❌ OpenOCD 启动失败 (exit {ret})")
        if stderr.strip():
            for line in stderr.strip().split("\n")[-10:]:
                print(f"  {line}")
    else:
        print("❌ OpenOCD 启动超时，GDB 端口未就绪")
        proc.terminate()

    return None


# ---------------------------------------------------------------------------
# GDB 脚本生成与执行
# ---------------------------------------------------------------------------

def generate_gdb_script(
    mode: str,
    elf_path: str,
    gdb_port: int,
) -> str:
    elf_posix = elf_path.replace("\\", "/")
    lines: list[str] = [
        f"file {elf_posix}",
        f"target extended-remote localhost:{gdb_port}",
    ]

    if mode == "download-and-halt":
        lines.extend([
            "monitor reset halt",
            "load",
            "monitor reset halt",
            "info registers",
            "backtrace",
            "quit",
        ])
    elif mode == "attach-only":
        lines.extend([
            "info registers",
            "backtrace",
            "info threads",
            "quit",
        ])
    elif mode == "crash-context":
        lines.extend([
            "monitor halt",
            "info registers",
            "backtrace full",
            "info threads",
            "print/x *((uint32_t*)0xE000ED28)",  # CFSR
            "print/x *((uint32_t*)0xE000ED2C)",  # HFSR
            "print/x *((uint32_t*)0xE000ED34)",  # MMFAR
            "print/x *((uint32_t*)0xE000ED38)",  # BFAR
            "quit",
        ])

    return "\n".join(lines) + "\n"


def run_gdb(
    gdb_path: str,
    script_content: str,
    verbose: bool,
) -> tuple[bool, list[str], list[str]]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".gdb", delete=False, encoding="utf-8",
    ) as f:
        f.write(script_content)
        script_path = f.name

    cmd = [gdb_path, "--batch", "-x", script_path]
    cmd_str = " ".join(cmd)
    print(f"🔍 GDB 命令: {cmd_str}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, ["❌ GDB 执行超时（30 秒）"], []
    except FileNotFoundError:
        return False, [f"❌ 未找到 GDB: {gdb_path}"], []
    finally:
        Path(script_path).unlink(missing_ok=True)

    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    evidence: list[str] = []
    observations: list[str] = []

    if verbose and output:
        evidence.extend(output.split("\n")[-30:])

    # 提取关键观察
    for line in output.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # 寄存器信息
        if any(reg in stripped.lower() for reg in ["sp ", "pc ", "lr ", "r0 ", "xpsr"]):
            observations.append(stripped)
        # 回溯帧
        elif stripped.startswith("#"):
            observations.append(stripped)
        # Fault 寄存器
        elif "$" in stripped and "0x" in stripped:
            observations.append(stripped)

    if result.returncode != 0:
        if stderr:
            evidence.extend(stderr.split("\n")[-10:])
        return False, evidence, observations

    print("✅ GDB 会话完成")
    return True, evidence, observations


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 调试环境探测结果：")
    for name in ["openocd", "gdb"]:
        info = env[name]
        status = "✅" if info["available"] else "❌"
        ver = f" ({info['version']})" if info.get("version") else ""
        path = f" @ {info['path']}" if info.get("path") else ""
        print(f"  {status} {name}{ver}{path}")

    probes = env.get("probes", [])
    if probes:
        print(f"\n  已连接探针:")
        for p in probes:
            print(f"    - {p}")
    elif env["openocd"]["available"]:
        print("\n  ⚠️ 未检测到已连接的调试探针")


def print_debug_report(result: DebugResult) -> None:
    icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 调试结果: {icon} {result.summary}")

    if result.mode:
        print(f"\n  调试模式:   {result.mode}")
    if result.openocd_cmd:
        print(f"  OpenOCD:    {result.openocd_cmd}")
    if result.gdb_executable:
        print(f"  GDB:        {result.gdb_executable}")
    if result.elf_path:
        print(f"  ELF:        {result.elf_path}")

    if result.observations:
        print(f"\n🔍 关键观察（共 {len(result.observations)} 条）:")
        for obs in result.observations[:20]:
            print(f"  {obs}")

    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:15]:
            print(f"  {line}")

    if result.failure_category:
        print(f"\n  失败分类: {result.failure_category}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嵌入式 GDB + OpenOCD 调试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --elf build/app.elf --interface stlink --target target/stm32f4x.cfg
  %(prog)s --elf build/app.elf --config board/st_nucleo_f4.cfg --mode attach-only
  %(prog)s --elf build/app.elf --interface stlink --target target/stm32f4x.cfg --mode crash-context
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测调试环境")
    parser.add_argument("--elf", help="带符号的 ELF 文件路径")
    parser.add_argument(
        "--mode", choices=DEBUG_MODES, default="download-and-halt",
        help="调试模式（默认 download-and-halt）",
    )
    parser.add_argument("--gdb", help="GDB 可执行文件路径")
    parser.add_argument(
        "--interface",
        choices=["stlink", "cmsis-dap", "daplink", "jlink"],
        help="调试接口",
    )
    parser.add_argument("--target", action="append", default=[], help="OpenOCD 目标配置，可重复")
    parser.add_argument("--config", action="append", default=[], help="额外 OpenOCD -f 配置，可重复")
    parser.add_argument("--no-detect", action="store_true", help="禁止自动探测调试接口")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("--port", type=int, default=DEFAULT_GDB_PORT, help="GDB 服务端口")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 探测模式
    if args.detect:
        env = detect_environment(args.gdb)
        print_detect_report(env)
        if args.save_config:
            if env["openocd"]["available"]:
                cfg_path = set_tool_path("openocd", env["openocd"]["path"])
                print(f"  💾 openocd 已保存到 {cfg_path}")
            if env["gdb"]["available"]:
                cfg_path = set_tool_path("arm-none-eabi-gdb", env["gdb"]["path"])
                print(f"  💾 gdb 已保存到 {cfg_path}")
        ok = env["openocd"]["available"] and env["gdb"]["available"]
        return 0 if ok else 1

    # 调试模式 - 需要 ELF
    if not args.elf:
        print("❌ 请提供 --elf（带符号的 ELF 文件路径）。")
        return 1

    elf_path = str(Path(args.elf).resolve())
    if not Path(elf_path).exists():
        print(f"❌ ELF 文件不存在: {elf_path}")
        return 1
    print(f"📦 ELF: {elf_path}")

    # 检查工具
    ocd_path, _ = find_tool("openocd", ["openocd.exe"])
    if not ocd_path:
        print("❌ 未找到 openocd，请先安装。")
        return 1

    gdb_path, _ = find_gdb(args.gdb)
    if not gdb_path:
        print("❌ 未找到兼容的 GDB（需要 arm-none-eabi-gdb 或 gdb-multiarch）。")
        return 1
    print(f"ℹ️ 使用 GDB: {gdb_path}")

    # 选择接口
    interface = choose_interface(args.interface, args.no_detect)

    # 构建 OpenOCD 命令
    ocd_cmd = build_openocd_command(interface, args.config, args.target, args.port)
    if ocd_cmd is None:
        return 1
    ocd_cmd_str = " ".join(ocd_cmd)

    # 启动 OpenOCD
    ocd_proc = start_openocd(ocd_cmd, args.port)
    if ocd_proc is None:
        result = DebugResult(
            status="failure",
            summary="OpenOCD 启动失败",
            mode=args.mode,
            openocd_cmd=ocd_cmd_str,
            gdb_executable=gdb_path,
            elf_path=elf_path,
            failure_category="connection-failure",
        )
        print_debug_report(result)
        return 1

    # 生成并执行 GDB 脚本
    try:
        script = generate_gdb_script(args.mode, elf_path, args.port)
        ok, evidence, observations = run_gdb(gdb_path, script, args.verbose)

        failure_category = None
        if not ok:
            for line in evidence:
                ll = line.lower()
                if "connection refused" in ll or "remote communication error" in ll:
                    failure_category = "connection-failure"
                    break
                if "no symbol" in ll or "not in executable" in ll:
                    failure_category = "project-config-error"
                    break
            if not failure_category:
                failure_category = "target-response-abnormal"

        result = DebugResult(
            status="success" if ok else "failure",
            summary=f"{args.mode} 会话{'完成' if ok else '失败'}",
            mode=args.mode,
            openocd_cmd=ocd_cmd_str,
            gdb_cmd=f"{gdb_path} --batch -x <script>",
            gdb_executable=gdb_path,
            elf_path=elf_path,
            observations=observations,
            failure_category=failure_category,
            evidence=evidence,
        )
        print_debug_report(result)
        return 0 if ok else 1

    finally:
        ocd_proc.terminate()
        try:
            ocd_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ocd_proc.kill()
        print("🔌 OpenOCD 已关闭")


if __name__ == "__main__":
    sys.exit(main())
