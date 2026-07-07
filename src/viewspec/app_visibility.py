"""AppBundle V4 visibility_v0 compile-time helpers.

Bridges validated visibility rules to compiled screen artifacts: resolves each rule's target ref
to its deterministic IR node id, derives the per-screen IR-props overlay from the SINGLE source of
initial truth (``initial_visibility``, SC-V1), and cross-checks the emitted manifest against that
overlay so a silently dropped or mis-baked marker fails the proof (``APP_VISIBILITY_BAKE_MISMATCH``).
"""

from __future__ import annotations

from typing import Any

from viewspec.state_ir import StateIR, initial_visibility

# ref kind -> IR node id prefix. Verified against the compiler's node construction:
# region:x -> region_x, binding:x -> binding_x, motif:x -> motif_x.
_TARGET_NODE_PREFIXES = {"region": "region_", "binding": "binding_", "motif": "motif_"}

VISIBILITY_OVERLAY_KEYS = frozenset({"visibility_rule_id", "visibility_hidden_initial"})


def visibility_target_node_id(target_ref: str) -> str:
    kind, _, target_id = target_ref.partition(":")
    prefix = _TARGET_NODE_PREFIXES.get(kind)
    if prefix is None:
        raise ValueError(f"Unsupported visibility target ref {target_ref!r}.")
    return f"{prefix}{target_id}"


def screen_visibility_overlays(
    app_payload: dict[str, Any],
    state_ir: StateIR,
) -> dict[str, dict[str, dict[str, Any]]]:
    """screen_id -> IR node id -> props overlay, derived from initial_visibility (SC-V1)."""
    verdicts = initial_visibility(app_payload, state_ir)
    overlays: dict[str, dict[str, dict[str, Any]]] = {}
    for rule in state_ir.visibility:
        node_id = visibility_target_node_id(rule.target_ref)
        overlays.setdefault(rule.screen_id, {})[node_id] = {
            "visibility_rule_id": rule.id,
            "visibility_hidden_initial": not verdicts.get(rule.id, True),
        }
    return overlays


def check_screen_visibility_bake(
    manifest: dict[str, Any],
    overlay: dict[str, dict[str, Any]],
) -> list[str]:
    """SC-V1 cross-check: the emitted manifest must carry exactly the overlay's markers.

    Returns human-readable mismatch descriptions (empty = proven bake). Catches a marker that was
    silently dropped by the emitter, applied to the wrong node, baked with the wrong initial
    verdict, or materialized more than once.
    """
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    if not isinstance(nodes, dict):
        return ["screen manifest has no nodes map"]
    baked: dict[str, tuple[str, bool]] = {}
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        rule_id = props.get("visibility_rule_id")
        if not isinstance(rule_id, str):
            continue
        ir_id = str(entry.get("ir_id"))
        if ir_id in baked:
            return [f"node {ir_id} carries more than one visibility marker"]
        baked[ir_id] = (rule_id, bool(props.get("visibility_hidden_initial")))
    mismatches: list[str] = []
    for node_id, props in overlay.items():
        expected = (str(props["visibility_rule_id"]), bool(props["visibility_hidden_initial"]))
        actual = baked.pop(node_id, None)
        if actual is None:
            mismatches.append(f"rule {expected[0]}: node {node_id} lost its visibility marker")
        elif actual != expected:
            mismatches.append(
                f"rule {expected[0]}: node {node_id} baked {actual} but initial_visibility requires {expected}"
            )
    for node_id, (rule_id, _hidden) in sorted(baked.items()):
        mismatches.append(f"rule {rule_id}: unexpected visibility marker on node {node_id}")
    return mismatches


__all__ = [
    "VISIBILITY_OVERLAY_KEYS",
    "check_screen_visibility_bake",
    "screen_visibility_overlays",
    "visibility_target_node_id",
]
