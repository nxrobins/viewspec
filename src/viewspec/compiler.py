"""
ViewSpec Reference Compiler — local compilation for standard motif types.

Handles: table, dashboard, outline, comparison, list, form, detail, empty_state, hero.
For complex or novel layouts, use the hosted compiler at api.viewspec.dev.

This is a deterministic, offline compiler that produces correct CompositionIR
with full provenance for every motif type the SDK builders generate.
"""

from __future__ import annotations

import json
import re
from typing import Any

from viewspec.types import (
    ActionIntent,
    ASTBundle,
    BindingSpec,
    CompileRequestPayload,
    CompileResponse,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    DEFAULT_STYLE_TOKEN_VALUES,
    DesignRequest,
    GroupSpec,
    IntentBundle,
    IRNode,
    MotifSpec,
    Provenance,
    RegionSpec,
    SemanticNode,
    SemanticSubstrate,
    StyleSpec,
    ViewSpec,
)
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system, merge_style_values

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRESENT_AS_TO_PRIMITIVE: dict[str, str] = {
    "text": "text",
    "label": "label",
    "value": "value",
    "badge": "badge",
    "input": "input",
    "rich_text": "text",
    "image_slot": "image_slot",
    "rule": "rule",
}

SUPPORTED_ACTION_KINDS = ("select", "submit", "navigate")
SUPPORTED_BINDING_CARDINALITIES = {"exactly_once"}
SUPPORTED_GROUP_KINDS = {"ordered"}
SUPPORTED_REGION_LAYOUTS = {"cluster", "grid", "stack"}

CANONICAL_ADDRESS_RE = re.compile(
    r"^node:(?P<node_id>[A-Za-z0-9_.-]+)"
    r"(?:#attr:(?P<attr>[A-Za-z0-9_.-]+))?"
    r"(?:#slot:(?P<slot>[A-Za-z0-9_.-]+)(?:\[(?P<slot_index>\d+)\])?)?"
    r"(?:#edge:(?P<edge>[A-Za-z0-9_.-]+))?$"
)
SAFE_COMPILER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

PRODUCT_SURFACE_PLANNER_V1_SURFACE = "workspace_dashboard_v1"
PRODUCT_SURFACE_PLANNER_V1_ROLES = frozenset(
    {
        "app_shell",
        "app_header",
        "content_grid",
        "primary_column",
        "side_rail",
        "page_header",
        "metric_grid",
        "metric_card",
        "form_panel",
        "field_group",
        "detail_panel",
        "action_row",
    }
)
WORKSPACE_HEADER_ROLE_MARKERS = frozenset({"app_header", "banner", "header", "page_header"})
WORKSPACE_CONTENT_GRID_ROLE_MARKERS = frozenset({"application", "body", "content", "main", "workspace"})
WORKSPACE_PRIMARY_ROLE_MARKERS = frozenset({"main", "primary", "primary_column", "workspace_primary"})
WORKSPACE_SIDE_RAIL_ROLE_MARKERS = frozenset(
    {"aside", "complementary", "detail_rail", "rail", "side_rail", "sidebar"}
)


# ---------------------------------------------------------------------------
# Address resolution
# ---------------------------------------------------------------------------


def _parse_canonical_address(address: str) -> dict[str, Any]:
    match = CANONICAL_ADDRESS_RE.match(address)
    if not match:
        raise ValueError(f"Invalid canonical address: {address}")
    parts = match.groupdict()
    return {
        "node_id": parts["node_id"],
        "attr": parts["attr"],
        "slot": parts["slot"],
        "slot_index": int(parts["slot_index"]) if parts["slot_index"] is not None else None,
        "edge": parts["edge"],
    }


def _build_address_index(substrate: SemanticSubstrate) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for node in substrate.nodes.values():
        index[f"node:{node.id}"] = {"kind": node.kind, "id": node.id}
        for attr_name, attr_value in node.attrs.items():
            index[f"node:{node.id}#attr:{attr_name}"] = attr_value
        for slot_name, values in node.slots.items():
            index[f"node:{node.id}#slot:{slot_name}"] = list(values)
            for idx, value in enumerate(values):
                index[f"node:{node.id}#slot:{slot_name}[{idx}]"] = value
        for edge_name, target_ids in node.edges.items():
            index[f"node:{node.id}#edge:{edge_name}"] = list(target_ids)
    return index


def _resolve_address(address: str, address_index: dict[str, Any]) -> Any:
    if address not in address_index:
        raise KeyError(f"Address not found: {address}")
    return address_index[address]


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


# ---------------------------------------------------------------------------
# Provenance ref helpers
# ---------------------------------------------------------------------------


def _region_ref(region_id: str) -> str:
    return f"viewspec:region:{region_id}"


def _binding_ref(binding_id: str) -> str:
    return f"viewspec:binding:{binding_id}"


def _motif_ref(motif_id: str) -> str:
    return f"viewspec:motif:{motif_id}"


def _style_ref(style_id: str) -> str:
    return f"viewspec:style:{style_id}"


def _action_ref(action_id: str) -> str:
    return f"viewspec:action:{action_id}"


def _view_ref(view_id: str) -> str:
    return f"viewspec:view:{view_id}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _layout_primitive(layout: str) -> str:
    if layout in {"stack", "grid", "cluster"}:
        return layout
    return "stack"


def _planner_columns_v0(rendered_child_count: int) -> int:
    if rendered_child_count <= 1:
        return 1
    if rendered_child_count <= 3:
        return 2
    return 3


def _ordered_binding_positions(view_spec: ViewSpec) -> dict[str, int]:
    positions: dict[str, int] = {}
    cursor = 0
    for group in view_spec.groups:
        if group.kind != "ordered":
            continue
        for member_id in group.members:
            if member_id not in positions:
                positions[member_id] = cursor
                cursor += 1
    for binding in view_spec.bindings:
        if binding.id not in positions:
            positions[binding.id] = cursor
            cursor += 1
    return positions


def _binding_node_id(binding: BindingSpec) -> str:
    return str(_parse_canonical_address(binding.address)["node_id"])


def _bindings_by_semantic_node(
    bindings: list[BindingSpec], ordered_positions: dict[str, int]
) -> list[tuple[str, list[BindingSpec]]]:
    grouped: dict[str, list[BindingSpec]] = {}
    for binding in sorted(bindings, key=lambda b: ordered_positions[b.id]):
        grouped.setdefault(_binding_node_id(binding), []).append(binding)
    return list(grouped.items())


def _semantic_children(substrate: SemanticSubstrate, node_id: str) -> list[str]:
    node = substrate.nodes.get(node_id)
    if node is None:
        return []
    children: list[str] = []
    for child_ids in node.slots.values():
        for child_id in child_ids:
            if child_id in substrate.nodes and child_id not in children:
                children.append(child_id)
    for child_ids in node.edges.values():
        for child_id in child_ids:
            if child_id in substrate.nodes and child_id not in children:
                children.append(child_id)
    return children


def _semantic_parent_by_id(substrate: SemanticSubstrate) -> dict[str, str]:
    parents: dict[str, str] = {}
    for node in substrate.nodes.values():
        for child_ids in node.slots.values():
            for child_id in child_ids:
                if child_id in substrate.nodes and child_id not in parents:
                    parents[child_id] = node.id
        for child_ids in node.edges.values():
            for child_id in child_ids:
                if child_id in substrate.nodes and child_id not in parents:
                    parents[child_id] = node.id
    return parents


def _input_label(binding: BindingSpec, address_index: dict[str, object]) -> str:
    try:
        parts = _parse_canonical_address(binding.address)
    except ValueError:
        return binding.id
    label_value = address_index.get(f"node:{parts['node_id']}#attr:label")
    if label_value is None:
        return binding.id
    label = _text_from_value(label_value).strip()
    return label or binding.id


