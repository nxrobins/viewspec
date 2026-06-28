"""AppBundle starter generation helpers."""

from __future__ import annotations

from typing import Any

from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_SCHEMA_VERSION,
)
from viewspec.sdk.builder import ViewSpecBuilder


def starter_app_bundle(kind: str = "internal_tool", *, resource_binding: str = APP_BUNDLE_RESOURCE_BINDING) -> dict[str, Any]:
    """Return a valid two-screen AppBundle starter."""
    if kind not in APP_BUNDLE_ALLOWED_KINDS:
        raise ValueError(f"Unknown starter app kind: {kind}")
    if resource_binding not in {APP_BUNDLE_RESOURCE_BINDING, APP_BUNDLE_RESOURCE_BINDING_READONLY}:
        raise ValueError(f"Unknown starter app resource binding: {resource_binding}")
    if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return _starter_bound_app_bundle(kind)
    return {
        "schema_version": APP_BUNDLE_SCHEMA_VERSION,
        "app": {
            "id": "incident_console",
            "title": "Incident Console",
            "kind": "internal_tool",
            "root_route": "/",
        },
        "routes": [
            {"id": "queue", "path": "/", "label": "Queue", "screen_id": "queue"},
            {"id": "detail", "path": "/incident", "label": "Incident", "screen_id": "detail"},
        ],
        "resources": [
            {
                "id": "incidents",
                "kind": "fixture",
                "records": [
                    {"id": "inc_1042", "severity": "high", "status": "investigating"},
                    {"id": "inc_1043", "severity": "medium", "status": "queued"},
                ],
            }
        ],
        "screens": [
            {
                "id": "queue",
                "title": "Incident Queue",
                "intent_bundle": _starter_queue_screen_intent(),
            },
            {
                "id": "detail",
                "title": "Incident Detail",
                "intent_bundle": _starter_detail_screen_intent(),
            },
        ],
    }


def _starter_bound_app_bundle(kind: str) -> dict[str, Any]:
    resources = [
        {
            "id": "incidents",
            "kind": "fixture",
            "records": [
                {"id": "inc_1042", "severity": "high", "status": "investigating"},
                {"id": "inc_1043", "severity": "medium", "status": "queued"},
            ],
        }
    ]
    return {
        "schema_version": APP_BUNDLE_BOUND_SCHEMA_VERSION,
        "resource_binding": APP_BUNDLE_RESOURCE_BINDING_READONLY,
        "app": {
            "id": "incident_console",
            "title": "Incident Console",
            "kind": kind,
            "root_route": "/",
        },
        "routes": [
            {"id": "queue", "path": "/", "label": "Queue", "screen_id": "queue"},
            {"id": "detail", "path": "/incident", "label": "Incident", "screen_id": "detail"},
        ],
        "resources": resources,
        "screens": [
            {
                "id": "queue",
                "title": "Incident Queue",
                "resource_views": [
                    {
                        "id": "queue_incidents",
                        "resource_id": "incidents",
                        "mode": "list",
                        "record_ids": ["inc_1042", "inc_1043"],
                        "fields": ["id", "severity", "status"],
                        "target_motif_id": "incidents",
                    }
                ],
                "intent_bundle": _starter_bound_queue_screen_intent(),
            },
            {
                "id": "detail",
                "title": "Incident Detail",
                "resource_views": [
                    {
                        "id": "detail_incident",
                        "resource_id": "incidents",
                        "mode": "list",
                        "record_ids": ["inc_1042"],
                        "fields": ["id", "severity", "status"],
                        "target_motif_id": "incident",
                    }
                ],
                "intent_bundle": _starter_bound_detail_screen_intent(),
            },
        ],
    }


def _starter_queue_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident Queue"})
    table = builder.add_table("incidents", region="main", group_id="incident_rows")
    table.add_row(label="INC-1042", value="High - Investigating", id="inc_1042")
    table.add_row(label="INC-1043", value="Medium - Queued", id="inc_1043")
    return builder.build_bundle().to_json()


def _starter_detail_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_detail", root_attrs={"title": "Incident Detail"})
    detail = builder.add_detail("incident", region="main", group_id="incident_fields")
    detail.add_field(label="Incident", value="INC-1042", id="identifier")
    detail.add_field(label="Severity", value="High", id="severity")
    detail.add_field(label="Status", value="Investigating", id="status")
    detail.add_field(label="Owner", value="On-call Response", id="owner")
    return builder.build_bundle().to_json()


def _starter_bound_queue_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident Queue"})
    members: list[str] = []
    for record in (
        {"id": "inc_1042", "severity": "high", "status": "investigating"},
        {"id": "inc_1043", "severity": "medium", "status": "queued"},
    ):
        record_id = str(record["id"])
        builder.add_node(record_id, "table_row", attrs=dict(record))
        members.extend(
            [
                builder.bind_attr(f"{record_id}_id", record_id, "id", present_as="label"),
                builder.bind_attr(f"{record_id}_severity", record_id, "severity", present_as="value"),
                builder.bind_attr(f"{record_id}_status", record_id, "status", present_as="value"),
            ]
        )
    builder.add_group("incident_rows", "ordered", members, target_region="main")
    builder.add_motif("incidents", "table", "main", members)
    return builder.build_bundle().to_json()


def _starter_bound_detail_screen_intent() -> dict[str, Any]:
    builder = ViewSpecBuilder("incident_detail", root_attrs={"title": "Incident Detail"})
    record = {"id": "inc_1042", "severity": "high", "status": "investigating"}
    builder.add_node("inc_1042", "detail_field", attrs=dict(record))
    members = [
        builder.bind_attr("inc_1042_id", "inc_1042", "id", present_as="label"),
        builder.bind_attr("inc_1042_severity", "inc_1042", "severity", present_as="value"),
        builder.bind_attr("inc_1042_status", "inc_1042", "status", present_as="value"),
    ]
    builder.add_group("incident_fields", "ordered", members, target_region="main")
    builder.add_motif("incident", "detail", "main", members)
    return builder.build_bundle().to_json()


__all__ = ["starter_app_bundle"]
