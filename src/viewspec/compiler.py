"""
ViewSpec Reference Compiler — local compilation for standard motif types.

Handles: table, dashboard, outline, comparison, list, form, detail, empty_state,
loading_state, error_state, hero.
For complex or novel layouts, use the hosted compiler at api.viewspec.dev.

This is a deterministic, offline compiler that produces correct CompositionIR
with full provenance for every motif type the SDK builders generate.
"""

from __future__ import annotations

import json
import re
from typing import Any

from viewspec.aesthetics import (
    AESTHETIC_PROFILE_TOKENS,
    AestheticProfileError,
    is_aesthetic_profile_token,
    profile_layout_props,
    profile_style_values,
    validate_aesthetic_profile_registry,
)
from viewspec.compiler_refs import (
    MAX_COMPILE_NESTING_DEPTH,
    action_ref as _action_ref,
    add_diagnostic as _add_diagnostic,
    binding_ref as _binding_ref,
    motif_ref as _motif_ref,
    region_ref as _region_ref,
    style_ref as _style_ref,
    view_ref as _view_ref,
)
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
    parse_canonical_address as _parse_canonical_address,
)
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system, merge_style_values
from viewspec.motif_compilers import SUPPORTED_MOTIF_KINDS
from viewspec.motif_plugins import (
    MotifCompileContext,
    MotifPlugin,
    MotifPluginError,
    MotifRegistry,
    builtin_motif_registry,
    create_motif_registry,
)

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

COLLECTION_MOTIF_KINDS = frozenset({"table", "list"})
STATE_MOTIF_KINDS = frozenset({"empty_state", "loading_state", "error_state"})
CONFLICTING_STATE_MOTIF_KINDS = frozenset({"loading_state", "error_state"})
COLLECTION_ACTION_KINDS = frozenset({"search", "filter", "sort", "paginate", "bulk_action"})
SUPPORTED_ACTION_KINDS = ("select", "submit", "navigate", "search", "filter", "sort", "paginate", "bulk_action")
MAX_COLLECTION_ACTIONS_PER_COLLECTION = 8
MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS = 8
MAX_STATE_MOTIFS = 16
COLLECTION_PAYLOAD_VALUE_MAX_BYTES = 512
BULK_SELECTION_VALUE_MAX_BYTES = 4096
BULK_SELECTION_MAX_IDS = 100
SUPPORTED_BINDING_CARDINALITIES = {"exactly_once"}
SUPPORTED_GROUP_KINDS = {"ordered"}
SUPPORTED_REGION_LAYOUTS = {"cluster", "grid", "stack"}

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

SUPPORTED_AESTHETIC_PROFILES = AESTHETIC_PROFILE_TOKENS
WORKSPACE_HEADER_ROLE_MARKERS = frozenset({"app_header", "banner", "header", "page_header"})
WORKSPACE_CONTENT_GRID_ROLE_MARKERS = frozenset({"application", "body", "content", "main", "workspace"})
WORKSPACE_PRIMARY_ROLE_MARKERS = frozenset({"main", "primary", "primary_column", "workspace_primary"})
WORKSPACE_SIDE_RAIL_ROLE_MARKERS = frozenset(
    {"aside", "complementary", "detail_rail", "rail", "side_rail", "sidebar"}
)


# ---------------------------------------------------------------------------
# Address resolution
# ---------------------------------------------------------------------------


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


def _utf8_size(value: Any) -> int:
    return len(_text_from_value(value).encode("utf-8"))


def _resolved_payload_value(binding_id: str, bindings_by_id: dict[str, BindingSpec], address_index: dict[str, object]) -> object:
    binding = bindings_by_id.get(binding_id)
    if binding is None:
        return ""
    try:
        return _resolve_address(binding.address, address_index)
    except KeyError:
        return ""


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


