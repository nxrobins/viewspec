from __future__ import annotations

import json
import re

import pytest

from viewspec import (
    ASTBundle,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    IRNode,
    Provenance,
    ViewSpecBuilder,
    compile,
)
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter, OFFLINE_EMITTER_CSS
from viewspec.emitters.react_tailwind_tsx import (
    RECIPE_BY_KEY,
    TAILWIND_AESTHETIC_RECIPE_OVERLAYS,
    TAILWIND_RECIPE_PACK,
    CompilerConstraintError,
    ReactTailwindTsxEmitter,
)
from viewspec.emitters.react_tsx import BASE_STYLE_BY_PRODUCT_ROLE, ReactTsxEmitter
from viewspec.intent_tools import compile_intent_bundle_file_tool
from viewspec.local_tools import check_artifact_dir


def _manifest_entry_by_primitive(manifest_nodes: dict[str, object], primitive: str) -> dict[str, object]:
    for entry in manifest_nodes.values():
        if isinstance(entry, dict) and entry.get("primitive") == primitive:
            return entry
    raise AssertionError(f"manifest missing primitive {primitive}")


def _manifest_entry_by_product_role(manifest_nodes: dict[str, object], product_role: str) -> dict[str, object]:
    for entry in manifest_nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props")
        if isinstance(props, dict) and props.get("product_role") == product_role:
            return entry
    raise AssertionError(f"manifest missing product_role {product_role}")


def _aesthetic_workspace_bundle(profile: str):
    builder = ViewSpecBuilder(
        "aesthetic_workspace",
        root_attrs={"title": "Aesthetic Workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile(profile)
    builder.add_region("north", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("canvas", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("focus", parent_region="canvas", role="primary", layout="stack", min_children=1)
    builder.add_region("assist", parent_region="canvas", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "intro",
        eyebrow="Operations",
        title="Review workspace",
        description="Compiler-owned profile layout.",
        region="north",
        group_id="intro",
    )
    dashboard = builder.add_dashboard("numbers", region="focus", group_id="metrics")
    dashboard.add_card(label="Open", value="4", id="open")
    dashboard.add_card(label="Blocked", value="1", id="blocked")
    dashboard.add_card(label="Ready", value="9", id="ready")
    detail = builder.add_detail("identity", region="assist", group_id="details")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    return builder.build_bundle()


def test_emitter_escapes_html_and_writes_contract_artifacts(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    provenance=Provenance(intent_refs=["viewspec:view:escape"]),
                    children=[
                        IRNode(
                            id="danger-node",
                            primitive="text",
                            props={"text": '<script>alert("&")</script>'},
                            provenance=Provenance(
                                content_refs=["node:doc#attr:title"],
                                intent_refs=["viewspec:binding:title"],
                            ),
                        ),
                        IRNode(
                            id="button",
                            primitive="button",
                            props={
                                "text": "Open",
                                "action_id": "open",
                                "action_kind": "navigate",
                                "target_ref": "view:escape",
                                "payload_bindings": ["title"],
                            },
                            provenance=Provenance(intent_refs=["viewspec:action:open"]),
                        ),
                        IRNode(
                            id="field",
                            primitive="input",
                            props={"value": 'Draft "&"', "binding_id": "field_value"},
                            provenance=Provenance(intent_refs=["viewspec:binding:field_value"]),
                        ),
                        IRNode(
                            id="hero-image",
                            primitive="image_slot",
                            props={"alt": 'Hero "preview"'},
                            provenance=Provenance(intent_refs=["viewspec:binding:hero"]),
                        ),
                        IRNode(
                            id="sparkline",
                            primitive="svg",
                            props={"label": "Revenue trend"},
                            provenance=Provenance(intent_refs=["viewspec:binding:trend"]),
                        ),
                        IRNode(
                            id="boundary",
                            primitive="error_boundary",
                            props={"diagnostic_code": "TEST", "message": "Broken"},
                            provenance=Provenance(intent_refs=["viewspec:binding:broken"]),
                        ),
                    ],
                )
            ),
            diagnostics=[
                CompilerDiagnostic(
                    severity="error",
                    code="TEST_DIAGNOSTIC",
                    message="Diagnostic text",
                    intent_ref="viewspec:binding:title",
                )
            ],
        ),
        style_values={},
        title='<Title "&">',
    )

    paths = HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    manifest = json.loads(tmp_path.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(tmp_path.joinpath("diagnostics.json").read_text(encoding="utf-8"))

    assert paths["html"].endswith("index.html")
    assert "&lt;Title &quot;&amp;&quot;&gt;" in html
    assert "https://cdn.tailwindcss.com" not in html
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&quot;&amp;&quot;)&lt;/script&gt;" in html
    assert 'data-action-id="open"' in html
    assert 'data-action-target-ref="view:escape"' in html
    assert 'type="button"' in html
    assert 'data-payload-bindings="[&quot;title&quot;]"' in html
    assert '<input id="dom-field"' in html
    assert 'value="Draft &quot;&amp;&quot;"' in html
    assert 'data-binding-id="field_value"' in html
    assert 'role="img" aria-label="Hero &quot;preview&quot;"' in html
    assert 'role="img" aria-label="Revenue trend"' in html
    assert 'role="alert"' in html
    assert manifest["dom-danger-node"]["content_refs"] == ["node:doc#attr:title"]
    assert diagnostics[0]["code"] == "TEST_DIAGNOSTIC"


def test_grid_emitter_merges_layout_and_token_styles(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    provenance=Provenance(intent_refs=["viewspec:view:grid"]),
                    children=[
                        IRNode(
                            id="grid",
                            primitive="grid",
                            props={"columns": 2},
                            style_tokens=["density.regular"],
                            provenance=Provenance(intent_refs=["viewspec:region:main"]),
                        )
                    ],
                )
            ),
            diagnostics=[],
        ),
        style_values={"density.regular": "gap: 20px; padding: 10px;"},
        title="Grid",
    )

    HtmlTailwindEmitter().emit(ast, tmp_path)
    html = tmp_path.joinpath("index.html").read_text(encoding="utf-8")
    grid_tag = html[html.index('id="dom-grid"') : html.index(">", html.index('id="dom-grid"'))]

    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in grid_tag
    assert "gap: 20px; padding: 10px;" in grid_tag
    assert 'data-style-tokens="[&quot;density.regular&quot;]"' in grid_tag
    assert grid_tag.count("style=") == 1


