from __future__ import annotations

import json

import pytest

from viewspec import (
    ASTBundle,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    IRNode,
    Provenance,
)
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter


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
    assert "payloadValues: collectPayloadValues" in tsx
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
