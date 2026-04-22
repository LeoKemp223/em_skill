"""ESP-IDF 环境自动探测与激活。

统一提供 IDF 环境信息，替代各脚本中重复的 _find_idf_py()。
支持 v5.x (export.sh) 和 v6.0+ (EIM activate_idf_v*.sh) 两种安装方式。

用法::

    from idf_env import get_idf_env

    env = get_idf_env()
    if env is None:
        sys.exit("ESP-IDF 环境不可用")
    subprocess.run(env.idf_py_cmd + ["build"], env=env.env, cwd=project_dir)
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 同目录下的 tool_config
from tool_config import get_tool_path


@dataclass
class IdfEnv:
    """已激活的 ESP-IDF 环境快照。"""

    idf_py_cmd: list[str]  # e.g. ["python", "/path/to/idf.py"]
    env: dict[str, str]  # 完整环境变量，可直接传给 subprocess.run(env=)
    version: str | None  # e.g. "6.0"
    source: str  # "already-active" | "activate-script" | "export-sh" | "path"


# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------
_cached_env: IdfEnv | None = None
_cached_resolved: bool = False


def get_idf_env() -> IdfEnv | None:
    """探测并返回可用的 IDF 环境，同一进程内缓存结果。"""
    global _cached_env, _cached_resolved
    if _cached_resolved:
        return _cached_env
    _cached_env = _resolve_idf_env()
    _cached_resolved = True
    return _cached_env


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

def _resolve_idf_env() -> IdfEnv | None:
    """按优先级依次尝试各种方式获取 IDF 环境。"""

    # 1. 已激活：IDF_PATH 已设置且 idf.py 可达
    result = _try_already_active()
    if result:
        return result

    # 2. 通过激活脚本获取环境
    result = _try_activate_script()
    if result:
        return result

    # 3. idf.py 在 PATH 中（旧版安装或用户手动配置）
    result = _try_path_lookup()
    if result:
        return result

    return None


def _try_already_active() -> IdfEnv | None:
    """检查当前进程环境是否已激活 IDF。"""
    idf_path = os.environ.get("IDF_PATH")
    if not idf_path:
        return None

    idf_py = Path(idf_path) / "tools" / "idf.py"
    if not idf_py.exists():
        return None

    cmd = [sys.executable, str(idf_py)]
    env = dict(os.environ)
    version = _probe_version(cmd, env)
    return IdfEnv(idf_py_cmd=cmd, env=env, version=version, source="already-active")


def _try_activate_script() -> IdfEnv | None:
    """查找并 source 激活脚本，捕获激活后的环境变量。"""

    # 优先使用用户配置的路径
    configured = get_tool_path("idf-activate")
    if configured:
        script = Path(configured).expanduser()
        if script.exists():
            result = _source_and_capture(script)
            if result:
                return result

    is_win = platform.system() == "Windows"
    home = Path.home()

    # v6.0+ EIM 安装：~/.espressif/tools/activate_idf_v*.sh
    eim_dir = home / ".espressif" / "tools"
    if eim_dir.is_dir():
        if is_win:
            pattern = "activate_idf_v*.ps1"
        else:
            pattern = "activate_idf_v*.sh"
        scripts = sorted(eim_dir.glob(pattern), reverse=True)
        for script in scripts:
            result = _source_and_capture(script)
            if result:
                result.source = "activate-script"
                return result

    # v5.x：常见路径下的 export.sh
    export_candidates: list[Path] = []
    idf_path_env = os.environ.get("IDF_PATH")
    if idf_path_env:
        export_candidates.append(Path(idf_path_env) / ("export.ps1" if is_win else "export.sh"))

    for candidate_dir in [home / "esp" / "esp-idf", home / "esp-idf", Path("/opt/esp-idf")]:
        export_candidates.append(candidate_dir / ("export.ps1" if is_win else "export.sh"))

    for script in export_candidates:
        if script.exists():
            result = _source_and_capture(script)
            if result:
                result.source = "export-sh"
                return result

    return None


def _try_path_lookup() -> IdfEnv | None:
    """尝试从 PATH 或 tool_config 配置中找到 idf.py。"""
    env = dict(os.environ)

    # tool_config 配置
    configured = get_tool_path("idf-py")
    if configured and shutil.which(configured):
        cmd = configured.split()
        version = _probe_version(cmd, env)
        return IdfEnv(idf_py_cmd=cmd, env=env, version=version, source="path")

    # PATH 中直接可用
    if shutil.which("idf.py"):
        cmd = ["idf.py"]
        version = _probe_version(cmd, env)
        return IdfEnv(idf_py_cmd=cmd, env=env, version=version, source="path")

    return None


def _source_and_capture(script: Path) -> IdfEnv | None:
    """Source 一个 shell 脚本并捕获激活后的完整环境变量。"""
    system = platform.system()

    if system == "Windows":
        return _source_windows(script)
    else:
        return _source_posix(script)


def _source_posix(script: Path) -> IdfEnv | None:
    """在 bash 子进程中 source 脚本，用 env -0 捕获环境。"""
    try:
        result = subprocess.run(
            ["bash", "-c", f'source "{script}" > /dev/null 2>&1 && env -0'],
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    env = _parse_env0(result.stdout)
    if not env:
        return None

    return _build_env_from_captured(env)


def _source_windows(script: Path) -> IdfEnv | None:
    """在 PowerShell / cmd 子进程中执行激活脚本并捕获环境。"""
    suffix = script.suffix.lower()
    try:
        if suffix == ".ps1":
            cmd_line = (
                f'. "{script}" | Out-Null; '
                'Get-ChildItem Env: | ForEach-Object { "$($_.Name)=$($_.Value)" }'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd_line],
                capture_output=True, text=True, timeout=30,
            )
        elif suffix == ".bat":
            cmd_line = f'call "{script}" > nul 2>&1 && set'
            result = subprocess.run(
                ["cmd", "/c", cmd_line],
                capture_output=True, text=True, timeout=30,
            )
        else:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    env = _parse_env_text(result.stdout)
    if not env:
        return None

    return _build_env_from_captured(env)


def _build_env_from_captured(env: dict[str, str]) -> IdfEnv | None:
    """从捕获的环境变量中构造 IdfEnv。"""
    idf_path = env.get("IDF_PATH")
    if not idf_path:
        return None

    idf_py = Path(idf_path) / "tools" / "idf.py"
    if not idf_py.exists():
        return None

    # 使用捕获环境中的 python，回退到当前解释器
    python = env.get("IDF_PYTHON_ENV_PATH")
    if python:
        python_bin = Path(python) / "bin" / "python"
        if not python_bin.exists():
            python_bin = Path(python) / "Scripts" / "python.exe"
        if python_bin.exists():
            cmd = [str(python_bin), str(idf_py)]
        else:
            cmd = [sys.executable, str(idf_py)]
    else:
        cmd = [sys.executable, str(idf_py)]

    version = _probe_version(cmd, env)
    return IdfEnv(idf_py_cmd=cmd, env=env, version=version, source="activate-script")


def _probe_version(cmd: list[str], env: dict[str, str]) -> str | None:
    """运行 idf.py --version 获取版本号。"""
    try:
        result = subprocess.run(
            cmd + ["--version"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            # 提取类似 "v5.3" 或 "ESP-IDF v5.3-dev-..."
            m = re.search(r"v?(\d+\.\d+)", text)
            return m.group(1) if m else text
    except Exception:
        pass
    return env.get("ESP_IDF_VERSION")


def _parse_env0(raw: bytes) -> dict[str, str]:
    """解析 env -0 的 NUL 分隔输出。"""
    env: dict[str, str] = {}
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        try:
            decoded = entry.decode("utf-8", errors="replace")
        except Exception:
            continue
        eq = decoded.find("=")
        if eq > 0:
            env[decoded[:eq]] = decoded[eq + 1 :]
    return env


def _parse_env_text(text: str) -> dict[str, str]:
    """解析 KEY=VALUE 格式的文本环境变量输出（Windows set / PowerShell）。"""
    env: dict[str, str] = {}
    for line in text.splitlines():
        eq = line.find("=")
        if eq > 0:
            env[line[:eq]] = line[eq + 1 :]
    return env
