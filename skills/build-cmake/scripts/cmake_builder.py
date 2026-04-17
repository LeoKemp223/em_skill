#!/usr/bin/env python
"""通用嵌入式 CMake 构建工具。

这个脚本为 `build-cmake` skill 提供可重复调用的执行入口，支持：

- 探测构建环境（cmake、生成器、编译器）
- 扫描 CMakePresets.json 并列出可用预设
- 执行 cmake configure + build 全流程
- 在构建目录中搜索 ELF、HEX、BIN 产物并按优先级排序
- 输出结构化的构建结果和分析报告
"""

from __future__ import annotations

import argparse
import json
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
ARTIFACT_EXTENSIONS = {".elf": "elf", ".hex": "hex", ".bin": "bin", ".axf": "elf"}
GENERATOR_PRIORITY = ["Ninja", "Unix Makefiles", "MinGW Makefiles", "NMake Makefiles"]

@dataclass
class ToolInfo:
    name: str
    path: str | None
    version: str | None


@dataclass
class Preset:
    name: str
    display_name: str
    description: str
    generator: str | None
    build_type: str | None
    toolchain: str | None


@dataclass
class Artifact:
    path: Path
    kind: str
    size: int


@dataclass
class BuildResult:
    status: str  # success, failure, blocked
    summary: str
    configure_cmd: str | None = None
    build_cmd: str | None = None
    build_dir: str | None = None
    generator: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具探测
# ---------------------------------------------------------------------------

def find_tool(name: str, alt_names: list[str] | None = None) -> ToolInfo:
    # 配置文件
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


def detect_generator() -> str | None:
    for gen_name in GENERATOR_PRIORITY:
        if gen_name == "Ninja" and shutil.which("ninja"):
            return "Ninja"
        if gen_name == "Unix Makefiles" and (shutil.which("make") or shutil.which("gmake")):
            return "Unix Makefiles"
        if gen_name == "MinGW Makefiles" and shutil.which("mingw32-make"):
            return "MinGW Makefiles"
        if gen_name == "NMake Makefiles" and shutil.which("nmake"):
            return "NMake Makefiles"
    return None


def detect_environment() -> dict[str, Any]:
    cmake = find_tool("cmake", ["cmake.exe"])
    ninja = find_tool("ninja", ["ninja.exe"])
    make = find_tool("make", ["gmake", "mingw32-make"])
    arm_gcc = find_tool("arm-none-eabi-gcc")
    generator = detect_generator()

    env = {
        "cmake": {"available": cmake.path is not None, "path": cmake.path, "version": cmake.version},
        "ninja": {"available": ninja.path is not None, "path": ninja.path, "version": ninja.version},
        "make": {"available": make.path is not None, "path": make.path, "version": make.version},
        "arm_gcc": {"available": arm_gcc.path is not None, "path": arm_gcc.path, "version": arm_gcc.version},
        "preferred_generator": generator,
    }
    return env


# ---------------------------------------------------------------------------
# CMakePresets.json 解析
# ---------------------------------------------------------------------------

def load_presets(source_dir: Path) -> list[Preset]:
    presets_file = source_dir / "CMakePresets.json"
    if not presets_file.exists():
        return []

    try:
        data = json.loads(presets_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"⚠️ 无法解析 CMakePresets.json: {exc}")
        return []

    configure_presets = data.get("configurePresets", [])
    results: list[Preset] = []
    for p in configure_presets:
        if p.get("hidden", False):
            continue
        cache_vars = p.get("cacheVariables", {})
        results.append(Preset(
            name=p.get("name", ""),
            display_name=p.get("displayName", p.get("name", "")),
            description=p.get("description", ""),
            generator=p.get("generator"),
            build_type=cache_vars.get("CMAKE_BUILD_TYPE"),
            toolchain=p.get("toolchainFile") or cache_vars.get("CMAKE_TOOLCHAIN_FILE"),
        ))
    return results


def list_presets_display(source_dir: Path) -> list[Preset]:
    presets = load_presets(source_dir)
    if not presets:
        print("❌ 未找到可用的 CMake 预设")
        presets_file = source_dir / "CMakePresets.json"
        if not presets_file.exists():
            print(f"   {presets_file} 不存在")
        return []

    print("📋 可用 CMake 预设：")
    for i, p in enumerate(presets, 1):
        gen_info = f" [{p.generator}]" if p.generator else ""
        bt_info = f" ({p.build_type})" if p.build_type else ""
        desc = f" - {p.description}" if p.description else ""
        print(f"  {i}. {p.name}{gen_info}{bt_info}{desc}")
    return presets


# ---------------------------------------------------------------------------
# CMakeLists.txt 扫描
# ---------------------------------------------------------------------------

