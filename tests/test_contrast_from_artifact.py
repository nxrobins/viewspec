"""C-14: the contrast proof measures the colours the artifact was written with.

`viewspec prove` used to report `a11y_contrast: "passed"` from a table of colour pairs chosen by
profile name. These tests read the compiled artifact instead: each text node's ink against the
background of its nearest ancestor that paints one, from the inline styles and `<style>` rules of
an HTML artifact, or from the Tailwind classes of a React Tailwind artifact.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from viewspec import compile as compile_bundle
from viewspec.aesthetics import AESTHETIC_PROFILE_STYLE_VALUES
from viewspec.cli import main as cli_main
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tailwind_tsx.recipes import DEFAULT_TAILWIND_RECIPE_REGISTRY
from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import compile_intent_bundle_file_tool, init_intent_file, wrap_intent_bundle_manifest
from viewspec.prove import _a11y_report, prove
from viewspec.sdk.builder import ViewSpecBuilder

WHITE_DESIGN = (
    "---\nname: W\ncolors:\n"
    '  primary: "#FFFFFF"\n  secondary: "#FFFFFF"\n  surface: "#FFFFFF"\n'
    '  background: "#FFFFFF"\n  accent: "#FFFFFF"\n---\n'
)


def _hero_and_table(profile: str | None) -> ViewSpecBuilder:
    builder = ViewSpecBuilder("probe", root_attrs={"title": "Probe"})
    builder.add_hero("intro", title="Incident console", description="Live queue health", eyebrow="Operations")
    table = builder.add_table("tbl", region="main", group_id="rows")
    table.add_row(id="r1", label="Owner", value="Ada Lovelace")
    if profile is not None:
        builder.set_aesthetic_profile(profile)
    return builder


def _compile(bundle_path: Path, out_dir: Path, target: str, cwd: Path) -> dict[str, Any]:
    response = compile_intent_bundle_file_tool(bundle_path, out_dir, target=target, cwd=cwd)
    assert response["ok"], response.get("errors")
    return _a11y_report(out_dir / "provenance_manifest.json")


# --- the white DESIGN.md of the audit ---------------------------------------------------------------


def test_white_design_fails_the_contrast_proof(tmp_path):
    intent = init_intent_file(tmp_path / "viewspec.intent.json")
    design = tmp_path / "WHITE.md"
    design.write_text(WHITE_DESIGN, encoding="utf-8")

    report = prove(intent_path=intent, design_path=design, out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["a11y_contrast"] == "failed"
    assert report["a11y"]["contrast_source"] == "artifact"
    assert report["a11y"]["min_contrast_ratio"] == 1.0
    below = [error for error in report["errors"] if error["code"] == "A11Y_CONTRAST_BELOW_AA"]
    assert len(below) == 1
    # The message names the node, both colours and the ratio.
    assert "dom-planner_starter_dashboard_page_title" in below[0]["message"]
    assert "#ffffff on #ffffff" in below[0]["message"]
    assert "1.0:1" in below[0]["message"]


def test_white_design_fails_the_cli_proof(tmp_path, capsys):
    intent = init_intent_file(tmp_path / "viewspec.intent.json")
    design = tmp_path / "WHITE.md"
    design.write_text(WHITE_DESIGN, encoding="utf-8")

    code = cli_main(["prove", str(intent), "--design", str(design), "--out", str(tmp_path / "proof"), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["checks"]["a11y_contrast"] == "failed"
    assert "A11Y_CONTRAST_BELOW_AA" in {error["code"] for error in payload["errors"]}


def test_the_starter_proof_still_passes_from_the_artifact(tmp_path):
    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is True, report["errors"]
    assert report["checks"]["a11y_contrast"] == "passed"
    assert report["a11y"]["contrast_source"] == "artifact"
    assert report["a11y"]["pairs_checked"] >= 9  # page title plus four label/value metric cards
    assert report["a11y"]["contrast_unresolved"] == 0


# --- a deliberately broken profile fails ------------------------------------------------------------


def test_a_broken_profile_fails_on_html(tmp_path):
    bundle = _hero_and_table("aesthetic.calm_ops").export_json(tmp_path / "probe.intent.json")
    broken = dict(AESTHETIC_PROFILE_STYLE_VALUES["aesthetic.calm_ops"])
    broken["tone.neutral"] = "color: #f2f7f5; font-family: ui-sans-serif, system-ui, sans-serif;"
    with patch.dict(AESTHETIC_PROFILE_STYLE_VALUES, {"aesthetic.calm_ops": broken}):
        a11y = _compile(bundle, tmp_path / "out", "html-tailwind", tmp_path)

    assert a11y["contrast_failures"] > 0
    assert any(label.startswith("dom-binding_r1_value: #f2f7f5 on ") for label in a11y["contrast_failure_labels"])


def test_a_broken_overlay_fails_on_react_tailwind(tmp_path):
    overlays = {key: dict(value) for key, value in DEFAULT_TAILWIND_RECIPE_REGISTRY.aesthetic_overlays.items()}
    overlays["aesthetic.calm_ops"]["app_role:app_shell"] = "bg-slate-950 text-slate-900"
    registry = replace(DEFAULT_TAILWIND_RECIPE_REGISTRY, aesthetic_overlays=overlays)
    ast = compile_bundle(_hero_and_table("aesthetic.calm_ops").build_bundle())
    paths = ReactTailwindTsxEmitter(recipe_registry=registry).emit(ast, tmp_path / "out")
    wrap_intent_bundle_manifest(
        Path(paths["manifest"]),
        source_name="probe.intent.json",
        raw_source_hash="0" * 64,
        design=None,
        command_args=["viewspec", "compile", "probe.intent.json", "--target", "react-tailwind-tsx"],
        artifact_path=Path(paths["tsx"]),
        emitter="react_tailwind_tsx",
    )

    a11y = _a11y_report(Path(paths["manifest"]))

    assert a11y["contrast_failures"] > 0
    # The hero title keeps the base recipe's slate-950 ink, now on the overlay's slate-950 ground.
    assert "dom-binding_intro_title: #020618 on #020618 = 1.0:1 (large, needs 3.0:1)" in a11y["contrast_failure_labels"]


# --- a colour the proof cannot resolve fails with A11Y_CONTRAST_UNRESOLVED --------------------------


def test_unresolved_code_is_registered():
    assert "A11Y_CONTRAST_UNRESOLVED" in ERROR_CODES


def test_an_unresolvable_html_colour_fails_the_proof(tmp_path, monkeypatch):
    prove_module = importlib.import_module("viewspec.prove")
    real_compile = prove_module.compile_intent_bundle_file_tool

    def compile_then_patch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        response = real_compile(*args, **kwargs)
        html_path = Path(args[1]) / "index.html"
        html = html_path.read_text(encoding="utf-8")
        title = 'id="dom-planner_starter_dashboard_page_title"'
        assert html.count(title) == 1
        patched = html.replace(title, f'{title} style="color: var(--vs-undefined-ink)"', 1)
        html_path.write_text(patched, encoding="utf-8")
        return response

    monkeypatch.setattr(prove_module, "compile_intent_bundle_file_tool", compile_then_patch)
    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["a11y_contrast"] == "failed"
    unresolved = [error for error in report["errors"] if error["code"] == "A11Y_CONTRAST_UNRESOLVED"]
    assert len(unresolved) == 1
    assert "dom-planner_starter_dashboard_page_title" in unresolved[0]["message"]
    assert "var(--vs-undefined-ink)" in unresolved[0]["message"]


def test_an_unknown_tailwind_colour_fails_the_proof(tmp_path, monkeypatch):
    prove_module = importlib.import_module("viewspec.prove")
    real_compile = prove_module.compile_intent_bundle_file_tool

    def compile_then_patch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        response = real_compile(*args, **kwargs)
        tsx_path = Path(args[1]) / "ViewSpecView.tsx"
        tsx = tsx_path.read_text(encoding="utf-8")
        assert "text-slate-500 vs-label" in tsx
        tsx_path.write_text(tsx.replace("text-slate-500 vs-label", "text-chartreuse-500 vs-label"), encoding="utf-8")
        return response

    def host_passes(artifact_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "artifact_dir": str(artifact_dir), "assertions": {}, "errors": []}

    monkeypatch.setattr(prove_module, "compile_intent_bundle_file_tool", compile_then_patch)
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", host_passes)
    report = prove(out_dir=tmp_path / "proof", target="react-tailwind-tsx", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["a11y_contrast"] == "failed"
    unresolved = [error for error in report["errors"] if error["code"] == "A11Y_CONTRAST_UNRESOLVED"]
    assert len(unresolved) == 1
    assert "dom-binding_revenue_label" in unresolved[0]["message"]
    assert "text-chartreuse-500" in unresolved[0]["message"]


def test_the_palette_resolves_every_colour_the_base_recipes_paint():
    from viewspec.emitters.react_tailwind_tsx.a11y import TAILWIND_PALETTE

    # bg-red-50 is the error-state and error-boundary ground; it used to be missing.
    assert TAILWIND_PALETTE["red-50"] == "#fef2f2"


def test_react_tsx_contrast_is_not_claimed(tmp_path):
    # The react-tsx emitter's inline style objects are outside what the measurement reads, so the
    # proof does not report a pass for them (it used to, from the HTML pair table).
    report = prove(out_dir=tmp_path / "proof", target="react-tsx", cwd=tmp_path)

    assert report["checks"]["a11y_contrast"] == "not_applicable"
    assert report["a11y"]["contrast_checked"] is False


# --- what the HTML measurement reads ----------------------------------------------------------------


def _html(body: str, css: str = "") -> str:
    return f"<!DOCTYPE html><html><head><style>{css}</style></head><body>{body}</body></html>"


def _pairs(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["node"]: entry for entry in report["pairs"]}


def test_html_ink_is_measured_against_the_nearest_painted_ancestor():
    from viewspec.a11y import html_contrast_report

    html = _html(
        '<main style="background-color: #05060a"><table class="t"><tbody><tr>'
        '<td id="cell" style="color: #d8f6ff">Ada</td></tr></tbody></table></main>',
        "table.t { background: #ffffff; }",
    )
    cell = _pairs(html_contrast_report(html))["cell"]
    assert (cell["fg"], cell["bg"], cell["ratio"], cell["kind"]) == ("#d8f6ff", "#ffffff", 1.13, "body")


def test_html_cascade_follows_order_specificity_and_importance():
    from viewspec.a11y import html_contrast_report

    html = _html(
        '<main style="background: #000000; background-color: #ffffff">'
        '<p id="last" style="color: #777777; color: #ffffff">a</p>'
        '<p id="specific" class="dark">b</p>'
        '<p id="important" class="pale" style="color: #000000">c</p></main>',
        "p { color: #ffffff; } .dark { color: #000000; } .pale { color: #ffffff !important; }",
    )
    pairs = _pairs(html_contrast_report(html))
    assert (pairs["last"]["fg"], pairs["last"]["bg"]) == ("#ffffff", "#ffffff")  # the later declaration wins
    assert pairs["specific"]["fg"] == "#000000"  # a class outranks a type selector
    assert pairs["important"]["fg"] == "#ffffff"  # !important outranks the style attribute


def test_html_alpha_backgrounds_composite_over_the_layer_below():
    from viewspec.a11y import html_contrast_report

    html = _html('<div style="background: #000000"><p id="p" style="background: rgb(255 255 255 / 0.5)">x</p></div>')
    pair = _pairs(html_contrast_report(html))["p"]
    assert pair["bg"] == "#808080"
    assert pair["fg"] == "#000000"  # the initial ink


def test_html_large_text_reads_font_size_and_custom_property_weights():
    from viewspec.a11y import html_contrast_report

    css = ".v { font-size: 1.2rem; font-weight: var(--w, 900); color: #767676; }"
    bold = _pairs(html_contrast_report(_html('<p id="p" class="v">x</p>', css)))["p"]
    light = _pairs(html_contrast_report(_html('<main style="--w: 400"><p id="p" class="v">x</p></main>', css)))["p"]
    assert bold["kind"] == "large"  # 19.2px at the fallback weight 900
    assert light["kind"] == "body"  # the same size at 400 is body text
    button = _pairs(html_contrast_report(_html('<button id="b" style="background: #0f766e; color: #fff">Go</button>')))
    assert button["b"]["kind"] == "ui"


def test_html_hidden_nodes_and_empty_runtime_slots_are_measured():
    from viewspec.a11y import html_contrast_report

    html = _html(
        '<p id="hidden" hidden style="color: #ffffff">x</p><p id="slot" data-binding-id="x" style="color: #fff"></p>'
    )
    report = html_contrast_report(html)
    assert {entry["node"] for entry in report["failures"]} == {"hidden", "slot"}


def test_html_interaction_states_are_not_the_default_render():
    from viewspec.a11y import html_contrast_report

    html = _html('<p id="p">x</p>', "p:hover { color: #ffffff; } p:focus-visible { background: #000000; }")
    assert _pairs(html_contrast_report(html))["p"]["ratio"] == 21.0


@pytest.mark.parametrize(
    ("body", "css", "expected"),
    [
        ('<p id="p" style="color: hsl(0 0% 50%)">x</p>', "", ("p", "color", "hsl(0 0% 50%)")),
        ('<p id="p" style="color: var(--missing)">x</p>', "", ("p", "color", "var(--missing)")),
        (
            '<div style="background: linear-gradient(#000000, #ffffff)"><p id="p">x</p></div>',
            "",
            ("p", "background", "linear-gradient(#000000, #ffffff)"),
        ),
        ('<p id="p">x</p>', "@media (min-width: 1px) { p { color: #ffffff; } }", ("p", "conditional rule", "p")),
        ('<p id="p" style="opacity: 0.5">x</p>', "", ("p", "opacity", "0.5")),
        ('<p id="p">x</p>', "p::first-line { color: #ffffff; }", ("style", "selector", "p::first-line")),
    ],
)
def test_html_colours_it_cannot_resolve_are_unresolved(body, css, expected):
    from viewspec.a11y import html_contrast_report

    report = html_contrast_report(_html(body, css))
    assert (expected[0], expected[1], expected[2]) in {
        (entry["node"], entry["property"], entry["value"]) for entry in report["unresolved"]
    }


# --- what the React Tailwind measurement reads ------------------------------------------------------


def _tsx(*lines: str) -> str:
    return "export function ViewSpecView() {\n  return (\n" + "\n".join(lines) + "\n  );\n}\n"


def test_tsx_classes_resolve_through_the_palette_and_inherit():
    from viewspec.emitters.react_tailwind_tsx.a11y import tsx_contrast_report

    tsx = _tsx(
        '<main id={"dom-root"} className="bg-slate-950 text-cyan-50">',
        '<header id={"dom-hero"} className="bg-transparent p-0">',
        '<h1 id={"dom-title"} className="text-3xl font-black text-slate-950">{"Title"}</h1>',
        '<p id={"dom-copy"} className="text-base">{"Copy"}</p>',
        "</header>",
        '<div id={"dom-card"} className="bg-slate-900">',
        '<span id={"dom-badge"} className="bg-fuchsia-500/20 text-fuchsia-200 hover:text-white">{"B"}</span>',
        '<input id={"dom-field"} className="bg-white text-slate-950" type="text" />',
        "</div>",
        "</main>",
    )
    pairs = _pairs(tsx_contrast_report(tsx))
    assert (pairs["dom-title"]["fg"], pairs["dom-title"]["bg"], pairs["dom-title"]["kind"]) == (
        "#020618",
        "#020618",
        "large",
    )
    assert pairs["dom-copy"]["fg"] == "#ecfeff"  # inherited from the shell, on the shell's ground
    assert pairs["dom-badge"]["bg"] == "#391b55"  # 0.2 x fuchsia-500 #e12afb + 0.8 x slate-900 #0f172b
    assert pairs["dom-field"]["bg"] == "#ffffff"


@pytest.mark.parametrize(
    ("classes", "expected"),
    [
        ("text-chartreuse-500", ("color", "text-chartreuse-500")),
        ("text-slate-700 text-slate-950", ("color", "text-slate-700 text-slate-950")),
        ("text-slate-700 sm:text-white", ("color", "sm:text-white")),
        ("bg-linear-to-r text-slate-700", ("background-image", "bg-linear-to-r")),
        ("opacity-50 text-slate-700", ("opacity", "opacity-50")),
    ],
)
def test_tsx_classes_it_cannot_resolve_are_unresolved(classes, expected):
    from viewspec.emitters.react_tailwind_tsx.a11y import tsx_contrast_report

    report = tsx_contrast_report(_tsx(f'<p id={{"dom-p"}} className="{classes}">{{"x"}}</p>'))
    assert [(entry["node"], entry["property"], entry["value"]) for entry in report["unresolved"]] == [
        ("dom-p", *expected)
    ]
