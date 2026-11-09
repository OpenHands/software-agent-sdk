"""Expose skills to any ACP agent as files plus a catalog that points at them."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from html import escape
from pathlib import Path

from openhands.sdk.skills import Skill


def _description(skill: Skill) -> str:
    if skill.description:
        return skill.description
    return next(
        (line.strip() for line in skill.content.splitlines() if line.strip()),
        skill.name,
    )


def _directory_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "skill"
    candidate, suffix = base, 2
    while candidate.lower() in taken:
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    taken.add(candidate.lower())
    return candidate


def write_skill_files(root: Path, skills: Sequence[Skill]) -> dict[str, Path]:
    """Write each skill to ``root/<dir>/SKILL.md``; return its path by skill name."""
    locations: dict[str, Path] = {}
    taken: set[str] = set()
    for skill in skills:
        skill_dir = root / _directory_name(skill.name, taken)
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        name = json.dumps(skill.name, ensure_ascii=False)
        description = json.dumps(_description(skill)[:1000], ensure_ascii=False)
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{skill.content}\n",
            encoding="utf-8",
        )
        locations[skill.name] = path
    return locations


def render_skill_catalog(skills: Sequence[Skill], locations: dict[str, Path]) -> str:
    entries = [
        "  <skill>\n"
        f"    <name>{escape(skill.name)}</name>\n"
        f"    <description>{escape(_description(skill)[:1024])}</description>\n"
        f"    <location>{escape(str(locations[skill.name]))}</location>\n"
        "  </skill>"
        for skill in skills
        if skill.name in locations
    ]
    return (
        "<SKILLS>\n"
        "The following skills are available. When one is relevant to the task, "
        "read its SKILL.md at the given location with your file-reading tool "
        "and follow its instructions.\n\n"
        "<available_skills>\n" + "\n".join(entries) + "\n</available_skills>\n"
        "</SKILLS>"
    )
