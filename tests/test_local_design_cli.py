from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from viewspec import DesignSystemError, IntentBundle, ViewSpecBuilder, __version__, compile
from viewspec.cli import main as cli_main
from viewspec.design_md import DESIGN_MAX_BYTES, load_design_system
from viewspec.emitters.react_tailwind_tsx import tailwind_recipe_registry_digest
from viewspec.local_tools import file_hash

ROOT = Path(__file__).resolve().parents[1]


def _design(color: str = "#112233") -> str:
    return f"""---
name: Local
colors:
  primary: "{color}"
  secondary: "#445566"
typography:
  body:
    fontFamily: Inter
spacing:
  md: 10px
---
"""


def test_local_design_maps_shared_tokens_into_reference_compile():
    context = load_design_system(content=_design())
    builder = ViewSpecBuilder("local_design")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")

    ast = compile(builder.build_bundle(), design=context)

    assert "#112233" in ast.style_values["tone.neutral"]
    assert "#445566" in ast.style_values["tone.muted"]
    root = ast.result.root.root
    assert "tone.neutral" in root.style_tokens


def test_local_design_heading_typography_reaches_prominent_values(tmp_path):
    design = load_design_system(
        content="""---
name: Typography
typography:
  heading:
    fontFamily: Fraunces
    fontSize: 28px
    lineHeight: 1.1
    letterSpacing: 0.02em
---
"""
    )
    builder = ViewSpecBuilder("typography_design")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")

    ast = compile(builder.build_bundle(), design=design)

    assert "Fraunces" in ast.style_values["emphasis.high"]
    assert "font-size: 28px;" in ast.style_values["emphasis.high"]
    assert "line-height: 1.1;" in ast.style_values["emphasis.high"]
    assert "letter-spacing: 0.02em;" in ast.style_values["emphasis.high"]

    from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    assert "font-family: &quot;Fraunces&quot;" in html
    assert "font-size: 28px;" in html


def test_local_design_accent_themes_action_buttons(tmp_path):
    design = load_design_system(
        content="""---
name: Actions
colors:
  primary: "#111111"
  accent: "#CC5500"
---
"""
    )
    builder = ViewSpecBuilder("action_theme")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="Ready", id="alpha")
    builder.add_action("open_alpha", "select", "Open Alpha", target_region="main")

    ast = compile(builder.build_bundle(), design=design)
    from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")

    assert "action.accent" in ast.style_values
    assert "background-color: #CC5500;" in ast.style_values["action.accent"]
    assert (
        'style="background-color: #0f766e; color: #ffffff; background-color: #111111; background-color: #CC5500;"'
        in html
    )


def test_local_design_rejects_broken_refs_and_cycles_but_warns_on_bad_colors():
    bad_color = load_design_system(content=_design("red"))
    assert any(finding.code == "DESIGN_COLOR_FORMAT_WARNING" for finding in bad_color.lint_report.findings)

    with pytest.raises(DesignSystemError):
        load_design_system(content='---\nname: Broken\ncolors:\n  primary: "{colors.missing}"\n---\n')

    with pytest.raises(DesignSystemError):
        load_design_system(
            content='---\nname: Cycle\ncolors:\n  primary: "{colors.accent}"\n  accent: "{colors.primary}"\n---\n'
        )

    with pytest.raises(DesignSystemError, match="strict"):
        load_design_system(content=_design("red"), strict=True)


def test_local_design_rejects_duplicate_yaml_keys():
    duplicate = """---
name: Duplicate
colors:
  primary: "#112233"
  primary: "#445566"
---
"""

    with pytest.raises(DesignSystemError) as exc_info:
        load_design_system(content=duplicate)

    assert exc_info.value.report is not None
    findings = exc_info.value.report.findings
    assert findings[0].code == "DESIGN_YAML_ERROR"
    assert "duplicate key" in findings[0].message


@given(
    key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=24),
    first=st.integers(min_value=0, max_value=0xFFFFFF),
    second=st.integers(min_value=0, max_value=0xFFFFFF),
)
def test_design_yaml_duplicate_keys_fail_closed_for_arbitrary_nested_tokens(key: str, first: int, second: int):
    duplicate = (
        f'---\nname: Duplicate property probe\ncolors:\n  {key}: "#{first:06X}"\n  {key}: "#{second:06X}"\n---\n'
    )

    with pytest.raises(DesignSystemError) as exc_info:
        load_design_system(content=duplicate)

    assert exc_info.value.report is not None
    assert exc_info.value.report.findings[0].code == "DESIGN_YAML_ERROR"
    assert "duplicate key" in exc_info.value.report.findings[0].message


