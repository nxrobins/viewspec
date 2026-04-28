"""Agent-facing helpers for producing valid ViewSpec intent bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from viewspec.compiler import CompilerInputError, UnsupportedMotifError, compile
from viewspec.types import IntentBundle, PRESENT_AS_TO_PRIMITIVE, parse_canonical_address

SUPPORTED_AGENT_MOTIFS = ("table", "dashboard", "outline", "comparison")

AGENT_SYSTEM_PROMPT = """You are a ViewSpec IntentBundle compiler.

Your job is to translate user intent into ViewSpec IntentBundle JSON. You do not output HTML, CSS, React, or CompositionIR. CompositionIR is compiler output only.

Output strict JSON only. Do not wrap it in markdown. Do not explain it.

The JSON object must contain:
- substrate.id
- substrate.root_id
- substrate.nodes as an object keyed by node ID, never an array
- every substrate node with id, kind, attrs, slots, and edges
- view_spec.id
- view_spec.substrate_id
- view_spec.complexity_tier
- view_spec.root_region
- view_spec.regions
- view_spec.bindings
- view_spec.groups
- view_spec.motifs
- view_spec.styles
- view_spec.actions

Use only these v1 motif kinds: table, dashboard, outline, comparison.

Use only these binding present_as values: text, label, value, badge, rich_text, image_slot, rule.

Use canonical binding addresses:
- node:{node_id}
- node:{node_id}#attr:{attr_name}
- node:{node_id}#slot:{slot_name}
- node:{node_id}#slot:{slot_name}[{index}]
- node:{node_id}#edge:{edge_name}

For JSON wire compatibility, slots and edges are maps whose values contain a values array:
{
  "slots": {"items": {"values": ["item_1", "item_2"]}},
  "edges": {"next": {"values": ["item_2"]}}
}

