"""`viewspec check` active-content gate: the audit bypasses (S-1, S-2) and the scan cost bound (S-3).

Every payload here passed `check` with ``"ok": true`` once the unsigned manifest's artifact_hash was
re-computed, so each case tampers a freshly compiled artifact, re-hashes it, and asserts the rejection
and its registered code. The positive cases pin that legitimate emitter output (intent artifacts, the
raw-HTML sanitizer's data:image/mailto/https/placeholder surfaces, the committed demos) still passes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_shell import (
    _assert_screen_artifact_shell_safe,
    _extract_screen_body_fragment,
    _extract_screen_styles,
)
from viewspec.intent_tools import compile_intent_bundle_file_tool, init_intent_file
from viewspec.local_tools import check_artifact_dir, check_artifact_tool, compile_html_file_tool, file_hash
from viewspec.local_tools_validators import _validate_no_autofetch_surfaces, css_has_unsafe_reference, find_tag_blocks

HANDLER = "CHECK_INLINE_EVENT_HANDLER"
SCHEME = "CHECK_UNSAFE_URL_SCHEME"
UNCLOSED_SCRIPT = "CHECK_UNCLOSED_SCRIPT"
UNGOVERNED = "CHECK_RAW_HTML_DOM_UNGOVERNED"
AUTOFETCH = "index.html contains an active or auto-fetching surface"
REMOTE_ATTR = "index.html contains an auto-fetching remote URL attribute"


def _compile_intent_artifact(tmp_path: Path) -> Path:
    init_intent_file(tmp_path / "viewspec.intent.json")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True
    return tmp_path / "dist"


def _tamper(artifact_dir: Path, mutate) -> None:
    """Rewrite index.html and re-hash the (unsigned) manifest exactly as the audit reproductions did."""
    html_path = artifact_dir / "index.html"
    html_path.write_text(mutate(html_path.read_text(encoding="utf-8")), encoding="utf-8")
    manifest_path = artifact_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def _assert_rejected(result: dict, expected: str) -> None:
    assert result["ok"] is False
    if expected.startswith("CHECK_"):
        assert expected in _codes(result), result["errors"]
    else:
        assert expected in result["errors"], result["errors"]
    for issue in result["issues"]:
        assert set(issue) == {"code", "path", "message", "fix"}
        assert issue["code"] and issue["path"] and issue["message"] and issue["fix"]


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        # S-1: inline event handlers on injected and SVG/HTML elements.
        ("<img src=x onerror=alert(1)>", HANDLER),
        ("<p onclick=alert(1)>x</p>", HANDLER),
        ("<svg onload=alert(1)></svg>", HANDLER),
        ("<details open ontoggle=alert(1)>x</details>", HANDLER),
        ("<svg><set onbegin=alert(1) attributename=x dur=1s></set></svg>", HANDLER),
        ("<body onpageshow=alert(1)>", HANDLER),
        ("<p ONCLICK=alert(1)>x</p>", HANDLER),
        # S-1: javascript:/vbscript:/data: on every URL-bearing attribute, with the decodings a browser applies.
        ("<a href='javascript:alert(1)'>x</a>", SCHEME),
        ("<a href='JAVASCRIPT:alert(1)'>x</a>", SCHEME),
        ("<a href='&#106;avascript:alert(1)'>x</a>", SCHEME),
        ("<a href='java&#09;script:alert(1)'>x</a>", SCHEME),
        ("<a href='java&#10;script:alert(1)'>x</a>", SCHEME),
        ("<a href=' javascript:alert(1)'>x</a>", SCHEME),
        ("<a href='&#1;javascript:alert(1)'>x</a>", SCHEME),
        ("<a href='javascript&colon;alert(1)'>x</a>", SCHEME),
        ("<a href='vbscript:msgbox(1)'>x</a>", SCHEME),
        ("<svg><a href='javascript:alert(1)'><text>x</text></a></svg>", SCHEME),
        ("<svg><a xlink:href='javascript:alert(1)'><text>x</text></a></svg>", SCHEME),
        ("<math><maction href='javascript:alert(1)'>x</maction></math>", SCHEME),
        ("<a href='data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=='>x</a>", SCHEME),
        ("<a href='data:text/html,<script>alert(1)</script>'>x</a>", SCHEME),
        ("<img src='data:text/html,<script>alert(1)</script>'>", SCHEME),
        ("<img src='data:image/svg+xml,<svg onload=alert(1)>'>", SCHEME),
        ("<img srcset='javascript:alert(1) 1x'>", SCHEME),
        ("<img srcset='x.png 1x, data:text/html,x 2x'>", SCHEME),
        ("<button formaction='javascript:alert(1)'>x</button>", SCHEME),
        ("<video poster='javascript:alert(1)'></video>", SCHEME),
        ("<video src='javascript:alert(1)'></video>", SCHEME),
        ("<object data='javascript:alert(1)'></object>", SCHEME),
        ("<form action='javascript:alert(1)'></form>", SCHEME),
        # S-1: unclosed <script backstop.
        ("<script>alert(1)", UNCLOSED_SCRIPT),
        ("<script>alert(1)</script", UNCLOSED_SCRIPT),
        # `</script foo</body>` closes at </body>'s ">" exactly as a browser does: a closed, unknown script.
        ("<script>alert(1)</script foo", "index.html contains an unknown inline script"),
        ("<script>alert(1)</script >", "index.html contains an unknown inline script"),
        # S-2: CSS escapes, comments, URL-loading functions, feImage, ping.
        ('<p style="background:\\75rl(https://evil.example/x)">x</p>', AUTOFETCH),
        ('<p style="background:u\\72 l(https://evil.example/x)">x</p>', AUTOFETCH),
        ('<p style="background:url/**/(https://evil.example/x)">x</p>', AUTOFETCH),
        ('<p style="background:image-set(\'https://evil.example/x\' 1x)">x</p>', AUTOFETCH),
        ('<p style="background:-webkit-image-set(url(x) 1x)">x</p>', AUTOFETCH),
        ('<p style="background:image(https://evil.example/x)">x</p>', AUTOFETCH),
        ('<p style="background:src(https://evil.example/x)">x</p>', AUTOFETCH),
        ('<p style="width:expression(alert(1))">x</p>', AUTOFETCH),
        ('<p style="background:url(//evil.example/x)">x</p>', AUTOFETCH),
        ("<style>@\\69mport 'https://evil.example/x';</style>", AUTOFETCH),
        ("<style>.x{background:\\75rl(//evil.example/x)}</style>", AUTOFETCH),
        ("<style>.x{background:/**/url(https://evil.example/x)}</style>", AUTOFETCH),
        ('<style>.x{content:"/*"; background:url(https://evil.example/x)}</style>', AUTOFETCH),
        ('<style>.x{background:image-set("https://evil.example/x" 1x)}</style>', AUTOFETCH),
        ("<style>.x{background:u\\rl(https://evil.example/x)}</style>", AUTOFETCH),
        ("<style>.x{background:url(https://evil.example/x)}", AUTOFETCH),
        ("<svg><filter><feImage href='https://evil.example/x'/></filter></svg>", REMOTE_ATTR),
        ("<svg><filter><feImage xlink:href='//evil.example/x'/></filter></svg>", REMOTE_ATTR),
        ("<a href='/x' ping='https://evil.example/x'>x</a>", REMOTE_ATTR),
        ("<a href='/x' ping='//evil.example/x'>x</a>", REMOTE_ATTR),
    ],
)
def test_check_rejects_active_content_bypasses_in_intent_artifact(tmp_path, snippet, expected):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html.replace("</body>", f"{snippet}</body>", 1))

    _assert_rejected(check_artifact_dir(artifact_dir), expected)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        # A handler added to an EXISTING node, not an injected element.
        (lambda html: html.replace("<div ", '<div onclick="alert(1)" ', 1), HANDLER),
        (lambda html: html.replace("<main", "<main onmouseover=alert(1)", 1), HANDLER),
        # Browser-recoverable unterminated final tags that HTMLParser drops.
        (lambda html: html + "\n<img src=x onerror=alert(1)", HANDLER),
        (lambda html: html + "\n<a href='javascript:alert(1)'", SCHEME),
        (lambda html: html + "\n<a href='/x' ping='https://evil.example/x'", REMOTE_ATTR),
        (lambda html: html + '\n<p style="background:\\75rl(https://evil.example/x)"', AUTOFETCH),
        (lambda html: html + "\n<script>alert(1)", UNCLOSED_SCRIPT),
    ],
)
def test_check_rejects_handlers_on_existing_nodes_and_unterminated_tags(tmp_path, mutate, expected):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, mutate)

    _assert_rejected(check_artifact_dir(artifact_dir), expected)


def test_check_tool_surfaces_registered_code_path_and_fix(tmp_path):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html.replace("</body>", "<img src=x onerror=alert(1)></body>", 1))

    checked = check_artifact_tool("dist", cwd=tmp_path)

    assert checked["ok"] is False
    handler = next(error for error in checked["errors"] if error["code"] == HANDLER)
    assert handler["path"] == "index.html"
    assert handler["message"] == "index.html contains an inline event handler attribute"
    assert handler["fix"].startswith("Remove every on* attribute")
    assert all(set(error) == {"code", "path", "message", "fix"} for error in checked["errors"])
    # Legacy (unprefixed) messages keep the generic code and the tool's own fix text.
    legacy = [error for error in checked["errors"] if error["code"] == "CHECK_FAILED"]
    assert all(error["fix"] == "Re-run viewspec compile from the source HTML, or fix the tampered artifact." for error in legacy)


RAW_HTML_SOURCE = """
<h1 title="Quarterly report">Report</h1>
<p aria-label="Summary">Revenue grew. <a href="https://example.com/docs">Docs</a>,
<a href="mailto:ops@example.com">mail ops</a>, <a href="#top">top</a>, <a href="/relative/path">local</a>.</p>
<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==" alt="dot">
<img src="https://example.com/remote.png" alt="remote chart">
<img src="./local.png" alt="local">
<table><tr><th scope="col" colspan="2">Head</th></tr><tr><td>1</td><td>2</td></tr></table>
<button>Go</button>
"""


def _compile_raw_html_artifact(tmp_path: Path) -> Path:
    (tmp_path / "report.html").write_text(RAW_HTML_SOURCE, encoding="utf-8")
    result = compile_html_file_tool("report.html", "dist", cwd=tmp_path)
    assert result["ok"] is True, result["errors"]
    return tmp_path / "dist"


def test_legitimate_raw_html_surfaces_still_pass_check(tmp_path):
    # data:image on <img src>, mailto/https/#/relative hrefs, the inert external-image placeholder,
    # aria-*/title attributes and table scope/colspan are all sanitizer output and must pass.
    artifact_dir = _compile_raw_html_artifact(tmp_path)
    html = (artifact_dir / "index.html").read_text(encoding="utf-8")
    assert 'src="data:image/png;base64,' in html
    assert 'href="mailto:ops@example.com"' in html
    assert 'class="vs-external-image"' in html

    checked = check_artifact_dir(artifact_dir)

    assert checked["ok"] is True, checked["errors"]
    assert checked["issues"] == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        # An element the manifest does not govern, with and without active content.
        (lambda html: html.replace("</h1>", "</h1><p>injected</p>", 1), UNGOVERNED),
        (lambda html: html.replace("</h1>", "</h1><svg onload=alert(1)></svg>", 1), UNGOVERNED),
        (lambda html: html.replace("</h1>", "</h1><svg onload=alert(1)></svg>", 1), HANDLER),
        (lambda html: html.replace("</head>", "<style>.x{color:red}</style></head>", 1), UNGOVERNED),
        (lambda html: html.replace("<body>", "<body><div>wrapper copy</div>", 1), UNGOVERNED),
        # Attributes added to a governed node beyond its manifest attrs.
        (lambda html: html.replace("<h1 ", '<h1 onclick="alert(1)" ', 1), UNGOVERNED),
        (lambda html: html.replace("<h1 ", '<h1 onclick="alert(1)" ', 1), HANDLER),
        (lambda html: html.replace("<h1 ", '<h1 style="background:url(//evil.example/x)" ', 1), UNGOVERNED),
        (lambda html: html.replace('href="mailto:ops@example.com"', 'href="javascript:alert(1)"', 1), SCHEME),
        # Wrapper drift.
        (lambda html: html.replace('<html lang="en">', '<html lang="en" onload="alert(1)">', 1), UNGOVERNED),
    ],
)
def test_check_rejects_ungoverned_raw_html_dom(tmp_path, mutate, expected):
    artifact_dir = _compile_raw_html_artifact(tmp_path)
    _tamper(artifact_dir, mutate)

    _assert_rejected(check_artifact_dir(artifact_dir), expected)


def _tamper_raw_html_consistently(artifact_dir: Path, *, node_tag: str, dom_mutation, manifest_mutation) -> None:
    """Tamper DOM and manifest together, so only the sanitizer-allowlist rule can catch it."""
    manifest_path = artifact_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    node_id = next(node_id for node_id, entry in sorted(manifest["nodes"].items()) if entry["tag"] == node_tag)
    manifest_mutation(manifest["nodes"][node_id])
    html_path = artifact_dir / "index.html"
    html_path.write_text(dom_mutation(html_path.read_text(encoding="utf-8"), node_id), encoding="utf-8")
    manifest["artifact_hash"] = file_hash(html_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def test_check_holds_tampered_raw_html_manifest_to_the_sanitizer_allowlist(tmp_path):
    artifact_dir = _compile_raw_html_artifact(tmp_path)

    def add_handler_to_dom(html: str, node_id: str) -> str:
        return html.replace(f'<h1 data-viewspec-node-id="{node_id}"', f'<h1 data-viewspec-node-id="{node_id}" onclick="alert(1)"', 1)

    _tamper_raw_html_consistently(
        artifact_dir,
        node_tag="h1",
        dom_mutation=add_handler_to_dom,
        manifest_mutation=lambda entry: entry["attrs"].__setitem__("onclick", "alert(1)"),
    )
    checked = check_artifact_dir(artifact_dir)
    _assert_rejected(checked, UNGOVERNED)
    _assert_rejected(checked, HANDLER)
    assert not any("does not match DOM" in error for error in checked["errors"]), checked["errors"]

    (tmp_path / "second").mkdir()
    artifact_dir = _compile_raw_html_artifact(tmp_path / "second")

    def retarget_link(html: str, node_id: str) -> str:
        return html.replace('href="mailto:ops@example.com"', 'href="javascript:alert(1)"', 1)

    manifest_path = artifact_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    link_id = next(node_id for node_id, entry in manifest["nodes"].items() if entry["attrs"].get("href") == "mailto:ops@example.com")
    manifest["nodes"][link_id]["attrs"]["href"] = "javascript:alert(1)"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    _tamper(artifact_dir, lambda html: retarget_link(html, link_id))
    checked = check_artifact_dir(artifact_dir)
    _assert_rejected(checked, UNGOVERNED)
    _assert_rejected(checked, SCHEME)


def test_committed_demo_artifacts_pass_check():
    root = Path("demos/landing-compiled")
    for artifact_dir in [root, *sorted(path for path in (root / "profiles").iterdir() if path.is_dir())]:
        checked = check_artifact_dir(artifact_dir)
        assert checked["ok"] is True, (artifact_dir, checked["errors"])


@given(
    function=st.sampled_from(("url", "URL", "u\\72l", "\\75rl", "ur\\6c", "u/**/rl", "image-set", "-webkit-image-set", "image", "src")),
    whitespace=st.text(alphabet=" \t\n\r\f", min_size=0, max_size=8),
)
def test_css_gate_rejects_escape_comment_and_function_variants(function, whitespace):
    assert css_has_unsafe_reference(f"background-image: {function}{whitespace}(https://evil.example/pixel.png);") is True


def test_css_gate_accepts_emitter_shaped_css():
    assert css_has_unsafe_reference("color: #1f2937; font: 14px/1.4 ui-sans-serif; background: rgb(15 118 110 / 0.06);") is False
    assert css_has_unsafe_reference("/* ViewSpec PresentationPlan v1: shared by static and React AppBundle targets. */ .x{gap:1rem}") is False
    assert css_has_unsafe_reference("--vs-radius: 12px; filter: saturate(var(--vs-saturation)); aspect-ratio: 16 / 9;") is False


def test_autofetch_gate_rejects_unclosed_style_and_many_openers_in_linear_time():
    # S-3: 20,000 unclosed openers took minutes with the old `<style\b[^>]*>(?:(?!</style>).)*` shape.
    for opener in ("<style>", "<style x", "<script>", "<meta ", "<style>url(x)</style>"):
        html = "<html><body>" + opener * 20_000
        started = time.perf_counter()
        errors = _validate_no_autofetch_surfaces(html)
        scan = find_tag_blocks(html, "script")
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, (opener, elapsed)
        if "url(" in opener:
            assert AUTOFETCH in errors
        assert scan.truncated is False or opener == "<script>"


def test_check_artifact_dir_bounds_cost_on_crafted_openers(tmp_path):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html + "<style>" * 20_000 + "<script>" * 20_000)

    started = time.perf_counter()
    checked = check_artifact_dir(artifact_dir)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, elapsed
    assert checked["ok"] is False
    assert UNCLOSED_SCRIPT in _codes(checked)


def test_app_shell_screen_scan_is_linear_and_normalizes_css():
    with pytest.raises(AppBundleProofFailure) as unclosed:
        _extract_screen_styles("<html><head>" + "<style>" * 20_000, "s1")
    assert unclosed.value.code == "APP_SHELL_EMBEDDING_UNSUPPORTED"

    started = time.perf_counter()
    _assert_screen_artifact_shell_safe("<html><body>" + "<script>" * 20_000 + "</script>" * 20_000 + "</body></html>", "s1")
    assert time.perf_counter() - started < 2.0

    for css in ("\\75rl(https://evil.example/x)", "u\\72 l(x)", "url/**/(x)", "image-set('https://evil.example/x' 1x)", "@\\69mport 'x'"):
        with pytest.raises(AppBundleProofFailure) as rejected:
            _assert_screen_artifact_shell_safe(f"<html><head><style>.x{{background:{css}}}</style></head><body><p>x</p></body></html>", "s1")
        assert rejected.value.code == "APP_SHELL_NETWORK_SURFACE_REJECTED"

    body = _extract_screen_body_fragment("<html><body>\n<p>a</p><script>x</script><p>b</p>\n</body></html>", "s1")
    assert body == "<p>a</p><p>b</p>"
    with pytest.raises(AppBundleProofFailure):
        _extract_screen_body_fragment("<html><body><p>a</p><script>x</body></html>", "s1")
