"""Bounded summaries for compiled ViewSpec provenance manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize_intent_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"available": False, "reason": "missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "reason": "unreadable"}
    if not isinstance(manifest, dict):
        return {"available": False, "reason": "invalid"}
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return {"available": False, "reason": "missing_nodes"}
    return {
        "available": True,
        "kind": manifest.get("kind"),
        "emitter": manifest.get("emitter"),
        "artifact_file": manifest.get("artifact_file"),
        "node_count": len(nodes),
        "aesthetic_profile": manifest_root_aesthetic_profile(nodes),
        "aesthetic_layout": manifest_aesthetic_layout_summary(nodes),
    }


def manifest_root_aesthetic_profile(nodes: dict[str, Any]) -> str | None:
    for entry in nodes.values():
        if not isinstance(entry, dict) or entry.get("primitive") != "root":
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        profile = props.get("aesthetic_profile")
        return profile if isinstance(profile, str) else None
    return None


def manifest_aesthetic_layout_summary(nodes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    layout: dict[str, dict[str, Any]] = {}
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        profile = props.get("aesthetic_layout_profile")
        role = props.get("product_role")
        if not isinstance(profile, str) or not isinstance(role, str):
            continue
        item = layout.setdefault(
            role,
            {
                "profile": profile,
                "columns": _manifest_int(props.get("columns")),
                "node_count": 0,
            },
        )
        item["node_count"] = int(item["node_count"]) + 1
        if item["profile"] != profile or item["columns"] != _manifest_int(props.get("columns")):
            item["mixed"] = True
    return {role: layout[role] for role in sorted(layout)}


def _manifest_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "manifest_aesthetic_layout_summary",
    "manifest_root_aesthetic_profile",
    "summarize_intent_manifest",
]
