from __future__ import annotations

from viewspec.compiler_refs import add_diagnostic, motif_ref
from viewspec.motif_plugins import (
    MOTIF_PLUGIN_ABI_VERSION,
    MotifBuildFn,
    MotifCompileContext,
    MotifCompileResult,
    MotifCompiler,
    MotifPlugin,
    MotifPluginCheckIssue,
    MotifPluginCheckReport,
    MotifPluginError,
    MotifPluginFixture,
    MotifPluginManifest,
    MotifPluginSlot,
    MotifRegistry,
    MotifValidateFn,
    _register_motif_compilers,
    builtin_motif_registry,
    check_motif_plugin,
    create_motif_registry,
    motif_compiler_registry,
)
from viewspec.types import (
    BindingSpec,
    IRNode,
    MotifSpec,
    Provenance,
    SemanticSubstrate,
    parse_canonical_address,
)


def _motif_result(wrapper: IRNode, placed_binding_ids: set[str]) -> MotifCompileResult:
    return MotifCompileResult(wrapper=wrapper, placed_binding_ids=frozenset(placed_binding_ids))


def _binding_node_id(binding: BindingSpec) -> str:
    return str(parse_canonical_address(binding.address)["node_id"])


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


def _binding_attr_or_slot(binding: BindingSpec) -> str:
    try:
        parts = parse_canonical_address(binding.address)
    except ValueError:
        return ""
    return str(parts.get("attr") or parts.get("slot") or "")


