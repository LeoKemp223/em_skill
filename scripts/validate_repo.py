#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = [
    ".gitignore",
    "README.md",
    "CONTRIBUTING.md",
    "shared/contracts.md",
    "shared/failure-taxonomy.md",
    "shared/platform-compatibility.md",
    "shared/references/tool-detection.md",
    "shared/references/acceptance-scenarios.md",
    "templates/skill-template/SKILL.md",
    "templates/skill-template/CHECKLIST.md",
    "templates/skill-template/SCENARIOS.md",
]

REQUIRED_SKILLS = [
    "project-intake",
    "build-cmake",
    "flash-openocd",
    "serial-monitor",
    "debug-gdb-openocd",
]

REQUIRED_SECTIONS = [
    "## 适用场景",
    "## 必要输入",
    "## 自动探测",
    "## 执行步骤",
    "## 失败分流",
    "## 平台说明",
    "## 输出约定",
    "## 交接关系",
]


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def validate_required_files(root: Path, failures: list[str]) -> None:
    for relative_path in REQUIRED_FILES:
        if not (root / relative_path).is_file():
            fail(f"missing file: {relative_path}", failures)


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def validate_skill(skill_dir: Path, failures: list[str]) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        fail(f"missing file: {skill_md.relative_to(skill_dir.parent.parent)}", failures)
        return

    text = skill_md.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    expected_name = skill_dir.name

    if frontmatter.get("name") != expected_name:
        fail(
            f"{skill_md.relative_to(skill_dir.parent.parent)} frontmatter name should be '{expected_name}'",
            failures,
        )

    if not frontmatter.get("description"):
        fail(f"{skill_md.relative_to(skill_dir.parent.parent)} missing frontmatter description", failures)

    for heading in REQUIRED_SECTIONS:
        if heading not in text:
            fail(f"{skill_md.relative_to(skill_dir.parent.parent)} missing section: {heading}", failures)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures: list[str] = []

    validate_required_files(root, failures)

    for skill_name in REQUIRED_SKILLS:
        validate_skill(root / "skills" / skill_name, failures)

    if failures:
        print("Repository validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Repository validation passed.")
    print(f"Validated {len(REQUIRED_SKILLS)} skills and {len(REQUIRED_FILES)} shared files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