def test_design_yaml_aliases_are_rejected_before_expansion():
    bomb = "\n".join(
        [
            "a: &a [x, x, x, x, x, x, x, x, x, x]",
            "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]",
            "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]",
            "d: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]",
            "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d, *d]",
            "f: [*e, *e, *e, *e, *e, *e, *e, *e, *e, *e]",
        ]
    )

    with pytest.raises(DesignSystemError) as exc_info:
        load_design_system(content=f"---\nname: Bomb\n{bomb}\n---\n")

    findings = exc_info.value.report.findings
    assert findings[0].code == "DESIGN_YAML_ERROR"
    assert "alias" in findings[0].message

    with pytest.raises(DesignSystemError) as merge_info:
        load_design_system(content="---\nbase: &base {primary: '#112233'}\ncolor:\n  <<: *base\n---\n")
    assert merge_info.value.report.findings[0].code == "DESIGN_YAML_ERROR"

    anchor_only = load_design_system(content="---\nname: Anchored\ncolor:\n  primary: &p '#112233'\n---\n")
    assert anchor_only.name == "Anchored"


def test_design_yaml_nesting_depth_fails_closed():
    deep = "---\nname: Deep\nx: " + "[" * 5000 + "]" * 5000 + "\n---\n"

    with pytest.raises(DesignSystemError) as exc_info:
        load_design_system(content=deep)

    assert exc_info.value.report.findings[0].code == "DESIGN_YAML_ERROR"


def test_design_file_size_is_gated_before_reading(tmp_path, monkeypatch):
    head = "---\nname: Fits\ncolor:\n  primary: '#112233'\n---\n"
    fits = head + "x" * (DESIGN_MAX_BYTES - len(head.encode("utf-8")))
    assert len(fits.encode("utf-8")) == DESIGN_MAX_BYTES
    assert load_design_system(content=fits).name == "Fits"

    with pytest.raises(DesignSystemError) as content_info:
        load_design_system(content=fits + "x")
    assert content_info.value.report.findings[0].code == "DESIGN_FILE_TOO_LARGE"

    oversized = tmp_path / "DESIGN.md"
    with oversized.open("wb") as handle:
        handle.truncate(DESIGN_MAX_BYTES + 1)

    def refuse_open(self, *args, **kwargs):
        raise AssertionError(f"{self.name} must not be opened before the size gate")

    monkeypatch.setattr(Path, "open", refuse_open)

    with pytest.raises(DesignSystemError) as file_info:
        load_design_system(path=oversized)

    assert file_info.value.report.findings[0].code == "DESIGN_FILE_TOO_LARGE"
    assert str(DESIGN_MAX_BYTES) in file_info.value.report.findings[0].message


def test_cli_compile_stays_local(tmp_path):
    design_path = tmp_path / "DESIGN.md"
    design_path.write_text(_design(), encoding="utf-8")
    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1><p>$1</p><button onclick='x()'>Open</button>", encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert (
        cli_main(["compile", str(html_path), "--design", str(design_path), "--out", str(out_dir), "--lift-json"]) == 0
    )
    assert html_path.read_text(encoding="utf-8").startswith("<h1>Report</h1>")
    assert out_dir.joinpath("index.html").exists()
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["manifest_schema_version"] == 1
    assert manifest["design_hash"]
    assert manifest["artifact_hash"]
    assert manifest["command_args"] == [
        "viewspec",
        "compile",
        "report.html",
        "--design",
        "DESIGN.md",
        "--out",
        "<out>",
        "--lift-json",
    ]
    assert manifest["guarantees"]["network_calls"] == "none"
    assert cli_main(["check", str(out_dir)]) == 0