def _build_table_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        row_header_assigned = False
        row = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="cluster",
            props={"layout_role": "cluster", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            node = context.binding_nodes[binding.id]
            if node.primitive == "label" and not row_header_assigned:
                node.props["table_cell_role"] = "row_header"
                row_header_assigned = True
            else:
                node.props["table_cell_role"] = "cell"
            row.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(row)
    return _motif_result(wrapper, placed)


def _build_dashboard_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        card = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        has_label = any(context.binding_nodes[b.id].primitive == "label" for b in grouped)
        has_value = any(context.binding_nodes[b.id].primitive == "value" for b in grouped)
        for binding in grouped:
            node = context.binding_nodes[binding.id]
            if has_label and not has_value and node.primitive == "badge":
                node.primitive = "value"
                has_value = True
            card.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(card)
    return _motif_result(wrapper, placed)


def _build_list_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        item = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            item.children.append(context.binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(item)
    return _motif_result(wrapper, placed)


def _build_form_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        field = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="surface",
            props={"layout_role": "surface", "motif_kind": motif.kind, "field_id": node_id},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            field.children.append(context.binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(field)
    return _motif_result(wrapper, placed)


def _build_detail_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        row = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="cluster",
            props={"layout_role": "cluster", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        term_assigned = False
        for binding in grouped:
            node = context.binding_nodes[binding.id]
            if node.primitive == "label" and not term_assigned:
                node.props["detail_role"] = "term"
                term_assigned = True
            else:
                node.props["detail_role"] = "description"
            row.children.append(node)
            placed.add(binding.id)
        wrapper.children.append(row)
    return _motif_result(wrapper, placed)


def _build_empty_state_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind, "aria_label": "Empty state"},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    title_assigned = False
    for binding in sorted(motif_bindings, key=lambda b: context.ordered_positions[b.id]):
        node = context.binding_nodes[binding.id]
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
    return _motif_result(wrapper, placed)


def _build_state_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    state_role = "loading" if motif.kind == "loading_state" else "error"
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="surface",
        props={
            "layout_role": "surface",
            "motif_kind": motif.kind,
            "state_role": state_role,
            "aria_label": "Loading state" if state_role == "loading" else "Error state",
        },
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    title_assigned = False
    for binding in sorted(motif_bindings, key=lambda b: context.ordered_positions[b.id]):
        node = context.binding_nodes[binding.id]
        attr = _binding_attr_or_slot(binding)
        if attr in {"title", "heading", "headline", "label"} and not title_assigned:
            node.props["state_motif_role"] = "title"
            title_assigned = True
        elif attr in {"description", "body", "message"}:
            node.props["state_motif_role"] = "description"
        else:
            node.props["state_motif_role"] = "detail"
        wrapper.children.append(node)
        placed.add(binding.id)
    return _motif_result(wrapper, placed)


def _validate_state_motif_contract(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> bool:
    title_count = 0
    description_count = 0
    for binding in motif_bindings:
        attr = _binding_attr_or_slot(binding)
        if attr in {"title", "heading", "headline", "label"}:
            title_count += 1
        elif attr in {"description", "body", "message"}:
            description_count += 1
    ok = True
    if title_count != 1:
        ok = False
        add_diagnostic(
            context.diagnostics,
            "STATE_MOTIF_TITLE_REQUIRED",
            f"{motif.kind} motif {motif.id} must declare exactly one title binding.",
            intent_ref=motif_ref(motif.id),
            region_id=motif.region,
        )
    if description_count > 1:
        ok = False
        add_diagnostic(
            context.diagnostics,
            "STATE_MOTIF_TOO_MANY_DESCRIPTIONS",
            f"{motif.kind} motif {motif.id} may declare at most one description binding.",
            intent_ref=motif_ref(motif.id),
            region_id=motif.region,
        )
    return ok


def _build_hero_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind, "aria_label": "Hero"},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    title_assigned = False
    for binding in sorted(motif_bindings, key=lambda b: context.ordered_positions[b.id]):
        node = context.binding_nodes[binding.id]
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
    return _motif_result(wrapper, placed)


def _build_outline_branch(
    node_id: str,
    *,
    motif: MotifSpec,
    bindings_by_node_id: dict[str, list[BindingSpec]],
    motif_node_ids: set[str],
    context: MotifCompileContext,
    active_path: tuple[str, ...],
    emitted_node_ids: set[str],
) -> IRNode | None:
    if node_id in active_path:
        cycle_path = " -> ".join((*active_path, node_id))
        add_diagnostic(
            context.diagnostics,
            "SEMANTIC_GRAPH_CYCLE",
            f"Outline motif {motif.id} skipped cyclic semantic edge at {node_id}: {cycle_path}.",
            intent_ref=motif_ref(motif.id),
            content_ref=f"node:{node_id}",
            region_id=motif.region,
        )
        return None
    if node_id in emitted_node_ids:
        add_diagnostic(
            context.diagnostics,
            "SEMANTIC_GRAPH_SHARED_NODE",
            f"Outline motif {motif.id} skipped repeated semantic node {node_id} to keep IR node ids unique.",
            intent_ref=motif_ref(motif.id),
            content_ref=f"node:{node_id}",
            region_id=motif.region,
        )
        return None

    emitted_node_ids.add(node_id)
    child_path = (*active_path, node_id)
    motif_r = motif_ref(motif.id)
    branch = IRNode(
        id=f"motif_{motif.id}_branch_{node_id}",
        primitive="surface",
        props={"layout_role": "surface", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    for binding in sorted(
        bindings_by_node_id.get(node_id, []),
        key=lambda b: context.ordered_positions[b.id],
    ):
        branch.children.append(context.binding_nodes[binding.id])
    for child_id in _semantic_children(context.substrate, node_id):
        if child_id in motif_node_ids:
            child = _build_outline_branch(
                child_id,
                motif=motif,
                bindings_by_node_id=bindings_by_node_id,
                motif_node_ids=motif_node_ids,
                context=context,
                active_path=child_path,
                emitted_node_ids=emitted_node_ids,
            )
            if child is not None:
                branch.children.append(child)
    return branch


def _build_outline_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="stack",
        props={"layout_role": "stack", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    motif_node_ids = {_binding_node_id(binding) for binding in motif_bindings}
    outline_bindings = [
        binding
        for binding in context.bindings_by_region.get(motif.region, [])
        if _binding_node_id(binding) in motif_node_ids
    ]
    bindings_by_node_id: dict[str, list[BindingSpec]] = {}
    for binding in outline_bindings:
        bindings_by_node_id.setdefault(_binding_node_id(binding), []).append(binding)
        placed.add(binding.id)
    semantic_parent = _semantic_parent_by_id(context.substrate)
    top_level = [node_id for node_id in motif_node_ids if semantic_parent.get(node_id) not in motif_node_ids]

    def _min_pos(node_id: str) -> int:
        node_bindings = bindings_by_node_id.get(node_id)
        if node_bindings:
            return min(context.ordered_positions[binding.id] for binding in node_bindings)
        return 10**9

    top_level.sort(key=_min_pos)
    if motif_node_ids and not top_level:
        add_diagnostic(
            context.diagnostics,
            "SEMANTIC_GRAPH_CYCLE",
            f"Outline motif {motif.id} has no acyclic top-level semantic node; "
            "using declaration order and skipping cyclic repeats.",
            intent_ref=motif_ref(motif.id),
            region_id=motif.region,
        )
        top_level = sorted(motif_node_ids, key=_min_pos)

    emitted_outline_nodes: set[str] = set()
    for node_id in top_level:
        branch = _build_outline_branch(
            node_id,
            motif=motif,
            bindings_by_node_id=bindings_by_node_id,
            motif_node_ids=motif_node_ids,
            context=context,
            active_path=(),
            emitted_node_ids=emitted_outline_nodes,
        )
        if branch is not None:
            wrapper.children.append(branch)
    return _motif_result(wrapper, placed)


def _build_comparison_motif(
    motif: MotifSpec, motif_bindings: list[BindingSpec], context: MotifCompileContext
) -> MotifCompileResult:
    motif_r = motif_ref(motif.id)
    wrapper = IRNode(
        id=f"motif_{motif.id}",
        primitive="cluster",
        props={"layout_role": "cluster", "motif_kind": motif.kind},
        provenance=Provenance(intent_refs=[motif_r]),
    )
    placed: set[str] = set()
    for node_id, grouped in _bindings_by_semantic_node(motif_bindings, context.ordered_positions):
        panel = IRNode(
            id=f"motif_{motif.id}_{node_id}",
            primitive="stack",
            props={"layout_role": "stack", "motif_kind": motif.kind},
            provenance=Provenance(intent_refs=[motif_r]),
        )
        for binding in grouped:
            panel.children.append(context.binding_nodes[binding.id])
            placed.add(binding.id)
        wrapper.children.append(panel)
    return _motif_result(wrapper, placed)


BUILTIN_MOTIF_COMPILERS = (
    MotifCompiler(kinds=("table",), build=_build_table_motif),
    MotifCompiler(kinds=("dashboard",), build=_build_dashboard_motif),
    MotifCompiler(kinds=("outline",), build=_build_outline_motif),
    MotifCompiler(kinds=("comparison",), build=_build_comparison_motif),
    MotifCompiler(kinds=("list",), build=_build_list_motif),
    MotifCompiler(kinds=("form",), build=_build_form_motif),
    MotifCompiler(kinds=("detail",), build=_build_detail_motif),
    MotifCompiler(kinds=("empty_state",), build=_build_empty_state_motif),
    MotifCompiler(
        kinds=("loading_state", "error_state"),
        build=_build_state_motif,
        validate=_validate_state_motif_contract,
    ),
    MotifCompiler(kinds=("hero",), build=_build_hero_motif),
)
MOTIF_COMPILER_REGISTRY = _register_motif_compilers(BUILTIN_MOTIF_COMPILERS)
SUPPORTED_MOTIF_KINDS = tuple(MOTIF_COMPILER_REGISTRY)
BUILTIN_MOTIF_KIND_SET = frozenset(SUPPORTED_MOTIF_KINDS)
BUILTIN_MOTIF_REGISTRY = MotifRegistry(
    compilers=MOTIF_COMPILER_REGISTRY,
    builtin_kinds=BUILTIN_MOTIF_KIND_SET,
)


__all__ = [
    "MOTIF_PLUGIN_ABI_VERSION",
    "BUILTIN_MOTIF_COMPILERS",
    "BUILTIN_MOTIF_KIND_SET",
    "BUILTIN_MOTIF_REGISTRY",
    "MOTIF_COMPILER_REGISTRY",
    "SUPPORTED_MOTIF_KINDS",
    "MotifBuildFn",
    "MotifCompileContext",
    "MotifCompileResult",
    "MotifCompiler",
    "MotifPlugin",
    "MotifPluginCheckIssue",
    "MotifPluginCheckReport",
    "MotifPluginError",
    "MotifPluginFixture",
    "MotifPluginManifest",
    "MotifPluginSlot",
    "MotifRegistry",
    "MotifValidateFn",
    "builtin_motif_registry",
    "check_motif_plugin",
    "create_motif_registry",
    "motif_compiler_registry",
]
