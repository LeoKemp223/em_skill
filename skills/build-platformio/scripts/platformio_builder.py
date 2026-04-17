#!/usr/bin/env python
"""通用 PlatformIO 命令行构建工具。

这个脚本为 `build-platformio` skill 提供可重复调用的执行入口，支持：

- 探测 PlatformIO CLI (pio) 是否可用及版本
- 解析 platformio.ini 中的环境列表
- 执行 pio run 构建、清理和上传
- 列出已连接的设备
- 在 .pio/build/<env>/ 中搜索 firmware 产物
"""

from __future__ import annotations

import argparse
import configparser
import os
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


ARTIFACT_EXTENSIONS = {".elf": "elf", ".hex": "hex", ".bin": "bin"}
ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}


@dataclass
class PIOEnvironment:
    name: str
    platform: str
    board: str
    framework: str
    upload_protocol: str
    is_default: bool


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
    project_dir: str | None = None
    env_name: str | None = None
    board: str | None = None
    platform: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    primary_artifact: Artifact | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PlatformIO 探测
# ---------------------------------------------------------------------------

def find_pio() -> str | None:
    # 配置文件
    configured = get_tool_path("pio")
    if configured:
        configured_path = shutil.which(configured) or configured
        if Path(configured_path).exists():
            return configured_path

    return shutil.which("pio") or shutil.which("platformio")


