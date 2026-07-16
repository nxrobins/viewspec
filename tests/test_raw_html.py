from __future__ import annotations

import hashlib
import json
import re
import socket
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from viewspec import ViewSpecBuilder, compile, compile_html, diff_html, lift_html, load_design_system
from viewspec.cli import main as cli_main
from viewspec.raw_html import MAX_DOM_DEPTH, MAX_HTML_INPUT_BYTES, HtmlInputError, write_html_compile_result


DESIGN = """---
name: Acme
colors:
  primary: "#123456"
  surface: "#FFFFFF"
  background: "#F8FAFC"
typography:
  body:
    fontFamily: Inter
spacing:
  md: 12px
rounded:
  md: 8px
---
"""

ROOT = Path(__file__).resolve().parents[1]


def test_compile_html_sanitizes_active_content_and_writes_manifest(tmp_path):
    html = """
    <h1 onclick="bad()">Quarterly Report</h1>
    <script>alert("x")</script>
    <iframe src="https://example.com/embed"></iframe>
    <p>Revenue</p>
    <a href="javascript:alert(1)">Open</a>
    <img src="https://example.com/chart.png" onload="bad()" alt="Chart">
    """
    result = compile_html(html, design=load_design_system(content=DESIGN), source_name="report.html")

    assert "<script" not in result.html
    assert "onclick" not in result.html
    assert "javascript:" not in result.html
    assert "<iframe" not in result.html
    assert 'src="https://example.com/chart.png"' not in result.html
    assert "External image: Chart" in result.html
    assert "Quarterly Report" in result.html
    assert "#123456" in result.html
    assert result.manifest["version"] == 1
    assert result.manifest["guarantees"]["network_calls"] == "none"
    assert result.manifest["guarantees"]["sdk_network_calls"] == "none"
    assert result.manifest["guarantees"]["artifact_autofetch_network"] == "none"
    assert result.manifest["guarantees"]["decompilation"] == "not_claimed"
    assert result.manifest["design"]["design_hash"] == result.manifest["design_hash"]
    assert result.manifest["design"]["lint_summary"] == {"errors": 0, "warnings": 0, "info": 0}
    assert result.manifest["external_refs"] == [
        {"attr": "src", "behavior": "inert_placeholder", "kind": "image", "url": "https://example.com/chart.png"},
    ]
    assert any(item["code"] == "HTML_ATTR_STRIPPED" for item in result.diagnostics)
    assert any(item["code"] == "HTML_URL_STRIPPED" for item in result.diagnostics)

    paths = write_html_compile_result(result, tmp_path, include_lift=True)
    assert set(paths) == {"html", "manifest", "diagnostics", "lift"}
    manifest = json.loads(tmp_path.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_hash"] == result.lift.source_hash
    assert manifest["design"]["name"] == "Acme"
    assert tmp_path.joinpath("lift.json").exists()


def test_design_tokens_cannot_inject_css_or_autofetch_into_raw_html():
    design = load_design_system(
        content="""---
name: Unsafe Theme
typography:
  body:
    fontFamily: "Inter; background:url(https://evil.example/font)"
    fontWeight: true
    fontSize: true
    lineHeight: -1
  heading:
    fontFamily: "Heading; color:red"
spacing:
  md: "1rem; background:url(https://evil.example/space)"
  card: true
rounded:
  md: "10px; background:url(https://evil.example/radius)"
---
"""
    )

    result = compile_html("<h1>Report</h1><p>Body</p>", design=design)

    assert "typography.body.fontFamily" in design.ignored_tokens
    assert "typography.body.fontWeight" in design.ignored_tokens
    assert "typography.body.fontSize" in design.ignored_tokens
    assert "typography.body.lineHeight" in design.ignored_tokens
    assert "typography.heading.fontFamily" in design.ignored_tokens
    assert "spacing.md" in design.ignored_tokens
    assert "spacing.card" in design.ignored_tokens
    assert "rounded.md" in design.ignored_tokens
    assert "evil.example" not in result.html
    assert "background:url" not in result.html
    assert "font-family:ui-sans-serif, system-ui, sans-serif" in result.html
    assert "--vs-gap:1rem;" in result.html
    assert "--vs-radius:10px;" in result.html


def test_lift_html_reports_roles_groups_and_stable_topology():
    html = """
    <main>
      <h1>Metrics</h1>
      <section>
        <article><h2>Revenue</h2><p>$10</p><button>Open</button></article>
        <article><h2>Churn</h2><p>4%</p><button>Review</button></article>
      </section>
    </main>
    """

    first = lift_html(html)
    second = lift_html(html.replace("Revenue", "ARR"))

    roles = {(role.role, role.text) for role in first.roles}
    assert ("heading", "Metrics") in roles
    assert ("value", "$10") in roles
    assert ("action", "Open") in roles
    assert len(first.group_candidates) >= 1
    assert first.topology_fingerprint.hex == second.topology_fingerprint.hex


def test_diff_html_reports_narrow_semantic_changes():
    result = diff_html(
        "<h1>Old</h1><ul><li>A</li></ul><table><tr><td>$1</td></tr></table>",
        "<h1>New</h1><ul><li>B</li></ul><table><tr><td>$2</td></tr></table>",
    )
    payload = result.to_json()

    assert payload["changed_headings"] == {"removed": ["Old"], "added": ["New"]}
    assert payload["diff_version"] == 1
    assert payload["basis"] == "lift_v1"
    assert payload["changed_values"] == {"removed": ["$1"], "added": ["$2"]}
    assert payload["changed_lists"]["added"] == [["B"]]
    assert payload["changed_tables"]["removed"] == [["$1"]]
    assert 0 <= payload["topology_similarity"] <= 1


def test_oversized_html_fails_before_parsing():
    with pytest.raises(HtmlInputError, match="exceeds"):
        compile_html("x" * (MAX_HTML_INPUT_BYTES + 1))


def test_sanitizer_policy_rejects_obfuscated_active_surfaces():
    html = """
    <svg><script>alert(1)</script><text>hidden</text></svg>
    <math><mi>x</mi></math>
    <template><p>hidden template</p></template>
    <form action="https://evil.example/post"><input name="x"><button formaction="javascript:bad()">Send</button></form>
    <a href=" JaVa&#x0A;ScRiPt:alert(1)">bad</a>
    <a href=" HTTPs://example.com/path ">good</a>
    <img srcset="https://example.com/a.png 1x" src="jav&#x09;ascript:bad()" alt="Bad">
    <img src="data:image/svg+xml,<svg onload=bad()>" alt="Svg">
    <p>After active content</p>
    """

    result = compile_html(html)

    assert "<svg" not in result.html
    assert "<math" not in result.html
    assert "<template" not in result.html
    assert "<form" not in result.html
    assert "<input" not in result.html
    assert "formaction" not in result.html
    assert "javascript:" not in result.html.lower()
    assert "srcset" not in result.html
    assert "data:image/svg" not in result.html
    assert "hidden template" not in result.html
    assert "After active content" in result.html
    assert 'href="https://example.com/path"' in result.html
    assert 'rel="noopener noreferrer"' in result.html


def test_protocol_relative_autofetch_guard(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("proto_rel")
    result = compile_html('<img src="//example.com/chart.png" alt="Chart"><a href="//example.com/report">Report</a>')
    lowered = result.html.lower()

    assert 'src="//' not in lowered
    assert 'href="//' not in lowered
    assert 'href="https://example.com/chart.png"' in result.html
    assert 'href="https://example.com/report"' in result.html
    assert "External image: Chart" in result.html
    assert result.manifest["external_refs"] == [
        {"attr": "src", "behavior": "inert_placeholder", "kind": "image", "url": "https://example.com/chart.png"},
        {"attr": "href", "behavior": "user_click", "kind": "link", "url": "https://example.com/report"},
    ]

    write_html_compile_result(result, tmp_path)

    assert cli_main(["check", str(tmp_path)]) == 0


def test_html_dom_depth_is_bounded_not_recursion_error():
    deep = "<div>" * (MAX_DOM_DEPTH + 40) + "x" + "</div>" * (MAX_DOM_DEPTH + 40)
    with pytest.raises(HtmlInputError) as exc:
        compile_html(deep)
    assert exc.value.code == "HTML_DOM_DEPTH_EXCEEDED"

    # A well-under-limit document still compiles.
    shallow = "<section>" * 10 + "Hello" + "</section>" * 10
    assert compile_html(shallow).html


def test_backslash_protocol_relative_urls_are_treated_as_cross_origin(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("backslash_proto")
    # Browsers resolve "/\\host", "\\/host", "\\\\host" and percent-encoded
    # "%5c" the same as "//host" (protocol-relative / cross-origin). They must be
    # caught like "//host" instead of slipping through as same-origin relative.
    result = compile_html(
        '<img src="/\\evil.com/chart.png" alt="Chart">'
        '<a href="/%5Cevil.com/report">Report</a>'
    )
    lowered = result.html.lower()

    assert 'src="/\\' not in result.html
    assert 'href="/\\' not in result.html
    assert "%5c" not in lowered
    assert 'href="https://evil.com/report"' in result.html
    assert "External image: Chart" in result.html
    assert 'rel="noopener noreferrer"' in result.html
    assert result.manifest["external_refs"] == [
        {"attr": "src", "behavior": "inert_placeholder", "kind": "image", "url": "https://evil.com/chart.png"},
        {"attr": "href", "behavior": "user_click", "kind": "link", "url": "https://evil.com/report"},
    ]

    write_html_compile_result(result, tmp_path)

    # The governed artifact must not pass `check` while still beaconing out.
    assert cli_main(["check", str(tmp_path)]) == 0


def test_check_autofetch_guard_catches_backslash_protocol_relative():
    from viewspec.local_tools import _contains_remote_http_reference

    for beacon in ("/\\evil.com/p.png", "\\/evil.com", "\\\\evil.com", "/%5Cevil.com", "//evil.com"):
        assert _contains_remote_http_reference(beacon) is True, beacon
    for benign in ("/assets/logo.png", "./x.png", "#anchor", "data:image/png;base64,AAAA"):
        assert _contains_remote_http_reference(benign) is False, benign


def test_check_autofetch_guard_catches_control_whitespace_scheme():
    # Browsers strip tab/LF/CR from URLs, so "https:/<ctrl>/evil.com" resolves to a
    # cross-origin authority even though it has no literal "//". The check gate must
    # collapse control whitespace the same way the compiler-side URL policy does, or a
    # tampered artifact beacons while `viewspec check` reports ok.
    from viewspec.local_tools import _contains_remote_http_reference

    for beacon in (
        "https:/\t/evil.com",
        "https:/\n/evil.com",
        "https:/\r/evil.com",
        "/\t/evil.com",
        "https:/\t\\evil.com",
    ):
        assert _contains_remote_http_reference(beacon) is True, repr(beacon)
    for benign in ("/assets/logo.png", "#anchor", "data:image/png;base64,AAAA"):
        assert _contains_remote_http_reference(benign) is False, repr(benign)


@given(
    scheme=st.sampled_from(("", "http:", "https:", "HTTPs:")),
    first_separator=st.sampled_from(("/", "\\", "%5c", "%5C")),
    second_separator=st.sampled_from(("/", "\\", "%5c", "%5C")),
    controls=st.text(alphabet="\x00\t\n\r\x1f\x20\x7f", min_size=0, max_size=6),
)
def test_url_policy_rejects_every_browser_equivalent_obfuscated_authority(
    scheme: str,
    first_separator: str,
    second_separator: str,
    controls: str,
):
    from viewspec.local_tools import _contains_remote_http_reference

    candidate = f"{scheme}{first_separator}{controls}{second_separator}evil.example/beacon"
    assert _contains_remote_http_reference(candidate) is True, repr(candidate)


def test_check_certifies_no_beacon_but_rejects_tampered_control_whitespace_artifact(tmp_path):
    # End-to-end: the compiled artifact is clean and passes check; a hand-tampered
    # index.html with a control-whitespace-obfuscated remote src must fail check.
    from viewspec.local_tools import file_hash

    result = compile_html("<h1>Report</h1><p>Revenue</p>")
    write_html_compile_result(result, tmp_path)
    assert cli_main(["check", str(tmp_path)]) == 0  # clean artifact certifies

    index = tmp_path / "index.html"
    tampered = index.read_text(encoding="utf-8").replace(
        "</body>", '<img src="https:/\t/evil.example/track"></body>', 1
    )
    index.write_text(tampered, encoding="utf-8")
    manifest_path = tmp_path / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(index)  # re-stamp so only the beacon is "wrong"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert cli_main(["check", str(tmp_path)]) == 2  # tampered beacon is rejected


def test_nested_void_tags_inside_stripped_content_do_not_swallow_document():
    result = compile_html("<script><img src=x></script><h1>Still Here</h1><p>$4</p>")

    assert "Still Here" in result.html
    assert ("heading", "Still Here") in {(role.role, role.text) for role in result.lift.roles}


def test_manifest_contract_is_stable_and_diagnostics_are_deduped():
    result = compile_html('<p onclick="x()" onmouseover="y()">Hello</p><a href="javascript:bad()">Open</a>')
    manifest = result.manifest

    assert {
        "version",
        "manifest_schema_version",
        "kind",
        "sdk_version",
        "source_name",
        "raw_source_hash",
        "source_hash",
        "design_hash",
        "artifact_hash",
        "command",
        "command_args",
        "policy_version",
        "guarantees",
        "nodes",
        "diagnostics",
        "external_refs",
    }.issubset(manifest)
    assert all({"severity", "code", "message"}.issubset(item) for item in manifest["diagnostics"])
    assert manifest["manifest_schema_version"] == 1
    assert manifest["artifact_hash"] == hashlib.sha256(result.html.encode("utf-8")).hexdigest()
    assert manifest["raw_source_hash"] == hashlib.sha256(
        '<p onclick="x()" onmouseover="y()">Hello</p><a href="javascript:bad()">Open</a>'.encode("utf-8")
    ).hexdigest()
    assert manifest["command_args"] == ["compile_html"]
    keys = {(item["code"], item.get("node_id", ""), item.get("path", ""), item["message"]) for item in result.diagnostics}
    assert len(keys) == len(result.diagnostics)
    assert result.lift.source_hash == lift_html('<p onclick="other()">Hello</p><a>Open</a>').source_hash


def test_manifest_v1_schema_and_golden_fixture_match_generated_shape():
    schema = json.loads(ROOT.joinpath("docs/manifest-v1.schema.json").read_text(encoding="utf-8"))
    golden = json.loads(ROOT.joinpath("tests/fixtures/raw_html_manifest_v1.golden.json").read_text(encoding="utf-8"))
    manifest = compile_html("<h1>Golden</h1>", source_name="golden.html").manifest

    for field in schema["required"]:
        assert field in manifest
    for field, value in golden.items():
        assert manifest[field] == value
    assert re.fullmatch(schema["properties"]["source_hash"]["pattern"], manifest["source_hash"])
    assert re.fullmatch(schema["properties"]["artifact_hash"]["pattern"], manifest["artifact_hash"])
    assert isinstance(manifest["nodes"], dict)
    assert schema["properties"]["design"]["$ref"] == "#/$defs/designMetadata"
    assert schema["properties"]["guarantees"]["required"] == [
        "sdk_network_calls",
        "artifact_autofetch_network",
        "network_calls",
        "decompilation",
    ]
    raw_clause = next(item for item in schema["allOf"] if item["if"]["properties"]["kind"]["const"] == "raw_html_compile")
    intent_clause = next(item for item in schema["allOf"] if item["if"]["properties"]["kind"]["const"] == "intent_bundle_compile")
    assert raw_clause["then"]["properties"]["command"]["const"] == "compile_html"
    assert raw_clause["then"]["properties"]["policy_version"]["const"] == "viewspec-raw-html-allowlist@1"
    assert raw_clause["then"]["properties"]["nodes"]["propertyNames"]["$ref"] == "#/$defs/safeId"
    assert raw_clause["then"]["properties"]["nodes"]["additionalProperties"]["$ref"] == "#/$defs/rawHtmlNode"
    assert raw_clause["then"]["properties"]["guarantees"]["properties"]["decompilation"]["const"] == "not_claimed"
    assert intent_clause["then"]["properties"]["command"]["const"] == "compile"
    assert intent_clause["then"]["properties"]["policy_version"]["const"] == "viewspec-intent-bundle@1"
    assert intent_clause["then"]["properties"]["nodes"]["propertyNames"]["$ref"] == "#/$defs/safeId"
    assert intent_clause["then"]["properties"]["nodes"]["additionalProperties"]["$ref"] == "#/$defs/intentBundleNode"
    assert intent_clause["then"]["properties"]["guarantees"]["properties"]["decompilation"]["const"] == "not_applicable"
    assert schema["$defs"]["intentBundleNode"]["required"] == [
        "ir_id",
        "primitive",
        "content_refs",
        "intent_refs",
        "style_tokens",
        "props",
    ]
    assert schema["$defs"]["designMetadata"]["required"] == [
        "name",
        "design_hash",
        "lint_summary",
        "findings",
        "applied_tokens",
        "ignored_tokens",
        "dropped_tokens",
        "mode_defaults",
    ]


def test_manifest_v1_schema_documents_intent_trust_boundary(tmp_path):
    schema = json.loads(ROOT.joinpath("docs/manifest-v1.schema.json").read_text(encoding="utf-8"))
    intent_node_schema = schema["$defs"]["intentBundleNode"]

    assert schema["$defs"]["safeId"]["pattern"] == "^[A-Za-z0-9_.-]+$"
    assert schema["$defs"]["viewspecIntentRef"]["pattern"] == "^viewspec:(view|region|binding|group|motif|style|action):[A-Za-z0-9_.-]+$"
    assert intent_node_schema["properties"]["ir_id"]["$ref"] == "#/$defs/safeId"
    assert intent_node_schema["properties"]["intent_refs"]["minItems"] == 1
    assert intent_node_schema["properties"]["props"]["properties"]["binding_id"]["$ref"] == "#/$defs/safeId"
    assert intent_node_schema["properties"]["props"]["properties"]["action_id"]["$ref"] == "#/$defs/safeId"
    assert intent_node_schema["properties"]["props"]["properties"]["target_ref"]["pattern"] == "^(region|binding|motif|view):[A-Za-z0-9_.-]+$"
    assert intent_node_schema["properties"]["props"]["properties"]["detail_role"]["enum"] == ["term", "description"]
    assert intent_node_schema["properties"]["props"]["properties"]["empty_state_role"]["enum"] == ["title", "description", "detail"]
    assert intent_node_schema["properties"]["props"]["properties"]["state_motif_role"]["enum"] == ["title", "description", "detail"]
    assert intent_node_schema["properties"]["props"]["properties"]["state_role"]["enum"] == ["loading", "error"]
    assert intent_node_schema["allOf"][0]["then"]["properties"]["content_refs"]["$ref"] == "#/$defs/nonEmptyStringList"
    assert intent_node_schema["allOf"][1]["then"]["properties"]["props"]["required"] == [
        "action_id",
        "action_kind",
        "payload_bindings",
    ]

    builder = ViewSpecBuilder("schema_intent")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1", id="alpha")
    builder.add_action("open_alpha", "select", "Open Alpha", target_region="main", target_ref="binding:alpha_label")
    bundle_path = tmp_path / "viewspec.intent.json"
    bundle_path.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(bundle_path), "--out", str(out_dir)]) == 0
    manifest = json.loads(out_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    binding_entry = manifest["nodes"]["dom-binding_alpha_label"]
    action_entry = manifest["nodes"]["dom-action_open_alpha"]

    assert re.fullmatch(schema["$defs"]["safeId"]["pattern"], binding_entry["ir_id"])
    assert binding_entry["content_refs"] == ["node:alpha#attr:label"]
    assert binding_entry["intent_refs"] == ["viewspec:binding:alpha_label"]
    assert action_entry["props"]["target_ref"] == "binding:alpha_label"
    assert action_entry["props"]["action_kind"] == "select"
    assert action_entry["props"]["payload_bindings"] == []


def test_design_hash_changes_with_design_content():
    first = compile_html("<h1>Report</h1>", design=load_design_system(content=DESIGN))
    second_design = DESIGN.replace("#123456", "#654321")
    second = compile_html("<h1>Report</h1>", design=load_design_system(content=second_design))

    assert first.manifest["design_hash"] != second.manifest["design_hash"]
    assert first.manifest["source_hash"] == second.manifest["source_hash"]


def test_raw_html_artifact_has_no_autofetch_vectors():
    result = compile_html(
        '<meta http-equiv="refresh" content="0;url=https://example.com">'
        '<script src="https://example.com/x.js"></script>'
        '<iframe src="https://example.com/frame"></iframe>'
        '<form action="https://example.com/post"><button>Post</button></form>'
        '<img src="https://example.com/chart.png" alt="Chart">'
        '<a href="https://example.com">Example</a>'
    )
    html = result.html.lower()

    assert "<script" not in html
    assert "<iframe" not in html
    assert "<embed" not in html
    assert "<form" not in html
    assert "srcset=" not in html
    assert "http-equiv" not in html
    assert "@import" not in html
    assert "url(" not in html
    assert not re.search(r'\s(?:src|poster|background|action)=["\']https?://', html)
    assert 'href="https://example.com"' in result.html


def test_local_python_apis_make_no_socket_calls(monkeypatch):
    def fail_socket(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    monkeypatch.setattr(socket.socket, "connect", fail_socket)

    design = load_design_system(content=DESIGN)
    compile_html("<h1>Report</h1>", design=design)
    lift_html("<h1>Report</h1>")
    diff_html("<h1>Old</h1>", "<h1>New</h1>")

    builder = ViewSpecBuilder("local_no_network")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="A", value="1", id="a")
    compile(builder.build_bundle(), design=design)


def test_cli_compile_lift_diff_make_no_socket_calls(tmp_path, monkeypatch):
    def fail_socket(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    monkeypatch.setattr(socket.socket, "connect", fail_socket)

    html_path = tmp_path / "input.html"
    html_path.write_text("<h1>Report</h1><p>$1</p>", encoding="utf-8")
    new_path = tmp_path / "new.html"
    new_path.write_text("<h1>Report 2</h1><p>$2</p>", encoding="utf-8")

    assert cli_main(["compile", str(html_path), "--out", str(tmp_path / "dist")]) == 0
    assert cli_main(["lift", str(html_path), "--out", str(tmp_path / "lift.json")]) == 0
    assert cli_main(["diff", str(html_path), str(new_path), "--json"]) == 0