def _binding_text(binding: BindingSpec, resolved_value: object, address_index: dict[str, object]) -> dict[str, object]:
    primitive = PRESENT_AS_TO_PRIMITIVE.get(binding.present_as, binding.present_as)
    if primitive == "image_slot":
        return {"alt": _text_from_value(resolved_value)}
    if primitive == "svg":
        return {"label": _text_from_value(resolved_value)}
    if primitive == "input":
        return {
            "value": _text_from_value(resolved_value),
            "input_type": "text",
            "binding_id": binding.id,
            "aria_label": _input_label(binding, address_index),
        }
    if primitive == "rule":
        return {}
    return {"text": _text_from_value(resolved_value), "binding_id": binding.id}


def _build_binding_node(binding: BindingSpec, address_index: dict[str, object]) -> IRNode:
    resolved_value = _resolve_address(binding.address, address_index)
    return IRNode(
        id=f"binding_{binding.id}",
        primitive=PRESENT_AS_TO_PRIMITIVE.get(binding.present_as, binding.present_as),
        props=_binding_text(binding, resolved_value, address_index),
        provenance=Provenance(
            content_refs=[binding.address],
            intent_refs=[_binding_ref(binding.id)],
        ),
    )


def _build_action_node(action: ActionIntent, bindings_by_id: dict[str, BindingSpec]) -> IRNode:
    content_refs = [bindings_by_id[binding_id].address for binding_id in action.payload_bindings if binding_id in bindings_by_id]
    props: dict[str, object] = {
        "text": action.label,
        "action_id": action.id,
        "action_kind": action.kind,
        "payload_bindings": list(action.payload_bindings),
    }
    if action.target_ref:
        props["target_ref"] = action.target_ref
    return IRNode(
        id=f"action_{action.id}",
        primitive="button",
        props=props,
        provenance=Provenance(
            content_refs=content_refs,
            intent_refs=[_action_ref(action.id)],
        ),
    )


def _bare_style_target_matches(
    target_id: str,
    *,
    view_spec: ViewSpec,
    region_nodes: dict[str, IRNode],
    binding_nodes: dict[str, IRNode],
    motif_by_id: dict[str, MotifSpec],
) -> list[str]:
    matches: list[str] = []
    if target_id in binding_nodes:
        matches.append("binding")
    if target_id in region_nodes:
        matches.append("region")
    if target_id in motif_by_id:
        matches.append("motif")
    if target_id == view_spec.id:
        matches.append("view")
    return matches


def _attach_style(node: IRNode, style: StyleSpec) -> None:
    if style.token not in node.style_tokens:
        node.style_tokens.append(style.token)
    ref = _style_ref(style.id)
    if ref not in node.provenance.intent_refs:
        node.provenance.intent_refs.append(ref)


def _role_markers(role: str) -> set[str]:
    normalized = role.strip().lower().replace("-", "_")
    markers = {normalized} if normalized else set()
    markers.update(part for part in re.split(r"[^a-z0-9]+", normalized) if part)
    return markers


def _region_role_matches(region: RegionSpec, markers: frozenset[str]) -> bool:
    return bool(_role_markers(region.role) & markers)


def _detect_workspace_surface_shape_v1(
    *,
    valid_regions: list[RegionSpec],
    root_region: str,
) -> dict[str, str | None] | None:
    header_candidates = [
        region
        for region in valid_regions
        if region.id != root_region and _region_role_matches(region, WORKSPACE_HEADER_ROLE_MARKERS)
    ]
    body_candidates = [
        region
        for region in valid_regions
        if region.id != root_region
        and region.layout == "grid"
        and _region_role_matches(region, WORKSPACE_CONTENT_GRID_ROLE_MARKERS)
    ]
    if len(header_candidates) != 1 or len(body_candidates) != 1:
        return None

    header = header_candidates[0]
    body = body_candidates[0]
    if header.id == body.id or header.parent_region != root_region or body.parent_region != root_region:
        return None

    body_children = [region for region in valid_regions if region.parent_region == body.id]
    primary_candidates = [
        region for region in body_children if _region_role_matches(region, WORKSPACE_PRIMARY_ROLE_MARKERS)
    ]
    side_rail_candidates = [
        region for region in body_children if _region_role_matches(region, WORKSPACE_SIDE_RAIL_ROLE_MARKERS)
    ]
    if len(primary_candidates) != 1 or len(side_rail_candidates) > 1:
        return None

    classified_body_children = {primary_candidates[0].id, *(region.id for region in side_rail_candidates)}
    if any(region.id not in classified_body_children for region in body_children):
        return None

    return {
        "header": header.id,
        "body": body.id,
        "primary": primary_candidates[0].id,
        "side_rail": side_rail_candidates[0].id if side_rail_candidates else None,
    }


def _assign_product_role_v1(node: IRNode, role: str) -> None:
    if role not in PRODUCT_SURFACE_PLANNER_V1_ROLES:
        raise RuntimeError(f"PLANNER_ROLE_METADATA_ONLY: unsupported product role {role!r}.")
    node.props["product_role"] = role


def _apply_workspace_surface_roles_v1(
    *,
    shape: dict[str, str | None],
    region_nodes: dict[str, IRNode],
    motif_wrappers: dict[str, IRNode],
    motif_by_id: dict[str, MotifSpec],
    root_region: str,
) -> None:
    root_node = region_nodes[root_region]
    _assign_product_role_v1(root_node, "app_shell")
    root_node.props["planner_surface"] = PRODUCT_SURFACE_PLANNER_V1_SURFACE
    _assign_product_role_v1(region_nodes[shape["header"] or ""], "app_header")
    _assign_product_role_v1(region_nodes[shape["body"] or ""], "content_grid")
    _assign_product_role_v1(region_nodes[shape["primary"] or ""], "primary_column")
    side_rail = shape["side_rail"]
    if side_rail is not None:
        _assign_product_role_v1(region_nodes[side_rail], "side_rail")

    for motif_id in sorted(motif_wrappers):
        motif = motif_by_id[motif_id]
        wrapper = motif_wrappers[motif_id]
        if motif.kind == "hero" and motif.region == shape["header"]:
            _assign_product_role_v1(wrapper, "page_header")
        elif motif.kind == "dashboard" and motif.region == shape["primary"]:
            _assign_product_role_v1(wrapper, "metric_grid")
            for child in wrapper.children:
                if child.primitive == "surface":
                    _assign_product_role_v1(child, "metric_card")
        elif motif.kind == "form" and motif.region == shape["primary"]:
            _assign_product_role_v1(wrapper, "form_panel")
            for child in wrapper.children:
                if child.primitive == "surface":
                    _assign_product_role_v1(child, "field_group")
        elif side_rail is not None and motif.kind == "detail" and motif.region == side_rail:
            _assign_product_role_v1(wrapper, "detail_panel")


SAFE_MOTIF_LOCAL_ACTION_KINDS = {"dashboard", "empty_state", "form", "hero", "list"}
SAFE_MOTIF_LOCAL_ACTION_ROOT_TAGS = {"div", "header", "section", "ul"}


def _motif_wrapper_root_tag_v0(node: IRNode) -> str:
    motif_kind = node.props.get("motif_kind")
    if motif_kind == "table" and node.primitive == "stack":
        return "table"
    if motif_kind == "detail" and node.primitive == "stack":
        return "dl"
    if motif_kind == "list" and node.primitive == "stack":
        return "ul"
    if motif_kind == "form" and node.primitive == "stack":
        return "section"
    if motif_kind == "empty_state" and node.primitive == "surface":
        return "section"
    if motif_kind == "hero" and node.primitive == "surface":
        return "header"
    return "div"


