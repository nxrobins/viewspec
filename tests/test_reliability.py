from __future__ import annotations

from dataclasses import replace

import pytest

from viewspec import (
    CompilerInputError,
    IntentBundle,
    SemanticSubstrate,
    ViewSpecBuilder,
    compile,
)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _content_refs(root):
    refs = []
    for node in _walk(root):
        refs.extend(node.provenance.content_refs)
    return refs


def _ir_ids(root):
    return [node.id for node in _walk(root)]


@pytest.mark.parametrize("motif_name", ["table", "dashboard", "comparison", "outline"])
def test_valid_motifs_route_each_binding_exactly_once(motif_name):
    builder = ViewSpecBuilder(f"{motif_name}_reliability")
    if motif_name == "table":
        motif = builder.add_table("items", region="main", group_id="order")
        motif.add_row(label="Alpha", value="1", id="alpha")
        motif.add_row(label="Beta", value="2", id="beta")
    elif motif_name == "dashboard":
        motif = builder.add_dashboard("cards", region="main", group_id="order")
        motif.add_card(label="Alpha", value="1", id="alpha")
        motif.add_card(label="Beta", value="2", id="beta")
    elif motif_name == "comparison":
        motif = builder.add_comparison("plans", region="main", group_id="order")
        motif.add_item(label="Alpha", value="1", id="alpha")
        motif.add_item(label="Beta", value="2", id="beta")
    else:
        motif = builder.add_outline("tree", region="main", group_id="order")
        motif.add_branch(label="Alpha", id="alpha")
        motif.add_branch(label="Beta", id="beta")

    bundle = builder.build_bundle()
    ast = compile(bundle)
    root = ast.result.root.root
    refs = _content_refs(root)

    assert not ast.result.diagnostics
    assert sorted(refs) == sorted(binding.address for binding in bundle.view_spec.bindings)
    assert len(refs) == len(set(refs))
    ids = _ir_ids(root)
    assert len(ids) == len(set(ids))
    assert all(node.provenance.intent_refs for node in _walk(root))


def test_ordered_group_preserves_declared_binding_order():
    builder = ViewSpecBuilder("ordering")
    table = builder.add_table("items", region="main", group_id="order")
    table.add_row(label="First", value="1", id="first")
    table.add_row(label="Second", value="2", id="second")
    table.add_row(label="Third", value="3", id="third")

    ast = compile(builder.build_bundle())
    labels = [
        node.props["text"]
        for node in _walk(ast.result.root.root)
        if node.id.endswith("_label") and "text" in node.props
    ]

    assert labels == ["First", "Second", "Third"]


def test_outline_semantic_cycle_is_diagnostic_not_recursion_error():
    builder = ViewSpecBuilder("cycle_outline")
    outline = builder.add_outline("tree", region="main", group_id="branches")
    outline.add_branch(label="Alpha", id="alpha")
    outline.add_branch(label="Beta", id="beta")
    bundle = builder.build_bundle()
    root_id = bundle.substrate.root_id
    nodes = dict(bundle.substrate.nodes)
    nodes[root_id] = replace(nodes[root_id], slots={"items": ["alpha"]})
    nodes["alpha"] = replace(nodes["alpha"], slots={"items": ["beta"]})
    nodes["beta"] = replace(nodes["beta"], slots={"items": ["alpha"]})
    cyclic_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=root_id,
            nodes=nodes,
        ),
        view_spec=bundle.view_spec,
    )

    ast = compile(cyclic_bundle)
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}
    ids = _ir_ids(ast.result.root.root)

    assert "SEMANTIC_GRAPH_CYCLE" in codes
    assert len(ids) == len(set(ids))


