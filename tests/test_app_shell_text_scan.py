"""C-12: the static AppBundle shell reads markup by context, so ordinary text compiles.

The default AppBundle target (html-tailwind-app) once scanned its whole HTML as raw text. Visible
text such as "HTTP: 502 Bad Gateway", "Worker (night shift)" or "Nodes online = 12" then failed the
compile with an error about network, worker or event-handler surfaces that were not there. Tags and
attributes, script blocks, and text are now checked for what can occur in each, and a full URL is
still rejected wherever it appears.

Everything used here was already in the v0.4.0 tag, so the 0.4.1 patch release can carry this file.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

import pytest

import viewspec
from viewspec.app_bundle import compile_app, starter_app_bundle
from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_shell import (
    _assert_rendered_shell_static_contract,
    _assert_screen_artifact_shell_safe,
    _collect_shell_screens,
)
from viewspec.app_starters import starter_react_app_bundle
from viewspec.cli import main as cli_main

ORDINARY_TEXT = ("HTTP: 502 Bad Gateway", "Worker (night shift)", "Nodes online = 12", "Filter: only = open")
NETWORK = "APP_SHELL_NETWORK_SURFACE_REJECTED"
EMBEDDING = "APP_SHELL_EMBEDDING_UNSUPPORTED"


def _set_screen_text(app: dict, text: str) -> None:
    app["screens"][0]["intent_bundle"]["substrate"]["nodes"]["inc_1042"]["attrs"]["value"] = text


def _set_app_title(app: dict, text: str) -> None:
    app["app"]["title"] = text


def _set_route_label(app: dict, text: str) -> None:
    app["routes"][0]["label"] = text


PLACES = {"screen text": _set_screen_text, "app title": _set_app_title, "route label": _set_route_label}


def _compile(tmp_path: Path, app: dict) -> tuple[dict, Path]:
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(app, indent=2, sort_keys=True), encoding="utf-8")
    out_dir = tmp_path / "app-dist"
    return compile_app(app_path, out_dir=out_dir, cwd=tmp_path), out_dir


def _rendered_form(place: str, text: str) -> str:
    # Screen text reaches the shell as escaped markup; the title and route labels as route JSON.
    return html.escape(text) if place == "screen text" else json.dumps(text)


@pytest.mark.parametrize("place", sorted(PLACES))
@pytest.mark.parametrize("text", ORDINARY_TEXT)
def test_ordinary_text_compiles_for_the_static_shell_and_passes_check(tmp_path, text, place):
    app = starter_app_bundle()
    PLACES[place](app, text)

    report, out_dir = _compile(tmp_path, app)

    assert report["ok"] is True, report["errors"]
    assert _rendered_form(place, text) in (out_dir / "index.html").read_text(encoding="utf-8")
    checked = viewspec.check(out_dir)
    assert checked["ok"] is True, checked["errors"]


def test_audit_reproduction_compiles_a_site(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["init", "app", "--out", "app.json"]) == 0
    payload = json.loads(Path("app.json").read_text(encoding="utf-8"))
    payload["app"]["title"] = "HTTP: 502 Bad Gateway"
    Path("app.json").write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()

    assert cli_main(["check", "app.json", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli_main(["compile", "app.json", "--out", "site"]) == 0
    assert Path("site/index.html").is_file()
    assert cli_main(["check", "site"]) == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="AppBundle V4 compiles check reducer conformance under Node")
def test_ordinary_text_compiles_in_a_static_shell_with_the_state_runtime(tmp_path):
    # The react-app starter is AppBundle V4: its static shell also carries the generated reducer,
    # the state runtime, and a second JSON data block.
    app = starter_react_app_bundle()
    app["app"]["title"] = "HTTP: 502 Bad Gateway"
    app["routes"][0]["label"] = "Nodes online = 12"
    app["routes"][1]["label"] = "Worker (night shift)"

    report, out_dir = _compile(tmp_path, app)

    assert report["ok"] is True, report["errors"]
    shell = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "ViewSpecStateRuntime" in shell
    assert json.dumps("Nodes online = 12") in shell and json.dumps("Worker (night shift)") in shell
    assert viewspec.check(out_dir)["ok"] is True


# --- real surfaces are still rejected ---------------------------------------------------------------


@pytest.fixture(scope="module")
def compiled_starter(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("starter")
    report, out_dir = _compile(root, starter_app_bundle())
    assert report["ok"] is True, report["errors"]
    return out_dir


def _screen_artifact(compiled_starter: Path) -> str:
    # The queue screen has an action button, so its artifact also holds the emitter's script block.
    text = (compiled_starter / "screens" / "queue" / "artifact" / "index.html").read_text(encoding="utf-8")
    assert text.count("<button ") == 1 and text.count("<script>\n") == 1
    return text


def _inject(document: str, anchor: str, replacement: str) -> str:
    assert document.count(anchor) >= 1, anchor
    return document.replace(anchor, replacement, 1)


SCREEN_SURFACES = {
    "onclick attribute": ("<button ", '<button onclick="alert(1)" ', EMBEDDING, "onclick", "an attribute"),
    "iframe": ("</body>", '<iframe src="frame.html"></iframe>\n</body>', EMBEDDING, "<iframe", "a tag"),
    "link": (
        '<meta charset="utf-8">',
        '<meta charset="utf-8">\n<link rel="stylesheet" href="theme.css">',
        NETWORK,
        "<link",
        "a tag",
    ),
    "http href attribute": (
        "</body>",
        '<a href="http://evil.example/">Docs</a>\n</body>',
        NETWORK,
        "http://evil.example/",
        "an attribute",
    ),
    "worker in a script block": (
        "<script>\n",
        "<script>\nnew Worker('worker.js');\n",
        EMBEDDING,
        "Worker(",
        "a script block",
    ),
}


@pytest.mark.parametrize("surface", sorted(SCREEN_SURFACES))
def test_real_surfaces_injected_into_a_screen_artifact_are_rejected(compiled_starter, tmp_path, surface):
    anchor, replacement, code, matched, where = SCREEN_SURFACES[surface]
    tampered = _inject(_screen_artifact(compiled_starter), anchor, replacement)
    artifact = tmp_path / "index.html"
    artifact.write_text(tampered, encoding="utf-8")

    with pytest.raises(AppBundleProofFailure) as caught:
        _collect_shell_screens([{"id": "queue", "paths": {"artifact": str(artifact)}}])

    assert caught.value.code == code
    assert matched in caught.value.message
    assert f" in {where}: " in caught.value.message


SHELL_SURFACES = {
    "onclick attribute": ('<nav id="vs-app-nav"', '<nav onclick="alert(1)" id="vs-app-nav"', EMBEDDING, "onclick"),
    "iframe": ("</main>", '<iframe src="frame.html"></iframe>\n</main>', EMBEDDING, "<iframe"),
    "http href attribute": (
        "</main>",
        '<a href="http://evil.example/">Docs</a>\n</main>',
        NETWORK,
        "http://evil.example/",
    ),
    "worker in a script block": ("(() => {", "(() => {\n  new Worker('worker.js');", EMBEDDING, "Worker("),
}


@pytest.mark.parametrize("surface", sorted(SHELL_SURFACES))
def test_real_surfaces_injected_into_the_rendered_shell_are_rejected(compiled_starter, surface):
    anchor, replacement, code, matched = SHELL_SURFACES[surface]
    tampered = _inject((compiled_starter / "index.html").read_text(encoding="utf-8"), anchor, replacement)

    with pytest.raises(AppBundleProofFailure) as caught:
        _assert_rendered_shell_static_contract(tampered)

    assert caught.value.code == code
    assert matched in caught.value.message


def test_a_full_url_in_visible_text_is_still_rejected_and_named(compiled_starter):
    tampered = _inject(_screen_artifact(compiled_starter), "High - Investigating", "See https://example.invalid/x")

    with pytest.raises(AppBundleProofFailure) as caught:
        _assert_screen_artifact_shell_safe(tampered, "queue")

    assert caught.value.code == NETWORK
    assert "in text: 'https://example.invalid/x'" in caught.value.message


def test_a_css_reference_is_still_rejected_and_named(compiled_starter):
    tampered = _inject(_screen_artifact(compiled_starter), "</style>", ".vs-x { background: url(x.png); }\n</style>")

    with pytest.raises(AppBundleProofFailure) as caught:
        _assert_screen_artifact_shell_safe(tampered, "queue")

    assert caught.value.code == NETWORK
    assert "in a style block: 'url(x.png)'" in caught.value.message


@pytest.mark.parametrize("text", ORDINARY_TEXT)
def test_ordinary_text_in_a_text_attribute_is_not_read_as_markup(compiled_starter, text):
    label = html.escape(text, quote=True)
    screen = _inject(_screen_artifact(compiled_starter), "<button ", f'<button aria-label="{label}" ')
    shell = _inject(
        (compiled_starter / "index.html").read_text(encoding="utf-8"),
        '<nav id="vs-app-nav"',
        f'<nav title="{label}" id="vs-app-nav"',
    )

    _assert_screen_artifact_shell_safe(screen, "queue")
    _assert_rendered_shell_static_contract(shell)


# --- markup a flat split could misread is still scanned whole ---------------------------------------

# Each hides an inline handler from a split that trusts every "<script>" opener or ends every tag at
# the first ">": a quoted ">", a comment, foreign content, a look-alike raw-text tag, and a raw-text
# opener inside an attribute value. None of these is compiler output, so the scan reads all of it.
DISGUISED_HANDLERS = (
    "<img src=\"x\" alt='>' onerror=alert(1)>",
    '<!--<script>--><img src="x" onerror="alert(1)"><!--</script>-->',
    '<svg><script>x<img src="y" onerror="alert(1)"//</script></svg>',
    '<style-x><img src="x" onerror="alert(1)"></style>',
    '<a title="a><script>x" onclick="alert(1)" </script>',
)


@pytest.mark.parametrize("disguised", DISGUISED_HANDLERS)
def test_handlers_hidden_from_a_flat_split_are_still_rejected(compiled_starter, disguised):
    screen = _inject(_screen_artifact(compiled_starter), "</body>", f"{disguised}\n</body>")
    shell = _inject((compiled_starter / "index.html").read_text(encoding="utf-8"), "</main>", f"{disguised}\n</main>")

    with pytest.raises(AppBundleProofFailure) as screen_caught:
        _assert_screen_artifact_shell_safe(screen, "queue")
    with pytest.raises(AppBundleProofFailure) as shell_caught:
        _assert_rendered_shell_static_contract(shell)

    for caught in (screen_caught, shell_caught):
        assert caught.value.code == EMBEDDING
        assert "in markup the scan cannot split by context" in caught.value.message


def test_markup_outside_the_compiler_form_is_scanned_whole(compiled_starter):
    # A single-quoted attribute is not compiler output, so the text beside it is read as before.
    screen = _inject(_screen_artifact(compiled_starter), "High - Investigating", "<b class='x'>Nodes online = 12</b>")

    with pytest.raises(AppBundleProofFailure) as caught:
        _assert_screen_artifact_shell_safe(screen, "queue")

    assert caught.value.code == EMBEDDING
    assert "online =" in caught.value.message
