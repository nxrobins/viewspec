"""
ViewSpec Reference Compiler — local compilation for standard motif types.

Handles: table, dashboard, outline, comparison.
For complex or novel layouts, use the hosted compiler at api.viewspec.dev.

This is a deterministic, offline compiler that produces correct CompositionIR
with full provenance for every motif type the SDK builders generate.
"""

from __future__ import annotations

import json
import re
from typing import Any

from viewspec.types import (
    ASTBundle,
    BindingSpec,
    CompileRequestPayload,
    CompileResponse,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    IntentBundle,
    IRNode,
    MotifSpec,
    Provenance,
    SemanticSubstrate,
    StyleSpec,
    ViewSpec,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRESENT_AS_TO_PRIMITIVE: dict[str, str] = {
    "text": "text",
    "label": "label",
    "value": "value",
    "badge": "badge",
    "rich_text": "text",
    "image_slot": "image_slot",
    "rule": "rule",
}

DEFAULT_STYLE_TOKEN_VALUES: dict[str, str] = {
    "emphasis.low": "font-weight: 500;",
    "emphasis.medium": "font-weight: 600;",
    "emphasis.high": "font-weight: 700; letter-spacing: -0.02em;",
    "density.compact": "gap: 0.4rem; padding: 0.4rem 0.55rem;",
    "density.regular": "gap: 0.7rem; padding: 0.6rem 0.8rem;",
    "tone.muted": "color: #6b7280;",
    "tone.accent": "color: #0f766e;",
    "surface.subtle": "background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 14px;",
    "surface.strong": "background: #e2e8f0; border: 1px solid #94a3b8; border-radius: 14px;",
}

CANONICAL_ADDRESS_RE = re.compile(
    r"^node:(?P<node_id>[^#]+)"
    r"(?:#attr:(?P<attr>[^#]+))?"
    r"(?:#slot:(?P<slot>[^#\[]+)(?:\[(?P<slot_index>\d+)\])?)?"
    r"(?:#edge:(?P<edge>[^#]+))?$"
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


def _grid_columns(region_id: str, view_spec: ViewSpec) -> int:
    binding_count = sum(1 for b in view_spec.bindings if b.target_region == region_id)
    if binding_count >= 4:
        return 2
    return 1


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


def _binding_text(binding: BindingSpec, resolved_value: object) -> dict[str, object]:
    primitive = PRESENT_AS_TO_PRIMITIVE.get(binding.present_as, binding.present_as)
    if primitive == "image_slot":
        return {"alt": _text_from_value(resolved_value)}
    if primitive == "svg":
        return {"label": _text_from_value(resolved_value)}
    if primitive == "rule":
        return {}
    return {"text": _text_from_value(resolved_value)}


def _build_binding_node(binding: BindingSpec, address_index: dict[str, object]) -> IRNode:
    resolved_value = _resolve_address(binding.address, address_index)
    return IRNode(
        id=f"binding_{binding.id}",
        primitive=PRESENT_AS_TO_PRIMITIVE.get(binding.present_as, binding.present_as),
        props=_binding_text(binding, resolved_value),
        provenance=Provenance(
            content_refs=[binding.address],
            intent_refs=[_binding_ref(binding.id)],
        ),
    )


def _attach_style(node: IRNode, style: StyleSpec) -> None:
    if style.token not in node.style_tokens:
        node.style_tokens.append(style.token)
    ref = _style_ref(style.id)
    if ref not in node.provenance.intent_refs:
        node.provenance.intent_refs.append(ref)


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
        row = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="cluster",
            props={"layout_role": "cluster", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            row.children.append(binding_nodes[binding.id])
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


def _build_outline_branch(
    node_id: str,
    *,
    motif: MotifSpec,
    substrate: SemanticSubstrate,
    ordered_positions: dict[str, int],
    bindings_by_node_id: dict[str, list[BindingSpec]],
    motif_node_ids: set[str],
    binding_nodes: dict[str, IRNode],
) -> IRNode:
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
            branch.children.append(
                _build_outline_branch(
                    child_id,
                    motif=motif,
                    substrate=substrate,
                    ordered_positions=ordered_positions,
                    bindings_by_node_id=bindings_by_node_id,
                    motif_node_ids=motif_node_ids,
                    binding_nodes=binding_nodes,
                )
            )
    return branch


def _build_outline_motif(
    motif: MotifSpec,
    motif_bindings: list[BindingSpec],
    *,
    substrate: SemanticSubstrate,
    ordered_positions: dict[str, int],
    bindings_by_region: dict[str, list[BindingSpec]],
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

    for nid in top_level:
        wrapper.children.append(
            _build_outline_branch(
                nid,
                motif=motif,
                substrate=substrate,
                ordered_positions=ordered_positions,
                bindings_by_node_id=bindings_by_node_id,
                motif_node_ids=motif_node_ids,
                binding_nodes=binding_nodes,
            )
        )
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


def _validate_roots(substrate: SemanticSubstrate, view_spec: ViewSpec) -> None:
    if not substrate.root_id:
        raise CompilerInputError("SemanticSubstrate.root_id is required.")
    if substrate.root_id not in substrate.nodes:
        raise CompilerInputError(
            f"SemanticSubstrate.root_id '{substrate.root_id}' is not present in substrate.nodes."
        )
    if not view_spec.root_region:
        raise CompilerInputError("ViewSpec.root_region is required.")


def compile(bundle: IntentBundle) -> ASTBundle:
    """
    Compile an IntentBundle into an ASTBundle using the reference compiler.

    Supports motif kinds: table, dashboard, outline, comparison.
    Raises UnsupportedMotifError for motif kinds not in the reference set.

    For full compilation (arbitrary data shapes, OOD generalization),
    use the hosted compiler at api.viewspec.dev.
    """
    substrate = bundle.substrate
    view_spec = bundle.view_spec
    diagnostics: list[CompilerDiagnostic] = []
    _validate_roots(substrate, view_spec)
    address_index = _build_address_index(substrate)
    ordered_positions = _ordered_binding_positions(view_spec)

    supported_kinds = {"table", "dashboard", "outline", "comparison"}
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
        if primitive == "grid":
            props["columns"] = _grid_columns(region.id, view_spec)
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
            )
        elif motif.kind == "comparison":
            wrapper, motif_placed = _build_comparison_motif(
                motif, motif_bindings,
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
        target = style.target
        if ":" in target:
            target_kind, target_id = target.split(":", 1)
        else:
            # Bare ID — infer kind from known lookups
            target_id = target
            if target_id in binding_nodes:
                target_kind = "binding"
            elif target_id in region_nodes:
                target_kind = "region"
            elif target_id in motif_by_id:
                target_kind = "motif"
            else:
                target_kind = "binding"  # default assumption

        if target_kind == "region" and target_id in region_nodes:
            _attach_style(region_nodes[target_id], style)
        elif target_kind == "binding" and target_id in binding_nodes:
            _attach_style(binding_nodes[target_id], style)
        elif target_kind == "motif" and target_id in motif_by_id:
            if target_id in motif_wrappers:
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
            _attach_style(region_nodes[view_spec.root_region], style)
        else:
            _add_diagnostic(
                diagnostics, "UNKNOWN_STYLE_TARGET",
                f"Style {style.id} targets unknown {target_kind}:{target_id}.",
                intent_ref=_style_ref(style.id),
            )

    seen_action_ids: set[str] = set()
    for action in view_spec.actions:
        if action.id in seen_action_ids:
            _add_diagnostic(
                diagnostics, "DUPLICATE_ACTION_ID",
                f"Action id {action.id} appears more than once. The first occurrence was used.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
            continue
        seen_action_ids.add(action.id)
        if action.target_region not in region_nodes:
            _add_diagnostic(
                diagnostics, "UNKNOWN_ACTION_TARGET",
                f"Action {action.id} targets unknown region {action.target_region}.",
                intent_ref=_action_ref(action.id),
                region_id=action.target_region,
            )
        if action.target_ref:
            if ":" not in action.target_ref:
                _add_diagnostic(
                    diagnostics, "INVALID_ACTION_TARGET_REF",
                    f"Action {action.id} target_ref must use kind:id form.",
                    intent_ref=_action_ref(action.id),
                )
            else:
                target_kind, target_id = action.target_ref.split(":", 1)
                target_exists = (
                    (target_kind == "region" and target_id in region_nodes)
                    or (target_kind == "binding" and target_id in all_binding_ids)
                    or (target_kind == "motif" and target_id in motif_by_id)
                    or (target_kind == "view" and target_id == view_spec.id)
                )
                if not target_exists:
                    _add_diagnostic(
                        diagnostics, "UNKNOWN_ACTION_TARGET",
                        f"Action {action.id} targets unknown {target_kind}:{target_id}.",
                        intent_ref=_action_ref(action.id),
                    )
        for binding_id in action.payload_bindings:
            if binding_id not in all_binding_ids:
                _add_diagnostic(
                    diagnostics, "UNKNOWN_ACTION_PAYLOAD_BINDING",
                    f"Action {action.id} references missing payload binding {binding_id}.",
                    intent_ref=_action_ref(action.id),
                )

    result = CompilerResult(
        root=CompositionIR(root=region_nodes[view_spec.root_region]),
        diagnostics=diagnostics,
    )

    return ASTBundle(
        result=result,
        style_values=_derive_style_tokens(substrate, view_spec),
        title=view_spec.id,
    )


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

    return {
        "emphasis.high": f"font-weight: {emphasis_weight}; letter-spacing: -0.025em;",
        "emphasis.medium": f"font-weight: {medium_weight};",
        "tone.accent": "color: #0f766e;",
        "tone.muted": "color: #6b7280;",
        "surface.subtle": "background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 14px;",
        "surface.strong": "background: #e2e8f0; border: 1px solid #94a3b8; border-radius: 14px;",
        "density.compact": f"gap: {compact_gap}; padding: {compact_padding};",
        "density.regular": f"gap: {regular_gap}; padding: {regular_padding};",
    }


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
    if prefer_local and payload.design is None:
        try:
            return compile(payload.bundle)
        except UnsupportedMotifError:
            return compile_remote(payload, api_url=api_url, api_key=api_key)
    return compile_remote(payload, api_url=api_url, api_key=api_key)
