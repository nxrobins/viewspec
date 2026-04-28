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