def _motif_wrapper_allows_local_action_v0(node: IRNode) -> bool:
    motif_kind = node.props.get("motif_kind")
    root_tag = _motif_wrapper_root_tag_v0(node)
    return motif_kind in SAFE_MOTIF_LOCAL_ACTION_KINDS and root_tag in SAFE_MOTIF_LOCAL_ACTION_ROOT_TAGS


def _action_target_motif_id(action: ActionIntent) -> str | None:
    if not action.target_ref or ":" not in action.target_ref:
        return None
    target_kind, target_id = action.target_ref.split(":", 1)
    if target_kind != "motif":
        return None
    return target_id


def _validate_unique_action_placement_v0(root: IRNode, diagnostics: list[CompilerDiagnostic]) -> None:
    action_locations: dict[str, str] = {}

    def walk(node: IRNode) -> None:
        action_id = node.props.get("action_id")
        if isinstance(action_id, str) and action_id:
            previous = action_locations.setdefault(action_id, node.id)
            if previous != node.id:
                _add_diagnostic(
                    diagnostics,
                    "DUPLICATE_ACTION_PLACEMENT",
                    f"Action {action_id} was placed more than once in the compiled IR.",
                    intent_ref=_action_ref(action_id),
                )
        for child in node.children:
            walk(child)

    walk(root)


def _action_row_for_motif_v1(wrapper: IRNode, motif_id: str) -> IRNode:
    row_id = f"planner_{motif_id}_actions"
    for child in wrapper.children:
        if child.id == row_id:
            return child
    row = IRNode(
        id=row_id,
        primitive="cluster",
        props={"layout_role": "cluster", "layout_strategy": "action_row_v1"},
        provenance=Provenance(intent_refs=list(wrapper.provenance.intent_refs)),
    )
    _assign_product_role_v1(row, "action_row")
    wrapper.children.append(row)
    return row


