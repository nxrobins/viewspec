"""Managed instruction files for native agent adoption."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.local_tools import atomic_write


BEGIN_MARKER = "<!-- BEGIN VIEWSPEC AGENT INSTRUCTIONS v1 -->"
END_MARKER = "<!-- END VIEWSPEC AGENT INSTRUCTIONS v1 -->"
TARGET_PATHS = {
    "codex": Path("AGENTS.md"),
    "claude-code": Path("CLAUDE.md"),
    "cursor": Path(".cursor/rules/viewspec.mdc"),
    "copilot": Path(".github/copilot-instructions.md"),
}
VALID_TARGETS = (*TARGET_PATHS.keys(), "all")


class NativeAgentError(ValueError):
    """Raised when managed agent instructions cannot be safely updated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentInstructionChange:
    target: str
    path: Path
    action: str
    content: str

    def to_json(self, root: Path) -> dict[str, str]:
        return {
            "target": self.target,
            "path": _display_path(self.path, root),
            "action": self.action,
        }


def init_agent_instructions(root: str | Path, target: str, *, dry_run: bool = False) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    changes = plan_agent_instruction_changes(resolved_root, target)
    if not dry_run:
        for change in changes:
            if change.action == "unchanged":
                continue
            atomic_write(change.path, change.content)
    return {
        "ok": True,
        "target": target,
        "root": str(resolved_root),
        "changes": [change.to_json(resolved_root) for change in changes if change.action != "unchanged"],
    }


def plan_agent_instruction_changes(root: Path, target: str) -> list[AgentInstructionChange]:
    targets = _expand_targets(target)
    changes: list[AgentInstructionChange] = []
    for name in targets:
        path = root / TARGET_PATHS[name]
        block = agent_instruction_block(name)
        if not path.exists():
            changes.append(AgentInstructionChange(name, path, "create", block))
            continue
        current = _read_text_exact(path)
        next_text, action = _merge_instruction_block(current, block, path)
        changes.append(AgentInstructionChange(name, path, action, next_text))
    return changes


def agent_instruction_block(target: str) -> str:
    label = {
        "codex": "Codex",
        "claude-code": "Claude Code",
        "cursor": "Cursor",
        "copilot": "GitHub Copilot",
    }[target]
    return (
        f"{BEGIN_MARKER}\n"
        f"# ViewSpec Agent HTML Governance ({label})\n\n"
        "When you create or edit human-facing HTML, treat the HTML file as source and run ViewSpec locally before "
        "showing the artifact to the user.\n\n"
        "Required workflow:\n\n"
        "```bash\n"
        "viewspec compile input.html --design DESIGN.md --out dist/\n"
        "viewspec check dist/\n"
        "```\n\n"
        "When comparing revisions, run:\n\n"
        "```bash\n"
        "viewspec diff old.html new.html --json\n"
        "```\n\n"
        "Rules:\n\n"
        "- Source HTML is editable source; compiled `dist/index.html` is a generated artifact.\n"
        "- Never recursively compile generated `dist/index.html`.\n"
        "- Do not upload, share, call hosted APIs, or use remote services unless the user explicitly asks.\n"
        "- Use `DESIGN.md` only through ViewSpec. Do not inject arbitrary CSS, HTML, or script from it.\n"
        "- If `viewspec check` fails, fix the source HTML or `DESIGN.md`, then re-run compile and check.\n"
        f"{END_MARKER}\n"
    )


def _merge_instruction_block(current: str, block: str, path: Path) -> tuple[str, str]:
    begin_count = current.count(BEGIN_MARKER)
    end_count = current.count(END_MARKER)
    if begin_count != end_count or begin_count > 1:
        raise NativeAgentError(
            "MARKER_CONFLICT",
            f"{path} has malformed or duplicated ViewSpec instruction markers; no files were changed.",
        )
    if begin_count == 0:
        separator = "" if not current else "\n" if current.endswith("\n") else "\n\n"
        return f"{current}{separator}{block}", "append"
    begin = current.find(BEGIN_MARKER)
    end = current.find(END_MARKER)
    if end < begin:
        raise NativeAgentError(
            "MARKER_CONFLICT",
            f"{path} has reversed ViewSpec instruction markers; no files were changed.",
        )
    end += len(END_MARKER)
    if current.startswith("\r\n", end):
        end += 2
    elif end < len(current) and current[end] in "\r\n":
        end += 1
    next_text = f"{current[:begin]}{block}{current[end:]}"
    return (next_text, "unchanged") if next_text == current else (next_text, "replace")


def _expand_targets(target: str) -> tuple[str, ...]:
    if target == "all":
        return tuple(TARGET_PATHS)
    if target not in TARGET_PATHS:
        raise ValueError(f"Unknown init-agent target: {target}")
    return (target,)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


__all__ = [
    "BEGIN_MARKER",
    "END_MARKER",
    "TARGET_PATHS",
    "VALID_TARGETS",
    "NativeAgentError",
    "agent_instruction_block",
    "init_agent_instructions",
    "plan_agent_instruction_changes",
]
