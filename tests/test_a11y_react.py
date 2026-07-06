"""React Tailwind emitter contrast proof (a11y slice 2).

Guards that the React recipes clear scoped WCAG AA on the browser-grounded Tailwind v4 palette, that
Tailwind class parsing is correct (variants ignored, sizes not read as colors), and that a missing
palette entry fails closed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from viewspec import AESTHETIC_PROFILE_TOKENS
from viewspec.emitters.react_tailwind_tsx.a11y import (
    TAILWIND_PALETTE,
    ReactA11yError,
    _last_bg,
    _last_text,
    _palette,
    react_contrast_report,
)
from viewspec.emitters.react_tailwind_tsx.recipes import TAILWIND_AESTHETIC_RECIPE_OVERLAYS


def test_palette_is_browser_grounded_v4_not_v3():
    # Blink-rasterized v4.3 values, NOT the v3 hex: teal-700 renders #00786f (v3 was #0f766e),
    # slate-500 renders #62748e (v3 was #64748b). This is the crux of RC-A.
    assert TAILWIND_PALETTE["teal-700"] == "#00786f"
    assert TAILWIND_PALETTE["slate-500"] == "#62748e"
    assert TAILWIND_PALETTE["white"] == "#ffffff"


def test_every_profile_clears_scoped_aa():
    for profile in AESTHETIC_PROFILE_TOKENS:
        report = react_contrast_report(profile)
        assert report["contrast_failures"] == 0, f"{profile} fails AA: {report['failures']}"


def test_react_min_contrast_golden():
    # Deliberate pin: a recipe/overlay color change that dips a pair below its scoped threshold
    # changes these and fails loudly. data_dense's min is a high-contrast body pair; the tightest
    # profiles (calm/premium/brutalist/warm) are ~4.76-4.79 (labels), safely above 4.5.
    expected = {
        "aesthetic.calm_ops": 4.76,
        "aesthetic.premium_saas": 4.76,
        "aesthetic.data_dense": 6.83,
        "aesthetic.editorial_product": 6.03,
        "aesthetic.executive_review": 7.58,
        "aesthetic.brutalist": 4.77,
        "aesthetic.neon_cyber": 5.69,
        "aesthetic.warm_organic": 4.79,
    }
    got = {p: react_contrast_report(p)["min_contrast_ratio"] for p in AESTHETIC_PROFILE_TOKENS}
    assert got == expected


def test_variant_classes_are_ignored():
    # executive_review button: "bg-slate-950 ... hover:bg-slate-800 ..." — the base (slate-950)
    # applies to the default render, not the hover variant.
    assert _last_bg("rounded-none bg-slate-950 px-4 py-2 hover:bg-slate-800 focus:ring-slate-950") == ("slate-950", 1.0)
    assert _last_text("text-slate-950 hover:text-white") == "slate-950"


def test_size_classes_are_not_read_as_colors():
    assert _last_text("text-2xl font-black leading-none text-neutral-950") == "neutral-950"
    assert _last_text("text-xs font-bold uppercase") is None


def test_alpha_badge_composites_over_surface():
    # neon badge bg-fuchsia-500/20 sits on the slate-900 surface -> an opaque composited bg.
    report = react_contrast_report("aesthetic.neon_cyber")
    badge = next(entry for entry in report["pairs"] if entry["label"] == "badge text")
    assert badge["passes"] is True
    assert badge["bg"] != TAILWIND_PALETTE["fuchsia-500"]  # composited, not the raw 100% fuchsia


def test_missing_palette_color_fails_closed():
    with pytest.raises(ReactA11yError):
        _palette("chartreuse-500")


def test_contrast_failure_is_flagged():
    overlay = dict(TAILWIND_AESTHETIC_RECIPE_OVERLAYS["aesthetic.calm_ops"])
    overlay["primitive:label"] = "text-xs uppercase text-slate-300"  # pale label on white surface
    with patch.dict(TAILWIND_AESTHETIC_RECIPE_OVERLAYS, {"aesthetic.calm_ops": overlay}):
        report = react_contrast_report("aesthetic.calm_ops")
    assert report["contrast_failures"] > 0
    assert any("label" in failure["label"] for failure in report["failures"])
