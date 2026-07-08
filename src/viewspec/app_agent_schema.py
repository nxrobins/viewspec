"""Agent-facing AppBundle JSON schema."""

from __future__ import annotations

from typing import Any

from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_ALLOWED_RESOURCE_KINDS,
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_MAX_ID_CHARS,
    APP_BUNDLE_MAX_RECORD_FIELDS,
    APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
    APP_BUNDLE_MAX_RESOURCES,
    APP_BUNDLE_MAX_ROUTE_CHARS,
    APP_BUNDLE_MAX_ROUTES,
    APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
    APP_BUNDLE_MAX_SCREENS,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS,
    APP_BUNDLE_VISIBILITY_SCHEMA_VERSION,
    APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
    APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
)
from viewspec.state_ir import (
    APP_STATE_MAX_ENTRIES,
    APP_STATE_MAX_EVENTS_PER_REPLAY,
    APP_STATE_MAX_MUTATIONS,
    APP_STATE_MAX_OPS_PER_MUTATION,
    APP_STATE_MAX_REPLAY_ASSERTIONS,
    APP_STATE_MAX_SELECTOR_OPS,
    APP_STATE_MAX_SELECTORS,
    APP_VISIBILITY_MAX_RULES,
    INTERACTIVE_STATE_PROFILE,
)

