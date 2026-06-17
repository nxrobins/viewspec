"""Tests for the reference compiler."""

import json
import re
from itertools import combinations
from pathlib import Path

import pytest

from viewspec import AESTHETIC_PROFILE_TOKENS, UnsupportedMotifError, ViewSpecBuilder, compile
from viewspec.agent import SUPPORTED_AGENT_ACTION_KINDS, SUPPORTED_AGENT_STYLE_TOKENS
from viewspec.aesthetics import (
    AESTHETIC_PROFILE_LAYOUT_ROLES,
    MIN_AESTHETIC_PROFILE_CATEGORIES,
    MIN_AESTHETIC_PROFILE_STYLE_CHANGES,
    profile_layout_props,
    profile_style_facts,
    profile_style_values,
)
from viewspec.compiler import PRODUCT_SURFACE_PLANNER_V1_SURFACE, SUPPORTED_ACTION_KINDS
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT, HtmlTailwindEmitter
from viewspec.types import DEFAULT_STYLE_TOKEN_VALUES


def _count_nodes(node):
    """Recursively count IR nodes."""
    return 1 + sum(_count_nodes(c) for c in node.children)


def _find_nodes(node, primitive):
    matches = [node] if node.primitive == primitive else []
    for child in node.children:
        matches.extend(_find_nodes(child, primitive))
    return matches


def _find_node_id(node, node_id):
    if node.id == node_id:
        return node
    for child in node.children:
        match = _find_node_id(child, node_id)
        if match is not None:
            return match
    return None


def _walk_nodes(node):
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk_nodes(child))
    return nodes


