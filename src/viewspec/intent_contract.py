"""Canonical, dependency-free IntentBundle document envelope contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

INTENT_BUNDLE_SCHEMA_VERSION = 1
INTENT_BUNDLE_ROOT_FIELDS = frozenset({"schema_version", "substrate", "view_spec"})

# Every attribute and slot value is stored as a protobuf Struct number, i.e. an IEEE-754 double.
# Integers whose magnitude exceeds 2**53 are not exactly representable there, so the intent
# contract rejects them instead of silently rounding ids and counters.
MAX_SAFE_INTEGER = 2**53


class IntentBundleContractError(ValueError):
    """Stable wire-contract failure shared by local and hosted compilers."""

    def __init__(self, code: str, message: str, path: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def validate_intent_bundle_schema_version(value: object) -> int:
    """Validate JSON Schema integer semantics for the current bundle version."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != INTENT_BUNDLE_SCHEMA_VERSION:
        raise IntentBundleContractError(
            "unsupported_schema_version",
            f"IntentBundle schema_version must be {INTENT_BUNDLE_SCHEMA_VERSION} when present, got {value!r}",
            "$.schema_version",
        )
    return INTENT_BUNDLE_SCHEMA_VERSION


def normalize_intent_bundle_payload(payload: Any) -> dict[str, Any]:
    """Return protobuf-ready bundle fields without mutating the source document."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IntentBundleContractError("invalid_intent_bundle", "IntentBundle is not valid JSON", "$") from exc
    if not isinstance(payload, Mapping):
        raise IntentBundleContractError("invalid_intent_bundle", "IntentBundle must be a JSON object", "$")
    unknown = sorted(str(key) for key in payload if key not in INTENT_BUNDLE_ROOT_FIELDS)
    if unknown:
        field = unknown[0]
        raise IntentBundleContractError(
            "invalid_intent_bundle",
            f"Unknown IntentBundle root field: {field}",
            f"$.{field}",
        )
    if "schema_version" in payload:
        validate_intent_bundle_schema_version(payload["schema_version"])
    return {str(key): value for key, value in payload.items() if key != "schema_version"}


__all__ = [
    "INTENT_BUNDLE_ROOT_FIELDS",
    "INTENT_BUNDLE_SCHEMA_VERSION",
    "MAX_SAFE_INTEGER",
    "IntentBundleContractError",
    "normalize_intent_bundle_payload",
    "validate_intent_bundle_schema_version",
]
