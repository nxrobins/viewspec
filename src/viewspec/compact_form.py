"""The compact authoring form of an IntentBundle: ViewSpecBuilder calls written as JSON.

A compact document carries ``"format": "compact"`` at its root. :func:`normalize` turns it into the
canonical IntentBundle document by driving :class:`viewspec.sdk.builder.ViewSpecBuilder`, so every
derived id (``items_row_1``, ``items_row_1_value``) is the id the builder derives, and the result is
byte-identical to what the same builder calls export. Normalization is a pure function of the
document: the same compact document always gives the same canonical document.

The compact form rejects, with a ``COMPACT_*`` code, every construct that the builder cannot express
or would lose silently (an unknown key, a duplicate id, a missing item list). Everything the canonical
contract checks (motif completeness, style targets, collection action rules, caps) is left to the
unchanged canonical validator, which reports it with canonical ``$.view_spec`` paths.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import (
    MAX_AGENT_ACTIONS,
    MAX_AGENT_INTENT_BYTES,
    MAX_AGENT_MOTIFS,
    MAX_AGENT_NODES,
    MAX_AGENT_REGIONS,
    MAX_AGENT_STYLES,
    PRESENT_AS_ALIASES,
    SAFE_AGENT_EXPLICIT_TARGET_PATTERN,
    SAFE_AGENT_ID_PATTERN,
    SAFE_AGENT_ID_RE,
    SAFE_AGENT_STYLE_TARGET_PATTERN,
    SUPPORTED_AGENT_ACTION_KINDS,
    SUPPORTED_AGENT_MOTIFS,
    SUPPORTED_AGENT_REGION_LAYOUTS,
    AgentValidationIssue,
)
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import INTENT_BUNDLE_SCHEMA_VERSION, PRESENT_AS_TO_PRIMITIVE

COMPACT_FORMAT = "compact"
COMPACT_FORM_SCHEMA_ID = "https://viewspec.dev/agent-intent-compact.schema.json"
MAX_COMPACT_BYTES = MAX_AGENT_INTENT_BYTES

# The closed set of codes the compact form itself reports. UNSUPPORTED_SCHEMA_VERSION is shared with
# the canonical validator because it means the same thing at the same path.
COMPACT_ERROR_CODES: tuple[str, ...] = (
    "COMPACT_ACTION_KIND_UNSUPPORTED",
    "COMPACT_ACTION_PAYLOAD_UNKNOWN",
    "COMPACT_ACTION_TARGET_UNKNOWN",
    "COMPACT_DUPLICATE_ID",
    "COMPACT_FIELD_INVALID",
    "COMPACT_MISSING_FIELD",
    "COMPACT_MOTIF_ITEMS_MISSING",
    "COMPACT_MOTIF_KIND_UNKNOWN",
    "COMPACT_REGION_UNKNOWN",
    "COMPACT_TOO_LARGE",
    "COMPACT_UNKNOWN_FIELD",
)

ROOT_FIELDS = (
    "schema_version",
    "format",
    "id",
    "title",
    "aesthetic",
    "root",
    "regions",
    "motifs",
    "inputs",
    "styles",
    "actions",
)
ROOT_OPTIONS_FIELDS = ("min_children", "max_children")
REGION_FIELDS = ("id", "parent", "role", "layout", "min_children", "max_children")
MOTIF_COMMON_FIELDS = ("id", "kind", "region", "group")
INPUT_FIELDS = ("id", "label", "value", "region", "group")
STYLE_FIELDS = ("id", "target", "token")
ACTION_FIELDS = ("id", "kind", "label", "region", "target", "collection", "payload")
DEFAULT_REGION = "main"
ROOT_REGION = "root"

# One item key per collection-like motif kind, and the builder method it serializes.
ITEM_KEY_BY_KIND: dict[str, str] = {
    "table": "rows",
    "dashboard": "cards",
    "outline": "items",
    "comparison": "items",
    "list": "items",
    "form": "fields",
    "detail": "fields",
}
# The word the builder uses in a derived item id: ``<motif>_<word>_<n>``.
DERIVED_ITEM_WORD_BY_KIND: dict[str, str] = {
    "table": "row",
    "dashboard": "card",
    "outline": "branch",
    "comparison": "item",
    "list": "item",
    "form": "field",
    "detail": "field",
}
ITEM_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "table": ("id", "label", "value", "values"),
    "dashboard": ("id", "label", "value", "present_as"),
    "outline": ("id", "label"),
    "comparison": ("id", "label", "value"),
    "list": ("id", "label", "value", "description", "values"),
    "form": ("id", "label", "value"),
    "detail": ("id", "label", "value", "values"),
}
ITEM_REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    "table": ("label",),
    "dashboard": ("label", "value"),
    "outline": ("label",),
    "comparison": ("label", "value"),
    "list": ("label",),
    "form": ("label",),
    "detail": ("label",),
}
# Scalar motifs carry their attributes on the motif object itself.
SCALAR_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "hero": ("title", "description", "eyebrow"),
    "empty_state": ("title", "description"),
    "loading_state": ("title", "description"),
    "error_state": ("title", "description"),
}
# Keys that ``values`` may not carry because the item's own keys own those attributes.
RESERVED_VALUES_KEYS = ("label", "value", "description")
# bulk_action needs a hand-declared ``_selection`` binding, which the compact form cannot write.
COMPACT_ACTION_KINDS: tuple[str, ...] = tuple(kind for kind in SUPPORTED_AGENT_ACTION_KINDS if kind != "bulk_action")
# One compact motif per kind, in SUPPORTED_AGENT_MOTIFS order. The agent system prompt prints each one
# as a line of JSON, and COMPACT_FORM_SCHEMA publishes them as x-viewspec-examples. Each one, alone in
# a compact document, passes viewspec check with no fixes; tests run that check on every entry.
COMPACT_MOTIF_EXAMPLES: dict[str, dict[str, Any]] = {
    "table": {"id": "orders", "kind": "table", "rows": [{"label": "#1042", "value": "$120"}]},
    "dashboard": {"id": "kpis", "kind": "dashboard", "cards": [{"label": "Open", "value": 42}]},
    "outline": {"id": "plan", "kind": "outline", "items": [{"label": "Scope"}]},
    "comparison": {
        "id": "plans",
        "kind": "comparison",
        "items": [{"label": "Free", "value": 0}, {"label": "Pro", "value": 9}],
    },
    "list": {"id": "tasks", "kind": "list", "items": [{"label": "Review"}]},
    "form": {"id": "contact", "kind": "form", "fields": [{"label": "Email"}]},
    "detail": {"id": "owner", "kind": "detail", "fields": [{"label": "Owner", "value": "Ada"}]},
    "empty_state": {"id": "empty", "kind": "empty_state", "title": "No orders"},
    "loading_state": {"id": "loading", "kind": "loading_state", "title": "Loading"},
    "error_state": {"id": "failed", "kind": "error_state", "title": "Failed"},
    "hero": {"id": "intro", "kind": "hero", "title": "Orders"},
}

_EXPLICIT_TARGET_RE = re.compile(SAFE_AGENT_EXPLICIT_TARGET_PATTERN)
_STYLE_TARGET_RE = re.compile(SAFE_AGENT_STYLE_TARGET_PATTERN)
_COMPACT_MARKER_RE = re.compile(r'"format"\s*:\s*"compact"')

COMPACT_FORM_PROMPT_SECTION = """Compact form. You may write the IntentBundle in the compact form instead of the canonical form. Set "format": "compact" at the root; ViewSpec normalizes the document into the canonical bundle before it validates and compiles, and viewspec check FILE --canonical-out PATH writes that bundle. A --patch names the compact document. Root keys: schema_version (1), format, id, title, aesthetic, root, regions, motifs, inputs, styles, actions. A motif has id, kind, optional region (default main) and group, and one item key by kind: table rows, dashboard cards, detail and form fields, list, comparison and outline items. hero takes title, description and eyebrow; empty_state, loading_state and error_state take title and description. An item has label and value; list items also description; table, list and detail items also values, an object of extra attributes; cards also present_as. Item ids are optional and derive as <motif>_<row|card|field|item|branch>_<n>. An input has id, label and value. An action has id, kind, label, optional region, target (region:id, binding:id, motif:id or view:id) or collection (a table or list motif id), and payload, a list of field or input ids. bulk_action is not available in the compact form. Unknown keys are rejected. Example:
{"schema_version":1,"format":"compact","id":"sales","title":"Sales","motifs":[{"id":"kpis","kind":"dashboard","group":"cards","cards":[{"id":"revenue","label":"Revenue","value":"$2.4M"}]}]}
"""

_SAFE_ID = {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}
_JSON_VALUE = {"not": {"type": "null"}}
_NULLABLE_COUNT = {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]}


def _item_schema(kind: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in ITEM_FIELDS_BY_KIND[kind]:
        if field == "id":
            properties[field] = _SAFE_ID
        elif field == "values":
            properties[field] = {
                "type": "object",
                "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN, "not": {"enum": list(RESERVED_VALUES_KEYS)}},
                "additionalProperties": _JSON_VALUE,
            }
        elif field == "present_as":
            properties[field] = {"enum": sorted(PRESENT_AS_TO_PRIMITIVE)}
        else:
            properties[field] = _JSON_VALUE
    return {
        "type": "object",
        "required": list(ITEM_REQUIRED_BY_KIND[kind]),
        "additionalProperties": False,
        "properties": properties,
    }


def _motif_schema(kind: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": _SAFE_ID,
        "kind": {"const": kind},
        "region": _SAFE_ID,
        "group": _SAFE_ID,
    }
    if kind in ITEM_KEY_BY_KIND:
        item_key = ITEM_KEY_BY_KIND[kind]
        properties[item_key] = {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_AGENT_NODES,
            "items": _item_schema(kind),
        }
        required = ["id", "kind", item_key]
    else:
        for field in SCALAR_FIELDS_BY_KIND[kind]:
            properties[field] = _JSON_VALUE
        required = ["id", "kind", "title"]
    return {"type": "object", "required": required, "additionalProperties": False, "properties": properties}


COMPACT_FORM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": COMPACT_FORM_SCHEMA_ID,
    "title": "ViewSpec Agent IntentBundle Compact Form",
    "description": (
        "The compact authoring form of a ViewSpec IntentBundle: ViewSpecBuilder calls as JSON. "
        "ViewSpec normalizes it into the canonical IntentBundle before validation and compilation."
    ),
    "type": "object",
    "required": ["format", "id"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "type": "integer",
            "const": INTENT_BUNDLE_SCHEMA_VERSION,
            "description": "Optional IntentBundle schema version. Documents without schema_version are schema version 1.",
        },
        "format": {
            "const": COMPACT_FORMAT,
            "description": "Marks the compact form. Canonical bundles have no format key.",
        },
        "id": _SAFE_ID,
        "title": _JSON_VALUE,
        "aesthetic": {"enum": list(AESTHETIC_PROFILE_TOKENS)},
        "root": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"min_children": {"type": "integer", "minimum": 0}, "max_children": _NULLABLE_COUNT},
        },
        "regions": {"type": "array", "maxItems": MAX_AGENT_REGIONS, "items": {"$ref": "#/$defs/region"}},
        "motifs": {
            "type": "array",
            "maxItems": MAX_AGENT_MOTIFS,
            "items": {
                "oneOf": [{"$ref": f"#/$defs/motif_{kind}"} for kind in SUPPORTED_AGENT_MOTIFS],
                "x-viewspec-examples": [COMPACT_MOTIF_EXAMPLES[kind] for kind in SUPPORTED_AGENT_MOTIFS],
            },
        },
        "inputs": {"type": "array", "maxItems": MAX_AGENT_NODES, "items": {"$ref": "#/$defs/input"}},
        "styles": {"type": "array", "maxItems": MAX_AGENT_STYLES, "items": {"$ref": "#/$defs/style"}},
        "actions": {"type": "array", "maxItems": MAX_AGENT_ACTIONS, "items": {"$ref": "#/$defs/action"}},
    },
    "x-viewspec-intent-schema-versions": [INTENT_BUNDLE_SCHEMA_VERSION],
    "x-viewspec-compact-item-keys": dict(ITEM_KEY_BY_KIND),
    "x-viewspec-invariants": [
        "A compact document normalizes to the canonical IntentBundle that the same ViewSpecBuilder calls export.",
        "Derived item ids are <motif>_<row|card|field|item|branch>_<n>, counting every item of the motif.",
        "Without regions, the bundle has the root region and a main region; with regions, root plus the listed ones.",
        "Motifs, inputs, styles, the aesthetic profile and actions normalize in that order.",
        "Every id must be unique within its kind, and node and binding ids must not collide.",
        "Action payload entries name field or input ids, which bind to <id>_value, or binding ids.",
        "bulk_action is not available in the compact form.",
        "Unknown keys are rejected instead of silently ignored.",
        "The canonical validator runs on the normalized bundle; its issues keep canonical $.view_spec paths.",
    ],
    "$defs": {
        "region": {
            "type": "object",
            "required": ["id"],
            "additionalProperties": False,
            "properties": {
                "id": _SAFE_ID,
                "parent": _SAFE_ID,
                "role": {"type": "string", "minLength": 1},
                "layout": {"enum": list(SUPPORTED_AGENT_REGION_LAYOUTS)},
                "min_children": {"type": "integer", "minimum": 0},
                "max_children": _NULLABLE_COUNT,
            },
        },
        **{f"motif_{kind}": _motif_schema(kind) for kind in SUPPORTED_AGENT_MOTIFS},
        "input": {
            "type": "object",
            "required": ["id", "label"],
            "additionalProperties": False,
            "properties": {
                "id": _SAFE_ID,
                "label": _JSON_VALUE,
                "value": _JSON_VALUE,
                "region": _SAFE_ID,
                "group": _SAFE_ID,
            },
        },
        "style": {
            "type": "object",
            "required": ["target", "token"],
            "additionalProperties": False,
            "properties": {
                "id": _SAFE_ID,
                "target": {"type": "string", "pattern": SAFE_AGENT_STYLE_TARGET_PATTERN},
                "token": {"type": "string", "minLength": 1},
            },
        },
        "action": {
            "type": "object",
            "required": ["id", "kind", "label"],
            "additionalProperties": False,
            "properties": {
                "id": _SAFE_ID,
                "kind": {"enum": list(COMPACT_ACTION_KINDS)},
                "label": {"type": "string", "minLength": 1},
                "region": _SAFE_ID,
                "target": {"type": "string", "pattern": SAFE_AGENT_EXPLICIT_TARGET_PATTERN},
                "collection": _SAFE_ID,
                "payload": {"type": "array", "items": _SAFE_ID},
            },
        },
    },
}


class CompactFormError(ValueError):
    """A compact document that cannot be normalized; ``issues`` lists every problem found."""

    def __init__(self, issues: list[AgentValidationIssue]) -> None:
        self.issues = list(issues)
        first = self.issues[0] if self.issues else None
        super().__init__(f"{first.code} at {first.path}: {first.message}" if first else "invalid compact document")


def detect_compact(payload: Any) -> bool:
    """Return whether ``payload`` is a compact-form document: an object with ``"format": "compact"``."""
    return isinstance(payload, Mapping) and payload.get("format") == COMPACT_FORMAT


def looks_like_compact_text(text: str) -> bool:
    """A bounded check for the compact marker in text that is too large to parse under the cap."""
    return _COMPACT_MARKER_RE.search(text) is not None


def compact_too_large_issue(size: int) -> AgentValidationIssue:
    return AgentValidationIssue(
        "error",
        "COMPACT_TOO_LARGE",
        "$",
        f"Compact document is {size} bytes; the v1 local limit is {MAX_COMPACT_BYTES} bytes.",
        "Split the UI into smaller documents before validation.",
    )


def canonical_json_text(document: Mapping[str, Any]) -> str:
    """The text ``viewspec init`` writes for a document: sorted keys, two-space indent, no trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True)


