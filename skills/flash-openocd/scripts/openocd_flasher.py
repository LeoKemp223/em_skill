#!/usr/bin/env python
"""通用嵌入式 OpenOCD 烧录工具。

这个脚本为 `flash-openocd` skill 提供可重复调用的执行入口，支持：

- 探测 OpenOCD 环境和已连接调试探针
- 扫描工作区中的 OpenOCD 配置线索
- 验证固件产物并自动识别类型
- 组装并执行 OpenOCD 烧录命令
- 支持 ELF/HEX 直接烧录和 BIN 带地址烧录
- 输出结构化的烧录结果报告
"""

from __future__ import annotations

import argparse
import os
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


ARTIFACT_EXTENSIONS = {".elf": "elf", ".hex": "hex", ".bin": "bin", ".axf": "elf"}
ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}
INTERFACE_CONFIGS = {
    "stlink": "interface/stlink.cfg",
    "cmsis-dap": "interface/cmsis-dap.cfg",
    "daplink": "interface/cmsis-dap.cfg",
    "jlink": "interface/jlink.cfg",
}
INTERFACE_PRIORITY = ["stlink", "cmsis-dap", "jlink"]

@dataclass
class FlashResult:
    status: str  # success, failure, blocked
    summary: str
    command: str | None = None
    interface: str | None = None
    target_config: str | None = None
    artifact_path: str | None = None
    artifact_kind: str | None = None
    verified: bool = False
    reset: bool = False
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# OpenOCD 探测
# ---------------------------------------------------------------------------

def check_openocd() -> tuple[bool, str | None]:
    # 配置文件
    configured = get_tool_path("openocd")
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            try:
                result = subprocess.run(
                    [configured_path, "--version"], capture_output=True, text=True, timeout=5,
                )
                version = (result.stdout or result.stderr).strip().split("\n")[0]
                return True, version
            except Exception:
                return True, None

    path = shutil.which("openocd")
    if not path:
        return False, None
    try:
        result = subprocess.run(
            ["openocd", "--version"], capture_output=True, text=True, timeout=5,
        )
        version = (result.stdout or result.stderr).strip().split("\n")[0]
        return True, version
    except Exception:
        return True, None


