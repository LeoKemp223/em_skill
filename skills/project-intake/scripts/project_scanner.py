#!/usr/bin/env python
"""通用嵌入式工程识别工具。

这个脚本为 `project-intake` skill 提供可重复调用的执行入口，支持：

- 扫描工作区根目录线索，识别构建系统和工具链
- 从 OpenOCD 配置和 IDE 启动文件中提取目标芯片与探针线索
- 在构建目录中搜索 ELF/HEX/BIN 产物
- 可选检测串口设备
- 输出标准化的 Project Profile
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
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


ARTIFACT_EXTENSIONS = {".elf": "elf", ".hex": "hex", ".bin": "bin", ".axf": "elf"}
ARTIFACT_PRIORITY = {"elf": 1, "hex": 2, "bin": 3}
BUILD_DIR_NAMES = ["build", "cmake-build-debug", "cmake-build-release", "out", "Build"]


@dataclass
class ProjectProfile:
    workspace_root: str = ""
    workspace_os: str = ""
    build_system: str | None = None
    toolchain: str | None = None
    target_mcu: str | None = None
    board: str | None = None
    probe: str | None = None
    artifact_path: str | None = None
    artifact_kind: str | None = None
    openocd_config: list[str] = field(default_factory=list)
    gdb_executable: str | None = None
    serial_port: str | None = None
    baud_rate: int | None = None
    notes: str | None = None


@dataclass
class Evidence:
    field: str
    value: str
    source: str


# ---------------------------------------------------------------------------
# 宿主识别
# ---------------------------------------------------------------------------

def detect_os() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# 构建系统识别
# ---------------------------------------------------------------------------

def detect_build_system(workspace: Path) -> tuple[str | None, list[Evidence]]:
    evidence: list[Evidence] = []

    if (workspace / "CMakeLists.txt").exists():
        evidence.append(Evidence("build_system", "cmake", "CMakeLists.txt"))
        if (workspace / "CMakePresets.json").exists():
            evidence.append(Evidence("build_system", "cmake", "CMakePresets.json"))
        return "cmake", evidence

    if (workspace / "platformio.ini").exists():
        evidence.append(Evidence("build_system", "platformio", "platformio.ini"))
        return "platformio", evidence

    # IAR: 搜索 .ewp / .eww 文件（仅顶层和一级子目录）
    for ewp in workspace.glob("*.ewp"):
        evidence.append(Evidence("build_system", "iar", str(ewp.relative_to(workspace))))
        return "iar", evidence
    for ewp in workspace.glob("*/*.ewp"):
        evidence.append(Evidence("build_system", "iar", str(ewp.relative_to(workspace))))
        return "iar", evidence
    for eww in workspace.glob("*.eww"):
        evidence.append(Evidence("build_system", "iar", str(eww.relative_to(workspace))))
        return "iar", evidence

    for name in ["Makefile", "makefile", "GNUmakefile"]:
        if (workspace / name).exists():
            evidence.append(Evidence("build_system", "make", name))
            return "make", evidence

    if (workspace / "sdkconfig").exists() or (workspace / "sdkconfig.defaults").exists():
        evidence.append(Evidence("build_system", "idf", "sdkconfig"))
        return "idf", evidence

    return None, evidence


# ---------------------------------------------------------------------------
# 工具链识别
# ---------------------------------------------------------------------------

def detect_toolchain(workspace: Path) -> tuple[str | None, list[Evidence]]:
    evidence: list[Evidence] = []

    # 搜索工具链文件
    for pattern in ["*.cmake", "cmake/*.cmake", "toolchain/*.cmake"]:
        for f in workspace.glob(pattern):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")[:4096]
            except OSError:
                continue
            if "arm-none-eabi" in content or "ARM" in content:
                evidence.append(Evidence("toolchain", "gnu-arm", str(f.relative_to(workspace))))
                return "gnu-arm", evidence
            if "armclang" in content or "ARMCC" in content:
                evidence.append(Evidence("toolchain", "armcc", str(f.relative_to(workspace))))
                return "armcc", evidence

    # CMakePresets.json 中的工具链
    presets_file = workspace / "CMakePresets.json"
    if presets_file.exists():
        try:
            data = json.loads(presets_file.read_text(encoding="utf-8"))
            for p in data.get("configurePresets", []):
                tc = p.get("toolchainFile") or p.get("cacheVariables", {}).get("CMAKE_TOOLCHAIN_FILE", "")
                if "arm-none-eabi" in str(tc).lower():
                    evidence.append(Evidence("toolchain", "gnu-arm", f"CMakePresets.json ({p.get('name', '')})"))
                    return "gnu-arm", evidence
        except Exception:
            pass

    return None, evidence


# ---------------------------------------------------------------------------
# 目标芯片与探针识别
# ---------------------------------------------------------------------------

MCU_PATTERNS = [
    (re.compile(r"stm32[a-z]\d", re.IGNORECASE), "stm32"),
    (re.compile(r"nrf5\d", re.IGNORECASE), "nrf5x"),
    (re.compile(r"esp32", re.IGNORECASE), "esp32"),
    (re.compile(r"gd32", re.IGNORECASE), "gd32"),
    (re.compile(r"at32", re.IGNORECASE), "at32"),
]

PROBE_PATTERNS = [
    (re.compile(r"st-?link", re.IGNORECASE), "stlink"),
    (re.compile(r"cmsis-?dap|dap-?link", re.IGNORECASE), "cmsis-dap"),
    (re.compile(r"j-?link", re.IGNORECASE), "jlink"),
]


def detect_target_and_probe(workspace: Path) -> tuple[str | None, str | None, str | None, list[Evidence]]:
    """返回 (mcu, board, probe, evidence)"""
    evidence: list[Evidence] = []
    mcu: str | None = None
    board: str | None = None
    probe: str | None = None

    # 扫描 OpenOCD 配置文件
    scan_files: list[Path] = []
    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > 3:
            continue
        for fname in files:
            if re.match(r"openocd.*\.cfg$", fname, re.IGNORECASE):
                scan_files.append(Path(root) / fname)

    # .vscode/launch.json
    launch_json = workspace / ".vscode" / "launch.json"
    if launch_json.exists():
        scan_files.append(launch_json)

    # *.ioc (STM32CubeMX)
    for f in workspace.glob("*.ioc"):
        scan_files.append(f)

    for fpath in scan_files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")[:8192]
        except OSError:
            continue
        rel = str(fpath.relative_to(workspace)) if fpath.is_relative_to(workspace) else str(fpath)

        if not mcu:
            for pattern, family in MCU_PATTERNS:
                m = pattern.search(content)
                if m:
                    mcu = m.group(0).lower()
                    evidence.append(Evidence("target_mcu", mcu, rel))
                    break

        if not probe:
            for pattern, probe_name in PROBE_PATTERNS:
                if pattern.search(content):
                    probe = probe_name
                    evidence.append(Evidence("probe", probe, rel))
                    break

    # 从 CMakeLists.txt 提取板卡线索
    cmakelists = workspace / "CMakeLists.txt"
    if cmakelists.exists() and not board:
        try:
            content = cmakelists.read_text(encoding="utf-8", errors="ignore")[:8192]
            board_match = re.search(r"(?:BOARD|board)\s*[=:\"]\s*([^\s\";\)]+)", content)
            if board_match:
                board = board_match.group(1)
                evidence.append(Evidence("board", board, "CMakeLists.txt"))
        except OSError:
            pass

    return mcu, board, probe, evidence


# ---------------------------------------------------------------------------
# 产物扫描
# ---------------------------------------------------------------------------

def scan_artifacts(workspace: Path) -> tuple[str | None, str | None, list[Evidence]]:
    evidence: list[Evidence] = []
    candidates: list[tuple[int, Path, str]] = []

    for dir_name in BUILD_DIR_NAMES:
        build_dir = workspace / dir_name
        if not build_dir.is_dir():
            continue
        for root, _dirs, files in os.walk(build_dir):
            for fname in files:
                ext = Path(fname).suffix.lower()
                kind = ARTIFACT_EXTENSIONS.get(ext)
                if not kind:
                    continue
                fpath = Path(root) / fname
                try:
                    size = fpath.stat().st_size
                except OSError:
                    continue
                if size < 256:
                    continue
                priority = ARTIFACT_PRIORITY.get(kind, 9)
                candidates.append((priority, fpath, kind))

    if not candidates:
        return None, None, evidence

    candidates.sort(key=lambda c: (c[0], -c[1].stat().st_size))
    best = candidates[0]
    rel = str(best[1].relative_to(workspace)) if best[1].is_relative_to(workspace) else str(best[1])
    evidence.append(Evidence("artifact_path", str(best[1]), rel))
    evidence.append(Evidence("artifact_kind", best[2], rel))

    if len(candidates) > 1:
        evidence.append(Evidence("notes", f"共找到 {len(candidates)} 个产物候选", "build dirs"))

    return str(best[1]), best[2], evidence


# ---------------------------------------------------------------------------
# 串口扫描
# ---------------------------------------------------------------------------

def scan_serial_ports() -> tuple[str | None, int | None, list[Evidence]]:
    evidence: list[Evidence] = []
    try:
        from serial.tools import list_ports
    except ImportError:
        evidence.append(Evidence("serial_port", "(pyserial 未安装)", "import"))
        return None, None, evidence

    ports = list(list_ports.comports())
    if not ports:
        return None, None, evidence

    for port in ports:
        evidence.append(Evidence("serial_port", f"{port.device}: {port.description}", "pyserial"))

    if len(ports) == 1:
        return ports[0].device, 115200, evidence

    # 多个端口，按优先级选择
    desc_upper = [(p, p.description.upper()) for p in ports]
    for p, d in desc_upper:
        if any(kw in d for kw in ["CH340", "CH341", "CP210"]):
            return p.device, 115200, evidence
    for p, d in desc_upper:
        if any(kw in d for kw in ["CMSIS-DAP", "STLINK", "ST-LINK", "J-LINK"]):
            return p.device, 115200, evidence

    evidence.append(Evidence("notes", f"检测到 {len(ports)} 个串口，无法自动选择", "ambiguous"))
    return None, None, evidence


# ---------------------------------------------------------------------------
# OpenOCD 配置收集
# ---------------------------------------------------------------------------

def collect_openocd_configs(workspace: Path) -> list[str]:
    configs: list[str] = []
    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > 3:
            continue
        for fname in files:
            if re.match(r"openocd.*\.cfg$", fname, re.IGNORECASE):
                configs.append(str(Path(root) / fname))
    return configs


# ---------------------------------------------------------------------------
# Profile 组装与输出
# ---------------------------------------------------------------------------

def build_profile(
    workspace: Path,
    scan_serial: bool,
    hints: dict[str, str | None],
    verbose: bool,
) -> tuple[ProjectProfile, list[Evidence]]:
    all_evidence: list[Evidence] = []

    profile = ProjectProfile(
        workspace_root=str(workspace.resolve()),
        workspace_os=detect_os(),
    )

    # 构建系统
    bs, ev = detect_build_system(workspace)
    profile.build_system = bs
    all_evidence.extend(ev)

    # 工具链
    tc, ev = detect_toolchain(workspace)
    profile.toolchain = tc
    all_evidence.extend(ev)

    # 目标芯片与探针
    mcu, board, probe, ev = detect_target_and_probe(workspace)
    profile.target_mcu = mcu
    profile.board = board
    profile.probe = probe
    all_evidence.extend(ev)

    # 产物
    artifact_path, artifact_kind, ev = scan_artifacts(workspace)
    profile.artifact_path = artifact_path
    profile.artifact_kind = artifact_kind
    all_evidence.extend(ev)

    # OpenOCD 配置
    profile.openocd_config = collect_openocd_configs(workspace)

    # 串口
    if scan_serial:
        serial_port, baud, ev = scan_serial_ports()
        profile.serial_port = serial_port
        profile.baud_rate = baud
        all_evidence.extend(ev)

    # 用户提示覆盖
    if hints.get("mcu"):
        profile.target_mcu = hints["mcu"]
    if hints.get("board"):
        profile.board = hints["board"]
    if hints.get("probe"):
        profile.probe = hints["probe"]
    if hints.get("toolchain"):
        profile.toolchain = hints["toolchain"]

    return profile, all_evidence


def suggest_next_skill(profile: ProjectProfile) -> str:
    if profile.build_system and not profile.artifact_path:
        skill_map = {
            "cmake": "build-cmake",
            "keil": "build-keil",
            "iar": "build-iar",
            "platformio": "build-platformio",
        }
        return skill_map.get(profile.build_system, "build-cmake")
    if profile.artifact_path and profile.probe:
        return "flash-openocd"
    if profile.serial_port:
        return "serial-monitor"
    if profile.build_system:
        skill_map = {
            "cmake": "build-cmake",
            "keil": "build-keil",
            "iar": "build-iar",
            "platformio": "build-platformio",
        }
        return skill_map.get(profile.build_system, "build-cmake")
    return "project-intake (需要更多信息)"


def print_profile_yaml(profile: ProjectProfile) -> None:
    print("\n📋 Project Profile:")
    print("---")
    for fld in [
        "workspace_root", "workspace_os", "build_system", "toolchain",
        "target_mcu", "board", "probe", "artifact_path", "artifact_kind",
        "gdb_executable", "serial_port", "baud_rate", "notes",
    ]:
        val = getattr(profile, fld)
        if val is not None:
            print(f"{fld}: {val}")
    if profile.openocd_config:
        print("openocd_config:")
        for cfg in profile.openocd_config:
            print(f"  - {cfg}")
    print("---")


def print_profile_json(profile: ProjectProfile) -> None:
    d: dict[str, Any] = {}
    for fld in [
        "workspace_root", "workspace_os", "build_system", "toolchain",
        "target_mcu", "board", "probe", "artifact_path", "artifact_kind",
        "gdb_executable", "serial_port", "baud_rate", "notes",
    ]:
        val = getattr(profile, fld)
        if val is not None:
            d[fld] = val
    if profile.openocd_config:
        d["openocd_config"] = profile.openocd_config
    print(json.dumps(d, ensure_ascii=False, indent=2))


def print_evidence(evidence: list[Evidence]) -> None:
    if not evidence:
        return
    print(f"\n🔍 探测证据（共 {len(evidence)} 条）:")
    for ev in evidence:
        print(f"  [{ev.field}] {ev.value} ← {ev.source}")


def print_report(profile: ProjectProfile, evidence: list[Evidence], verbose: bool, as_json: bool) -> None:
    if as_json:
        print_profile_json(profile)
    else:
        print_profile_yaml(profile)

    if verbose:
        print_evidence(evidence)

    next_skill = suggest_next_skill(profile)
    print(f"\n💡 推荐下一步: {next_skill}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="嵌入式工程识别工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --workspace /repo/fw
  %(prog)s --workspace /repo/fw --scan-serial --verbose
  %(prog)s --workspace /repo/fw --hint-mcu stm32f429zi --hint-probe stlink
  %(prog)s --workspace /repo/fw --json
        """,
    )
    parser.add_argument("--workspace", default=".", help="工作区根目录路径")
    parser.add_argument("--scan-serial", action="store_true", help="同时扫描串口设备")
    parser.add_argument("--hint-mcu", help="用户提示：目标 MCU")
    parser.add_argument("--hint-board", help="用户提示：开发板名称")
    parser.add_argument("--hint-probe", help="用户提示：调试探针")
    parser.add_argument("--hint-toolchain", help="用户提示：工具链")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细探测证据")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"❌ 工作区路径无效: {workspace}")
        return 1

    print(f"📂 扫描工作区: {workspace}")

    hints = {
        "mcu": args.hint_mcu,
        "board": args.hint_board,
        "probe": args.hint_probe,
        "toolchain": args.hint_toolchain,
    }

    profile, evidence = build_profile(workspace, args.scan_serial, hints, args.verbose)
    print_report(profile, evidence, args.verbose, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
