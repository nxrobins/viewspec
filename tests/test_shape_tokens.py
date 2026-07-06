"""Shape-language tokens (beauty pipeline B2).

The emitter's structural bones (radius / shadow / border / value weight) are now driven by a
closed set of CSS custom properties a profile can set. B2 ships the plumbing with EMPTY maps —
zero visual change — so these tests pin the load-bearing guarantees the byte-identity drift
test cannot (it regenerates both sides from the same emitter).
"""

from __future__ import annotations

import pathlib
import re
import tempfile

import pytest

from viewspec import ViewSpecBuilder, compile
from viewspec.aesthetics import (
    AESTHETIC_PROFILE_SHAPE_VARS,
    AESTHETIC_PROFILE_TOKENS,
    AESTHETIC_SHAPE_VAR_NAMES,
    AestheticProfileError,
    profile_shape_var_css,
    profile_shape_vars,
    validate_aesthetic_profile_registry,
)
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

# SC-1: the 8 vars' fallbacks MUST equal the pre-B2 hardcoded literals. A wrong fallback
# (e.g. 15px) would change the drift golden AND the builder output identically and pass — so
# it is pinned here, independent of regeneration.
EXPECTED_FALLBACKS = (
    "border-radius: var(--vs-radius, 16px)",
    "border-radius: var(--vs-control-radius, 10px)",  # .vs-input
    "border-radius: var(--vs-control-radius, 12px)",  # .vs-image-slot / .vs-svg / table
    "border-radius: var(--vs-badge-radius, 999px)",
    "border-radius: var(--vs-action-radius, 12px)",
    "box-shadow: var(--vs-surface-shadow, 0 1px 2px rgb(15 23 42 / 0.08))",
    "box-shadow: var(--vs-action-shadow, 0 1px 2px rgb(15 23 42 / 0.16))",
    "border: var(--vs-surface-border, 1px solid #e2e8f0)",
    "font-weight: var(--vs-value-weight, 900)",
)


def _emit(profile: str | None = None) -> str:
    builder = ViewSpecBuilder("t")
    table = builder.add_table("x", region="main", group_id="g")
    table.add_row(label="a", value="b")
    if profile:
        builder.set_aesthetic_profile(profile)
    ast = compile(builder.build_bundle())
    out = tempfile.mkdtemp()
    paths = HtmlTailwindEmitter().emit(ast, pathlib.Path(out))
    return pathlib.Path(paths["html"]).read_text(encoding="utf-8")


def _root_style(html: str) -> str:
    match = re.search(r'id="dom-region_root"[^>]*style="([^"]*)"', html)
    return match.group(1) if match else ""


def test_base_css_pins_all_shape_fallbacks_to_the_pre_b2_literals():
    html = _emit()
    for fallback in EXPECTED_FALLBACKS:
        assert fallback in html, f"emitted base CSS missing exact fallback: {fallback}"


def test_empty_shape_map_injects_nothing(monkeypatch):
    # Mechanism guard: a profile with no shape vars emits no --vs-* custom property (the base CSS
    # only var()-references them). B3 populates every real map, so clear one to prove it.
    monkeypatch.setitem(AESTHETIC_PROFILE_SHAPE_VARS, "aesthetic.data_dense", {})
    html = _emit("aesthetic.data_dense")
    assert "--vs-radius:" not in html
    assert "--vs-control-radius:" not in html
    assert "--vs-value-weight:" not in html


def test_patched_shape_vars_are_injected_on_the_root_in_canonical_order(monkeypatch):
    # Plumbing works end-to-end: a non-empty map lands on region_root, shape vars first.
    patched = {"--vs-badge-radius": "0px", "--vs-radius": "2px"}  # deliberately non-canonical order
    monkeypatch.setitem(AESTHETIC_PROFILE_SHAPE_VARS, "aesthetic.data_dense", patched)
    style = _root_style(_emit("aesthetic.data_dense"))
    assert style.startswith("--vs-radius: 2px; --vs-badge-radius: 0px;"), style[:80]


def test_shape_var_css_is_canonical_order_regardless_of_dict_order(monkeypatch):
    # SC-2: emission order follows AESTHETIC_SHAPE_VAR_NAMES, not insertion order.
    scrambled = {"--vs-value-weight": "700", "--vs-radius": "4px", "--vs-action-radius": "6px"}
    monkeypatch.setitem(AESTHETIC_PROFILE_SHAPE_VARS, "aesthetic.calm_ops", scrambled)
    assert profile_shape_var_css("aesthetic.calm_ops") == (
        "--vs-radius: 4px; --vs-action-radius: 6px; --vs-value-weight: 700;"
    )


def test_every_profile_now_drives_the_bones():
    # B3 re-expression: every profile sets a non-empty, valid shape-var map (the emitter bones).
    for token in AESTHETIC_PROFILE_TOKENS:
        assert profile_shape_vars(token), f"{token} has no shape vars — B3 should re-express it"


@pytest.mark.parametrize(
    "bad_map",
    [
        {"--vs-bogus": "4px"},  # unknown var name
        {"--vs-radius": "url(#x)"},  # unsafe value
        {"--vs-surface-shadow": "0 0 4px var(--x)"},  # var() indirection
        {"--vs-radius": "4px <"},  # angle bracket
        {"--vs-radius": "0px " * 200},  # > 512 bytes
    ],
)
def test_shape_var_validator_rejects_unsafe_or_unknown(monkeypatch, bad_map):
    monkeypatch.setitem(AESTHETIC_PROFILE_SHAPE_VARS, "aesthetic.calm_ops", bad_map)
    with pytest.raises(AestheticProfileError):
        validate_aesthetic_profile_registry()


def test_shape_var_names_are_the_closed_eight():
    assert len(AESTHETIC_SHAPE_VAR_NAMES) == 8
    assert AESTHETIC_SHAPE_VAR_NAMES[0] == "--vs-radius"


def test_unknown_profile_raises():
    with pytest.raises(AestheticProfileError):
        profile_shape_vars("aesthetic.nonexistent")
