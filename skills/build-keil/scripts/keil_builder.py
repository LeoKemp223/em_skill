#!/usr/bin/env python
"""通用 Keil MDK 命令行构建工具。

这个脚本为 `build-keil` skill 提供可重复调用的执行入口，支持：

- 探测 Keil MDK 安装路径和 UV4.exe
- 扫描工作区中的 .uvprojx / .uvproj 工程文件
- 解析工程文件中的目标列表、输出目录和芯片信息
- 通过 UV4.exe 命令行执行 build / rebuild
- 在输出目录中搜索 AXF、HEX、BIN 产物
- 解析编译日志，提取错误和警告统计
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import re
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
elif sys.stdout:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif sys.stderr:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _SCRIPT_DIR.parent.parent
for _candidate in [_SKILLS_DIR / "shared", _SKILLS_DIR.parent / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
from tool_config import get_tool_path, set_tool_path

try:
    import xml.etree.ElementTree as ET
except ImportError:
    ET = None  # type: ignore[assignment,misc]

ARTIFACT_EXTENSIONS = {".axf": "elf", ".elf": "elf", ".hex": "hex", ".bin": "bin"}
ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}
PROJECT_EXTENSIONS = {".uvprojx", ".uvproj"}

# Keil MDK 常见安装路径
KEIL_SEARCH_PATHS = [
    r"C:\Keil_v5\UV4\UV4.exe",
    r"C:\Keil\UV4\UV4.exe",
    r"D:\Keil_v5\UV4\UV4.exe",
    r"D:\Keil\UV4\UV4.exe",
]


@dataclass
class KeilTarget:
    name: str
    device: str
    output_dir: str
    output_name: str
    toolchain: str  # ARMCC or ARMCLANG
    create_hex: bool
    create_bin: bool


@dataclass
class Artifact:
    path: Path
    kind: str
    size: int


@dataclass
class BuildResult:
    status: str  # success, failure, blocked
    summary: str
    build_cmd: str | None = None
    project_file: str | None = None
    target_name: str | None = None
    device: str | None = None
    toolchain: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    errors: int = 0
    warnings: int = 0
    program_size: dict[str, int] | None = None
    build_time: str | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Keil MDK 探测
# ---------------------------------------------------------------------------

def is_windows() -> bool:
    return platform.system().lower() == "windows"


def find_uv4(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return str(p)
        return None

    # 配置文件
    configured = get_tool_path("uv4")
    if configured and Path(configured).exists():
        return configured

    # 环境变量
    keil_root = os.environ.get("KEIL_ROOT") or os.environ.get("MDK_ROOT")
    if keil_root:
        candidate = Path(keil_root) / "UV4" / "UV4.exe"
        if candidate.exists():
            return str(candidate)

    # 常见路径
    for path_str in KEIL_SEARCH_PATHS:
        p = Path(path_str)
        if p.exists():
            return str(p)

    # PATH 搜索
    import shutil
    path = shutil.which("UV4") or shutil.which("UV4.exe")
    if path:
        return path

    return None


def detect_environment(explicit_uv4: str | None = None) -> dict[str, Any]:
    uv4_path = find_uv4(explicit_uv4)
    env: dict[str, Any] = {
        "platform": platform.system(),
        "is_windows": is_windows(),
        "uv4": {"available": uv4_path is not None, "path": uv4_path},
    }

    # 尝试获取 ARMCC/ARMCLANG 版本
    if uv4_path:
        keil_root = Path(uv4_path).parent.parent
        for compiler, exe in [("ARMCC", "ARM/ARMCC/bin/armcc.exe"), ("ARMCLANG", "ARM/ARMCLANG/bin/armclang.exe")]:
            comp_path = keil_root / exe
            if comp_path.exists():
                env[compiler.lower()] = {"available": True, "path": str(comp_path)}
            else:
                env[compiler.lower()] = {"available": False, "path": None}
    else:
        env["armcc"] = {"available": False, "path": None}
        env["armclang"] = {"available": False, "path": None}

    return env


# ---------------------------------------------------------------------------
# 工程文件解析
# ---------------------------------------------------------------------------

def scan_project_files(workspace: Path) -> list[Path]:
    results: list[Path] = []
    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > 4:
            continue
        for fname in files:
            if Path(fname).suffix.lower() in PROJECT_EXTENSIONS:
                results.append(Path(root) / fname)
    results.sort(key=lambda p: (p.suffix != ".uvprojx", str(p)))
    return results


def parse_project(project_path: Path) -> list[KeilTarget]:
    if ET is None:
        print("❌ xml.etree.ElementTree 不可用")
        return []

    try:
        tree = ET.parse(project_path)
    except ET.ParseError as exc:
        print(f"❌ 工程文件解析失败: {exc}")
        return []

    root = tree.getroot()
    targets: list[KeilTarget] = []

    for target_elem in root.iter("Target"):
        name_elem = target_elem.find("TargetName")
        if name_elem is None or not name_elem.text:
            continue
        name = name_elem.text.strip()

        # 芯片信息
        device = ""
        device_elem = target_elem.find(".//Device")
        if device_elem is not None and device_elem.text:
            device = device_elem.text.strip()

        # 输出配置
        output_dir = ""
        output_name = ""
        output_elem = target_elem.find(".//OutputDirectory")
        if output_elem is not None and output_elem.text:
            output_dir = output_elem.text.strip().rstrip("\\/")
        oname_elem = target_elem.find(".//OutputName")
        if oname_elem is not None and oname_elem.text:
            output_name = oname_elem.text.strip()

        # 工具链
        toolchain = "ARMCC"
        armclang_elem = target_elem.find(".//uAC6")
        if armclang_elem is not None and armclang_elem.text == "1":
            toolchain = "ARMCLANG"

        # HEX/BIN 输出
        create_hex = False
        create_bin = False
        hex_elem = target_elem.find(".//CreateHexFile")
        if hex_elem is not None and hex_elem.text == "1":
            create_hex = True
        bin_elem = target_elem.find(".//CreateBinFile") or target_elem.find(".//CreateLib")
        # Keil 没有直接的 CreateBinFile 标签，通常通过 User Command 或 fromelf 生成
        # 这里保守处理

        targets.append(KeilTarget(
            name=name,
            device=device,
            output_dir=output_dir,
            output_name=output_name,
            toolchain=toolchain,
            create_hex=create_hex,
            create_bin=create_bin,
        ))

    return targets


# ---------------------------------------------------------------------------
# 产物扫描
# ---------------------------------------------------------------------------

def scan_artifacts(search_dir: Path) -> list[Artifact]:
    if not search_dir.exists():
        return []

    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for root, _dirs, files in os.walk(search_dir):
        for fname in files:
            ext = Path(fname).suffix.lower()
            kind = ARTIFACT_EXTENSIONS.get(ext)
            if not kind:
                continue
            fpath = Path(root) / fname
            real = str(fpath.resolve())
            if real in seen:
                continue
            seen.add(real)
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            if size < 256:
                continue
            artifacts.append(Artifact(path=fpath, kind=kind, size=size))

    artifacts.sort(key=lambda a: (ARTIFACT_PRIORITY.get(a.kind, 9), -a.size))
    return artifacts


def resolve_output_dir(project_path: Path, target: KeilTarget) -> Path:
    if target.output_dir:
        candidate = project_path.parent / target.output_dir
        return candidate.resolve()
    return project_path.parent.resolve()


# ---------------------------------------------------------------------------
# 编译执行
# ---------------------------------------------------------------------------

def parse_build_log(log_path: Path) -> tuple[int, int, list[str], dict[str, int] | None, str | None]:
    """解析编译日志，返回 (errors, warnings, evidence_lines, program_size, build_time)"""
    errors = 0
    warnings = 0
    evidence: list[str] = []
    program_size: dict[str, int] | None = None
    build_time: str | None = None

    if not log_path.exists():
        return 0, 0, [], None, None

    try:
        # Keil 日志可能是 GBK/UTF-8 混合编码
        for encoding in ["utf-8", "gbk", "latin-1"]:
            try:
                content = log_path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            return 0, 0, ["(日志编码无法识别)"], None, None
    except OSError:
        return 0, 0, [], None, None

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Keil 输出格式: "xxx.c(123): error: ..."  或  "xxx.c(123): warning: ..."
        if ": error" in stripped.lower() or "error:" in stripped.lower():
            errors += 1
            if len(evidence) < 20:
                evidence.append(stripped)
        elif ": warning" in stripped.lower() or "warning:" in stripped.lower():
            warnings += 1

        # 汇总行: "0 Error(s), 0 Warning(s)."
        summary_match = re.search(r"(\d+)\s+Error\(s\),\s+(\d+)\s+Warning\(s\)", stripped)
        if summary_match:
            errors = max(errors, int(summary_match.group(1)))
            warnings = max(warnings, int(summary_match.group(2)))

        # 构建结果行
        if "build target" in stripped.lower() or "rebuild target" in stripped.lower():
            evidence.append(stripped)
        if "compiling" in stripped.lower() and errors == 0 and len(evidence) < 5:
            evidence.append(stripped)

        # Program Size: Code=2852 RO-data=372 RW-data=16 ZI-data=1632
        size_match = re.search(
            r"Program Size:\s*Code=(\d+)\s+RO-data=(\d+)\s+RW-data=(\d+)\s+ZI-data=(\d+)",
            stripped,
        )
        if size_match:
            program_size = {
                "Code": int(size_match.group(1)),
                "RO-data": int(size_match.group(2)),
                "RW-data": int(size_match.group(3)),
                "ZI-data": int(size_match.group(4)),
            }

        # Build Time Elapsed:  00:00:05
        time_match = re.search(r"Build Time Elapsed:\s*(\S+)", stripped)
        if time_match:
            build_time = time_match.group(1)

    return errors, warnings, evidence, program_size, build_time


@dataclass
class KeilBuildOutput:
    ok: bool
    cmd_str: str
    errors: int
    warnings: int
    evidence: list[str]
    program_size: dict[str, int] | None = None
    build_time: str | None = None


def run_keil_build(
    uv4_path: str,
    project_path: Path,
    target_name: str,
    rebuild: bool,
    log_path: Path,
) -> KeilBuildOutput:
    flag = "-r" if rebuild else "-b"
    cmd = [uv4_path, flag, str(project_path), "-t", target_name, "-o", str(log_path)]
    cmd_str = " ".join(cmd)
    print(f"🔨 构建命令: {cmd_str}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return KeilBuildOutput(False, cmd_str, 0, 0, ["❌ Keil 编译超时（600 秒）"])
    except FileNotFoundError:
        return KeilBuildOutput(False, cmd_str, 0, 0, [f"❌ 未找到 UV4: {uv4_path}"])

    errors, warnings, log_evidence, prog_size, b_time = parse_build_log(log_path)

    if result.returncode <= 1 and errors == 0:
        action = "重新编译" if rebuild else "编译"
        warn_note = f"（{warnings} 个警告）" if warnings > 0 else ""
        print(f"✅ {action}成功{warn_note}")
        return KeilBuildOutput(True, cmd_str, errors, warnings, log_evidence, prog_size, b_time)

    log_evidence.insert(0, f"UV4 返回码: {result.returncode}")
    return KeilBuildOutput(False, cmd_str, errors, warnings, log_evidence, prog_size, b_time)


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 Keil MDK 环境探测结果：")
    print(f"  平台: {env['platform']}")

    uv4 = env["uv4"]
    status = "✅" if uv4["available"] else "❌"
    path = f" @ {uv4['path']}" if uv4.get("path") else ""
    print(f"  {status} UV4.exe{path}")

    for name in ["armcc", "armclang"]:
        info = env.get(name, {})
        status = "✅" if info.get("available") else "❌"
        path = f" @ {info.get('path', '')}" if info.get("path") else ""
        print(f"  {status} {name.upper()}{path}")

    if not env["is_windows"]:
        print("\n  ⚠️ Keil MDK 仅在 Windows 上支持编译")
        print("  ℹ️ 工程解析、目标列表和产物扫描仍可在当前平台使用")


def print_build_report(result: BuildResult) -> None:
    icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {icon} {result.summary}")

    if result.build_cmd:
        print(f"\n  构建命令:   {result.build_cmd}")
    if result.project_file:
        print(f"  工程文件:   {result.project_file}")
    if result.target_name:
        print(f"  目标:       {result.target_name}")
    if result.device:
        print(f"  芯片:       {result.device}")
    if result.toolchain:
        print(f"  工具链:     {result.toolchain}")
    if result.errors or result.warnings:
        print(f"  错误: {result.errors}  警告: {result.warnings}")
    if result.build_time:
        print(f"  编译耗时:   {result.build_time}")

    if result.program_size:
        ps = result.program_size
        flash = ps.get("Code", 0) + ps.get("RO-data", 0) + ps.get("RW-data", 0)
        ram = ps.get("RW-data", 0) + ps.get("ZI-data", 0)
        print(f"\n📐 固件大小:")
        print(f"  Code={ps.get('Code', 0)}  RO-data={ps.get('RO-data', 0)}  "
              f"RW-data={ps.get('RW-data', 0)}  ZI-data={ps.get('ZI-data', 0)}")
        print(f"  Flash ≈ {flash / 1024:.1f} KB (Code + RO + RW)    RAM ≈ {ram / 1024:.1f} KB (RW + ZI)")

    if result.artifacts:
        print(f"\n📦 找到 {len(result.artifacts)} 个固件产物：")
        for i, a in enumerate(result.artifacts):
            size_kb = a.size / 1024
            primary = " ⭐ 首选" if a == result.primary_artifact else ""
            print(f"  {i + 1}. [{a.kind.upper()}] {a.path} ({size_kb:.1f} KB){primary}")
    elif result.status == "success":
        print("\n  ⚠️ 编译成功但未找到固件产物")

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
        description="Keil MDK 命令行构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --scan /repo/fw
  %(prog)s --list-targets --project app.uvprojx
  %(prog)s --project app.uvprojx --target Debug
  %(prog)s --project app.uvprojx --target Release --rebuild
  %(prog)s --scan-artifacts Objects/
        """,
    )
    parser.add_argument("--detect", action="store_true",
                        help="探测 Keil MDK 环境（可与 --project/--target 组合，一次完成探测+编译）")
    parser.add_argument("--project", help=".uvprojx 或 .uvproj 工程文件路径")
    parser.add_argument("--target", help="构建目标名称")
    parser.add_argument("--list-targets", action="store_true", help="列出工程中的所有目标")
    parser.add_argument("--rebuild", action="store_true", help="重新编译（clean + build）")
    parser.add_argument("--scan", help="扫描指定目录中的 Keil 工程文件")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的产物")
    parser.add_argument("--uv4", help="显式指定 UV4.exe 路径")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("--log", help="编译日志输出路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 环境探测（可独立使用，也可与 --project 组合）
    if args.detect:
        env = detect_environment(args.uv4)
        print_detect_report(env)
        if args.save_config and env["uv4"]["available"]:
            cfg_path = set_tool_path("uv4", env["uv4"]["path"])
            print(f"  💾 已保存到 {cfg_path}")
        if not env["uv4"]["available"]:
            return 1
        if not args.project:
            return 0

    # 扫描工程文件
    if args.scan:
        scan_dir = Path(args.scan).resolve()
        projects = scan_project_files(scan_dir)
        if not projects:
            print(f"❌ 在 {scan_dir} 中未找到 Keil 工程文件")
            return 1
        print(f"📋 找到 {len(projects)} 个 Keil 工程文件：")
        for i, p in enumerate(projects, 1):
            print(f"  {i}. {p}")
        return 0

    # 仅扫描产物
    if args.scan_artifacts:
        scan_dir = Path(args.scan_artifacts).resolve()
        artifacts = scan_artifacts(scan_dir)
        if not artifacts:
            print(f"❌ 在 {scan_dir} 中未找到固件产物")
            return 1
        primary = artifacts[0] if artifacts else None
        result = BuildResult(
            status="success",
            summary=f"找到 {len(artifacts)} 个产物",
            artifacts=artifacts,
            primary_artifact=primary,
        )
        print_build_report(result)
        return 0

    # 需要工程文件
    if not args.project:
        print("❌ 请提供 --project（Keil 工程文件路径）。")
        return 1

    project_path = Path(args.project).resolve()
    if not project_path.exists():
        print(f"❌ 工程文件不存在: {project_path}")
        return 1

    # 解析工程
    targets = parse_project(project_path)
    if not targets:
        print(f"❌ 未能从工程文件中解析出目标: {project_path}")
        return 1

    # 列出目标
    if args.list_targets:
        print(f"📋 工程 {project_path.name} 中的目标：")
        for i, t in enumerate(targets, 1):
            tc_info = f" [{t.toolchain}]" if t.toolchain else ""
            dev_info = f" ({t.device})" if t.device else ""
            out_info = f" → {t.output_dir}/{t.output_name}" if t.output_name else ""
            print(f"  {i}. {t.name}{tc_info}{dev_info}{out_info}")
        return 0

    # 选择目标
    selected: KeilTarget | None = None
    if args.target:
        for t in targets:
            if t.name == args.target:
                selected = t
                break
        if not selected:
            print(f"❌ 未找到目标 '{args.target}'，可用目标：")
            for t in targets:
                print(f"  - {t.name}")
            return 1
    else:
        selected = targets[0]
        if len(targets) > 1:
            print(f"ℹ️ 未指定目标，默认使用: {selected.name}")

    print(f"📦 目标: {selected.name} [{selected.toolchain}] {selected.device}")

    # 检查 UV4
    uv4_path = find_uv4(args.uv4)
    if not uv4_path:
        if not is_windows():
            print("❌ Keil MDK 仅在 Windows 上支持编译。")
            print("   当前平台可使用 --list-targets 和 --scan-artifacts。")
        else:
            print("❌ 未找到 UV4.exe，请安装 Keil MDK 或通过 --uv4 指定路径。")
        return 1

    # 日志路径
    log_path = Path(args.log) if args.log else project_path.parent / f"{selected.name}_build.log"

    # 执行编译
    build_out = run_keil_build(
        uv4_path=uv4_path,
        project_path=project_path,
        target_name=selected.name,
        rebuild=args.rebuild,
        log_path=log_path,
    )

    # 扫描产物（避免重叠：output_dir 是 project_path.parent 子目录时不重复扫描）
    output_dir = resolve_output_dir(project_path, selected)
    artifacts = scan_artifacts(output_dir)
    if not artifacts:
        project_dir = project_path.parent.resolve()
        if output_dir != project_dir and not str(output_dir).startswith(str(project_dir) + os.sep):
            artifacts = scan_artifacts(project_dir)
    primary = artifacts[0] if artifacts else None

    if not build_out.ok:
        result = BuildResult(
            status="failure",
            summary="Keil 编译失败",
            build_cmd=build_out.cmd_str,
            project_file=str(project_path),
            target_name=selected.name,
            device=selected.device,
            toolchain=selected.toolchain,
            errors=build_out.errors,
            warnings=build_out.warnings,
            failure_category="project-config-error",
            evidence=build_out.evidence,
            program_size=build_out.program_size,
            build_time=build_out.build_time,
        )
        print_build_report(result)
        return 1

    if not artifacts:
        result = BuildResult(
            status="success",
            summary="编译成功但未找到固件产物",
            build_cmd=build_out.cmd_str,
            project_file=str(project_path),
            target_name=selected.name,
            device=selected.device,
            toolchain=selected.toolchain,
            errors=build_out.errors,
            warnings=build_out.warnings,
            artifacts=[],
            failure_category="artifact-missing",
            evidence=build_out.evidence,
            program_size=build_out.program_size,
            build_time=build_out.build_time,
        )
        print_build_report(result)
        return 1

    result = BuildResult(
        status="success",
        summary=f"编译成功，找到 {len(artifacts)} 个产物",
        build_cmd=build_out.cmd_str,
        project_file=str(project_path),
        target_name=selected.name,
        device=selected.device,
        toolchain=selected.toolchain,
        errors=build_out.errors,
        warnings=build_out.warnings,
        artifacts=artifacts,
        primary_artifact=primary,
        evidence=build_out.evidence,
        program_size=build_out.program_size,
        build_time=build_out.build_time,
    )
    print_build_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