def test_cli_compile_json_wraps_stable_manifest(tmp_path):
    builder = ViewSpecBuilder("json_cli")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "json-dist"

    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["manifest_schema_version"] == 1
    assert manifest["kind"] == "intent_bundle_compile"
    assert manifest["artifact_hash"]
    assert manifest["command_args"] == ["viewspec", "compile", "bundle.json", "--out", "<out>"]
    assert manifest["guarantees"]["network_calls"] == "none"
    assert manifest["guarantees"]["sdk_network_calls"] == "none"
    assert manifest["guarantees"]["artifact_autofetch_network"] == "none"
    assert manifest["external_refs"] == []
    assert manifest["diagnostics"] == []
    assert "nodes" in manifest
    assert manifest["semantic_digest"]["version"] == "semantic_digest.v1"
    assert manifest["semantic_digest"]["manifest_projection"] == manifest["semantic_digest"]["source_projection"]


def test_cli_compile_json_can_emit_react_tsx_target(tmp_path):
    builder = ViewSpecBuilder("react_cli")
    field = builder.add_text_input("message", label="Message", value="Hello", group_id="fields")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[field])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tsx", "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    tsx = out_dir.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")

    assert out_dir.joinpath("index.html").exists() is False
    assert manifest["kind"] == "intent_bundle_compile"
    assert manifest["emitter"] == "react_tsx"
    assert manifest["artifact_file"] == "ViewSpecView.tsx"
    assert manifest["artifact_hash"] == file_hash(out_dir / "ViewSpecView.tsx")
    assert manifest["semantic_digest"]["version"] == "semantic_digest.v1"
    assert manifest["command_args"] == [
        "viewspec",
        "compile",
        "bundle.json",
        "--target",
        "react-tsx",
        "--out",
        "<out>",
    ]
    assert 'source: "viewspec-react-tsx"' in tsx
    assert "const payloadValues = collectPayloadValues(payloadBindings);" in tsx
    assert "assertPayloadBounds(" in tsx
    assert cli_main(["check", str(out_dir), "--json"]) == 0


def test_cli_compile_json_can_emit_react_tailwind_tsx_target(tmp_path):
    builder = ViewSpecBuilder("react_tailwind_cli")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    field = builder.add_text_input("message", label="Message", value="Hello", group_id="fields")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[field])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    tsx = out_dir.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")

    assert out_dir.joinpath("index.html").exists() is False
    assert manifest["kind"] == "intent_bundle_compile"
    assert manifest["emitter"] == "react_tailwind_tsx"
    assert manifest["artifact_file"] == "ViewSpecView.tsx"
    assert manifest["artifact_hash"] == file_hash(out_dir / "ViewSpecView.tsx")
    assert manifest["tailwind_recipe_inventory"]["recipe_pack"] == "tailwind_app_v1"
    assert manifest["tailwind_recipe_inventory"]["registry_version"] == "tailwind_recipe_registry.v1"
    assert manifest["tailwind_recipe_inventory"]["recipe_registry_digest"]
    assert "primitive:button" in manifest["tailwind_recipe_inventory"]["recipes"]
    assert manifest["semantic_digest"]["version"] == "semantic_digest.v1"
    assert manifest["semantic_digest"]["manifest_projection"] == manifest["semantic_digest"]["source_projection"]
    assert manifest["command_args"] == [
        "viewspec",
        "compile",
        "bundle.json",
        "--target",
        "react-tailwind-tsx",
        "--out",
        "<out>",
    ]
    assert 'source: "viewspec-react-tailwind-tsx"' in tsx
    assert "const payloadValues = collectPayloadValues(payloadBindings);" in tsx
    assert "assertPayloadBounds(" in tsx
    assert "className={" not in tsx
    assert "style={{" not in tsx
    assert cli_main(["check", str(out_dir), "--json"]) == 0


