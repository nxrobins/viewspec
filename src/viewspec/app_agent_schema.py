"""Agent-facing AppBundle JSON schema."""

from __future__ import annotations

from typing import Any

from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.app_format import STATE_TEXT_CURRENCIES, STATE_TEXT_FORMAT_MAX_DIGITS
from viewspec.app_validation import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_ALLOWED_RESOURCE_KINDS,
    APP_BUNDLE_BINDING_SCOPE,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_EXPRESSIONS_SCHEMA_VERSION,
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
from viewspec.presentation_plan import (
    ALIGN_VALUES,
    BACKGROUND_VALUES,
    BORDER_VALUES,
    DIRECTION_VALUES,
    DISPLAY_VALUES,
    FONT_FAMILY_VALUES,
    FONT_SIZE_VALUES,
    FONT_WEIGHT_VALUES,
    FOREGROUND_VALUES,
    JUSTIFY_VALUES,
    LETTER_SPACING_VALUES,
    LINE_HEIGHT_VALUES,
    MIN_INLINE_SIZE_VALUES,
    PRESENTATION_BREAKPOINTS,
    PRESENTATION_MAX_ANCHORS,
    PRESENTATION_MAX_GRID_ROWS,
    PRESENTATION_MAX_GRID_TRACKS,
    PRESENTATION_MAX_RULES,
    PRESENTATION_PROFILES,
    PRESENTATION_RELATIONS,
    RADIUS_VALUES,
    SIZE_VALUES,
    SPACING_VALUES,
    TEXT_TRANSFORM_VALUES,
    TEXT_WRAP_VALUES,
    TRACK_VALUES,
    VISIBILITY_VALUES,
)
from viewspec.state_ir import (
    APP_STATE_MAX_ENTRIES,
    APP_STATE_MAX_EVENTS_PER_REPLAY,
    APP_STATE_MAX_MUTATIONS,
    APP_STATE_MAX_OPS_PER_MUTATION,
    APP_STATE_MAX_REPLAY_ASSERTIONS,
    APP_STATE_MAX_SELECTOR_OPS,
    APP_STATE_MAX_SELECTORS,
    APP_STATE_TEXT_MAX_PLACEHOLDERS,
    APP_STATE_TEXT_MAX_RULES,
    APP_STATE_TEXT_MAX_TEMPLATE_CHARS,
    APP_STATE_TEXT_V5_MAX_TEMPLATE_CHARS,
    APP_VISIBILITY_MAX_COMPOSITE_ITEMS,
    APP_VISIBILITY_MAX_DEPTH,
    APP_VISIBILITY_MAX_LEAVES,
    APP_VISIBILITY_MAX_RULES,
    APP_VISIBILITY_MIN_COMPOSITE_ITEMS,
    INTERACTIVE_STATE_PROFILE,
)

# The schema_version 5 guidance a later agent-prompt build splices into the AppBundle section.
# tests/test_app_bundle_v5.py keeps it within 1200 bytes and validates both examples.
APPBUNDLE_V5_PROMPT_SECTION = """Use AppBundle schema_version 5 only for composed visibility, multi-value text, or formatted numbers and dates. Version 5 accepts every version 4 bundle. A when may also be all or any of 2 to 8 conditions, or not of one condition. Nest at most 3 levels. Use at most 8 leaf conditions per rule:
{"id": "escalation", "screen_id": "queue", "target_ref": "binding:escalate", "when": {"all": [{"selector": "active", "is": "non_empty"}, {"not": {"state": "acked", "is": "truthy"}}]}}
A multi-value state_text template names each {placeholder} once. Its values map each placeholder to {"state": id} or {"selector": id, "measure": "count"}. A value may add one format: integer, decimal or percent (digits 0 to 6), currency (USD, EUR, GBP, JPY, CHF, INR), or date_iso:
{"id": "open_summary", "screen_id": "queue", "target_ref": "binding:open_text", "template": "{open} open of {total}, {spend} spent", "values": {"open": {"selector": "active", "measure": "count"}, "total": {"selector": "all_incidents", "measure": "count"}, "spend": {"state": "spend", "format": {"format": "currency", "currency": "USD"}}}}
Formats use no locale or time zone. Version 5 adds no free expressions, paths, or host functions.
"""


