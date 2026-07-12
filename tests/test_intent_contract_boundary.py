"""Canonical IntentBundle envelope behavior shared with the hosted compiler."""

from __future__ import annotations

import json

from hypothesis import given, strategies as st
import pytest

from viewspec import IntentBundle, starter_intent_payload
from viewspec.intent_contract import (
    INTENT_BUNDLE_SCHEMA_VERSION,
    IntentBundleContractError,
    normalize_intent_bundle_payload,
    validate_intent_bundle_schema_version,
)


JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=20),
)


@given(JSON_SCALARS)
def test_schema_version_acceptance_matches_json_integer_semantics(version: object) -> None:
    accepted = not isinstance(version, bool) and isinstance(version, (int, float)) and version == 1

    if accepted:
        assert validate_intent_bundle_schema_version(version) == INTENT_BUNDLE_SCHEMA_VERSION
    else:
        with pytest.raises(IntentBundleContractError) as raised:
            validate_intent_bundle_schema_version(version)
        assert raised.value.code == "unsupported_schema_version"
        assert raised.value.path == "$.schema_version"


def test_normalization_is_non_mutating_and_removes_contract_metadata() -> None:
    payload = starter_intent_payload("dashboard")
    original = json.loads(json.dumps(payload))

    normalized = normalize_intent_bundle_payload(payload)

    assert payload == original
    assert "schema_version" not in normalized
    assert set(normalized) == {"substrate", "view_spec"}


def test_normalization_accepts_json_text_and_rejects_unknown_root_fields() -> None:
    payload = starter_intent_payload("dashboard")
    assert normalize_intent_bundle_payload(json.dumps(payload))["view_spec"]["id"] == "starter_dashboard"

    with pytest.raises(IntentBundleContractError) as raised:
        normalize_intent_bundle_payload({**payload, "surprise": True})
    assert raised.value.code == "invalid_intent_bundle"
    assert raised.value.path == "$.surprise"


@pytest.mark.parametrize("version", [1, 1.0])
def test_public_parser_uses_the_canonical_envelope(version: int | float) -> None:
    payload = {**starter_intent_payload("dashboard"), "schema_version": version}

    assert IntentBundle.from_json(payload).view_spec.id == "starter_dashboard"
