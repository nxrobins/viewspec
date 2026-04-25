"""Tests for the reference compiler."""

import pytest
from viewspec import ViewSpecBuilder, compile, UnsupportedMotifError
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter


def _count_nodes(node):
    """Recursively count IR nodes."""
    return 1 + sum(_count_nodes(c) for c in node.children)


def test_table_compile():
    builder = ViewSpecBuilder("invoice")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Widget A", value="$50")
    table.add_row(label="Widget B", value="$120")

    ast = compile(builder.build_bundle())
    assert len(ast.result.diagnostics) == 0
    assert ast.result.root.root.primitive == "root"
    # root → motif_wrapper → 2 row clusters → 2 bindings each = 1+1+2+4 = 8
    assert _count_nodes(ast.result.root.root) >= 7


def test_dashboard_compile():
    builder = ViewSpecBuilder("metrics")
    dash = builder.add_dashboard("kpis", region="main", group_id="cards")
    dash.add_card(label="Revenue", value="$2.4M")
    dash.add_card(label="Users", value="18K")

    ast = compile(builder.build_bundle())
    assert len(ast.result.diagnostics) == 0
    # root → region_main → motif_wrapper → cards
    root = ast.result.root.root
    region_main = root.children[0]
    motif_wrapper = region_main.children[0]
    assert motif_wrapper.props.get("motif_kind") == "dashboard"
    for card in motif_wrapper.children:
        assert card.primitive == "surface"


def test_comparison_compile():
    builder = ViewSpecBuilder("pricing")
    comp = builder.add_comparison("plans", region="main", group_id="tiers")
    comp.add_item(label="Free", value="$0")
    comp.add_item(label="Pro", value="$99")

    ast = compile(builder.build_bundle())
    assert len(ast.result.diagnostics) == 0
    root = ast.result.root.root
    region_main = root.children[0]
    motif_wrapper = region_main.children[0]
    assert motif_wrapper.primitive == "cluster"
    assert motif_wrapper.props.get("motif_kind") == "comparison"


def test_style_application():
    builder = ViewSpecBuilder("styled")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$100")
    builder.add_style("s1", "items_row_1_label", "tone.muted")

    ast = compile(builder.build_bundle())
    assert len(ast.result.diagnostics) == 0
    # Find the binding node for the styled item
    root = ast.result.root.root
    region_main = root.children[0]
    motif = region_main.children[0]
    row = motif.children[0]
    label_node = [c for c in row.children if c.primitive == "label"][0]
    assert "tone.muted" in label_node.style_tokens


def test_provenance_complete():
    builder = ViewSpecBuilder("provenanced")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="A", value="1")
    table.add_row(label="B", value="2")

    ast = compile(builder.build_bundle())
    root = ast.result.root.root

    def _check_provenance(node):
        if node.primitive not in {"root", "stack", "grid", "cluster", "surface"}:
            # Leaf binding nodes must have content_refs
            assert len(node.provenance.content_refs) > 0, f"{node.id} missing content_refs"
        assert len(node.provenance.intent_refs) > 0, f"{node.id} missing intent_refs"
        for child in node.children:
            _check_provenance(child)

    _check_provenance(root)


def test_style_tokens_generated():
    builder = ViewSpecBuilder("tokens")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")

    ast = compile(builder.build_bundle())
    assert "emphasis.high" in ast.style_values
    assert "tone.muted" in ast.style_values
    assert "density.compact" in ast.style_values


def test_full_pipeline_emit():
    """End-to-end: build → compile → emit HTML."""
    import tempfile, os

    builder = ViewSpecBuilder("e2e_test")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Test Item", value="$42")

    ast = compile(builder.build_bundle())
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = HtmlTailwindEmitter().emit(ast, tmpdir)
        assert os.path.exists(paths["html"])
        html = open(paths["html"]).read()
        assert "Test Item" in html
        assert "$42" in html
        assert "<!DOCTYPE html>" in html


def test_unsupported_motif_raises():
    """Reference compiler rejects unknown motif kinds."""
    builder = ViewSpecBuilder("unknown")
    bundle = builder.build_bundle()
    # Manually inject an unsupported motif
    from viewspec.types import MotifSpec
    bundle.view_spec.motifs.append(MotifSpec(
        id="fancy", kind="neural_layout", region="main", members=[]
    ))

    with pytest.raises(UnsupportedMotifError, match="neural_layout"):
        compile(bundle)