All binding IDs must be unique. Any binding with cardinality exactly_once must use an address that appears only once. Region, group, motif, style, and action references must resolve to declared IDs.
"""

AGENT_INTENT_BUNDLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/agent-intent-bundle.schema.json",
    "title": "ViewSpec Agent IntentBundle",
    "description": "Agent-authored ViewSpec compiler input. Agents output IntentBundle JSON, never CompositionIR.",
    "type": "object",
    "required": ["substrate", "view_spec"],
    "additionalProperties": False,
    "properties": {
        "substrate": {"$ref": "#/$defs/substrate"},
        "view_spec": {"$ref": "#/$defs/view_spec"},
    },
    "$defs": {
        "values": {
            "type": "object",
            "required": ["values"],
            "additionalProperties": False,
            "properties": {"values": {"type": "array"}},
        },
        "substrate_node": {
            "type": "object",
            "required": ["id", "kind", "attrs", "slots", "edges"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "minLength": 1},
                "attrs": {"type": "object"},
                "slots": {"type": "object", "additionalProperties": {"$ref": "#/$defs/values"}},
                "edges": {"type": "object", "additionalProperties": {"$ref": "#/$defs/values"}},
            },
        },
        "substrate": {
            "type": "object",
            "required": ["id", "root_id", "nodes"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "root_id": {"type": "string", "minLength": 1},
                "nodes": {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/$defs/substrate_node"},
                },
            },
        },
        "region": {
            "type": "object",
            "required": ["id", "parent_region", "role", "layout", "min_children", "max_children"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "parent_region": {"type": ["string", "null"]},
                "role": {"type": "string"},
                "layout": {"type": "string"},
                "min_children": {"type": "integer"},
                "max_children": {"type": ["integer", "null"]},
            },
        },
        "binding": {
            "type": "object",
            "required": ["id", "address", "target_region", "present_as", "cardinality"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "address": {"type": "string", "pattern": "^node:"},
                "target_region": {"type": "string", "minLength": 1},
                "present_as": {"enum": sorted(PRESENT_AS_TO_PRIMITIVE)},
                "cardinality": {"type": "string"},
            },
        },
        "group": {
            "type": "object",
            "required": ["id", "kind", "members", "target_region"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"type": "string"},
                "members": {"type": "array", "items": {"type": "string"}},
                "target_region": {"type": ["string", "null"]},
            },
        },
        "motif": {
            "type": "object",
            "required": ["id", "kind", "region", "members"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"enum": list(SUPPORTED_AGENT_MOTIFS)},
                "region": {"type": "string", "minLength": 1},
                "members": {"type": "array", "items": {"type": "string"}},
            },
        },
        "style": {
            "type": "object",
            "required": ["id", "target", "token"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
                "token": {"type": "string", "minLength": 1},
            },
        },
        "action": {
            "type": "object",
            "required": ["id", "kind", "label", "target_region", "target_ref", "payload_bindings"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "minLength": 1},
                "label": {"type": "string"},
                "target_region": {"type": "string", "minLength": 1},
                "target_ref": {"type": ["string", "null"]},
                "payload_bindings": {"type": "array", "items": {"type": "string"}},
            },
        },
        "view_spec": {
            "type": "object",
            "required": [
                "id",
                "substrate_id",
                "complexity_tier",
                "root_region",
                "regions",
                "bindings",
                "groups",
                "motifs",
                "styles",
                "actions",
            ],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "substrate_id": {"type": "string", "minLength": 1},
                "complexity_tier": {"type": "integer", "minimum": 1},
                "root_region": {"type": "string", "minLength": 1},
                "regions": {"type": "array", "items": {"$ref": "#/$defs/region"}},
                "bindings": {"type": "array", "items": {"$ref": "#/$defs/binding"}},
                "groups": {"type": "array", "items": {"$ref": "#/$defs/group"}},
                "motifs": {"type": "array", "items": {"$ref": "#/$defs/motif"}},
                "styles": {"type": "array", "items": {"$ref": "#/$defs/style"}},
                "actions": {"type": "array", "items": {"$ref": "#/$defs/action"}},
            },
        },
    },
}


@dataclass(frozen=True)
class AgentValidationIssue:
    severity: str
    code: str
    path: str
    message: str
    suggestion: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.suggestion:
            data["suggestion"] = self.suggestion
        return data


@dataclass(frozen=True)
class AgentValidationResult:
    valid: bool
    bundle: IntentBundle | None
    issues: list[AgentValidationIssue]

    def to_json(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [issue.to_json() for issue in self.issues],
        }


def validate_agent_intent_bundle(
    payload: str | dict[str, Any],
    *,
    require_reference_compiler_support: bool = True,
) -> AgentValidationResult:
    """Validate an agent-authored IntentBundle and optionally compile it locally."""
    data, issues = _coerce_payload(payload)
    if data is None:
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    issues.extend(_validate_intent_bundle_shape(data))
    if issues:
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    try:
        bundle = IntentBundle.from_json(data)
    except Exception as exc:
        issues.append(
            AgentValidationIssue(
                "error",
                "INTENT_BUNDLE_PARSE_ERROR",
                "$",
                f"IntentBundle.from_json rejected the payload: {exc}",
                "Regenerate the full IntentBundle using the published JSON schema.",
            )
        )
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    if require_reference_compiler_support:
        try:
            ast = compile(bundle)
        except UnsupportedMotifError as exc:
            issues.append(
                AgentValidationIssue(
                    "error",
                    "UNSUPPORTED_MOTIF",
                    "$.view_spec.motifs",
                    str(exc),
                    f"Use only these v1 motifs: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
            )
        except CompilerInputError as exc:
            issues.append(
                AgentValidationIssue(
                    "error",
                    "COMPILER_INPUT_ERROR",
                    "$",
                    str(exc),
                    "Fix missing or impossible root substrate/view_spec declarations.",
                )
            )
        else:
            for diagnostic in ast.result.diagnostics:
                issues.append(
                    AgentValidationIssue(
                        diagnostic.severity or "error",
                        diagnostic.code,
                        _diagnostic_path(diagnostic),
                        diagnostic.message,
                        "Regenerate the referenced field and preserve the rest of the IntentBundle.",
                    )
                )

    return AgentValidationResult(valid=not issues, bundle=bundle if not issues else None, issues=issues)


def agent_correction_prompt(result: AgentValidationResult) -> str:
    """Return a compact correction prompt for an agent repair loop."""
    issues = [issue.to_json() for issue in result.issues]
    return (
        "Regenerate the full ViewSpec IntentBundle JSON. Output strict JSON only. "
        "Fix these validation issues:\n"
        f"{json.dumps(issues, separators=(',', ':'), sort_keys=True)}"
    )


def _coerce_payload(payload: str | dict[str, Any]) -> tuple[dict[str, Any] | None, list[AgentValidationIssue]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            return None, [
                AgentValidationIssue(
                    "error",
                    "INVALID_JSON",
                    "$",
                    f"Payload is not valid JSON: {exc.msg}",
                    "Return one strict JSON object with substrate and view_spec.",
                )
            ]
    if not isinstance(payload, dict):
        return None, [
            AgentValidationIssue(
                "error",
                "INVALID_PAYLOAD",
                "$",
                "Agent output must be a JSON object.",
                "Return one IntentBundle object, not prose, markdown, or an array.",
            )
        ]
    return payload, []


def _validate_intent_bundle_shape(data: dict[str, Any]) -> list[AgentValidationIssue]:
    issues: list[AgentValidationIssue] = []
    if "primitive" in data or "children" in data:
        issues.append(
            _issue(
                "COMPOSITION_IR_INPUT",
                "$",
                "Agent output looks like CompositionIR. Agents must output IntentBundle JSON.",
                "Move semantic data into substrate and declarative intent into view_spec.",
            )
        )

    substrate = _required_object(data, "substrate", "$", issues)
    view_spec = _required_object(data, "view_spec", "$", issues)
    if substrate is None or view_spec is None:
        return issues

    if isinstance(substrate.get("nodes"), list):
        issues.append(
            _issue(
                "NODES_MUST_BE_OBJECT",
                "$.substrate.nodes",
                "substrate.nodes must be an object keyed by node ID, not an array.",
                "Use {\"node_id\": {\"id\": \"node_id\", ...}}.",
            )
        )
        nodes = None
    else:
        nodes = _required_object(substrate, "nodes", "$.substrate", issues)

    root_id = _required_string(substrate, "root_id", "$.substrate", issues)
    substrate_id = _required_string(substrate, "id", "$.substrate", issues)
    view_substrate_id = _required_string(view_spec, "substrate_id", "$.view_spec", issues)
    view_id = _required_string(view_spec, "id", "$.view_spec", issues)
    root_region = _required_string(view_spec, "root_region", "$.view_spec", issues)

    if substrate_id and view_substrate_id and substrate_id != view_substrate_id:
        issues.append(
            _issue(
                "SUBSTRATE_ID_MISMATCH",
                "$.view_spec.substrate_id",
                f"view_spec.substrate_id '{view_substrate_id}' does not match substrate.id '{substrate_id}'.",
                "Use the same substrate id in both locations.",
            )
        )

    if nodes is not None:
        _validate_nodes(nodes, root_id, issues)

    regions = _required_array(view_spec, "regions", "$.view_spec", issues)
    bindings = _required_array(view_spec, "bindings", "$.view_spec", issues)
    groups = _required_array(view_spec, "groups", "$.view_spec", issues)
    motifs = _required_array(view_spec, "motifs", "$.view_spec", issues)
    styles = _required_array(view_spec, "styles", "$.view_spec", issues)
    actions = _required_array(view_spec, "actions", "$.view_spec", issues)
    _required_int(view_spec, "complexity_tier", "$.view_spec", issues)

    region_ids = _collect_ids(regions, "$.view_spec.regions", "DUPLICATE_REGION_ID", issues)
    binding_ids = _collect_ids(bindings, "$.view_spec.bindings", "DUPLICATE_BINDING_ID", issues)
    group_ids = _collect_ids(groups, "$.view_spec.groups", "DUPLICATE_GROUP_ID", issues)
    motif_ids = _collect_ids(motifs, "$.view_spec.motifs", "DUPLICATE_MOTIF_ID", issues)
    style_ids = _collect_ids(styles, "$.view_spec.styles", "DUPLICATE_STYLE_ID", issues)
    action_ids = _collect_ids(actions, "$.view_spec.actions", "DUPLICATE_ACTION_ID", issues)

    if root_region and root_region not in region_ids:
        issues.append(
            _issue(
                "MISSING_ROOT_REGION",
                "$.view_spec.root_region",
                f"view_spec.root_region '{root_region}' is not declared in view_spec.regions.",
                "Add a matching root region object.",
            )
        )

    _validate_regions(regions, region_ids, issues)
    _validate_bindings(bindings, binding_ids, region_ids, nodes, issues)
    _validate_groups(groups, binding_ids, region_ids, issues)
    _validate_motifs(motifs, binding_ids, region_ids, issues)
    _validate_styles(styles, view_id, region_ids, binding_ids, motif_ids, issues)
    _validate_actions(actions, view_id, region_ids, binding_ids, motif_ids, issues)

    # Touch these sets so duplicate collection stays explicit for future additions.
    _ = group_ids, style_ids, action_ids
    return issues


def _validate_nodes(nodes: dict[str, Any], root_id: str | None, issues: list[AgentValidationIssue]) -> None:
    if root_id and root_id not in nodes:
        issues.append(
            _issue(
                "MISSING_SUBSTRATE_ROOT",
                "$.substrate.root_id",
                f"substrate.root_id '{root_id}' is not present in substrate.nodes.",
                "Add a substrate node with that key or change root_id.",
            )
        )
    for node_key, node in nodes.items():
        path = f"$.substrate.nodes.{node_key}"
        if not isinstance(node, dict):
            issues.append(_issue("INVALID_NODE", path, "Each substrate node must be an object."))
            continue
        node_id = _required_string(node, "id", path, issues)
        _required_string(node, "kind", path, issues)
        attrs = _required_object(node, "attrs", path, issues)
        slots = _required_object(node, "slots", path, issues)
        edges = _required_object(node, "edges", path, issues)
        if node_id and node_id != node_key:
            issues.append(
                _issue(
                    "NODE_KEY_MISMATCH",
                    f"{path}.id",
                    f"Node key '{node_key}' must match node.id '{node_id}'.",
                    "Use the node id as the substrate.nodes object key.",
                )
            )
        if attrs is not None:
            for attr_key in attrs:
                if not isinstance(attr_key, str) or not attr_key:
                    issues.append(_issue("INVALID_ATTR_KEY", f"{path}.attrs", "Attribute keys must be non-empty strings."))
        _validate_values_map(slots, f"{path}.slots", "SLOT_VALUES_SHAPE", issues)
        _validate_values_map(edges, f"{path}.edges", "EDGE_VALUES_SHAPE", issues)


def _validate_values_map(
    value_map: dict[str, Any] | None,
    path: str,
    code: str,
    issues: list[AgentValidationIssue],
) -> None:
    if value_map is None:
        return
    for key, value in value_map.items():
        item_path = f"{path}.{key}"
        if not isinstance(value, dict) or not isinstance(value.get("values"), list):
            issues.append(
                _issue(
                    code,
                    item_path,
                    "Slot and edge values must use protobuf JSON shape {\"values\": [...]}.",
                    "Wrap the array in an object with a values key.",
                )
            )


def _validate_regions(regions: list[Any], region_ids: set[str], issues: list[AgentValidationIssue]) -> None:
    for index, region in enumerate(regions):
        path = f"$.view_spec.regions[{index}]"
        if not isinstance(region, dict):
            issues.append(_issue("INVALID_REGION", path, "Each region must be an object."))
            continue
        parent = region.get("parent_region")
        if parent not in (None, "") and parent not in region_ids:
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.parent_region",
                    f"Region {region.get('id')} declares unknown parent region {parent}.",
                    "Use a declared region id or null for the root region.",
                )
            )
        _required_string(region, "role", path, issues)
        _required_string(region, "layout", path, issues)
        _required_int(region, "min_children", path, issues)
        if "max_children" not in region:
            issues.append(_issue("MISSING_FIELD", f"{path}.max_children", "Missing required field max_children."))


def _validate_bindings(
    bindings: list[Any],
    binding_ids: set[str],
    region_ids: set[str],
    nodes: dict[str, Any] | None,
    issues: list[AgentValidationIssue],
) -> None:
    seen_exactly_once: dict[str, str] = {}
    for index, binding in enumerate(bindings):
        path = f"$.view_spec.bindings[{index}]"
        if not isinstance(binding, dict):
            issues.append(_issue("INVALID_BINDING", path, "Each binding must be an object."))
            continue
        binding_id = _required_string(binding, "id", path, issues)
        address = _required_string(binding, "address", path, issues)
        target_region = _required_string(binding, "target_region", path, issues)
        present_as = _required_string(binding, "present_as", path, issues)
        cardinality = _required_string(binding, "cardinality", path, issues)
        if target_region and target_region not in region_ids:
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.target_region",
                    f"Binding {binding_id} targets unknown region {target_region}.",
                    "Target a declared region id.",
                )
            )
        if present_as and present_as not in PRESENT_AS_TO_PRIMITIVE:
            issues.append(
                _issue(
                    "UNKNOWN_PRESENT_AS",
                    f"{path}.present_as",
                    f"Binding {binding_id} uses unknown present_as {present_as}.",
                    f"Use one of: {', '.join(sorted(PRESENT_AS_TO_PRIMITIVE))}.",
                )
            )
        if address and nodes is not None:
            _validate_address(address, nodes, f"{path}.address", binding_id or "binding", issues)
            if cardinality == "exactly_once":
                if address in seen_exactly_once:
                    issues.append(
                        _issue(
                            "DUPLICATE_EXACTLY_ONCE_ADDRESS",
                            f"{path}.address",
                            f"Binding {binding_id} duplicates exactly_once address {address} already used by {seen_exactly_once[address]}.",
                            "Use each exactly_once address once, or change cardinality where repeat use is intentional.",
                        )
                    )
                else:
                    seen_exactly_once[address] = binding_id or address
    _ = binding_ids


def _validate_address(
    address: str,
    nodes: dict[str, Any],
    path: str,
    binding_id: str,
    issues: list[AgentValidationIssue],
) -> None:
    try:
        parts = parse_canonical_address(address)
    except ValueError as exc:
        issues.append(
            _issue(
                "INVALID_ADDRESS",
                path,
                f"Binding {binding_id} has invalid address {address}: {exc}",
                "Use node:id, node:id#attr:name, node:id#slot:name[index], or node:id#edge:name.",
            )
        )
        return

    node_id = parts["node_id"]
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        issues.append(
            _issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing node {node_id}.")
        )
        return
    attr = parts["attr"]
    slot = parts["slot"]
    edge = parts["edge"]
    if attr and attr not in (node.get("attrs") or {}):
        issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing attr {attr}."))
    if slot:
        slot_entry = (node.get("slots") or {}).get(slot)
        values = slot_entry.get("values") if isinstance(slot_entry, dict) else None
        if not isinstance(values, list):
            issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing slot {slot}."))
        elif parts["slot_index"] is not None and parts["slot_index"] >= len(values):
            issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing slot index {parts['slot_index']}."))
    if edge and edge not in (node.get("edges") or {}):
        issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing edge {edge}."))


def _validate_groups(
    groups: list[Any],
    binding_ids: set[str],
    region_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, group in enumerate(groups):
        path = f"$.view_spec.groups[{index}]"
        if not isinstance(group, dict):
            issues.append(_issue("INVALID_GROUP", path, "Each group must be an object."))
            continue
        _required_string(group, "kind", path, issues)
        members = _required_array(group, "members", path, issues)
        if "target_region" not in group:
            issues.append(_issue("MISSING_FIELD", f"{path}.target_region", "Missing required field target_region."))
        elif group["target_region"] not in (None, "") and group["target_region"] not in region_ids:
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.target_region",
                    f"Group {group.get('id')} targets unknown region {group['target_region']}.",
                    "Target a declared region id or use null.",
                )
            )
        for member in members:
            if member not in binding_ids:
                issues.append(
                    _issue(
                        "MISSING_GROUP_MEMBER",
                        f"{path}.members",
                        f"Group {group.get('id')} references missing binding {member}.",
                        "Use declared binding ids in group members.",
                    )
                )


def _validate_motifs(
    motifs: list[Any],
    binding_ids: set[str],
    region_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, motif in enumerate(motifs):
        path = f"$.view_spec.motifs[{index}]"
        if not isinstance(motif, dict):
            issues.append(_issue("INVALID_MOTIF", path, "Each motif must be an object."))
            continue
        kind = _required_string(motif, "kind", path, issues)
        region = _required_string(motif, "region", path, issues)
        members = _required_array(motif, "members", path, issues)
        if kind and kind not in SUPPORTED_AGENT_MOTIFS:
            issues.append(
                _issue(
                    "UNSUPPORTED_MOTIF",
                    f"{path}.kind",
                    f"Motif kind '{kind}' is not supported by the v1 agent contract.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
            )
        if region and region not in region_ids:
            issues.append(_issue("UNKNOWN_REGION", f"{path}.region", f"Motif {motif.get('id')} targets unknown region {region}."))
        for member in members:
            if member not in binding_ids:
                issues.append(
                    _issue(
                        "MISSING_MOTIF_MEMBER",
                        f"{path}.members",
                        f"Motif {motif.get('id')} references missing binding {member}.",
                        "Use declared binding ids in motif members.",
                    )
                )


def _validate_styles(
    styles: list[Any],
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, style in enumerate(styles):
        path = f"$.view_spec.styles[{index}]"
        if not isinstance(style, dict):
            issues.append(_issue("INVALID_STYLE", path, "Each style must be an object."))
            continue
        target = _required_string(style, "target", path, issues)
        _required_string(style, "token", path, issues)
        if target and not _target_exists(target, view_id, region_ids, binding_ids, motif_ids):
            issues.append(
                _issue(
                    "UNKNOWN_STYLE_TARGET",
                    f"{path}.target",
                    f"Style {style.get('id')} targets unknown {target}.",
                    "Use region:id, binding:id, motif:id, view:id, or a declared bare id.",
                )
            )


def _validate_actions(
    actions: list[Any],
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, action in enumerate(actions):
        path = f"$.view_spec.actions[{index}]"
        if not isinstance(action, dict):
            issues.append(_issue("INVALID_ACTION", path, "Each action must be an object."))
            continue
        target_region = _required_string(action, "target_region", path, issues)
        _required_string(action, "kind", path, issues)
        _required_string(action, "label", path, issues)
        target_ref = action.get("target_ref")
        payload_bindings = _required_array(action, "payload_bindings", path, issues)
        if target_region and target_region not in region_ids:
            issues.append(
                _issue("UNKNOWN_ACTION_TARGET", f"{path}.target_region", f"Action {action.get('id')} targets unknown region {target_region}.")
            )
        if target_ref not in (None, "") and (not isinstance(target_ref, str) or not _target_exists(target_ref, view_id, region_ids, binding_ids, motif_ids)):
            issues.append(
                _issue(
                    "UNKNOWN_ACTION_TARGET",
                    f"{path}.target_ref",
                    f"Action {action.get('id')} targets unknown {target_ref}.",
                    "Use a declared target ref like binding:save_button or motif:items.",
                )
            )
        for binding_id in payload_bindings:
            if binding_id not in binding_ids:
                issues.append(
                    _issue(
                        "UNKNOWN_ACTION_PAYLOAD_BINDING",
                        f"{path}.payload_bindings",
                        f"Action {action.get('id')} references missing payload binding {binding_id}.",
                    )
                )


def _target_exists(
    target: str,
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
) -> bool:
    if ":" in target:
        kind, target_id = target.split(":", 1)
    else:
        target_id = target
        return target_id in region_ids or target_id in binding_ids or target_id in motif_ids or target_id == view_id
    return (
        (kind == "region" and target_id in region_ids)
        or (kind == "binding" and target_id in binding_ids)
        or (kind == "motif" and target_id in motif_ids)
        or (kind == "view" and target_id == view_id)
    )


def _collect_ids(
    values: list[Any],
    path: str,
    duplicate_code: str,
    issues: list[AgentValidationIssue],
) -> set[str]:
    ids: set[str] = set()
    for index, value in enumerate(values):
        item_path = f"{path}[{index}]"
        if not isinstance(value, dict):
            continue
        item_id = _required_string(value, "id", item_path, issues)
        if item_id:
            if item_id in ids:
                issues.append(_issue(duplicate_code, f"{item_path}.id", f"Duplicate id {item_id}."))
            ids.add(item_id)
    return ids


def _required_object(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> dict[str, Any] | None:
    value = obj.get(key)
    if not isinstance(value, dict):
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required object field {key}."))
        return None
    return value


def _required_array(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required array field {key}."))
        return []
    return value


def _required_string(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or value == "":
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required string field {key}."))
        return None
    return value


def _required_int(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> int | None:
    value = obj.get(key)
    if not isinstance(value, int):
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required integer field {key}."))
        return None
    return value


def _diagnostic_path(diagnostic: Any) -> str:
    if diagnostic.intent_ref:
        return f"intent_ref:{diagnostic.intent_ref}"
    if diagnostic.content_ref:
        return f"content_ref:{diagnostic.content_ref}"
    if diagnostic.region_id:
        return f"region:{diagnostic.region_id}"
    if diagnostic.node_id:
        return f"node:{diagnostic.node_id}"
    return "$"


def _issue(
    code: str,
    path: str,
    message: str,
    suggestion: str | None = None,
) -> AgentValidationIssue:
    return AgentValidationIssue("error", code, path, message, suggestion)