AGENT_APP_BUNDLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/agent-app-bundle.schema.json",
    "title": "ViewSpec Agent AppBundle V1/V2/V3/V4",
    "description": (
        "Local-only multi-screen app contract with embedded IntentBundles, static routes, V1 unbound "
        "fixtures, V2 read-only fixture binding proof, V3 bounded interactive_state_v0 reducers, and "
        "V4 bounded visibility_v0 conditional visibility rules."
    ),
    "oneOf": [
        {"$ref": "#/$defs/app_bundle_v1"},
        {"$ref": "#/$defs/app_bundle_v2"},
        {"$ref": "#/$defs/app_bundle_v3"},
        {"$ref": "#/$defs/app_bundle_v4"},
    ],
    "x-viewspec-app-schema-versions": list(APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS),
    "x-viewspec-resource-binding": APP_BUNDLE_RESOURCE_BINDING,
    "x-viewspec-resource-bindings": [APP_BUNDLE_RESOURCE_BINDING, APP_BUNDLE_RESOURCE_BINDING_READONLY],
    "x-viewspec-binding-scope": APP_BUNDLE_BINDING_SCOPE,
    "x-viewspec-interactive-state": INTERACTIVE_STATE_PROFILE,
    "x-viewspec-embedded-intent-schema": "https://viewspec.dev/agent-intent-bundle.schema.json",
    "x-viewspec-invariants": [
        "AppBundles are local-only and no-network.",
        "schema_version 1 rejects resource_binding and resource_views, and reports unbound_v0.",
        "schema_version 2 requires resource_binding fixture_readonly_v0 and per-screen resource_views.",
        "schema_version 3 requires fixture_readonly_v0 plus interactive_state_v0 state, mutations, and selectors.",
        "schema_version 4 adds optional bounded visibility rules: per-screen show/hide conditions over declared state and selectors, at most one rule per (screen, target).",
        "Routes are static canonical paths only and must map to declared screens.",
        "The root route must resolve to exactly one route.",
        "Every screen must be reachable by at least one static route.",
        "V2 binding proof is exact byte-for-byte fixture scalar visibility in declared target motifs only.",
        "V3 state mutations are declarative reducer operations triggered by declared embedded screen actions only.",
        "V3 selectors are deterministic read-only derived views over declared state.",
        "Every embedded screen intent must validate against the local V1 IntentBundle contract.",
        "Unknown AppBundle-owned fields are rejected instead of ignored.",
        "Proof output paths are derived from validated safe ids only.",
    ],
    "x-viewspec-anti-goals": [
        "No runtime browser navigation proof.",
        "No dynamic routes, route params, query strings, hashes, redirects, guards, nested routers, or locale routing.",
        "No live data or text rebinding (the V4 shell performs bounded visibility toggling only), framework state adapter, optimistic server reconciliation, persistence, CRDT, websocket sync, or gesture runtime.",
        "No transformed, localized, formatted, joined, sorted, filtered, paginated, grouped, or aggregated fixture proof.",
        "No whole-app data-flow consistency proof beyond explicitly declared resource_views.",
        "No accessibility, pixel-perfect, cross-browser, production deployment, arbitrary host-app, or hosted extended compiler certification.",
        "Visibility V0 is bounded conditional show/hide only: no boolean condition composition, animation, focus management, or data or text rebinding.",
    ],
    "$defs": {
        "app_bundle_v1": {
            "type": "object",
            "required": ["schema_version", "app", "routes", "resources", "screens"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_SCHEMA_VERSION},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v1"},
                },
            },
        },
        "app_bundle_v2": {
            "type": "object",
            "required": ["schema_version", "resource_binding", "app", "routes", "resources", "screens"],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_BOUND_SCHEMA_VERSION},
                "resource_binding": {"const": APP_BUNDLE_RESOURCE_BINDING_READONLY},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v2"},
                },
            },
        },
        "app_bundle_v3": {
            "type": "object",
            "required": [
                "schema_version",
                "resource_binding",
                "interactive_state",
                "app",
                "routes",
                "resources",
                "screens",
                "state",
                "mutations",
                "selectors",
            ],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_STATE_SCHEMA_VERSION},
                "resource_binding": {"const": APP_BUNDLE_RESOURCE_BINDING_READONLY},
                "interactive_state": {"const": INTERACTIVE_STATE_PROFILE},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v2"},
                },
                "state": {"$ref": "#/$defs/state_entries"},
                "mutations": {"$ref": "#/$defs/state_mutations"},
                "selectors": {"$ref": "#/$defs/state_selectors"},
                "state_replay_assertions": {"$ref": "#/$defs/state_replay_assertions"},
            },
        },
        "app_bundle_v4": {
            "type": "object",
            "required": [
                "schema_version",
                "resource_binding",
                "interactive_state",
                "app",
                "routes",
                "resources",
                "screens",
                "state",
                "mutations",
                "selectors",
            ],
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": APP_BUNDLE_VISIBILITY_SCHEMA_VERSION},
                "resource_binding": {"const": APP_BUNDLE_RESOURCE_BINDING_READONLY},
                "interactive_state": {"const": INTERACTIVE_STATE_PROFILE},
                "app": {"$ref": "#/$defs/app"},
                "routes": {"$ref": "#/$defs/routes"},
                "resources": {"$ref": "#/$defs/resources"},
                "screens": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_BUNDLE_MAX_SCREENS,
                    "items": {"$ref": "#/$defs/screen_v2"},
                },
                "state": {"$ref": "#/$defs/state_entries"},
                "mutations": {"$ref": "#/$defs/state_mutations"},
                "selectors": {"$ref": "#/$defs/state_selectors"},
                "visibility": {"$ref": "#/$defs/visibility_rules"},
                "state_replay_assertions": {"$ref": "#/$defs/state_replay_assertions_v4"},
            },
        },
        "safe_id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
        "safe_string": {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
        "json_value": {
            "anyOf": [
                {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
                {
                    "type": "array",
                    "maxItems": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
                    "items": {"$ref": "#/$defs/json_value"},
                },
                {
                    "type": "object",
                    "maxProperties": APP_BUNDLE_MAX_RECORD_FIELDS,
                    "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            ]
        },
        "payload_expr": {
            "anyOf": [
                {"$ref": "#/$defs/json_value"},
                {
                    "type": "object",
                    "required": ["from_payload"],
                    "additionalProperties": False,
                    "properties": {"from_payload": {"$ref": "#/$defs/safe_id"}},
                },
            ]
        },
        "app": {
            "type": "object",
            "required": ["id", "title", "kind", "root_route"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "kind": {"enum": list(APP_BUNDLE_ALLOWED_KINDS)},
                "root_route": {"type": "string", "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS, "pattern": "^/[A-Za-z0-9_.~\\-/]*$"},
            },
        },
        "routes": {
            "type": "array",
            "minItems": 1,
            "maxItems": APP_BUNDLE_MAX_ROUTES,
            "items": {"$ref": "#/$defs/route"},
        },
        "route": {
            "type": "object",
            "required": ["id", "path", "label", "screen_id"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "path": {"type": "string", "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS, "pattern": "^/[A-Za-z0-9_.~\\-/]*$"},
                "label": {"$ref": "#/$defs/safe_string"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "resources": {
            "type": "array",
            "maxItems": APP_BUNDLE_MAX_RESOURCES,
            "items": {"$ref": "#/$defs/resource"},
        },
        "resource": {
            "type": "object",
            "required": ["id", "kind", "records"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "kind": {"enum": list(APP_BUNDLE_ALLOWED_RESOURCE_KINDS)},
                "records": {
                    "type": "array",
                    "maxItems": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
                    "items": {"$ref": "#/$defs/fixture_record"},
                },
            },
        },
        "fixture_record": {
            "type": "object",
            "maxProperties": APP_BUNDLE_MAX_RECORD_FIELDS,
            "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN, "maxLength": APP_BUNDLE_MAX_ID_CHARS},
            "additionalProperties": {
                "anyOf": [
                    {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "null"},
                ]
            },
        },
        "screen_v1": {
            "type": "object",
            "required": ["id", "title", "intent_bundle"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "intent_bundle": {"$ref": "#/$defs/intent_bundle"},
            },
        },
        "screen_v2": {
            "type": "object",
            "required": ["id", "title", "resource_views", "intent_bundle"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "title": {"$ref": "#/$defs/safe_string"},
                "resource_views": {
                    "type": "array",
                    "maxItems": APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
                    "items": {"$ref": "#/$defs/resource_view"},
                },
                "intent_bundle": {"$ref": "#/$defs/intent_bundle"},
            },
        },
        "resource_view": {
            "type": "object",
            "required": ["id", "resource_id", "mode", "record_ids", "fields", "target_motif_id"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "resource_id": {"$ref": "#/$defs/safe_id"},
                "mode": {"const": "list"},
                "record_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
                    "items": {"$ref": "#/$defs/safe_id"},
                },
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
                    "items": {"$ref": "#/$defs/safe_id"},
                },
                "target_motif_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_entries": {
            "type": "array",
            "maxItems": APP_STATE_MAX_ENTRIES,
            "items": {"$ref": "#/$defs/state_entry"},
        },
        "state_entry": {
            "type": "object",
            "required": ["id", "kind", "scope", "initial"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "kind": {"enum": ["collection", "record", "scalar", "selection"]},
                "scope": {"enum": ["app", "screen"]},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "initial": {"$ref": "#/$defs/state_initial"},
            },
        },
        "state_initial": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["value"],
                    "additionalProperties": False,
                    "properties": {"value": {"$ref": "#/$defs/json_value"}},
                },
                {
                    "type": "object",
                    "required": ["from_resource_view"],
                    "additionalProperties": False,
                    "properties": {"from_resource_view": {"$ref": "#/$defs/resource_view_ref"}},
                },
            ]
        },
        "resource_view_ref": {
            "type": "object",
            "required": ["screen_id", "view_id"],
            "additionalProperties": False,
            "properties": {
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "view_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_mutations": {
            "type": "array",
            "maxItems": APP_STATE_MAX_MUTATIONS,
            "items": {"$ref": "#/$defs/state_mutation"},
        },
        "state_mutation": {
            "type": "object",
            "required": ["id", "trigger", "ops"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "trigger": {"$ref": "#/$defs/state_mutation_trigger"},
                "ops": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_OPS_PER_MUTATION,
                    "items": {"$ref": "#/$defs/state_mutation_op"},
                },
            },
        },
        "state_mutation_trigger": {
            "type": "object",
            "required": ["screen_id", "action_id"],
            "additionalProperties": False,
            "properties": {
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "action_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_mutation_op": {
            "oneOf": [
                {"$ref": "#/$defs/state_op_set"},
                {"$ref": "#/$defs/state_op_patch"},
                {"$ref": "#/$defs/state_op_toggle"},
                {"$ref": "#/$defs/state_op_append"},
                {"$ref": "#/$defs/state_op_remove"},
                {"$ref": "#/$defs/state_op_move"},
                {"$ref": "#/$defs/state_op_increment"},
            ]
        },
        "state_op_set": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "set"},
                "state": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_patch": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "patch"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_toggle": {
            "type": "object",
            "required": ["op", "state"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "toggle"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "field": {"$ref": "#/$defs/safe_id"},
            },
        },
        "state_op_append": {
            "type": "object",
            "required": ["op", "state", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "append"},
                "state": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_remove": {
            "type": "object",
            "required": ["op", "state", "item_id"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "remove"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_move": {
            "type": "object",
            "required": ["op", "state", "item_id", "to_index"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "move"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "to_index": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_op_increment": {
            "type": "object",
            "required": ["op", "state"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "increment"},
                "state": {"$ref": "#/$defs/safe_id"},
                "item_id": {"$ref": "#/$defs/payload_expr"},
                "field": {"$ref": "#/$defs/safe_id"},
                "amount": {"$ref": "#/$defs/payload_expr"},
            },
        },
        "state_selectors": {
            "type": "array",
            "maxItems": APP_STATE_MAX_SELECTORS,
            "items": {"$ref": "#/$defs/state_selector"},
        },
        "state_selector": {
            "type": "object",
            "required": ["id", "source_state", "ops"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "source_state": {"$ref": "#/$defs/safe_id"},
                "ops": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_SELECTOR_OPS,
                    "items": {"$ref": "#/$defs/state_selector_op"},
                },
            },
        },
        "state_selector_op": {
            "oneOf": [
                {"$ref": "#/$defs/selector_op_filter_eq"},
                {"$ref": "#/$defs/selector_op_sort_by"},
                {"$ref": "#/$defs/selector_op_slice"},
            ]
        },
        "selector_op_filter_eq": {
            "type": "object",
            "required": ["op", "field", "value"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "filter_eq"},
                "field": {"$ref": "#/$defs/safe_id"},
                "value": {"$ref": "#/$defs/json_value"},
            },
        },
        "selector_op_sort_by": {
            "type": "object",
            "required": ["op", "field"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "sort_by"},
                "field": {"$ref": "#/$defs/safe_id"},
                "direction": {"enum": ["asc", "desc"]},
            },
        },
        "selector_op_slice": {
            "type": "object",
            "required": ["op"],
            "additionalProperties": False,
            "properties": {
                "op": {"const": "slice"},
                "start": {"type": "integer", "minimum": 0},
                "end": {"type": "integer", "minimum": 0},
            },
        },
        "state_replay_assertions": {
            "type": "array",
            "maxItems": APP_STATE_MAX_REPLAY_ASSERTIONS,
            "items": {"$ref": "#/$defs/state_replay_assertion"},
        },
        "state_replay_assertion": {
            "type": "object",
            "required": ["id", "events", "expect_state", "expect_selectors"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "events": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_EVENTS_PER_REPLAY,
                    "items": {"$ref": "#/$defs/state_replay_event"},
                },
                "expect_state": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
                "expect_selectors": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            },
        },
        "state_replay_event": {
            "type": "object",
            "required": ["mutation_id"],
            "additionalProperties": False,
            "properties": {
                "mutation_id": {"$ref": "#/$defs/safe_id"},
                "payload_values": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            },
        },
        "json_scalar": {
            "anyOf": [
                {"type": "string", "maxLength": APP_BUNDLE_MAX_SCALAR_STRING_CHARS},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
            ]
        },
        "visibility_rules": {
            "type": "array",
            "maxItems": APP_VISIBILITY_MAX_RULES,
            "items": {"$ref": "#/$defs/visibility_rule"},
        },
        "visibility_rule": {
            "type": "object",
            "required": ["id", "screen_id", "target_ref", "when"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ID_CHARS + 8,
                    "pattern": "^(region|binding|motif):[A-Za-z0-9_.-]+$",
                },
                "when": {"$ref": "#/$defs/visibility_condition"},
            },
        },
        "visibility_condition": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["state", "is"],
                    "additionalProperties": False,
                    "properties": {
                        "state": {"$ref": "#/$defs/safe_id"},
                        "is": {"enum": ["truthy", "falsy"]},
                    },
                },
                {
                    "type": "object",
                    "required": ["state", "equals"],
                    "additionalProperties": False,
                    "properties": {
                        "state": {"$ref": "#/$defs/safe_id"},
                        "equals": {"$ref": "#/$defs/json_scalar"},
                    },
                },
                {
                    "type": "object",
                    "required": ["selector", "is"],
                    "additionalProperties": False,
                    "properties": {
                        "selector": {"$ref": "#/$defs/safe_id"},
                        "is": {"enum": ["non_empty", "empty"]},
                    },
                },
            ]
        },
        "state_replay_assertions_v4": {
            "type": "array",
            "maxItems": APP_STATE_MAX_REPLAY_ASSERTIONS,
            "items": {"$ref": "#/$defs/state_replay_assertion_v4"},
        },
        "state_replay_assertion_v4": {
            "type": "object",
            "required": ["id", "events", "expect_state", "expect_selectors"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "events": {
                    "type": "array",
                    "maxItems": APP_STATE_MAX_EVENTS_PER_REPLAY,
                    "items": {"$ref": "#/$defs/state_replay_event"},
                },
                "expect_state": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
                "expect_selectors": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
                "expect_visibility": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"type": "boolean"},
                },
            },
        },
        "intent_bundle": {
            "type": "object",
            "description": "Embedded local V1 IntentBundle. validate-app/prove-app enforce the full local V1 validator.",
        },
    },
}


__all__ = ["AGENT_APP_BUNDLE_SCHEMA"]