def _composed_condition_forms(child_ref: str) -> list[dict[str, Any]]:
    """A leaf condition or one all/any/not node whose children use ``child_ref``."""
    child = {"$ref": f"#/$defs/{child_ref}"}
    child_list = {
        "type": "array",
        "minItems": APP_VISIBILITY_MIN_COMPOSITE_ITEMS,
        "maxItems": APP_VISIBILITY_MAX_COMPOSITE_ITEMS,
        "items": child,
    }
    return [
        {"$ref": "#/$defs/visibility_condition"},
        {"type": "object", "required": ["all"], "additionalProperties": False, "properties": {"all": child_list}},
        {"type": "object", "required": ["any"], "additionalProperties": False, "properties": {"any": child_list}},
        {"type": "object", "required": ["not"], "additionalProperties": False, "properties": {"not": child}},
    ]


AGENT_APP_BUNDLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/agent-app-bundle.schema.json",
    "title": "ViewSpec Agent AppBundle V1/V2/V3/V4/V5",
    "description": (
        "Local-only multi-screen app contract with embedded IntentBundles, static routes, V1 unbound "
        "fixtures, V2 read-only fixture binding proof, V3 bounded interactive_state_v0 reducers, "
        "V4 bounded visibility_v0 conditional visibility rules, and V5 bounded expressions: composed "
        "visibility conditions, multi-value state text, and closed formatters."
    ),
    "oneOf": [
        {"$ref": "#/$defs/app_bundle_v1"},
        {"$ref": "#/$defs/app_bundle_v2"},
        {"$ref": "#/$defs/app_bundle_v3"},
        {"$ref": "#/$defs/app_bundle_v4"},
        {"$ref": "#/$defs/app_bundle_v5"},
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
        "schema_version 4 optionally projects one scalar state value into one exactly-once text binding through a literal {value} template.",
        "schema_version 5 accepts every schema_version 4 construct, composes visibility conditions with all, any, and not (lists of 2 to 8, depth at most 3, at most 8 leaves per rule), and fills up to 8 named state_text placeholders from scalar state or selector counts through closed formatters.",
        "A schema_version 1 to 4 bundle that uses a schema_version 5 construct fails with APP_V5_FEATURE_REQUIRES_SCHEMA_VERSION_5.",
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
        "No live data rebinding, framework state adapter, optimistic server reconciliation, persistence, CRDT, websocket sync, or gesture runtime.",
        "No transformed, localized, formatted, joined, sorted, filtered, paginated, grouped, or aggregated fixture proof.",
        "No whole-app data-flow consistency proof beyond explicitly declared resource_views.",
        "No accessibility, pixel-perfect, cross-browser, production deployment, or arbitrary host-app certification.",
        "Visibility V0 (schema_version 4) is bounded conditional show/hide only: no boolean condition composition, animation, or focus management.",
        "State Text V0 (schema_version 4) is one scalar-to-text projection only: no HTML, expressions, paths, localization, formatting functions, or multiple placeholders.",
        "Bounded expressions (schema_version 5) add no free expressions, arbitrary JSON paths, host functions, HTML, Intl, locale, or time zone behavior, and no animation or focus management.",
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
                "state_text": {"$ref": "#/$defs/state_text_rules"},
                "state_replay_assertions": {"$ref": "#/$defs/state_replay_assertions_v4"},
            },
        },
        "app_bundle_v5": {
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
                "schema_version": {"const": APP_BUNDLE_EXPRESSIONS_SCHEMA_VERSION},
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
                "visibility": {"$ref": "#/$defs/visibility_rules_v5"},
                "state_text": {"$ref": "#/$defs/state_text_rules_v5"},
                "state_replay_assertions": {"$ref": "#/$defs/state_replay_assertions_v5"},
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
                "root_route": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS,
                    "pattern": "^/[A-Za-z0-9_.~\\-/]*$",
                },
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
                "path": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ROUTE_CHARS,
                    "pattern": "^/[A-Za-z0-9_.~\\-/]*$",
                },
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
                "presentation": {"$ref": "#/$defs/presentation"},
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
                "presentation": {"$ref": "#/$defs/presentation"},
                "intent_bundle": {"$ref": "#/$defs/intent_bundle"},
            },
        },
        "presentation": {
            "type": "object",
            "description": "Shared static/React layout contract. operations_workspace keeps sibling sidebar/main regions visible, stacks them at compact/medium, and uses a wide rail/content row.",
            "additionalProperties": False,
            "properties": {
                "profile": {"enum": list(PRESENTATION_PROFILES)},
                "rules": {
                    "type": "array",
                    "maxItems": PRESENTATION_MAX_RULES,
                    "items": {"$ref": "#/$defs/presentation_rule"},
                },
                "anchors": {
                    "type": "array",
                    "maxItems": PRESENTATION_MAX_ANCHORS,
                    "items": {"$ref": "#/$defs/presentation_anchor"},
                },
            },
        },
        "presentation_rule": {
            "type": "object",
            "required": ["id", "target_ref"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {"$ref": "#/$defs/presentation_target_ref"},
                "base": {"$ref": "#/$defs/presentation_layout"},
                "items": {"$ref": "#/$defs/presentation_item_layout"},
                "variants": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        breakpoint: {"$ref": "#/$defs/presentation_layout"} for breakpoint in PRESENTATION_BREAKPOINTS
                    },
                },
            },
        },
        "presentation_item_layout": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "base": {"$ref": "#/$defs/presentation_layout"},
                "variants": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        breakpoint: {"$ref": "#/$defs/presentation_layout"} for breakpoint in PRESENTATION_BREAKPOINTS
                    },
                },
            },
        },
        "presentation_anchor": {
            "type": "object",
            "description": "Responsive semantic geometry assertion. Scope viewports to widths where the relation is true; a rail/content same_row relation is normally wide-only when compact and medium stack.",
            "required": ["id", "target_ref", "relation", "anchor_ref"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {"$ref": "#/$defs/presentation_target_ref"},
                "relation": {"enum": list(PRESENTATION_RELATIONS)},
                "anchor_ref": {"$ref": "#/$defs/presentation_target_ref"},
                "viewports": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": len(PRESENTATION_BREAKPOINTS),
                    "uniqueItems": True,
                    "items": {"enum": list(PRESENTATION_BREAKPOINTS)},
                },
            },
        },
        "presentation_target_ref": {
            "type": "string",
            "pattern": "^(region|motif|binding):[A-Za-z0-9_.-]+$",
        },
        "presentation_layout": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "display": {"enum": list(DISPLAY_VALUES)},
                "area": {"$ref": "#/$defs/safe_id"},
                "font_family": {"enum": list(FONT_FAMILY_VALUES)},
                "font_size": {"enum": list(FONT_SIZE_VALUES)},
                "font_weight": {"enum": list(FONT_WEIGHT_VALUES)},
                "line_height": {"enum": list(LINE_HEIGHT_VALUES)},
                "letter_spacing": {"enum": list(LETTER_SPACING_VALUES)},
                "text_transform": {"enum": list(TEXT_TRANSFORM_VALUES)},
                "foreground": {"enum": list(FOREGROUND_VALUES)},
                "background": {"enum": list(BACKGROUND_VALUES)},
                "border": {"enum": list(BORDER_VALUES)},
                "radius": {"enum": list(RADIUS_VALUES)},
                "direction": {"enum": list(DIRECTION_VALUES)},
                "columns": {
                    "oneOf": [
                        {"type": "integer", "minimum": 1, "maximum": PRESENTATION_MAX_GRID_TRACKS},
                        {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": PRESENTATION_MAX_GRID_TRACKS,
                            "items": {"enum": list(TRACK_VALUES)},
                        },
                    ]
                },
                "areas": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": PRESENTATION_MAX_GRID_ROWS,
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": PRESENTATION_MAX_GRID_TRACKS,
                        "items": {"$ref": "#/$defs/safe_id"},
                    },
                },
                "gap": {"enum": list(SPACING_VALUES)},
                "padding": {"enum": list(SPACING_VALUES)},
                "width": {"enum": list(SIZE_VALUES)},
                "max_width": {"enum": list(SIZE_VALUES)},
                "order": {"type": "integer", "minimum": -16, "maximum": 16},
                "span": {"type": "integer", "minimum": 1, "maximum": 4},
                "visibility": {"enum": list(VISIBILITY_VALUES)},
                "align": {"enum": list(ALIGN_VALUES)},
                "justify": {"enum": list(JUSTIFY_VALUES)},
                "sticky": {"type": "boolean"},
                "text_wrap": {"enum": list(TEXT_WRAP_VALUES)},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 8},
                "min_inline_size": {"enum": list(MIN_INLINE_SIZE_VALUES)},
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
                "repeat": {"$ref": "#/$defs/resource_repeat"},
            },
        },
        "resource_repeat": {
            "type": "object",
            "description": "Generates every declared record-field binding inside the target motif. Do not also keep hand-authored prototype bindings for repeated record-fields in that motif.",
            "required": ["field_presentations"],
            "additionalProperties": False,
            "properties": {
                "field_presentations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
                    "items": {"$ref": "#/$defs/resource_repeat_field_presentation"},
                },
                "group_id": {"$ref": "#/$defs/safe_id"},
            },
        },
        "resource_repeat_field_presentation": {
            "type": "object",
            "required": ["field", "present_as"],
            "additionalProperties": False,
            "properties": {
                "field": {"$ref": "#/$defs/safe_id"},
                "present_as": {"enum": ["badge", "label", "text", "value"]},
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
            "additionalProperties": False,
            "properties": {
                "mutation_id": {"$ref": "#/$defs/safe_id"},
                "action_id": {"$ref": "#/$defs/safe_id"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "repeat": {"type": "integer", "minimum": 1, "maximum": APP_STATE_MAX_EVENTS_PER_REPLAY},
                "payload_values": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {"$ref": "#/$defs/json_value"},
                },
            },
            "oneOf": [
                {"required": ["mutation_id"], "not": {"required": ["action_id"]}},
                {"required": ["action_id"], "not": {"required": ["mutation_id"]}},
            ],
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
        "state_text_rules": {
            "type": "array",
            "maxItems": APP_STATE_TEXT_MAX_RULES,
            "items": {"$ref": "#/$defs/state_text_rule"},
        },
        "state_text_rule": {
            "type": "object",
            "required": ["id", "screen_id", "target_ref", "state", "template"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ID_CHARS + 8,
                    "pattern": "^binding:[A-Za-z0-9_.-]+$",
                },
                "state": {"$ref": "#/$defs/safe_id"},
                "template": {
                    "type": "string",
                    "maxLength": APP_STATE_TEXT_MAX_TEMPLATE_CHARS,
                    "pattern": "^[^{}]*\\{value\\}[^{}]*$",
                },
            },
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
                "expect_text": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {
                        "type": "string",
                        "maxLength": APP_STATE_TEXT_MAX_TEMPLATE_CHARS + APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
                    },
                },
            },
        },
        "visibility_rules_v5": {
            "type": "array",
            "maxItems": APP_VISIBILITY_MAX_RULES,
            "items": {"$ref": "#/$defs/visibility_rule_v5"},
        },
        "visibility_rule_v5": {
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
                "when": {"$ref": "#/$defs/visibility_condition_v5"},
            },
        },
        "visibility_condition_v5": {
            "description": (
                f"A leaf condition, all or any of {APP_VISIBILITY_MIN_COMPOSITE_ITEMS} to "
                f"{APP_VISIBILITY_MAX_COMPOSITE_ITEMS} conditions, or not of one condition. Nesting stops at depth "
                f"{APP_VISIBILITY_MAX_DEPTH}, and a rule holds at most {APP_VISIBILITY_MAX_LEAVES} leaves."
            ),
            "oneOf": _composed_condition_forms("visibility_condition_v5_level_2"),
        },
        "visibility_condition_v5_level_2": {
            "oneOf": _composed_condition_forms("visibility_condition"),
        },
        "state_text_rules_v5": {
            "type": "array",
            "maxItems": APP_STATE_TEXT_MAX_RULES,
            "items": {"$ref": "#/$defs/state_text_rule_v5"},
        },
        "state_text_rule_v5": {
            "oneOf": [
                {"$ref": "#/$defs/state_text_rule_v5_single"},
                {"$ref": "#/$defs/state_text_rule_v5_values"},
            ]
        },
        "state_text_rule_v5_single": {
            "type": "object",
            "required": ["id", "screen_id", "target_ref", "state", "template"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ID_CHARS + 8,
                    "pattern": "^binding:[A-Za-z0-9_.-]+$",
                },
                "state": {"$ref": "#/$defs/safe_id"},
                "template": {
                    "type": "string",
                    "maxLength": APP_STATE_TEXT_MAX_TEMPLATE_CHARS,
                    "pattern": "^[^{}]*\\{value\\}[^{}]*$",
                },
                "format": {"$ref": "#/$defs/state_text_format"},
            },
        },
        "state_text_rule_v5_values": {
            "type": "object",
            "description": "Each {name} placeholder appears once in template and has exactly one values entry.",
            "required": ["id", "screen_id", "target_ref", "template", "values"],
            "additionalProperties": False,
            "properties": {
                "id": {"$ref": "#/$defs/safe_id"},
                "screen_id": {"$ref": "#/$defs/safe_id"},
                "target_ref": {
                    "type": "string",
                    "maxLength": APP_BUNDLE_MAX_ID_CHARS + 8,
                    "pattern": "^binding:[A-Za-z0-9_.-]+$",
                },
                "template": {
                    "type": "string",
                    "maxLength": APP_STATE_TEXT_V5_MAX_TEMPLATE_CHARS,
                    "pattern": "^[^{}]*(\\{[a-z][a-z0-9_]{0,31}\\}[^{}]*)+$",
                },
                "values": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": APP_STATE_TEXT_MAX_PLACEHOLDERS,
                    "propertyNames": {"pattern": "^[a-z][a-z0-9_]{0,31}$"},
                    "additionalProperties": {"$ref": "#/$defs/state_text_value_source"},
                },
            },
        },
        "state_text_value_source": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["state"],
                    "additionalProperties": False,
                    "properties": {
                        "state": {"$ref": "#/$defs/safe_id"},
                        "format": {"$ref": "#/$defs/state_text_format"},
                    },
                },
                {
                    "type": "object",
                    "required": ["selector", "measure"],
                    "additionalProperties": False,
                    "properties": {
                        "selector": {"$ref": "#/$defs/safe_id"},
                        "measure": {"const": "count"},
                        "format": {"$ref": "#/$defs/state_text_format"},
                    },
                },
            ]
        },
        "state_text_format": {
            "description": "The closed formatter set. Formatters use no locale or time zone.",
            "oneOf": [
                {
                    "type": "object",
                    "required": ["format"],
                    "additionalProperties": False,
                    "properties": {"format": {"const": "integer"}},
                },
                {
                    "type": "object",
                    "required": ["format", "digits"],
                    "additionalProperties": False,
                    "properties": {
                        "format": {"const": "decimal"},
                        "digits": {"type": "integer", "minimum": 0, "maximum": STATE_TEXT_FORMAT_MAX_DIGITS},
                    },
                },
                {
                    "type": "object",
                    "required": ["format", "digits"],
                    "additionalProperties": False,
                    "properties": {
                        "format": {"const": "percent"},
                        "digits": {"type": "integer", "minimum": 0, "maximum": STATE_TEXT_FORMAT_MAX_DIGITS},
                    },
                },
                {
                    "type": "object",
                    "required": ["format", "currency"],
                    "additionalProperties": False,
                    "properties": {
                        "format": {"const": "currency"},
                        "currency": {"enum": sorted(STATE_TEXT_CURRENCIES)},
                    },
                },
                {
                    "type": "object",
                    "required": ["format"],
                    "additionalProperties": False,
                    "properties": {"format": {"const": "date_iso"}},
                },
            ],
        },
        "state_replay_assertions_v5": {
            "type": "array",
            "maxItems": APP_STATE_MAX_REPLAY_ASSERTIONS,
            "items": {"$ref": "#/$defs/state_replay_assertion_v5"},
        },
        "state_replay_assertion_v5": {
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
                "expect_text": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/safe_id"},
                    "additionalProperties": {
                        "type": "string",
                        "maxLength": APP_STATE_TEXT_V5_MAX_TEMPLATE_CHARS
                        + APP_STATE_TEXT_MAX_PLACEHOLDERS * APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
                    },
                },
            },
        },
        "intent_bundle": {
            "type": "object",
            "description": "Embedded local V1 IntentBundle. viewspec check and viewspec prove enforce the full local V1 validator.",
        },
    },
}


__all__ = ["AGENT_APP_BUNDLE_SCHEMA", "APPBUNDLE_V5_PROMPT_SECTION"]
