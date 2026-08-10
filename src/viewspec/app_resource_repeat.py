"""Deterministic resource-view repeat materialization for AppBundle screens."""

from __future__ import annotations

import copy
import hashlib
from typing import Any


RESOURCE_REPEAT_ALLOWED_FIELDS = frozenset({"field_presentations", "group_id"})
RESOURCE_REPEAT_FIELD_PRESENTATION_ALLOWED_FIELDS = frozenset({"field", "present_as"})


def materialize_resource_repeats(payload: dict[str, Any]) -> dict[str, Any]:
    materialized = copy.deepcopy(payload)
    resources = _fixture_records(materialized)
    for screen in materialized.get("screens", []):
        if not isinstance(screen, dict):
            continue
        intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
        substrate = intent.get("substrate") if isinstance(intent.get("substrate"), dict) else {}
        nodes = substrate.get("nodes") if isinstance(substrate.get("nodes"), dict) else {}
        view_spec = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
        bindings = view_spec.get("bindings") if isinstance(view_spec.get("bindings"), list) else []
        motifs = {
            str(motif.get("id")): motif
            for motif in view_spec.get("motifs", [])
            if isinstance(motif, dict) and isinstance(motif.get("id"), str)
        }
        groups = {
            str(group.get("id")): group
            for group in view_spec.get("groups", [])
            if isinstance(group, dict) and isinstance(group.get("id"), str)
        }
        existing_binding_ids = {
            str(binding.get("id"))
            for binding in bindings
            if isinstance(binding, dict) and isinstance(binding.get("id"), str)
        }
        existing_addresses = {
            str(binding.get("address"))
            for binding in bindings
            if isinstance(binding, dict) and isinstance(binding.get("address"), str)
        }
        for resource_view in screen.get("resource_views", []):
            if not isinstance(resource_view, dict) or not isinstance(resource_view.get("repeat"), dict):
                continue
            view_id = str(resource_view.get("id") or "")
            resource_id = str(resource_view.get("resource_id") or "")
            motif = motifs.get(str(resource_view.get("target_motif_id") or ""))
            if motif is None:
                continue
            region_id = str(motif.get("region") or "")
            motif_members = motif.get("members") if isinstance(motif.get("members"), list) else []
            group_id = resource_view["repeat"].get("group_id")
            group = groups.get(str(group_id)) if isinstance(group_id, str) else None
            group_members = group.get("members") if isinstance(group, dict) and isinstance(group.get("members"), list) else None
            presentations = {
                str(item.get("field")): str(item.get("present_as"))
                for item in resource_view["repeat"].get("field_presentations", [])
                if isinstance(item, dict)
                and isinstance(item.get("field"), str)
                and isinstance(item.get("present_as"), str)
            }
            records = resources.get(resource_id, {})
            for record_id in resource_view.get("record_ids", []):
                if not isinstance(record_id, str):
                    continue
                record = records.get(record_id)
                if not isinstance(record, dict):
                    continue
                node_id = resource_repeat_node_id(view_id, record_id)
                if node_id not in nodes:
                    fields = [field for field in resource_view.get("fields", []) if isinstance(field, str)]
                    nodes[node_id] = {
                        "id": node_id,
                        "kind": _node_kind_for_motif(str(motif.get("kind") or "")),
                        "attrs": {field: copy.deepcopy(record[field]) for field in fields if field in record},
                        "slots": {},
                        "edges": {},
                    }
                for field in resource_view.get("fields", []):
                    if not isinstance(field, str) or field not in presentations:
                        continue
                    binding_id = resource_repeat_binding_id(view_id, record_id, field)
                    address = resource_binding_address(resource_view, record_id, field)
                    if binding_id in existing_binding_ids or address in existing_addresses:
                        continue
                    bindings.append(
                        {
                            "id": binding_id,
                            "address": address,
                            "target_region": region_id,
                            "present_as": presentations[field],
                            "cardinality": "exactly_once",
                        }
                    )
                    existing_binding_ids.add(binding_id)
                    existing_addresses.add(address)
                    if binding_id not in motif_members:
                        motif_members.append(binding_id)
                    if group_members is not None and binding_id not in group_members:
                        group_members.append(binding_id)
    return materialized


def resource_binding_address(resource_view: dict[str, Any], record_id: str, field: str) -> str:
    if isinstance(resource_view.get("repeat"), dict):
        return f"node:{resource_repeat_node_id(str(resource_view.get('id') or ''), record_id)}#attr:{field}"
    return f"node:{record_id}#attr:{field}"


def resource_repeat_node_id(view_id: str, record_id: str) -> str:
    return _bounded_id("rvn", (view_id, record_id))


def resource_repeat_binding_id(view_id: str, record_id: str, field: str) -> str:
    return _bounded_id("rvb", (view_id, record_id, field))


def resource_repeat_summary(payload: dict[str, Any]) -> dict[str, Any]:
    views: list[dict[str, Any]] = []
    for screen in payload.get("screens", []):
        if not isinstance(screen, dict):
            continue
        for resource_view in screen.get("resource_views", []):
            if not isinstance(resource_view, dict) or not isinstance(resource_view.get("repeat"), dict):
                continue
            record_ids = [item for item in resource_view.get("record_ids", []) if isinstance(item, str)]
            fields = [item for item in resource_view.get("fields", []) if isinstance(item, str)]
            views.append(
                {
                    "screen_id": str(screen.get("id") or ""),
                    "resource_view_id": str(resource_view.get("id") or ""),
                    "resource_id": str(resource_view.get("resource_id") or ""),
                    "target_motif_id": str(resource_view.get("target_motif_id") or ""),
                    "record_count": len(record_ids),
                    "field_count": len(fields),
                    "generated_binding_count": len(record_ids) * len(fields),
                }
            )
    return {
        "profile": "resource_repeat_v1",
        "view_count": len(views),
        "generated_binding_count": sum(view["generated_binding_count"] for view in views),
        "views": views,
    }


def _fixture_records(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    resources: dict[str, dict[str, dict[str, Any]]] = {}
    for resource in payload.get("resources", []):
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            continue
        records: dict[str, dict[str, Any]] = {}
        for record in resource.get("records", []):
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records[record["id"]] = record
        resources[resource["id"]] = records
    return resources


def _node_kind_for_motif(motif_kind: str) -> str:
    if motif_kind == "table":
        return "table_row"
    if motif_kind == "list":
        return "list_item"
    return "record"


def _bounded_id(prefix: str, parts: tuple[str, ...]) -> str:
    candidate = f"{prefix}_{'__'.join(parts)}"
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    shortened = [part[:28] for part in parts]
    return f"{prefix}_{'__'.join(shortened)}__{digest}"[:128]


__all__ = [
    "RESOURCE_REPEAT_ALLOWED_FIELDS",
    "RESOURCE_REPEAT_FIELD_PRESENTATION_ALLOWED_FIELDS",
    "materialize_resource_repeats",
    "resource_binding_address",
    "resource_repeat_binding_id",
    "resource_repeat_node_id",
    "resource_repeat_summary",
]
