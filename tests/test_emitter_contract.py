from __future__ import annotations

import json

from viewspec import (
    ASTBundle,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    IRNode,
    Provenance,
)
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter


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
                            id='danger-"node"',
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
                                "payload_bindings": ["title"],
                            },
                            provenance=Provenance(intent_refs=["viewspec:action:open"]),
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
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&quot;&amp;&quot;)&lt;/script&gt;" in html
    assert 'data-action-id="open"' in html
    assert 'data-payload-bindings="[&quot;title&quot;]"' in html
    assert manifest['dom-danger-"node"']["content_refs"] == ["node:doc#attr:title"]
    assert diagnostics[0]["code"] == "TEST_DIAGNOSTIC"
