"""AppBundle resource-binding proof helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from viewspec.app_validation import (
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_RESOURCE_BINDING_MAX_REPORT_BYTES,
    APP_RESOURCE_BINDING_TEXT_PRIMITIVES,
    _fixture_records_by_resource,
    _resource_binding_for_payload,
    _resource_binding_limits,
    _stable_json,
)


def _resource_binding_assertion_report(payload: dict[str, Any], screen_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    if _resource_binding_for_payload(payload) != APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return None
    screen_reports_by_id = {screen.get("id"): screen for screen in screen_reports if isinstance(screen, dict)}
    resources = _fixture_records_by_resource(payload.get("resources") if isinstance(payload.get("resources"), list) else [], [])
    views: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    occurrence_credit: set[tuple[str, str, str]] = set()
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    for screen in screens:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        screen_id = screen["id"]
        projection = _screen_manifest_binding_projection(screen_reports_by_id.get(screen_id))
        for resource_view in screen.get("resource_views", []) if isinstance(screen.get("resource_views"), list) else []:
            if not isinstance(resource_view, dict):
                continue
            view_id = str(resource_view.get("id") or "")
            target_motif_id = str(resource_view.get("target_motif_id") or "")
            resource_id = str(resource_view.get("resource_id") or "")
            view_assertions: list[dict[str, Any]] = []
            records = resources.get(resource_id, {})
            record_ids = [item for item in resource_view.get("record_ids", []) if isinstance(item, str)]
            fields = [item for item in resource_view.get("fields", []) if isinstance(item, str)]
            ambiguous_view_values = _resource_binding_ambiguous_view_values(records, record_ids, fields)
            for record_id in record_ids:
                record = records.get(record_id, {})
                for field in fields:
                    value_text = _resource_binding_scalar_text(record.get(field) if isinstance(record, dict) else None)
                    ambiguous_value = (field, value_text) in ambiguous_view_values
                    candidates = (
                        []
                        if ambiguous_value
                        else _resource_binding_candidates(
                            projection,
                            screen,
                            target_motif_id=target_motif_id,
                            record_id=record_id,
                            field=field,
                            value_text=value_text,
                        )
                    )
                    assertion = {
                        "screen_id": screen_id,
                        "resource_view_id": view_id,
                        "resource_id": resource_id,
                        "record_id": record_id,
                        "field": field,
                        "target_motif_id": target_motif_id,
                        "expected": value_text,
                        "status": "passed" if len(candidates) == 1 else "failed",
                        "source": "compiler_semantic_inventory_text",
                        "matched_binding_id": candidates[0]["binding_id"] if len(candidates) == 1 else None,
                        "matched_dom_id": candidates[0]["dom_id"] if len(candidates) == 1 else None,
                    }
                    if ambiguous_value:
                        errors.append(
                            {
                                "code": "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE",
                                "message": f"Resource view {view_id} repeats scalar value {value_text!r} for field {field}.",
                                "fix": "Use unique scalar values within each declared resource_view field or defer this proof to a later binding slice.",
                            }
                        )
                    elif len(candidates) == 1:
                        credit_key = (screen_id, str(candidates[0]["dom_id"]), f"{view_id}:{record_id}:{field}")
                        if any(existing[0] == credit_key[0] and existing[1] == credit_key[1] for existing in occurrence_credit):
                            assertion["status"] = "failed"
                            errors.append(
                                {
                                    "code": "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE",
                                    "message": f"Screen {screen_id} value for {view_id}.{record_id}.{field} reused one semantic occurrence.",
                                    "fix": "Render each record-field assertion from a distinct target motif binding.",
                                }
                            )
                        else:
                            occurrence_credit.add(credit_key)
                    else:
                        error_code = "APP_RESOURCE_BINDING_ASSERTION_FAILED"
                        if len(candidates) > 1:
                            error_code = "APP_RESOURCE_BINDING_AMBIGUOUS_VALUE"
                        errors.append(
                            {
                                "code": error_code,
                                "message": f"Screen {screen_id} failed resource binding assertion {view_id}.{record_id}.{field}.",
                                "fix": "Render the exact fixture scalar as visible text in the declared target motif binding.",
                            }
                        )
                    view_assertions.append(assertion)
                    assertions.append(assertion)
            view_status = "passed" if view_assertions and all(item.get("status") == "passed" for item in view_assertions) else "failed"
            if not view_assertions:
                errors.append(
                    {
                        "code": "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                        "message": f"Resource view {view_id} produced no record-field assertions.",
                        "fix": "Declare at least one record_id and one field for every resource_view.",
                    }
                )
            views.append(
                {
                    "id": view_id,
                    "screen_id": screen_id,
                    "resource_id": resource_id,
                    "target_motif_id": target_motif_id,
                    "assertion_count": len(view_assertions),
                    "passed_count": sum(1 for item in view_assertions if item.get("status") == "passed"),
                    "status": view_status,
                    "assertions": view_assertions,
                }
            )
    if not assertions:
        errors.append(
            {
                "code": "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                "message": "fixture_readonly_v0 produced no record-field assertions.",
                "fix": "Declare at least one resource_view with one record_id and one field.",
            }
        )
    digest_payload = {
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "assertions": [
            {
                "screen_id": item.get("screen_id"),
                "resource_view_id": item.get("resource_view_id"),
                "record_id": item.get("record_id"),
                "field": item.get("field"),
                "target_motif_id": item.get("target_motif_id"),
                "expected": item.get("expected"),
                "matched_binding_id": item.get("matched_binding_id"),
                "matched_dom_id": item.get("matched_dom_id"),
                "status": item.get("status"),
            }
            for item in assertions
        ],
    }
    binding_digest = _sha256_text(_stable_json(digest_payload))
    report = {
        "ok": not errors,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "proof_source": "compiler_semantic_inventory_text",
        "assertion_count": len(assertions),
        "passed_count": sum(1 for item in assertions if item.get("status") == "passed"),
        "failed_count": sum(1 for item in assertions if item.get("status") != "passed"),
        "view_count": len(views),
        "views": views,
        "binding_digest": binding_digest,
        "limits": _resource_binding_limits(),
        "errors": errors,
    }
    size = len(json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > APP_RESOURCE_BINDING_MAX_REPORT_BYTES:
        return {
            "ok": False,
            "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
            "binding_scope": APP_BUNDLE_BINDING_SCOPE,
            "proof_source": "compiler_semantic_inventory_text",
            "assertion_count": len(assertions),
            "passed_count": 0,
            "failed_count": len(assertions),
            "view_count": len(views),
            "views": [],
            "binding_digest": binding_digest,
            "limits": _resource_binding_limits(),
            "errors": [
                {
                    "code": "APP_RESOURCE_BINDING_REPORT_TOO_LARGE",
                    "message": f"Resource binding assertion report is {size} bytes; limit is {APP_RESOURCE_BINDING_MAX_REPORT_BYTES}.",
                    "fix": "Reduce resource views, record refs, or fields.",
                }
            ],
        }
    return report


def _screen_manifest_binding_projection(screen_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(screen_report, dict):
        return []
    paths = screen_report.get("paths") if isinstance(screen_report.get("paths"), dict) else {}
    manifest_path = Path(str(paths.get("manifest") or ""))
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    nodes = manifest.get("nodes") if isinstance(manifest.get("nodes"), dict) else {}
    projection: list[dict[str, Any]] = []
    for dom_id, entry in nodes.items():
        if not isinstance(dom_id, str) or not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        primitive = str(entry.get("primitive") or "")
        binding_id = props.get("binding_id")
        if not isinstance(binding_id, str) or primitive not in APP_RESOURCE_BINDING_TEXT_PRIMITIVES:
            continue
        content_refs = entry.get("content_refs") if isinstance(entry.get("content_refs"), list) else []
        text = props.get("text")
        projection.append(
            {
                "dom_id": dom_id,
                "ir_id": str(entry.get("ir_id") or ""),
                "primitive": primitive,
                "binding_id": binding_id,
                "content_refs": [item for item in content_refs if isinstance(item, str)],
                "visible_text": str(text) if isinstance(text, str) else "",
            }
        )
    return projection


def _resource_binding_ambiguous_view_values(
    records: dict[str, dict[str, Any]],
    record_ids: list[str],
    fields: list[str],
) -> set[tuple[str, str]]:
    repeated: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    for record_id in record_ids:
        record = records.get(record_id)
        if not isinstance(record, dict):
            continue
        for field in fields:
            value_text = _resource_binding_scalar_text(record.get(field))
            key = (field, value_text)
            if key in seen:
                repeated.add(key)
            seen.add(key)
    return repeated


def _resource_binding_candidates(
    projection: list[dict[str, Any]],
    screen: dict[str, Any],
    *,
    target_motif_id: str,
    record_id: str,
    field: str,
    value_text: str,
) -> list[dict[str, Any]]:
    motif_members = _screen_motif_members(screen, target_motif_id)
    expected_ref = f"node:{record_id}#attr:{field}"
    matches: list[dict[str, Any]] = []
    for item in projection:
        binding_id = item.get("binding_id")
        if binding_id not in motif_members:
            continue
        if item.get("visible_text") != value_text:
            continue
        content_refs = item.get("content_refs") if isinstance(item.get("content_refs"), list) else []
        if expected_ref not in content_refs:
            continue
        matches.append(item)
    return matches


def _screen_motif_members(screen: dict[str, Any], motif_id: str) -> set[str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view_spec = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    motifs = view_spec.get("motifs") if isinstance(view_spec.get("motifs"), list) else []
    for motif in motifs:
        if isinstance(motif, dict) and motif.get("id") == motif_id and isinstance(motif.get("members"), list):
            return {item for item in motif["members"] if isinstance(item, str)}
    return set()


def _resource_binding_scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if type(value) in {int, float}:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["_resource_binding_assertion_report"]