def _resolve_aesthetic_profile(view_spec: ViewSpec, diagnostics: list[CompilerDiagnostic]) -> StyleSpec | None:
    profile_styles = [style for style in view_spec.styles if is_aesthetic_profile_token(style.token)]
    if not profile_styles:
        return None
    if len(profile_styles) > 1:
        _add_diagnostic(
            diagnostics,
            "AESTHETIC_PROFILE_MULTIPLE",
            "IntentBundle may declare at most one aesthetic.* style token.",
            intent_ref=_view_ref(view_spec.id),
        )
        return None
    style = profile_styles[0]
    if style.token not in SUPPORTED_AESTHETIC_PROFILES:
        _add_diagnostic(
            diagnostics,
            "AESTHETIC_PROFILE_UNKNOWN",
            f"Style {style.id} uses unknown aesthetic profile {style.token}.",
            intent_ref=_style_ref(style.id),
        )
        return None
    expected_target = f"view:{view_spec.id}"
    if style.target != expected_target:
        _add_diagnostic(
            diagnostics,
            "AESTHETIC_PROFILE_TARGET_INVALID",
            f"Style {style.id} must target exactly {expected_target}.",
            intent_ref=_style_ref(style.id),
        )
        return None
    try:
        validate_aesthetic_profile_registry()
    except AestheticProfileError as exc:
        _add_diagnostic(
            diagnostics,
            exc.code,
            exc.message,
            intent_ref=_style_ref(style.id),
        )
        return None
    return style


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


def _walk_ir(node: IRNode) -> list[IRNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk_ir(child))
    return nodes


def _parent_by_ir_id(root: IRNode) -> dict[str, IRNode | None]:
    parents: dict[str, IRNode | None] = {root.id: None}

    def walk(node: IRNode) -> None:
        for child in node.children:
            parents[child.id] = node
            walk(child)

    walk(root)
    return parents


def _apply_aesthetic_profile_layout_v1(root: IRNode, profile: str) -> None:
    layout_by_role = profile_layout_props(profile)
    parents = _parent_by_ir_id(root)
    for node in _walk_ir(root):
        product_role = node.props.get("product_role")
        if not isinstance(product_role, str):
            continue
        layout_props = layout_by_role.get(product_role)
        if not layout_props:
            continue
        if "columns" in layout_props:
            if node.primitive != "grid":
                raise RuntimeError(
                    f"AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT: product role {product_role!r} no longer maps to a grid node."
                )
            node.props["columns"] = layout_props["columns"]
            node.props["aesthetic_layout_profile"] = profile
        if "span_columns" in layout_props:
            if node.primitive != "surface" or product_role != "metric_card":
                raise RuntimeError(
                    f"AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT: product role {product_role!r} no longer maps to a metric card surface."
                )
            parent = parents.get(node.id)
            if parent is None or parent.props.get("product_role") != "metric_grid" or parent.primitive != "grid":
                raise RuntimeError("AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT: metric card span no longer targets a metric grid child.")
            if parent.children[:1] != [node]:
                continue
            parent_columns = int(parent.props.get("columns") or 1)
            span_columns = min(layout_props["span_columns"], parent_columns)
            if span_columns > 1:
                node.props["span_columns"] = span_columns
                node.props["aesthetic_layout_profile"] = profile
        if "layout_emphasis" in layout_props:
            if node.primitive != "surface" or product_role != "metric_card":
                raise RuntimeError(
                    f"AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT: product role {product_role!r} no longer maps to a metric card surface."
                )
            parent = parents.get(node.id)
            if parent is None or parent.props.get("product_role") != "metric_grid" or parent.primitive != "grid":
                raise RuntimeError("AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT: metric card emphasis no longer targets a metric grid child.")
            if parent.children[:1] != [node]:
                continue
            node.props["layout_emphasis"] = layout_props["layout_emphasis"]
            node.props["aesthetic_layout_profile"] = profile


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


