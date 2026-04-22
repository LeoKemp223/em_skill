#!/usr/bin/env python3
"""ESP-IDF 构建工具。

为 build-idf skill 提供可重复调用的执行入口，支持：
- 探测 ESP-IDF 构建环境
- 设置目标芯片
- 执行 idf.py build
- 扫描构建产物
"""

from __future__ import annotations

import argparse
import json
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

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _SCRIPT_DIR.parent.parent
for _candidate in [_SKILLS_DIR / "shared", _SKILLS_DIR.parent / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
from tool_config import get_tool_path, set_tool_path
from idf_env import get_idf_env

KNOWN_TARGETS = ["esp32", "esp32s2", "esp32s3", "esp32c2", "esp32c3", "esp32c5", "esp32c6", "esp32c61", "esp32h2", "esp32p4"]

@dataclass
class Artifact:
    path: Path
    kind: str
    size: int


@dataclass
class BuildResult:
    status: str
    summary: str
    build_cmd: str | None = None
    build_dir: str | None = None
    target_chip: str | None = None
    idf_version: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


def _read_sdkconfig_target(project_dir: Path) -> str | None:
    sdkconfig = project_dir / "sdkconfig"
    if not sdkconfig.exists():
        return None
    try:
        content = sdkconfig.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r'^CONFIG_IDF_TARGET="(\w+)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return None


def _is_idf_project(project_dir: Path) -> bool:
    return (project_dir / "CMakeLists.txt").exists() and (project_dir / "main").is_dir()


def detect_environment() -> dict[str, Any]:
    idf_env = get_idf_env()
    idf_path = idf_env.env.get("IDF_PATH") if idf_env else os.environ.get("IDF_PATH")
    return {
        "idf_py": {
            "available": idf_env is not None,
            "command": " ".join(idf_env.idf_py_cmd) if idf_env else None,
            "source": idf_env.source if idf_env else None,
        },
        "idf_version": idf_env.version if idf_env else None,
        "idf_path": idf_path,
        "idf_path_valid": bool(idf_path and Path(idf_path).is_dir()),
        "known_targets": KNOWN_TARGETS,
    }


def set_target(project_dir: Path, target: str) -> tuple[bool, list[str]]:
    idf_env = get_idf_env()
    if not idf_env:
        return False, ["❌ idf.py 不可用"]
    if target not in KNOWN_TARGETS:
        return False, [f"❌ 未知目标芯片: {target}，支持: {', '.join(KNOWN_TARGETS)}"]

    cmd = idf_env.idf_py_cmd + ["set-target", target]
    cmd_str = " ".join(cmd)
    print(f"🎯 设置目标: {cmd_str}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                cwd=str(project_dir), env=idf_env.env)
    except subprocess.TimeoutExpired:
        return False, ["❌ set-target 超时（120 秒）"]

    if result.returncode != 0:
        last_lines = (result.stdout + "\n" + result.stderr).strip().split("\n")[-10:]
        return False, [f"❌ set-target 失败:"] + last_lines

    print(f"✅ 目标芯片已设置为 {target}")
    return True, []


def build_project(project_dir: Path, verbose: bool = False) -> BuildResult:
    idf_env = get_idf_env()
    if not idf_env:
        return BuildResult(status="failure", summary="idf.py 不可用",
                           failure_category="environment-missing")

    if not _is_idf_project(project_dir):
        return BuildResult(status="failure", summary=f"{project_dir} 不是有效的 ESP-IDF 工程",
                           failure_category="project-config-error",
                           evidence=["需要 CMakeLists.txt 和 main/ 目录"])

    cmd = idf_env.idf_py_cmd + ["build"]
    if verbose:
        cmd.append("-v")
    cmd_str = " ".join(cmd)
    print(f"🔨 构建命令: {cmd_str}")

    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                cwd=str(project_dir), env=idf_env.env)
    except subprocess.TimeoutExpired:
        return BuildResult(status="failure", summary="构建超时（600 秒）",
                           build_cmd=cmd_str, failure_category="project-config-error")

    elapsed = time.time() - start
    evidence: list[str] = [f"构建耗时: {elapsed:.1f} 秒"]
    output = (result.stdout + "\n" + result.stderr).strip()

    if result.returncode != 0:
        last_lines = output.split("\n")[-20:]
        evidence.append("构建失败输出（末尾）:")
        evidence.extend(last_lines)
        return BuildResult(status="failure", summary="构建失败", build_cmd=cmd_str,
                           build_dir=str(project_dir / "build"),
                           failure_category="project-config-error", evidence=evidence)

    print(f"✅ 构建成功（耗时 {elapsed:.1f} 秒）")
    build_dir = project_dir / "build"
    artifacts = scan_artifacts(build_dir)
    primary = artifacts[0] if artifacts else None
    target = _read_sdkconfig_target(project_dir)

    return BuildResult(
        status="success",
        summary=f"构建成功，找到 {len(artifacts)} 个产物",
        build_cmd=cmd_str, build_dir=str(build_dir),
        target_chip=target, idf_version=idf_env.version,
        artifacts=artifacts, primary_artifact=primary, evidence=evidence,
    )


def scan_artifacts(build_dir: Path) -> list[Artifact]:
    if not build_dir.exists():
        return []
    artifacts: list[Artifact] = []
    for ext, kind in [(".elf", "elf"), (".bin", "bin")]:
        for f in build_dir.glob(f"*{ext}"):
            if f.is_file() and f.stat().st_size > 256:
                artifacts.append(Artifact(path=f, kind=kind, size=f.stat().st_size))
    flasher_args = build_dir / "flasher_args.json"
    if flasher_args.exists():
        try:
            data = json.loads(flasher_args.read_text(encoding="utf-8"))
            for section in data.get("flash_files", {}).values():
                p = build_dir / section if isinstance(section, str) else None
                if p and p.exists() and p.suffix == ".bin" and not any(a.path == p for a in artifacts):
                    artifacts.append(Artifact(path=p, kind="bin", size=p.stat().st_size))
        except (json.JSONDecodeError, OSError):
            pass
    artifacts.sort(key=lambda a: (0 if a.kind == "elf" else 1, -a.size))
    return artifacts


def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 ESP-IDF 构建环境探测结果：")
    idf = env["idf_py"]
    status = "✅" if idf["available"] else "❌"
    print(f"  {status} idf.py: {idf.get('command', '未找到')}")
    if env["idf_version"]:
        print(f"  版本: {env['idf_version']}")
    if env["idf_path"]:
        valid = "✅" if env["idf_path_valid"] else "⚠️"
        print(f"  {valid} IDF_PATH: {env['idf_path']}")
    else:
        print("  ⚠️ IDF_PATH 未设置")
    print(f"  支持的目标芯片: {', '.join(env['known_targets'])}")


def print_build_report(result: BuildResult) -> None:
    status_icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {status_icon} {result.summary}")
    if result.build_cmd:
        print(f"  构建命令: {result.build_cmd}")
    if result.build_dir:
        print(f"  构建目录: {result.build_dir}")
    if result.target_chip:
        print(f"  目标芯片: {result.target_chip}")
    if result.idf_version:
        print(f"  IDF 版本: {result.idf_version}")
    if result.artifacts:
        print(f"\n📦 找到 {len(result.artifacts)} 个固件产物：")
        for i, a in enumerate(result.artifacts):
            size_kb = a.size / 1024
            primary = " ⭐ 首选" if a == result.primary_artifact else ""
            print(f"  {i + 1}. [{a.kind.upper()}] {a.path} ({size_kb:.1f} KB){primary}")
    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:15]:
            print(f"  {line}")
    if result.failure_category:
        print(f"\n  失败分类: {result.failure_category}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ESP-IDF 构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --set-target esp32s3 --project /repo/fw
  %(prog)s --build --project /repo/fw
  %(prog)s --scan-artifacts /repo/fw/build
  %(prog)s --clean --project /repo/fw
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测 ESP-IDF 构建环境")
    parser.add_argument("--build", action="store_true", help="执行构建")
    parser.add_argument("--project", help="ESP-IDF 工程目录路径")
    parser.add_argument("--set-target", help="设置目标芯片")
    parser.add_argument("--clean", action="store_true", help="执行 fullclean")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的构建产物")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.detect:
        env = detect_environment()
        print_detect_report(env)
        return 0 if env["idf_py"]["available"] else 1

    if args.scan_artifacts:
        scan_dir = Path(args.scan_artifacts).resolve()
        artifacts = scan_artifacts(scan_dir)
        if not artifacts:
            print(f"❌ 在 {scan_dir} 中未找到固件产物")
            return 1
        result = BuildResult(status="success", summary=f"找到 {len(artifacts)} 个产物",
                             build_dir=str(scan_dir), artifacts=artifacts,
                             primary_artifact=artifacts[0] if artifacts else None)
        print_build_report(result)
        return 0

    if args.set_target:
        if not args.project:
            print("❌ 请通过 --project 指定工程目录")
            return 1
        project_dir = Path(args.project).resolve()
        ok, errors = set_target(project_dir, args.set_target)
        for e in errors:
            print(f"  {e}")
        return 0 if ok else 1

    if args.clean:
        if not args.project:
            print("❌ 请通过 --project 指定工程目录")
            return 1
        idf_env = get_idf_env()
        if not idf_env:
            print("❌ idf.py 不可用")
            return 1
        project_dir = Path(args.project).resolve()
        print("🗑️ 清理构建目录 ...")
        subprocess.run(idf_env.idf_py_cmd + ["fullclean"], cwd=str(project_dir),
                        timeout=60, env=idf_env.env)
        print("✅ 清理完成")
        return 0

    if args.build:
        if not args.project:
            print("❌ 请通过 --project 指定工程目录")
            return 1
        project_dir = Path(args.project).resolve()
        result = build_project(project_dir, verbose=args.verbose)
        print_build_report(result)
        return 0 if result.status == "success" else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