def test_cli_check_verifies_react_tailwind_static_class_inventory(tmp_path, capsys):
    builder = ViewSpecBuilder("react_tailwind_check")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(out_dir), "--json"]) == 0
    capsys.readouterr()
    tsx_path = out_dir / "ViewSpecView.tsx"
    manifest_path = out_dir / "provenance_manifest.json"
    original = tsx_path.read_text(encoding="utf-8")
    tsx_path.write_text(original.replace('className="', "className={"), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_DYNAMIC_CLASS" in capsys.readouterr().out

    tsx_path.write_text(original.replace("uppercase", "normal-case", 1), encoding="utf-8")
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_INVENTORY_MISMATCH" in capsys.readouterr().out


def test_cli_check_verifies_semantic_digest_for_react_tailwind(tmp_path, capsys):
    builder = ViewSpecBuilder("react_tailwind_digest")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    tsx_path = out_dir / "ViewSpecView.tsx"
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    missing = dict(manifest)
    missing.pop("semantic_digest")
    manifest_path.write_text(json.dumps(missing, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "SEMANTIC_DIGEST_MISSING" in capsys.readouterr().out

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tsx_path.write_text(
        tsx_path.read_text(encoding="utf-8").replace(
            'renderValue(data["open_label"], "Open")', 'renderValue(data["open_label"], "Closed")'
        ),
        encoding="utf-8",
    )
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "SEMANTIC_DIGEST_MISMATCH" in capsys.readouterr().out


def test_cli_check_rejects_forbidden_and_circular_semantic_digest_fields(tmp_path, capsys):
    builder = ViewSpecBuilder("react_tailwind_digest_fields")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest["semantic_digest"]["manifest_projection"]["nodes"][0]["classes"] = ["not-approved"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "SEMANTIC_DIGEST_FIELD_FORBIDDEN" in capsys.readouterr().out

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["semantic_digest"]["manifest_projection"]["nodes"][0].pop("classes")
    manifest["semantic_digest"]["source_projection"]["semantic_digest"] = {}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "SEMANTIC_DIGEST_CIRCULAR" in capsys.readouterr().out


def _multi_region_bundle() -> IntentBundle:
    builder = ViewSpecBuilder(
        "multi_region_workspace",
        root_attrs={"title": "Workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.add_region("header", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("body", parent_region="root", role="main", layout="grid", min_children=2)
    builder.add_region("main", parent_region="body", role="main", layout="stack", min_children=2)
    builder.add_region("side", parent_region="body", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "workspace_intro",
        eyebrow="Quality",
        title="Benchmark the floor",
        description="Measure structural quality before changing layout behavior.",
        region="header",
        group_id="intro_message",
    )
    dashboard = builder.add_dashboard("workspace_metrics", region="main", group_id="metrics")
    dashboard.add_card(label="Fixtures", value="6", id="fixtures")
    dashboard.add_card(label="Emitters", value="2", id="emitters")
    form = builder.add_form("review_form", region="main", group_id="review_fields")
    form.add_field(label="Reviewer", value="", id="reviewer")
    form.add_field(label="Decision", value="approve", id="decision")
    detail = builder.add_detail("artifact_identity", region="side", group_id="identity_fields")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    detail.add_field(label="Network", value="none", id="network")
    builder.add_action(
        "submit_review",
        "submit",
        "Submit review",
        target_region="main",
        target_ref="motif:review_form",
        payload_bindings=["reviewer_value", "decision_value"],
    )
    builder.add_style("workspace_body_density", "region:body", "density.regular")
    builder.add_style("workspace_identity_surface", "motif:artifact_identity", "surface.strong")
    return builder.build_bundle()


def test_cli_check_recomputes_tailwind_app_role_derivation(tmp_path, capsys):
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(_multi_region_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    filter_bar_dom_id = next(
        dom_id for dom_id, entry in manifest["nodes"].items() if entry.get("app_role") == "filter_bar"
    )
    assert (
        manifest["nodes"][filter_bar_dom_id]["app_role_source"]
        == "tailwind_app_v1.structural.filter_bar_from_form_action_row"
    )

    manifest["nodes"][filter_bar_dom_id]["app_role_source"] = "tailwind_app_v1.structural.toolbar_from_action_row"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "APP_ROLE_DERIVATION_MISMATCH" in capsys.readouterr().out

    manifest["nodes"][filter_bar_dom_id]["app_role_source"] = (
        "tailwind_app_v1.structural.filter_bar_from_form_action_row"
    )
    manifest["nodes"][filter_bar_dom_id]["props"]["app_role"] = "filter_bar"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "APP_ROLE_LEXICAL_SOURCE" in capsys.readouterr().out


def test_cli_check_verifies_tailwind_registry_digest_and_reachability(tmp_path, capsys):
    builder = ViewSpecBuilder("react_tailwind_registry")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest["tailwind_recipe_inventory"]["recipe_registry_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_RECIPE_REGISTRY_DIGEST_MISMATCH" in capsys.readouterr().out

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tailwind_recipe_inventory"]["recipe_registry_digest"] = tailwind_recipe_registry_digest()
    manifest["tailwind_recipe_inventory"]["recipes"].append("app_role:overlay_panel")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_RECIPE_UNREACHABLE" in capsys.readouterr().out


def test_cli_check_rejects_tailwind_metadata_leaking_into_react_tsx(tmp_path, capsys):
    builder = ViewSpecBuilder("react_scope_leak")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tsx", "--out", str(out_dir)]) == 0
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_node = next(iter(manifest["nodes"].values()))
    first_node["recipe_key"] = "primitive:root"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_PLANNER_SCOPE_LEAK" in capsys.readouterr().out


def test_cli_check_rejects_react_tailwind_active_surfaces(tmp_path, capsys):
    builder = ViewSpecBuilder("react_tailwind_active_surface")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    tsx_path = out_dir / "ViewSpecView.tsx"
    manifest_path = out_dir / "provenance_manifest.json"
    tsx_path.write_text(tsx_path.read_text(encoding="utf-8") + "\nfetch('/unsafe');\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "TAILWIND_ACTIVE_SURFACE_FORBIDDEN" in capsys.readouterr().out


def test_cli_rejects_raw_html_import_for_react_tailwind_target(tmp_path, capsys):
    html_path = tmp_path / "input.html"
    html_path.write_text("<main><h1>Hello</h1></main>", encoding="utf-8")

    assert cli_main(["compile", str(html_path), "--target", "react-tailwind-tsx", "--out", str(tmp_path / "out")]) == 2
    assert "TAILWIND_IMPORT_NOT_SUPPORTED" in capsys.readouterr().err


def test_cli_check_verifies_react_tsx_artifact_source(tmp_path, capsys):
    builder = ViewSpecBuilder("react_check")
    field = builder.add_text_input("message", label="Message", value="Hello", group_id="fields")
    table = builder.add_table("copy", region="main", group_id="copy_rows")
    table.add_row(label="fetch('/copy') WebSocket dangerouslySetInnerHTML", value="Literal text", id="copy")
    builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[field])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "react-dist"

    assert cli_main(["compile", str(bundle_path), "--target", "react-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(out_dir), "--json"]) == 0
    capsys.readouterr()
    tsx_path = out_dir / "ViewSpecView.tsx"
    manifest_path = out_dir / "provenance_manifest.json"
    tsx = tsx_path.read_text(encoding="utf-8")
    tsx_path.write_text(tsx + "\n// tampered\n", encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "artifact_hash does not match ViewSpecView.tsx" in capsys.readouterr().out

    tsx_path.write_text(tsx + "\nconst unsafe = { dangerouslySetInnerHTML: {} };\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(tsx_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "ViewSpecView.tsx contains dangerouslySetInnerHTML" in capsys.readouterr().out


def test_cli_check_uses_byte_exact_artifact_hash(tmp_path, capsys):
    builder = ViewSpecBuilder("byte_exact_hash")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0
    html_path = out_dir / "index.html"
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_hash"] == file_hash(html_path)
    assert cli_main(["check", str(out_dir), "--json"]) == 0

    html = html_path.read_text(encoding="utf-8")
    html_path.write_bytes(html.replace("\n", "\r\n").encode("utf-8"))

    assert cli_main(["check", str(out_dir)]) == 2
    assert "artifact_hash does not match" in capsys.readouterr().out


def test_cli_init_design_and_check_tamper(tmp_path, capsys):
    design_path = tmp_path / "DESIGN.md"
    assert cli_main(["init", "design", "--out", str(design_path)]) == 0
    assert "colors:" in design_path.read_text(encoding="utf-8")
    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")
    out_dir = tmp_path / "dist"
    assert cli_main(["compile", str(html_path), "--design", str(design_path), "--out", str(out_dir)]) == 0
    assert cli_main(["check", str(out_dir), "--json"]) == 0

    out_dir.joinpath("index.html").write_text("<h1>Tampered</h1>", encoding="utf-8")
    assert cli_main(["check", str(out_dir)]) == 2
    assert "artifact_hash does not match" in capsys.readouterr().out


def test_python_module_entrypoint_runs_cli(tmp_path):
    env = os.environ.copy()
    src_path = str(ROOT / "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"

    help_result = subprocess.run(
        [sys.executable, "-m", "viewspec", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert help_result.returncode == 0
    assert "viewspec prove" in help_result.stdout or "prove" in help_result.stdout

    version_result = subprocess.run(
        [sys.executable, "-m", "viewspec", "--version"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert version_result.returncode == 0
    assert version_result.stdout.startswith("viewspec ")


def test_cli_check_rejects_machine_local_command_args(tmp_path, capsys):
    html_path = tmp_path / "report.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")
    out_dir = tmp_path / "dist"
    assert cli_main(["compile", str(html_path), "--out", str(out_dir)]) == 0

    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command_args"] = ["viewspec", "compile", str(html_path)]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    assert cli_main(["check", str(out_dir)]) == 2
    assert "command_args must not contain absolute paths" in capsys.readouterr().out


def test_cli_version_and_design_lint_errors_are_stable(tmp_path, capsys):
    with pytest.raises(SystemExit) as version_exit:
        cli_main(["--version"])
    assert version_exit.value.code == 0
    assert f"viewspec {__version__}" in capsys.readouterr().out

    bad_design = tmp_path / "DESIGN.md"
    bad_design.write_text(_design("red"), encoding="utf-8")
    html_path = tmp_path / "input.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")

    exit_code = cli_main(
        ["compile", str(html_path), "--design", str(bad_design), "--strict-design", "--out", str(tmp_path / "dist")]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "DESIGN.md lint" in stderr
    assert "DESIGN_COLOR_FORMAT_WARNING" in stderr


def test_cli_refuses_input_overwrite(tmp_path, capsys):
    html_path = tmp_path / "index.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")

    exit_code = cli_main(["compile", str(html_path), "--out", str(tmp_path)])

    assert exit_code == 2
    assert "Refusing to overwrite input file" in capsys.readouterr().err


def test_cli_refuses_html_input_overwrite_before_design_load(tmp_path, capsys):
    html_path = tmp_path / "index.html"
    html_path.write_text("<h1>Report</h1>", encoding="utf-8")

    exit_code = cli_main(
        ["compile", str(html_path), "--design", str(tmp_path / "missing-DESIGN.md"), "--out", str(tmp_path)]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "Refusing to overwrite input file" in stderr
    assert "missing-DESIGN.md" not in stderr


def test_cli_refuses_json_input_overwrite_before_design_load(tmp_path, capsys):
    builder = ViewSpecBuilder("overwrite_json")
    builder.add_dashboard("cards", region="main", group_id="cards").add_card(label="Revenue", value="$12", id="revenue")
    bundle_path = tmp_path / "provenance_manifest.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")

    exit_code = cli_main(
        ["compile", str(bundle_path), "--design", str(tmp_path / "missing-DESIGN.md"), "--out", str(tmp_path)]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "Refusing to overwrite input file" in stderr
    assert "missing-DESIGN.md" not in stderr


def test_cli_accepts_explicit_stdin_formats(tmp_path, monkeypatch):
    html_out = tmp_path / "html-out"
    monkeypatch.setattr(sys, "stdin", StringIO("<h1>From stdin</h1>"))
    assert cli_main(["compile", "-", "--stdin-format", "html", "--out", str(html_out)]) == 0
    assert "From stdin" in html_out.joinpath("index.html").read_text(encoding="utf-8")

    builder = ViewSpecBuilder("stdin_json")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    json_out = tmp_path / "json-out"
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(builder.build_bundle().to_json())))
    assert cli_main(["compile", "-", "--stdin-format", "json", "--out", str(json_out)]) == 0
    assert (
        json.loads(json_out.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))["source_name"]
        == "<stdin>"
    )


def test_claude_code_integration_mentions_only_local_commands():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("integrations/claude-code/SKILL.md").read_text(encoding="utf-8")

    assert "viewspec init" in text
    assert "viewspec compile" in text
    assert "viewspec check" in text
    assert "viewspec.intent.json" in text
    assert "viewspec share" not in text
    assert "api.viewspec.dev" not in text
