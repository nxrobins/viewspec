"""integrations/claude-code/SKILL.md is a hand-maintained copy of the managed agent instructions.

`viewspec init-agent --target claude-code` writes CLAUDE.md from `viewspec.native_agents`; the
skill file is a curated parallel copy that is not generated from that source (audit finding D-6).
This test keeps the two from drifting: every fenced ``viewspec ...`` command in SKILL.md must also
appear verbatim in the managed instruction block, except for the commands listed below with the
reason they are intentionally skill-only.
"""

from __future__ import annotations

import re
from pathlib import Path

from viewspec.native_agents import agent_instruction_block

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "integrations/claude-code/SKILL.md"
FENCED_BASH_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)

# Fenced SKILL.md commands that are deliberately absent from the managed block, with the reason.
SKILL_ONLY_COMMANDS: dict[str, str] = {
    "viewspec doctor --freerange": (
        "Bun readiness probe for the opt-in Freerange proof; the managed block documents only the default proof path."
    ),
    "viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --freerange --json": (
        "opt-in Freerange numeric-kernel proof variant; the managed block shows the default prove-app command."
    ),
    "viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --pretext --json": (
        "opt-in Pretext native-DOM text proof variant; the managed block shows the default prove-app command."
    ),
    "viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --freerange --pretext --json": (
        "composed opt-in Freerange plus Pretext proof variant."
    ),
    "viewspec compile viewspec.intent.json --design DESIGN.md --target react-tailwind-tsx --out react-tailwind-output/": (
        "the managed block names `--target react-tailwind-tsx` inline instead of as a fenced example."
    ),
    "viewspec check react-tailwind-output/": "companion check for the react-tailwind-tsx compile example above.",
    "viewspec diff report-v1.html report-v2.html --json": (
        "same command as the managed block's `viewspec diff old.html new.html --json` with different example filenames."
    ),
}


def _fenced_viewspec_commands(text: str) -> list[str]:
    commands: list[str] = []
    for block in FENCED_BASH_RE.finditer(text):
        for line in block.group(1).splitlines():
            line = line.strip()
            if line.startswith("viewspec "):
                commands.append(line)
    return commands


def test_skill_declares_it_is_maintained_by_hand() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "Maintained by hand" in text
    assert "viewspec init-agent --target claude-code" in text
    assert "src/viewspec/native_agents.py" in text


def test_every_fenced_skill_command_is_in_managed_instructions_or_documented() -> None:
    skill_commands = _fenced_viewspec_commands(SKILL_PATH.read_text(encoding="utf-8"))
    assert skill_commands, "SKILL.md lost its fenced viewspec commands"
    managed = agent_instruction_block("claude-code")
    missing = [command for command in skill_commands if command not in managed and command not in SKILL_ONLY_COMMANDS]
    assert not missing, (
        "SKILL.md fenced commands that the managed claude-code instructions do not contain. Add the "
        "command to viewspec.native_agents.agent_instruction_block, remove it from SKILL.md, or document "
        f"why it is skill-only in SKILL_ONLY_COMMANDS: {missing}"
    )


def test_skill_only_allowlist_is_still_needed() -> None:
    skill_commands = set(_fenced_viewspec_commands(SKILL_PATH.read_text(encoding="utf-8")))
    managed = agent_instruction_block("claude-code")
    for command, reason in SKILL_ONLY_COMMANDS.items():
        assert reason
        assert command in skill_commands, f"{command!r} is no longer in SKILL.md; drop its allowlist entry"
        assert command not in managed, f"{command!r} is now in the managed block; drop its allowlist entry"