def test_outline_shared_semantic_child_is_diagnostic_and_keeps_unique_ir_ids():
    builder = ViewSpecBuilder("shared_outline")
    outline = builder.add_outline("tree", region="main", group_id="branches")
    outline.add_branch(label="Alpha", id="alpha")
    outline.add_branch(label="Beta", id="beta")
    outline.add_branch(label="Gamma", id="gamma")
    bundle = builder.build_bundle()
    root_id = bundle.substrate.root_id
    nodes = dict(bundle.substrate.nodes)
    nodes[root_id] = replace(nodes[root_id], slots={"items": ["alpha", "beta"]})
    nodes["alpha"] = replace(nodes["alpha"], slots={"items": ["gamma"]})
    nodes["beta"] = replace(nodes["beta"], slots={"items": ["gamma"]})
    shared_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=root_id,
            nodes=nodes,
        ),
        view_spec=bundle.view_spec,
    )

    ast = compile(shared_bundle)
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}
    ids = _ir_ids(ast.result.root.root)

    assert "SEMANTIC_GRAPH_SHARED_NODE" in codes
    assert ids.count("motif_tree_branch_gamma") == 1
    assert len(ids) == len(set(ids))


def test_recoverable_malformed_bundle_returns_diagnostics():
    builder = ViewSpecBuilder("diagnostics")
    table = builder.add_table("items", region="main", group_id="order")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_binding("alpha_label", "node:alpha#attr:value", region="main", present_as="value")
    builder.add_binding("duplicate_address", "node:alpha#attr:value", region="main", present_as="value")
    builder.add_binding("bad_address", "node:missing#attr:value", region="main", present_as="value")
    builder.add_binding("bad_region", "node:alpha#attr:label", region="missing", present_as="label")
    builder.add_binding("bad_present_as", "node:alpha#attr:label", region="main", present_as="chart")
    builder.add_group("bad_group", "ordered", ["missing_group_member"], target_region="main")
    builder.add_motif("bad_motif", "table", "main", ["missing_motif_member"])
    builder.add_style("bad_style", "binding:missing_style_target", "tone.muted")
    builder.add_action(
        "bad_action",
        "select",
        "Select",
        target_region="missing_region",
        target_ref="binding:missing_action_target",
        payload_bindings=["missing_payload"],
    )

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "DUPLICATE_BINDING_ID" in codes
    assert "DUPLICATE_EXACTLY_ONCE_ADDRESS" in codes
    assert "INVALID_ADDRESS" in codes
    assert "UNKNOWN_REGION" in codes
    assert "UNKNOWN_PRESENT_AS" in codes
    assert "MISSING_GROUP_MEMBER" in codes
    assert "MISSING_MOTIF_MEMBER" in codes
    assert "UNKNOWN_STYLE_TARGET" in codes
    assert "UNKNOWN_ACTION_TARGET" in codes
    assert "UNKNOWN_ACTION_PAYLOAD_BINDING" in codes
    assert "node:alpha#attr:label" in _content_refs(ast.result.root.root)


def test_missing_root_region_is_fatal():
    bundle = ViewSpecBuilder("bad_root").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, root_region="missing"),
    )

    with pytest.raises(CompilerInputError, match="root_region"):
        compile(bad_bundle)


def test_region_tree_cycle_is_fatal():
    bundle = ViewSpecBuilder("bad_region_cycle").build_bundle()
    regions = list(bundle.view_spec.regions)
    regions[0] = replace(regions[0], parent_region="main")
    regions[1] = replace(regions[1], parent_region="root")
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=regions),
    )

    with pytest.raises(CompilerInputError, match="must not declare parent_region"):
        compile(bad_bundle)


def test_detached_region_is_fatal():
    bundle = ViewSpecBuilder("bad_detached_region").build_bundle()
    regions = list(bundle.view_spec.regions)
    regions[1] = replace(regions[1], parent_region="")
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=regions),
    )

    with pytest.raises(CompilerInputError, match="must declare parent_region"):
        compile(bad_bundle)


def test_missing_substrate_root_is_fatal():
    bundle = ViewSpecBuilder("bad_substrate").build_bundle()
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id="missing",
            nodes=bundle.substrate.nodes,
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="root_id"):
        compile(bad_bundle)


def test_compile_rejects_non_intent_bundle():
    with pytest.raises(CompilerInputError, match="IntentBundle"):
        compile({"substrate": {}, "view_spec": {}})


def test_substrate_nodes_shape_is_fatal():
    bundle = ViewSpecBuilder("bad_nodes_shape").build_bundle()
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=bundle.substrate.root_id,
            nodes={"root": {"id": "root"}},
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="SemanticNode"):
        compile(bad_bundle)


