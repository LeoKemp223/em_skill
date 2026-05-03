#!/usr/bin/env python
"""裸 Makefile 嵌入式构建工具。

为 `build-makefile` skill 提供可重复调用的执行入口，支持：

- 探测构建环境（make、交叉编译器）
- 解析 Makefile 变量（CROSS_COMPILE、TARGET、MCU 等）
- 列出 Makefile 中可用的 make 目标
- 执行 make 构建并定位固件产物
- 在构建目录中搜索 ELF、HEX、BIN 产物并按优先级排序
- 输出结构化的构建结果和分析报告
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
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

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _SCRIPT_DIR.parent.parent
for _candidate in [_SKILLS_DIR / "shared", _SKILLS_DIR.parent / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
from tool_config import get_tool_path, set_tool_path


ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}
ARTIFACT_EXTENSIONS = {".elf": "elf", ".hex": "hex", ".bin": "bin", ".axf": "elf", ".out": "elf"}
MAKE_NAMES = ["make", "gmake", "mingw32-make"]
MAKEFILE_NAMES = ["Makefile", "makefile", "GNUmakefile"]
CROSS_COMPILE_MAP = {
    "arm-none-eabi-": "gnu-arm",
    "arm-linux-gnueabihf-": "gnu-arm-linux",
    "riscv32-unknown-elf-": "gnu-riscv",
    "riscv64-unknown-elf-": "gnu-riscv",
    "xtensa-esp32-elf-": "gnu-esp",
    "aarch64-none-elf-": "gnu-aarch64",
}


@dataclass
class ToolInfo:
    name: str
    path: str | None
    version: str | None


@dataclass
class MakefileInfo:
    path: Path
    variables: dict[str, str] = field(default_factory=dict)
    cross_compile: str | None = None
    cc: str | None = None
    target: str | None = None
    mcu_hint: str | None = None
    toolchain_family: str | None = None


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
    build_dir: str | None = None
    make_target: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具探测
# ---------------------------------------------------------------------------

def _get_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = (result.stdout or result.stderr).strip().split("\n")[0]
        return first_line if first_line else None
    except Exception:
        return None


def find_tool(name: str, alt_names: list[str] | None = None) -> ToolInfo:
    configured = get_tool_path(name)
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            version = _get_version(configured_path)
            return ToolInfo(name=name, path=configured_path, version=version)

    candidates = [name] + (alt_names or [])
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            version = _get_version(path)
            return ToolInfo(name=candidate, path=path, version=version)
    return ToolInfo(name=name, path=None, version=None)


def find_make() -> ToolInfo:
    configured = get_tool_path("make")
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            version = _get_version(configured_path)
            return ToolInfo(name="make", path=configured_path, version=version)

    for name in MAKE_NAMES:
        path = shutil.which(name)
        if path:
            version = _get_version(path)
            return ToolInfo(name=name, path=path, version=version)
    return ToolInfo(name="make", path=None, version=None)


def detect_environment() -> dict[str, Any]:
    make = find_make()
    arm_gcc = find_tool("arm-none-eabi-gcc")
    riscv_gcc = find_tool("riscv32-unknown-elf-gcc", ["riscv64-unknown-elf-gcc"])
    objdump = find_tool("arm-none-eabi-objdump")

    return {
        "make": {"available": make.path is not None, "path": make.path, "version": make.version},
        "arm_gcc": {"available": arm_gcc.path is not None, "path": arm_gcc.path, "version": arm_gcc.version},
        "riscv_gcc": {"available": riscv_gcc.path is not None, "path": riscv_gcc.path, "version": riscv_gcc.version},
        "objdump": {"available": objdump.path is not None, "path": objdump.path, "version": objdump.version},
    }


# ---------------------------------------------------------------------------
# Makefile 发现与解析
# ---------------------------------------------------------------------------

def find_makefile(workspace: Path, max_depth: int = 2) -> list[Path]:
    results: list[tuple[int, Path]] = []
    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > max_depth:
            continue
        for fname in files:
            if fname in MAKEFILE_NAMES:
                results.append((depth, Path(root) / fname))
    results.sort(key=lambda x: x[0])
    return [p for _, p in results]


def parse_makefile(makefile_path: Path) -> MakefileInfo:
    info = MakefileInfo(path=makefile_path)

    try:
        text = makefile_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return info

    # 提取变量赋值：VAR = ..., VAR ?= ..., VAR := ...
    var_pattern = re.compile(r"^(?:export\s+)?(\w+)\s*[:?]?=\s*(.+?)$", re.MULTILINE)
    for m in var_pattern.finditer(text):
        key, value = m.group(1), m.group(2).strip()
        info.variables[key] = value

    info.cross_compile = info.variables.get("CROSS_COMPILE")
    info.cc = info.variables.get("CC")
    info.target = info.variables.get("TARGET") or info.variables.get("PROJECT")
    info.mcu_hint = info.variables.get("MCU") or info.variables.get("CPU") or info.variables.get("CORTEX_M")

    # 推断工具链家族
    prefix = info.cross_compile or ""
    if prefix:
        for known_prefix, family in CROSS_COMPILE_MAP.items():
            if prefix.startswith(known_prefix) or prefix == known_prefix:
                info.toolchain_family = family
                break
    elif info.cc:
        if "arm-none-eabi" in info.cc:
            info.toolchain_family = "gnu-arm"
        elif "riscv" in info.cc:
            info.toolchain_family = "gnu-riscv"
        elif "xtensa" in info.cc:
            info.toolchain_family = "gnu-esp"

    # 从 CFLAGS 提取 -mcpu=
    cflags = info.variables.get("CFLAGS", "")
    if not info.mcu_hint:
        mcpu_match = re.search(r"-mcpu=(\S+)", cflags)
        if mcpu_match:
            info.mcu_hint = mcpu_match.group(1)

    return info


def guess_mcu(makefile_info: MakefileInfo) -> str | None:
    if makefile_info.mcu_hint:
        return makefile_info.mcu_hint

    # 尝试从链接脚本名推断
    ldflags = makefile_info.variables.get("LDFLAGS", "")
    ld_match = re.search(r"-T\s*(\S*?)(\.ld|\.lds|\.x)", ldflags)
    if ld_match:
        script_name = ld_match.group(1).lower()
        for prefix in ("stm32", "gd32", "esp", "ch32", "hc32"):
            if prefix in script_name:
                return script_name.split("_")[0] if "_" in script_name else script_name

    # 从工具链家族推断大类
    if makefile_info.toolchain_family == "gnu-arm":
        return "arm-cortex-m"
    if makefile_info.toolchain_family == "gnu-riscv":
        return "riscv"
    return None


def list_makefile_targets(makefile_path: Path, make_cmd: str) -> list[str]:
    try:
        result = subprocess.run(
            [make_cmd, "-pn", "-C", str(makefile_path.parent),
             "-f", str(makefile_path.name)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _parse_targets_from_file(makefile_path)

    targets: list[str] = []
    seen: set[str] = set()
    for line in (result.stdout + result.stderr).splitlines():
        m = re.match(r"^([a-zA-Z_][\w.-]*)\s*:", line)
        if m:
            name = m.group(1)
            if name not in seen and not name.startswith("."):
                seen.add(name)
                targets.append(name)
    return targets if targets else _parse_targets_from_file(makefile_path)


def _parse_targets_from_file(makefile_path: Path) -> list[str]:
    try:
        text = makefile_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    targets: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"^([a-zA-Z_][\w.-]*)\s*:", text, re.MULTILINE):
        name = m.group(1)
        if name not in seen and not name.startswith("."):
            seen.add(name)
            targets.append(name)
    return targets


def is_cmake_generated(makefile_path: Path) -> bool:
    try:
        text = makefile_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "Generated by CMake" in text or "CMAKE" in text[:500]


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


def pick_primary_artifact(artifacts: list[Artifact]) -> Artifact | None:
    if not artifacts:
        return None
    return artifacts[0]


def resolve_build_dir(source_dir: Path, build_dir: str | None = None) -> Path:
    if build_dir:
        return Path(build_dir).resolve()

    for candidate in ["build", "Build", "output", "Output", "out"]:
        d = source_dir / candidate
        if d.is_dir():
            return d.resolve()
    return source_dir.resolve()


def scan_all_artifact_dirs(source_dir: Path) -> list[Artifact]:
    """Scan source dir itself + standard build subdirectories for artifacts."""
    all_artifacts: list[Artifact] = []
    seen: set[str] = set()

    dirs_to_scan = [source_dir]
    for candidate in ["build", "Build", "output", "Output", "out"]:
        d = source_dir / candidate
        if d.is_dir():
            dirs_to_scan.append(d)

    for scan_dir in dirs_to_scan:
        for artifact in scan_artifacts(scan_dir):
            real = str(artifact.path.resolve())
            if real not in seen:
                seen.add(real)
                all_artifacts.append(artifact)

    all_artifacts.sort(key=lambda a: (ARTIFACT_PRIORITY.get(a.kind, 9), -a.size))
    return all_artifacts


# ---------------------------------------------------------------------------
# 构建执行
# ---------------------------------------------------------------------------

def run_make_build(
    source_dir: Path,
    make_cmd: str,
    target: str | None = None,
    jobs: int | None = None,
    verbose: bool = False,
    extra_args: list[str] | None = None,
) -> tuple[bool, str, list[str]]:
    cmd: list[str] = [make_cmd, "-C", str(source_dir)]

    if target:
        cmd.append(target)
    if jobs:
        cmd.extend(["-j", str(jobs)])
    if verbose:
        cmd.append("V=1")
    if extra_args:
        cmd.extend(extra_args)

    cmd_str = " ".join(cmd)
    print(f"🔨 构建命令: {cmd_str}")

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, cmd_str, ["❌ 构建超时（600 秒）"]
    except FileNotFoundError:
        return False, cmd_str, [f"❌ 未找到 {make_cmd} 命令"]

    elapsed = time.time() - start
    evidence: list[str] = []
    output = (result.stdout + "\n" + result.stderr).strip()

    if result.returncode != 0:
        last_lines = output.split("\n")[-30:]
        evidence.append("构建失败输出（末尾）:")
        evidence.extend(last_lines)
        return False, cmd_str, evidence

    print(f"✅ 构建成功（耗时 {elapsed:.1f} 秒）")
    evidence.append(f"构建耗时: {elapsed:.1f} 秒")
    return True, cmd_str, evidence


def run_make_clean(source_dir: Path, make_cmd: str) -> tuple[bool, str]:
    cmd = [make_cmd, "-C", str(source_dir), "clean"]
    cmd_str = " ".join(cmd)
    print(f"🗑️ 清理命令: {cmd_str}")
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return True, cmd_str
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("  ⚠️ clean 目标失败（可忽略）")
        return False, cmd_str


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 构建环境探测结果：")
    for tool_name in ["make", "arm_gcc", "riscv_gcc", "objdump"]:
        info = env[tool_name]
        status = "✅" if info["available"] else "❌"
        ver = f" ({info['version']})" if info.get("version") else ""
        path = f" @ {info['path']}" if info.get("path") else ""
        print(f"  {status} {tool_name}{ver}{path}")


def print_makefile_report(info: MakefileInfo) -> None:
    print(f"\n📄 Makefile 解析结果: {info.path}")
    print(f"  CROSS_COMPILE:   {info.cross_compile or '(未设置)'}")
    print(f"  CC:              {info.cc or '(未设置)'}")
    print(f"  TARGET:          {info.target or '(未设置)'}")
    print(f"  MCU hint:        {info.mcu_hint or '(未设置)'}")
    print(f"  工具链家族:      {info.toolchain_family or '(未知)'}")

    if info.variables:
        interesting = {k: v for k, v in info.variables.items()
                       if k in ("MCU", "CPU", "BOARD", "CFLAGS", "LDFLAGS", "CROSS_COMPILE",
                                "CC", "CXX", "TARGET", "PROJECT", "OBJCOPY", "SIZE")}
        if interesting:
            print("\n  关键变量:")
            for k, v in sorted(interesting.items()):
                print(f"    {k} = {v}")


def print_build_report(result: BuildResult) -> None:
    status_icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {status_icon} {result.summary}")

    if result.build_cmd:
        print(f"\n  构建命令: {result.build_cmd}")
    if result.build_dir:
        print(f"  构建目录: {result.build_dir}")
    if result.make_target:
        print(f"  Make 目标: {result.make_target}")

    if result.artifacts:
        print(f"\n📦 找到 {len(result.artifacts)} 个固件产物：")
        for i, a in enumerate(result.artifacts):
            size_kb = a.size / 1024
            primary = " ⭐ 首选" if a == result.primary_artifact else ""
            print(f"  {i + 1}. [{a.kind.upper()}] {a.path} ({size_kb:.1f} KB){primary}")
    elif result.status == "success":
        print("\n  ⚠️ 构建成功但未找到固件产物")

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
        description="裸 Makefile 嵌入式构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --parse-makefile --source /repo/fw
  %(prog)s --list-targets --source /repo/fw
  %(prog)s --source /repo/fw
  %(prog)s --source /repo/fw --target all --clean
  %(prog)s --scan-artifacts /repo/fw/build
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测构建环境")
    parser.add_argument("--source", help="Makefile 源码目录")
    parser.add_argument("--makefile", help="显式指定 Makefile 路径（覆盖自动探测）")
    parser.add_argument("--target", help="Make 目标名称（默认：all）")
    parser.add_argument("--list-targets", action="store_true", help="列出 Makefile 中的可用目标")
    parser.add_argument("--parse-makefile", action="store_true", help="解析并显示 Makefile 变量（不构建）")
    parser.add_argument("--build-dir", help="覆盖产物扫描目录")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的产物")
    parser.add_argument("--clean", action="store_true", help="构建前执行 make clean")
    parser.add_argument("--extra-args", action="append", default=[], help="传递给 make 的额外参数（可重复）")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细构建输出（V=1）")
    parser.add_argument("-j", "--jobs", type=int, help="并行构建任务数")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 环境探测模式
    if args.detect:
        env = detect_environment()
        print_detect_report(env)
        if args.save_config:
            for tool_key in ["make", "arm_gcc", "riscv_gcc", "objdump"]:
                info = env[tool_key]
                if info["available"]:
                    cfg_path = set_tool_path(tool_key.replace("_", "-"), info["path"])
                    print(f"  💾 {tool_key} 已保存到 {cfg_path}")
        return 0 if env["make"]["available"] else 1

    # 仅扫描产物模式
    if args.scan_artifacts:
        scan_dir = Path(args.scan_artifacts).resolve()
        artifacts = scan_artifacts(scan_dir)
        if not artifacts:
            print(f"❌ 在 {scan_dir} 中未找到固件产物")
            return 1
        primary = pick_primary_artifact(artifacts)
        result = BuildResult(
            status="success",
            summary=f"找到 {len(artifacts)} 个产物",
            build_dir=str(scan_dir),
            artifacts=artifacts,
            primary_artifact=primary,
        )
        print_build_report(result)
        return 0

    # 需要源码目录
    if not args.source:
        print("❌ 请提供 --source（源码目录）。")
        return 1

    source_dir = Path(args.source).resolve()

    # 查找 Makefile
    makefile_path = None
    if args.makefile:
        makefile_path = Path(args.makefile).resolve()
        if not makefile_path.exists():
            print(f"❌ 指定的 Makefile 不存在: {makefile_path}")
            return 1
    else:
        candidates = find_makefile(source_dir, max_depth=1)
        if not candidates:
            print(f"❌ 在 {source_dir} 中未找到 Makefile")
            return 1
        makefile_path = candidates[0]
        if len(candidates) > 1:
            print(f"ℹ️ 找到多个 Makefile，使用: {makefile_path}")

    # 检查是否是 CMake 生成的 Makefile
    if is_cmake_generated(makefile_path):
        print("⚠️ 检测到 CMake 生成的 Makefile，建议使用 build-cmake skill 代替。")

    # 解析 Makefile 模式
    if args.parse_makefile:
        info = parse_makefile(makefile_path)
        print_makefile_report(info)
        mcu = guess_mcu(info)
        if mcu:
            print(f"\n  推断 MCU: {mcu}")
        return 0

    # 检查 make 是否可用
    make_info = find_make()
    if not make_info.path:
        print("❌ 未找到 make，请先安装。")
        return 1

    # 列出目标模式
    if args.list_targets:
        targets = list_makefile_targets(makefile_path, make_info.path)
        if not targets:
            print("❌ 未找到可用的 make 目标")
            return 1
        print("📋 可用 Make 目标：")
        for i, t in enumerate(targets, 1):
            default = " (默认)" if t == "all" else ""
            print(f"  {i}. {t}{default}")
        return 0

    # 构建模式
    if args.clean:
        run_make_clean(source_dir, make_info.path)

    ok, bld_cmd, evidence = run_make_build(
        source_dir=source_dir,
        make_cmd=make_info.path,
        target=args.target,
        jobs=args.jobs,
        verbose=args.verbose,
        extra_args=args.extra_args,
    )

    if not ok:
        result = BuildResult(
            status="failure",
            summary="Make 构建失败",
            build_cmd=bld_cmd,
            build_dir=str(source_dir),
            make_target=args.target,
            failure_category="project-config-error",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    # 扫描产物
    scan_dir = resolve_build_dir(source_dir, args.build_dir)
    artifacts = scan_all_artifact_dirs(source_dir)
    if args.build_dir:
        artifacts = scan_artifacts(scan_dir)

    primary = pick_primary_artifact(artifacts)

    if not artifacts:
        result = BuildResult(
            status="success",
            summary="构建成功但未找到固件产物",
            build_cmd=bld_cmd,
            build_dir=str(scan_dir),
            make_target=args.target,
            artifacts=[],
            failure_category="artifact-missing",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    result = BuildResult(
        status="success",
        summary=f"构建成功，找到 {len(artifacts)} 个产物",
        build_cmd=bld_cmd,
        build_dir=str(scan_dir),
        make_target=args.target,
        artifacts=artifacts,
        primary_artifact=primary,
        evidence=evidence,
    )
    print_build_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