def _product_workspace_bundle(*, duplicate_header=False, missing_header=False, extra_body_child=False, profile=None):
    builder = ViewSpecBuilder(
        "product_surface_shape",
        root_attrs={"title": "Surface"},
        default_main_region=False,
        root_min_children=2,
    )
    if profile is not None:
        builder.set_aesthetic_profile(profile)
    if not missing_header:
        builder.add_region("north", parent_region="root", role="banner", layout="stack", min_children=1)
    if duplicate_header:
        builder.add_region("north_alt", parent_region="root", role="page_header", layout="stack", min_children=0)
    builder.add_region("canvas", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("focus", parent_region="canvas", role="primary", layout="stack", min_children=2)
    builder.add_region("assist", parent_region="canvas", role="complementary", layout="stack", min_children=1)
    if extra_body_child:
        builder.add_region("overflow", parent_region="canvas", role="secondary", layout="stack", min_children=0)

    if not missing_header:
        builder.add_hero(
            "intro",
            eyebrow="Quality",
            title="Review workspace",
            description="Compiler-owned product surface roles.",
            region="north",
            group_id="intro_group",
        )
    dashboard = builder.add_dashboard("numbers", region="focus", group_id="metric_group")
    dashboard.add_card(label="Fixtures", value="6", id="fixtures")
    dashboard.add_card(label="Emitters", value="2", id="emitters")
    form = builder.add_form("review_form", region="focus", group_id="review_group")
    form.add_field(label="Reviewer", value="", id="reviewer")
    form.add_field(label="Decision", value="approve", id="decision")
    detail = builder.add_detail("identity", region="assist", group_id="identity_group")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    detail.add_field(label="Network", value="none", id="network")
    builder.add_action(
        "submit_review",
        "submit",
        "Submit review",
        target_region="focus",
        target_ref="motif:review_form",
        payload_bindings=["reviewer_value", "decision_value"],
    )
    return builder.build_bundle()


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


def test_table_compile_emits_semantic_table_contract(tmp_path):
    builder = ViewSpecBuilder("invoice")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Widget A", value="$50", id="widget_a")

    ast = compile(builder.build_bundle())
    labels = _find_nodes(ast.result.root.root, "label")
    values = _find_nodes(ast.result.root.root, "value")

    assert len(ast.result.diagnostics) == 0
    assert labels[0].props["table_cell_role"] == "row_header"
    assert values[0].props["table_cell_role"] == "cell"

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    header_tag = html[html.index('id="dom-binding_widget_a_label"') : html.index(">", html.index('id="dom-binding_widget_a_label"'))]

    assert '<table id="dom-motif_items"' in html
    assert "<tbody><tr " in html
    assert '<tr id="dom-motif_items_widget_a"' in html
    assert '<th id="dom-binding_widget_a_label"' in html
    assert 'scope="row"' in header_tag
    assert '<td id="dom-binding_widget_a_value"' in html


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
    assert motif_wrapper.primitive == "grid"
    assert motif_wrapper.props.get("motif_kind") == "dashboard"
    assert motif_wrapper.props["columns"] == 2
    assert motif_wrapper.props["layout_strategy"] == "dashboard_grid_v0"
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


def test_list_compile(tmp_path):
    builder = ViewSpecBuilder("tasks")
    items = builder.add_list("next_steps", region="main", group_id="steps")
    items.add_item(label="Plan", description="Define the UI intent", id="plan")
    items.add_item(label="Build", description="Compile the artifact", id="build")

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    region_main = root.children[0]
    motif_wrapper = region_main.children[0]

    assert len(ast.result.diagnostics) == 0
    assert motif_wrapper.primitive == "stack"
    assert motif_wrapper.props.get("motif_kind") == "list"
    assert [item.primitive for item in motif_wrapper.children] == ["surface", "surface"]
    assert motif_wrapper.children[0].children[0].props["text"] == "Plan"

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert '<ul id="dom-motif_next_steps"' in html
    assert '<li id="dom-motif_next_steps_plan"' in html


def test_form_compile_emits_inert_form_role_contract(tmp_path):
    builder = ViewSpecBuilder("contact")
    form = builder.add_form("contact_form", region="main", group_id="fields")
    form.add_field(label="Name", value="Ada", id="name")
    form.add_field(label="Email", value="ada@example.com", id="email")
    builder.add_action(
        "submit_contact",
        "submit",
        "Submit",
        target_region="main",
        target_ref="motif:contact_form",
        payload_bindings=["name_value", "email_value"],
    )

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    form_wrapper = root.children[0].children[0]
    inputs = _find_nodes(root, "input")

    assert len(ast.result.diagnostics) == 0
    assert form_wrapper.primitive == "stack"
    assert form_wrapper.props["motif_kind"] == "form"
    fields = [child for child in form_wrapper.children if child.primitive == "surface"]
    buttons = [child for child in form_wrapper.children if child.primitive == "button"]
    assert [field.props["field_id"] for field in fields] == ["name", "email"]
    assert len(buttons) == 1
    assert buttons[0].props["placement"] == "motif_local"
    assert [node.props["aria_label"] for node in inputs] == ["Name", "Email"]

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert "<form" not in html
    assert '<section id="dom-motif_contact_form"' in html
    assert 'role="form"' in html
    assert 'role="group"' in html
    assert 'data-action-target-ref="motif:contact_form"' in html
    assert 'data-payload-bindings="[&quot;name_value&quot;, &quot;email_value&quot;]"' in html
    assert "document.addEventListener('keydown'" in html
    assert 'data-action-kind="submit"' in html


def test_detail_compile_emits_definition_list_contract(tmp_path):
    builder = ViewSpecBuilder("profile")
    detail = builder.add_detail("profile_details", region="main", group_id="fields")
    detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
    detail.add_field(label="Status", value="Ready", id="status")

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    detail_wrapper = root.children[0].children[0]

    assert len(ast.result.diagnostics) == 0
    assert detail_wrapper.primitive == "stack"
    assert detail_wrapper.props["motif_kind"] == "detail"
    assert [row.primitive for row in detail_wrapper.children] == ["cluster", "cluster"]
    assert detail_wrapper.children[0].children[0].props["detail_role"] == "term"
    assert detail_wrapper.children[0].children[1].props["detail_role"] == "description"

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert '<dl id="dom-motif_profile_details"' in html
    assert '<div id="dom-motif_profile_details_owner"' in html
    assert '<dt id="dom-binding_owner_label"' in html
    assert '<dd id="dom-binding_owner_value"' in html
    assert "Ada Lovelace" in html


def test_empty_state_compile_emits_section_heading_contract(tmp_path):
    builder = ViewSpecBuilder("search")
    builder.add_empty_state(
        "no_results",
        title="No results yet",
        description="Adjust filters or create the first item.",
        region="main",
        group_id="message",
    )

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    empty_state = root.children[0].children[0]

    assert len(ast.result.diagnostics) == 0
    assert empty_state.primitive == "surface"
    assert empty_state.props["motif_kind"] == "empty_state"
    assert [child.props["empty_state_role"] for child in empty_state.children] == ["title", "description"]

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert '<section id="dom-motif_no_results"' in html
    assert 'aria-label="Empty state"' in html
    assert '<h2 id="dom-binding_no_results_title"' in html
    assert '<p id="dom-binding_no_results_description"' in html
    assert "No results yet" in html
    assert "Adjust filters or create the first item." in html


@pytest.mark.parametrize(
    ("kind", "method_name", "motif_id", "role_attr", "aria_busy"),
    [
        ("loading_state", "add_loading_state", "loading_results", 'role="status"', 'aria-busy="true"'),
        ("error_state", "add_error_state", "error_results", 'role="alert"', None),
    ],
)
def test_state_motifs_compile_to_checked_status_or_alert_sections(tmp_path, kind, method_name, motif_id, role_attr, aria_busy):
    builder = ViewSpecBuilder(f"{kind}_view")
    getattr(builder, method_name)(
        motif_id,
        title="Collection unavailable" if kind == "error_state" else "Loading collection",
        description="Current state description.",
        region="main",
        group_id="message",
    )

    ast = compile(builder.build_bundle())
    state = _find_node_id(ast.result.root.root, f"motif_{motif_id}")

    assert len(ast.result.diagnostics) == 0
    assert state is not None
    assert state.primitive == "surface"
    assert state.props["motif_kind"] == kind
    assert state.props["state_role"] == ("loading" if kind == "loading_state" else "error")
    assert [child.props["state_motif_role"] for child in state.children] == ["title", "description"]

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert f'<section id="dom-motif_{motif_id}"' in html
    assert role_attr in html
    if aria_busy is not None:
        assert aria_busy in html
    assert f'<h2 id="dom-binding_{motif_id}_title"' in html
    assert f'<p id="dom-binding_{motif_id}_description"' in html


def test_hero_compile_emits_header_heading_contract(tmp_path):
    builder = ViewSpecBuilder("landing")
    builder.add_hero(
        "intro",
        eyebrow="Agent-native UI",
        title="Stop writing DOM",
        description="ViewSpec compiles intent into checked UI artifacts.",
        region="main",
        group_id="message",
    )

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    hero = root.children[0].children[0]

    assert len(ast.result.diagnostics) == 0
    assert hero.primitive == "surface"
    assert hero.props["motif_kind"] == "hero"
    assert [child.props["hero_role"] for child in hero.children] == ["eyebrow", "title", "description"]

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert '<header id="dom-motif_intro"' in html
    assert 'aria-label="Hero"' in html
    assert '<h1 id="dom-binding_intro_title"' in html
    assert '<p id="dom-binding_intro_description"' in html
    assert "Stop writing DOM" in html
    assert "ViewSpec compiles intent into checked UI artifacts." in html


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
    assert "tone.neutral" in ast.style_values
    assert "palette.temperature" in ast.style_values
    assert "tone.warning" in ast.style_values
    assert "tone.positive" in ast.style_values
    assert "rhythm.hierarchy" in ast.style_values
    assert "narrative.flow" in ast.style_values


def test_agent_supported_style_tokens_are_compiler_known():
    builder = ViewSpecBuilder("all_tokens")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    for index, token in enumerate(SUPPORTED_AGENT_STYLE_TOKENS):
        if token.startswith("aesthetic."):
            continue
        builder.add_style(f"style_{index}", "binding:items_row_1_value", token)

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert set(SUPPORTED_AGENT_STYLE_TOKENS).issubset(set(DEFAULT_STYLE_TOKEN_VALUES) | set(AESTHETIC_PROFILE_TOKENS))
    assert "UNKNOWN_STYLE_TOKEN" not in codes


def test_aesthetic_profile_compiles_to_root_metadata_and_style_projection():
    builder = ViewSpecBuilder("styled_profile")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.set_aesthetic_profile("aesthetic.premium_saas")

    ast = compile(builder.build_bundle())
    root = ast.result.root.root

    assert ast.result.diagnostics == []
    assert root.props["aesthetic_profile"] == "aesthetic.premium_saas"
    assert "aesthetic.premium_saas" in root.style_tokens
    assert "viewspec:style:aesthetic_profile" in root.provenance.intent_refs
    assert ast.style_values["action.accent"].endswith("background-color: #4f46e5; color: #ffffff; border-radius: 999px;")
    assert ast.style_values["surface.subtle"].endswith(
        "border-radius: 18px; box-shadow: 0 20px 46px rgb(79 70 229 / 0.16);"
    )
    assert "font-size: 1.38rem" in ast.style_values["rhythm.hierarchy"]


@pytest.mark.parametrize(
    ("token", "target", "expected_code"),
    [
        ("aesthetic.brutalist", "view:bad_profile", "AESTHETIC_PROFILE_UNKNOWN"),
        ("aesthetic.calm_ops", "motif:items", "AESTHETIC_PROFILE_TARGET_INVALID"),
    ],
)
def test_aesthetic_profile_rejects_unknown_or_non_view_target(token, target, expected_code):
    builder = ViewSpecBuilder("bad_profile")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.add_style("profile", target, token)

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert expected_code in codes
    assert "aesthetic_profile" not in ast.result.root.root.props


def test_aesthetic_profile_rejects_multiple_profiles():
    builder = ViewSpecBuilder("duplicate_profile")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.add_style("profile_a", "view:duplicate_profile", "aesthetic.calm_ops")
    builder.add_style("profile_b", "view:duplicate_profile", "aesthetic.data_dense")

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "AESTHETIC_PROFILE_MULTIPLE" in codes
    assert "aesthetic_profile" not in ast.result.root.root.props


def test_aesthetic_profiles_change_minimum_checked_style_projection():
    for profile in AESTHETIC_PROFILE_TOKENS:
        values = profile_style_values(profile)
        changed = {
            token
            for token, css in values.items()
            if DEFAULT_STYLE_TOKEN_VALUES.get(token, "").strip() != css.strip()
        }
        categories = {token.split(".", 1)[0] for token in changed if "." in token}

        assert len(changed) >= MIN_AESTHETIC_PROFILE_STYLE_CHANGES
        assert len(categories) >= MIN_AESTHETIC_PROFILE_CATEGORIES


def test_aesthetic_profile_style_facts_are_bounded_non_css_metadata():
    for profile in AESTHETIC_PROFILE_TOKENS:
        facts = profile_style_facts(profile)

        assert facts["changed_token_count"] >= MIN_AESTHETIC_PROFILE_STYLE_CHANGES
        assert facts["category_count"] >= MIN_AESTHETIC_PROFILE_CATEGORIES
        assert len(facts["changed_tokens"]) == facts["changed_token_count"]
        assert len(facts["categories"]) == facts["category_count"]
        assert facts["changed_tokens"] == sorted(facts["changed_tokens"])
        assert facts["categories"] == sorted(facts["categories"])
        assert facts["declaration_count"] >= facts["changed_token_count"]
        assert not any(":" in token or ";" in token for token in facts["changed_tokens"])
        assert not any(":" in category or ";" in category for category in facts["categories"])


def test_aesthetic_profiles_are_pairwise_distinct_style_projections():
    for left, right in combinations(AESTHETIC_PROFILE_TOKENS, 2):
        left_values = profile_style_values(left)
        right_values = profile_style_values(right)
        changed = {
            token
            for token in set(left_values) | set(right_values)
            if left_values.get(token, DEFAULT_STYLE_TOKEN_VALUES.get(token, "")).strip()
            != right_values.get(token, DEFAULT_STYLE_TOKEN_VALUES.get(token, "")).strip()
        }
        categories = {token.split(".", 1)[0] for token in changed if "." in token}

        assert len(changed) >= 8, f"{left} and {right} are too visually similar"
        assert len(categories) >= 4, f"{left} and {right} do not differ across enough aesthetic categories"


def test_aesthetic_profiles_have_closed_layout_props():
    signatures = set()
    for profile in AESTHETIC_PROFILE_TOKENS:
        layout_props = profile_layout_props(profile)
        signatures.add(tuple(sorted((role, tuple(sorted(props.items()))) for role, props in layout_props.items())))

        assert set(layout_props).issubset(AESTHETIC_PROFILE_LAYOUT_ROLES)
        for role, props in layout_props.items():
            if role in {"content_grid", "metric_grid"}:
                assert set(props) == {"columns"}
                assert 1 <= props["columns"] <= 3
            elif role == "metric_card":
                assert set(props).issubset({"span_columns", "layout_emphasis"})
                assert props
                if "span_columns" in props:
                    assert 1 <= props["span_columns"] <= 3
                if "layout_emphasis" in props:
                    assert props["layout_emphasis"] == "featured"

    assert len(signatures) >= 3


def test_design_overrides_append_after_aesthetic_profile_defaults():
    builder = ViewSpecBuilder("brand_override")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    design = """---
name: Brand Override
colors:
  accent: "#AA00CC"
  background: "#FDF2F8"
---
"""

    ast = compile(builder.build_bundle(), design)

    assert ast.result.diagnostics == []
    assert ast.style_values["action.accent"].endswith("background-color: #AA00CC;")
    assert ast.style_values["action.accent"].index("#0f766e") < ast.style_values["action.accent"].index("#AA00CC")
    assert ast.style_values["palette.temperature"].endswith("background-color: #FDF2F8;")


def test_bundles_without_aesthetic_profile_omit_root_metadata():
    builder = ViewSpecBuilder("plain_style")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")

    ast = compile(builder.build_bundle())

    assert ast.result.diagnostics == []
    assert "aesthetic_profile" not in ast.result.root.root.props
    assert not any(token.startswith("aesthetic.") for token in ast.result.root.root.style_tokens)


def test_sdk_helper_emits_one_view_scoped_aesthetic_profile():
    builder = ViewSpecBuilder("sdk_profile")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    builder.set_aesthetic_profile("aesthetic.executive_review")
    bundle = builder.build_bundle()

    assert [(style.id, style.target, style.token) for style in bundle.view_spec.styles] == [
        ("aesthetic_profile", "view:sdk_profile", "aesthetic.executive_review")
    ]
    with pytest.raises(ValueError, match="profile must be one of"):
        builder.set_aesthetic_profile("aesthetic.unknown")


def test_agent_supported_action_kinds_are_compiler_known():
    assert SUPPORTED_AGENT_ACTION_KINDS == SUPPORTED_ACTION_KINDS


def test_unknown_style_token_is_diagnostic_and_not_attached():
    builder = ViewSpecBuilder("bad_style_token")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Item", value="$50")
    builder.add_style("bad_token", "binding:items_row_1_value", "css.position.fixed")

    ast = compile(builder.build_bundle())
    value_nodes = _find_nodes(ast.result.root.root, "value")
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "UNKNOWN_STYLE_TOKEN" in codes
    assert "css.position.fixed" not in value_nodes[0].style_tokens


def test_full_pipeline_emit():
    """End-to-end: build → compile → emit HTML."""
    import os
    import tempfile

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
        assert "<script>" not in html


def test_actions_compile_to_button_ir_and_html(tmp_path):
    builder = ViewSpecBuilder("actions")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action(
        "open_alpha",
        "select",
        "Open Alpha",
        target_region="main",
        target_ref="binding:alpha_label",
        payload_bindings=["alpha_label", "alpha_value"],
    )

    ast = compile(builder.build_bundle())
    buttons = _find_nodes(ast.result.root.root, "button")

    assert len(ast.result.diagnostics) == 0
    assert len(buttons) == 1
    assert buttons[0].props["text"] == "Open Alpha"
    assert buttons[0].props["target_ref"] == "binding:alpha_label"
    assert "placement" not in buttons[0].props
    assert buttons[0].props["payload_bindings"] == ["alpha_label", "alpha_value"]
    assert buttons[0].provenance.intent_refs == ["viewspec:action:open_alpha"]
    assert buttons[0].provenance.content_refs == ["node:alpha#attr:label", "node:alpha#attr:value"]

    paths = HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert "Open Alpha" in html
    assert 'type="button"' in html
    assert 'data-action-id="open_alpha"' in html
    assert 'data-action-target-ref="binding:alpha_label"' in html
    assert ACTION_EVENT_SCRIPT in html
    assert paths["html"] == str(tmp_path / "index.html")


def test_product_surface_planner_v1_applies_workspace_roles_without_fixture_ids():
    ast = compile(_product_workspace_bundle())
    root = ast.result.root.root
    nodes = {node.id: node for node in _walk_nodes(root)}

    assert len(ast.result.diagnostics) == 0
    assert root.props["planner_surface"] == PRODUCT_SURFACE_PLANNER_V1_SURFACE
    assert nodes["region_root"].props["product_role"] == "app_shell"
    assert nodes["region_north"].props["product_role"] == "app_header"
    assert nodes["region_canvas"].props["product_role"] == "content_grid"
    assert nodes["region_focus"].props["product_role"] == "primary_column"
    assert nodes["region_assist"].props["product_role"] == "side_rail"
    assert nodes["motif_intro"].props["product_role"] == "page_header"
    assert nodes["motif_numbers"].props["product_role"] == "metric_grid"
    assert [child.props.get("product_role") for child in nodes["motif_numbers"].children] == [
        "metric_card",
        "metric_card",
    ]
    assert nodes["motif_review_form"].props["product_role"] == "form_panel"
    assert [
        child.props.get("product_role")
        for child in nodes["motif_review_form"].children
        if child.id.startswith("motif_review_form_")
    ] == ["field_group", "field_group"]
    assert nodes["motif_identity"].props["product_role"] == "detail_panel"
    assert nodes["planner_review_form_actions"].props["product_role"] == "action_row"
    assert nodes["planner_review_form_actions"].primitive == "cluster"


@pytest.mark.parametrize(
    "shape_kwargs",
    [
        {"duplicate_header": True},
        {"missing_header": True},
        {"extra_body_child": True},
    ],
)
def test_product_surface_planner_v1_fails_closed_for_ambiguous_or_incomplete_workspace_shape(shape_kwargs):
    ast = compile(_product_workspace_bundle(**shape_kwargs))
    root = ast.result.root.root
    nodes = _walk_nodes(root)
    form_wrapper = _find_node_id(root, "motif_review_form")
    button = _find_node_id(root, "action_submit_review")

    assert len(ast.result.diagnostics) == 0
    assert all("product_role" not in node.props for node in nodes)
    assert "planner_surface" not in root.props
    assert _find_node_id(root, "planner_review_form_actions") is None
    assert form_wrapper is not None
    assert button is not None
    assert button in form_wrapper.children


def test_product_surface_planner_v1_action_row_preserves_action_contract():
    bundle = _product_workspace_bundle()
    before = json.dumps(bundle.to_json(), sort_keys=True)
    action = bundle.view_spec.actions[0]

    ast = compile(bundle)
    after = json.dumps(bundle.to_json(), sort_keys=True)
    root = ast.result.root.root
    row = _find_node_id(root, "planner_review_form_actions")
    button = _find_node_id(root, "action_submit_review")
    inputs = {node.props["binding_id"]: node for node in _find_nodes(root, "input")}

    assert before == after
    assert row is not None
    assert button is not None
    assert button in row.children
    assert [child.id for child in row.children] == ["action_submit_review"]
    assert button.props["text"] == action.label
    assert button.props["action_id"] == action.id
    assert button.props["action_kind"] == action.kind
    assert button.props["target_ref"] == action.target_ref
    assert button.props["payload_bindings"] == list(action.payload_bindings)
    assert button.provenance.intent_refs == ["viewspec:action:submit_review"]
    assert button.provenance.content_refs == ["node:reviewer#attr:value", "node:decision#attr:value"]
    assert inputs["reviewer_value"].props["value"] == ""
    assert inputs["decision_value"].props["value"] == "approve"


def test_product_surface_planner_v1_adds_no_synthetic_visible_content_or_actions():
    bundle = _product_workspace_bundle()
    allowed_text = {
        str(value)
        for node in bundle.substrate.nodes.values()
        for value in node.attrs.values()
        if value is not None
    }
    allowed_text.update(action.label for action in bundle.view_spec.actions)

    ast = compile(bundle)
    root = ast.result.root.root
    visible_text = {
        str(node.props["text"])
        for node in _walk_nodes(root)
        if node.primitive in {"badge", "button", "label", "text", "value"} and "text" in node.props
    }
    action_ids = [
        node.props["action_id"]
        for node in _walk_nodes(root)
        if node.primitive == "button" and node.props.get("action_id")
    ]

    assert visible_text.issubset(allowed_text)
    assert action_ids == [action.id for action in bundle.view_spec.actions]
    assert _find_nodes(root, "image_slot") == []
    assert _find_nodes(root, "svg") == []


@pytest.mark.parametrize(
    ("profile", "expected_content_columns", "expected_metric_columns", "expected_featured_span", "expected_layout_emphasis"),
    [
        ("aesthetic.data_dense", 3, 3, None, None),
        ("aesthetic.editorial_product", 2, 1, None, None),
        ("aesthetic.premium_saas", 2, 2, 2, "featured"),
        ("aesthetic.executive_review", 2, 2, 2, "featured"),
    ],
)
def test_aesthetic_profile_layout_props_adjust_columns_and_spans_without_semantic_drift(
    profile, expected_content_columns, expected_metric_columns, expected_featured_span, expected_layout_emphasis
):
    plain = compile(_product_workspace_bundle())
    profiled = compile(_product_workspace_bundle(profile=profile))
    plain_nodes = {node.id: node for node in _walk_nodes(plain.result.root.root)}
    profiled_nodes = {node.id: node for node in _walk_nodes(profiled.result.root.root)}

    assert plain.result.diagnostics == []
    assert profiled.result.diagnostics == []
    assert sorted(plain_nodes) == sorted(profiled_nodes)
    assert [child.id for child in plain_nodes["region_canvas"].children] == [
        child.id for child in profiled_nodes["region_canvas"].children
    ]
    assert [child.id for child in plain_nodes["motif_numbers"].children] == [
        child.id for child in profiled_nodes["motif_numbers"].children
    ]
    assert profiled_nodes["region_canvas"].props["columns"] == expected_content_columns
    assert profiled_nodes["motif_numbers"].props["columns"] == expected_metric_columns
    assert profiled_nodes["region_canvas"].props["layout_strategy"] == "region_grid_v0"
    assert profiled_nodes["motif_numbers"].props["layout_strategy"] == "dashboard_grid_v0"
    assert profiled_nodes["region_canvas"].props["aesthetic_layout_profile"] == profile
    assert profiled_nodes["motif_numbers"].props["aesthetic_layout_profile"] == profile
    metric_cards = profiled_nodes["motif_numbers"].children
    assert [child.id for child in metric_cards] == ["motif_numbers_fixtures", "motif_numbers_emitters"]
    assert metric_cards[0].props.get("span_columns") == expected_featured_span
    assert metric_cards[0].props.get("layout_emphasis") == expected_layout_emphasis
    assert metric_cards[0].props.get("aesthetic_layout_profile") == (profile if expected_featured_span or expected_layout_emphasis else None)
    assert "span_columns" not in metric_cards[1].props
    assert "layout_emphasis" not in metric_cards[1].props
    assert "aesthetic_layout_profile" not in metric_cards[1].props


def test_layout_planner_region_grid_columns_use_rendered_children_and_cap():
    builder = ViewSpecBuilder("grid_plan", default_main_region=False)
    builder.add_region("body", parent_region="root", role="main", layout="grid", min_children=1)
    builder.add_region("main", parent_region="body", role="main", layout="stack", min_children=0)
    builder.add_region("side", parent_region="body", role="complementary", layout="stack", min_children=0)
    builder.add_region("aux", parent_region="body", role="complementary", layout="stack", min_children=0)
    builder.add_region("extra", parent_region="body", role="complementary", layout="stack", min_children=0)
    dashboard = builder.add_dashboard("metrics", region="body", group_id="cards")
    dashboard.add_card(label="One", value="1", id="one")
    dashboard.add_card(label="Two", value="2", id="two")

    ast = compile(builder.build_bundle())
    body = _find_node_id(ast.result.root.root, "region_body")

    assert len(ast.result.diagnostics) == 0
    assert body is not None
    assert body.primitive == "grid"
    assert [child.id for child in body.children] == ["region_main", "region_side", "region_aux", "region_extra", "motif_metrics"]
    assert body.props["columns"] == 3
    assert body.props["layout_strategy"] == "region_grid_v0"


def test_layout_planner_places_safe_motif_actions_once():
    builder = ViewSpecBuilder("hero_action")
    builder.add_hero(
        "intro",
        eyebrow="Agent-native UI",
        title="Describe intent",
        description="Compile checked UI.",
        region="main",
        group_id="message",
    )
    builder.add_action("open", "navigate", "Open", target_region="main", target_ref="motif:intro")

    ast = compile(builder.build_bundle())
    hero = _find_node_id(ast.result.root.root, "motif_intro")
    buttons = _find_nodes(ast.result.root.root, "button")

    assert len(ast.result.diagnostics) == 0
    assert hero is not None
    assert len(buttons) == 1
    assert buttons[0] in hero.children
    assert buttons[0].props["placement"] == "motif_local"


def test_collection_actions_compile_to_single_previous_sibling_action_bar(tmp_path):
    builder = ViewSpecBuilder("collection_actions")
    builder.add_text_input("query", label="Search", value="", group_id="controls")
    builder.add_text_input("status", label="Status", value="open", group_id="controls")
    builder.add_text_input("sort", label="Sort", value="created_desc", group_id="controls")
    builder.add_text_input("page", label="Page", value="next", group_id="controls")
    builder.add_node("selection", "selection", attrs={"label": "Selected", "selected_ids": "alpha,beta"})
    builder.bind_attr("selection_label", "selection", "label", region="main", present_as="label")
    builder.bind_attr("queue_selected_ids", "selection", "selected_ids", region="main", present_as="input")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    table.add_row(label="Beta", value="Waiting", id="beta")
    builder.add_collection_action("search_items", "search", "Search", collection_id="items", payload_bindings=["query_value"])
    builder.add_collection_action("filter_items", "filter", "Filter", collection_id="items", payload_bindings=["status_value"])
    builder.add_collection_action("sort_items", "sort", "Sort", collection_id="items", payload_bindings=["sort_value"])
    builder.add_collection_action("paginate_items", "paginate", "Next", collection_id="items", payload_bindings=["page_value"])
    builder.add_collection_action(
        "bulk_items",
        "bulk_action",
        "Assign selected",
        collection_id="items",
        payload_bindings=["queue_selected_ids"],
    )

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    region_main = _find_node_id(root, "region_main")
    bar = _find_node_id(root, "planner_items_collection_actions")
    wrapper = _find_node_id(root, "motif_items")
    buttons = _find_nodes(root, "button")

    assert len(ast.result.diagnostics) == 0
    assert region_main is not None
    assert bar is not None
    assert wrapper is not None
    assert region_main.children[region_main.children.index(wrapper) - 1] is bar
    assert bar.props["layout_strategy"] == "collection_action_bar_v1"
    assert bar.props["product_role"] == "action_row"
    assert [button.props["action_kind"] for button in buttons] == [
        "search",
        "filter",
        "sort",
        "paginate",
        "bulk_action",
    ]
    assert [button.props["placement"] for button in buttons] == ["collection_action_bar"] * 5

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert html.index('id="dom-planner_items_collection_actions"') < html.index('id="dom-motif_items"')
    assert 'data-action-kind="bulk_action"' in html


@pytest.mark.parametrize(
    ("kind", "payload_bindings", "expected_code"),
    [
        ("search", [], "COLLECTION_ACTION_PAYLOAD_REQUIRED"),
        ("bulk_action", [], "COLLECTION_BULK_SELECTION_REQUIRED"),
        ("bulk_action", ["queue_selected_ids", "query_value"], "COLLECTION_BULK_SELECTION_AMBIGUOUS"),
    ],
)
def test_collection_action_payload_constraints_fail_closed(kind, payload_bindings, expected_code):
    builder = ViewSpecBuilder(f"bad_{kind}")
    query = builder.add_text_input("query", label="Search", value="", group_id="controls")
    builder.add_node("selection", "selection", attrs={"selected_ids": "alpha"})
    builder.bind_attr("queue_selected_ids", "selection", "selected_ids", region="main", present_as="input")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    if payload_bindings == ["queue_selected_ids", "query_value"]:
        payload_bindings = ["queue_selected_ids", query]
    builder.add_action(
        "bad_collection_action",
        kind,
        "Bad",
        target_region="main",
        target_ref="motif:items",
        payload_bindings=payload_bindings,
    )

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert expected_code in codes
    assert _find_nodes(ast.result.root.root, "button") == []


def test_collection_action_target_and_size_constraints_fail_closed():
    builder = ViewSpecBuilder("bad_collection_target")
    query = builder.add_text_input("query", label="Search", value="x" * 513, group_id="controls")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    builder.add_action("bad_target", "search", "Search", target_region="main", target_ref="motif:metrics", payload_bindings=[query])

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "COLLECTION_ACTION_TARGET_INVALID" in codes

    builder = ViewSpecBuilder("bad_collection_size")
    oversized = builder.add_text_input("query", label="Search", value="x" * 513, group_id="controls")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action("too_large", "search", "Search", target_region="main", target_ref="motif:items", payload_bindings=[oversized])

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "COLLECTION_ACTION_PAYLOAD_TOO_LARGE" in codes
    assert _find_nodes(ast.result.root.root, "button") == []


def test_bulk_selection_size_and_collection_action_count_constraints_fail_closed():
    builder = ViewSpecBuilder("bad_bulk_size")
    builder.add_node("selection", "selection", attrs={"selected_ids": [f"row_{index}" for index in range(101)]})
    builder.bind_attr("queue_selected_ids", "selection", "selected_ids", region="main", present_as="input")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action(
        "bulk_too_large",
        "bulk_action",
        "Assign",
        target_region="main",
        target_ref="motif:items",
        payload_bindings=["queue_selected_ids"],
    )

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "COLLECTION_BULK_SELECTION_TOO_LARGE" in codes

    builder = ViewSpecBuilder("too_many_collection_actions")
    payload = builder.add_text_input("query", label="Search", value="", group_id="controls")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    for index in range(9):
        builder.add_action(
            f"search_items_{index}",
            "search",
            f"Search {index}",
            target_region="main",
            target_ref="motif:items",
            payload_bindings=[payload],
        )

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "TOO_MANY_COLLECTION_ACTIONS" in codes
    assert _find_nodes(ast.result.root.root, "button") == []


def test_collection_state_conflict_and_state_contract_constraints_fail_closed():
    builder = ViewSpecBuilder("state_conflict")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_loading_state("loading_items", title="Loading", region="main", group_id="message")

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "COLLECTION_STATE_CONFLICT" in codes
    assert _find_node_id(ast.result.root.root, "motif_loading_items") is None

    builder = ViewSpecBuilder("bad_state_contract")
    builder.add_node("state", "loading_state", attrs={"description": "Still loading", "body": "Please wait"})
    description = builder.bind_attr("state_description", "state", "description", region="main", present_as="text")
    body = builder.bind_attr("state_body", "state", "body", region="main", present_as="text")
    builder.add_motif("loading_items", "loading_state", "main", [description, body])

    ast = compile(builder.build_bundle())
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "STATE_MOTIF_TITLE_REQUIRED" in codes
    assert "STATE_MOTIF_TOO_MANY_DESCRIPTIONS" in codes


@pytest.mark.parametrize("motif_kind", ["comparison", "detail", "table"])
def test_layout_planner_keeps_unsafe_motif_actions_at_region_level(motif_kind):
    builder = ViewSpecBuilder(f"{motif_kind}_action")
    if motif_kind == "comparison":
        motif_id = "choices"
        comparison = builder.add_comparison(motif_id, region="main", group_id="items")
        comparison.add_item(label="Alpha", value="Ready", id="alpha")
        comparison.add_item(label="Beta", value="Next", id="beta")
    elif motif_kind == "detail":
        motif_id = "profile"
        detail = builder.add_detail(motif_id, region="main", group_id="items")
        detail.add_field(label="Owner", value="Ada", id="owner")
        detail.add_field(label="Status", value="Ready", id="status")
    else:
        motif_id = "items"
        table = builder.add_table(motif_id, region="main", group_id="items")
        table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action("submit_surface", "submit", "Submit", target_region="main", target_ref=f"motif:{motif_id}")

    ast = compile(builder.build_bundle())
    root = ast.result.root.root
    region_main = _find_node_id(root, "region_main")
    wrapper = _find_node_id(root, f"motif_{motif_id}")
    buttons = _find_nodes(root, "button")
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "UNSAFE_ACTION_PLACEMENT" in codes
    assert region_main is not None
    assert wrapper is not None
    assert len(buttons) == 1
    assert buttons[0] in region_main.children
    assert buttons[0] not in wrapper.children
    assert "placement" not in buttons[0].props


def test_layout_planner_reports_missing_declared_motif_wrapper():
    from viewspec.types import MotifSpec

    builder = ViewSpecBuilder("missing_wrapper")
    builder.add_node("payload", "item", attrs={"label": "Payload", "value": "Ready"})
    builder.bind_attr("payload_label", "payload", "label", region="main", present_as="label")
    builder.bind_attr("payload_value", "payload", "value", region="main", present_as="value")
    builder.add_action("open_ghost", "select", "Open", target_region="main", target_ref="motif:ghost")
    bundle = builder.build_bundle()
    bundle.view_spec.motifs.append(MotifSpec(id="ghost", kind="dashboard", region="missing", members=[]))

    ast = compile(bundle)
    buttons = _find_nodes(ast.result.root.root, "button")
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "UNKNOWN_REGION" in codes
    assert "MISSING_ACTION_MOTIF_WRAPPER" in codes
    assert buttons == []


def test_layout_planner_does_not_mutate_intent_input():
    builder = ViewSpecBuilder("mutation_guard")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="One", value="1", id="one")
    dashboard.add_card(label="Two", value="2", id="two")
    builder.add_action("open_metrics", "select", "Open", target_region="main", target_ref="motif:metrics")
    bundle = builder.build_bundle()
    before = json.dumps(bundle.to_json(), sort_keys=True)

    compile(bundle)
    after = json.dumps(bundle.to_json(), sort_keys=True)

    assert before == after