def test_view_spec_collection_shape_is_fatal():
    bundle = ViewSpecBuilder("bad_collection_shape").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, bindings=None),
    )

    with pytest.raises(CompilerInputError, match="ViewSpec.bindings"):
        compile(bad_bundle)


def test_view_spec_collection_item_shape_is_fatal():
    bundle = ViewSpecBuilder("bad_collection_item").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=[{}]),
    )

    with pytest.raises(CompilerInputError, match=r"ViewSpec\.regions\[0\]"):
        compile(bad_bundle)


def test_invalid_complexity_tier_is_fatal():
    bundle = ViewSpecBuilder("bad_complexity").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, complexity_tier=0),
    )

    with pytest.raises(CompilerInputError, match="complexity_tier"):
        compile(bad_bundle)


def test_invalid_region_child_bounds_are_fatal():
    bundle = ViewSpecBuilder("bad_region_bounds").build_bundle()
    regions = list(bundle.view_spec.regions)
    regions[1] = replace(regions[1], min_children=3, max_children=2)
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=regions),
    )

    with pytest.raises(CompilerInputError, match="max_children"):
        compile(bad_bundle)


def test_negative_region_child_bounds_are_fatal():
    bundle = ViewSpecBuilder("negative_region_bounds").build_bundle()
    regions = list(bundle.view_spec.regions)
    regions[1] = replace(regions[1], min_children=-1)
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=regions),
    )

    with pytest.raises(CompilerInputError, match="min_children"):
        compile(bad_bundle)


def test_unknown_region_layout_is_fatal():
    bundle = ViewSpecBuilder("bad_region_layout").build_bundle()
    regions = list(bundle.view_spec.regions)
    regions[1] = replace(regions[1], layout="masonry")
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, regions=regions),
    )

    with pytest.raises(CompilerInputError, match="layout"):
        compile(bad_bundle)


def test_unsupported_binding_cardinality_is_fatal():
    builder = ViewSpecBuilder("bad_cardinality")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle = builder.build_bundle()
    bindings = list(bundle.view_spec.bindings)
    bindings[0] = replace(bindings[0], cardinality="optional")
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, bindings=bindings),
    )

    with pytest.raises(CompilerInputError, match="cardinality"):
        compile(bad_bundle)


def test_unsupported_group_kind_is_fatal():
    builder = ViewSpecBuilder("bad_group_kind")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle = builder.build_bundle()
    groups = list(bundle.view_spec.groups)
    groups[0] = replace(groups[0], kind="unordered")
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, groups=groups),
    )

    with pytest.raises(CompilerInputError, match="kind"):
        compile(bad_bundle)


def test_group_members_shape_is_fatal():
    builder = ViewSpecBuilder("bad_group_members")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle = builder.build_bundle()
    groups = list(bundle.view_spec.groups)
    groups[0] = replace(groups[0], members=None)
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, groups=groups),
    )

    with pytest.raises(CompilerInputError, match="members"):
        compile(bad_bundle)


def test_style_shape_errors_are_fatal_before_style_application():
    builder = ViewSpecBuilder("bad_style_shape")
    builder.add_style("broken_style", "view:bad_style_shape", "tone.neutral")
    bundle = builder.build_bundle()
    styles = list(bundle.view_spec.styles)
    styles[0] = replace(styles[0], token=None)
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, styles=styles),
    )

    with pytest.raises(CompilerInputError, match="Style"):
        compile(bad_bundle)


def test_ambiguous_bare_style_target_is_diagnostic_and_not_applied():
    builder = ViewSpecBuilder("ambiguous_style")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_node("style_source", "metric", attrs={"label": "Style source"})
    builder.add_binding("main", "node:style_source#attr:label", region="main", present_as="label")
    builder.add_style("ambiguous", "main", "tone.accent")

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}
    root = ast.result.root.root
    nodes_by_id = {node.id: node for node in _walk(root)}

    assert "AMBIGUOUS_STYLE_TARGET" in codes
    assert "tone.accent" not in nodes_by_id["region_main"].style_tokens
    assert "tone.accent" not in nodes_by_id["binding_main"].style_tokens


