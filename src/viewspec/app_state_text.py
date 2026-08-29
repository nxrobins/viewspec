"""Bounded scalar state-to-text compile helpers for AppBundle V4."""

from __future__ import annotations

from typing import Any

from viewspec.state_ir import StateIR, initial_state_text


STATE_TEXT_OVERLAY_KEYS = frozenset({"state_text_id", "state_text_initial"})


def state_text_target_node_id(target_ref: str) -> str:
    kind, _, target_id = target_ref.partition(":")
    if kind != "binding" or not target_id:
        raise ValueError(f"Unsupported state text target ref {target_ref!r}.")
    return f"binding_{target_id}"


def screen_state_text_overlays(
    app_payload: dict[str, Any],
    state_ir: StateIR,
) -> dict[str, dict[str, dict[str, Any]]]:
    values = initial_state_text(app_payload, state_ir)
    overlays: dict[str, dict[str, dict[str, Any]]] = {}
    for rule in state_ir.state_text:
        overlays.setdefault(rule.screen_id, {})[state_text_target_node_id(rule.target_ref)] = {
            "state_text_id": rule.id,
            "state_text_initial": values[rule.id],
        }
    return overlays


def check_screen_state_text_bake(
    manifest: dict[str, Any],
    overlay: dict[str, dict[str, Any]],
) -> list[str]:
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    if not isinstance(nodes, dict):
        return ["screen manifest has no nodes map"]
    baked: dict[str, tuple[str, str]] = {}
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        projection_id = props.get("state_text_id")
        if not isinstance(projection_id, str):
            continue
        ir_id = str(entry.get("ir_id"))
        if ir_id in baked:
            return [f"node {ir_id} carries more than one state text marker"]
        baked[ir_id] = (projection_id, str(props.get("state_text_initial") or ""))
    mismatches: list[str] = []
    for node_id, props in overlay.items():
        expected = (str(props["state_text_id"]), str(props["state_text_initial"]))
        actual = baked.pop(node_id, None)
        if actual is None:
            mismatches.append(f"rule {expected[0]}: node {node_id} lost its state text marker")
        elif actual != expected:
            mismatches.append(f"rule {expected[0]}: node {node_id} baked {actual}, expected {expected}")
    for node_id, (projection_id, _initial) in sorted(baked.items()):
        mismatches.append(f"rule {projection_id}: unexpected state text marker on node {node_id}")
    return mismatches


__all__ = [
    "STATE_TEXT_OVERLAY_KEYS",
    "check_screen_state_text_bake",
    "screen_state_text_overlays",
    "state_text_target_node_id",
]