def get_pio_version(pio_path: str) -> str | None:
    try:
        result = subprocess.run(
            [pio_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        return output if output else None
    except Exception:
        return None


def detect_environment() -> dict[str, Any]:
    pio_path = find_pio()
    version = get_pio_version(pio_path) if pio_path else None
    env: dict[str, Any] = {
        "pio": {
            "available": pio_path is not None,
            "path": pio_path,
            "version": version,
        },
    }
    return env


# ---------------------------------------------------------------------------
# platformio.ini 解析
# ---------------------------------------------------------------------------

def parse_platformio_ini(project_dir: Path) -> tuple[list[PIOEnvironment], list[str]]:
    """解析 platformio.ini，返回 (环境列表, 默认环境名列表)。"""
    ini_path = project_dir / "platformio.ini"
    if not ini_path.exists():
        return [], []

    config = configparser.ConfigParser()
    try:
        config.read(str(ini_path), encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as exc:
        print(f"⚠️ 无法解析 platformio.ini: {exc}")
        return [], []

    # 提取默认环境
    default_envs: list[str] = []
    if config.has_section("platformio"):
        raw = config.get("platformio", "default_envs", fallback="")
        if raw:
            default_envs = [e.strip() for e in raw.split(",") if e.strip()]

    # 提取环境列表
    envs: list[PIOEnvironment] = []
    for section in config.sections():
        if not section.startswith("env:"):
            continue
        env_name = section[4:]
        envs.append(PIOEnvironment(
            name=env_name,
            platform=config.get(section, "platform", fallback=""),
            board=config.get(section, "board", fallback=""),
            framework=config.get(section, "framework", fallback=""),
            upload_protocol=config.get(section, "upload_protocol", fallback=""),
            is_default=env_name in default_envs,
        ))

    return envs, default_envs


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


def resolve_build_dir(project_dir: Path, env_name: str) -> Path:
    return project_dir / ".pio" / "build" / env_name


# ---------------------------------------------------------------------------
# 构建执行
# ---------------------------------------------------------------------------

def run_pio_build(
    pio_path: str,
    project_dir: Path,
    env_name: str | None,
    target: str | None,
    jobs: int | None,
    verbose: bool,
) -> tuple[bool, str, list[str]]:
    """调用 pio run 执行构建。

    target: None (构建), "clean" (清理), "upload" (上传)
    """
    cmd = [pio_path, "run", "-d", str(project_dir)]
    if env_name:
        cmd.extend(["-e", env_name])
    if target:
        cmd.extend(["-t", target])
    if jobs:
        cmd.extend(["-j", str(jobs)])
    if verbose:
        cmd.append("-v")

    cmd_str = " ".join(cmd)
    print(f"🔨 构建命令: {cmd_str}")

    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, cmd_str, ["❌ PlatformIO 构建超时（600 秒）"]
    except FileNotFoundError:
        return False, cmd_str, [f"❌ 未找到 pio: {pio_path}"]

    elapsed = time.time() - start
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    evidence: list[str] = []

    if result.returncode != 0:
        last_lines = output.strip().split("\n")[-20:]
        evidence.append("构建失败输出（末尾）:")
        evidence.extend(last_lines)
        return False, cmd_str, evidence

    action_map = {None: "构建", "clean": "清理", "upload": "上传"}
    action = action_map.get(target, "操作")
    print(f"✅ {action}成功（耗时 {elapsed:.1f} 秒）")
    evidence.append(f"耗时: {elapsed:.1f} 秒")
    return True, cmd_str, evidence


def run_pio_device_list(pio_path: str) -> list[str]:
    """调用 pio device list 列出设备。"""
    try:
        result = subprocess.run(
            [pio_path, "device", "list"],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report(env: dict[str, Any]) -> None:
    print("\n📊 PlatformIO 环境探测结果：")
    pio = env["pio"]
    status = "✅" if pio["available"] else "❌"
    ver = f" ({pio['version']})" if pio.get("version") else ""
    path = f" @ {pio['path']}" if pio.get("path") else ""
    print(f"  {status} pio{ver}{path}")


def print_build_report(result: BuildResult) -> None:
    icon = {"success": "✅", "failure": "❌", "blocked": "⚠️"}.get(result.status, "❓")
    print(f"\n📊 构建结果: {icon} {result.summary}")

    if result.build_cmd:
        print(f"\n  构建命令:   {result.build_cmd}")
    if result.project_dir:
        print(f"  工程目录:   {result.project_dir}")
    if result.env_name:
        print(f"  环境:       {result.env_name}")
    if result.board:
        print(f"  板卡:       {result.board}")
    if result.platform:
        print(f"  平台:       {result.platform}")

    if result.artifacts:
        print(f"\n📦 找到 {len(result.artifacts)} 个固件产物：")
        for i, a in enumerate(result.artifacts):
            size_kb = a.size / 1024
            primary = " ⭐ 首选" if a == result.primary_artifact else ""
            print(f"  {i + 1}. [{a.kind.upper()}] {a.path} ({size_kb:.1f} KB){primary}")
    elif result.status == "success" and result.env_name:
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
        description="PlatformIO 命令行构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --list-envs --project-dir /repo/fw
  %(prog)s --project-dir /repo/fw --env esp32dev
  %(prog)s --project-dir /repo/fw --env nucleo_f429zi --upload
  %(prog)s --project-dir /repo/fw --clean
  %(prog)s --scan-artifacts /repo/fw/.pio/build/esp32dev
  %(prog)s --list-devices
        """,
    )
    parser.add_argument("--detect", action="store_true", help="探测 PlatformIO 环境")
    parser.add_argument("--project-dir", help="PlatformIO 工程目录")
    parser.add_argument("--env", help="构建环境名称")
    parser.add_argument("--list-envs", action="store_true", help="列出工程中的所有环境")
    parser.add_argument("--clean", action="store_true", help="清理构建产物")
    parser.add_argument("--upload", action="store_true", help="构建并上传固件")
    parser.add_argument("--list-devices", action="store_true", help="列出已连接的设备")
    parser.add_argument("--scan-artifacts", help="仅扫描指定目录中的产物")
    parser.add_argument("--save-config", action="store_true", help="探测成功后保存工具路径到配置")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-j", "--jobs", type=int, help="并行构建任务数")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # 环境探测
    if args.detect:
        env = detect_environment()
        print_detect_report(env)
        if args.save_config and env["pio"]["available"]:
            cfg_path = set_tool_path("pio", env["pio"]["path"])
            print(f"  💾 已保存到 {cfg_path}")
        return 0 if env["pio"]["available"] else 1

    # 列出设备
    if args.list_devices:
        pio_path = find_pio()
        if not pio_path:
            print("❌ 未找到 pio 命令，请先安装 PlatformIO。")
            return 1
        lines = run_pio_device_list(pio_path)
        if not lines:
            print("❌ 未检测到已连接的设备")
            return 1
        print("📋 已连接的设备：")
        for line in lines:
            print(f"  {line}")
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

    # 需要工程目录
    if not args.project_dir and not args.list_envs:
        print("❌ 请提供 --project-dir（PlatformIO 工程目录）。")
        return 1

    project_dir = Path(args.project_dir or ".").resolve()
    ini_path = project_dir / "platformio.ini"
    if not ini_path.exists():
        print(f"❌ 在 {project_dir} 中未找到 platformio.ini")
        return 1

    # 解析环境
    envs, default_envs = parse_platformio_ini(project_dir)
    if not envs:
        print(f"❌ 未能从 platformio.ini 中解析出环境")
        return 1

    # 列出环境
    if args.list_envs:
        print(f"📋 工程 {project_dir.name} 中的环境：")
        for i, e in enumerate(envs, 1):
            default_mark = " ⭐ 默认" if e.is_default else ""
            board_info = f" [{e.board}]" if e.board else ""
            plat_info = f" ({e.platform})" if e.platform else ""
            fw_info = f" framework={e.framework}" if e.framework else ""
            print(f"  {i}. {e.name}{board_info}{plat_info}{fw_info}{default_mark}")
        return 0

    # 检查 pio
    pio_path = find_pio()
    if not pio_path:
        print("❌ 未找到 pio 命令，请先安装 PlatformIO。")
        return 1

    # 选择环境
    selected: PIOEnvironment | None = None
    if args.env:
        for e in envs:
            if e.name == args.env:
                selected = e
                break
        if not selected:
            print(f"❌ 未找到环境 '{args.env}'，可用环境：")
            for e in envs:
                print(f"  - {e.name}")
            return 1
    else:
        # 优先使用默认环境
        for e in envs:
            if e.is_default:
                selected = e
                break
        if not selected:
            selected = envs[0]
        if len(envs) > 1:
            print(f"ℹ️ 未指定环境，使用: {selected.name}")

    env_name = selected.name
    print(f"📦 环境: {selected.name} [{selected.board}] ({selected.platform})")

    # 确定构建目标
    target: str | None = None
    if args.clean:
        target = "clean"
    elif args.upload:
        target = "upload"

    # 执行构建
    ok, cmd_str, evidence = run_pio_build(
        pio_path=pio_path,
        project_dir=project_dir,
        env_name=env_name,
        target=target,
        jobs=args.jobs,
        verbose=args.verbose,
    )

    if not ok:
        result = BuildResult(
            status="failure",
            summary="PlatformIO 构建失败",
            build_cmd=cmd_str,
            project_dir=str(project_dir),
            env_name=env_name,
            board=selected.board,
            platform=selected.platform,
            failure_category="project-config-error",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    if args.clean:
        result = BuildResult(
            status="success",
            summary=f"清理环境 {env_name} 成功",
            build_cmd=cmd_str,
            project_dir=str(project_dir),
            env_name=env_name,
            evidence=evidence,
        )
        print_build_report(result)
        return 0

    # 扫描产物
    build_dir = resolve_build_dir(project_dir, env_name)
    artifacts = scan_artifacts(build_dir)
    primary = artifacts[0] if artifacts else None

    if not artifacts:
        result = BuildResult(
            status="success",
            summary="构建成功但未找到固件产物",
            build_cmd=cmd_str,
            project_dir=str(project_dir),
            env_name=env_name,
            board=selected.board,
            platform=selected.platform,
            artifacts=[],
            failure_category="artifact-missing",
            evidence=evidence,
        )
        print_build_report(result)
        return 1

    result = BuildResult(
        status="success",
        summary=f"构建成功，找到 {len(artifacts)} 个产物",
        build_cmd=cmd_str,
        project_dir=str(project_dir),
        env_name=env_name,
        board=selected.board,
        platform=selected.platform,
        artifacts=artifacts,
        primary_artifact=primary,
        evidence=evidence,
    )
    print_build_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
