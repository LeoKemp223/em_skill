#!/usr/bin/env python3
"""embed-ai-tool 安装脚本 — 零依赖，仅标准库。

用法：
    python3 scripts/install.py /path/to/project              # 安装全部 skill
    python3 scripts/install.py /path/to/project --skills build-cmake flash-openocd
    python3 scripts/install.py /path/to/project --force       # 强制覆盖
    python3 scripts/install.py /path/to/project --detect      # 安装后探测工具路径
    python3 scripts/install.py /path/to/project --uninstall   # 卸载
    python3 scripts/install.py /path/to/project --status      # 查看安装状态
    python3 scripts/install.py --list                         # 列出可用 skill
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_SRC = REPO_ROOT / "skills"
SHARED_SRC = REPO_ROOT / "shared"
META_FILENAME = ".em_skill_meta.json"

SKIP_PATTERNS = {"__pycache__", ".pyc", ".pyo", ".DS_Store", "Thumbs.db"}

DETECT_TOOLS = [
    "cmake",
    "ninja",
    "make",
    "openocd",
    "arm-none-eabi-gcc",
    "arm-none-eabi-gdb",
    "gdb-multiarch",
    "platformio",
    "pio",
    "idf.py",
]


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_PATTERNS or part.endswith((".pyc", ".pyo")):
            return True
    return False


def _git_short_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return "unknown"


def _copy_tree(src: Path, dst: Path, force: bool = False) -> tuple[int, int]:
    """递归拷贝目录，返回 (copied, skipped) 计数。"""
    copied = 0
    skipped = 0
    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        if _should_skip(rel):
            continue
        target = dst / rel
        if target.exists() and not force:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return copied, skipped


def _available_skills() -> list[str]:
    if not SKILLS_SRC.is_dir():
        return []
    return sorted(
        d.name
        for d in SKILLS_SRC.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


def _read_skill_description(skill_name: str) -> str:
    skill_md = SKILLS_SRC / skill_name / "SKILL.md"
    if not skill_md.is_file():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("description:"):
            desc = line[len("description:"):].strip()
            return desc.strip("\"'")
    return ""


def _skills_dir(project: Path) -> Path:
    return project / ".claude" / "skills"


def _meta_path(project: Path) -> Path:
    return _skills_dir(project) / META_FILENAME


def _load_meta(project: Path) -> dict:
    mp = _meta_path(project)
    if mp.is_file():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_meta(project: Path, meta: dict) -> None:
    mp = _meta_path(project)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Commands ─────────────────────────────────────────────────────────


def cmd_list() -> None:
    skills = _available_skills()
    if not skills:
        print("未找到可用 skill。")
        return
    print(f"可用 skill（共 {len(skills)} 个）：\n")
    max_name = max(len(s) for s in skills)
    for s in skills:
        desc = _read_skill_description(s)
        print(f"  {s:<{max_name}}  {desc}")


def cmd_install(project: Path, skill_names: list[str] | None, force: bool) -> None:
    available = _available_skills()
    if not available:
        print("错误：未在仓库中找到任何 skill。", file=sys.stderr)
        sys.exit(1)

    if skill_names:
        invalid = [s for s in skill_names if s not in available]
        if invalid:
            print(f"错误：以下 skill 不存在：{', '.join(invalid)}", file=sys.stderr)
            print(f"可用 skill：{', '.join(available)}", file=sys.stderr)
            sys.exit(1)
        to_install = skill_names
    else:
        to_install = available

    dest = _skills_dir(project)
    dest.mkdir(parents=True, exist_ok=True)

    total_copied = 0
    total_skipped = 0

    # 拷贝 skill 目录
    for skill in to_install:
        src = SKILLS_SRC / skill
        dst = dest / skill
        c, s = _copy_tree(src, dst, force)
        total_copied += c
        total_skipped += s
        status = "✓" if c > 0 else ("跳过" if s > 0 else "空")
        print(f"  {status} {skill} ({c} 文件)")

    # 拷贝 shared 目录
    if SHARED_SRC.is_dir():
        c, s = _copy_tree(SHARED_SRC, dest / "shared", force)
        total_copied += c
        total_skipped += s
        print(f"  {'✓' if c > 0 else '跳过'} shared ({c} 文件)")

    # 写入 meta
    meta = _load_meta(project)
    existing_skills = set(meta.get("skills", []))
    existing_skills.update(to_install)
    meta.update(
        {
            "source": "embed-ai-tool",
            "version": _git_short_hash(),
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "skills": sorted(existing_skills),
        }
    )
    _save_meta(project, meta)

    print(f"\n安装完成：{total_copied} 文件已拷贝，{total_skipped} 文件已跳过。")
    print(f"目标目录：{dest}")
    if total_skipped > 0 and not force:
        print("提示：使用 --force 可覆盖已有文件。")


def cmd_uninstall(project: Path) -> None:
    dest = _skills_dir(project)
    meta = _load_meta(project)

    if not meta:
        # 没有 meta 文件，尝试列出疑似目录
        if dest.is_dir():
            dirs = [d.name for d in dest.iterdir() if d.is_dir()]
            if dirs:
                print("未找到安装记录（.em_skill_meta.json），但发现以下目录：")
                for d in sorted(dirs):
                    print(f"  - {d}")
                print("请手动确认并删除。")
                return
        print("未找到安装记录，也没有发现已安装的 skill。")
        return

    skills = meta.get("skills", [])
    removed = 0

    for skill in skills:
        skill_dir = dest / skill
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            print(f"  ✓ 已删除 {skill}")
            removed += 1

    # 删除 shared
    shared_dir = dest / "shared"
    if shared_dir.is_dir():
        shutil.rmtree(shared_dir)
        print("  ✓ 已删除 shared")

    # 删除 meta 文件
    mp = _meta_path(project)
    if mp.is_file():
        mp.unlink()

    # 如果 skills 目录为空，也删除
    if dest.is_dir() and not any(dest.iterdir()):
        dest.rmdir()

    print(f"\n卸载完成：已删除 {removed} 个 skill。")


def cmd_status(project: Path) -> None:
    meta = _load_meta(project)
    if not meta:
        print("未找到安装记录。该项目可能尚未安装 embed-ai-tool skill。")
        return

    print("embed-ai-tool 安装状态：\n")
    print(f"  版本：     {meta.get('version', '未知')}")
    print(f"  安装时间： {meta.get('installed_at', '未知')}")
    print(f"  来源：     {meta.get('source', '未知')}")

    skills = meta.get("skills", [])
    dest = _skills_dir(project)
    print(f"\n  已安装 skill（{len(skills)} 个）：")
    for s in skills:
        exists = (dest / s).is_dir()
        marker = "✓" if exists else "✗ (目录缺失)"
        print(f"    {marker} {s}")

    # 显示工具路径配置
    config_path = project / ".em_skill.json"
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            tools = cfg.get("tools", {})
            if tools:
                print(f"\n  工具路径配置（{len(tools)} 个）：")
                for name, path in sorted(tools.items()):
                    print(f"    {name}: {path}")
        except (json.JSONDecodeError, OSError):
            pass


def cmd_detect(project: Path) -> None:
    print("探测工具路径...\n")

    # 复用 shared/tool_config.py 的逻辑
    sys.path.insert(0, str(SHARED_SRC))
    try:
        from tool_config import set_tool_path
    except ImportError:
        # 回退：直接写 .em_skill.json
        set_tool_path = None

    found = {}
    for tool in DETECT_TOOLS:
        path = shutil.which(tool)
        if path:
            found[tool] = path
            print(f"  ✓ {tool}: {path}")
        else:
            print(f"  ✗ {tool}: 未找到")

    if not found:
        print("\n未找到任何工具，请确认工具已安装并在 PATH 中。")
        return

    # 写入配置
    if set_tool_path:
        for tool, path in found.items():
            set_tool_path(tool, path, workspace=project)
    else:
        config_path = project / ".em_skill.json"
        cfg = {}
        if config_path.is_file():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        tools = cfg.setdefault("tools", {})
        tools.update(found)
        config_path.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(f"\n已将 {len(found)} 个工具路径写入 {project / '.em_skill.json'}")


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="embed-ai-tool 安装脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "project",
        nargs="?",
        help="目标工程路径",
    )
    parser.add_argument(
        "--skills",
        nargs="+",
        metavar="SKILL",
        help="只安装指定 skill（默认安装全部）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖已有文件",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="卸载已安装的 skill",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_skills",
        help="列出仓库中所有可用 skill",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="显示当前安装状态",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="安装后自动探测工具路径",
    )

    args = parser.parse_args()

    # --list 不需要 project 参数
    if args.list_skills:
        cmd_list()
        return

    if not args.project:
        parser.error("请指定目标工程路径（或使用 --list 查看可用 skill）。")

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"错误：目录不存在：{project}", file=sys.stderr)
        sys.exit(1)

    if args.uninstall:
        cmd_uninstall(project)
        return

    if args.status:
        cmd_status(project)
        return

    # 默认动作：安装
    cmd_install(project, args.skills, args.force)

    if args.detect:
        print()
        cmd_detect(project)


if __name__ == "__main__":
    main()
