"""The canonical-JSON helpers pinned here are thin aliases of one of the two shared forms.

Both an ASCII-escaped and a UTF-8 canonical form are in use. Which module uses which is part of
that module's digest contract (switching a module's form rotates every digest it computes), so
this test pins the mapping. It also checks that every helper rejects NaN and Infinity instead of
writing the non-JSON tokens ``NaN`` and ``Infinity``.
"""

from __future__ import annotations

import math

import pytest

from viewspec import (
    app_pretext,
    app_validation,
    intent_tools,
    local_tools_validators,
    presentation_plan,
    state_ir,
    verification,
)
from viewspec._canonical import (
    canonical_json_ascii,
    canonical_json_ascii_bytes,
    canonical_json_utf8,
    canonical_json_utf8_bytes,
    canonical_sha256,
)

SAMPLE = {"b": "Müller", "a": ["Zoë", 1.5, 10**20, -0.0], "z": {"y": None, "x": True}}
NON_FINITE = [pytest.param(math.nan, id="nan"), pytest.param(math.inf, id="inf"), pytest.param(-math.inf, id="-inf")]

# Plain aliases: the module name is bound to the shared function itself.
ASCII_ALIASES = {
    "verification._canonical_json": (verification._canonical_json, canonical_json_ascii_bytes),
    "presentation_plan._stable_json": (presentation_plan._stable_json, canonical_json_ascii),
    "local_tools_validators._stable_semantic_json": (
        local_tools_validators._stable_semantic_json,
        canonical_json_ascii,
    ),
}
UTF8_ALIASES = {
    "state_ir._stable_json": (state_ir._stable_json, canonical_json_utf8),
    "intent_tools._stable_json": (intent_tools._stable_json, canonical_json_utf8),
    "app_validation._stable_json": (app_validation._stable_json, canonical_json_utf8),
}


@pytest.mark.parametrize("name", sorted(ASCII_ALIASES) + sorted(UTF8_ALIASES))
def test_plain_helpers_are_the_shared_function(name):
    helper, shared = {**ASCII_ALIASES, **UTF8_ALIASES}[name]
    assert helper is shared


def test_the_two_forms_differ_only_in_non_ascii_rendering():
    ascii_text = canonical_json_ascii(SAMPLE)
    utf8_text = canonical_json_utf8(SAMPLE)
    assert ascii_text.isascii()
    assert not utf8_text.isascii()
    assert ascii_text.encode("ascii").decode("unicode_escape") == utf8_text
    assert canonical_json_ascii_bytes(SAMPLE) == ascii_text.encode("utf-8")
    assert canonical_json_utf8_bytes(SAMPLE) == utf8_text.encode("utf-8")


def test_sha256_helpers_hash_their_module_form():
    assert app_pretext._canonical_sha256(SAMPLE) == canonical_sha256(canonical_json_ascii(SAMPLE))
    assert state_ir._hash_json(SAMPLE) == canonical_sha256(canonical_json_utf8(SAMPLE))
    assert canonical_sha256(canonical_json_ascii(SAMPLE)) != canonical_sha256(canonical_json_utf8(SAMPLE))


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize(
    "shared", [canonical_json_ascii, canonical_json_utf8, canonical_json_ascii_bytes, canonical_json_utf8_bytes]
)
def test_shared_forms_reject_non_finite_floats(shared, value):
    with pytest.raises(ValueError):
        shared({"x": value})


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("name", sorted(ASCII_ALIASES) + sorted(UTF8_ALIASES) + ["app_pretext._canonical_sha256"])
def test_plain_helpers_reject_non_finite_floats(name, value):
    helper = {**ASCII_ALIASES, **UTF8_ALIASES, "app_pretext._canonical_sha256": (app_pretext._canonical_sha256, None)}[
        name
    ][0]
    with pytest.raises(ValueError):
        helper({"x": value})