def test_product_surface_planner_v1_has_single_named_pass_call_site():
    source = Path("src/viewspec/compiler.py").read_text(encoding="utf-8")

    assert source.count("def _apply_product_surface_planner_v1(") == 1
    assert len(re.findall(r"(?<!def )_apply_product_surface_planner_v1\(", source)) == 1
    assert "_apply_layout_planner_v0(" not in source


def test_product_surface_planner_v1_has_no_fixture_id_branches():
    source = Path("src/viewspec/compiler.py").read_text(encoding="utf-8")

    for forbidden in (
        "multi_region_product",
        "benchmark_workspace",
        "workspace_metrics",
        "artifact_identity",
        "review_form",
        "submit_review",
    ):
        assert forbidden not in source
    assert not re.search(r"\.id\s*==\s*['\"](?:header|body|main|side)['\"]", source)


def test_unsupported_action_kind_is_diagnostic_and_not_emitted(tmp_path):
    builder = ViewSpecBuilder("bad_action_kind")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action(
        "destroy",
        "delete_everything",
        "Destroy",
        target_region="main",
        target_ref="binding:alpha_label",
        payload_bindings=["alpha_label"],
    )

    ast = compile(builder.build_bundle())
    buttons = _find_nodes(ast.result.root.root, "button")
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "UNSUPPORTED_ACTION_KIND" in codes
    assert buttons == []

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert 'data-action-id="destroy"' not in html
    assert ACTION_EVENT_SCRIPT not in html


