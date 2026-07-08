"""AppBundle semantic diff helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec.app_validation import APP_BUNDLE_RESULT_SCHEMA_VERSION, _reject_json_constant, _stable_json, validate_app_text
from viewspec.intent_tools import _intent_topology_similarity, diff_intent_text, intent_semantic_change_lines

APP_BUNDLE_DIFF_VERSION = 1
APP_BUNDLE_DIFF_BASIS = "app_bundle_v0_v3"

def diff_app_text(left_text: str, right_text: str, *, compile_check: bool = True) -> dict[str, Any]:
    left_basic = validate_app_text(left_text, compile_check=False)
    right_basic = validate_app_text(right_text, compile_check=False)
    if not left_basic["ok"] or not right_basic["ok"]:
        if _validation_has_screen_intent_issue(left_basic) or _validation_has_screen_intent_issue(right_basic):
            return _app_diff_error_payload(
                "APP_DIFF_SCREEN_INTENT_INVALID",
                "One or both AppBundles contain an embedded screen intent that cannot be validated for diff-app.",
                "Regenerate invalid embedded screen IntentBundles before running diff-app.",
                validation={"left": left_basic, "right": right_basic},
            )
        return _app_diff_error_payload(
            "APP_DIFF_APP_INVALID",
            "One or both AppBundles failed V0 validation before diff.",
            "Fix AppBundle validation issues before running diff-app.",
            validation={"left": left_basic, "right": right_basic},
        )

    left_payload = json.loads(left_text, parse_constant=_reject_json_constant)
    right_payload = json.loads(right_text, parse_constant=_reject_json_constant)
    left_sections = {
        "app": {"app": left_payload["app"]},
        "routes": _index_by_id(left_payload["routes"]),
        "resources": _resource_sections(left_payload["resources"]),
        "screens": _screen_sections(left_payload["screens"]),
        "state": _state_sections(left_payload, "state"),
        "mutations": _state_sections(left_payload, "mutations"),
        "selectors": _state_sections(left_payload, "selectors"),
        "visibility": _state_sections(left_payload, "visibility"),
        "state_replay_assertions": _state_sections(left_payload, "state_replay_assertions"),
    }
    right_sections = {
        "app": {"app": right_payload["app"]},
        "routes": _index_by_id(right_payload["routes"]),
        "resources": _resource_sections(right_payload["resources"]),
        "screens": _screen_sections(right_payload["screens"]),
        "state": _state_sections(right_payload, "state"),
        "mutations": _state_sections(right_payload, "mutations"),
        "selectors": _state_sections(right_payload, "selectors"),
        "visibility": _state_sections(right_payload, "visibility"),
        "state_replay_assertions": _state_sections(right_payload, "state_replay_assertions"),
    }
    changes = {name: _diff_named_items(left_sections[name], right_sections[name]) for name in left_sections}
    semantic_changes = _app_semantic_changes(left_payload, right_payload)
    screen_intent_diffs: dict[str, Any] = {}
    left_screens = _index_by_id(left_payload["screens"])
    right_screens = _index_by_id(right_payload["screens"])
    for screen_id in sorted(set(left_screens) & set(right_screens)):
        left_intent = left_screens[screen_id].get("intent_bundle")
        right_intent = right_screens[screen_id].get("intent_bundle")
        if _stable_json(left_intent) == _stable_json(right_intent):
            continue
        intent_diff = diff_intent_text(
            _stable_json(left_intent),
            _stable_json(right_intent),
            compile_check=compile_check,
        )
        if not intent_diff.get("ok"):
            return _app_diff_error_payload(
                "APP_DIFF_SCREEN_INTENT_INVALID",
                f"Changed embedded screen intent could not be validated or diffed: {screen_id}",
                "Regenerate the changed screen IntentBundle before running diff-app.",
                validation={"left": left_basic, "right": right_basic},
                errors=[
                    {
                        "code": "APP_DIFF_SCREEN_INTENT_INVALID",
                        "message": f"Screen {screen_id} changed intent failed diff-intent validation.",
                        "fix": "Regenerate the changed screen IntentBundle before running diff-app.",
                        "screen_id": screen_id,
                    },
                    *_normalize_diff_errors(intent_diff.get("errors"), screen_id=screen_id),
                ],
            )
        summary = intent_semantic_change_lines(intent_diff.get("semantic_changes"))
        screen_intent_diffs[screen_id] = {
            "ok": True,
            "topology_similarity": intent_diff.get("topology_similarity"),
            "semantic_summary": summary,
            "semantic_changes": intent_diff.get("semantic_changes"),
            "changes": intent_diff.get("changes"),
        }
        semantic_changes["screen_intents"].append(
            {
                "screen_id": screen_id,
                "change": "intent_changed",
                "semantic_summary": summary,
            }
        )

    changed_fields = _app_changed_fields(left_payload, right_payload)
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "diff_version": APP_BUNDLE_DIFF_VERSION,
        "basis": APP_BUNDLE_DIFF_BASIS,
        "ok": True,
        "compile_check": "skipped" if not compile_check else "passed",
        "validation": {"left": _validation_summary(left_basic), "right": _validation_summary(right_basic)},
        "changes": changes,
        "changed_fields": changed_fields,
        "semantic_changes": semantic_changes,
        "semantic_summary": app_semantic_change_lines(semantic_changes),
        "screen_intent_diffs": screen_intent_diffs,
        "counts": _app_counts(left_payload, right_payload),
        "topology_similarity": _intent_topology_similarity(left_sections, right_sections, changes),
        "errors": [],
    }

def diff_app_files(left_path: str | Path, right_path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    left_text = Path(left_path).read_text(encoding="utf-8")
    right_text = Path(right_path).read_text(encoding="utf-8")
    return diff_app_text(left_text, right_text, compile_check=compile_check)

def app_semantic_change_lines(semantic_changes: object) -> list[str]:
    if not isinstance(semantic_changes, dict):
        return []
    lines: list[str] = []
    for item in semantic_changes.get("app_metadata", []):
        if isinstance(item, dict):
            lines.append(
                "app_metadata: "
                f"{_diff_value(item.get('field'))} "
                f"{_diff_value(item.get('left'))} -> {_diff_value(item.get('right'))}"
            )
    for section in ("routes", "resources", "screens", "state", "mutations", "selectors", "visibility", "state_replay_assertions"):
        entries = semantic_changes.get(section)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            item_id = _diff_value(item.get("id"))
            change = _diff_value(item.get("change"))
            if "field" in item:
                lines.append(
                    f"{section}.{item_id}: {change} "
                    f"{_diff_value(item.get('field'))} "
                    f"{_diff_value(item.get('left'))} -> {_diff_value(item.get('right'))}"
                )
            else:
                lines.append(f"{section}.{item_id}: {change}")
    for item in semantic_changes.get("screen_intents", []):
        if not isinstance(item, dict):
            continue
        screen_id = _diff_value(item.get("screen_id"))
        summary = item.get("semantic_summary")
        if isinstance(summary, list) and summary:
            for line in summary:
                lines.append(f"screen_intents.{screen_id}: {line}")
        else:
            lines.append(f"screen_intents.{screen_id}: intent_changed")
    return lines

def _index_by_id(items: list[Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id not in indexed:
            indexed[item_id] = item
    return indexed

def _resource_sections(resources: list[Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            continue
        records = resource.get("records") if isinstance(resource.get("records"), list) else []
        sections[resource["id"]] = {
            "id": resource.get("id"),
            "kind": resource.get("kind"),
            "record_count": len(records),
            "records_hash": _stable_json(records),
        }
    return sections

def _screen_sections(screens: list[Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for screen in screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        sections[screen["id"]] = {
            "id": screen.get("id"),
            "title": screen.get("title"),
            "resource_view_count": len(screen.get("resource_views")) if isinstance(screen.get("resource_views"), list) else 0,
            "resource_views_hash": _stable_json(screen.get("resource_views", [])),
            "intent_hash": _stable_json(screen.get("intent_bundle")),
        }
    return sections

def _state_sections(payload: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    items = payload.get(field) if isinstance(payload.get(field), list) else []
    sections: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        sections[item["id"]] = {"id": item["id"], "definition_hash": _stable_json(item)}
    return sections

def _diff_named_items(left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(right) - set(left)),
        "removed": sorted(set(left) - set(right)),
        "changed": sorted(
            item_id
            for item_id in set(left) & set(right)
            if _stable_json(left[item_id]) != _stable_json(right[item_id])
        ),
    }

def _app_semantic_changes(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    changes: dict[str, list[dict[str, Any]]] = {
        "app_metadata": [],
        "routes": [],
        "resources": [],
        "screens": [],
        "state": [],
        "mutations": [],
        "selectors": [],
        "visibility": [],
        "state_replay_assertions": [],
        "screen_intents": [],
    }
    left_app = left.get("app") if isinstance(left.get("app"), dict) else {}
    right_app = right.get("app") if isinstance(right.get("app"), dict) else {}
    for field in ("id", "title", "kind", "root_route"):
        if _stable_json(left_app.get(field)) != _stable_json(right_app.get(field)):
            changes["app_metadata"].append({"field": field, "left": left_app.get(field), "right": right_app.get(field)})
    for field in ("schema_version", "resource_binding"):
        if _stable_json(left.get(field)) != _stable_json(right.get(field)):
            changes["app_metadata"].append({"field": field, "left": left.get(field), "right": right.get(field)})
    for section, left_items, right_items in (
        ("routes", _index_by_id(left.get("routes", [])), _index_by_id(right.get("routes", []))),
        ("resources", _resource_sections(left.get("resources", [])), _resource_sections(right.get("resources", []))),
        ("screens", _screen_sections(left.get("screens", [])), _screen_sections(right.get("screens", []))),
        ("state", _state_sections(left, "state"), _state_sections(right, "state")),
        ("mutations", _state_sections(left, "mutations"), _state_sections(right, "mutations")),
        ("selectors", _state_sections(left, "selectors"), _state_sections(right, "selectors")),
        ("visibility", _state_sections(left, "visibility"), _state_sections(right, "visibility")),
        (
            "state_replay_assertions",
            _state_sections(left, "state_replay_assertions"),
            _state_sections(right, "state_replay_assertions"),
        ),
    ):
        for item_id in sorted(set(right_items) - set(left_items)):
            changes[section].append({"id": item_id, "change": "added"})
        for item_id in sorted(set(left_items) - set(right_items)):
            changes[section].append({"id": item_id, "change": "removed"})
        for item_id in sorted(set(left_items) & set(right_items)):
            left_item = left_items[item_id]
            right_item = right_items[item_id]
            for field in sorted(set(left_item) | set(right_item)):
                if field in {"id", "intent_hash", "records_hash", "resource_views_hash", "definition_hash"}:
                    continue
                if _stable_json(left_item.get(field)) != _stable_json(right_item.get(field)):
                    changes[section].append(
                        {
                            "id": item_id,
                            "change": "field_changed",
                            "field": field,
                            "left": left_item.get(field),
                            "right": right_item.get(field),
                        }
                    )
            if section == "resources" and left_item.get("records_hash") != right_item.get("records_hash"):
                changes[section].append({"id": item_id, "change": "records_changed"})
            if section == "screens" and left_item.get("intent_hash") != right_item.get("intent_hash"):
                changes[section].append({"id": item_id, "change": "intent_changed"})
            if section == "screens" and left_item.get("resource_views_hash") != right_item.get("resource_views_hash"):
                changes[section].append({"id": item_id, "change": "resource_views_changed"})
            if section in {"state", "mutations", "selectors", "visibility", "state_replay_assertions"} and left_item.get("definition_hash") != right_item.get("definition_hash"):
                changes[section].append({"id": item_id, "change": "definition_changed"})
    return changes

def _app_changed_fields(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for item in _app_semantic_changes(left, right)["app_metadata"]:
        field = str(item["field"])
        path = f"$.{field}" if field in {"schema_version", "resource_binding"} else f"$.app.{field}"
        fields.append({"path": path, "left": item.get("left"), "right": item.get("right")})
    return fields

def _app_counts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        "routes": {"left": len(left.get("routes", [])), "right": len(right.get("routes", []))},
        "resources": {"left": len(left.get("resources", [])), "right": len(right.get("resources", []))},
        "screens": {"left": len(left.get("screens", [])), "right": len(right.get("screens", []))},
        "resource_views": {"left": _resource_view_count(left), "right": _resource_view_count(right)},
        "state": {"left": len(left.get("state", [])) if isinstance(left.get("state"), list) else 0, "right": len(right.get("state", [])) if isinstance(right.get("state"), list) else 0},
        "mutations": {"left": len(left.get("mutations", [])) if isinstance(left.get("mutations"), list) else 0, "right": len(right.get("mutations", [])) if isinstance(right.get("mutations"), list) else 0},
        "selectors": {"left": len(left.get("selectors", [])) if isinstance(left.get("selectors"), list) else 0, "right": len(right.get("selectors", [])) if isinstance(right.get("selectors"), list) else 0},
        "visibility": {"left": len(left.get("visibility", [])) if isinstance(left.get("visibility"), list) else 0, "right": len(right.get("visibility", [])) if isinstance(right.get("visibility"), list) else 0},
        "state_replay_assertions": {
            "left": len(left.get("state_replay_assertions", [])) if isinstance(left.get("state_replay_assertions"), list) else 0,
            "right": len(right.get("state_replay_assertions", [])) if isinstance(right.get("state_replay_assertions"), list) else 0,
        },
    }

def _resource_view_count(payload: dict[str, Any]) -> int:
    total = 0
    for screen in payload.get("screens", []) if isinstance(payload.get("screens"), list) else []:
        if isinstance(screen, dict) and isinstance(screen.get("resource_views"), list):
            total += len(screen["resource_views"])
    return total

def _validation_summary(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": validation.get("ok"),
        "app_schema_version": validation.get("app_schema_version"),
        "compile_check": validation.get("compile_check"),
        "summary": validation.get("summary"),
        "issue_count": len(validation.get("issues", [])) if isinstance(validation.get("issues"), list) else 0,
    }

def _validation_has_screen_intent_issue(validation: dict[str, Any]) -> bool:
    issues = validation.get("issues")
    if not isinstance(issues, list):
        return False
    return any(isinstance(issue, dict) and str(issue.get("code", "")).startswith("APP_SCREEN_INTENT") for issue in issues)

def _app_diff_error_payload(
    code: str,
    message: str,
    fix: str,
    *,
    validation: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "diff_version": APP_BUNDLE_DIFF_VERSION,
        "basis": APP_BUNDLE_DIFF_BASIS,
        "ok": False,
        "compile_check": "failed",
        "validation": validation or {"left": None, "right": None},
        "changes": {
            "app": _empty_change_set(),
            "routes": _empty_change_set(),
            "resources": _empty_change_set(),
            "screens": _empty_change_set(),
            "state": _empty_change_set(),
            "mutations": _empty_change_set(),
            "selectors": _empty_change_set(),
            "visibility": _empty_change_set(),
            "state_replay_assertions": _empty_change_set(),
        },
        "changed_fields": [],
        "semantic_changes": {
            "app_metadata": [],
            "routes": [],
            "resources": [],
            "screens": [],
            "state": [],
            "mutations": [],
            "selectors": [],
            "visibility": [],
            "state_replay_assertions": [],
            "screen_intents": [],
        },
        "semantic_summary": [],
        "screen_intent_diffs": {},
        "counts": {
            "routes": {"left": 0, "right": 0},
            "resources": {"left": 0, "right": 0},
            "screens": {"left": 0, "right": 0},
            "state": {"left": 0, "right": 0},
            "mutations": {"left": 0, "right": 0},
            "selectors": {"left": 0, "right": 0},
            "visibility": {"left": 0, "right": 0},
            "state_replay_assertions": {"left": 0, "right": 0},
        },
        "topology_similarity": 0.0,
        "errors": errors or [{"code": code, "message": message, "fix": fix}],
    }

def _empty_change_set() -> dict[str, list[str]]:
    return {"added": [], "removed": [], "changed": []}

def _normalize_diff_errors(errors: object, *, screen_id: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(errors, list):
        return normalized
    for error in errors[:8]:
        if not isinstance(error, dict):
            continue
        normalized.append(
            {
                "code": str(error.get("code") or "APP_DIFF_SCREEN_INTENT_INVALID"),
                "message": f"Screen {screen_id}: {error.get('message') or 'Embedded intent diff failed.'}",
                "fix": str(error.get("fix") or "Regenerate the changed screen IntentBundle."),
            }
        )
    return normalized

def _diff_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)

__all__ = [
    "APP_BUNDLE_DIFF_BASIS",
    "APP_BUNDLE_DIFF_VERSION",
    "_validation_summary",
    "app_semantic_change_lines",
    "diff_app_files",
    "diff_app_text",
]