def test_action_target_ref_shape_is_fatal():
    builder = ViewSpecBuilder("bad_action_target_ref")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_action("open_alpha", "select", "Open", target_region="main")
    bundle = builder.build_bundle()
    actions = list(bundle.view_spec.actions)
    actions[0] = replace(actions[0], target_ref=["binding:alpha_label"])
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, actions=actions),
    )

    with pytest.raises(CompilerInputError, match="target_ref"):
        compile(bad_bundle)


def test_action_payload_bindings_shape_is_fatal():
    builder = ViewSpecBuilder("bad_action_payload_shape")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_action("open_alpha", "select", "Open", target_region="main")
    bundle = builder.build_bundle()
    actions = list(bundle.view_spec.actions)
    actions[0] = replace(actions[0], payload_bindings=None)
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, actions=actions),
    )

    with pytest.raises(CompilerInputError, match="payload_bindings"):
        compile(bad_bundle)


def test_view_spec_substrate_id_mismatch_is_fatal():
    bundle = ViewSpecBuilder("bad_substrate_identity").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, substrate_id="different_substrate"),
    )

    with pytest.raises(CompilerInputError, match="substrate_id"):
        compile(bad_bundle)


def test_substrate_node_key_mismatch_is_fatal():
    bundle = ViewSpecBuilder("bad_node_identity").build_bundle()
    root_node = bundle.substrate.nodes[bundle.substrate.root_id]
    bad_nodes = dict(bundle.substrate.nodes)
    bad_nodes[bundle.substrate.root_id] = replace(root_node, id="different_root")
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=bundle.substrate.root_id,
            nodes=bad_nodes,
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="must match SemanticNode.id"):
        compile(bad_bundle)


def test_substrate_node_attrs_shape_is_fatal():
    bundle = ViewSpecBuilder("bad_node_attrs").build_bundle()
    root_node = bundle.substrate.nodes[bundle.substrate.root_id]
    bad_nodes = dict(bundle.substrate.nodes)
    bad_nodes[bundle.substrate.root_id] = replace(root_node, attrs=None)
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=bundle.substrate.root_id,
            nodes=bad_nodes,
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="attrs"):
        compile(bad_bundle)


def test_substrate_node_relation_values_shape_is_fatal():
    bundle = ViewSpecBuilder("bad_node_relations").build_bundle()
    root_node = bundle.substrate.nodes[bundle.substrate.root_id]
    bad_nodes = dict(bundle.substrate.nodes)
    bad_nodes[bundle.substrate.root_id] = replace(root_node, slots={"items": "not_a_list"})
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=bundle.substrate.root_id,
            nodes=bad_nodes,
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="slots"):
        compile(bad_bundle)


def test_missing_semantic_edge_target_is_fatal():
    bundle = ViewSpecBuilder("bad_edge_target").build_bundle()
    root_node = bundle.substrate.nodes[bundle.substrate.root_id]
    bad_nodes = dict(bundle.substrate.nodes)
    bad_nodes[bundle.substrate.root_id] = replace(root_node, edges={"next": ["missing_node"]})
    bad_bundle = IntentBundle(
        substrate=SemanticSubstrate(
            id=bundle.substrate.id,
            root_id=bundle.substrate.root_id,
            nodes=bad_nodes,
        ),
        view_spec=bundle.view_spec,
    )

    with pytest.raises(CompilerInputError, match="must reference a declared substrate node"):
        compile(bad_bundle)


def test_invalid_programmatic_id_is_fatal():
    bundle = ViewSpecBuilder("bad_ids").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, id="bad/view"),
    )

    with pytest.raises(CompilerInputError, match="ViewSpec.id"):
        compile(bad_bundle)


def test_non_string_programmatic_id_is_fatal():
    bundle = ViewSpecBuilder("bad_typed_ids").build_bundle()
    bad_bundle = IntentBundle(
        substrate=bundle.substrate,
        view_spec=replace(bundle.view_spec, id=42),
    )

    with pytest.raises(CompilerInputError, match="ViewSpec.id"):
        compile(bad_bundle)