def scan_cmakelists(source_dir: Path) -> dict[str, str | None]:
    cmakelists = source_dir / "CMakeLists.txt"
    info: dict[str, str | None] = {"project_name": None, "toolchain_hint": None}
    if not cmakelists.exists():
        return info

    try:
        content = cmakelists.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return info

    project_match = re.search(r"project\s*\(\s*(\w+)", content, re.IGNORECASE)
    if project_match:
        info["project_name"] = project_match.group(1)

    tc_match = re.search(r"CMAKE_TOOLCHAIN_FILE\s+[\"']?([^\s\"')]+)", content)
    if tc_match:
        info["toolchain_hint"] = tc_match.group(1)

    return info


# ---------------------------------------------------------------------------
# 产物扫描
# ---------------------------------------------------------------------------

def scan_artifacts(build_dir: Path) -> list[Artifact]:
    if not build_dir.exists():
        return []

    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for root, _dirs, files in os.walk(build_dir):
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


# ---------------------------------------------------------------------------
# 构建执行
# ---------------------------------------------------------------------------

def run_cmake_configure(
    source_dir: Path,
    build_dir: Path,
    preset: str | None,
    generator: str | None,
    build_type: str | None,
    toolchain: str | None,
    extra_args: list[str],
) -> tuple[bool, str, list[str]]:
    cmd: list[str] = ["cmake"]

    if preset:
        cmd.extend(["--preset", preset])
        if source_dir:
            cmd.extend(["-S", str(source_dir)])
    else:
        cmd.extend(["-S", str(source_dir), "-B", str(build_dir)])
        if generator:
            cmd.extend(["-G", generator])
        if build_type:
            cmd.append(f"-DCMAKE_BUILD_TYPE={build_type}")
        if toolchain:
            cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain}")

    cmd.extend(extra_args)
    cmd_str = " ".join(cmd)
    print(f"🔧 配置命令: {cmd_str}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, cmd_str, ["❌ CMake 配置超时（120 秒）"]
    except FileNotFoundError:
        return False, cmd_str, ["❌ 未找到 cmake 命令"]

    evidence = []
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        last_lines = output.split("\n")[-20:]
        evidence.append("配置失败输出（末尾）:")
        evidence.extend(last_lines)
        return False, cmd_str, evidence

    print("✅ CMake 配置成功")
    return True, cmd_str, evidence


def run_cmake_build(
    build_dir: Path,
    preset: str | None,
    target: str | None,
    jobs: int | None,
    verbose: bool,
) -> tuple[bool, str, list[str]]:
    cmd: list[str] = ["cmake", "--build"]

    if preset:
        cmd.extend(["--preset", preset])
    else:
        cmd.append(str(build_dir))

    if target:
        cmd.extend(["--target", target])
    if jobs:
        cmd.extend(["-j", str(jobs)])
    if verbose:
        cmd.append("--verbose")

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
        return False, cmd_str, ["❌ 未找到 cmake 命令"]

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


def clean_build_dir(build_dir: Path) -> None:
    if build_dir.exists():
        print(f"🗑️ 清理构建目录: {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)


def resolve_build_dir(source_dir: Path, build_dir: str | None, preset: str | None) -> Path:
    if build_dir:
        return Path(build_dir).resolve()

    if preset:
        presets = load_presets(source_dir)
        for p in presets:
            if p.name == preset:
                candidate = source_dir / "build" / p.name
                return candidate.resolve()

    return (source_dir / "build").resolve()


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 构建环境探测结果：")
    for tool_name in ["cmake", "ninja", "make", "arm_gcc"]:
        info = env[tool_name]
        status = "✅" if info["available"] else "❌"
        ver = f" ({info['version']})" if info.get("version") else ""
        path = f" @ {info['path']}" if info.get("path") else ""
        print(f"  {status} {tool_name}{ver}{path}")

    gen = env.get("preferred_generator")
    if gen:
        print(f"\n  首选生成器: {gen}")
    else:
        print("\n  ⚠️ 未找到可用的生成器（需要 ninja 或 make）")


def print_build_report(result: BuildResult) -> None:
    status_icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {status_icon} {result.summary}")

    if result.configure_cmd:
        print(f"\n  配置命令: {result.configure_cmd}")
    if result.build_cmd:
        print(f"  构建命令: {result.build_cmd}")
    if result.build_dir:
        print(f"  构建目录: {result.build_dir}")
    if result.generator:
        print(f"  生成器:   {result.generator}")

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
        description="嵌入式 CMake 构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --list-presets --source /repo/fw
  %(prog)s --source /repo/fw --preset debug
  %(prog)s --source /repo/fw --build-dir build --build-type Debug
  %(prog)s --scan-artifacts /repo/fw/build/debug
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测构建环境")
    parser.add_argument("--source", help="CMake 源码目录")
    parser.add_argument("--build-dir", help="构建输出目录")
    parser.add_argument("--preset", help="CMake 预设名称")
    parser.add_argument("--list-presets", action="store_true", help="列出可用预设")
    parser.add_argument("--generator", help="CMake 生成器")
    parser.add_argument("--build-type", help="构建类型: Debug, Release, RelWithDebInfo, MinSizeRel")
    parser.add_argument("--toolchain", help="工具链文件路径")
    parser.add_argument("--target", help="构建目标名称")
    parser.add_argument("--clean", action="store_true", help="构建前清理")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的产物")
    parser.add_argument("--extra-args", action="append", default=[], help="传递给 cmake 的额外参数")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
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
            for tool_key in ["cmake", "ninja", "make", "arm_gcc"]:
                info = env[tool_key]
                if info["available"]:
                    cfg_path = set_tool_path(tool_key.replace("_", "-"), info["path"])
                    print(f"  💾 {tool_key} 已保存到 {cfg_path}")
        return 0 if env["cmake"]["available"] else 1

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

    # 列出预设模式
    if args.list_presets:
        source_dir = Path(args.source or ".").resolve()
        presets = list_presets_display(source_dir)
        return 0 if presets else 1

    # 构建模式 - 需要源码目录
    if not args.source and not args.preset:
        print("❌ 请提供 --source（源码目录）或 --preset（预设名称）。")
        return 1

    source_dir = Path(args.source or ".").resolve()
    if not (source_dir / "CMakeLists.txt").exists() and not args.preset:
        print(f"❌ 在 {source_dir} 中未找到 CMakeLists.txt")
        return 1

    # 检查 cmake 是否可用
    cmake_info = find_tool("cmake", ["cmake.exe"])
    if not cmake_info.path:
        print("❌ 未找到 cmake，请先安装。")
        return 1

    # 解析构建目录
    build_dir = resolve_build_dir(source_dir, args.build_dir, args.preset)

    # 选择生成器
    generator = args.generator
    if not generator and not args.preset:
        generator = detect_generator()
        if generator:
            print(f"ℹ️ 自动选择生成器: {generator}")
        else:
            print("⚠️ 未找到 Ninja 或 Make，将使用 CMake 默认生成器")

    # 选择构建类型
    build_type = args.build_type
    if not build_type and not args.preset:
        build_type = "Debug"
        print(f"ℹ️ 未指定构建类型，默认使用: {build_type}")

    # 清理
    if args.clean:
        clean_build_dir(build_dir)

    # 配置
    ok, conf_cmd, conf_evidence = run_cmake_configure(
        source_dir=source_dir,
        build_dir=build_dir,
        preset=args.preset,
        generator=generator,
        build_type=build_type,
        toolchain=args.toolchain,
        extra_args=args.extra_args,
    )
    if not ok:
        result = BuildResult(
            status="failure",
            summary="CMake 配置失败",
            configure_cmd=conf_cmd,
            build_dir=str(build_dir),
            generator=generator,
            failure_category="project-config-error",
            evidence=conf_evidence,
        )
        print_build_report(result)
        return 1

    # 构建
    ok, bld_cmd, bld_evidence = run_cmake_build(
        build_dir=build_dir,
        preset=args.preset,
        target=args.target,
        jobs=args.jobs,
        verbose=args.verbose,
    )
    all_evidence = conf_evidence + bld_evidence
    if not ok:
        result = BuildResult(
            status="failure",
            summary="CMake 构建失败",
            configure_cmd=conf_cmd,
            build_cmd=bld_cmd,
            build_dir=str(build_dir),
            generator=generator,
            failure_category="project-config-error",
            evidence=all_evidence,
        )
        print_build_report(result)
        return 1

    # 扫描产物
    artifacts = scan_artifacts(build_dir)
    primary = pick_primary_artifact(artifacts)

    if not artifacts:
        result = BuildResult(
            status="success",
            summary="构建成功但未找到固件产物",
            configure_cmd=conf_cmd,
            build_cmd=bld_cmd,
            build_dir=str(build_dir),
            generator=generator,
            artifacts=[],
            failure_category="artifact-missing",
            evidence=all_evidence,
        )
        print_build_report(result)
        return 1

    result = BuildResult(
        status="success",
        summary=f"构建成功，找到 {len(artifacts)} 个产物",
        configure_cmd=conf_cmd,
        build_cmd=bld_cmd,
        build_dir=str(build_dir),
        generator=generator,
        artifacts=artifacts,
        primary_artifact=primary,
        evidence=all_evidence,
    )
    print_build_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
