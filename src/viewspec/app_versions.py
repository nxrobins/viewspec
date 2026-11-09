"""AppBundle schema_version capability predicates.

Each predicate names one capability and the schema versions that carry it, so a later version
inherits every earlier behavior without a version comparison at each call site. A predicate
compares the declared value with Python equality, exactly like the comparisons it replaced, so
``4.0`` still counts as ``4`` for the summaries of an invalid document. An unhashable declared
value never matches.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from typing import Any

APP_SCHEMA_VERSIONS_WITH_INTERACTIVE_STATE = frozenset({3, 4, 5})
APP_SCHEMA_VERSIONS_WITH_VISIBILITY = frozenset({4, 5})
APP_SCHEMA_VERSIONS_WITH_BOUNDED_EXPRESSIONS = frozenset({5})


def _declared_schema_version(payload: Mapping[str, Any]) -> Hashable | None:
    value = payload.get("schema_version") if isinstance(payload, Mapping) else None
    return value if isinstance(value, Hashable) else None


def app_has_interactive_state(payload: Mapping[str, Any]) -> bool:
    """True for schema_version 3 and later: state, mutations, selectors, and the reducer."""
    return _declared_schema_version(payload) in APP_SCHEMA_VERSIONS_WITH_INTERACTIVE_STATE


def app_has_visibility(payload: Mapping[str, Any]) -> bool:
    """True for schema_version 4 and later: visibility rules and optional state text."""
    return _declared_schema_version(payload) in APP_SCHEMA_VERSIONS_WITH_VISIBILITY


def app_has_state_text(payload: Mapping[str, Any]) -> bool:
    """True when a schema_version 4 or later document declares the optional state_text array."""
    return app_has_visibility(payload) and "state_text" in payload


def app_is_v5(payload: Mapping[str, Any]) -> bool:
    """True for schema_version 5: composed visibility, multi-value state text, and formatters."""
    return _declared_schema_version(payload) in APP_SCHEMA_VERSIONS_WITH_BOUNDED_EXPRESSIONS


__all__ = [
    "APP_SCHEMA_VERSIONS_WITH_BOUNDED_EXPRESSIONS",
    "APP_SCHEMA_VERSIONS_WITH_INTERACTIVE_STATE",
    "APP_SCHEMA_VERSIONS_WITH_VISIBILITY",
    "app_has_interactive_state",
    "app_has_state_text",
    "app_has_visibility",
    "app_is_v5",
]