def _collection_action_bar_for_motif_v1(parent: IRNode, wrapper: IRNode, motif_id: str) -> IRNode:
    row_id = f"planner_{motif_id}_collection_actions"
    existing = [child for child in parent.children if child.id == row_id]
    if len(existing) > 1:
        raise RuntimeError(f"COLLECTION_ACTION_BAR_DUPLICATE: duplicate action bar for collection motif {motif_id}.")
    if existing:
        row = existing[0]
        wrapper_index = parent.children.index(wrapper)
        row_index = parent.children.index(row)
        if row_index != wrapper_index - 1:
            parent.children.remove(row)
            wrapper_index = parent.children.index(wrapper)
            parent.children.insert(wrapper_index, row)
        return row
    row = IRNode(
        id=row_id,
        primitive="cluster",
        props={"layout_role": "cluster", "layout_strategy": "collection_action_bar_v1"},
        provenance=Provenance(intent_refs=list(wrapper.provenance.intent_refs)),
    )
    _assign_product_role_v1(row, "action_row")
    wrapper_index = parent.children.index(wrapper)
    parent.children.insert(wrapper_index, row)
    return row


def _motif_parent(root: IRNode, target: IRNode) -> IRNode | None:
    for child in root.children:
        if child is target:
            return root
        parent = _motif_parent(child, target)
        if parent is not None:
            return parent
    return None


def _is_collection_action(action: ActionIntent) -> bool:
    return action.kind in COLLECTION_ACTION_KINDS


def _collection_action_target_id(action: ActionIntent) -> str | None:
    if not _is_collection_action(action) or not action.target_ref or ":" not in action.target_ref:
        return None
    target_kind, target_id = action.target_ref.split(":", 1)
    if target_kind != "motif":
        return None
    return target_id


