"""Projection-distance instrument (queue item B0).

A dev-only gauge over the aesthetic registry: every pair of profiles must render as visibly
distinct (>= PROJECTION_DISTANCE_FLOOR differing axes). The floor is a RATCHET, tight against
the real weakest pair so it catches drift toward sameness before a human sees it.
"""

from __future__ import annotations

from itertools import combinations
from unittest.mock import patch

import pytest

from viewspec.aesthetics import (
    AESTHETIC_PROFILE_LAYOUT_PROPS,
    AESTHETIC_PROFILE_STYLE_VALUES,
    AESTHETIC_PROFILE_TOKENS,
    AestheticProfileError,
    profile_projection_axes,
    profile_projection_distance,
)

# RATCHET: 4 today (weakest real pair premium_saas <-> executive_review == 4).
# B3 raises to 5. Never lower it to make a change pass.
PROJECTION_DISTANCE_FLOOR = 4

_EXPECTED_AXIS_KEYS = {
    "ground",
    "ground_luminance",
    "font_class",
    "metric_columns",
    "radius_bucket",
    "surface_shadow",
    "uppercase_accent",
    "emphasis_weight",
    "accent_hue_bucket",
    "featured_span",
}


def _all_pairwise() -> dict[tuple[str, str], int]:
    return {
        (a, b): profile_projection_distance(a, b)
        for a, b in combinations(AESTHETIC_PROFILE_TOKENS, 2)
    }


def test_every_profile_pair_clears_the_floor():
    for (a, b), dist in _all_pairwise().items():
        if dist < PROJECTION_DISTANCE_FLOOR:
            same = [k for k in profile_projection_axes(a) if profile_projection_axes(a)[k] == profile_projection_axes(b)[k]]
            raise AssertionError(
                f"{a} <-> {b} distance {dist} < floor {PROJECTION_DISTANCE_FLOOR}; shared axes: {same}"
            )


def test_floor_is_tight_not_slack():
    # The floor must track the real weakest pair, so a future change that collapses two
    # profiles toward sameness (while still 'passing' a slack floor) can't slip through.
    assert min(_all_pairwise().values()) == PROJECTION_DISTANCE_FLOOR


def test_gauge_detects_a_collapsed_profile():
    # Make executive_review a byte-for-byte clone of premium_saas (style + layout); the gauge
    # must read their distance as 0 and the floor test must then fail. In-process patch only.
    cloned_style = dict(AESTHETIC_PROFILE_STYLE_VALUES["aesthetic.premium_saas"])
    cloned_layout = {
        role: dict(props) for role, props in AESTHETIC_PROFILE_LAYOUT_PROPS["aesthetic.premium_saas"].items()
    }
    with (
        patch.dict(AESTHETIC_PROFILE_STYLE_VALUES, {"aesthetic.executive_review": cloned_style}),
        patch.dict(AESTHETIC_PROFILE_LAYOUT_PROPS, {"aesthetic.executive_review": cloned_layout}),
    ):
        assert profile_projection_distance("aesthetic.premium_saas", "aesthetic.executive_review") == 0
        assert min(_all_pairwise().values()) < PROJECTION_DISTANCE_FLOOR


def test_distance_is_symmetric():
    a, b = AESTHETIC_PROFILE_TOKENS[0], AESTHETIC_PROFILE_TOKENS[2]
    assert profile_projection_distance(a, b) == profile_projection_distance(b, a)


@pytest.mark.parametrize(
    ("token", "value"),
    [
        ("action.accent", "background-color: #4f46e5; color: #ffffff; border-radius: 50%;"),
        ("tone.accent", "color: rgb(1, 2, 3); font-weight: 860;"),
    ],
)
def test_unmeasurable_value_raises_rather_than_guessing(token, value):
    patched = dict(AESTHETIC_PROFILE_STYLE_VALUES["aesthetic.premium_saas"])
    patched[token] = value
    with patch.dict(AESTHETIC_PROFILE_STYLE_VALUES, {"aesthetic.premium_saas": patched}):
        with pytest.raises(AestheticProfileError):
            profile_projection_axes("aesthetic.premium_saas")


def test_axis_key_set_is_closed():
    # A silently added/removed axis is a loud diff here (C4).
    assert set(profile_projection_axes("aesthetic.calm_ops")) == _EXPECTED_AXIS_KEYS


def test_axis_sanity_pins():
    # Deliberate literals — a registry change that shifts these updates them WITH an explanation.
    data = profile_projection_axes("aesthetic.data_dense")
    assert data["ground"] == "dark"
    assert data["font_class"] == "mono"
    assert data["metric_columns"] == 3
    assert data["radius_bucket"] == "sharp"
    assert profile_projection_axes("aesthetic.premium_saas")["radius_bucket"] == "pill"
    assert profile_projection_axes("aesthetic.premium_saas")["featured_span"] == 2
    assert profile_projection_axes("aesthetic.editorial_product")["font_class"] == "serif"
    assert profile_projection_axes("aesthetic.executive_review")["uppercase_accent"] is True


def test_unknown_profile_raises():
    with pytest.raises(AestheticProfileError):
        profile_projection_axes("aesthetic.brutalist")


def test_print_pairwise_matrix():
    # Passing test; run with `-s` to capture the matrix for PR bodies.
    print(f"\nProjection-distance matrix (floor {PROJECTION_DISTANCE_FLOOR}):")
    for (a, b), dist in sorted(_all_pairwise().items(), key=lambda kv: kv[1]):
        print(f"  {dist}  {a.split('.')[1]:20} <-> {b.split('.')[1]}")
