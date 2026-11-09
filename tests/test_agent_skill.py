"""The agent skill under skills/ follows the Agent Skills layout, and the old path points to it.

`npx skills add` and the agents that read skills find a skill by its directory and its YAML front
matter. The front matter must parse, `name` must equal the directory name, and `description` must say
when an agent should use the skill. integrations/claude-code/SKILL.md was the skill's old home. It
stays as a short pointer without front matter, so no tool discovers it as a second skill.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "viewspec" / "SKILL.md"
POINTER = ROOT / "integrations" / "claude-code" / "SKILL.md"
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MARKDOWN_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def _front_matter(path: Path) -> dict[str, object]:
    match = FRONT_MATTER_RE.match(path.read_text(encoding="utf-8"))
    assert match is not None, f"{path.relative_to(ROOT)} has no YAML front matter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), path
    return data


def test_each_skill_front_matter_parses_with_its_directory_name() -> None:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert SKILL in skills
    for path in skills:
        data = _front_matter(path)
        name = data.get("name")
        assert name == path.parent.name, (path, name)
        assert isinstance(name, str) and len(name) <= 64 and SKILL_NAME_RE.fullmatch(name), name
        description = data.get("description")
        assert isinstance(description, str) and 0 < len(description.strip()) <= 1024, path
        assert "Use when" in description, f"{path.relative_to(ROOT)} must say when an agent should use it"


def test_old_integration_path_is_a_short_pointer_to_the_skill() -> None:
    text = POINTER.read_text(encoding="utf-8")

    assert "skills/viewspec/SKILL.md" in text
    assert not text.startswith("---"), "the pointer must not carry front matter, or it reads as a second skill"
    assert len(text.splitlines()) <= 20
    targets = [(POINTER.parent / target).resolve() for target in MARKDOWN_LINK_RE.findall(text)]
    assert SKILL.resolve() in targets, targets