def _apply_product_surface_planner_v1(
    *,
    view_spec: ViewSpec,
    valid_regions: list[RegionSpec],
    region_nodes: dict[str, IRNode],
    motif_wrappers: dict[str, IRNode],
    motif_by_id: dict[str, MotifSpec],
    valid_actions: list[ActionIntent],
    bindings_by_id: dict[str, BindingSpec],
    diagnostics: list[CompilerDiagnostic],
    root_region: str,
) -> None:
    workspace_shape = _detect_workspace_surface_shape_v1(valid_regions=valid_regions, root_region=root_region)

    for region_id in sorted(region_nodes):
        node = region_nodes[region_id]
        if node.primitive != "grid":
            continue
        node.props["columns"] = _planner_columns_v0(len(node.children))
        node.props["layout_strategy"] = "region_grid_v0"

    for motif_id in sorted(motif_wrappers):
        wrapper = motif_wrappers[motif_id]
        if wrapper.props.get("motif_kind") != "dashboard":
            continue
        if len(wrapper.children) < 2:
            continue
        wrapper.primitive = "grid"
        wrapper.props["layout_role"] = "grid"
        wrapper.props["columns"] = _planner_columns_v0(len(wrapper.children))
        wrapper.props["layout_strategy"] = "dashboard_grid_v0"

    if workspace_shape is not None:
        _apply_workspace_surface_roles_v1(
            shape=workspace_shape,
            region_nodes=region_nodes,
            motif_wrappers=motif_wrappers,
            motif_by_id=motif_by_id,
            root_region=view_spec.root_region,
        )

    for action in valid_actions:
        action_node = _build_action_node(action, bindings_by_id)
        target_motif_id = _action_target_motif_id(action)
        if target_motif_id is None:
            region_nodes[action.target_region].children.append(action_node)
            continue
        if target_motif_id in motif_by_id and target_motif_id not in motif_wrappers:
            _add_diagnostic(
                diagnostics,
                "MISSING_ACTION_MOTIF_WRAPPER",
                f"Action {action.id} targets declared motif {target_motif_id}, but no compiled motif wrapper exists.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            continue
        wrapper = motif_wrappers.get(target_motif_id)
        if wrapper is None:
            region_nodes[action.target_region].children.append(action_node)
            continue
        if _motif_wrapper_allows_local_action_v0(wrapper):
            action_node.props["placement"] = "motif_local"
            if workspace_shape is not None:
                _action_row_for_motif_v1(wrapper, target_motif_id).children.append(action_node)
            else:
                wrapper.children.append(action_node)
            continue
        _add_diagnostic(
            diagnostics,
            "UNSAFE_ACTION_PLACEMENT",
            f"Action {action.id} targets motif {target_motif_id}, whose compiled wrapper is not a safe local action container.",
            intent_ref=_action_ref(action.id),
            region_id=action.target_region,
        )
        region_nodes[action.target_region].children.append(action_node)

    _validate_unique_action_placement_v0(region_nodes[root_region], diagnostics)


def _add_diagnostic(
    diagnostics: list[CompilerDiagnostic],
    code: str,
    message: str,
    *,
    intent_ref: str | None = None,
    content_ref: str | None = None,
    region_id: str | None = None,
) -> CompilerDiagnostic:
    d = CompilerDiagnostic(
        severity="error",
        code=code,
        message=message,
        intent_ref=intent_ref or "",
        content_ref=content_ref or "",
        region_id=region_id or "",
    )
    diagnostics.append(d)
    return d


# ---------------------------------------------------------------------------
# Motif builders
# ---------------------------------------------------------------------------


def _build_table_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        row_header_assigned = False
        row = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="cluster",
            props={"layout_role": "cluster", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            node = binding_nodes[binding.id]
            if node.primitive == "label" and not row_header_assigned:
                node.props["table_cell_role"] = "row_header"
                row_header_assigned = True
            else:
                node.props["table_cell_role"] = "cell"
            row.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(row)
    return wrapper, placed


def _build_dashboard_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        card = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        has_label = any(binding_nodes[b.id].primitive == "label" for b in grouped)
        has_value = any(binding_nodes[b.id].primitive == "value" for b in grouped)
        for binding in grouped:
            node = binding_nodes[binding.id]
            if has_label and not has_value and node.primitive == "badge":
                node.primitive = "value"
                has_value = True
            card.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(card)
    return wrapper, placed


def _build_list_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        item = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            item.children.append(binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(item)
    return wrapper, placed


def _build_form_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        field = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind, "field_id": node_id},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            field.children.append(binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(field)
    return wrapper, placed


def _build_detail_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        row = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="cluster",
            props={"layout_role": "cluster", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        term_assigned = False
        for binding in grouped:
            node = binding_nodes[binding.id]
            if node.primitive == "label" and not term_assigned:
                node.props["detail_role"] = "term"
                term_assigned = True
            else:
                node.props["detail_role"] = "description"
            row.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(row)
    return wrapper, placed


def _binding_attr_or_slot(binding: BindingSpec) -> str:
    try:
        parts = _parse_canonical_address(binding.address)
    except ValueError:
        return ""
    return str(parts.get("attr") or parts.get("slot") or "")


def _build_empty_state_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind, "aria_label": "Empty state"},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    title_assigned = False
    for binding in sorted(motif_bindings, key=lambda b: ordered_positions[b.id]):
        node = binding_nodes[binding.id]
        attr = _binding_attr_or_slot(binding)
        if attr in {"title", "heading", "label"} and not title_assigned:
            node.props["empty_state_role"] = "title"
            title_assigned = True
        elif attr in {"description", "body", "message"}:
            node.props["empty_state_role"] = "description"
        else:
            node.props["empty_state_role"] = "detail"
        wrapper.children.append(node)
        placed.add(binding.id)
    return wrapper, placed


def _build_hero_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind, "aria_label": "Hero"},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    title_assigned = False
    for binding in sorted(motif_bindings, key=lambda b: ordered_positions[b.id]):
        node = binding_nodes[binding.id]
        attr = _binding_attr_or_slot(binding)
        if attr in {"eyebrow", "kicker", "label"}:
            node.props["hero_role"] = "eyebrow"
        elif attr in {"title", "heading", "headline"} and not title_assigned:
            node.props["hero_role"] = "title"
            title_assigned = True
        elif attr in {"description", "subtitle", "body", "summary"}:
            node.props["hero_role"] = "description"
        else:
            node.props["hero_role"] = "detail"
        wrapper.children.append(node)
        placed.add(binding.id)
    return wrapper, placed


def _build_outline_branch(
    node_id: str,
    *,
    motif: MotifSpec,
    substrate: SemanticSubstrate,
    ordered_positions: dict[str, int],
    bindings_by_node_id: dict[str, list[BindingSpec]],
    motif_node_ids: set[str],
    binding_nodes: dict[str, IRNode],
    diagnostics: list[CompilerDiagnostic],
    active_path: tuple[str, ...],
    emitted_node_ids: set[str],
) -> IRNode | None:
    if node_id in active_path:
        cycle_path = " -> ".join((*active_path, node_id))
        _add_diagnostic(
            diagnostics,
            "SEMANTIC_GRAPH_CYCLE",
            f"Outline motif {motif.id} skipped cyclic semantic edge at {node_id}: {cycle_path}.",
            intent_ref=_motif_ref(motif.id),
            content_ref=f"node:{node_id}",
            region_id=motif.region,
        )
        return None
    if node_id in emitted_node_ids:
        _add_diagnostic(
            diagnostics,
            "SEMANTIC_GRAPH_SHARED_NODE",
            f"Outline motif {motif.id} skipped repeated semantic node {node_id} to keep IR node ids unique.",
            intent_ref=_motif_ref(motif.id),
            content_ref=f"node:{node_id}",
            region_id=motif.region,
        )
        return None

    emitted_node_ids.add(node_id)
    child_path = (*active_path, node_id)
    motif_r = _motif_ref(motif.id)
    branch = IRNode(
        id=f"motif_{motif.id}_branch_{node_id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    for binding in sorted(
        bindings_by_node_id.get(node_id, []),
        key=lambda b: ordered_positions[b.id],
    ):
        branch.children.append(binding_nodes[binding.id])
    for child_id in _semantic_children(substrate, node_id):
        if child_id in motif_node_ids:
            child = _build_outline_branch(
                child_id,
                motif=motif,
                substrate=substrate,
                ordered_positions=ordered_positions,
                bindings_by_node_id=bindings_by_node_id,
                motif_node_ids=motif_node_ids,
                binding_nodes=binding_nodes,
                diagnostics=diagnostics,
                active_path=child_path,
                emitted_node_ids=emitted_node_ids,
            )
            if child is not None:
                branch.children.append(child)
    return branch


def _build_outline_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    substrate: SemanticSubstrate,
    ordered_positions: dict[str, int],
    bindings_by_region: dict[str, list[BindingSpec]],
    binding_nodes: dict[str, IRNode],
    diagnostics: list[CompilerDiagnostic],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    motif_node_ids = {_binding_node_id(b) for b in motif_bindings}
    outline_bindings = [
        b for b in bindings_by_region.get(motif.region, [])
        if _binding_node_id(b) in motif_node_ids
    ]
    bindings_by_node_id: dict[str, list[BindingSpec]] = {}
    for b in outline_bindings:
        bindings_by_node_id.setdefault(_binding_node_id(b), []).append(b)
        placed.add(b.id)
    semantic_parent = _semantic_parent_by_id(substrate)
    top_level = [
        nid for nid in motif_node_ids
        if semantic_parent.get(nid) not in motif_node_ids
    ]

    def _min_pos(nid: str) -> int:
        bs = bindings_by_node_id.get(nid)
        if bs:
            return min(ordered_positions[b.id] for b in bs)
        return 10**9

    top_level.sort(key=_min_pos)
    if motif_node_ids and not top_level:
        _add_diagnostic(
            diagnostics,
            "SEMANTIC_GRAPH_CYCLE",
            f"Outline motif {motif.id} has no acyclic top-level semantic node; using declaration order and skipping cyclic repeats.",
            intent_ref=_motif_ref(motif.id),
            region_id=motif.region,
        )
        top_level = sorted(motif_node_ids, key=_min_pos)

    emitted_outline_nodes: set[str] = set()
    for nid in top_level:
        branch = _build_outline_branch(
            nid,
            motif=motif,
            substrate=substrate,
            ordered_positions=ordered_positions,
            bindings_by_node_id=bindings_by_node_id,
            motif_node_ids=motif_node_ids,
            binding_nodes=binding_nodes,
            diagnostics=diagnostics,
            active_path=(),
            emitted_node_ids=emitted_outline_nodes,
        )
        if branch is not None:
            wrapper.children.append(branch)
    return wrapper, placed


def _build_comparison_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    ordered_positions: dict[str, int],
    binding_nodes: dict[str, IRNode],
) -> tuple[IRNode, set[str]]:
    motif_r = _motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="cluster",
        props={"layout_role": "cluster", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, ordered_positions):
        panel = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="stack",
            props={"layout_role": "stack", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            panel.children.append(binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(panel)
    return wrapper, placed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class UnsupportedMotifError(Exception):
    """Raised when the reference compiler encounters a motif type it can't handle."""
    pass


class CompilerInputError(Exception):
    """Raised when a bundle cannot produce a root CompositionIR."""
    pass


def _validate_bundle_shape(bundle: Any) -> tuple[SemanticSubstrate, ViewSpec]:
    if not isinstance(bundle, IntentBundle):
        raise CompilerInputError("compile() expects an IntentBundle.")
    if not isinstance(bundle.substrate, SemanticSubstrate):
        raise CompilerInputError("IntentBundle.substrate must be a SemanticSubstrate.")
    if not isinstance(bundle.view_spec, ViewSpec):
        raise CompilerInputError("IntentBundle.view_spec must be a ViewSpec.")
    if not isinstance(bundle.substrate.nodes, dict):
        raise CompilerInputError("SemanticSubstrate.nodes must be a dictionary.")
    for node_key, node in bundle.substrate.nodes.items():
        if not isinstance(node, SemanticNode):
            raise CompilerInputError(
                f"SemanticSubstrate.nodes['{node_key}'] must be a SemanticNode."
            )

    list_fields: tuple[tuple[str, type[Any]], ...] = (
        ("regions", RegionSpec),
        ("bindings", BindingSpec),
        ("groups", GroupSpec),
        ("motifs", MotifSpec),
        ("styles", StyleSpec),
        ("actions", ActionIntent),
    )
    for field_name, expected_type in list_fields:
        values = getattr(bundle.view_spec, field_name)
        if not isinstance(values, list):
            raise CompilerInputError(f"ViewSpec.{field_name} must be a list.")
        for index, value in enumerate(values):
            if not isinstance(value, expected_type):
                raise CompilerInputError(
                    f"ViewSpec.{field_name}[{index}] must be a {expected_type.__name__}."
                )

    return bundle.substrate, bundle.view_spec


def _validate_roots(substrate: SemanticSubstrate, view_spec: ViewSpec) -> None:
    if not substrate.root_id:
        raise CompilerInputError("SemanticSubstrate.root_id is required.")
    if substrate.root_id not in substrate.nodes:
        raise CompilerInputError(
            f"SemanticSubstrate.root_id '{substrate.root_id}' is not present in substrate.nodes."
        )
    if not view_spec.root_region:
        raise CompilerInputError("ViewSpec.root_region is required.")


def _validate_safe_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or not SAFE_COMPILER_ID_RE.match(value):
        raise CompilerInputError(
            f"{label} '{value}' must use only letters, digits, underscore, dot, and dash."
        )


def _validate_semantic_node_maps(node_key: str, node: Any, node_ids: set[str]) -> None:
    if not isinstance(node.attrs, dict):
        raise CompilerInputError(f"SemanticNode '{node_key}'.attrs must be a dictionary.")
    for attr_key in node.attrs:
        _validate_safe_id(attr_key, f"SemanticNode '{node_key}'.attrs key")

    if not isinstance(node.slots, dict):
        raise CompilerInputError(f"SemanticNode '{node_key}'.slots must be a dictionary.")
    for slot_key, values in node.slots.items():
        _validate_safe_id(slot_key, f"SemanticNode '{node_key}'.slots key")
        if not isinstance(values, list):
            raise CompilerInputError(
                f"SemanticNode '{node_key}'.slots['{slot_key}'] must be a list."
            )

    if not isinstance(node.edges, dict):
        raise CompilerInputError(f"SemanticNode '{node_key}'.edges must be a dictionary.")
    for edge_key, target_ids in node.edges.items():
        _validate_safe_id(edge_key, f"SemanticNode '{node_key}'.edges key")
        if not isinstance(target_ids, list):
            raise CompilerInputError(
                f"SemanticNode '{node_key}'.edges['{edge_key}'] must be a list."
            )
        for target_id in target_ids:
            _validate_safe_id(target_id, f"SemanticNode '{node_key}'.edges['{edge_key}'] target id")
            if target_id not in node_ids:
                raise CompilerInputError(
                    f"SemanticNode '{node_key}'.edges['{edge_key}'] target id '{target_id}' "
                    "must reference a declared substrate node."
                )


def _validate_non_negative_int(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CompilerInputError(f"{label} must be a non-negative integer.")


def _validate_id_list(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise CompilerInputError(f"{label} must be a list.")
    for item in value:
        _validate_safe_id(item, label)


def _validate_view_spec_constraints(view_spec: ViewSpec) -> None:
    if not isinstance(view_spec.complexity_tier, int) or isinstance(view_spec.complexity_tier, bool) or view_spec.complexity_tier < 1:
        raise CompilerInputError("ViewSpec.complexity_tier must be a positive integer.")
    for region in view_spec.regions:
        if region.layout not in SUPPORTED_REGION_LAYOUTS:
            supported = ", ".join(sorted(SUPPORTED_REGION_LAYOUTS))
            raise CompilerInputError(f"Region '{region.id}' layout must be one of: {supported}.")
        _validate_non_negative_int(region.min_children, f"Region '{region.id}'.min_children")
        if region.max_children is not None:
            _validate_non_negative_int(region.max_children, f"Region '{region.id}'.max_children")
            if region.max_children < region.min_children:
                raise CompilerInputError(
                    f"Region '{region.id}'.max_children must be null or greater than or equal to min_children."
                )
    for binding in view_spec.bindings:
        if binding.cardinality not in SUPPORTED_BINDING_CARDINALITIES:
            supported = ", ".join(sorted(SUPPORTED_BINDING_CARDINALITIES))
            raise CompilerInputError(f"Binding '{binding.id}'.cardinality must be one of: {supported}.")
    for group in view_spec.groups:
        if group.kind not in SUPPORTED_GROUP_KINDS:
            supported = ", ".join(sorted(SUPPORTED_GROUP_KINDS))
            raise CompilerInputError(f"Group '{group.id}'.kind must be one of: {supported}.")
        _validate_id_list(group.members, f"Group '{group.id}'.members")
    for motif in view_spec.motifs:
        _validate_id_list(motif.members, f"Motif '{motif.id}'.members")
    for style in view_spec.styles:
        if not isinstance(style.target, str) or not style.target:
            raise CompilerInputError(f"Style '{style.id}'.target must be a non-empty string.")
        if not isinstance(style.token, str) or not style.token:
            raise CompilerInputError(f"Style '{style.id}'.token must be a non-empty string.")
    for action in view_spec.actions:
        if not isinstance(action.kind, str) or not action.kind:
            raise CompilerInputError(f"Action '{action.id}'.kind must be a non-empty string.")
        if not isinstance(action.label, str):
            raise CompilerInputError(f"Action '{action.id}'.label must be a string.")
        if action.target_ref is not None and not isinstance(action.target_ref, str):
            raise CompilerInputError(f"Action '{action.id}'.target_ref must be a string or None.")
        _validate_id_list(action.payload_bindings, f"Action '{action.id}'.payload_bindings")


def _validate_identifier_contract(substrate: SemanticSubstrate, view_spec: ViewSpec) -> None:
    _validate_safe_id(substrate.id, "SemanticSubstrate.id")
    _validate_safe_id(substrate.root_id, "SemanticSubstrate.root_id")
    node_ids = set(substrate.nodes)
    for node_key, node in substrate.nodes.items():
        _validate_safe_id(node_key, "SemanticSubstrate.nodes key")
        _validate_safe_id(node.id, "SemanticNode.id")
        _validate_semantic_node_maps(node_key, node, node_ids)
        if node_key != node.id:
            raise CompilerInputError(
                f"SemanticSubstrate.nodes key '{node_key}' must match SemanticNode.id '{node.id}'."
            )

    _validate_safe_id(view_spec.id, "ViewSpec.id")
    _validate_safe_id(view_spec.substrate_id, "ViewSpec.substrate_id")
    _validate_view_spec_constraints(view_spec)
    if view_spec.substrate_id != substrate.id:
        raise CompilerInputError(
            f"ViewSpec.substrate_id '{view_spec.substrate_id}' must match "
            f"SemanticSubstrate.id '{substrate.id}'."
        )
    _validate_safe_id(view_spec.root_region, "ViewSpec.root_region")
    for region in view_spec.regions:
        _validate_safe_id(region.id, "Region.id")
        if region.parent_region:
            _validate_safe_id(region.parent_region, "Region.parent_region")
    for binding in view_spec.bindings:
        _validate_safe_id(binding.id, "Binding.id")
        _validate_safe_id(binding.target_region, "Binding.target_region")
    for group in view_spec.groups:
        _validate_safe_id(group.id, "Group.id")
        if group.target_region:
            _validate_safe_id(group.target_region, "Group.target_region")
    for motif in view_spec.motifs:
        _validate_safe_id(motif.id, "Motif.id")
        _validate_safe_id(motif.region, "Motif.region")
    for style in view_spec.styles:
        _validate_safe_id(style.id, "Style.id")
    for action in view_spec.actions:
        _validate_safe_id(action.id, "Action.id")
        _validate_safe_id(action.target_region, "Action.target_region")


def _validate_region_tree(valid_regions: list[Any], root_region: str) -> None:
    parent_by_region = {region.id: region.parent_region or None for region in valid_regions}
    root_parent = parent_by_region.get(root_region)
    if root_parent is not None:
        raise CompilerInputError(
            f"ViewSpec.root_region '{root_region}' must not declare parent_region '{root_parent}'."
        )

    for region in valid_regions:
        if region.id == root_region:
            continue
        if not region.parent_region:
            raise CompilerInputError(
                f"Region '{region.id}' is not the root and must declare parent_region."
            )
        seen: set[str] = set()
        cursor: str | None = region.id
        while cursor is not None and cursor != root_region:
            if cursor in seen:
                raise CompilerInputError(
                    f"Region parent_region cycle detected while walking from '{region.id}'."
                )
            seen.add(cursor)
            cursor = parent_by_region.get(cursor)
        if cursor is None:
            raise CompilerInputError(
                f"Region '{region.id}' does not reach root_region '{root_region}'."
            )


def compile(
    bundle: IntentBundle,
    design: DesignSystemContext | DesignRequest | str | None = None,
    *,
    strict_design: bool = False,
) -> ASTBundle:
    """
    Compile an IntentBundle into an ASTBundle using the reference compiler.

    Supports motif kinds: table, dashboard, outline, comparison, list, form, detail, empty_state, hero.
    Raises UnsupportedMotifError for motif kinds not in the reference set.

    For full compilation (arbitrary data shapes, OOD generalization),
    use the hosted compiler at api.viewspec.dev.
    """
    substrate, view_spec = _validate_bundle_shape(bundle)
    diagnostics: list[CompilerDiagnostic] = []
    _validate_roots(substrate, view_spec)
    _validate_identifier_contract(substrate, view_spec)
    address_index = _build_address_index(substrate)
    ordered_positions = _ordered_binding_positions(view_spec)

    supported_kinds = {"table", "dashboard", "outline", "comparison", "list", "form", "detail", "empty_state", "hero"}
    for motif in view_spec.motifs:
        if motif.kind not in supported_kinds:
            raise UnsupportedMotifError(
                f"Motif kind '{motif.kind}' is not supported by the reference compiler. "
                f"Supported: {', '.join(sorted(supported_kinds))}. "
                f"Use the hosted compiler at api.viewspec.dev for full support."
            )

    # Build region nodes
    region_nodes: dict[str, IRNode] = {}
    valid_regions = []
    seen_region_ids: set[str] = set()
    for region in view_spec.regions:
        if region.id in seen_region_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_REGION_ID",
                f"Region id {region.id} appears more than once. The first occurrence was used.",
                intent_ref=_region_ref(region.id),
                region_id=region.id,
            )
            continue
        seen_region_ids.add(region.id)
        valid_regions.append(region)
        primitive = "root" if region.id == view_spec.root_region else _layout_primitive(region.layout)
        props: dict[str, object] = {"layout_role": primitive}
        node = IRNode(
            id=f"region_{region.id}",
            primitive=primitive,
            props=props,
            provenance=Provenance(intent_refs=[_region_ref(region.id)]),
        )
        if region.id == view_spec.root_region:
            node.provenance.intent_refs.insert(0, _view_ref(view_spec.id))
        region_nodes[region.id] = node

    if view_spec.root_region not in region_nodes:
        raise CompilerInputError(f"ViewSpec.root_region '{view_spec.root_region}' is not present in view_spec.regions.")
    _validate_region_tree(valid_regions, view_spec.root_region)

    # Wire region hierarchy
    for region in valid_regions:
        if region.parent_region and region.parent_region in region_nodes:
            region_nodes[region.parent_region].children.append(region_nodes[region.id])
        elif region.parent_region:
            _add_diagnostic(
                diagnostics, "UNKNOWN_REGION",
                f"Region {region.id} declares unknown parent region {region.parent_region}.",
                intent_ref=_region_ref(region.id),
                region_id=region.parent_region,
            )

    all_binding_ids: set[str] = set()
    seen_binding_ids: set[str] = set()
    seen_exactly_once_addresses: dict[str, str] = {}

    # Validate bindings
    valid_bindings: list[BindingSpec] = []
    valid_binding_ids: set[str] = set()
    for binding in view_spec.bindings:
        if binding.id in seen_binding_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_BINDING_ID",
                f"Binding id {binding.id} appears more than once. The first occurrence was used.",
                intent_ref=_binding_ref(binding.id),
                content_ref=binding.address,
                region_id=binding.target_region,
            )
            continue
        seen_binding_ids.add(binding.id)
        all_binding_ids.add(binding.id)
        if binding.target_region not in region_nodes:
            _add_diagnostic(
                diagnostics, "UNKNOWN_REGION",
                f"Binding {binding.id} targets unknown region {binding.target_region}.",
                intent_ref=_binding_ref(binding.id),
                content_ref=binding.address,
                region_id=binding.target_region,
            )
            continue
        if binding.present_as not in PRESENT_AS_TO_PRIMITIVE:
            _add_diagnostic(
                diagnostics, "UNKNOWN_PRESENT_AS",
                f"Binding {binding.id} uses unknown present_as {binding.present_as}.",
                intent_ref=_binding_ref(binding.id),
                content_ref=binding.address,
                region_id=binding.target_region,
            )
            continue
        if binding.cardinality == "exactly_once" and binding.address in seen_exactly_once_addresses:
            _add_diagnostic(
                diagnostics, "DUPLICATE_EXACTLY_ONCE_ADDRESS",
                f"Binding {binding.id} duplicates exactly_once address {binding.address} "
                f"already used by {seen_exactly_once_addresses[binding.address]}.",
                intent_ref=_binding_ref(binding.id),
                content_ref=binding.address,
                region_id=binding.target_region,
            )
            continue
        try:
            _parse_canonical_address(binding.address)
            _resolve_address(binding.address, address_index)
        except (ValueError, KeyError) as exc:
            _add_diagnostic(
                diagnostics, "INVALID_ADDRESS",
                f"Binding {binding.id} has invalid address {binding.address}: {exc}",
                intent_ref=_binding_ref(binding.id),
                content_ref=binding.address,
                region_id=binding.target_region,
            )
            continue
        valid_bindings.append(binding)
        valid_binding_ids.add(binding.id)
        if binding.cardinality == "exactly_once":
            seen_exactly_once_addresses[binding.address] = binding.id

    # Build binding IR nodes
    bindings_by_id = {b.id: b for b in valid_bindings}
    binding_nodes: dict[str, IRNode] = {
        b.id: _build_binding_node(b, address_index) for b in valid_bindings
    }

    # Sort bindings by region
    bindings_by_region: dict[str, list[BindingSpec]] = {r.id: [] for r in valid_regions}
    for b in valid_bindings:
        if b.target_region in bindings_by_region:
            bindings_by_region[b.target_region].append(b)
    for region_id in bindings_by_region:
        bindings_by_region[region_id].sort(key=lambda b: ordered_positions[b.id])

    # Process motifs
    placed_binding_ids: set[str] = set()
    motif_wrappers: dict[str, IRNode] = {}
    motif_by_id: dict[str, MotifSpec] = {}
    valid_motifs: list[MotifSpec] = []
    for motif in view_spec.motifs:
        if motif.id in motif_by_id:
            _add_diagnostic(
                diagnostics, "DUPLICATE_MOTIF_ID",
                f"Motif id {motif.id} appears more than once. The first occurrence was used.",
                intent_ref=_motif_ref(motif.id),
                region_id=motif.region,
            )
            continue
        motif_by_id[motif.id] = motif
        valid_motifs.append(motif)

    seen_group_ids: set[str] = set()
    for group in view_spec.groups:
        if group.id in seen_group_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_GROUP_ID",
                f"Group id {group.id} appears more than once. The first occurrence was used.",
                intent_ref=f"viewspec:group:{group.id}",
                region_id=group.target_region,
            )
            continue
        seen_group_ids.add(group.id)
        for member_id in group.members:
            if member_id not in all_binding_ids:
                _add_diagnostic(
                    diagnostics, "MISSING_GROUP_MEMBER",
                    f"Group {group.id} references missing binding {member_id}.",
                    intent_ref=f"viewspec:group:{group.id}",
                    region_id=group.target_region,
                )

    for motif in valid_motifs:
        if motif.region not in region_nodes:
            _add_diagnostic(
                diagnostics, "UNKNOWN_REGION",
                f"Motif {motif.id} targets unknown region {motif.region}.",
                intent_ref=_motif_ref(motif.id),
                region_id=motif.region,
            )
            continue

        for member_id in motif.members:
            if member_id not in all_binding_ids:
                _add_diagnostic(
                    diagnostics, "MISSING_MOTIF_MEMBER",
                    f"Motif {motif.id} references missing binding {member_id}.",
                    intent_ref=_motif_ref(motif.id),
                    region_id=motif.region,
                )

        carrier = region_nodes[motif.region]
        motif_bindings = sorted(
            [bindings_by_id[mid] for mid in motif.members if mid in valid_binding_ids],
            key=lambda b: ordered_positions[b.id],
        )

        if motif.kind == "table":
            wrapper, motif_placed = _build_table_motif(
                motif, motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "dashboard":
            wrapper, motif_placed = _build_dashboard_motif(
                motif, motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "outline":
            wrapper, motif_placed = _build_outline_motif(
                motif, motif_bindings,
                substrate=substrate,
                ordered_positions=ordered_positions,
                bindings_by_region=bindings_by_region,
                binding_nodes=binding_nodes,
                diagnostics=diagnostics,
            )
        elif motif.kind == "comparison":
            wrapper, motif_placed = _build_comparison_motif(
                motif, motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "list":
            wrapper, motif_placed = _build_list_motif(
                motif,
                motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "form":
            wrapper, motif_placed = _build_form_motif(
                motif,
                motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "detail":
            wrapper, motif_placed = _build_detail_motif(
                motif,
                motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "empty_state":
            wrapper, motif_placed = _build_empty_state_motif(
                motif,
                motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        elif motif.kind == "hero":
            wrapper, motif_placed = _build_hero_motif(
                motif,
                motif_bindings,
                ordered_positions=ordered_positions,
                binding_nodes=binding_nodes,
            )
        else:
            continue

        carrier.children.append(wrapper)
        motif_wrappers[motif.id] = wrapper
        placed_binding_ids.update(motif_placed)

    # Place remaining unplaced bindings directly in their regions
    for region_id, bindings in bindings_by_region.items():
        region_node = region_nodes[region_id]
        for b in bindings:
            if b.id not in placed_binding_ids:
                region_node.children.append(binding_nodes[b.id])

    valid_actions: list[ActionIntent] = []
    seen_action_ids: set[str] = set()
    for action in view_spec.actions:
        action_valid = True
        if action.id in seen_action_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_ACTION_ID",
                f"Action id {action.id} appears more than once. The first occurrence was used.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            continue
        seen_action_ids.add(action.id)
        if action.kind not in SUPPORTED_ACTION_KINDS:
            action_valid = False
            _add_diagnostic(
                diagnostics,
                "UNSUPPORTED_ACTION_KIND",
                f"Action {action.id} uses unsupported kind {action.kind}.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
        if action.target_region not in region_nodes:
            action_valid = False
            _add_diagnostic(
                diagnostics, "UNKNOWN_ACTION_TARGET",
                f"Action {action.id} targets unknown region {action.target_region}.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
        if action.target_ref:
            if ":" not in action.target_ref:
                action_valid = False
                _add_diagnostic(
                    diagnostics, "INVALID_ACTION_TARGET_REF",
                    f"Action {action.id} target_ref must use kind:id form.",
                    intent_ref=_action_ref(action.id),
                )
            else:
                target_kind, target_id = action.target_ref.split(":", 1)
                target_exists = (
                    (target_kind == "region" and target_id in region_nodes)
                    or (target_kind == "binding" and target_id in valid_binding_ids)
                    or (target_kind == "motif" and target_id in motif_by_id)
                    or (target_kind == "view" and target_id == view_spec.id)
                )
                if not target_exists:
                    action_valid = False
                    _add_diagnostic(
                        diagnostics, "UNKNOWN_ACTION_TARGET",
                        f"Action {action.id} targets unknown {target_kind}:{target_id}.",
                        intent_ref=_action_ref(action.id),
                    )
        for binding_id in action.payload_bindings:
            if binding_id not in valid_binding_ids:
                action_valid = False
                _add_diagnostic(
                    diagnostics, "UNKNOWN_ACTION_PAYLOAD_BINDING",
                    f"Action {action.id} references missing payload binding {binding_id}.",
                    intent_ref=_action_ref(action.id),
                )
        if action_valid:
            valid_actions.append(action)

    _apply_product_surface_planner_v1(
        view_spec=view_spec,
        valid_regions=valid_regions,
        region_nodes=region_nodes,
        motif_wrappers=motif_wrappers,
        motif_by_id=motif_by_id,
        valid_actions=valid_actions,
        bindings_by_id=bindings_by_id,
        diagnostics=diagnostics,
        root_region=view_spec.root_region,
    )

    style_values = _derive_style_tokens(substrate, view_spec)
    design_context = _coerce_design_context(design, strict_design=strict_design)
    if design_context is not None:
        style_values = merge_style_values(style_values, design_context.style_values)

    # Apply styles
    seen_style_ids: set[str] = set()
    for style in view_spec.styles:
        if style.id in seen_style_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_STYLE_ID",
                f"Style id {style.id} appears more than once. The first occurrence was used.",
                intent_ref=_style_ref(style.id),
            )
            continue
        seen_style_ids.add(style.id)
        style_token_known = style.token in style_values
        if not style_token_known:
            _add_diagnostic(
                diagnostics,
                "UNKNOWN_STYLE_TOKEN",
                f"Style {style.id} uses unknown token {style.token}.",
                intent_ref=_style_ref(style.id),
            )
        target = style.target
        if ":" in target:
            target_kind, target_id = target.split(":", 1)
        else:
            target_id = target
            matches = _bare_style_target_matches(
                target_id,
                view_spec=view_spec,
                region_nodes=region_nodes,
                binding_nodes=binding_nodes,
                motif_by_id=motif_by_id,
            )
            if len(matches) > 1:
                _add_diagnostic(
                    diagnostics,
                    "AMBIGUOUS_STYLE_TARGET",
                    f"Style {style.id} bare target {target_id} matches multiple namespaces: {', '.join(matches)}.",
                    intent_ref=_style_ref(style.id),
                )
                continue
            target_kind = matches[0] if matches else "binding"

        if target_kind == "region" and target_id in region_nodes:
            if style_token_known:
                _attach_style(region_nodes[target_id], style)
        elif target_kind == "binding" and target_id in binding_nodes:
            if style_token_known:
                _attach_style(binding_nodes[target_id], style)
        elif target_kind == "motif" and target_id in motif_by_id:
            if not style_token_known:
                pass
            elif target_id in motif_wrappers:
                _attach_style(motif_wrappers[target_id], style)
            elif motif_by_id[target_id].region in region_nodes:
                _attach_style(region_nodes[motif_by_id[target_id].region], style)
            else:
                _add_diagnostic(
                    diagnostics, "UNKNOWN_STYLE_TARGET",
                    f"Style {style.id} targets motif {target_id}, but its region is not available.",
                    intent_ref=_style_ref(style.id),
                )
        elif target_kind == "view" and target_id == view_spec.id:
            if style_token_known:
                _attach_style(region_nodes[view_spec.root_region], style)
        else:
            _add_diagnostic(
                diagnostics, "UNKNOWN_STYLE_TARGET",
                f"Style {style.id} targets unknown {target_kind}:{target_id}.",
                intent_ref=_style_ref(style.id),
            )

    root_node = region_nodes[view_spec.root_region]
    _assign_default_style_tokens(root_node)

    result = CompilerResult(
        root=CompositionIR(root=root_node),
        diagnostics=diagnostics,
    )

    return ASTBundle(
        result=result,
        style_values=style_values,
        title=view_spec.id,
    )


def _coerce_design_context(
    design: DesignSystemContext | DesignRequest | str | None,
    *,
    strict_design: bool = False,
) -> DesignSystemContext | None:
    if design is None:
        return None
    if isinstance(design, DesignSystemContext):
        return design
    if isinstance(design, DesignRequest):
        return load_design_system(content=design.content, lint=design.lint, strict=strict_design)
    if isinstance(design, str):
        return load_design_system(content=design, strict=strict_design)
    raise TypeError("design must be a DesignSystemContext, DesignRequest, DESIGN.md string, or None")


PRIMITIVE_DEFAULT_TOKENS: dict[str, list[str]] = {
    "root": ["palette.temperature", "tone.neutral"],
    "surface": ["surface.subtle"],
    "stack": ["density.regular"],
    "grid": ["density.regular"],
    "cluster": ["density.compact"],
    "text": ["tone.neutral"],
    "label": ["tone.muted"],
    "value": ["emphasis.high", "tone.neutral"],
    "badge": ["tone.accent"],
    "input": ["surface.subtle", "tone.neutral"],
    "button": ["action.accent"],
}


def _assign_default_style_tokens(node: IRNode) -> None:
    defaults = PRIMITIVE_DEFAULT_TOKENS.get(node.primitive, [])
    for token in defaults:
        if token not in node.style_tokens:
            node.style_tokens.insert(0, token)
    for child in node.children:
        _assign_default_style_tokens(child)


def _derive_style_tokens(substrate: SemanticSubstrate, view_spec: ViewSpec) -> dict[str, str]:
    """Simple style token derivation for the reference compiler."""
    numeric_values: list[float] = []
    total_text_len = 0
    attr_count = 0

    for node in substrate.nodes.values():
        for attr_value in node.attrs.values():
            attr_count += 1
            text = str(attr_value).strip()
            total_text_len += len(text)
            try:
                numeric_values.append(float(text))
            except ValueError:
                pass

    value_bindings = sum(1 for b in view_spec.bindings if b.present_as in {"value", "metric", "number"})
    value_heavy = len(view_spec.bindings) > 0 and value_bindings * 2 >= len(view_spec.bindings)
    dashboard_like = any(m.kind in {"dashboard", "table"} for m in view_spec.motifs)
    dense_surface = dashboard_like or any(r.layout == "grid" for r in view_spec.regions)

    emphasis_weight = 800 if value_heavy else 730
    medium_weight = 685 if dense_surface else 625

    if dense_surface:
        compact_gap, compact_padding = "0.34rem", "0.36rem 0.5rem"
        regular_gap, regular_padding = "0.68rem", "0.58rem 0.74rem"
    else:
        compact_gap, compact_padding = "0.48rem", "0.46rem 0.62rem"
        regular_gap, regular_padding = "0.85rem", "0.74rem 0.9rem"

    style_values = dict(DEFAULT_STYLE_TOKEN_VALUES)
    style_values.update({
        "emphasis.high": f"font-weight: {emphasis_weight}; letter-spacing: -0.025em;",
        "emphasis.medium": f"font-weight: {medium_weight};",
        "tone.accent": "color: #0f766e;",
        "action.accent": "background-color: #0f766e; color: #ffffff;",
        "tone.muted": "color: #6b7280;",
        "surface.subtle": "background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 14px;",
        "surface.strong": "background: #e2e8f0; border: 1px solid #94a3b8; border-radius: 14px;",
        "density.compact": f"gap: {compact_gap}; padding: {compact_padding};",
        "density.regular": f"gap: {regular_gap}; padding: {regular_padding};",
    })
    return style_values


# ---------------------------------------------------------------------------
# Hosted compiler client
# ---------------------------------------------------------------------------

VIEWSPEC_API_URL = "https://api.viewspec.dev"


class CompilerAPIError(Exception):
    """Raised when the hosted compiler API returns an error."""
    pass


def _compile_request_payload(request: IntentBundle | CompileRequestPayload) -> CompileRequestPayload:
    if isinstance(request, CompileRequestPayload):
        return request
    if isinstance(request, IntentBundle):
        return CompileRequestPayload(bundle=request)
    raise TypeError("Expected IntentBundle or CompileRequestPayload")


def compile_remote_response(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
) -> CompileResponse:
    """
    Compile an IntentBundle or CompileRequestPayload using the hosted ViewSpec compiler API.

    Requires the `httpx` package (`pip install viewspec[remote]`).

    Args:
        request: The IntentBundle or hosted compiler request to compile.
        api_url: Base URL of the ViewSpec API (default: https://api.viewspec.dev).
        api_key: Optional API key for authenticated access (higher rate limits).

    Returns:
        CompileResponse with the compiled AST and response metadata.

    Raises:
        CompilerAPIError: If the API returns an error.
        ImportError: If httpx is not installed.
    """
    try:
        import httpx
    except ImportError:
        raise ImportError(
            "httpx is required for remote compilation. Install it: pip install viewspec[remote]"
        ) from None

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = _compile_request_payload(request)

    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/v1/compile",
            json=payload.to_json(),
            headers=headers,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise CompilerAPIError(f"Remote compilation request failed: {exc}") from exc

    def _response_json() -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise CompilerAPIError("Compilation failed: response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise CompilerAPIError("Compilation failed: response JSON was not an object")
        return data

    if response.status_code == 429:
        data = _response_json()
        raise CompilerAPIError(
            f"Rate limit exceeded: {data.get('message', 'Upgrade at viewspec.dev')}"
        )
    if response.status_code == 401:
        raise CompilerAPIError("Invalid API key")
    if response.status_code != 200:
        raise CompilerAPIError(
            f"Compilation failed (HTTP {response.status_code}): {response.text}"
        )

    data = _response_json()
    if "ast" not in data:
        raise CompilerAPIError("Compilation failed: response missing ast")
    try:
        return CompileResponse.from_json(data)
    except Exception as exc:
        raise CompilerAPIError("Compilation failed: response ast was invalid") from exc


def compile_remote(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
) -> ASTBundle:
    """
    Compile an IntentBundle or CompileRequestPayload using the hosted compiler API.

    Returns only the ASTBundle for backward compatibility. Use
    compile_remote_response() to access response metadata such as meta.design.
    """
    return compile_remote_response(request, api_url=api_url, api_key=api_key).ast


def compile_auto(
    request: IntentBundle | CompileRequestPayload,
    *,
    api_url: str = VIEWSPEC_API_URL,
    api_key: str | None = None,
    prefer_local: bool = True,
) -> ASTBundle:
    """
    Compile with automatic local→remote fallback.

    Tries the local reference compiler first. If the ViewSpec contains
    unsupported motif types, falls back to the hosted API (if api_key
    is provided or the free tier is available).

    Args:
        request: The IntentBundle or hosted compiler request to compile.
        api_url: Base URL of the ViewSpec API.
        api_key: Optional API key for higher rate limits.
        prefer_local: If True (default), try local compilation first.

    Returns:
        ASTBundle with the compiled result.
    """
    payload = _compile_request_payload(request)
    if prefer_local:
        try:
            return compile(payload.bundle, design=payload.design)
        except (UnsupportedMotifError, DesignSystemError):
            return compile_remote(payload, api_url=api_url, api_key=api_key)
    return compile_remote(payload, api_url=api_url, api_key=api_key)