def test_html_and_react_emit_identical_closed_role_class_inventories(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    props={"layout_role": "root", "product_role": "app_shell"},
                    provenance=Provenance(intent_refs=["viewspec:view:classes"]),
                    children=[
                        IRNode(
                            id="metrics",
                            primitive="grid",
                            props={
                                "columns": 2,
                                "layout_role": "grid",
                                "motif_kind": "dashboard",
                                "product_role": "metric_grid",
                            },
                            provenance=Provenance(intent_refs=["viewspec:motif:metrics"]),
                            children=[
                                IRNode(
                                    id="metric-card",
                                    primitive="surface",
                                    props={
                                        "layout_role": "surface",
                                        "motif_kind": "dashboard",
                                        "product_role": "metric_card",
                                    },
                                    provenance=Provenance(intent_refs=["viewspec:motif:metrics"]),
                                )
                            ],
                        )
                    ],
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Class Inventory",
    )
    html_out = tmp_path / "html"
    react_out = tmp_path / "react"

    HtmlTailwindEmitter().emit(ast, html_out)
    ReactTsxEmitter().emit(ast, react_out)
    html_manifest = json.loads(html_out.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    react_manifest = json.loads(react_out.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    html_classes = {entry["ir_id"]: entry["classes"] for entry in html_manifest.values()}
    react_classes = {entry["ir_id"]: entry["classes"] for entry in react_manifest.values()}
    html = html_out.joinpath("index.html").read_text(encoding="utf-8")
    tsx = react_out.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")

    assert html_classes == react_classes
    assert html_classes["root"] == ["vs-root", "vs-layout-root", "vs-role-app-shell"]
    assert html_classes["metrics"] == [
        "vs-grid",
        "vs-layout-grid",
        "vs-motif-dashboard",
        "vs-role-metric-grid",
    ]
    assert html_classes["metric-card"] == [
        "vs-surface",
        "vs-layout-surface",
        "vs-motif-dashboard",
        "vs-role-metric-card",
    ]
    assert 'class="vs-root vs-layout-root vs-role-app-shell"' in html
    assert '"vs-root vs-layout-root vs-role-app-shell"' in tsx


@pytest.mark.parametrize("emitter_cls", [HtmlTailwindEmitter, ReactTsxEmitter, ReactTailwindTsxEmitter])
def test_emitters_reject_unsafe_role_class_values_before_writing(tmp_path, emitter_cls):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    props={"product_role": "user-controlled"},
                    provenance=Provenance(intent_refs=["viewspec:view:unsafe_role"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Unsafe Role",
    )
    output = tmp_path / emitter_cls.__name__

    with pytest.raises(ValueError, match="UNSAFE_ROLE_CLASS"):
        emitter_cls().emit(ast, output)

    assert not output.exists()


def test_product_surface_style_additions_have_no_autofetch_surfaces():
    unsafe = re.compile(r"(?i)(@import|url\s*\(|https?://|fetch\s*\(|XMLHttpRequest|WebSocket|EventSource)")
    react_style_text = json.dumps(BASE_STYLE_BY_PRODUCT_ROLE, sort_keys=True)

    assert not unsafe.search(OFFLINE_EMITTER_CSS)
    assert not unsafe.search(react_style_text)


@pytest.mark.parametrize("emitter_cls", [HtmlTailwindEmitter, ReactTsxEmitter, ReactTailwindTsxEmitter])
def test_emitters_reject_autofetching_style_values_before_writing(tmp_path, emitter_cls):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    style_tokens=["brand.remote"],
                    provenance=Provenance(intent_refs=["viewspec:view:style_guard"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={"brand.remote": "background-image: url(https://evil.example/pixel.png);"},
        title="Unsafe Style",
    )
    output = tmp_path / emitter_cls.__name__

    with pytest.raises(ValueError, match="auto-fetching CSS surface"):
        emitter_cls().emit(ast, output)

    assert not output.exists()


def test_react_tsx_emitter_writes_component_manifest_and_action_contract(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    provenance=Provenance(intent_refs=["viewspec:view:react"]),
                    children=[
                        IRNode(
                            id="title",
                            primitive="text",
                            props={"text": '<script>alert("x")</script>', "binding_id": "title"},
                            provenance=Provenance(
                                content_refs=["node:doc#attr:title"],
                                intent_refs=["viewspec:binding:title"],
                            ),
                        ),
                        IRNode(
                            id="field",
                            primitive="input",
                            props={"value": "Draft", "binding_id": "message", "aria_label": "Message"},
                            provenance=Provenance(
                                content_refs=["node:message#attr:value"],
                                intent_refs=["viewspec:binding:message"],
                            ),
                        ),
                        IRNode(
                            id="send",
                            primitive="button",
                            props={
                                "text": "Send",
                                "action_id": "send",
                                "action_kind": "submit",
                                "target_ref": "view:react",
                                "payload_bindings": ["message", "title"],
                            },
                            provenance=Provenance(intent_refs=["viewspec:action:send"]),
                        ),
                    ],
                )
            ),
            diagnostics=[],
        ),
        style_values={"density.regular": "gap: 20px; padding: 10px;"},
        title="React Test",
    )

    paths = ReactTsxEmitter().emit(ast, tmp_path)
    tsx = tmp_path.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")
    manifest = json.loads(tmp_path.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))

    assert paths["tsx"].endswith("ViewSpecView.tsx")
    assert '"use client";' in tsx
    assert 'source: "viewspec-react-tsx"' in tsx
    assert "const payloadValues = collectPayloadValues(payloadBindings);" in tsx
    assert "assertPayloadBounds(" in tsx
    assert "payload: collectPayload" not in tsx
    assert "export type ViewSpecData = Record<string, unknown>;" in tsx
    assert "function renderValue(value: unknown, fallback: React.ReactNode): React.ReactNode" in tsx
    assert 'if (typeof value === "boolean") return value ? "true" : "false";' in tsx
    assert "return JSON.stringify(value);" in tsx
    assert "React.useState<Record<string, unknown>>" in tsx
    assert "Object.prototype.hasOwnProperty.call(data, bindingId)" in tsx
    assert 'data-binding-id={"message"}' in tsx
    assert '{renderValue(data["title"], "\\u003cscript\\u003ealert' in tsx
    assert "onAction?.({" in tsx
    assert "<script>alert" not in tsx
    assert "\\u003cscript\\u003ealert" in tsx
    assert manifest["dom-title"]["content_refs"] == ["node:doc#attr:title"]
    assert manifest["dom-send"]["props"]["payload_bindings"] == ["message", "title"]