def normalize(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical IntentBundle document for a compact one, or raise :class:`CompactFormError`."""
    canonical, issues = normalize_with_issues(payload)
    if issues or canonical is None:
        raise CompactFormError(issues)
    return canonical


def normalize_with_issues(payload: Any) -> tuple[dict[str, Any] | None, list[AgentValidationIssue]]:
    """Normalize a compact document; return the canonical document and the issues that stopped it.

    The document is validated in full before any builder call, so the issue list names every problem
    at once and the builder never raises on compact input.
    """
    plan = _CompactPlan()
    plan.validate(payload)
    if plan.issues:
        return None, plan.issues
    return plan.build(), []


def _issue(code: str, path: str, message: str, suggestion: str) -> AgentValidationIssue:
    return AgentValidationIssue("error", code, path, message, suggestion)


def _safe_id(value: Any) -> str | None:
    """``value`` when it is a safe id string, else None."""
    return value if isinstance(value, str) and SAFE_AGENT_ID_RE.match(value) else None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass
class _MotifPlan:
    index: int
    id: str
    kind: str
    region: str
    group: str | None
    items: list[dict[str, Any]]
    scalars: dict[str, Any]


@dataclass
class _InputPlan:
    id: str
    label: Any
    value: Any
    region: str
    group: str | None


@dataclass
class _ActionPlan:
    id: str
    kind: str
    label: str
    region: str
    target_ref: str | None
    payload_bindings: list[str]


class _CompactPlan:
    """Two phases: ``validate`` collects issues and derived ids; ``build`` drives the builder."""

    def __init__(self) -> None:
        self.issues: list[AgentValidationIssue] = []
        self.view_id: str = ""
        self.title: Any = None
        self.aesthetic: str | None = None
        self.root_min_children: int = 1
        self.root_max_children: int | None = None
        self.regions: list[dict[str, Any]] | None = None
        self.region_ids: set[str] = {ROOT_REGION, DEFAULT_REGION}
        self.motifs: list[_MotifPlan] = []
        self.inputs: list[_InputPlan] = []
        self.styles: list[tuple[str, str, str]] = []
        self.actions: list[_ActionPlan] = []
        self.node_ids: set[str] = set()
        self.binding_ids: set[str] = set()
        self.motif_kinds: dict[str, str] = {}
        self.group_ids: set[str] = set()

    # -- phase one: validate -------------------------------------------------------------------

    def add(self, code: str, path: str, message: str, suggestion: str) -> None:
        self.issues.append(_issue(code, path, message, suggestion))

    def validate(self, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            self.add("COMPACT_FIELD_INVALID", "$", "A compact document must be a JSON object.", "Return one object.")
            return
        self._reject_unknown(payload, "$", ROOT_FIELDS)
        if payload.get("format") != COMPACT_FORMAT:
            self.add(
                "COMPACT_FIELD_INVALID",
                "$.format",
                'A compact document declares "format": "compact".',
                'Set "format": "compact", or write the canonical IntentBundle without a format key.',
            )
        if "schema_version" in payload:
            version = payload["schema_version"]
            if (
                isinstance(version, bool)
                or not isinstance(version, (int, float))
                or version != INTENT_BUNDLE_SCHEMA_VERSION
            ):
                self.add(
                    "UNSUPPORTED_SCHEMA_VERSION",
                    "$.schema_version",
                    f"IntentBundle schema_version must be {INTENT_BUNDLE_SCHEMA_VERSION} when present.",
                    "Use schema_version 1 or omit the field; documents without schema_version are schema version 1.",
                )
        if "id" not in payload:
            self.add(
                "COMPACT_MISSING_FIELD", "$.id", "The document needs an id.", "Add a safe id such as sales_dashboard."
            )
        else:
            view_id = _safe_id(payload["id"])
            if view_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    "$.id",
                    "id must match ^[A-Za-z0-9_.-]+$.",
                    "Use letters, digits, _, . and -.",
                )
            else:
                self.view_id = view_id
                self.node_ids.add(view_id)
        if "title" in payload:
            if payload["title"] is None:
                self.add(
                    "COMPACT_FIELD_INVALID", "$.title", "title must not be null.", "Omit title or give it a value."
                )
            else:
                self.title = payload["title"]
        if "aesthetic" in payload:
            aesthetic = payload["aesthetic"]
            if not isinstance(aesthetic, str) or aesthetic not in AESTHETIC_PROFILE_TOKENS:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    "$.aesthetic",
                    f"aesthetic must be one of: {', '.join(AESTHETIC_PROFILE_TOKENS)}.",
                    "Pick one aesthetic.* profile token or omit aesthetic.",
                )
            else:
                self.aesthetic = aesthetic
        self._validate_root(payload.get("root"), "root" in payload)
        self._validate_regions(payload.get("regions"), "regions" in payload)
        self._validate_motifs(payload.get("motifs"), "motifs" in payload)
        self._validate_inputs(payload.get("inputs"), "inputs" in payload)
        self._validate_styles(payload.get("styles"), "styles" in payload)
        self._validate_actions(payload.get("actions"), "actions" in payload)

    def _reject_unknown(self, mapping: Mapping[str, Any], path: str, allowed: tuple[str, ...]) -> None:
        for key in mapping:
            if key not in allowed:
                self.add(
                    "COMPACT_UNKNOWN_FIELD",
                    f"{path}.{key}",
                    f"Unknown compact field {key!r}; allowed: {', '.join(allowed)}.",
                    "Remove the field. The compact form rejects unknown keys.",
                )

    def _array(self, value: Any, path: str, present: bool) -> list[Any] | None:
        if not present:
            return []
        if not isinstance(value, list):
            self.add("COMPACT_FIELD_INVALID", path, f"{path} must be an array.", "Write a JSON array of objects.")
            return None
        return value

    def _object(self, value: Any, path: str) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            self.add("COMPACT_FIELD_INVALID", path, f"{path} must be an object.", "Write a JSON object.")
            return None
        return value

    def _validate_root(self, root: Any, present: bool) -> None:
        if not present:
            return
        options = self._object(root, "$.root")
        if options is None:
            return
        self._reject_unknown(options, "$.root", ROOT_OPTIONS_FIELDS)
        if "min_children" in options:
            if not _is_count(options["min_children"]):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    "$.root.min_children",
                    "min_children must be an integer >= 0.",
                    "Use 0 or more.",
                )
            else:
                self.root_min_children = options["min_children"]
        if "max_children" in options:
            value = options["max_children"]
            if value is not None and not _is_count(value):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    "$.root.max_children",
                    "max_children must be null or an integer >= 0.",
                    "Use null for no limit.",
                )
            else:
                self.root_max_children = value

    def _validate_regions(self, regions: Any, present: bool) -> None:
        if not present:
            return
        entries = self._array(regions, "$.regions", present)
        if entries is None:
            return
        self.regions = []
        self.region_ids = {ROOT_REGION}
        declared: list[tuple[int, str, Mapping[str, Any]]] = []
        for index, entry in enumerate(entries):
            path = f"$.regions[{index}]"
            region = self._object(entry, path)
            if region is None:
                continue
            self._reject_unknown(region, path, REGION_FIELDS)
            if "id" not in region:
                self.add("COMPACT_MISSING_FIELD", f"{path}.id", "A region needs an id.", "Add a safe id.")
                continue
            region_id = _safe_id(region["id"])
            if region_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID", f"{path}.id", "Region id must match ^[A-Za-z0-9_.-]+$.", "Use a safe id."
                )
                continue
            if region_id == ROOT_REGION:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    f"{path}.id",
                    "root is the implicit root region.",
                    "Configure the root region with the root key and list only the regions below it.",
                )
                continue
            if region_id in self.region_ids:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    f"{path}.id",
                    f"Region id {region_id!r} is declared twice.",
                    "Give each region one id.",
                )
                continue
            self.region_ids.add(region_id)
            declared.append((index, region_id, region))
        for index, region_id, region in declared:
            path = f"$.regions[{index}]"
            parent = region.get("parent", ROOT_REGION)
            if _safe_id(parent) is None:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.parent",
                    "parent must be a safe region id.",
                    "Name root or a listed region.",
                )
            elif parent not in self.region_ids:
                self.add(
                    "COMPACT_REGION_UNKNOWN",
                    f"{path}.parent",
                    f"Unknown parent region {parent!r}.",
                    "Name root or a region listed in regions.",
                )
            role = region.get("role", region_id)
            if not isinstance(role, str) or not role:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.role",
                    "role must be a non-empty string.",
                    "Give the region a role or omit it.",
                )
            layout = region.get("layout", "stack")
            if layout not in SUPPORTED_AGENT_REGION_LAYOUTS:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.layout",
                    f"layout must be one of: {', '.join(SUPPORTED_AGENT_REGION_LAYOUTS)}.",
                    "Use stack, grid, or cluster.",
                )
            min_children = region.get("min_children", 1)
            if not _is_count(min_children):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.min_children",
                    "min_children must be an integer >= 0.",
                    "Use 0 or more.",
                )
            max_children = region.get("max_children")
            if max_children is not None and not _is_count(max_children):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.max_children",
                    "max_children must be null or an integer >= 0.",
                    "Use null for no limit.",
                )
            self.regions.append(
                {
                    "id": region_id,
                    "parent": parent,
                    "role": role,
                    "layout": layout,
                    "min_children": min_children,
                    "max_children": max_children,
                }
            )

    def _check_region(self, region: Any, path: str) -> str:
        region_id = _safe_id(region)
        if region_id is None:
            self.add(
                "COMPACT_FIELD_INVALID",
                path,
                "region must be a safe region id.",
                "Name a declared region such as main.",
            )
            return DEFAULT_REGION
        if region_id not in self.region_ids:
            self.add(
                "COMPACT_REGION_UNKNOWN",
                path,
                f"Unknown region {region_id!r}; declared: {', '.join(sorted(self.region_ids))}.",
                "Name a declared region, or add it to regions.",
            )
        return region_id

    def _check_group(self, group: Any, path: str, *, owned: bool) -> str | None:
        """A motif owns its group; an input may join one, so only motifs claim group ids."""
        if group is None:
            return None
        group_id = _safe_id(group)
        if group_id is None:
            self.add("COMPACT_FIELD_INVALID", path, "group must be a safe id.", "Use a safe id or omit group.")
            return None
        if owned:
            if group_id in self.group_ids:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    path,
                    f"Group {group_id!r} is already owned by another motif.",
                    "Give each motif its own group; inputs may join a motif's group.",
                )
                return None
            self.group_ids.add(group_id)
        return group_id

    def _claim_node(self, node_id: str, path: str) -> bool:
        if node_id in self.node_ids:
            self.add(
                "COMPACT_DUPLICATE_ID",
                path,
                f"Node id {node_id!r} is already used; the builder would overwrite the first node.",
                "Give every item, input, hero and state motif, and the document, a distinct id.",
            )
            return False
        self.node_ids.add(node_id)
        return True

    def _claim_binding(self, binding_id: str, path: str) -> None:
        if binding_id in self.binding_ids:
            self.add(
                "COMPACT_DUPLICATE_ID",
                path,
                f"Binding id {binding_id!r} is derived twice.",
                "Rename the item or the extra attribute so the derived binding ids stay distinct.",
            )
            return
        self.binding_ids.add(binding_id)

    def _validate_motifs(self, motifs: Any, present: bool) -> None:
        entries = self._array(motifs, "$.motifs", present)
        if entries is None:
            return
        for index, entry in enumerate(entries):
            path = f"$.motifs[{index}]"
            motif = self._object(entry, path)
            if motif is None:
                continue
            if "kind" not in motif:
                self.add(
                    "COMPACT_MISSING_FIELD",
                    f"{path}.kind",
                    "A motif needs a kind.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
                continue
            kind = motif["kind"]
            if not isinstance(kind, str) or kind not in SUPPORTED_AGENT_MOTIFS:
                self.add(
                    "COMPACT_MOTIF_KIND_UNKNOWN",
                    f"{path}.kind",
                    f"Unknown motif kind {kind!r}.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
                continue
            item_key = ITEM_KEY_BY_KIND.get(kind)
            allowed = MOTIF_COMMON_FIELDS + ((item_key,) if item_key else SCALAR_FIELDS_BY_KIND[kind])
            self._reject_unknown(motif, path, allowed)
            if "id" not in motif:
                self.add("COMPACT_MISSING_FIELD", f"{path}.id", "A motif needs an id.", "Add a safe id such as items.")
                continue
            motif_id = _safe_id(motif["id"])
            if motif_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID", f"{path}.id", "Motif id must match ^[A-Za-z0-9_.-]+$.", "Use a safe id."
                )
                continue
            if motif_id in self.motif_kinds:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    f"{path}.id",
                    f"Motif id {motif_id!r} is declared twice.",
                    "Give each motif one id.",
                )
                continue
            self.motif_kinds[motif_id] = kind
            region = self._check_region(motif.get("region", DEFAULT_REGION), f"{path}.region")
            group = self._check_group(motif.get("group"), f"{path}.group", owned=True)
            if item_key:
                items = self._validate_items(motif, motif_id, kind, item_key, path)
                self.motifs.append(_MotifPlan(index, motif_id, kind, region, group, items, {}))
            else:
                scalars = self._validate_scalars(motif, motif_id, kind, path)
                self.motifs.append(_MotifPlan(index, motif_id, kind, region, group, [], scalars))

    def _validate_items(
        self, motif: Mapping[str, Any], motif_id: str, kind: str, item_key: str, path: str
    ) -> list[dict[str, Any]]:
        raw_items = motif.get(item_key)
        if item_key not in motif or (isinstance(raw_items, list) and not raw_items):
            self.add(
                "COMPACT_MOTIF_ITEMS_MISSING",
                f"{path}.{item_key}",
                f"{kind} motifs need at least one entry in {item_key}.",
                f"Add {item_key} with one or more items.",
            )
            return []
        if not isinstance(raw_items, list):
            self.add(
                "COMPACT_FIELD_INVALID",
                f"{path}.{item_key}",
                f"{item_key} must be an array.",
                "Write a JSON array of items.",
            )
            return []
        allowed = ITEM_FIELDS_BY_KIND[kind]
        required = ITEM_REQUIRED_BY_KIND[kind]
        word = DERIVED_ITEM_WORD_BY_KIND[kind]
        items: list[dict[str, Any]] = []
        for number, entry in enumerate(raw_items, start=1):
            item_path = f"{path}.{item_key}[{number - 1}]"
            item = self._object(entry, item_path)
            if item is None:
                continue
            self._reject_unknown(item, item_path, allowed)
            for field in required:
                if field not in item:
                    self.add(
                        "COMPACT_MISSING_FIELD",
                        f"{item_path}.{field}",
                        f"Each {kind} item needs {field}.",
                        f"Add {field}.",
                    )
            for field in allowed:
                if field in item and field not in {"id", "values", "present_as"} and item[field] is None:
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{item_path}.{field}",
                        f"{field} must not be null.",
                        f"Omit {field} or give it a value.",
                    )
            if "id" in item:
                explicit_id = _safe_id(item["id"])
                if explicit_id is None:
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{item_path}.id",
                        "Item id must match ^[A-Za-z0-9_.-]+$.",
                        "Use a safe id or omit id.",
                    )
                    continue
                item_id = explicit_id
            else:
                item_id = f"{motif_id}_{word}_{number}"
            # An alias from the closed lossless table passes through to the canonical binding, where
            # the repair loop spells it canonically (FIX_PRESENT_AS_ALIAS) or, with fix off, reports it.
            present_as = item.get("present_as")
            if "present_as" in item and not (
                isinstance(present_as, str)
                and (present_as in PRESENT_AS_TO_PRIMITIVE or present_as in PRESENT_AS_ALIASES)
            ):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{item_path}.present_as",
                    f"present_as must be one of: {', '.join(sorted(PRESENT_AS_TO_PRIMITIVE))}.",
                    "Use value, badge, or another published present_as.",
                )
            extra_attrs: list[str] = []
            if "values" in item:
                values = item["values"]
                if not isinstance(values, Mapping):
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{item_path}.values",
                        "values must be an object of extra attributes.",
                        'Write {"attr": value}.',
                    )
                    values = {}
                for attr, attr_value in values.items():
                    if attr in RESERVED_VALUES_KEYS:
                        self.add(
                            "COMPACT_FIELD_INVALID",
                            f"{item_path}.values.{attr}",
                            f"values may not carry {attr!r}; the item's own key owns it.",
                            f"Move {attr} to the item.",
                        )
                    elif _safe_id(attr) is None:
                        self.add(
                            "COMPACT_FIELD_INVALID",
                            f"{item_path}.values.{attr}",
                            "values keys must match ^[A-Za-z0-9_.-]+$.",
                            "Use safe attribute names.",
                        )
                    elif attr_value is None:
                        self.add(
                            "COMPACT_FIELD_INVALID",
                            f"{item_path}.values.{attr}",
                            "values entries must not be null.",
                            "Omit the attribute or give it a value.",
                        )
                    else:
                        extra_attrs.append(attr)
            if not self._claim_node(item_id, f"{item_path}.id" if "id" in item else item_path):
                continue
            bound: list[str] = ["label"]
            if kind in {"dashboard", "comparison", "form"} or ("value" in item and item["value"] is not None):
                bound.append("value")
            if kind == "list" and "description" in item and item["description"] is not None:
                bound.append("description")
            bound.extend(extra_attrs)
            for attr in bound:
                self._claim_binding(f"{item_id}_{attr}", item_path)
            items.append(dict(item))
        return items

    def _validate_scalars(self, motif: Mapping[str, Any], motif_id: str, kind: str, path: str) -> dict[str, Any]:
        scalars: dict[str, Any] = {}
        if "title" not in motif:
            self.add("COMPACT_MISSING_FIELD", f"{path}.title", f"A {kind} motif needs a title.", "Add title.")
        for field in SCALAR_FIELDS_BY_KIND[kind]:
            if field in motif:
                if motif[field] is None:
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{path}.{field}",
                        f"{field} must not be null.",
                        f"Omit {field} or give it a value.",
                    )
                else:
                    scalars[field] = motif[field]
        if not self._claim_node(motif_id, f"{path}.id"):
            return scalars
        for field in SCALAR_FIELDS_BY_KIND[kind]:
            if field in scalars:
                self._claim_binding(f"{motif_id}_{field}", path)
        return scalars

    def _validate_inputs(self, inputs: Any, present: bool) -> None:
        entries = self._array(inputs, "$.inputs", present)
        if entries is None:
            return
        for index, entry in enumerate(entries):
            path = f"$.inputs[{index}]"
            item = self._object(entry, path)
            if item is None:
                continue
            self._reject_unknown(item, path, INPUT_FIELDS)
            if "id" not in item:
                self.add("COMPACT_MISSING_FIELD", f"{path}.id", "An input needs an id.", "Add a safe id such as query.")
                continue
            input_id = _safe_id(item["id"])
            if input_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID", f"{path}.id", "Input id must match ^[A-Za-z0-9_.-]+$.", "Use a safe id."
                )
                continue
            if "label" not in item:
                self.add("COMPACT_MISSING_FIELD", f"{path}.label", "An input needs a label.", "Add label.")
            for field in ("label", "value"):
                if field in item and item[field] is None:
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{path}.{field}",
                        f"{field} must not be null.",
                        f"Omit {field} or give it a value.",
                    )
            region = self._check_region(item.get("region", DEFAULT_REGION), f"{path}.region")
            group = self._check_group(item.get("group"), f"{path}.group", owned=False)
            if not self._claim_node(input_id, f"{path}.id"):
                continue
            self._claim_binding(f"{input_id}_label", path)
            self._claim_binding(f"{input_id}_value", path)
            self.inputs.append(_InputPlan(input_id, item.get("label"), item.get("value", ""), region, group))

    def _validate_styles(self, styles: Any, present: bool) -> None:
        entries = self._array(styles, "$.styles", present)
        if entries is None:
            return
        style_ids: set[str] = set()
        for index, entry in enumerate(entries):
            path = f"$.styles[{index}]"
            style = self._object(entry, path)
            if style is None:
                continue
            self._reject_unknown(style, path, STYLE_FIELDS)
            style_id = _safe_id(style.get("id", f"style_{index + 1}"))
            if style_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.id",
                    "Style id must match ^[A-Za-z0-9_.-]+$.",
                    "Use a safe id or omit id.",
                )
                continue
            if style_id in style_ids:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    f"{path}.id",
                    f"Style id {style_id!r} is declared twice.",
                    "Give each style one id.",
                )
                continue
            style_ids.add(style_id)
            target = style.get("target")
            token = style.get("token")
            ok = True
            if "target" not in style:
                self.add(
                    "COMPACT_MISSING_FIELD",
                    f"{path}.target",
                    "A style needs a target.",
                    "Name region:id, binding:id, motif:id, view:id, or a bare id.",
                )
                ok = False
            elif not isinstance(target, str) or not _STYLE_TARGET_RE.match(target):
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.target",
                    "target must be region:id, binding:id, motif:id, view:id, or a bare safe id.",
                    "Fix the target.",
                )
                ok = False
            if "token" not in style:
                self.add(
                    "COMPACT_MISSING_FIELD", f"{path}.token", "A style needs a token.", "Use a published style token."
                )
                ok = False
            elif not isinstance(token, str) or not token:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.token",
                    "token must be a non-empty string.",
                    "Use a published style token.",
                )
                ok = False
            if ok and isinstance(target, str) and isinstance(token, str):
                self.styles.append((style_id, target, token))

    def _validate_actions(self, actions: Any, present: bool) -> None:
        entries = self._array(actions, "$.actions", present)
        if entries is None:
            return
        action_ids: set[str] = set()
        for index, entry in enumerate(entries):
            path = f"$.actions[{index}]"
            action = self._object(entry, path)
            if action is None:
                continue
            self._reject_unknown(action, path, ACTION_FIELDS)
            ok = True
            action_id = _safe_id(action["id"]) if "id" in action else None
            if "id" not in action:
                self.add(
                    "COMPACT_MISSING_FIELD", f"{path}.id", "An action needs an id.", "Add a safe id such as submit."
                )
                ok = False
            elif action_id is None:
                self.add(
                    "COMPACT_FIELD_INVALID", f"{path}.id", "Action id must match ^[A-Za-z0-9_.-]+$.", "Use a safe id."
                )
                ok = False
            elif action_id in action_ids:
                self.add(
                    "COMPACT_DUPLICATE_ID",
                    f"{path}.id",
                    f"Action id {action_id!r} is declared twice.",
                    "Give each action one id.",
                )
                ok = False
            else:
                action_ids.add(action_id)
            kind = action.get("kind")
            if "kind" not in action:
                self.add(
                    "COMPACT_MISSING_FIELD",
                    f"{path}.kind",
                    "An action needs a kind.",
                    f"Use one of: {', '.join(COMPACT_ACTION_KINDS)}.",
                )
                ok = False
            elif not isinstance(kind, str) or kind not in COMPACT_ACTION_KINDS:
                detail = (
                    "bulk_action needs a hand-declared selection binding; write that bundle in the canonical form."
                    if kind == "bulk_action"
                    else f"Use one of: {', '.join(COMPACT_ACTION_KINDS)}."
                )
                self.add(
                    "COMPACT_ACTION_KIND_UNSUPPORTED",
                    f"{path}.kind",
                    f"Action kind {kind!r} is not available in the compact form.",
                    detail,
                )
                ok = False
            label = action.get("label")
            if "label" not in action:
                self.add("COMPACT_MISSING_FIELD", f"{path}.label", "An action needs a label.", "Add the button text.")
                ok = False
            elif not isinstance(label, str) or not label:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.label",
                    "label must be a non-empty string.",
                    "Add the button text.",
                )
                ok = False
            region = self._check_region(action.get("region", DEFAULT_REGION), f"{path}.region")
            target_ref: str | None = None
            if "target" in action and "collection" in action:
                self.add(
                    "COMPACT_FIELD_INVALID",
                    f"{path}.collection",
                    "An action takes target or collection, not both.",
                    "Keep one of them.",
                )
                ok = False
            elif "target" in action:
                target = action["target"]
                if not isinstance(target, str) or not _EXPLICIT_TARGET_RE.match(target):
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{path}.target",
                        "target must be region:id, binding:id, motif:id, or view:id.",
                        "Use an explicit target reference.",
                    )
                    ok = False
                elif not self._target_exists(target):
                    self.add(
                        "COMPACT_ACTION_TARGET_UNKNOWN",
                        f"{path}.target",
                        f"Action target {target!r} names nothing declared in this document.",
                        "Point target at a declared region, binding, motif, or the view id.",
                    )
                    ok = False
                else:
                    target_ref = target
            elif "collection" in action:
                collection = _safe_id(action["collection"])
                if collection is None:
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{path}.collection",
                        "collection must be a safe motif id.",
                        "Name a table or list motif.",
                    )
                    ok = False
                elif self.motif_kinds.get(collection) not in {"table", "list"}:
                    self.add(
                        "COMPACT_ACTION_TARGET_UNKNOWN",
                        f"{path}.collection",
                        f"collection {collection!r} is not a declared table or list motif.",
                        "Name the id of a table or list motif in motifs.",
                    )
                    ok = False
                else:
                    target_ref = f"motif:{collection}"
            payload_bindings: list[str] = []
            if "payload" in action:
                payload = action["payload"]
                if not isinstance(payload, list):
                    self.add(
                        "COMPACT_FIELD_INVALID",
                        f"{path}.payload",
                        "payload must be an array of field or input ids.",
                        "Write a JSON array of ids.",
                    )
                    ok = False
                else:
                    for position, entry_id in enumerate(payload):
                        entry_path = f"{path}.payload[{position}]"
                        safe_entry = _safe_id(entry_id)
                        if safe_entry is None:
                            self.add(
                                "COMPACT_FIELD_INVALID",
                                entry_path,
                                "payload entries must be safe ids.",
                                "Name a field or input id.",
                            )
                            ok = False
                        elif f"{safe_entry}_value" in self.binding_ids:
                            payload_bindings.append(f"{safe_entry}_value")
                        elif safe_entry in self.binding_ids:
                            payload_bindings.append(safe_entry)
                        else:
                            self.add(
                                "COMPACT_ACTION_PAYLOAD_UNKNOWN",
                                entry_path,
                                f"payload entry {safe_entry!r} names no field, input, or binding in this document.",
                                "Name the id of a form field, an input, or a derived binding.",
                            )
                            ok = False
            if ok and action_id is not None and isinstance(kind, str) and isinstance(label, str):
                self.actions.append(_ActionPlan(action_id, kind, label, region, target_ref, payload_bindings))

    def _target_exists(self, target: str) -> bool:
        prefix, _, target_id = target.partition(":")
        if prefix == "region":
            return target_id in self.region_ids
        if prefix == "binding":
            return target_id in self.binding_ids
        if prefix == "motif":
            return target_id in self.motif_kinds
        return target_id == self.view_id

    # -- phase two: build ----------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        builder = ViewSpecBuilder(
            self.view_id,
            root_attrs={"title": self.title} if self.title is not None else None,
            root_min_children=self.root_min_children,
            root_max_children=self.root_max_children,
            default_main_region=self.regions is None,
        )
        for region in self.regions or []:
            builder.add_region(
                region["id"],
                parent_region=region["parent"],
                role=region["role"],
                layout=region["layout"],
                min_children=region["min_children"],
                max_children=region["max_children"],
            )
        for motif in self.motifs:
            self._build_motif(builder, motif)
        for item in self.inputs:
            builder.add_text_input(item.id, label=item.label, value=item.value, region=item.region, group_id=item.group)
        for style_id, target, token in self.styles:
            builder.add_style(style_id, target, token)
        if self.aesthetic is not None:
            builder.set_aesthetic_profile(self.aesthetic)
        for action in self.actions:
            builder.add_action(
                action.id,
                action.kind,
                action.label,
                target_region=action.region,
                target_ref=action.target_ref,
                payload_bindings=action.payload_bindings,
            )
        return {"schema_version": INTENT_BUNDLE_SCHEMA_VERSION, **builder.build_bundle().to_json()}

    @staticmethod
    def _build_motif(builder: ViewSpecBuilder, motif: _MotifPlan) -> None:
        kind = motif.kind
        if kind == "table":
            table = builder.add_table(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                table.add_row(
                    label=item["label"], value=item.get("value"), values=item.get("values"), id=item.get("id")
                )
        elif kind == "dashboard":
            dashboard = builder.add_dashboard(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                dashboard.add_card(
                    label=item["label"],
                    value=item["value"],
                    id=item.get("id"),
                    value_present_as=item.get("present_as", "value"),
                )
        elif kind == "outline":
            outline = builder.add_outline(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                outline.add_branch(label=item["label"], id=item.get("id"))
        elif kind == "comparison":
            comparison = builder.add_comparison(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                comparison.add_item(label=item["label"], value=item["value"], id=item.get("id"))
        elif kind == "list":
            items = builder.add_list(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                items.add_item(
                    label=item["label"],
                    value=item.get("value"),
                    description=item.get("description"),
                    values=item.get("values"),
                    id=item.get("id"),
                )
        elif kind == "form":
            form = builder.add_form(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                form.add_field(label=item["label"], value=item.get("value", ""), id=item.get("id"))
        elif kind == "detail":
            detail = builder.add_detail(motif.id, region=motif.region, group_id=motif.group)
            for item in motif.items:
                detail.add_field(
                    label=item["label"], value=item.get("value"), values=item.get("values"), id=item.get("id")
                )
        elif kind == "hero":
            builder.add_hero(
                motif.id,
                title=motif.scalars["title"],
                description=motif.scalars.get("description"),
                eyebrow=motif.scalars.get("eyebrow"),
                region=motif.region,
                group_id=motif.group,
            )
        elif kind == "empty_state":
            builder.add_empty_state(
                motif.id,
                title=motif.scalars["title"],
                description=motif.scalars.get("description"),
                region=motif.region,
                group_id=motif.group,
            )
        elif kind == "loading_state":
            builder.add_loading_state(
                motif.id,
                title=motif.scalars["title"],
                description=motif.scalars.get("description"),
                region=motif.region,
                group_id=motif.group,
            )
        else:
            builder.add_error_state(
                motif.id,
                title=motif.scalars["title"],
                description=motif.scalars.get("description"),
                region=motif.region,
                group_id=motif.group,
            )


__all__ = [
    "COMPACT_ACTION_KINDS",
    "COMPACT_ERROR_CODES",
    "COMPACT_FORMAT",
    "COMPACT_FORM_PROMPT_SECTION",
    "COMPACT_FORM_SCHEMA",
    "COMPACT_FORM_SCHEMA_ID",
    "COMPACT_MOTIF_EXAMPLES",
    "DERIVED_ITEM_WORD_BY_KIND",
    "ITEM_KEY_BY_KIND",
    "MAX_COMPACT_BYTES",
    "CompactFormError",
    "canonical_json_text",
    "compact_too_large_issue",
    "detect_compact",
    "looks_like_compact_text",
    "normalize",
    "normalize_with_issues",
]
