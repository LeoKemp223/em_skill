#!/usr/bin/env python
"""通用 IAR Embedded Workbench 命令行构建工具。

这个脚本为 `build-iar` skill 提供可重复调用的执行入口，支持：

- 探测 IAR Embedded Workbench 安装路径和 iarbuild.exe
- 扫描工作区中的 .ewp / .eww 工程文件
- 解析工程文件中的 configuration 列表、工具链、芯片和输出目录
- 通过 iarbuild.exe 命令行执行 make / build / clean
- 在输出目录中搜索 .out（ELF）、HEX、BIN 产物
"""

from __future__ import annotations

import argparse
import os
import platform
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

try:
    import xml.etree.ElementTree as ET
except ImportError:
    ET = None  # type: ignore[assignment,misc]

ARTIFACT_EXTENSIONS = {".out": "elf", ".elf": "elf", ".hex": "hex", ".bin": "bin"}
ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}
PROJECT_EXTENSIONS = {".ewp", ".eww"}

# IAR 常见安装路径
IAR_SEARCH_PATHS = [
    r"C:\Program Files\IAR Systems\Embedded Workbench",
    r"C:\Program Files (x86)\IAR Systems\Embedded Workbench",
    r"D:\Program Files\IAR Systems\Embedded Workbench",
    r"D:\IAR Systems\Embedded Workbench",
    r"C:\IAR Systems\Embedded Workbench",
]


@dataclass
class IARConfig:
    name: str
    toolchain: str
    device: str
    exe_path: str
    output_file: str


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
    config_name: str | None = None
    device: str | None = None
    toolchain: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    errors: int = 0
    warnings: int = 0
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# IAR 探测
# ---------------------------------------------------------------------------

def is_windows() -> bool:
    return platform.system().lower() == "windows"


def _find_iarbuild_in_dir(iar_dir: Path) -> str | None:
    """在 IAR 安装目录中搜索 iarbuild.exe。"""
    # 常见子目录结构: <iar_root>/arm/bin/iarbuild.exe 或 <iar_root>/common/bin/iarbuild.exe
    for sub in ["arm/bin", "common/bin", "riscv/bin"]:
        candidate = iar_dir / sub / "iarbuild.exe"
        if candidate.exists():
            return str(candidate)
    # 直接在目录下搜索
    for root, _dirs, files in os.walk(iar_dir):
        depth = root.replace(str(iar_dir), "").count(os.sep)
        if depth > 3:
            continue
        for fname in files:
            if fname.lower() == "iarbuild.exe":
                return str(Path(root) / fname)
    return None


def find_iarbuild(explicit_root: str | None = None) -> str | None:
    """定位 iarbuild.exe。"""
    # 显式指定
    if explicit_root:
        p = Path(explicit_root)
        if p.is_file() and p.name.lower() == "iarbuild.exe":
            return str(p)
        if p.is_dir():
            found = _find_iarbuild_in_dir(p)
            if found:
                return found
        return None

    # 配置文件
    configured = get_tool_path("iarbuild")
    if configured and Path(configured).exists():
        return configured

    # 环境变量
    for env_var in ["IAR_ROOT", "EWARM_ROOT"]:
        val = os.environ.get(env_var)
        if val:
            found = _find_iarbuild_in_dir(Path(val))
            if found:
                return found

    # 常见路径
    for path_str in IAR_SEARCH_PATHS:
        p = Path(path_str)
        if p.is_dir():
            found = _find_iarbuild_in_dir(p)
            if found:
                return found

    # PATH 搜索
    import shutil
    path = shutil.which("iarbuild") or shutil.which("iarbuild.exe")
    if path:
        return path

    return None


def detect_environment(explicit_root: str | None = None) -> dict[str, Any]:
    iarbuild_path = find_iarbuild(explicit_root)
    env: dict[str, Any] = {
        "platform": platform.system(),
        "is_windows": is_windows(),
        "iarbuild": {"available": iarbuild_path is not None, "path": iarbuild_path},
    }
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
    results.sort(key=lambda p: (p.suffix != ".ewp", str(p)))
    return results