def test_react_tailwind_tsx_emitter_writes_static_recipe_component(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    props={"product_role": "app_shell"},
                    provenance=Provenance(intent_refs=["viewspec:view:tailwind"]),
                    children=[
                        IRNode(
                            id="metrics",
                            primitive="grid",
                            props={"columns": 2, "motif_kind": "dashboard", "product_role": "metric_grid"},
                            provenance=Provenance(intent_refs=["viewspec:motif:metrics"]),
                            children=[
                                IRNode(
                                    id="metric-card",
                                    primitive="surface",
                                    props={"motif_kind": "dashboard", "product_role": "metric_card"},
                                    provenance=Provenance(intent_refs=["viewspec:motif:metrics"]),
                                    children=[
                                        IRNode(id="label", primitive="label", props={"text": "Open"}),
                                        IRNode(id="value", primitive="value", props={"text": "18"}),
                                    ],
                                )
                            ],
                        ),
                        IRNode(
                            id="send",
                            primitive="button",
                            props={
                                "text": "Send",
                                "action_id": "send",
                                "action_kind": "submit",
                                "target_ref": "view:tailwind",
                                "payload_bindings": [],
                            },
                            provenance=Provenance(intent_refs=["viewspec:action:send"]),
                        ),
                    ],
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Tailwind Test",
    )

    paths = ReactTailwindTsxEmitter().emit(ast, tmp_path)
    tsx = tmp_path.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")
    manifest = json.loads(tmp_path.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))

    assert paths["tsx"].endswith("ViewSpecView.tsx")
    assert 'source: "viewspec-react-tailwind-tsx"' in tsx
    assert 'className="min-h-screen bg-slate-50 px-6 py-6 text-slate-950 sm:px-8"' in tsx
    assert "className={" not in tsx
    assert "style={{" not in tsx
    assert ".join(" not in tsx
    assert "dangerouslySetInnerHTML" not in tsx
    assert manifest["dom-root"]["recipe_pack"] == TAILWIND_RECIPE_PACK
    assert manifest["dom-root"]["recipe_key"] == "app_role:app_shell"
    assert manifest["dom-root"]["app_role"] == "app_shell"
    assert manifest["dom-metrics"]["recipe_key"] == "app_role:metric_grid"
    assert "grid-cols-1" in manifest["dom-metrics"]["classes"]
    assert "sm:grid-cols-2" in manifest["dom-metrics"]["classes"]
    assert "lg:grid-cols-3" not in manifest["dom-metrics"]["classes"]
    registry_tokens = {token for value in RECIPE_BY_KEY.values() for token in value.split()}
    assert set(manifest["dom-root"]["classes"]).issubset(registry_tokens)