def _validate_collection_action(
    action: ActionIntent,
    *,
    motif_by_id: dict[str, MotifSpec],
    motif_wrappers: dict[str, IRNode],
    bindings_by_id: dict[str, BindingSpec],
    address_index: dict[str, object],
    diagnostics: list[CompilerDiagnostic],
) -> bool:
    if not _is_collection_action(action):
        return True
    target_motif_id = _collection_action_target_id(action)
    if (
        target_motif_id is None
        or target_motif_id not in motif_by_id
        or motif_by_id[target_motif_id].kind not in COLLECTION_MOTIF_KINDS
        or target_motif_id not in motif_wrappers
    ):
        _add_diagnostic(
            diagnostics,
            "COLLECTION_ACTION_TARGET_INVALID",
            f"Collection action {action.id} must target a compiled table or list motif by explicit motif:<id>.",
            intent_ref=_action_ref(action.id),
            region_id=action.target_region,
        )
        return False
    if action.kind == "bulk_action":
        selection_bindings = [
            binding_id
            for binding_id in action.payload_bindings
            if binding_id.endswith("_selection") or binding_id.endswith("_selected_ids")
        ]
        if not selection_bindings:
            _add_diagnostic(
                diagnostics,
                "COLLECTION_BULK_SELECTION_REQUIRED",
                f"Bulk action {action.id} must declare exactly one _selection or _selected_ids payload binding.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            return False
        if len(selection_bindings) != 1 or len(action.payload_bindings) != 1:
            _add_diagnostic(
                diagnostics,
                "COLLECTION_BULK_SELECTION_AMBIGUOUS",
                f"Bulk action {action.id} must declare exactly one selection payload binding and no other payload bindings.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            return False
        payload_value = _resolved_payload_value(selection_bindings[0], bindings_by_id, address_index)
        if isinstance(payload_value, list) and len(payload_value) > BULK_SELECTION_MAX_IDS:
            _add_diagnostic(
                diagnostics,
                "COLLECTION_BULK_SELECTION_TOO_LARGE",
                f"Bulk action {action.id} selection payload exceeds {BULK_SELECTION_MAX_IDS} ids.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            return False
        if _utf8_size(payload_value) > BULK_SELECTION_VALUE_MAX_BYTES:
            _add_diagnostic(
                diagnostics,
                "COLLECTION_BULK_SELECTION_TOO_LARGE",
                f"Bulk action {action.id} selection payload exceeds {BULK_SELECTION_VALUE_MAX_BYTES} UTF-8 bytes.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            return False
        return True
    if not (1 <= len(action.payload_bindings) <= MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS):
        _add_diagnostic(
            diagnostics,
            "COLLECTION_ACTION_PAYLOAD_REQUIRED",
            f"Collection action {action.id} must declare 1-{MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS} payload bindings.",
            intent_ref=_action_ref(action.id),
            region_id=action.target_region,
        )
        return False
    oversized = [
        binding_id
        for binding_id in action.payload_bindings
        if _utf8_size(_resolved_payload_value(binding_id, bindings_by_id, address_index)) > COLLECTION_PAYLOAD_VALUE_MAX_BYTES
    ]
    if oversized:
        _add_diagnostic(
            diagnostics,
            "COLLECTION_ACTION_PAYLOAD_TOO_LARGE",
            f"Collection action {action.id} has payload bindings over {COLLECTION_PAYLOAD_VALUE_MAX_BYTES} UTF-8 bytes: {', '.join(sorted(oversized))}.",
            intent_ref=_action_ref(action.id),
            region_id=action.target_region,
        )
        return False
    return True


def _apply_product_surface_planner_v1(
    *,
    view_spec: ViewSpec,
    valid_regions: list[RegionSpec],
    region_nodes: dict[str, IRNode],
    motif_wrappers: dict[str, IRNode],
    motif_by_id: dict[str, MotifSpec],
    valid_actions: list[ActionIntent],
    bindings_by_id: dict[str, BindingSpec],
    address_index: dict[str, object],
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

    collection_action_counts: dict[str, int] = {}
    for action in valid_actions:
        target_collection_id = _collection_action_target_id(action)
        if target_collection_id is not None:
            collection_action_counts[target_collection_id] = collection_action_counts.get(target_collection_id, 0) + 1
    overfull_collections = {
        motif_id
        for motif_id, count in collection_action_counts.items()
        if count > MAX_COLLECTION_ACTIONS_PER_COLLECTION
    }
    for motif_id in sorted(overfull_collections):
        _add_diagnostic(
            diagnostics,
            "TOO_MANY_COLLECTION_ACTIONS",
            f"Collection motif {motif_id} has more than {MAX_COLLECTION_ACTIONS_PER_COLLECTION} collection actions.",
            intent_ref=_motif_ref(motif_id),
        )

    for action in valid_actions:
        action_node = _build_action_node(action, bindings_by_id)
        if _collection_action_target_id(action) in overfull_collections:
            continue
        if not _validate_collection_action(
            action,
            motif_by_id=motif_by_id,
            motif_wrappers=motif_wrappers,
            bindings_by_id=bindings_by_id,
            address_index=address_index,
            diagnostics=diagnostics,
        ):
            continue
        target_collection_id = _collection_action_target_id(action)
        if target_collection_id is not None:
            wrapper = motif_wrappers[target_collection_id]
            parent = _motif_parent(region_nodes[root_region], wrapper)
            if parent is None:
                _add_diagnostic(
                    diagnostics,
                    "COLLECTION_ACTION_TARGET_INVALID",
                    f"Collection action {action.id} target wrapper could not be located in the compiled IR.",
                    intent_ref=_action_ref(action.id),
                    region_id=action.target_region,
                )
                continue
            action_node.props["placement"] = "collection_action_bar"
            _collection_action_bar_for_motif_v1(parent, wrapper, target_collection_id).children.append(action_node)
            continue
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
        depth = 0
        while cursor is not None and cursor != root_region:
            if cursor in seen:
                raise CompilerInputError(
                    f"Region parent_region cycle detected while walking from '{region.id}'."
                )
            depth += 1
            if depth > MAX_COMPILE_NESTING_DEPTH:
                raise CompilerInputError(
                    f"Region nesting depth exceeds the limit of {MAX_COMPILE_NESTING_DEPTH} "
                    f"while walking from '{region.id}'. Flatten the region hierarchy or "
                    f"split into multiple IntentBundles."
                )
            seen.add(cursor)
            cursor = parent_by_region.get(cursor)
        if cursor is None:
            raise CompilerInputError(
                f"Region '{region.id}' does not reach root_region '{root_region}'."
            )


def _build_and_validate_regions(
    view_spec: ViewSpec,
    diagnostics: list[CompilerDiagnostic],
) -> tuple[dict[str, IRNode], list[RegionSpec]]:
    """Build region IR nodes, dedup ids, validate the region tree, wire hierarchy."""
    region_nodes: dict[str, IRNode] = {}
    valid_regions: list[RegionSpec] = []
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
    return region_nodes, valid_regions


def _build_and_validate_bindings(
    view_spec: ViewSpec,
    region_nodes: dict[str, IRNode],
    valid_regions: list[RegionSpec],
    address_index: dict[str, Any],
    ordered_positions: dict[str, int],
    diagnostics: list[CompilerDiagnostic],
) -> tuple[set[str], dict[str, BindingSpec], set[str], dict[str, IRNode], dict[str, list[BindingSpec]]]:
    """Validate bindings, build their IR nodes, and bucket them by region in order."""
    all_binding_ids: set[str] = set()
    seen_binding_ids: set[str] = set()
    seen_exactly_once_addresses: dict[str, str] = {}
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

    bindings_by_id = {b.id: b for b in valid_bindings}
    binding_nodes: dict[str, IRNode] = {
        b.id: _build_binding_node(b, address_index) for b in valid_bindings
    }

    bindings_by_region: dict[str, list[BindingSpec]] = {r.id: [] for r in valid_regions}
    for b in valid_bindings:
        if b.target_region in bindings_by_region:
            bindings_by_region[b.target_region].append(b)
    for region_id in bindings_by_region:
        bindings_by_region[region_id].sort(key=lambda b: ordered_positions[b.id])
    return all_binding_ids, bindings_by_id, valid_binding_ids, binding_nodes, bindings_by_region


def _compile_and_place_motifs(
    view_spec: ViewSpec,
    substrate: SemanticSubstrate,
    region_nodes: dict[str, IRNode],
    binding_nodes: dict[str, IRNode],
    bindings_by_id: dict[str, BindingSpec],
    bindings_by_region: dict[str, list[BindingSpec]],
    valid_binding_ids: set[str],
    all_binding_ids: set[str],
    ordered_positions: dict[str, int],
    active_motif_registry: Any,
    diagnostics: list[CompilerDiagnostic],
) -> tuple[dict[str, IRNode], dict[str, MotifSpec]]:
    """Dedup and compile motifs, validate groups, place motif wrappers and leftover bindings."""
    motif_context = MotifCompileContext(
        substrate=substrate,
        ordered_positions=ordered_positions,
        bindings_by_region=bindings_by_region,
        binding_nodes=binding_nodes,
        diagnostics=diagnostics,
    )
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

    state_motifs = [motif for motif in valid_motifs if motif.kind in STATE_MOTIF_KINDS]
    overfull_state_ids: set[str] = set()
    if len(state_motifs) > MAX_STATE_MOTIFS:
        overfull_state_ids = {motif.id for motif in state_motifs[MAX_STATE_MOTIFS:]}
        _add_diagnostic(
            diagnostics,
            "TOO_MANY_STATE_MOTIFS",
            f"ViewSpec contains more than {MAX_STATE_MOTIFS} state motifs.",
            intent_ref=_view_ref(view_spec.id),
        )
    motifs_by_region: dict[str, list[MotifSpec]] = {}
    for motif in valid_motifs:
        motifs_by_region.setdefault(motif.region, []).append(motif)
    conflicting_state_ids: set[str] = set()
    for region_id, region_motifs in motifs_by_region.items():
        has_collection = any(motif.kind in COLLECTION_MOTIF_KINDS for motif in region_motifs)
        conflicting_states = [
            motif
            for motif in region_motifs
            if motif.kind in CONFLICTING_STATE_MOTIF_KINDS
        ]
        if has_collection and conflicting_states:
            for motif in conflicting_states:
                conflicting_state_ids.add(motif.id)
            _add_diagnostic(
                diagnostics,
                "COLLECTION_STATE_CONFLICT",
                f"Region {region_id} must not render loading_state or error_state with a loaded table/list collection.",
                region_id=region_id,
            )

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

    motif_claimed_binding_ids: set[str] = set()
    for motif in valid_motifs:
        if motif.id in overfull_state_ids or motif.id in conflicting_state_ids:
            placed_binding_ids.update(member_id for member_id in motif.members if member_id in valid_binding_ids)
            continue
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
        owned_member_ids: list[str] = []
        for mid in motif.members:
            if mid not in valid_binding_ids:
                continue
            if mid in motif_claimed_binding_ids:
                _add_diagnostic(
                    diagnostics, "DUPLICATE_MOTIF_MEMBER",
                    f"Binding {mid} is claimed by more than one motif. The first "
                    f"motif to place it was used; motif {motif.id} does not re-render it.",
                    intent_ref=_motif_ref(motif.id),
                    region_id=motif.region,
                )
                continue
            owned_member_ids.append(mid)
        motif_bindings = sorted(
            [bindings_by_id[mid] for mid in owned_member_ids],
            key=lambda b: ordered_positions[b.id],
        )

        compiled_motif = active_motif_registry[motif.kind].compile(motif, motif_bindings, motif_context)
        if compiled_motif is None:
            # The motif failed its contract (the compiler already emitted a contract diagnostic).
            # Do NOT mark its members placed: leave them un-placed and un-claimed so the leftover
            # pass renders each owned member exactly once as a region child -- content must not
            # vanish silently -- and name them so the fallback is loud, mirroring the outline
            # SEMANTIC_GRAPH_UNREACHED_MEMBER handling.
            if owned_member_ids:
                _add_diagnostic(
                    diagnostics, "SEMANTIC_MOTIF_MEMBERS_UNPLACED",
                    f"Motif {motif.id} ({motif.kind}) did not satisfy its contract; its "
                    f"{len(owned_member_ids)} member binding(s) render as region leftovers instead "
                    f"of the motif ({', '.join(owned_member_ids)}).",
                    intent_ref=_motif_ref(motif.id),
                    region_id=motif.region,
                )
            continue
        allowed_placed_binding_ids = {binding.id for binding in motif_bindings}
        disallowed_placed_binding_ids = compiled_motif.placed_binding_ids - allowed_placed_binding_ids
        if disallowed_placed_binding_ids:
            raise MotifPluginError(
                f"Motif compiler for '{motif.kind}' placed bindings it does not own: "
                f"{', '.join(sorted(disallowed_placed_binding_ids))}."
            )
        wrapper = compiled_motif.wrapper

        carrier.children.append(wrapper)
        motif_wrappers[motif.id] = wrapper
        placed_binding_ids.update(compiled_motif.placed_binding_ids)
        motif_claimed_binding_ids.update(owned_member_ids)

    for region_id, bindings in bindings_by_region.items():
        region_node = region_nodes[region_id]
        for b in bindings:
            if b.id not in placed_binding_ids:
                region_node.children.append(binding_nodes[b.id])
    return motif_wrappers, motif_by_id


def _validate_actions(
    view_spec: ViewSpec,
    region_nodes: dict[str, IRNode],
    valid_binding_ids: set[str],
    motif_by_id: dict[str, MotifSpec],
    diagnostics: list[CompilerDiagnostic],
) -> list[ActionIntent]:
    """Validate action ids, kinds, targets, and payload bindings."""
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
    return valid_actions


def _resolve_and_apply_styles(
    view_spec: ViewSpec,
    substrate: SemanticSubstrate,
    design: DesignSystemContext | DesignRequest | str | None,
    strict_design: bool,
    region_nodes: dict[str, IRNode],
    binding_nodes: dict[str, IRNode],
    motif_wrappers: dict[str, IRNode],
    motif_by_id: dict[str, MotifSpec],
    diagnostics: list[CompilerDiagnostic],
) -> dict[str, str]:
    """Resolve the aesthetic profile + design tokens and attach styles to IR nodes."""
    profile_style = _resolve_aesthetic_profile(view_spec, diagnostics)
    style_values = _derive_style_tokens(substrate, view_spec)
    if profile_style is not None:
        selected_profile_values = profile_style_values(profile_style.token)
        if all(style_values.get(token, "").strip() == css.strip() for token, css in selected_profile_values.items()):
            _add_diagnostic(
                diagnostics,
                "AESTHETIC_PROFILE_NOOP",
                f"Style {profile_style.id} profile {profile_style.token} produced no style projection changes.",
                intent_ref=_style_ref(profile_style.id),
            )
            profile_style = None
        else:
            style_values = merge_style_values(style_values, selected_profile_values)
            _apply_aesthetic_profile_layout_v1(region_nodes[view_spec.root_region], profile_style.token)
    design_context = _coerce_design_context(design, strict_design=strict_design)
    if design_context is not None:
        style_values = merge_style_values(style_values, design_context.style_values)

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
        if is_aesthetic_profile_token(style.token):
            if profile_style is style:
                root_profile = region_nodes[view_spec.root_region]
                root_profile.props["aesthetic_profile"] = style.token
                _attach_style(root_profile, style)
            continue
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
    return style_values


def compile(
    bundle: IntentBundle,
    design: DesignSystemContext | DesignRequest | str | None = None,
    *,
    strict_design: bool = False,
    motif_plugins: tuple[MotifPlugin, ...] = (),
    motif_registry: MotifRegistry | None = None,
) -> ASTBundle:
    """
    Compile an IntentBundle into an ASTBundle using the reference compiler.

    Supports motif kinds: table, dashboard, outline, comparison, list, form, detail,
    empty_state, loading_state, error_state, hero.
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
    if motif_registry is not None and motif_plugins:
        raise MotifPluginError("Pass either motif_registry or motif_plugins, not both.")
    active_motif_registry = (
        motif_registry
        if motif_registry is not None
        else create_motif_registry(*tuple(motif_plugins))
        if motif_plugins
        else builtin_motif_registry()
    )
    supported_motif_kinds = active_motif_registry.kinds if motif_registry is not None or motif_plugins else SUPPORTED_MOTIF_KINDS

    for motif in view_spec.motifs:
        if motif.kind not in active_motif_registry:
            raise UnsupportedMotifError(
                f"Motif kind '{motif.kind}' is not supported by the reference compiler. "
                f"Supported: {', '.join(sorted(supported_motif_kinds))}. "
                f"Use the hosted compiler at api.viewspec.dev for full support."
            )

    region_nodes, valid_regions = _build_and_validate_regions(view_spec, diagnostics)

    (
        all_binding_ids,
        bindings_by_id,
        valid_binding_ids,
        binding_nodes,
        bindings_by_region,
    ) = _build_and_validate_bindings(
        view_spec, region_nodes, valid_regions, address_index, ordered_positions, diagnostics
    )

    motif_wrappers, motif_by_id = _compile_and_place_motifs(
        view_spec,
        substrate,
        region_nodes,
        binding_nodes,
        bindings_by_id,
        bindings_by_region,
        valid_binding_ids,
        all_binding_ids,
        ordered_positions,
        active_motif_registry,
        diagnostics,
    )

    valid_actions = _validate_actions(view_spec, region_nodes, valid_binding_ids, motif_by_id, diagnostics)

    _apply_product_surface_planner_v1(
        view_spec=view_spec,
        valid_regions=valid_regions,
        region_nodes=region_nodes,
        motif_wrappers=motif_wrappers,
        motif_by_id=motif_by_id,
        valid_actions=valid_actions,
        bindings_by_id=bindings_by_id,
        address_index=address_index,
        diagnostics=diagnostics,
        root_region=view_spec.root_region,
    )

    style_values = _resolve_and_apply_styles(
        view_spec,
        substrate,
        design,
        strict_design,
        region_nodes,
        binding_nodes,
        motif_wrappers,
        motif_by_id,
        diagnostics,
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
