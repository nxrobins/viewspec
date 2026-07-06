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
    nodes = {
        "n1": {"ir_id": "email_input", "classes": ["vs-input"], "props": {"aria_label": "Email"}},
        "n2": {"ir_id": "bare_input", "classes": ["vs-input"], "props": {"binding_id": "q"}},
        "n3": {"ir_id": "save_btn", "classes": ["vs-button"], "props": {"text": "Save"}},
        "n4": {"ir_id": "anon_img", "classes": ["vs-image-slot"], "props": {}},
        "n5": {"ir_id": "plain_surface", "classes": ["vs-surface"], "props": {}},  # not a control
    }
    report = name_report(nodes)
    assert report["interactive_controls"] == 4
    assert report["named"] == 2
    assert report["unnamed_interactive"] == 2
    assert report["unnamed"] == ["anon_img", "bare_input"]


def test_name_report_empty_when_no_controls():
    report = name_report({"r": {"ir_id": "root", "classes": ["vs-root"], "props": {}}})
    assert report == {"interactive_controls": 0, "named": 0, "unnamed_interactive": 0, "unnamed": []}


# --- prove integration: the proof carries a11y and passes on governed output -----------------------


def test_prove_reports_passing_a11y(tmp_path):
    from viewspec.prove import prove

    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)
    a11y = report["a11y"]
    assert a11y["available"] is True
    assert a11y["contrast_failures"] == 0
    assert a11y["min_contrast_ratio"] >= 4.5
    assert report["checks"]["a11y_contrast"] == "passed"
    assert report["checks"]["a11y_names"] in {"passed", "warn"}