def test_react_tailwind_grid_columns_come_from_layout_props_only():
    fixed_grid_classes = {"grid-cols-1", "sm:grid-cols-2", "lg:grid-cols-3"}

    for key, classes in RECIPE_BY_KEY.items():
        if key in {"layout:grid", "primitive:grid"}:
            continue
        assert fixed_grid_classes.isdisjoint(classes.split()), key


def test_aesthetic_profile_markers_and_tailwind_overlay_classes_are_checked(tmp_path):
    builder = ViewSpecBuilder("aesthetic_contract")
    builder.set_aesthetic_profile("aesthetic.data_dense")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$2.4M", id="revenue")
    ast = compile(builder.build_bundle())

    html_out = tmp_path / "html"
    react_out = tmp_path / "react"
    tailwind_out = tmp_path / "tailwind"
    HtmlTailwindEmitter().emit(ast, html_out)
    ReactTsxEmitter().emit(ast, react_out)
    ReactTailwindTsxEmitter().emit(ast, tailwind_out)

    html = html_out.joinpath("index.html").read_text(encoding="utf-8")
    tsx = react_out.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")
    tailwind_tsx = tailwind_out.joinpath("ViewSpecView.tsx").read_text(encoding="utf-8")
    tailwind_manifest = json.loads(tailwind_out.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))

    assert 'data-aesthetic-profile="aesthetic.data_dense"' in html
    assert 'data-aesthetic-profile={"aesthetic.data_dense"}' in tsx
    assert 'data-aesthetic-profile={"aesthetic.data_dense"}' in tailwind_tsx
    root_entry = _manifest_entry_by_primitive(tailwind_manifest, "root")
    label_entry = _manifest_entry_by_primitive(tailwind_manifest, "label")
    value_entry = _manifest_entry_by_primitive(tailwind_manifest, "value")
    assert root_entry["props"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert "font-semibold" in label_entry["classes"]
    assert "text-lg" in value_entry["classes"]


def test_react_tailwind_profile_artifact_passes_public_check_path(tmp_path):
    builder = ViewSpecBuilder("aesthetic_checked")
    builder.set_aesthetic_profile("aesthetic.executive_review")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open risks", value="4", id="open_risks")
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(builder.build_bundle().to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        tmp_path / "react-tailwind-output",
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )

    assert result["ok"] is True
    manifest = json.loads((tmp_path / "react-tailwind-output" / "provenance_manifest.json").read_text(encoding="utf-8"))
    root_entry = _manifest_entry_by_primitive(manifest["nodes"], "root")
    assert root_entry["props"]["aesthetic_profile"] == "aesthetic.executive_review"
    assert manifest["tailwind_recipe_inventory"]["aesthetic_profile"] == "aesthetic.executive_review"


def test_react_tailwind_profile_layout_metadata_passes_public_check_path(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.data_dense").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )

    assert result["ok"] is True
    manifest = json.loads((output / "provenance_manifest.json").read_text(encoding="utf-8"))
    root_entry = _manifest_entry_by_primitive(manifest["nodes"], "root")
    content_grid = _manifest_entry_by_product_role(manifest["nodes"], "content_grid")
    metric_grid = _manifest_entry_by_product_role(manifest["nodes"], "metric_grid")
    metric_card = _manifest_entry_by_product_role(manifest["nodes"], "metric_card")
    assert "sm:px-8" not in root_entry["classes"]
    assert "sm:px-5" in root_entry["classes"]
    assert "min-h-28" not in metric_card["classes"]
    assert "min-h-20" in metric_card["classes"]
    assert content_grid["props"]["aesthetic_layout_profile"] == "aesthetic.data_dense"
    assert content_grid["props"]["columns"] == 3
    assert metric_grid["props"]["aesthetic_layout_profile"] == "aesthetic.data_dense"
    assert metric_grid["props"]["columns"] == 3

    checked = check_artifact_dir(output)

    assert checked["ok"] is True, checked["errors"]
    assert checked["manifest_summary"]["aesthetic_profile"] == "aesthetic.data_dense"
    assert checked["manifest_summary"]["aesthetic_layout"]["content_grid"]["columns"] == 3
    assert checked["manifest_summary"]["aesthetic_layout"]["metric_grid"]["columns"] == 3


def test_react_tailwind_profile_span_metadata_passes_public_check_path(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.premium_saas").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )

    assert result["ok"] is True
    manifest = json.loads((output / "provenance_manifest.json").read_text(encoding="utf-8"))
    metric_card = _manifest_entry_by_product_role(manifest["nodes"], "metric_card")
    assert metric_card["props"]["aesthetic_layout_profile"] == "aesthetic.premium_saas"
    assert metric_card["props"]["layout_emphasis"] == "featured"
    assert metric_card["props"]["span_columns"] == 2
    assert "ring-2" in metric_card["classes"]
    assert "ring-teal-300" in metric_card["classes"]
    assert "sm:col-span-2" in metric_card["classes"]

    checked = check_artifact_dir(output)

    assert checked["ok"] is True, checked["errors"]
    assert checked["manifest_summary"]["aesthetic_layout"]["metric_card"]["span_columns"] == 2
    assert checked["manifest_summary"]["aesthetic_layout"]["metric_card"]["layout_emphasis"] == "featured"
    assert checked["manifest_summary"]["aesthetic_layout"]["metric_card"]["node_count"] == 1


def test_check_rejects_tampered_aesthetic_profile_metadata(tmp_path):
    builder = ViewSpecBuilder("aesthetic_tamper")
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open risks", value="4", id="open_risks")
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(builder.build_bundle().to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    manifest_path = output / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root_entry = _manifest_entry_by_primitive(manifest["nodes"], "root")
    root_entry["props"]["aesthetic_profile"] = "aesthetic.unchecked"
    manifest["tailwind_recipe_inventory"]["aesthetic_profile"] = "aesthetic.unchecked"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checked = check_artifact_dir(output)

    assert checked["ok"] is False
    assert any("AESTHETIC_PROFILE_UNKNOWN" in error for error in checked["errors"])


def test_check_rejects_tampered_aesthetic_layout_metadata(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.editorial_product").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    manifest_path = output / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metric_grid = _manifest_entry_by_product_role(manifest["nodes"], "metric_grid")
    assert metric_grid["props"]["aesthetic_layout_profile"] == "aesthetic.editorial_product"
    assert metric_grid["props"]["columns"] == 1
    metric_grid["props"]["columns"] = 2
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checked = check_artifact_dir(output)

    assert checked["ok"] is False
    assert any("AESTHETIC_PROFILE_LAYOUT_MISMATCH" in error for error in checked["errors"])


def test_check_rejects_tampered_aesthetic_span_metadata(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.premium_saas").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    manifest_path = output / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metric_card = _manifest_entry_by_product_role(manifest["nodes"], "metric_card")
    assert metric_card["props"]["aesthetic_layout_profile"] == "aesthetic.premium_saas"
    assert metric_card["props"]["span_columns"] == 2
    metric_card["props"]["span_columns"] = 1
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checked = check_artifact_dir(output)

    assert checked["ok"] is False
    assert any("AESTHETIC_PROFILE_LAYOUT_MISMATCH" in error for error in checked["errors"])


def test_check_rejects_tampered_aesthetic_emphasis_metadata(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.premium_saas").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    manifest_path = output / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metric_card = _manifest_entry_by_product_role(manifest["nodes"], "metric_card")
    assert metric_card["props"]["aesthetic_layout_profile"] == "aesthetic.premium_saas"
    assert metric_card["props"]["layout_emphasis"] == "featured"
    metric_card["props"]["layout_emphasis"] = "quiet"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checked = check_artifact_dir(output)

    assert checked["ok"] is False
    assert any("AESTHETIC_PROFILE_LAYOUT_MISMATCH" in error for error in checked["errors"])


def test_check_rejects_boolean_aesthetic_layout_columns(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    output = tmp_path / "react-tailwind-output"
    source.write_text(json.dumps(_aesthetic_workspace_bundle("aesthetic.editorial_product").to_json(), indent=2), encoding="utf-8")

    result = compile_intent_bundle_file_tool(
        source,
        output,
        target="react-tailwind-tsx",
        cwd=tmp_path,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True
    manifest_path = output / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metric_grid = _manifest_entry_by_product_role(manifest["nodes"], "metric_grid")
    assert metric_grid["props"]["aesthetic_layout_profile"] == "aesthetic.editorial_product"
    assert metric_grid["props"]["columns"] == 1
    metric_grid["props"]["columns"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    checked = check_artifact_dir(output)

    assert checked["ok"] is False
    assert any("AESTHETIC_PROFILE_LAYOUT_MISMATCH" in error for error in checked["errors"])


@pytest.mark.parametrize(
    ("profile_overlay", "expected_code"),
    [
        ({}, "TAILWIND_AESTHETIC_RECIPE_MISSING"),
        ({"aesthetic.calm_ops": {"app_role:app_shell": "x " * 2200}}, "TAILWIND_AESTHETIC_RECIPE_TOO_LARGE"),
        ({"aesthetic.calm_ops": {"app_role:app_shell": "[color:red]"}}, "TAILWIND_AESTHETIC_UNSAFE_CLASS"),
    ],
)
def test_react_tailwind_rejects_missing_oversized_or_unsafe_aesthetic_recipes(tmp_path, monkeypatch, profile_overlay, expected_code):
    import viewspec.emitters.react_tailwind_tsx as react_tailwind

    overlays = {profile: dict(overlay) for profile, overlay in TAILWIND_AESTHETIC_RECIPE_OVERLAYS.items()}
    if not profile_overlay:
        overlays.pop("aesthetic.calm_ops")
    else:
        for profile, overlay in profile_overlay.items():
            overlays[profile] = overlay
    monkeypatch.setattr(react_tailwind, "TAILWIND_AESTHETIC_RECIPE_OVERLAYS", overlays)
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    props={"aesthetic_profile": "aesthetic.calm_ops"},
                    style_tokens=["aesthetic.calm_ops"],
                    provenance=Provenance(intent_refs=["viewspec:view:bad_recipe"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Bad Recipe",
    )

    with pytest.raises(CompilerConstraintError) as exc_info:
        ReactTailwindTsxEmitter().emit(ast, tmp_path)

    assert exc_info.value.code == expected_code
    assert not tmp_path.joinpath("ViewSpecView.tsx").exists()


def test_react_tailwind_tsx_emitter_rejects_user_app_role_before_writing(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    props={"app_role": "sidebar_nav"},
                    provenance=Provenance(intent_refs=["viewspec:view:unsafe_app_role"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Unsafe App Role",
    )
    output = tmp_path / "tailwind"

    with pytest.raises(ValueError, match="APP_ROLE_LEXICAL_SOURCE"):
        ReactTailwindTsxEmitter().emit(ast, output)

    assert not output.exists()


def test_react_tailwind_tsx_constraint_errors_have_stable_codes(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[IRNode(id="grid", primitive="grid", props={"columns": 9})],
                    provenance=Provenance(intent_refs=["viewspec:view:bad_grid"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Bad Grid",
    )

    with pytest.raises(CompilerConstraintError) as exc_info:
        ReactTailwindTsxEmitter().emit(ast, tmp_path)

    assert exc_info.value.code == "TAILWIND_LIMIT_EXCEEDED_GRID_COLUMNS"
    assert not tmp_path.joinpath("ViewSpecView.tsx").exists()


def test_react_tsx_emitter_rejects_invalid_ir_before_writing(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[IRNode(id='bad-"id"', primitive="text", props={"text": "Bad"})],
                    provenance=Provenance(intent_refs=["viewspec:view:react"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Bad React",
    )

    output = tmp_path / "react"
    with pytest.raises(ValueError, match="IRNode.id"):
        ReactTsxEmitter().emit(ast, output)

    assert not output.exists()


def test_action_payload_collection_is_scoped_to_artifact_root():
    from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT

    assert "function viewspecPayloadBindings(btn)" in ACTION_EVENT_SCRIPT
    assert "let payloadBindings = [];" in ACTION_EVENT_SCRIPT
    assert "Array.isArray(parsedPayloadBindings)" in ACTION_EVENT_SCRIPT
    assert "payloadBindings = [];" in ACTION_EVENT_SCRIPT
    assert "function dispatchViewSpecAction(btn)" in ACTION_EVENT_SCRIPT
    assert "schemaVersion: 1" in ACTION_EVENT_SCRIPT
    assert "source: 'viewspec-html-tailwind'" in ACTION_EVENT_SCRIPT
    assert "const root = btn.closest('.vs-root') || document;" in ACTION_EVENT_SCRIPT
    assert "root.querySelectorAll('[data-binding-id]')" in ACTION_EVENT_SCRIPT
    assert "document.querySelectorAll('[data-binding-id]')" not in ACTION_EVENT_SCRIPT
    assert "targetRef: btn.dataset.actionTargetRef || ''" in ACTION_EVENT_SCRIPT
    assert "document.addEventListener('keydown'" in ACTION_EVENT_SCRIPT
    assert "e.key !== 'Enter'" in ACTION_EVENT_SCRIPT
    assert "target.tagName !== 'INPUT'" in ACTION_EVENT_SCRIPT
    assert "target.closest('[role=\"form\"][data-ir-id]')" in ACTION_EVENT_SCRIPT
    assert "irId.startsWith('motif_')" in ACTION_EVENT_SCRIPT
    assert "root.querySelectorAll('[data-action-id][data-action-kind=\"submit\"]')" in ACTION_EVENT_SCRIPT
    assert "candidate.dataset.actionTargetRef === targetRef" in ACTION_EVENT_SCRIPT
    assert "CSS.escape" not in ACTION_EVENT_SCRIPT
    assert "e.preventDefault();" in ACTION_EVENT_SCRIPT


@pytest.mark.parametrize(
    ("props", "message"),
    [
        ({"text": "Bad", "action_kind": "select", "payload_bindings": []}, "action_id"),
        ({"text": "Bad", "action_id": "bad", "payload_bindings": []}, "action_kind"),
        ({"text": "Bad", "action_id": "bad", "action_kind": "select", "target_ref": "../escape", "payload_bindings": []}, "target_ref"),
        ({"text": "Bad", "action_id": "bad", "action_kind": "select", "payload_bindings": "title"}, "payload_bindings"),
    ],
)
def test_emitter_rejects_invalid_action_metadata_before_writing(tmp_path, props, message):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[
                        IRNode(
                            id="button",
                            primitive="button",
                            props=props,
                            provenance=Provenance(intent_refs=["viewspec:action:bad"]),
                        )
                    ],
                    provenance=Provenance(intent_refs=["viewspec:view:bad_action"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Bad Action",
    )

    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match=message):
        HtmlTailwindEmitter().emit(ast, output)

    assert not output.exists()


def test_emitter_rejects_unsafe_ir_ids_before_writing(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[
                        IRNode(
                            id='danger-"node"',
                            primitive="text",
                            props={"text": "Unsafe identity"},
                            provenance=Provenance(intent_refs=["viewspec:binding:title"]),
                        )
                    ],
                    provenance=Provenance(intent_refs=["viewspec:view:unsafe"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Unsafe",
    )

    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="IRNode.id"):
        HtmlTailwindEmitter().emit(ast, output)

    assert not output.exists()


def test_emitter_rejects_duplicate_ir_ids_before_manifest_overwrite(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[
                        IRNode(id="duplicate", primitive="text", props={"text": "First"}),
                        IRNode(id="duplicate", primitive="text", props={"text": "Second"}),
                    ],
                    provenance=Provenance(intent_refs=["viewspec:view:duplicate"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Duplicate",
    )

    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="Duplicate IRNode.id"):
        HtmlTailwindEmitter().emit(ast, output)

    assert not output.exists()


def test_emitter_rejects_unsupported_ir_primitives_before_writing(tmp_path):
    ast = ASTBundle(
        result=CompilerResult(
            root=CompositionIR(
                root=IRNode(
                    id="root",
                    primitive="root",
                    children=[IRNode(id="canvas", primitive="webgl_canvas")],
                    provenance=Provenance(intent_refs=["viewspec:view:unsupported"]),
                )
            ),
            diagnostics=[],
        ),
        style_values={},
        title="Unsupported",
    )

    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="Unsupported IR primitive"):
        HtmlTailwindEmitter().emit(ast, output)

    assert not output.exists()