def canonical_interface(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    if lowered == "daplink":
        return "cmsis-dap"
    return lowered


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
            continue

        if any(kw in combined for kw in ["open failed", "no device found", "unable to find"]):
            continue

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


# ---------------------------------------------------------------------------
# 产物验证
# ---------------------------------------------------------------------------

def identify_artifact(artifact_path: str) -> tuple[str | None, int]:
    p = Path(artifact_path)
    if not p.exists():
        return None, 0
    ext = p.suffix.lower()
    kind = ARTIFACT_EXTENSIONS.get(ext)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    return kind, size


# ---------------------------------------------------------------------------
# 工作区配置扫描
# ---------------------------------------------------------------------------

def scan_openocd_configs(workspace: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    # openocd*.cfg 文件
    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > 3:
            continue
        for fname in files:
            if re.match(r"openocd.*\.cfg$", fname, re.IGNORECASE):
                fpath = Path(root) / fname
                results.append({"source": "config-file", "path": str(fpath)})

    # .vscode/launch.json
    launch_json = workspace / ".vscode" / "launch.json"
    if launch_json.exists():
        try:
            import json
            data = json.loads(launch_json.read_text(encoding="utf-8", errors="ignore"))
            for config in data.get("configurations", []):
                cfg_files = config.get("configFiles") or config.get("serverArgs") or []
                if isinstance(cfg_files, list):
                    for cf in cfg_files:
                        if isinstance(cf, str) and ".cfg" in cf:
                            results.append({"source": "launch.json", "path": cf})
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# 烧录命令组装与执行
# ---------------------------------------------------------------------------

def build_flash_command(
    interface: str | None,
    configs: list[str],
    targets: list[str],
    artifact: str,
    artifact_kind: str,
    base_address: str | None,
    verify: bool,
    reset: bool,
    custom_command: str | None,
) -> list[str] | None:
    cmd: list[str] = ["openocd"]

    # 接口配置
    if interface:
        icfg = INTERFACE_CONFIGS.get(interface)
        if not icfg:
            print(f"❌ 不支持的调试接口: {interface}")
            return None
        cmd.extend(["-f", icfg])

    # 额外配置
    for cfg in configs:
        cmd.extend(["-f", cfg])

    # 目标配置
    for tgt in targets:
        cmd.extend(["-f", tgt])

    # 检查是否有任何配置
    has_config = interface or configs or targets
    if not has_config:
        print("❌ 烧录需要 OpenOCD 配置。")
        print("   请提供 --interface + --target，或 --config。")
        return None

    # 烧录命令
    if custom_command:
        cmd.extend(["-c", custom_command])
    else:
        program_parts = [f"program {artifact}"]
        if artifact_kind == "bin" and base_address:
            program_parts.append(base_address)
        if verify:
            program_parts.append("verify")
        if reset:
            program_parts.append("reset")
        program_parts.append("exit")
        flash_cmd = "init; " + " ".join(program_parts)
        cmd.extend(["-c", flash_cmd])

    return cmd


def run_flash(cmd: list[str], verbose: bool) -> tuple[bool, list[str]]:
    cmd_str = " ".join(cmd)
    print(f"⚡ 烧录命令: {cmd_str}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, ["❌ OpenOCD 烧录超时（60 秒）"]
    except FileNotFoundError:
        return False, ["❌ 未找到 openocd 命令"]

    combined = result.stdout + "\n" + result.stderr
    evidence: list[str] = []

    if verbose:
        for line in combined.strip().split("\n")[-30:]:
            if line.strip():
                evidence.append(line.strip())

    # 判断烧录结果
    combined_lower = combined.lower()
    if result.returncode == 0 and "verified" in combined_lower:
        print("✅ 烧录成功，校验通过")
        return True, evidence
    if result.returncode == 0:
        print("✅ 烧录成功")
        return True, evidence

    # 失败分析
    last_lines = combined.strip().split("\n")[-15:]
    evidence.extend(last_lines)

    if "open failed" in combined_lower or "no device found" in combined_lower:
        evidence.insert(0, "failure_hint: connection-failure (探针未连接或驱动问题)")
    elif "target not examined" in combined_lower or "not halted" in combined_lower:
        evidence.insert(0, "failure_hint: target-response-abnormal")
    elif "invalid command" in combined_lower or "can't find" in combined_lower:
        evidence.insert(0, "failure_hint: project-config-error (配置文件无效)")

    return False, evidence


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(available: bool, version: str | None, probes: list[str]) -> None:
    print("\n📊 OpenOCD 环境探测结果：")
    status = "✅" if available else "❌"
    ver = f" ({version})" if version else ""
    print(f"  {status} openocd{ver}")

    if probes:
        print(f"\n  已连接探针:")
        for p in probes:
            print(f"    - {p}")
    elif available:
        print("\n  ⚠️ 未检测到已连接的调试探针")


def print_flash_report(result: FlashResult) -> None:
    icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 烧录结果: {icon} {result.summary}")

    if result.command:
        print(f"\n  烧录命令:   {result.command}")
    if result.interface:
        print(f"  调试接口:   {result.interface}")
    if result.target_config:
        print(f"  目标配置:   {result.target_config}")
    if result.artifact_path:
        print(f"  固件产物:   {result.artifact_path} [{result.artifact_kind or '?'}]")
    print(f"  校验: {'是' if result.verified else '否'}  复位: {'是' if result.reset else '否'}")

    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:15]:
            print(f"  {line}")

    if result.failure_category:
        print(f"\n  失败分类: {result.failure_category}")


def print_scan_report(configs: list[dict[str, str]]) -> None:
    if not configs:
        print("❌ 未找到 OpenOCD 配置线索")
        return
    print(f"\n📋 找到 {len(configs)} 个 OpenOCD 配置线索：")
    for i, c in enumerate(configs, 1):
        print(f"  {i}. [{c['source']}] {c['path']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嵌入式 OpenOCD 烧录工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --scan-configs /repo/fw
  %(prog)s --artifact build/app.elf --interface stlink --target target/stm32f4x.cfg
  %(prog)s --artifact build/app.elf --config board/st_nucleo_f4.cfg
  %(prog)s --artifact build/fw.bin --target target/stm32f1x.cfg --base-address 0x08000000
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测 OpenOCD 环境和已连接探针")
    parser.add_argument("--artifact", help="固件产物路径")
    parser.add_argument(
        "--interface",
        choices=["stlink", "cmsis-dap", "daplink", "jlink"],
        help="调试接口",
    )
    parser.add_argument("--target", action="append", default=[], help="OpenOCD 目标配置，可重复")
    parser.add_argument("--config", action="append", default=[], help="额外 OpenOCD -f 配置，可重复")
    parser.add_argument("--base-address", help="BIN 文件烧录基地址（十六进制）")
    parser.add_argument("--no-verify", action="store_true", help="跳过烧录后校验")
    parser.add_argument("--no-reset", action="store_true", help="烧录后不复位")
    parser.add_argument("--no-detect", action="store_true", help="禁止自动探测调试接口")
    parser.add_argument("--scan-configs", help="扫描指定目录中的 OpenOCD 配置线索")
    parser.add_argument("--openocd-command", help="自定义 OpenOCD 烧录命令")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 探测模式
    if args.detect:
        available, version = check_openocd()
        probes = detect_probes() if available else []
        print_detect_report(available, version, probes)
        if args.save_config and available:
            ocd_path = shutil.which("openocd")
            if ocd_path:
                cfg_path = set_tool_path("openocd", ocd_path)
                print(f"  💾 已保存到 {cfg_path}")
        return 0 if available else 1

    # 扫描配置模式
    if args.scan_configs:
        configs = scan_openocd_configs(Path(args.scan_configs).resolve())
        print_scan_report(configs)
        return 0 if configs else 1

    # 烧录模式
    if not args.artifact:
        print("❌ 请提供 --artifact（固件产物路径）。")
        return 1

    # 检查 openocd
    available, _ = check_openocd()
    if not available:
        print("❌ 未找到 openocd，请先安装。")
        return 1

    # 验证产物
    artifact_path = str(Path(args.artifact).resolve())
    kind, size = identify_artifact(artifact_path)
    if kind is None:
        print(f"❌ 产物不存在或类型无法识别: {artifact_path}")
        return 1
    print(f"📦 固件产物: {artifact_path} [{kind.upper()}, {size / 1024:.1f} KB]")

    # BIN 需要基地址
    if kind == "bin" and not args.base_address:
        print("❌ BIN 文件必须提供 --base-address（烧录基地址）。")
        result = FlashResult(
            status="blocked",
            summary="BIN 文件缺少烧录基地址",
            artifact_path=artifact_path,
            artifact_kind=kind,
            failure_category="artifact-missing",
        )
        print_flash_report(result)
        return 1

    # 选择接口
    interface = choose_interface(args.interface, args.no_detect)

    verify = not args.no_verify
    reset = not args.no_reset

    # 组装命令
    cmd = build_flash_command(
        interface=interface,
        configs=args.config,
        targets=args.target,
        artifact=artifact_path,
        artifact_kind=kind,
        base_address=args.base_address,
        verify=verify,
        reset=reset,
        custom_command=args.openocd_command,
    )
    if cmd is None:
        return 1

    cmd_str = " ".join(cmd)

    # 执行烧录
    ok, evidence = run_flash(cmd, verbose=args.verbose)

    # 分析失败分类
    failure_category = None
    if not ok:
        for line in evidence:
            if "connection-failure" in line:
                failure_category = "connection-failure"
                break
            if "target-response-abnormal" in line:
                failure_category = "target-response-abnormal"
                break
            if "project-config-error" in line:
                failure_category = "project-config-error"
                break
        if not failure_category:
            failure_category = "connection-failure"

    target_str = ", ".join(args.target) if args.target else None

    result = FlashResult(
        status="success" if ok else "failure",
        summary="烧录成功" if ok else "烧录失败",
        command=cmd_str,
        interface=interface,
        target_config=target_str,
        artifact_path=artifact_path,
        artifact_kind=kind,
        verified=verify and ok,
        reset=reset and ok,
        failure_category=failure_category,
        evidence=evidence,
    )
    print_flash_report(result)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
