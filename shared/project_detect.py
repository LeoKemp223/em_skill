"""统一项目探测模块。

供新 skill 调用，自动识别构建系统、目标芯片、RTOS 和调试探针。
现有 skill 不做改动，仅新 skill 引用本模块。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def detect_build_system(workspace: Path) -> str | None:
    markers = [
        ("CMakeLists.txt", "cmake"),
        ("platformio.ini", "platformio"),
        ("sdkconfig", "idf"),
    ]
    for filename, system in markers:
        if (workspace / filename).exists():
            return system

    for f in workspace.iterdir():
        if f.is_file():
            ext = f.suffix.lower()
            if ext == ".uvprojx":
                return "keil"
            if ext in (".eww", ".ewp"):
                return "iar"

    # Makefile 检测 — 最低优先级，仅在其他系统均未匹配时使用
    for mf_name in ("Makefile", "makefile", "GNUmakefile"):
        if (workspace / mf_name).is_file():
            return "makefile"

    return None


def detect_target_mcu(workspace: Path, build_system: str | None) -> str | None:
    if build_system == "keil":
        for f in workspace.glob("*.uvprojx"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"<Device>(.*?)</Device>", text)
                if m:
                    return m.group(1)
            except OSError:
                pass

    if build_system == "iar":
        for f in workspace.glob("*.ewp"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"<OGChipSelectEditMenu>(.*?)</OGChipSelectEditMenu>", text)
                if m:
                    return m.group(1).split("\t")[0] if "\t" in m.group(1) else m.group(1)
            except OSError:
                pass

    if build_system == "platformio":
        ini = workspace / "platformio.ini"
        if ini.is_file():
            try:
                text = ini.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"board\s*=\s*(\S+)", text)
                if m:
                    return m.group(1)
            except OSError:
                pass

    if build_system == "idf":
        sdkconfig = workspace / "sdkconfig"
        if sdkconfig.is_file():
            try:
                text = sdkconfig.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r'CONFIG_IDF_TARGET="(\S+)"', text)
                if m:
                    return m.group(1)
            except OSError:
                pass

    if build_system == "makefile":
        for mf_name in ("Makefile", "makefile", "GNUmakefile"):
            mf = workspace / mf_name
            if mf.is_file():
                try:
                    text = mf.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"(?:^|\n)MCU\s*[:?]?=\s*(\S+)", text)
                    if m:
                        return m.group(1).strip()
                    m = re.search(r"-mcpu=([a-z0-9]+)", text)
                    if m:
                        return m.group(1)
                except OSError:
                    pass
                break

    return None


def detect_rtos(workspace: Path) -> str | None:
    rtos_headers = {
        "FreeRTOS.h": "freertos",
        "rtthread.h": "rt-thread",
        "zephyr/kernel.h": "zephyr",
    }
    rtos_symbols = {
        "vTaskStartScheduler": "freertos",
        "rt_thread_init": "rt-thread",
        "k_thread_create": "zephyr",
    }

    for root, _dirs, files in os.walk(workspace):
        depth = str(root).replace(str(workspace), "").count(os.sep)
        if depth > 4:
            continue
        for fname in files:
            if not fname.endswith((".c", ".h", ".cpp")):
                continue
            fpath = Path(root) / fname
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for header, rtos in rtos_headers.items():
                if f'#include "{header}"' in text or f"#include <{header}>" in text:
                    return rtos
            for symbol, rtos in rtos_symbols.items():
                if symbol in text:
                    return rtos
    return None


def detect_probes() -> list[str]:
    probes: list[str] = []
    if shutil.which("JLinkExe") or shutil.which("JLink.exe"):
        probes.append("jlink")
    if shutil.which("openocd"):
        probes.append("openocd")
    if shutil.which("pyocd"):
        probes.append("pyocd")
    return probes


def _find_artifacts(workspace: Path) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    build_dirs = ["build", "Build", "output", "Output", "Debug", "Release", ".pio/build"]
    ext_map = {".elf": "elf", ".hex": "hex", ".bin": "bin", ".axf": "elf"}

    for bd_name in build_dirs:
        bd = workspace / bd_name
        if not bd.is_dir():
            continue
        for root, _dirs, files in os.walk(bd):
            for fname in files:
                ext = Path(fname).suffix.lower()
                kind = ext_map.get(ext)
                if kind:
                    artifacts.append({
                        "path": str(Path(root) / fname),
                        "kind": kind,
                    })
    return artifacts


def detect_project(workspace: Path) -> dict[str, Any]:
    build_system = detect_build_system(workspace)
    target_mcu = detect_target_mcu(workspace, build_system)
    rtos = detect_rtos(workspace)
    probes = detect_probes()
    artifacts = _find_artifacts(workspace)

    profile: dict[str, Any] = {
        "workspace_root": str(workspace),
        "workspace_os": _detect_os(),
    }
    if build_system:
        profile["build_system"] = build_system
    if target_mcu:
        profile["target_mcu"] = target_mcu
    if rtos:
        profile["rtos"] = rtos
    if probes:
        profile["probes"] = probes
    if artifacts:
        elf_arts = [a for a in artifacts if a["kind"] == "elf"]
        best = elf_arts[0] if elf_arts else artifacts[0]
        profile["artifact_path"] = best["path"]
        profile["artifact_kind"] = best["kind"]
        profile["all_artifacts"] = artifacts

    return profile


def _detect_os() -> str:
    import platform as _platform
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"
