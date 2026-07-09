"""Export local agent-facing ViewSpec contract assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA, starter_app_bundle
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.local_tools import atomic_write


AGENT_ASSET_SCHEMA_VERSION = 10
AGENT_ASSET_CONTRACT_PROFILE = "local_v1"
AGENT_ASSET_EXPORT_COMMAND = "viewspec export-agent-assets --out .viewspec"
AGENT_ASSET_CHECK_COMMAND = "viewspec check-agent-assets .viewspec --json"
AGENT_ASSET_NETWORK_POLICY = "no SDK network calls"
AGENT_ASSET_MANIFEST_FILE = "agent-assets.json"
AGENT_SYSTEM_PROMPT_FILE = "agent-system-prompt.txt"
AGENT_INTENT_SCHEMA_FILE = "agent-intent-bundle.schema.json"
AGENT_INTENT_EXAMPLE_FILE = "agent-intent-example.dashboard.json"
AGENT_APP_SCHEMA_FILE = "agent-app-bundle.schema.json"
AGENT_APP_EXAMPLE_FILE = "agent-app-example.internal-tool.json"
AGENT_ASSET_PAYLOAD_FILES = (
    AGENT_SYSTEM_PROMPT_FILE,
    AGENT_INTENT_SCHEMA_FILE,
    AGENT_INTENT_EXAMPLE_FILE,
    AGENT_APP_SCHEMA_FILE,
    AGENT_APP_EXAMPLE_FILE,
)


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
        "contract_profile": AGENT_ASSET_CONTRACT_PROFILE,
        "out": str(output),
        "check_command": AGENT_ASSET_CHECK_COMMAND,
        "network_policy": AGENT_ASSET_NETWORK_POLICY,
        "files": [change.to_json(output) for change in changes],
    }


def agent_asset_readiness() -> dict[str, Any]:
    contents = _agent_asset_contents()
    return {
        "ok": True,
        "schema_version": AGENT_ASSET_SCHEMA_VERSION,
        "contract_profile": AGENT_ASSET_CONTRACT_PROFILE,
        "asset_manifest_file": AGENT_ASSET_MANIFEST_FILE,
        "system_prompt_file": AGENT_SYSTEM_PROMPT_FILE,
        "intent_schema_file": AGENT_INTENT_SCHEMA_FILE,
        "intent_example_file": AGENT_INTENT_EXAMPLE_FILE,
        "app_schema_file": AGENT_APP_SCHEMA_FILE,
        "app_example_file": AGENT_APP_EXAMPLE_FILE,
        "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
        "app_schema_id": AGENT_APP_BUNDLE_SCHEMA["$id"],
        "asset_manifest_sha256": hashlib.sha256(contents[AGENT_ASSET_MANIFEST_FILE].encode("utf-8")).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(contents[AGENT_SYSTEM_PROMPT_FILE].encode("utf-8")).hexdigest(),
        "intent_schema_sha256": hashlib.sha256(contents[AGENT_INTENT_SCHEMA_FILE].encode("utf-8")).hexdigest(),
        "intent_example_sha256": hashlib.sha256(contents[AGENT_INTENT_EXAMPLE_FILE].encode("utf-8")).hexdigest(),
        "app_schema_sha256": hashlib.sha256(contents[AGENT_APP_SCHEMA_FILE].encode("utf-8")).hexdigest(),
        "app_example_sha256": hashlib.sha256(contents[AGENT_APP_EXAMPLE_FILE].encode("utf-8")).hexdigest(),
        "export_command": AGENT_ASSET_EXPORT_COMMAND,
        "check_command": AGENT_ASSET_CHECK_COMMAND,
        "network_policy": AGENT_ASSET_NETWORK_POLICY,
    }


def check_agent_assets(asset_dir: str | Path = ".viewspec") -> dict[str, Any]:
    root = Path(asset_dir).resolve()
    expected_contents = _agent_asset_contents()
    expected_manifest = json.loads(expected_contents[AGENT_ASSET_MANIFEST_FILE])
    manifest_path = root / AGENT_ASSET_MANIFEST_FILE
    errors: list[str] = []
    files: list[dict[str, Any]] = []

    manifest: dict[str, Any] | None = None
    if not manifest_path.exists():
        errors.append(f"missing {AGENT_ASSET_MANIFEST_FILE}")
    else:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                errors.append(f"{AGENT_ASSET_MANIFEST_FILE} must be a JSON object")
        except json.JSONDecodeError as exc:
            errors.append(f"invalid {AGENT_ASSET_MANIFEST_FILE}: {exc}")
        except OSError as exc:
            errors.append(f"could not read {AGENT_ASSET_MANIFEST_FILE}: {exc}")

    if manifest is not None:
        if manifest != expected_manifest:
            errors.append(f"{AGENT_ASSET_MANIFEST_FILE} does not match the current ViewSpec agent asset contract")
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            errors.append(f"{AGENT_ASSET_MANIFEST_FILE} files must be a list")

    for filename in AGENT_ASSET_PAYLOAD_FILES:
        path = root / filename
        expected_hash = hashlib.sha256(expected_contents[filename].encode("utf-8")).hexdigest()
        entry = {"path": filename, "sha256": expected_hash, "status": "ok"}
        if not path.exists():
            entry["status"] = "missing"
            errors.append(f"missing {filename}")
        else:
            try:
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    entry["status"] = "mismatch"
                    entry["actual_sha256"] = actual_hash
                    errors.append(f"{filename} sha256 does not match the current ViewSpec agent asset contract")
            except OSError as exc:
                entry["status"] = "unreadable"
                errors.append(f"could not read {filename}: {exc}")
        files.append(entry)

    return {
        "ok": not errors,
        "schema_version": AGENT_ASSET_SCHEMA_VERSION,
        "contract_profile": AGENT_ASSET_CONTRACT_PROFILE,
        "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
        "app_schema_id": AGENT_APP_BUNDLE_SCHEMA["$id"],
        "check_command": AGENT_ASSET_CHECK_COMMAND,
        "network_policy": AGENT_ASSET_NETWORK_POLICY,
        "path": str(root),
        "manifest": str(manifest_path),
        "files": files,
        "errors": errors,
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
    from viewspec.intent_tools import starter_intent_payload

    prompt = AGENT_SYSTEM_PROMPT if AGENT_SYSTEM_PROMPT.endswith("\n") else f"{AGENT_SYSTEM_PROMPT}\n"
    schema = json.dumps(AGENT_INTENT_BUNDLE_SCHEMA, indent=2, sort_keys=True) + "\n"
    example = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    app_schema = json.dumps(AGENT_APP_BUNDLE_SCHEMA, indent=2, sort_keys=True) + "\n"
    app_example = json.dumps(starter_app_bundle("internal_tool"), indent=2, sort_keys=True) + "\n"
    contents = {
        AGENT_SYSTEM_PROMPT_FILE: prompt,
        AGENT_INTENT_SCHEMA_FILE: schema,
        AGENT_INTENT_EXAMPLE_FILE: example,
        AGENT_APP_SCHEMA_FILE: app_schema,
        AGENT_APP_EXAMPLE_FILE: app_example,
    }
    contents[AGENT_ASSET_MANIFEST_FILE] = _agent_asset_manifest(contents)
    return contents


def _agent_asset_manifest(contents: dict[str, str]) -> str:
    payload = {
        "schema_version": AGENT_ASSET_SCHEMA_VERSION,
        "kind": "viewspec_agent_assets",
        "contract": {
            "profile": AGENT_ASSET_CONTRACT_PROFILE,
            "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
            "app_schema_id": AGENT_APP_BUNDLE_SCHEMA["$id"],
            "export_command": AGENT_ASSET_EXPORT_COMMAND,
            "check_command": AGENT_ASSET_CHECK_COMMAND,
            "network_policy": AGENT_ASSET_NETWORK_POLICY,
            "files": {
                "manifest": AGENT_ASSET_MANIFEST_FILE,
                "system_prompt": AGENT_SYSTEM_PROMPT_FILE,
                "intent_schema": AGENT_INTENT_SCHEMA_FILE,
                "intent_example": AGENT_INTENT_EXAMPLE_FILE,
                "app_schema": AGENT_APP_SCHEMA_FILE,
                "app_example": AGENT_APP_EXAMPLE_FILE,
            },
        },
        "intent_schema_id": AGENT_INTENT_BUNDLE_SCHEMA["$id"],
        "app_schema_id": AGENT_APP_BUNDLE_SCHEMA["$id"],
        "files": [
            {
                "path": filename,
                "sha256": hashlib.sha256(contents[filename].encode("utf-8")).hexdigest(),
            }
            for filename in AGENT_ASSET_PAYLOAD_FILES
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


__all__ = [
    "AGENT_ASSET_CHECK_COMMAND",
    "AGENT_ASSET_CONTRACT_PROFILE",
    "AGENT_ASSET_EXPORT_COMMAND",
    "AGENT_ASSET_MANIFEST_FILE",
    "AGENT_ASSET_NETWORK_POLICY",
    "AGENT_ASSET_PAYLOAD_FILES",
    "AGENT_ASSET_SCHEMA_VERSION",
    "AGENT_APP_EXAMPLE_FILE",
    "AGENT_APP_SCHEMA_FILE",
    "AGENT_INTENT_EXAMPLE_FILE",
    "AGENT_INTENT_SCHEMA_FILE",
    "AGENT_SYSTEM_PROMPT_FILE",
    "AgentAssetError",
    "agent_asset_readiness",
    "check_agent_assets",
    "export_agent_assets",
    "plan_agent_asset_exports",
]