def _get_option_state(settings_elem: Any, option_name: str) -> str:
    """从 IAR settings XML 中提取指定 option 的 state 值。"""
    if ET is None:
        return ""
    for option in settings_elem.iter("option"):
        name_elem = option.find("name")
        if name_elem is not None and name_elem.text == option_name:
            state_elem = option.find("state")
            if state_elem is not None and state_elem.text:
                return state_elem.text.strip()
    return ""


def parse_project(project_path: Path) -> list[IARConfig]:
    """解析 .ewp 文件，提取 configuration 列表。"""
    if ET is None:
        print("❌ xml.etree.ElementTree 不可用")
        return []

    try:
        tree = ET.parse(project_path)
    except ET.ParseError as exc:
        print(f"❌ 工程文件解析失败: {exc}")
        return []

    root = tree.getroot()
    configs: list[IARConfig] = []

    for config_elem in root.iter("configuration"):
        name_elem = config_elem.find("name")
        if name_elem is None or not name_elem.text:
            continue
        name = name_elem.text.strip()

        toolchain = ""
        device = ""
        exe_path = ""
        output_file = ""

        # 工具链名称
        tc_elem = config_elem.find("toolchain/name")
        if tc_elem is not None and tc_elem.text:
            toolchain = tc_elem.text.strip()

        # 遍历 settings 提取芯片、输出目录和输出文件
        for settings in config_elem.iter("settings"):
            settings_name = settings.find("name")
            if settings_name is None or not settings_name.text:
                continue
            sname = settings_name.text.strip()

            if sname == "General":
                # 芯片型号
                chip = _get_option_state(settings, "OGChipSelectEditMenu")
                if chip:
                    device = chip
                # 输出目录
                ep = _get_option_state(settings, "ExePath")
                if ep:
                    exe_path = ep

            if sname == "ILINK":
                # 输出文件名
                of = _get_option_state(settings, "IlinkOutputFile")
                if of:
                    output_file = of

        configs.append(IARConfig(
            name=name,
            toolchain=toolchain,
            device=device,
            exe_path=exe_path,
            output_file=output_file,
        ))

    return configs


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


def resolve_output_dir(project_path: Path, config: IARConfig) -> Path:
    """根据工程路径和配置的 ExePath 解析输出目录。"""
    if config.exe_path:
        # IAR 的 ExePath 通常是相对于工程文件的路径，如 "Debug\Exe"
        candidate = project_path.parent / config.exe_path.replace("$PROJ_DIR$", ".")
        return candidate.resolve()
    # 默认猜测: <project_dir>/<config_name>/Exe
    return (project_path.parent / config.name / "Exe").resolve()


# ---------------------------------------------------------------------------
# 编译执行
# ---------------------------------------------------------------------------

