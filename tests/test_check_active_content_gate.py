"""`viewspec check` active-content gate: known bypasses and the scan cost bound.

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
from viewspec.cli import main as cli_main
from viewspec.intent_tools import compile_intent_bundle_file_tool, init_intent_file, starter_intent_payload
from viewspec.local_tools import check_artifact_dir, file_hash
from viewspec.local_tools_validators import (
    _BEACON_REMOTE_URL_ATTR_RE,
    _contains_remote_http_reference,
    _has_ambiguous_markup,
    _validate_no_autofetch_surfaces,
    css_has_unsafe_reference,
    find_tag_blocks,
)

HANDLER = "CHECK_INLINE_EVENT_HANDLER"
SCHEME = "CHECK_UNSAFE_URL_SCHEME"
UNCLOSED_SCRIPT = "CHECK_UNCLOSED_SCRIPT"
UNGOVERNED = "CHECK_RAW_HTML_DOM_UNGOVERNED"
AMBIGUOUS = "CHECK_AMBIGUOUS_MARKUP"
AUTOFETCH = "index.html contains an active or auto-fetching surface"
REMOTE_ATTR = "index.html contains an auto-fetching remote URL attribute"


def _compile_intent_artifact(tmp_path: Path) -> Path:
    init_intent_file(tmp_path / "viewspec.intent.json")
    assert compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)["ok"] is True
    return tmp_path / "dist"


def _tamper(artifact_dir: Path, mutate) -> None:
    """Rewrite index.html and re-hash the (unsigned) manifest exactly as an attacker would did."""
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
        ("<p style=\"background:image-set('https://evil.example/x' 1x)\">x</p>", AUTOFETCH),
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
        # http(s) URLs need no "//": browsers resolve these to the remote host (finding F9).
        ("<img src='https:evil.example/beacon'>", REMOTE_ATTR),
        ("<img src='http:evil.example/beacon'>", REMOTE_ATTR),
        ("<img src='https:/evil.example/beacon'>", REMOTE_ATTR),
        ("<img src=HTTPS:evil.example/beacon>", REMOTE_ATTR),
        ("<img src=' h&#09;ttps:evil.example/beacon'>", REMOTE_ATTR),
        ("<video poster='http:evil.example/p'></video>", REMOTE_ATTR),
        ("<img srcset='http:evil.example/a 1x'>", REMOTE_ATTR),
        ("<img srcset='x.png 1x, https:evil.example/a 2x'>", REMOTE_ATTR),
        ("<a href='/x' ping='http:evil.example/p'>x</a>", REMOTE_ATTR),
        ("<a href='/x' ping='/local https:evil.example/p'>x</a>", REMOTE_ATTR),
        ("<svg><filter><feImage href='https:evil.example/x'/></filter></svg>", REMOTE_ATTR),
        ("<svg><image href='https:evil.example/i'/></svg>", REMOTE_ATTR),
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
        (lambda html: html + "\n<img src='https:evil.example/x'", REMOTE_ATTR),
        (lambda html: html + "\n<img srcset='x.png 1x, http:evil.example/x 2x'", REMOTE_ATTR),
        (lambda html: html + '\n<p style="background:\\75rl(https://evil.example/x)"', AUTOFETCH),
        (lambda html: html + "\n<script>alert(1)", UNCLOSED_SCRIPT),
    ],
)
def test_check_rejects_handlers_on_existing_nodes_and_unterminated_tags(tmp_path, mutate, expected):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, mutate)

    _assert_rejected(check_artifact_dir(artifact_dir), expected)


def test_check_surfaces_registered_code_path_and_fix(tmp_path):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html.replace("</body>", "<img src=x onerror=alert(1)></body>", 1))

    checked = check_artifact_dir(artifact_dir)

    assert checked["ok"] is False
    handler = next(issue for issue in checked["issues"] if issue["code"] == HANDLER)
    assert handler["path"] == "index.html"
    assert handler["message"] == "index.html contains an inline event handler attribute"
    assert handler["fix"].startswith("Remove every on* attribute")
    assert all(set(issue) == {"code", "path", "message", "fix"} for issue in checked["issues"])


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
    assert cli_main(["compile", str(tmp_path / "report.html"), "--out", str(tmp_path / "dist")]) == 0
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


# Markup that Python's HTMLParser and a browser tokenize differently (finding F1). In each payload the
# element after the construct is live in a browser, but HTMLParser (on at least one 3.11-3.14 release)
# folds it into a declaration, comment or raw-text run, so the start-tag probes never see it.
AMBIGUOUS_MARKUP_PAYLOADS = [
    # A browser ends `<![CDATA[` in HTML content at the first ">"; HTMLParser runs it to "]]>".
    "<![CDATA[><img src=x onerror=alert(1)>]]>",
    "<![CDATA[><a href='javascript:alert(1)'>x</a>]]>",
    "<![CDATA[><div>ungoverned</div>]]>",
    "<![CDATA[><svg onload=alert(1)>]]>",
    # Other `<![` sections end at "]>" for HTMLParser.
    "<![if x><img src=x onerror=alert(1)>]>",
    # Abrupt comments and the `--!>` closer end a browser comment earlier than some HTMLParser releases.
    "<!--><img src=x onerror=alert(1)>-->",
    "<!---><img src=x onerror=alert(1)>-->",
    "<!-- x --!><img src=x onerror=alert(1)>-->",
    # The rest of the family is rejected too: no emitter writes any of it.
    "<!-- x --><p>well-formed comment</p>",
    "<!x><p>bogus declaration</p>",
    "<?x><p>processing instruction</p>",
    # Raw-text elements are markup containers inside <svg>/<math>, raw text for HTMLParser.
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<svg><title><img src=x onerror=alert(1)></title></svg>",
    "<math><style><img src=x onerror=alert(1)></style></math>",
    # Round 2 (finding F1 remainder): the whole parser-differential raw-text class, closed with
    # parser-independent rules rather than one payload at a time. `<noscript>` is raw text in every
    # browser (scripting on) but markup for HTMLParser (scripting off), so an attribute-quote smuggle
    # hides the live element on ALL supported versions.
    "<noscript><p title='</noscript><img src=x onerror=alert(1)>'></noscript>",
    "<noscript><p title='</noscript><a href=javascript:alert(1)>x</a>'></noscript>",
    "<NOSCRIPT><p title='</noscript\n><img src=x onerror=alert(1)>'></NOSCRIPT>",
    # RCDATA/raw-text elements a browser always treats as text but older 3.11-3.14 html.parser
    # releases (e.g. 3.12.4, which has no RCDATA set) parse as markup.
    "<title><p title='</title><img src=x onerror=alert(1)>'>",
    "<textarea><p title='</textarea><img src=x onerror=alert(1)>'></textarea>",
    "<xmp><p title='</xmp><img src=x onerror=alert(1)>'></xmp>",
    "<noembed><p title='</noembed><img src=x onerror=alert(1)>'></noembed>",
    "<noframes><p title='</noframes><img src=x onerror=alert(1)>'></noframes>",
    # <plaintext> is never a text element for HTMLParser; a browser runs everything after it as text.
    "<plaintext><img src=x onerror=alert(1)>",
    # End tags a browser accepts but older html.parser does not, ending raw text early.
    "<style></style/><img src=x onerror=alert(1)>",
    "<style></style foo><img src=x onerror=alert(1)>",
    "<script></script/><img src=x onerror=alert(1)>",
    "<script></script foo><img src=x onerror=alert(1)>",
    # NUL: a browser replaces U+0000 and re-tokenizes; HTMLParser keeps it, so a start tag one
    # parser sees and the other does not.
    "\x00<img src=x onerror=alert(1)>",
    "<x\x00 onerror=alert(1) autofocus>payload</x>",
]


@pytest.mark.parametrize("snippet", AMBIGUOUS_MARKUP_PAYLOADS)
def test_check_rejects_parser_differential_markup_in_intent_artifact(tmp_path, snippet):
    artifact_dir = _compile_intent_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html.replace("</body>", f"{snippet}</body>", 1))

    _assert_rejected(check_artifact_dir(artifact_dir), AMBIGUOUS)


@pytest.mark.parametrize("snippet", AMBIGUOUS_MARKUP_PAYLOADS)
def test_check_rejects_parser_differential_markup_in_raw_html_artifact(tmp_path, snippet):
    artifact_dir = _compile_raw_html_artifact(tmp_path)
    _tamper(artifact_dir, lambda html: html.replace("</body>", f"{snippet}</body>", 1))

    checked = check_artifact_dir(artifact_dir)

    _assert_rejected(checked, AMBIGUOUS)
    # The raw-HTML DOM probe refuses to vouch for an element list HTMLParser may have under-counted.
    assert any(
        error.startswith(f"{UNGOVERNED}: index.html contains markup the DOM probe") for error in checked["errors"]
    )


def test_check_rejects_cdata_hidden_content_in_raw_html_artifact(tmp_path):
    artifact_dir = _compile_raw_html_artifact(tmp_path)
    _tamper(
        artifact_dir,
        lambda html: html.replace(
            "</body>",
            "<![CDATA[><div><a href='javascript:alert(1)'>x</a><img src=x onerror=alert(1)></div>]]></body>",
            1,
        ),
    )

    checked = check_artifact_dir(artifact_dir)

    assert checked["ok"] is False
    ambiguous = next(issue for issue in checked["issues"] if issue["code"] == AMBIGUOUS)
    assert ambiguous["path"] == "index.html"
    assert ambiguous["fix"].startswith("Remove comments, <![...]> sections")


def test_ambiguous_markup_rule_accepts_what_the_sdk_writes():
    # The emitters and the raw-HTML sanitizer write only the doctype; text and attribute values are escaped.
    assert _has_ambiguous_markup("<!DOCTYPE html>\n<html><body><p>&lt;!-- note --&gt;</p></body></html>") is False
    assert _has_ambiguous_markup("<!doctype html><title>t</title><style>a{}</style><script>x</script>") is False
    assert _has_ambiguous_markup('<p title="&lt;![CDATA[ x ]]&gt;">&lt;?x?&gt;</p>') is False
    # A doctype carrying anything else, or a second construct after it, is rejected.
    assert _has_ambiguous_markup("<!DOCTYPE html><!-- x -->") is True
    assert _has_ambiguous_markup("<!DOCTYPE html SYSTEM 'x'>") is True
    # Raw-text openers are fine before foreign content, rejected after it.
    assert _has_ambiguous_markup("<style>a{}</style><svg></svg>") is False
    assert _has_ambiguous_markup("<svg></svg><style>a{}</style>") is True


def test_ambiguous_markup_closes_the_raw_text_class(tmp_path):
    # Elements no emitter writes, rejected wherever they open (finding F1 remainder).
    for tag in ("noscript", "textarea", "xmp", "noembed", "noframes", "plaintext", "iframe"):
        assert _has_ambiguous_markup(f"<{tag}>x</{tag}>") is True, tag
        assert _has_ambiguous_markup(f"<{tag.upper()} foo>x") is True, tag
    # The attribute-quote smuggle a browser runs and HTMLParser folds into a <p> attribute.
    assert _has_ambiguous_markup("<noscript><p title='</noscript><img src=x onerror=alert(1)>'>") is True
    # Emitted text elements: a "<" inside the browser text run, or a closer carrying a solidus or
    # attributes (which older html.parser does not honour), is a parser disagreement.
    assert _has_ambiguous_markup("<title><p title='</title><img onerror=alert(1)>'>") is True
    assert _has_ambiguous_markup("<style></style/><img onerror=alert(1)>") is True
    assert _has_ambiguous_markup("<script></script foo><img onerror=alert(1)>") is True
    # A NUL anywhere.
    assert _has_ambiguous_markup("<p>ok</p>\x00") is True
    assert _has_ambiguous_markup("<x\x00 onerror=alert(1)>") is True
    # What the emitters actually write stays accepted: one escaped <title>, one <style>, the pinned
    # <script>, each closed by a plain </tag> with no markup inside.
    assert _has_ambiguous_markup("<title>Quarterly Report</title>") is False
    assert _has_ambiguous_markup("<style>body{color:red}</style>") is False
    assert _has_ambiguous_markup("<script>var a = 1;\nreturn a;</script >") is False
    assert _has_ambiguous_markup("<title>t</title><p>body</p><title>t2</title>") is False


def test_remote_url_backstop_ignores_scheme_like_prose(tmp_path):
    # Finding F9 remainder: the flat backstop scans tag source, so a scheme-looking token in visible
    # text (an intent value label, a raw-HTML paragraph) is not read as an attribute value, while a
    # real auto-fetch attribute still is.
    assert _validate_no_autofetch_surfaces("<p>Set the data=https: and ping=http: prefixes.</p>") == []
    assert _validate_no_autofetch_surfaces("<p>Visit https://docs.example for the guide.</p>") == []
    assert _validate_no_autofetch_surfaces("<img src='https:evil.example/x'>") == [REMOTE_ATTR]
    assert _validate_no_autofetch_surfaces("<a ping='http:evil.example/p'>x</a>") == [REMOTE_ATTR]

    # End to end: an intent value label full of scheme-like prose compiles and passes check.
    payload = starter_intent_payload()
    payload["substrate"]["nodes"]["revenue"]["attrs"]["label"] = "Endpoint data=https: and ping=http: prefixes"
    (tmp_path / "viewspec.intent.json").write_text(json.dumps(payload), encoding="utf-8")
    compiled = compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)
    assert compiled["ok"] is True, compiled
    assert check_artifact_dir(tmp_path / "dist")["ok"] is True


def test_raw_html_paragraph_with_scheme_like_prose_passes_check(tmp_path):
    (tmp_path / "report.html").write_text(
        "<h1>API</h1><p>Set the data=https: prefix and the ping=http: value in prose.</p>",
        encoding="utf-8",
    )
    assert cli_main(["compile", str(tmp_path / "report.html"), "--out", str(tmp_path / "dist")]) == 0
    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert "data=https:" in html  # the prose survived into the artifact
    assert check_artifact_dir(tmp_path / "dist")["ok"] is True


def test_intent_text_with_markup_like_content_still_passes_check(tmp_path):
    payload = starter_intent_payload()
    attrs = payload["substrate"]["nodes"]["revenue"]["attrs"]
    attrs["value"] = "<!-- a --> <![CDATA[ b ]]> <?c?> <!-->"
    attrs["label"] = "<svg><style>d</style></svg>"
    (tmp_path / "viewspec.intent.json").write_text(json.dumps(payload), encoding="utf-8")
    compiled = compile_intent_bundle_file_tool("viewspec.intent.json", "dist", cwd=tmp_path)
    assert compiled["ok"] is True, compiled

    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert "&lt;![CDATA[ b ]]&gt;" in html
    checked = check_artifact_dir(tmp_path / "dist")
    assert checked["ok"] is True, checked["errors"]


def test_raw_html_source_with_comments_and_cdata_compiles_to_a_passing_artifact(tmp_path):
    (tmp_path / "report.html").write_text(
        "<h1>Report</h1><!-- note --><p>a <![CDATA[ b ]]> c <?pi x?> d</p><!--><p>e</p><svg><style>x</style></svg>",
        encoding="utf-8",
    )
    assert cli_main(["compile", str(tmp_path / "report.html"), "--out", str(tmp_path / "dist")]) == 0

    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert "<!--" not in html and "<![" not in html and "<?" not in html and "<svg" not in html
    checked = check_artifact_dir(tmp_path / "dist")
    assert checked["ok"] is True, checked["errors"]


@pytest.mark.parametrize(
    "value",
    [
        "https:evil.example/x",
        "http:evil.example/x",
        "https:/evil.example/x",
        "HTTP:evil.example/x",
        " \thttps:evil.example/x",
        "h\nttps:evil.example/x",
        "x.png 1x, http:evil.example/a 2x",
        "x.png 1x,https:evil.example/a 2x",
        "/local https:evil.example/p",
        "https://evil.example/x",
        "//evil.example/x",
        "/\\evil.example/x",
    ],
)
def test_remote_reference_detects_http_urls_with_or_without_slashes(value):
    assert _contains_remote_http_reference(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "./local.png",
        "/relative/path",
        "#top",
        "x.png 1x, y.png 2x",
        "mailto:ops@example.com",
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
        "/docs/http-guide.png",
        "httpsfoo.png",
    ],
)
def test_remote_reference_accepts_local_urls(value):
    assert _contains_remote_http_reference(value) is False


def test_flat_remote_url_backstop_matches_slashless_http_schemes():
    for html in ("<img src=https:evil.example/x", "<img src='http:evil.example/x'", '<video poster="https:/e/x"'):
        assert _BEACON_REMOTE_URL_ATTR_RE.search(html), html
    assert _BEACON_REMOTE_URL_ATTR_RE.search('<img src="./http:local.png"') is None


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
        return html.replace(
            f'<h1 data-viewspec-node-id="{node_id}"', f'<h1 data-viewspec-node-id="{node_id}" onclick="alert(1)"', 1
        )

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
    link_id = next(
        node_id
        for node_id, entry in manifest["nodes"].items()
        if entry["attrs"].get("href") == "mailto:ops@example.com"
    )
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
    function=st.sampled_from(
        ("url", "URL", "u\\72l", "\\75rl", "ur\\6c", "u/**/rl", "image-set", "-webkit-image-set", "image", "src")
    ),
    whitespace=st.text(alphabet=" \t\n\r\f", min_size=0, max_size=8),
)
def test_css_gate_rejects_escape_comment_and_function_variants(function, whitespace):
    assert (
        css_has_unsafe_reference(f"background-image: {function}{whitespace}(https://evil.example/pixel.png);") is True
    )


def test_css_gate_accepts_emitter_shaped_css():
    assert (
        css_has_unsafe_reference("color: #1f2937; font: 14px/1.4 ui-sans-serif; background: rgb(15 118 110 / 0.06);")
        is False
    )
    assert (
        css_has_unsafe_reference(
            "/* ViewSpec PresentationPlan v1: shared by static and React AppBundle targets. */ .x{gap:1rem}"
        )
        is False
    )
    assert (
        css_has_unsafe_reference("--vs-radius: 12px; filter: saturate(var(--vs-saturation)); aspect-ratio: 16 / 9;")
        is False
    )


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
    _assert_screen_artifact_shell_safe(
        "<html><body>" + "<script>" * 20_000 + "</script>" * 20_000 + "</body></html>", "s1"
    )
    assert time.perf_counter() - started < 2.0

    for css in (
        "\\75rl(https://evil.example/x)",
        "u\\72 l(x)",
        "url/**/(x)",
        "image-set('https://evil.example/x' 1x)",
        "@\\69mport 'x'",
    ):
        with pytest.raises(AppBundleProofFailure) as rejected:
            _assert_screen_artifact_shell_safe(
                f"<html><head><style>.x{{background:{css}}}</style></head><body><p>x</p></body></html>", "s1"
            )
        assert rejected.value.code == "APP_SHELL_NETWORK_SURFACE_REJECTED"

    body = _extract_screen_body_fragment("<html><body>\n<p>a</p><script>x</script><p>b</p>\n</body></html>", "s1")
    assert body == "<p>a</p><p>b</p>"
    with pytest.raises(AppBundleProofFailure):
        _extract_screen_body_fragment("<html><body><p>a</p><script>x</body></html>", "s1")