def test_action_references_only_valid_rendered_bindings(tmp_path):
    builder = ViewSpecBuilder("bad_action_binding")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_binding("bad_payload", "node:missing#attr:value", region="main", present_as="value")
    builder.add_action(
        "send_bad_payload",
        "submit",
        "Send",
        target_region="main",
        target_ref="binding:bad_payload",
        payload_bindings=["bad_payload"],
    )

    ast = compile(builder.build_bundle())
    buttons = _find_nodes(ast.result.root.root, "button")
    codes = {diagnostic.code for diagnostic in ast.result.diagnostics}

    assert "INVALID_ADDRESS" in codes
    assert "UNKNOWN_ACTION_TARGET" in codes
    assert "UNKNOWN_ACTION_PAYLOAD_BINDING" in codes
    assert buttons == []

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert 'data-action-id="send_bad_payload"' not in html
    assert ACTION_EVENT_SCRIPT not in html


def test_input_binding_compiles_to_safe_local_text_control(tmp_path):
    builder = ViewSpecBuilder("compose")
    input_binding = builder.add_text_input("draft", label="Message", value="Hello", group_id="fields")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[input_binding])

    ast = compile(builder.build_bundle())
    inputs = _find_nodes(ast.result.root.root, "input")

    assert len(ast.result.diagnostics) == 0
    assert input_binding == "draft_value"
    assert inputs[0].props["value"] == "Hello"
    assert inputs[0].props["input_type"] == "text"
    assert inputs[0].props["binding_id"] == "draft_value"
    assert inputs[0].props["aria_label"] == "Message"
    assert "surface.subtle" in inputs[0].style_tokens

    group = next(group for group in builder.view_spec.groups if group.id == "fields")
    assert group.members == ["draft_label", "draft_value"]

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert '<input id="dom-binding_draft_value"' in html
    assert 'type="text"' in html
    assert 'value="Hello"' in html
    assert 'aria-label="Message"' in html
    assert 'data-binding-id="draft_value"' in html
    assert "payloadValues" in html
    assert "<form" not in html


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
