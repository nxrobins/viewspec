"""Accessibility proof — slice 1: scoped WCAG contrast + accessible-name presence.

Guards the invariant that ViewSpec's own governed output clears WCAG AA (contrast) and that the
name check is non-vacuous (SC-A) and the base-pair table cannot drift from the emitter (SC-B).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from viewspec import AESTHETIC_PROFILE_TOKENS
from viewspec.a11y import (
    BASE_CONTRAST_PAIRS,
    a11y_contrast_report,
    name_report,
    threshold_for,
    wcag_contrast_ratio,
    wcag_relative_luminance,
)
from viewspec.aesthetics import AESTHETIC_PROFILE_STYLE_VALUES
from viewspec.emitters.html_tailwind import OFFLINE_EMITTER_CSS

# --- WCAG primitives -------------------------------------------------------------------------------


def test_contrast_extremes_and_symmetry():
    assert wcag_contrast_ratio("#000000", "#ffffff") == 21.0
    assert wcag_contrast_ratio("#ffffff", "#ffffff") == 1.0
    # order-independent
    assert wcag_contrast_ratio("#0f766e", "#ffffff") == wcag_contrast_ratio("#ffffff", "#0f766e")


def test_relative_luminance_bounds():
    assert wcag_relative_luminance("#000000") == 0.0
    assert wcag_relative_luminance("#ffffff") == pytest.approx(1.0)


def test_bad_hex_raises():
    with pytest.raises(ValueError):
        wcag_contrast_ratio("teal", "#ffffff")


# --- scoped thresholds (unknown -> strictest) ------------------------------------------------------


def test_thresholds_are_scoped():
    assert threshold_for("body") == 4.5
    assert threshold_for("large") == 3.0
    assert threshold_for("ui") == 3.0
    assert threshold_for("mystery") == 4.5  # unknown kind -> body (strictest, fail-closed)


# --- SC-B: the fixed base-pair table cannot drift from the emitter's actual base CSS ---------------


def test_base_pairs_match_emitter_css():
    # The image-slot label uses the AA-clean color; the old sub-AA #64748b must be gone from it.
    assert "background: #e2e8f0; color: #475569;" in OFFLINE_EMITTER_CSS
    assert "background: #e2e8f0; color: #64748b;" not in OFFLINE_EMITTER_CSS
    # Every enumerated base fg/bg literal is present in the base CSS (drift guard).
    for label, fg, bg, _kind in BASE_CONTRAST_PAIRS:
        assert fg in OFFLINE_EMITTER_CSS, f"{label}: fg {fg} absent from emitter base CSS"
        assert bg in OFFLINE_EMITTER_CSS, f"{label}: bg {bg} absent from emitter base CSS"


# --- The shipped guarantee: every profile (and the profile-less default) clears scoped AA ----------


def test_every_profile_clears_scoped_aa():
    assert a11y_contrast_report(None)["contrast_failures"] == 0
    for profile in AESTHETIC_PROFILE_TOKENS:
        report = a11y_contrast_report(profile)
        assert report["contrast_failures"] == 0, f"{profile} fails AA: {report['failures']}"


def test_profile_min_contrast_golden():
    # Deliberate pin: a palette change that dips a governed pair below its scoped threshold changes
    # these numbers and fails here loudly. data_dense's 3.68 is its button (a UI pair, threshold 3.0).
    expected = {
        "aesthetic.calm_ops": 4.55,
        "aesthetic.premium_saas": 4.55,
        "aesthetic.data_dense": 3.68,
        "aesthetic.editorial_product": 4.53,
        "aesthetic.executive_review": 4.55,
        "aesthetic.brutalist": 4.55,
        "aesthetic.neon_cyber": 4.55,
        "aesthetic.warm_organic": 4.55,
    }
    got = {p: a11y_contrast_report(p)["min_contrast_ratio"] for p in AESTHETIC_PROFILE_TOKENS}
    assert got == expected


# --- fail-closed: a sub-threshold governed pair is caught -------------------------------------------


def test_contrast_failure_is_flagged():
    base = dict(AESTHETIC_PROFILE_STYLE_VALUES["aesthetic.calm_ops"])
    base["tone.neutral"] = "color: #cfe0d8; font-family: ui-sans-serif, system-ui, sans-serif;"
    with patch.dict(AESTHETIC_PROFILE_STYLE_VALUES, {"aesthetic.calm_ops": base}):
        report = a11y_contrast_report("aesthetic.calm_ops")
    assert report["contrast_failures"] > 0
    assert any("neutral" in failure["label"] for failure in report["failures"])


# --- SC-A: a name from the emitter's generic fallback ladder counts as UNNAMED ---------------------


def test_name_report_treats_fallback_as_unnamed():
    # Keyed by composition-IR primitive (emitter-agnostic), not the html-only vs-* class.
    nodes = {
        "n1": {"ir_id": "email_input", "primitive": "input", "props": {"aria_label": "Email"}},
        "n2": {"ir_id": "bare_input", "primitive": "input", "props": {"binding_id": "q"}},  # fallback
        "n3": {"ir_id": "save_btn", "primitive": "button", "props": {"text": "Save"}},
        "n4": {"ir_id": "anon_img", "primitive": "image_slot", "props": {}},  # fallback
        "n5": {"ir_id": "plain_surface", "primitive": "surface", "props": {}},  # not a control
    }
    report = name_report(nodes)
    assert report["interactive_controls"] == 4
    assert report["named"] == 2
    assert report["unnamed_interactive"] == 2
    assert report["unnamed"] == ["anon_img", "bare_input"]


def test_name_report_empty_when_no_controls():
    report = name_report({"r": {"ir_id": "root", "primitive": "root", "props": {}}})
    assert report == {"interactive_controls": 0, "named": 0, "unnamed_interactive": 0, "unnamed": []}


# --- label association: a form field's visible label names its input --------------------------------


def _form_bundle():
    from viewspec import ViewSpecBuilder

    builder = ViewSpecBuilder("label_probe", root_attrs={"title": "Label Probe"})
    form = builder.add_form("signup", region="main")
    form.add_field(label="Email address", value="", id="email")
    form.add_field(label="Full name", value="", id="name")
    return builder.build_bundle()


def _walk_inputs(node, inputs):
    if node.primitive == "input":
        inputs[node.id] = dict(node.props)
    for child in node.children:
        _walk_inputs(child, inputs)


def test_form_field_inputs_get_the_label_attr_as_aria_label():
    # Mechanism 1 (attr-level): the compiler resolves the source node's `label` attr as the input's
    # REAL aria_label — form fields are named by their visible label text, no aria_label authoring.
    from viewspec import compile

    ast = compile(_form_bundle())
    inputs: dict[str, dict] = {}
    _walk_inputs(ast.result.root.root, inputs)
    assert inputs["binding_email_value"]["aria_label"] == "Email address"
    assert inputs["binding_name_value"]["aria_label"] == "Full name"
    assert "labelled_by" not in inputs["binding_email_value"]  # attr-level name wins; no structural pass needed


def test_structural_association_covers_label_attr_less_inputs():
    # Mechanism 2 (structural): a node WITHOUT a `label` attr gets no aria_label; when its visible
    # label (bound from another attr) is the unambiguous sibling, the compiler records labelled_by.
    from viewspec import ViewSpecBuilder, compile

    builder = ViewSpecBuilder("raw_pair", root_attrs={"title": "Raw"})
    builder.add_node("q", "query", attrs={"title": "Search term", "value": ""})
    builder.bind_attr("q_title", "q", "title", region="main", present_as="label")
    builder.bind_attr("q_value", "q", "value", region="main", present_as="input")
    ast = compile(builder.build_bundle())
    inputs: dict[str, dict] = {}
    _walk_inputs(ast.result.root.root, inputs)
    assert "aria_label" not in inputs["binding_q_value"]  # no label attr -> no laundered fallback
    assert inputs["binding_q_value"]["labelled_by"] == "binding_q_title"


def test_unlabeled_input_is_genuinely_unnamed():
    # Non-vacuity (SC-A): before this fix the compiler wrote the binding id INTO aria_label, so the
    # name check could never fire for inputs. An input with no label attr and no unambiguous
    # sibling label must now count as unnamed.
    from viewspec import ViewSpecBuilder, compile
    from viewspec.emitters.html_tailwind import _render_node

    builder = ViewSpecBuilder("bare_input", root_attrs={"title": "Bare"})
    builder.add_node("q", "query", attrs={"value": ""})
    builder.bind_attr("q_value", "q", "value", region="main", present_as="input")
    ast = compile(builder.build_bundle())
    manifest: dict = {}
    _render_node(ast.result.root.root, manifest, dict(ast.style_values or {}))
    report = name_report(manifest)
    assert report["interactive_controls"] == 1
    assert report["unnamed_interactive"] == 1


def test_author_aria_label_wins_over_association():
    from viewspec import ViewSpecBuilder, compile

    builder = ViewSpecBuilder("override_probe", root_attrs={"title": "Override"})
    form = builder.add_form("f", region="main")
    form.add_field(label="Email", value="", id="email")
    bundle = builder.build_bundle()
    # Simulate an author aria_label by compiling, then re-running the pass on a fresh compile with
    # the prop injected pre-association is not possible from the public surface; instead assert the
    # rule directly: the pass skips inputs that already carry aria_label.
    from viewspec.compiler import _associate_field_labels

    ast = compile(bundle)

    def find_input(node):
        if node.primitive == "input":
            return node
        for child in node.children:
            found = find_input(child)
            if found is not None:
                return found
        return None

    input_node = find_input(ast.result.root.root)
    input_node.props.pop("labelled_by", None)
    input_node.props["aria_label"] = "Work email"
    _associate_field_labels(ast.result.root.root)
    assert "labelled_by" not in input_node.props  # author name wins; no association recorded


def test_ambiguous_parents_get_no_association():
    # Node without a label attr (so no attr-level name), two inputs sharing one visible label:
    # the structural rule refuses to guess — both stay unnamed.
    from viewspec import ViewSpecBuilder, compile

    builder = ViewSpecBuilder("ambiguous_probe", root_attrs={"title": "Ambiguous"})
    builder.add_node("q", "query", attrs={"title": "Search", "first": "", "second": ""})
    builder.bind_attr("q_title", "q", "title", region="main", present_as="label")
    builder.bind_attr("q_first", "q", "first", region="main", present_as="input")
    builder.bind_attr("q_second", "q", "second", region="main", present_as="input")
    ast = compile(builder.build_bundle())
    inputs: dict[str, dict] = {}
    _walk_inputs(ast.result.root.root, inputs)
    assert all("labelled_by" not in props and "aria_label" not in props for props in inputs.values())


def test_labelled_inputs_render_and_count_as_named(tmp_path):
    import json

    from viewspec import compile
    from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
    from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
    from viewspec.emitters.react_tsx import ReactTsxEmitter

    # Form fields (attr-level name): aria-label carries the visible label text in every emitter.
    ast = compile(_form_bundle())
    HtmlTailwindEmitter().emit(ast, tmp_path / "html")
    html = (tmp_path / "html" / "index.html").read_text(encoding="utf-8")
    assert 'aria-label="Email address"' in html
    assert 'aria-label="email_value"' not in html  # binding-id never masquerades as a name

    manifest = json.loads((tmp_path / "html" / "provenance_manifest.json").read_text(encoding="utf-8"))
    nodes = manifest["nodes"] if isinstance(manifest.get("nodes"), dict) else manifest
    report = name_report(nodes)
    assert report["interactive_controls"] == 2
    assert report["unnamed_interactive"] == 0

    # Structural pair (labelled_by): rendered as aria-labelledby in every emitter, counts as named.
    from viewspec import ViewSpecBuilder

    builder = ViewSpecBuilder("raw_pair", root_attrs={"title": "Raw"})
    builder.add_node("q", "query", attrs={"title": "Search term", "value": ""})
    builder.bind_attr("q_title", "q", "title", region="main", present_as="label")
    builder.bind_attr("q_value", "q", "value", region="main", present_as="input")
    bundle = builder.build_bundle()

    HtmlTailwindEmitter().emit(compile(bundle), tmp_path / "html2")
    html2 = (tmp_path / "html2" / "index.html").read_text(encoding="utf-8")
    assert 'aria-labelledby="dom-binding_q_title"' in html2
    manifest2 = json.loads((tmp_path / "html2" / "provenance_manifest.json").read_text(encoding="utf-8"))
    report2 = name_report(manifest2["nodes"] if isinstance(manifest2.get("nodes"), dict) else manifest2)
    assert report2["unnamed_interactive"] == 0

    ReactTsxEmitter().emit(compile(bundle), tmp_path / "react")
    tsx = (tmp_path / "react" / "ViewSpecView.tsx").read_text(encoding="utf-8")
    assert 'aria-labelledby={"dom-binding_q_title"}' in tsx
    ReactTailwindTsxEmitter().emit(compile(bundle), tmp_path / "tailwind")
    tailwind_tsx = (tmp_path / "tailwind" / "ViewSpecView.tsx").read_text(encoding="utf-8")
    assert 'aria-labelledby={"dom-binding_q_title"}' in tailwind_tsx


# --- prove integration: the proof carries a11y and passes on governed output -----------------------


def test_prove_reports_passing_a11y(tmp_path):
    from viewspec.prove import prove

    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)
    a11y = report["a11y"]
    assert a11y["available"] is True
    assert a11y["contrast_failures"] == 0
    assert a11y["min_contrast_ratio"] >= 4.5
    assert report["checks"]["a11y_contrast"] == "passed"
    assert report["checks"]["a11y_names"] in {"passed", "failed"}  # fail-closed now; starter has 0 controls
    assert report["checks"]["a11y_names"] == "passed"