def run_iar_build(
    iarbuild_path: str,
    project_path: Path,
    config_name: str,
    mode: str,
    parallel: int | None,
    verbose: bool,
) -> tuple[bool, str, list[str]]:
    """调用 iarbuild.exe 执行编译。

    mode: -make (增量), -build (全量重编译), -clean (清理)
    """
    cmd = [iarbuild_path, str(project_path), mode, config_name]
    if verbose:
        cmd.extend(["-log", "all"])
    if parallel and parallel > 1:
        cmd.extend(["-parallel", str(parallel)])

    cmd_str = " ".join(cmd)
    print(f"🔨 构建命令: {cmd_str}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, cmd_str, ["❌ IAR 编译超时（600 秒）"]
    except FileNotFoundError:
        return False, cmd_str, [f"❌ 未找到 iarbuild: {iarbuild_path}"]

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    evidence: list[str] = []
    errors = 0
    warnings = 0

    for line in output.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if "error" in stripped.lower() and ("Error[" in stripped or ": error" in stripped.lower()):
            errors += 1
            if len(evidence) < 20:
                evidence.append(stripped)
        elif "warning" in stripped.lower() and ("Warning[" in stripped or ": warning" in stripped.lower()):
            warnings += 1
        # 汇总行
        if "Total number of errors" in stripped:
            evidence.append(stripped)
        if "Total number of warnings" in stripped:
            evidence.append(stripped)

    if result.returncode == 0 and errors == 0:
        action_map = {"-make": "编译", "-build": "重新编译", "-clean": "清理"}
        action = action_map.get(mode, "操作")
        warn_note = f"（{warnings} 个警告）" if warnings > 0 else ""
        print(f"✅ {action}成功{warn_note}")
        return True, cmd_str, evidence

    evidence.insert(0, f"iarbuild 返回码: {result.returncode}")
    # 附加末尾输出
    tail = output.strip().split("\n")[-10:]
    for line in tail:
        if line.strip() and line.strip() not in evidence:
            evidence.append(line.strip())

    return False, cmd_str, evidence


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 IAR Embedded Workbench 环境探测结果：")
    print(f"  平台: {env['platform']}")

    iarbuild = env["iarbuild"]
    status = "✅" if iarbuild["available"] else "❌"
    path = f" @ {iarbuild['path']}" if iarbuild.get("path") else ""
    print(f"  {status} iarbuild.exe{path}")

    if not env["is_windows"]:
        print("\n  ⚠️ IAR Embedded Workbench 仅在 Windows 上支持编译")
        print("  ℹ️ 工程解析、配置列表和产物扫描仍可在当前平台使用")


def print_build_report(result: BuildResult) -> None:
    icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {icon} {result.summary}")

    if result.build_cmd:
        print(f"\n  构建命令:   {result.build_cmd}")
    if result.project_file:
        print(f"  工程文件:   {result.project_file}")
    if result.config_name:
        print(f"  配置:       {result.config_name}")
    if result.device:
        print(f"  芯片:       {result.device}")
    if result.toolchain:
        print(f"  工具链:     {result.toolchain}")
    if result.errors or result.warnings:
        print(f"  错误: {result.errors}  警告: {result.warnings}")

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
        description="IAR Embedded Workbench 命令行构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --scan /repo/fw
  %(prog)s --list-configs --project app.ewp
  %(prog)s --project app.ewp --config Debug
  %(prog)s --project app.ewp --config Release --rebuild
  %(prog)s --project app.ewp --config Debug --clean
  %(prog)s --scan-artifacts Debug/Exe/
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测 IAR 环境")
    parser.add_argument("--project", help=".ewp 工程文件路径")
    parser.add_argument("--config", help="构建配置名称")
    parser.add_argument("--list-configs", action="store_true", help="列出工程中的所有配置")
    parser.add_argument("--rebuild", action="store_true", help="重新编译（clean + build）")
    parser.add_argument("--clean", action="store_true", help="清理指定配置")
    parser.add_argument("--scan", help="扫描指定目录中的 IAR 工程文件")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的产物")
    parser.add_argument("--iar-root", help="显式指定 IAR 安装根目录")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("--parallel", type=int, help="并行编译任务数")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 环境探测
    if args.detect:
        env = detect_environment(args.iar_root)
        print_detect_report(env)
        if args.save_config and env["iarbuild"]["available"]:
            cfg_path = set_tool_path("iarbuild", env["iarbuild"]["path"])
            print(f"  💾 已保存到 {cfg_path}")
        return 0 if env["iarbuild"]["available"] else 1

    # 扫描工程文件
    if args.scan:
        scan_dir = Path(args.scan).resolve()
        projects = scan_project_files(scan_dir)
        if not projects:
            print(f"❌ 在 {scan_dir} 中未找到 IAR 工程文件")
            return 1
        print(f"📋 找到 {len(projects)} 个 IAR 工程文件：")
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
        print("❌ 请提供 --project（IAR .ewp 工程文件路径）。")
        return 1

    project_path = Path(args.project).resolve()
    if not project_path.exists():
        print(f"❌ 工程文件不存在: {project_path}")
        return 1

    # 解析工程
    configs = parse_project(project_path)
    if not configs:
        print(f"❌ 未能从工程文件中解析出配置: {project_path}")
        return 1

    # 列出配置
    if args.list_configs:
        print(f"📋 工程 {project_path.name} 中的配置：")
        for i, c in enumerate(configs, 1):
            tc_info = f" [{c.toolchain}]" if c.toolchain else ""
            dev_info = f" ({c.device})" if c.device else ""
            out_info = f" → {c.exe_path}/{c.output_file}" if c.output_file else ""
            print(f"  {i}. {c.name}{tc_info}{dev_info}{out_info}")
        return 0

    # 选择配置
    selected: IARConfig | None = None
    if args.config:
        for c in configs:
            if c.name == args.config:
                selected = c
                break
        if not selected:
            print(f"❌ 未找到配置 '{args.config}'，可用配置：")
            for c in configs:
                print(f"  - {c.name}")
            return 1
    else:
        selected = configs[0]
        if len(configs) > 1:
            print(f"ℹ️ 未指定配置，默认使用: {selected.name}")

    print(f"📦 配置: {selected.name} [{selected.toolchain}] {selected.device}")

    # 检查 iarbuild
    iarbuild_path = find_iarbuild(args.iar_root)
    if not iarbuild_path:
        if not is_windows():
            print("❌ IAR Embedded Workbench 仅在 Windows 上支持编译。")
            print("   当前平台可使用 --list-configs 和 --scan-artifacts。")
        else:
            print("❌ 未找到 iarbuild.exe，请安装 IAR 或通过 --iar-root 指定路径。")
        return 1

    # 确定编译模式
    if args.clean:
        mode = "-clean"
    elif args.rebuild:
        mode = "-build"
    else:
        mode = "-make"

    # 执行编译
    ok, cmd_str, evidence = run_iar_build(
        iarbuild_path=iarbuild_path,
        project_path=project_path,
        config_name=selected.name,
        mode=mode,
        parallel=args.parallel,
        verbose=args.verbose,
    )

    # 扫描产物
    output_dir = resolve_output_dir(project_path, selected)
    artifacts = scan_artifacts(output_dir)
    # 也搜索工程目录本身
    if not artifacts:
        artifacts = scan_artifacts(project_path.parent)
    primary = artifacts[0] if artifacts else None

    if not ok:
        result = BuildResult(
            status="failure",
            summary="IAR 编译失败",
            build_cmd=cmd_str,
            project_file=str(project_path),
            config_name=selected.name,
            device=selected.device,
            toolchain=selected.toolchain,
            failure_category="project-config-error",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    if args.clean:
        result = BuildResult(
            status="success",
            summary=f"清理配置 {selected.name} 成功",
            build_cmd=cmd_str,
            project_file=str(project_path),
            config_name=selected.name,
            evidence=evidence,
        )
        print_build_report(result)
        return 0

    if not artifacts:
        result = BuildResult(
            status="success",
            summary="编译成功但未找到固件产物",
            build_cmd=cmd_str,
            project_file=str(project_path),
            config_name=selected.name,
            device=selected.device,
            toolchain=selected.toolchain,
            artifacts=[],
            failure_category="artifact-missing",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    result = BuildResult(
        status="success",
        summary=f"编译成功，找到 {len(artifacts)} 个产物",
        build_cmd=cmd_str,
        project_file=str(project_path),
        config_name=selected.name,
        device=selected.device,
        toolchain=selected.toolchain,
        artifacts=artifacts,
        primary_artifact=primary,
        evidence=evidence,
    )
    print_build_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
