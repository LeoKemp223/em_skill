#!/usr/bin/env python
"""Workflow 流水线编排工具。

为 `workflow` skill 提供预定义流水线执行入口，支持：

- 编译 + 烧录 + 串口监控（build-flash-monitor）
- 编译 + 烧录 + GDB 调试（build-flash-debug）
- 自动根据构建系统选择对应 skill 脚本
- 步骤间自动传递产物路径
- 失败时立即停止并报告
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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

SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent

SCRIPT_MAP = {
    "keil": {
        "build": "build-keil/scripts/keil_builder.py",
        "flash": "flash-keil/scripts/keil_flasher.py",
        "debug": "debug-gdb-openocd/scripts/gdb_debugger.py",
        "monitor": "serial-monitor/scripts/serial_monitor.py",
    },
    "cmake": {
        "build": "build-cmake/scripts/cmake_builder.py",
        "flash": "flash-openocd/scripts/openocd_flasher.py",
        "debug": "debug-gdb-openocd/scripts/gdb_debugger.py",
        "monitor": "serial-monitor/scripts/serial_monitor.py",
    },
    "platformio": {
        "build": "build-platformio/scripts/platformio_builder.py",
        "flash": "flash-platformio/scripts/pio_flasher.py",
        "debug": "debug-platformio/scripts/pio_debugger.py",
        "monitor": "serial-monitor/scripts/serial_monitor.py",
    },
}

WORKFLOWS = {
    "build-flash-monitor": {
        "description": "编译 → 烧录 → 串口监控",
        "steps": ["build", "flash", "monitor"],
    },
    "build-flash-debug": {
        "description": "编译 → 烧录 → GDB 调试",
        "steps": ["build", "flash", "debug"],
    },
}

STEP_LABELS = {
    "build": "编译",
    "flash": "烧录",
    "monitor": "串口监控",
    "debug": "GDB 调试",
}


@dataclass
class WorkflowResult:
    status: str  # success, failure, partial
    summary: str
    workflow: str = ""
    steps_completed: int = 0
    steps_total: int = 0
    failed_step: str | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 脚本路径解析
# ---------------------------------------------------------------------------

def resolve_script(build_system: str, step: str) -> Path | None:
    mapping = SCRIPT_MAP.get(build_system)
    if not mapping or step not in mapping:
        return None
    return SKILLS_ROOT / mapping[step]


def check_scripts(build_system: str, steps: list[str]) -> list[tuple[str, Path, bool]]:
    results = []
    for step in steps:
        path = resolve_script(build_system, step)
        if path:
            results.append((step, path, path.exists()))
        else:
            results.append((step, Path("N/A"), False))
    return results


# ---------------------------------------------------------------------------
# 产物路径提取
# ---------------------------------------------------------------------------

def extract_artifact(output: str) -> str | None:
    for line in output.splitlines():
        if "⭐ 首选" in line or "⭐ 首选" in line:
            m = re.search(r'\]\s+(.+?)\s+\(', line)
            if m:
                return m.group(1).strip()
    for line in output.splitlines():
        for ext in (".elf", ".axf", ".hex", ".bin"):
            if ext in line.lower():
                m = re.search(r'(\S+' + re.escape(ext) + r')', line, re.IGNORECASE)
                if m:
                    return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 步骤命令构建
# ---------------------------------------------------------------------------

def build_build_cmd(script: Path, args) -> list[str]:
    cmd = [sys.executable, str(script)]
    if args.build_system == "keil":
        if args.project:
            cmd += ["--project", args.project]
        if args.target:
            cmd += ["--target", args.target]
    elif args.build_system == "cmake":
        if args.project:
            cmd += ["--source", args.project]
        if args.target:
            cmd += ["--preset", args.target]
    elif args.build_system == "platformio":
        if args.project:
            cmd += ["--project-dir", args.project]
        if args.target:
            cmd += ["--env", args.target]
    if args.verbose:
        cmd.append("-v")
    return cmd


def build_flash_cmd(script: Path, args, artifact: str | None) -> list[str]:
    cmd = [sys.executable, str(script)]
    if args.build_system == "keil":
        cmd.append("--flash")
        if args.project:
            cmd += ["--project", args.project]
        if args.target:
            cmd += ["--target", args.target]
    elif args.build_system == "cmake":
        cmd.append("--flash")
        if artifact:
            cmd += ["--artifact", artifact]
        if args.flash_interface:
            cmd += ["--interface", args.flash_interface]
        if args.flash_target:
            cmd += ["--target", args.flash_target]
    elif args.build_system == "platformio":
        cmd.append("--flash")
        if args.project:
            cmd += ["--project-dir", args.project]
        if args.target:
            cmd += ["--env", args.target]
    if args.verbose:
        cmd.append("-v")
    return cmd


def build_monitor_cmd(script: Path, args) -> list[str]:
    cmd = [sys.executable, str(script), "--listen"]
    if args.port:
        cmd += ["--port", args.port]
    if args.baud:
        cmd += ["--baud", str(args.baud)]
    return cmd


def build_debug_cmd(script: Path, args, artifact: str | None) -> list[str]:
    cmd = [sys.executable, str(script)]
    if args.build_system == "platformio":
        if args.project:
            cmd += ["--project-dir", args.project]
        if args.target:
            cmd += ["--env", args.target]
    else:
        if artifact:
            cmd += ["--elf", artifact]
        if args.flash_interface:
            cmd += ["--interface", args.flash_interface]
        if args.flash_target:
            cmd += ["--target", args.flash_target]
    if args.verbose:
        cmd.append("-v")
    return cmd


# ---------------------------------------------------------------------------
# 步骤执行
# ---------------------------------------------------------------------------

def run_step(name: str, cmd: list[str], inherit_io: bool = False, dry_run: bool = False) -> tuple[bool, str]:
    cmd_str = " ".join(cmd)
    if dry_run:
        print(f"  [dry-run] {cmd_str}")
        return True, ""

    print(f"  $ {cmd_str}")
    if inherit_io:
        proc = subprocess.run(cmd, cwd=os.getcwd())
        return proc.returncode == 0, ""
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(),
                              encoding="utf-8", errors="replace")
        output = proc.stdout or ""
        if proc.stdout:
            for line in proc.stdout.strip().splitlines():
                print(f"    {line}")
        if proc.returncode != 0 and proc.stderr:
            for line in proc.stderr.strip().splitlines()[-5:]:
                print(f"    ⚠️ {line}")
        return proc.returncode == 0, output


# ---------------------------------------------------------------------------
# Workflow 执行
# ---------------------------------------------------------------------------

def run_workflow(workflow_name: str, args) -> WorkflowResult:
    wf = WORKFLOWS[workflow_name]
    steps = wf["steps"]
    total = len(steps)
    artifact: str | None = args.artifact

    print(f"\n🚀 执行流水线: {workflow_name}（{wf['description']}）")
    print(f"  构建系统: {args.build_system}")
    if args.project:
        print(f"  工程路径: {args.project}")
    print()

    for i, step in enumerate(steps):
        label = STEP_LABELS.get(step, step)
        script = resolve_script(args.build_system, step)
        if not script or not script.exists():
            print(f"\n❌ [{i+1}/{total}] {label} — 脚本不存在: {script}")
            return WorkflowResult(status="failure", summary=f"{label}脚本不存在",
                                  workflow=workflow_name, steps_completed=i, steps_total=total,
                                  failed_step=step, failure_category="environment-missing")

        print(f"\n{'='*50}")
        print(f"[{i+1}/{total}] {label}")
        print(f"{'='*50}")

        if step == "build":
            cmd = build_build_cmd(script, args)
        elif step == "flash":
            cmd = build_flash_cmd(script, args, artifact)
        elif step == "monitor":
            cmd = build_monitor_cmd(script, args)
        elif step == "debug":
            cmd = build_debug_cmd(script, args, artifact)
        else:
            continue

        is_interactive = step in ("monitor", "debug")
        ok, output = run_step(step, cmd, inherit_io=is_interactive, dry_run=args.dry_run)

        if step == "build" and not args.dry_run and ok:
            found = extract_artifact(output)
            if found:
                artifact = found
                print(f"\n  📦 产物: {artifact}")

        if not ok and not args.dry_run:
            print(f"\n❌ 步骤 [{label}] 失败，流水线中止")
            return WorkflowResult(status="failure", summary=f"{label}失败",
                                  workflow=workflow_name, steps_completed=i, steps_total=total,
                                  failed_step=step, failure_category="target-response-abnormal",
                                  evidence=output.splitlines()[-5:] if output else [])

    return WorkflowResult(status="success", summary=f"流水线完成（{total} 步）",
                          workflow=workflow_name, steps_completed=total, steps_total=total)


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(result: WorkflowResult) -> None:
    icon = {"success": "✅", "failure": "❌", "partial": "⚠️"}.get(result.status, "❓")
    print(f"\n{'='*50}")
    print(f"📊 结果: {icon} {result.summary}")
    print(f"  流水线: {result.workflow}")
    print(f"  进度: {result.steps_completed}/{result.steps_total}")
    if result.failed_step:
        print(f"  失败步骤: {result.failed_step}")
    if result.failure_category:
        print(f"  失败分类: {result.failure_category}")
    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:10]:
            print(f"  {line}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Workflow 流水线编排工具",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--detect", action="store_true", help="探测环境")
    p.add_argument("--list", action="store_true", help="列出可用 workflow")
    p.add_argument("--run", help="执行指定 workflow")
    p.add_argument("--dry-run", action="store_true", help="仅打印命令，不实际执行")
    p.add_argument("--build-system", choices=["keil", "cmake", "platformio"],
                   help="构建系统")
    p.add_argument("--project", help="工程路径")
    p.add_argument("--target", help="构建目标/环境/预设")
    p.add_argument("--port", help="串口（monitor 用）")
    p.add_argument("--baud", type=int, help="波特率")
    p.add_argument("--artifact", help="固件产物路径（可选）")
    p.add_argument("--flash-interface", help="烧录接口（OpenOCD）")
    p.add_argument("--flash-target", help="烧录目标（OpenOCD）")
    p.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list:
        print("\n📋 可用 Workflow:")
        for name, wf in WORKFLOWS.items():
            steps_str = " → ".join(STEP_LABELS.get(s, s) for s in wf["steps"])
            print(f"  {name}: {wf['description']}（{steps_str}）")
        print(f"\n  支持的构建系统: {', '.join(SCRIPT_MAP.keys())}")
        return 0

    if args.detect:
        print("\n📊 Workflow 环境探测：")
        print(f"  Skills 根目录: {SKILLS_ROOT}")
        for bs in SCRIPT_MAP:
            print(f"\n  [{bs}]")
            for step in ["build", "flash", "debug", "monitor"]:
                path = resolve_script(bs, step)
                exists = path and path.exists()
                icon = "✅" if exists else "❌"
                print(f"    {icon} {step}: {path}")
        return 0

    if not args.run:
        parser.print_help()
        return 1

    if args.run not in WORKFLOWS:
        print(f"❌ 未知 workflow: {args.run}")
        print(f"  可用: {', '.join(WORKFLOWS.keys())}")
        return 1

    if not args.build_system:
        print("❌ 需要 --build-system 参数（keil / cmake / platformio）")
        return 1

    result = run_workflow(args.run, args)
    print_report(result)
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
