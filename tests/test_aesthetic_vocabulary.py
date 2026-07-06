"""Aesthetic CSS-property allow-list (beauty pipeline B1).

B1 widens AESTHETIC_ALLOWED_CSS_PROPERTIES by exactly four expressive properties. These tests
guard the allow-list itself (previously untested): the four are accepted with benign values,
still reject the unsafe vectors, the list stays closed, and no profile VALUE changed (B1 is
capability-only; restyling is B3).
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from viewspec.aesthetics import (
    AESTHETIC_ALLOWED_CSS_PROPERTIES,
    AESTHETIC_PROFILE_STYLE_VALUES,
    AESTHETIC_PROFILE_TOKENS,
    AestheticProfileError,
    validate_aesthetic_profile_registry,
)

NEW_PROPERTIES = ("border-style", "font-style", "text-shadow", "backdrop-filter")

# One extra declaration appended to a copy of calm_ops (which has ~28 declarations of 32 head-
# room), so a rejection can only mean the allow-list, never the declaration cap (SC-2).
_TARGET_PROFILE = "aesthetic.calm_ops"
_TARGET_TOKEN = "tone.accent"


def _with_extra_declaration(declaration: str):
    base = dict(AESTHETIC_PROFILE_STYLE_VALUES[_TARGET_PROFILE])
    base[_TARGET_TOKEN] = base[_TARGET_TOKEN].rstrip("; ") + "; " + declaration
    return patch.dict(AESTHETIC_PROFILE_STYLE_VALUES, {_TARGET_PROFILE: base})


def _declaration_props(css: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"([a-z-]+)\s*:", css)}


def test_the_four_new_properties_are_allowed():
    for prop in NEW_PROPERTIES:
        assert prop in AESTHETIC_ALLOWED_CSS_PROPERTIES


@pytest.mark.parametrize(
    "declaration",
    [
        "border-style: dashed",
        "font-style: italic",
        "text-shadow: 0 0 8px rgba(15, 118, 110, 0.4)",
        "backdrop-filter: blur(10px)",
    ],
)
def test_new_property_accepted_with_benign_value(declaration):
    with _with_extra_declaration(declaration):
        validate_aesthetic_profile_registry()  # must not raise


@pytest.mark.parametrize(
    "declaration",
    [
        "backdrop-filter: url(#filter)",  # url( — SVG-filter exfil vector
        "text-shadow: var(--x)",  # var( indirection
        "font-style: expression(1)",  # legacy IE expression
        "border-style: solid < 1",  # angle bracket
    ],
)
def test_new_property_still_rejects_unsafe_value(declaration):
    with _with_extra_declaration(declaration):
        with pytest.raises(AestheticProfileError):
            validate_aesthetic_profile_registry()


@pytest.mark.parametrize("declaration", ["outline: 2px solid red", "transform: rotate(4deg)", "position: absolute"])
def test_allow_list_stays_closed_for_reserved_and_layout_properties(declaration):
    # outline is reserved for the emitter's featured-emphasis marker; transform/position are
    # deliberate future conversations. The list must still reject them.
    with _with_extra_declaration(declaration):
        with pytest.raises(AestheticProfileError):
            validate_aesthetic_profile_registry()


def test_new_property_usage_is_the_pinned_b3_set():
    # B3 re-expression uses exactly these of the four widened properties (editorial italics,
    # data_dense value glow). A usage change beyond this pinned set is a deliberate diff to
    # update here; border-style and backdrop-filter remain unused for now.
    validate_aesthetic_profile_registry()
    usage: dict[str, set[str]] = {}
    for token in AESTHETIC_PROFILE_TOKENS:
        used: set[str] = set()
        for css in AESTHETIC_PROFILE_STYLE_VALUES[token].values():
            used |= _declaration_props(css)
        for prop in used & set(NEW_PROPERTIES):
            usage.setdefault(prop, set()).add(token)
    assert usage == {
        "font-style": {"aesthetic.editorial_product"},
        "text-shadow": {"aesthetic.data_dense"},
    }, usage
