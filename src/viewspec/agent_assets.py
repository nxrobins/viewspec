"""Export local agent-facing ViewSpec contract assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.local_tools import atomic_write


AGENT_ASSET_SCHEMA_VERSION = 2
AGENT_SYSTEM_PROMPT_FILE = "agent-system-prompt.txt"
AGENT_INTENT_SCHEMA_FILE = "agent-intent-bundle.schema.json"
AGENT_INTENT_EXAMPLE_FILE = "agent-intent-example.dashboard.json"


class AgentAssetError(ValueError):
    """Raised when local agent contract assets cannot be exported safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentAssetChange:
    path: Path
    action: str
    content: str

    def to_json(self, root: Path) -> dict[str, str]:
        return {
            "path": _display_path(self.path, root),
            "action": self.action,
            "sha256": hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
        }


def export_agent_assets(out_dir: str | Path, *, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    output = Path(out_dir).resolve()
    changes = plan_agent_asset_exports(output, force=force)
    if not dry_run:
        for change in changes:
            if change.action == "unchanged":
                continue
            atomic_write(change.path, change.content)
    return {
        "ok": True,
        "schema_version": AGENT_ASSET_SCHEMA_VERSION,
        "out": str(output),
        "files": [change.to_json(output) for change in changes],
    }


def agent_asset_readiness() -> dict[str, Any]:
    contents = _agent_asset_contents()
    return {
        "ok": True,
        "schema_version": AGENT_ASSET_SCHEMA_VERSION,
        "system_prompt_file": AGENT_SYSTEM_PROMPT_FILE,
        "intent_schema_file": AGENT_INTENT_SCHEMA_FILE,
        "intent_example_file": AGENT_INTENT_EXAMPLE_FILE,
        "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
        "system_prompt_sha256": hashlib.sha256(contents[AGENT_SYSTEM_PROMPT_FILE].encode("utf-8")).hexdigest(),
        "intent_schema_sha256": hashlib.sha256(contents[AGENT_INTENT_SCHEMA_FILE].encode("utf-8")).hexdigest(),
        "intent_example_sha256": hashlib.sha256(contents[AGENT_INTENT_EXAMPLE_FILE].encode("utf-8")).hexdigest(),
        "export_command": "viewspec export-agent-assets --out .viewspec",
    }


def plan_agent_asset_exports(out_dir: str | Path, *, force: bool = False) -> list[AgentAssetChange]:
    output = Path(out_dir).resolve()
    if output.exists() and not output.is_dir():
        raise AgentAssetError(
            "AGENT_ASSET_OUTPUT_NOT_DIRECTORY",
            f"{output} exists and is not a directory.",
        )
    changes: list[AgentAssetChange] = []
    for filename, content in _agent_asset_contents().items():
        path = output / filename
        if not path.exists():
            changes.append(AgentAssetChange(path, "create", content))
            continue
        current = _read_text_exact(path)
        if current == content:
            changes.append(AgentAssetChange(path, "unchanged", content))
        elif force:
            changes.append(AgentAssetChange(path, "replace", content))
        else:
            raise AgentAssetError(
                "AGENT_ASSET_CONFLICT",
                f"{path} already exists with different content; pass --force to replace it.",
            )
    return changes


def _agent_asset_contents() -> dict[str, str]:
    from viewspec.intent_tools import starter_intent_bundle

    prompt = AGENT_SYSTEM_PROMPT if AGENT_SYSTEM_PROMPT.endswith("\n") else f"{AGENT_SYSTEM_PROMPT}\n"
    schema = json.dumps(AGENT_INTENT_BUNDLE_SCHEMA, indent=2, sort_keys=True) + "\n"
    example = json.dumps(starter_intent_bundle("dashboard").to_json(), indent=2, sort_keys=True) + "\n"
    return {
        AGENT_SYSTEM_PROMPT_FILE: prompt,
        AGENT_INTENT_SCHEMA_FILE: schema,
        AGENT_INTENT_EXAMPLE_FILE: example,
    }


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


__all__ = [
    "AGENT_ASSET_SCHEMA_VERSION",
    "AGENT_INTENT_EXAMPLE_FILE",
    "AGENT_INTENT_SCHEMA_FILE",
    "AGENT_SYSTEM_PROMPT_FILE",
    "AgentAssetError",
    "agent_asset_readiness",
    "export_agent_assets",
    "plan_agent_asset_exports",
]
