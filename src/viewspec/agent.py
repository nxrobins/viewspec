"""Agent-facing helpers for producing valid ViewSpec intent bundles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from viewspec.aesthetics import (
    AESTHETIC_PROFILE_LAYOUT_PROPS,
    AESTHETIC_PROFILE_LAYOUT_ROLES,
    AESTHETIC_PROFILE_TOKENS,
    is_aesthetic_profile_token,
    profile_style_facts,
)
from viewspec.compiler import (
    COLLECTION_ACTION_KINDS,
    COLLECTION_MOTIF_KINDS,
    CONFLICTING_STATE_MOTIF_KINDS,
    MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS,
    MAX_COLLECTION_ACTIONS_PER_COLLECTION,
    MAX_STATE_MOTIFS,
    STATE_MOTIF_KINDS,
    SUPPORTED_ACTION_KINDS,
    SUPPORTED_MOTIF_KINDS,
    CompilerInputError,
    UnsupportedMotifError,
    compile,
)
from viewspec.types import DEFAULT_STYLE_TOKEN_VALUES, IntentBundle, PRESENT_AS_TO_PRIMITIVE, parse_canonical_address

SUPPORTED_AGENT_MOTIFS = SUPPORTED_MOTIF_KINDS
SUPPORTED_AGENT_ACTION_KINDS = SUPPORTED_ACTION_KINDS
SUPPORTED_AGENT_CARDINALITIES = ("exactly_once",)
SUPPORTED_AGENT_GROUP_KINDS = ("ordered",)
SUPPORTED_AGENT_REGION_LAYOUTS = ("stack", "grid", "cluster")
HOSTED_ONLY_ROOT_FIELDS = ("design", "motif_library")
HOSTED_ONLY_VIEW_SPEC_FIELDS = ("inputs", "projections", "rules")
ROOT_ALLOWED_FIELDS = {"substrate", "view_spec"}
SUBSTRATE_ALLOWED_FIELDS = {"id", "root_id", "nodes"}
SUBSTRATE_NODE_ALLOWED_FIELDS = {"id", "kind", "attrs", "slots", "edges"}
REGION_ALLOWED_FIELDS = {"id", "parent_region", "role", "layout", "min_children", "max_children"}
BINDING_ALLOWED_FIELDS = {"id", "address", "target_region", "present_as", "cardinality"}
GROUP_ALLOWED_FIELDS = {"id", "kind", "members", "target_region"}
MOTIF_ALLOWED_FIELDS = {"id", "kind", "region", "members"}
STYLE_ALLOWED_FIELDS = {"id", "target", "token"}
ACTION_ALLOWED_FIELDS = {"id", "kind", "label", "target_region", "target_ref", "payload_bindings"}
VIEW_SPEC_ALLOWED_FIELDS = {
    "id",
    "substrate_id",
    "complexity_tier",
    "root_region",
    "regions",
    "bindings",
    "groups",
    "motifs",
    "styles",
    "actions",
}
SUPPORTED_AGENT_STYLE_TOKENS = tuple(
    token
    for token in (
        "emphasis.low",
        "emphasis.medium",
        "emphasis.high",
        "density.compact",
        "density.regular",
        "density.airy",
        "palette.temperature",
        "tone.neutral",
        "tone.muted",
        "tone.accent",
        "tone.warning",
        "tone.positive",
        "action.accent",
        "surface.none",
        "surface.subtle",
        "surface.strong",
        "rhythm.hierarchy",
        "narrative.flow",
        "align.start",
        "align.center",
        "align.end",
        *AESTHETIC_PROFILE_TOKENS,
    )
    if token in DEFAULT_STYLE_TOKEN_VALUES
    or token in {"tone.warning", "tone.positive", "rhythm.hierarchy", "narrative.flow"}
    or token in AESTHETIC_PROFILE_TOKENS
)
AGENT_AESTHETIC_PROFILE_CONTRACT = {
    "tokens": list(AESTHETIC_PROFILE_TOKENS),
    "style_token_prefix": "aesthetic.",
    "max_declarations": 1,
    "target": "view:{view_spec.id}",
    "layout_roles": sorted(AESTHETIC_PROFILE_LAYOUT_ROLES),
    "layout_props": {
        profile: {role: dict(props) for role, props in role_props.items()}
        for profile, role_props in AESTHETIC_PROFILE_LAYOUT_PROPS.items()
    },
    "style_facts": {profile: profile_style_facts(profile) for profile in AESTHETIC_PROFILE_TOKENS},
    "non_claims": [
        "not_css",
        "not_pixel_perfect_visual_proof",
        "not_design_certification",
    ],
}
MAX_AGENT_INTENT_BYTES = 256 * 1024
MAX_AGENT_NODES = 200
MAX_AGENT_NODE_ATTRS = 64
MAX_AGENT_NODE_RELATIONS = 64
MAX_AGENT_RELATION_VALUES = 200
MAX_AGENT_REGIONS = 32
MAX_AGENT_BINDINGS = 400
MAX_AGENT_GROUPS = 64
MAX_AGENT_MOTIFS = 32
MAX_AGENT_STYLES = 400
MAX_AGENT_ACTIONS = 64
MAX_AGENT_ACTION_PAYLOAD_BINDINGS = 64
MAX_AGENT_CORRECTION_PROMPT_ISSUES = 20
MAX_AGENT_REPAIR_CHECKLIST_ITEMS = 8
DEFAULT_AGENT_REPAIR_SUGGESTION = "Regenerate the full IntentBundle using only the local V1 agent contract."
SAFE_AGENT_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"
SAFE_AGENT_ID_RE = re.compile(SAFE_AGENT_ID_PATTERN)
SAFE_AGENT_EXPLICIT_TARGET_PATTERN = r"^(region|binding|motif|view):[A-Za-z0-9_.-]+$"
SAFE_AGENT_STYLE_TARGET_PATTERN = r"^(?:(?:region|binding|motif|view):)?[A-Za-z0-9_.-]+$"
SAFE_AGENT_ADDRESS_PATTERN = (
    r"^node:[A-Za-z0-9_.-]+"
    r"(?:#attr:[A-Za-z0-9_.-]+|#slot:[A-Za-z0-9_.-]+(?:\[\d+])?|#edge:[A-Za-z0-9_.-]+)?$"
)

AGENT_SYSTEM_PROMPT = """You are a ViewSpec IntentBundle compiler.

Your job is to translate user intent into ViewSpec IntentBundle JSON. You do not output HTML, CSS, React, or CompositionIR. CompositionIR is compiler output only.

Output strict JSON only. Do not wrap it in markdown. Do not explain it.

Use stable ids and object keys matching this pattern only: A-Z, a-z, 0-9, underscore, dot, and dash. Do not use spaces, colons, slashes, markup, or path-like ids.

The JSON object must contain:
- substrate.id
- substrate.root_id
- substrate.nodes as an object keyed by node ID, never an array
- every substrate node with id, kind, attrs, slots, and edges
- view_spec.id
- view_spec.substrate_id
- view_spec.complexity_tier
- view_spec.root_region
- view_spec.regions
- view_spec.bindings
- view_spec.groups
- view_spec.motifs
- view_spec.styles
- view_spec.actions

Use only these v1 motif kinds: table, dashboard, outline, comparison, list, form, detail, empty_state, loading_state, error_state, hero.

Use only these binding present_as values: text, label, value, badge, input, rich_text, image_slot, rule.

Use only these action kinds: select, submit, navigate, search, filter, sort, paginate, bulk_action.

Use only this v1 binding cardinality: exactly_once.

Use only these v1 region layouts: stack, grid, cluster.

Use only this v1 group kind: ordered.

Do not include hosted-only fields in the local v1 contract: root design, root motif_library, view_spec.inputs, view_spec.projections, or view_spec.rules. Those belong to the hosted extended compiler contract.

Do not add custom or extension fields to local v1 objects. Unknown fields are rejected instead of silently ignored.

Use style tokens only from this v1 set: emphasis.low, emphasis.medium, emphasis.high, density.compact, density.regular, density.airy, palette.temperature, tone.neutral, tone.muted, tone.accent, tone.warning, tone.positive, action.accent, surface.none, surface.subtle, surface.strong, rhythm.hierarchy, narrative.flow, align.start, align.center, align.end, aesthetic.calm_ops, aesthetic.premium_saas, aesthetic.data_dense, aesthetic.editorial_product, aesthetic.executive_review.

Aesthetic profile tokens are deterministic art-direction handles, not CSS. At most one style may use an aesthetic.* token, it must target exactly view:{view_spec.id}, and it must be one of aesthetic.calm_ops, aesthetic.premium_saas, aesthetic.data_dense, aesthetic.editorial_product, or aesthetic.executive_review. The compiler may derive governed style projection and bounded layout metadata from that token.

Use canonical binding addresses:
- node:{node_id}
- node:{node_id}#attr:{attr_name}
- node:{node_id}#slot:{slot_name}
- node:{node_id}#slot:{slot_name}[{index}]
- node:{node_id}#edge:{edge_name}

For JSON wire compatibility, slots and edges are maps whose values contain a values array:
{
  "slots": {"items": {"values": ["item_1", "item_2"]}},
  "edges": {"next": {"values": ["item_2"]}}
}

All binding IDs must be unique. Any binding with cardinality exactly_once must use an address that appears only once. Semantic edges must reference declared substrate node IDs. Style targets must use region:id, binding:id, motif:id, view:id, or an unambiguous bare id. Action target_ref must be empty/null or use region:id, binding:id, motif:id, or view:id. Region, group, motif, style, and action references must resolve to declared IDs. Region parent links must form one acyclic tree rooted at view_spec.root_region.

Motifs must be semantically complete. Every motif must have at least one declared member. Hero and empty_state motifs need a title, heading, headline, or label binding. Loading_state and error_state motifs need exactly one title, heading, headline, or label binding and at most one description, body, or message binding. Form motifs need at least one input binding. Table, dashboard, and detail motifs need both label and value/text-style bindings. Comparison motifs need at least two distinct semantic items.

Stateful collection actions are bounded. Search, filter, sort, paginate, and bulk_action must use target_ref motif:{id} for a declared table or list motif. Search, filter, sort, and paginate need 1-8 payload_bindings. Bulk_action needs exactly one payload binding whose id ends with _selection or _selected_ids. A table or list may have at most 8 collection actions, and a region may not mix loading_state or error_state with a table or list.

Use complexity_tier >= 1. Region min_children must be >= 0. Region max_children must be null or >= min_children.

Stay inside the v1 local contract caps: max 256KB JSON, 200 substrate nodes, 32 regions, 400 bindings, 64 groups, 32 motifs, 400 styles, 64 actions, 64 attrs/slots/edges per node, 200 values per slot or edge, and 64 payload bindings per action. Split larger products into smaller IntentBundles.

Generated JSON is not a finished ViewSpec proof. After saving it as viewspec.intent.json, the host workflow must run viewspec prove --out .viewspec-proof or run viewspec validate-intent viewspec.intent.json --json, viewspec compile, and viewspec check before claiming the UI artifact is valid. Read .viewspec-proof/PROOF.md first when a proof bundle exists; use proof_report.json for machine-readable status and support_bundle.json for redacted failure triage. Do not describe ViewSpec proof as pixel-perfect visual regression, accessibility certification, arbitrary host-app certification, or hosted compiler publish automation.

When revising an existing IntentBundle, run viewspec diff-intent old.intent.json new.intent.json --json before inspecting generated artifacts. Review semantic_changes first, including compact aesthetic profile style impact counts and bounded layout deltas when a profile changes. For concise review text, use the human diff-intent output, MCP semantic_summary, or Python intent_semantic_change_lines(diff["semantic_changes"]).

For a multi-screen internal tool, emit AppBundle JSON as viewspec.app.json instead of inventing a router or app scaffold. Use schema_version 1 for unbound fixture context, schema_version 2 with resource_binding "fixture_readonly_v0" and per-screen resource_views when the proof must verify exact fixture scalar visibility inside declared target motifs, or schema_version 3 with the same fixture_readonly_v0 binding plus bounded interactive_state_v0 state, mutations, selectors, and replay assertions when the user needs a deterministic reducer artifact. Generate the V1/V2 starter with viewspec init-app or viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.app.json. Validate with viewspec validate-app viewspec.app.json --json, review changes with viewspec diff-app old.app.json new.app.json --json, compile a local Static Shell V0 artifact with viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json, and prove with viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json. V3 mutation triggers must reference declared embedded screen actions and may read only those actions' payload_bindings via from_payload. This is an app contract, static shell artifact, per-screen source-artifact proof, optional read-only fixture visibility proof, and optional pure reducer/replay proof only: no dynamic routes, browser history proof, runtime data binding, live DOM rebinding, deployable React/Vite/Next app, framework state adapter, persistence, sync, backend, unbounded custom code, or hosted extended compiler behavior.


Optional reference grounding

Do not call remote reference libraries by default. Use external or hosted reference sources only when the user explicitly asks for research or the repository instructions explicitly configure an approved source.

If reference grounding is explicitly enabled, use it only to inform semantic intent: sections, hierarchy, typical fields, and motif choices. Do not copy pixel layouts, hardcoded copy, design tokens, screenshot URLs, image bytes, or external references into IntentBundle output. If the approved reference source is unavailable, proceed without it.
"""

AGENT_INTENT_BUNDLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/agent-intent-bundle.schema.json",
    "title": "ViewSpec Agent IntentBundle",
    "description": "Agent-authored ViewSpec compiler input. Agents output IntentBundle JSON, never CompositionIR.",
    "type": "object",
    "required": ["substrate", "view_spec"],
    "additionalProperties": False,
    "not": {"anyOf": [{"required": [field]} for field in HOSTED_ONLY_ROOT_FIELDS]},
    "properties": {
        "substrate": {"$ref": "#/$defs/substrate"},
        "view_spec": {"$ref": "#/$defs/view_spec"},
    },
    "x-viewspec-invariants": [
        "view_spec.substrate_id must equal substrate.id.",
        "substrate.root_id must be a key in substrate.nodes.",
        "Each substrate.nodes object key must equal that node object's id.",
        "view_spec.root_region must be declared in view_spec.regions.",
        "Region parent_region links must form one acyclic tree rooted at view_spec.root_region.",
        "Semantic node edges must reference declared substrate node ids.",
        "Bindings, groups, motifs, styles, and actions may only reference declared ids.",
        "Motifs must be semantically complete for their kind before local compilation.",
        "IntentBundle may declare at most one aesthetic.* style token.",
        "Aesthetic profile style token must target exactly view:{view_spec.id}.",
        "Aesthetic profile tokens derive governed style projection and bounded layout metadata for content_grid, metric_grid, and featured metric_card roles.",
        "Hosted-only fields design, motif_library, view_spec.inputs, view_spec.projections, and view_spec.rules are rejected by the local schema and validate-intent.",
        "Unknown extension fields are rejected instead of silently ignored.",
        "JSON Schema enforces shape and caps; viewspec validate-intent enforces cross-reference invariants.",
    ],
    "x-viewspec-aesthetic-profiles": AGENT_AESTHETIC_PROFILE_CONTRACT,
    "$defs": {
        "values": {
            "type": "object",
            "required": ["values"],
            "additionalProperties": False,
            "properties": {"values": {"type": "array", "maxItems": MAX_AGENT_RELATION_VALUES}},
        },
        "edge_values": {
            "type": "object",
            "required": ["values"],
            "additionalProperties": False,
            "properties": {
                "values": {
                    "type": "array",
                    "maxItems": MAX_AGENT_RELATION_VALUES,
                    "items": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                }
            },
        },
        "substrate_node": {
            "type": "object",
            "required": ["id", "kind", "attrs", "slots", "edges"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "kind": {"type": "string", "minLength": 1},
                "attrs": {"type": "object", "maxProperties": MAX_AGENT_NODE_ATTRS, "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN}},
                "slots": {
                    "type": "object",
                    "maxProperties": MAX_AGENT_NODE_RELATIONS,
                    "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN},
                    "additionalProperties": {"$ref": "#/$defs/values"},
                },
                "edges": {
                    "type": "object",
                    "maxProperties": MAX_AGENT_NODE_RELATIONS,
                    "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN},
                    "additionalProperties": {"$ref": "#/$defs/edge_values"},
                },
            },
        },
        "substrate": {
            "type": "object",
            "required": ["id", "root_id", "nodes"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "root_id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "nodes": {
                    "type": "object",
                    "maxProperties": MAX_AGENT_NODES,
                    "propertyNames": {"pattern": SAFE_AGENT_ID_PATTERN},
                    "additionalProperties": {"$ref": "#/$defs/substrate_node"},
                },
            },
        },
        "region": {
            "type": "object",
            "required": ["id", "parent_region", "role", "layout", "min_children", "max_children"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "parent_region": {"anyOf": [{"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}, {"const": ""}, {"type": "null"}]},
                "role": {"type": "string", "minLength": 1},
                "layout": {"enum": list(SUPPORTED_AGENT_REGION_LAYOUTS)},
                "min_children": {"type": "integer", "minimum": 0},
                "max_children": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
            },
        },
        "binding": {
            "type": "object",
            "required": ["id", "address", "target_region", "present_as", "cardinality"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "address": {"type": "string", "pattern": SAFE_AGENT_ADDRESS_PATTERN},
                "target_region": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "present_as": {"enum": sorted(PRESENT_AS_TO_PRIMITIVE)},
                "cardinality": {"enum": list(SUPPORTED_AGENT_CARDINALITIES)},
            },
        },
        "group": {
            "type": "object",
            "required": ["id", "kind", "members", "target_region"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "kind": {"enum": list(SUPPORTED_AGENT_GROUP_KINDS)},
                "members": {"type": "array", "maxItems": MAX_AGENT_BINDINGS, "items": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}},
                "target_region": {"anyOf": [{"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}, {"const": ""}, {"type": "null"}]},
            },
        },
        "motif": {
            "type": "object",
            "required": ["id", "kind", "region", "members"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "kind": {"enum": list(SUPPORTED_AGENT_MOTIFS)},
                "region": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "members": {"type": "array", "maxItems": MAX_AGENT_BINDINGS, "items": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}},
            },
        },
        "style": {
            "type": "object",
            "required": ["id", "target", "token"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "target": {"type": "string", "pattern": SAFE_AGENT_STYLE_TARGET_PATTERN},
                "token": {"enum": list(SUPPORTED_AGENT_STYLE_TOKENS)},
            },
        },
        "action": {
            "type": "object",
            "required": ["id", "kind", "label", "target_region", "target_ref", "payload_bindings"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "kind": {"enum": list(SUPPORTED_AGENT_ACTION_KINDS)},
                "label": {"type": "string", "minLength": 1},
                "target_region": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "target_ref": {
                    "anyOf": [
                        {"type": "string", "pattern": SAFE_AGENT_EXPLICIT_TARGET_PATTERN},
                        {"const": ""},
                        {"type": "null"},
                    ]
                },
                "payload_bindings": {"type": "array", "maxItems": MAX_AGENT_ACTION_PAYLOAD_BINDINGS, "items": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN}},
            },
        },
        "view_spec": {
            "type": "object",
            "required": [
                "id",
                "substrate_id",
                "complexity_tier",
                "root_region",
                "regions",
                "bindings",
                "groups",
                "motifs",
                "styles",
                "actions",
            ],
            "additionalProperties": False,
            "not": {"anyOf": [{"required": [field]} for field in HOSTED_ONLY_VIEW_SPEC_FIELDS]},
            "properties": {
                "id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "substrate_id": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "complexity_tier": {"type": "integer", "minimum": 1},
                "root_region": {"type": "string", "pattern": SAFE_AGENT_ID_PATTERN},
                "regions": {"type": "array", "maxItems": MAX_AGENT_REGIONS, "items": {"$ref": "#/$defs/region"}},
                "bindings": {"type": "array", "maxItems": MAX_AGENT_BINDINGS, "items": {"$ref": "#/$defs/binding"}},
                "groups": {"type": "array", "maxItems": MAX_AGENT_GROUPS, "items": {"$ref": "#/$defs/group"}},
                "motifs": {"type": "array", "maxItems": MAX_AGENT_MOTIFS, "items": {"$ref": "#/$defs/motif"}},
                "styles": {"type": "array", "maxItems": MAX_AGENT_STYLES, "items": {"$ref": "#/$defs/style"}},
                "actions": {"type": "array", "maxItems": MAX_AGENT_ACTIONS, "items": {"$ref": "#/$defs/action"}},
            },
        },
    },
}


@dataclass(frozen=True)
class AgentValidationIssue:
    severity: str
    code: str
    path: str
    message: str
    suggestion: str | None = None

    def to_json(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "suggestion": self.suggestion or DEFAULT_AGENT_REPAIR_SUGGESTION,
        }


@dataclass(frozen=True)
class AgentValidationResult:
    valid: bool
    bundle: IntentBundle | None
    issues: list[AgentValidationIssue]

    def to_json(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [issue.to_json() for issue in self.issues],
            "repair_checklist": [] if self.valid else agent_repair_checklist(self),
        }


def validate_agent_intent_bundle(
    payload: str | dict[str, Any],
    *,
    require_reference_compiler_support: bool = True,
) -> AgentValidationResult:
    """Validate an agent-authored IntentBundle and optionally compile it locally."""
    data, issues = _coerce_payload(payload)
    if data is None:
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    issues.extend(_validate_intent_bundle_shape(data))
    if issues:
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    try:
        bundle = IntentBundle.from_json(data)
    except Exception as exc:
        issues.append(
            AgentValidationIssue(
                "error",
                "INTENT_BUNDLE_PARSE_ERROR",
                "$",
                f"IntentBundle.from_json rejected the payload: {exc}",
                "Regenerate the full IntentBundle using the published JSON schema.",
            )
        )
        return AgentValidationResult(valid=False, bundle=None, issues=issues)

    if require_reference_compiler_support:
        try:
            ast = compile(bundle)
        except UnsupportedMotifError as exc:
            issues.append(
                AgentValidationIssue(
                    "error",
                    "UNSUPPORTED_MOTIF",
                    "$.view_spec.motifs",
                    str(exc),
                    f"Use only these v1 motifs: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
            )
        except CompilerInputError as exc:
            issues.append(
                AgentValidationIssue(
                    "error",
                    "COMPILER_INPUT_ERROR",
                    "$",
                    str(exc),
                    "Fix missing or impossible root substrate/view_spec declarations.",
                )
            )
        else:
            for diagnostic in ast.result.diagnostics:
                issues.append(
                    AgentValidationIssue(
                        diagnostic.severity or "error",
                        diagnostic.code,
                        _diagnostic_path(diagnostic),
                        diagnostic.message,
                        "Regenerate the referenced field and preserve the rest of the IntentBundle.",
                    )
                )

    return AgentValidationResult(valid=not issues, bundle=bundle if not issues else None, issues=issues)


def agent_correction_prompt(result: AgentValidationResult) -> str:
    """Return a compact correction prompt for an agent repair loop."""
    shown_issues = [issue.to_json() for issue in result.issues[:MAX_AGENT_CORRECTION_PROMPT_ISSUES]]
    report = {
        "issue_count": len(result.issues),
        "shown_issue_count": len(shown_issues),
        "truncated": len(result.issues) > len(shown_issues),
        "issue_codes": sorted({issue.code for issue in result.issues}),
        "affected_paths": _bounded_issue_paths(result.issues),
        "repair_mode": "regenerate_full_intent_bundle",
        "retry_command": "viewspec validate-intent viewspec.intent.json --json",
        "repair_checklist": agent_repair_checklist(result),
        "issues": shown_issues,
    }
    return (
        "Regenerate the full ViewSpec IntentBundle JSON. Output strict JSON only. "
        "Do not patch fragments. Fix this bounded validation report:\n"
        f"{json.dumps(report, separators=(',', ':'), sort_keys=True)}"
    )


def _bounded_issue_paths(issues: list[AgentValidationIssue]) -> list[str]:
    paths: list[str] = []
    for issue in issues:
        if issue.path not in paths:
            paths.append(issue.path)
        if len(paths) >= MAX_AGENT_CORRECTION_PROMPT_ISSUES:
            break
    return paths


def agent_repair_checklist(result: AgentValidationResult) -> list[str]:
    """Return bounded retry invariants for invalid agent-authored IntentBundles."""
    if result.valid:
        return []
    codes = {issue.code for issue in result.issues}
    checks: list[str] = []

    def add(text: str) -> None:
        if text not in checks and len(checks) < MAX_AGENT_REPAIR_CHECKLIST_ITEMS:
            checks.append(text)

    if codes & {"INVALID_JSON", "INVALID_JSON_VALUE", "INVALID_PAYLOAD", "COMPOSITION_IR_INPUT"}:
        add("Return one strict JSON object with substrate and view_spec; no markdown, arrays, CompositionIR, HTML, CSS, or React.")
    if codes & {
        "MISSING_FIELD",
        "NODES_MUST_BE_OBJECT",
        "INVALID_NODE",
        "INVALID_REGION",
        "INVALID_BINDING",
        "INVALID_GROUP",
        "INVALID_MOTIF",
        "INVALID_STYLE",
        "INVALID_ACTION",
        "INTENT_BUNDLE_PARSE_ERROR",
        "COMPILER_INPUT_ERROR",
    }:
        add("Include all required local V1 objects and arrays: substrate nodes plus view_spec regions, bindings, groups, motifs, styles, and actions.")
    if codes & {"UNKNOWN_FIELD", "HOSTED_ONLY_FIELD"}:
        add("Remove unknown and hosted-only fields; local V1 rejects root design, motif_library, inputs, projections, rules, and custom extensions.")
    if codes & {"INVALID_ID", "NODE_KEY_MISMATCH", "DUPLICATE_BINDING_ID", "DUPLICATE_REGION_ID", "DUPLICATE_GROUP_ID", "DUPLICATE_MOTIF_ID", "DUPLICATE_STYLE_ID", "DUPLICATE_ACTION_ID"}:
        add("Use unique safe ids and object keys with only letters, digits, underscore, dot, and dash; node map keys must match node ids.")
    if codes & {"SUBSTRATE_ID_MISMATCH", "MISSING_SUBSTRATE_ROOT", "MISSING_ROOT_REGION", "ROOT_REGION_HAS_PARENT", "DETACHED_REGION", "REGION_PARENT_CYCLE", "UNKNOWN_REGION", "UNKNOWN_EDGE_TARGET", "UNKNOWN_STYLE_TARGET", "UNKNOWN_ACTION_TARGET", "MISSING_GROUP_MEMBER", "MISSING_MOTIF_MEMBER", "UNKNOWN_ACTION_PAYLOAD_BINDING"}:
        add("Resolve every reference and keep regions as one acyclic tree rooted at view_spec.root_region.")
    if codes & {"INVALID_ADDRESS", "DUPLICATE_EXACTLY_ONCE_ADDRESS", "UNKNOWN_PRESENT_AS", "UNSUPPORTED_CARDINALITY", "UNSUPPORTED_GROUP_KIND", "UNSUPPORTED_REGION_LAYOUT"}:
        add("Use canonical node addresses, exactly_once cardinality, ordered groups, and stack/grid/cluster region layouts.")
    if codes & {
        "UNSUPPORTED_MOTIF",
        "EMPTY_MOTIF",
        "MOTIF_MISSING_INPUT",
        "MOTIF_MISSING_LABEL",
        "MOTIF_MISSING_TITLE",
        "MOTIF_MISSING_VALUE",
        "MOTIF_TOO_FEW_ITEMS",
        "STATE_MOTIF_TITLE_REQUIRED",
        "STATE_MOTIF_TOO_MANY_DESCRIPTIONS",
        "COLLECTION_STATE_CONFLICT",
    }:
        add("Use only supported motifs and make each motif semantically complete with the required label, value, title, input, or comparison members.")
    if codes & {
        "UNSUPPORTED_STYLE_TOKEN",
        "AMBIGUOUS_STYLE_TARGET",
        "UNSUPPORTED_ACTION_KIND",
        "INVALID_ACTION_TARGET_REF",
        "COLLECTION_ACTION_TARGET_INVALID",
        "COLLECTION_ACTION_PAYLOAD_REQUIRED",
        "COLLECTION_BULK_SELECTION_REQUIRED",
        "COLLECTION_BULK_SELECTION_AMBIGUOUS",
        "AESTHETIC_PROFILE_UNKNOWN",
        "AESTHETIC_PROFILE_MULTIPLE",
        "AESTHETIC_PROFILE_TARGET_INVALID",
    }:
        add("Use only published style tokens, unambiguous style targets, supported action kinds, and explicit region/binding/motif/view target_ref values.")
    if any(code.startswith("TOO_MANY_") for code in codes) or codes == {"INTENT_TOO_LARGE"} or "INTENT_TOO_LARGE" in codes:
        add("Stay within local V1 caps; split larger UI surfaces into smaller IntentBundles instead of forcing one bundle.")
    add("Regenerate the full IntentBundle, then rerun viewspec validate-intent before compiling.")
    return checks[:MAX_AGENT_REPAIR_CHECKLIST_ITEMS]


def _intent_too_large_issue(size: int) -> AgentValidationIssue:
    return AgentValidationIssue(
        "error",
        "INTENT_TOO_LARGE",
        "$",
        f"IntentBundle JSON is {size} bytes; the v1 local limit is {MAX_AGENT_INTENT_BYTES} bytes.",
        "Split the UI into smaller IntentBundles before validation.",
    )


def _coerce_payload(payload: str | dict[str, Any]) -> tuple[dict[str, Any] | None, list[AgentValidationIssue]]:
    payload_from_text = isinstance(payload, str)
    if isinstance(payload, str):
        size = len(payload.encode("utf-8"))
        if size > MAX_AGENT_INTENT_BYTES:
            return None, [_intent_too_large_issue(size)]
        try:
            payload = _strict_json_loads(payload)
        except json.JSONDecodeError as exc:
            return None, [
                AgentValidationIssue(
                    "error",
                    "INVALID_JSON",
                    "$",
                    f"Payload is not valid JSON: {exc.msg}",
                    "Return one strict JSON object with substrate and view_spec.",
                )
            ]
        except ValueError as exc:
            return None, [
                AgentValidationIssue(
                    "error",
                    "INVALID_JSON",
                    "$",
                    f"Payload is not strict JSON: {exc}",
                    "Return one strict JSON object with unique keys and finite JSON values.",
                )
            ]
    if not isinstance(payload, dict):
        return None, [
            AgentValidationIssue(
                "error",
                "INVALID_PAYLOAD",
                "$",
                "Agent output must be a JSON object.",
                "Return one IntentBundle object, not prose, markdown, or an array.",
            )
        ]
    if not payload_from_text:
        try:
            size = len(json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            return None, [
                AgentValidationIssue(
                    "error",
                    "INVALID_JSON_VALUE",
                    "$",
                    f"Dictionary payload contains a value that cannot be encoded as JSON: {exc}",
                    "Pass only finite JSON-serializable values in IntentBundle dictionaries.",
                )
            ]
        if size > MAX_AGENT_INTENT_BYTES:
            return None, [_intent_too_large_issue(size)]
    return payload, []


def _strict_json_loads(payload: str) -> dict[str, Any]:
    return json.loads(
        payload,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_non_standard_json_constant,
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r} is not allowed")


def _validate_intent_bundle_shape(data: dict[str, Any]) -> list[AgentValidationIssue]:
    issues: list[AgentValidationIssue] = []
    if "primitive" in data or "children" in data:
        issues.append(
            _issue(
                "COMPOSITION_IR_INPUT",
                "$",
                "Agent output looks like CompositionIR. Agents must output IntentBundle JSON.",
                "Move semantic data into substrate and declarative intent into view_spec.",
            )
        )

    _reject_unknown_fields(data, "$", ROOT_ALLOWED_FIELDS | set(HOSTED_ONLY_ROOT_FIELDS), issues)
    substrate = _required_object(data, "substrate", "$", issues)
    view_spec = _required_object(data, "view_spec", "$", issues)
    if substrate is None or view_spec is None:
        return issues
    _reject_unknown_fields(substrate, "$.substrate", SUBSTRATE_ALLOWED_FIELDS, issues)
    _reject_unknown_fields(view_spec, "$.view_spec", VIEW_SPEC_ALLOWED_FIELDS | set(HOSTED_ONLY_VIEW_SPEC_FIELDS), issues)
    hosted_only_found = _validate_hosted_only_fields(data, view_spec, issues)

    if isinstance(substrate.get("nodes"), list):
        issues.append(
            _issue(
                "NODES_MUST_BE_OBJECT",
                "$.substrate.nodes",
                "substrate.nodes must be an object keyed by node ID, not an array.",
                "Use {\"node_id\": {\"id\": \"node_id\", ...}}.",
            )
        )
        nodes = None
    else:
        nodes = _required_object(substrate, "nodes", "$.substrate", issues)

    root_id = _required_string(substrate, "root_id", "$.substrate", issues)
    substrate_id = _required_string(substrate, "id", "$.substrate", issues)
    view_substrate_id = _required_string(view_spec, "substrate_id", "$.view_spec", issues)
    view_id = _required_string(view_spec, "id", "$.view_spec", issues)
    root_region = _required_string(view_spec, "root_region", "$.view_spec", issues)
    _validate_safe_id(substrate_id, "$.substrate.id", "substrate id", issues)
    _validate_safe_id(root_id, "$.substrate.root_id", "substrate root id", issues)
    _validate_safe_id(view_substrate_id, "$.view_spec.substrate_id", "view substrate id", issues)
    _validate_safe_id(view_id, "$.view_spec.id", "view id", issues)
    _validate_safe_id(root_region, "$.view_spec.root_region", "root region id", issues)

    if substrate_id and view_substrate_id and substrate_id != view_substrate_id:
        issues.append(
            _issue(
                "SUBSTRATE_ID_MISMATCH",
                "$.view_spec.substrate_id",
                f"view_spec.substrate_id '{view_substrate_id}' does not match substrate.id '{substrate_id}'.",
                "Use the same substrate id in both locations.",
            )
        )

    if nodes is not None:
        if _check_count(nodes, MAX_AGENT_NODES, "$.substrate.nodes", "TOO_MANY_NODES", "substrate nodes", issues):
            _validate_nodes(nodes, root_id, issues)

    regions = _required_array(view_spec, "regions", "$.view_spec", issues)
    bindings = _required_array(view_spec, "bindings", "$.view_spec", issues)
    groups = _required_array(view_spec, "groups", "$.view_spec", issues)
    motifs = _required_array(view_spec, "motifs", "$.view_spec", issues)
    styles = _required_array(view_spec, "styles", "$.view_spec", issues)
    actions = _required_array(view_spec, "actions", "$.view_spec", issues)
    complexity_tier = _required_int(view_spec, "complexity_tier", "$.view_spec", issues)
    if complexity_tier is not None and complexity_tier < 1:
        issues.append(
            _issue(
                "INVALID_COMPLEXITY_TIER",
                "$.view_spec.complexity_tier",
                "view_spec.complexity_tier must be at least 1.",
                "Use a positive complexity tier, starting at 1.",
            )
        )

    caps_ok = all(
        (
            _check_count(regions, MAX_AGENT_REGIONS, "$.view_spec.regions", "TOO_MANY_REGIONS", "regions", issues),
            _check_count(bindings, MAX_AGENT_BINDINGS, "$.view_spec.bindings", "TOO_MANY_BINDINGS", "bindings", issues),
            _check_count(groups, MAX_AGENT_GROUPS, "$.view_spec.groups", "TOO_MANY_GROUPS", "groups", issues),
            _check_count(motifs, MAX_AGENT_MOTIFS, "$.view_spec.motifs", "TOO_MANY_MOTIFS", "motifs", issues),
            _check_count(styles, MAX_AGENT_STYLES, "$.view_spec.styles", "TOO_MANY_STYLES", "styles", issues),
            _check_count(actions, MAX_AGENT_ACTIONS, "$.view_spec.actions", "TOO_MANY_ACTIONS", "actions", issues),
        )
    )
    if not caps_ok:
        return issues
    if hosted_only_found:
        return issues

    region_ids = _collect_ids(regions, "$.view_spec.regions", "DUPLICATE_REGION_ID", issues)
    binding_ids = _collect_ids(bindings, "$.view_spec.bindings", "DUPLICATE_BINDING_ID", issues)
    binding_by_id = _index_bindings_by_id(bindings)
    group_ids = _collect_ids(groups, "$.view_spec.groups", "DUPLICATE_GROUP_ID", issues)
    motif_ids = _collect_ids(motifs, "$.view_spec.motifs", "DUPLICATE_MOTIF_ID", issues)
    motif_kind_by_id = _index_motif_kinds_by_id(motifs)
    style_ids = _collect_ids(styles, "$.view_spec.styles", "DUPLICATE_STYLE_ID", issues)
    action_ids = _collect_ids(actions, "$.view_spec.actions", "DUPLICATE_ACTION_ID", issues)

    if root_region and root_region not in region_ids:
        issues.append(
            _issue(
                "MISSING_ROOT_REGION",
                "$.view_spec.root_region",
                f"view_spec.root_region '{root_region}' is not declared in view_spec.regions.",
                "Add a matching root region object.",
            )
        )

    _validate_regions(regions, root_region, issues)
    _validate_bindings(bindings, binding_ids, region_ids, nodes, issues)
    _validate_groups(groups, binding_ids, region_ids, issues)
    _validate_motifs(motifs, binding_ids, binding_by_id, region_ids, issues)
    _validate_state_motif_conflicts(motifs, issues)
    _validate_styles(styles, view_id, region_ids, binding_ids, motif_ids, issues)
    _validate_actions(actions, view_id, region_ids, binding_ids, motif_ids, motif_kind_by_id, issues)

    # Touch these sets so duplicate collection stays explicit for future additions.
    _ = group_ids, style_ids, action_ids
    return issues


def _validate_hosted_only_fields(
    data: dict[str, Any],
    view_spec: dict[str, Any],
    issues: list[AgentValidationIssue],
) -> bool:
    found = False
    for key in HOSTED_ONLY_ROOT_FIELDS:
        if key in data:
            found = True
            issues.append(_hosted_only_issue(f"$.{key}", key))
    for key in HOSTED_ONLY_VIEW_SPEC_FIELDS:
        if key in view_spec:
            found = True
            issues.append(_hosted_only_issue(f"$.view_spec.{key}", key))
    return found


def _reject_unknown_fields(
    obj: dict[str, Any],
    path: str,
    allowed_fields: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for key in obj:
        if key not in allowed_fields:
            issues.append(
                _issue(
                    "UNKNOWN_FIELD",
                    f"{path}.{key}",
                    f"Field {key} is not part of the local V1 agent contract.",
                    "Remove unsupported extension fields or use the hosted compiler contract when those fields are required.",
                )
            )


def _hosted_only_issue(path: str, field: str) -> AgentValidationIssue:
    return _issue(
        "HOSTED_ONLY_FIELD",
        path,
        f"{field} is part of the hosted extended compiler contract, not the local V1 validate-intent contract.",
        "Use the hosted compiler for this bundle, or remove hosted-only fields before running local validate-intent.",
    )


def _validate_nodes(nodes: dict[str, Any], root_id: str | None, issues: list[AgentValidationIssue]) -> None:
    if root_id and root_id not in nodes:
        issues.append(
            _issue(
                "MISSING_SUBSTRATE_ROOT",
                "$.substrate.root_id",
                f"substrate.root_id '{root_id}' is not present in substrate.nodes.",
                "Add a substrate node with that key or change root_id.",
            )
        )
    for node_key, node in nodes.items():
        path = f"$.substrate.nodes.{node_key}"
        _validate_safe_id(node_key if isinstance(node_key, str) else None, path, "node key", issues)
        if not isinstance(node, dict):
            issues.append(_issue("INVALID_NODE", path, "Each substrate node must be an object."))
            continue
        _reject_unknown_fields(node, path, SUBSTRATE_NODE_ALLOWED_FIELDS, issues)
        node_id = _required_string(node, "id", path, issues)
        _validate_safe_id(node_id, f"{path}.id", "node id", issues)
        _required_string(node, "kind", path, issues)
        attrs = _required_object(node, "attrs", path, issues)
        slots = _required_object(node, "slots", path, issues)
        edges = _required_object(node, "edges", path, issues)
        if node_id and node_id != node_key:
            issues.append(
                _issue(
                    "NODE_KEY_MISMATCH",
                    f"{path}.id",
                    f"Node key '{node_key}' must match node.id '{node_id}'.",
                    "Use the node id as the substrate.nodes object key.",
                )
            )
        if attrs is not None and _check_count(
            attrs,
            MAX_AGENT_NODE_ATTRS,
            f"{path}.attrs",
            "TOO_MANY_NODE_ATTRS",
            "node attrs",
            issues,
        ):
            for attr_key in attrs:
                _validate_safe_id(attr_key if isinstance(attr_key, str) else None, f"{path}.attrs", "attribute key", issues)
        if slots is not None and _check_count(
            slots,
            MAX_AGENT_NODE_RELATIONS,
            f"{path}.slots",
            "TOO_MANY_NODE_SLOTS",
            "node slots",
            issues,
        ):
            _validate_values_map(slots, f"{path}.slots", "SLOT_VALUES_SHAPE", issues)
        if edges is not None and _check_count(
            edges,
            MAX_AGENT_NODE_RELATIONS,
            f"{path}.edges",
            "TOO_MANY_NODE_EDGES",
            "node edges",
            issues,
        ):
            _validate_values_map(
                edges,
                f"{path}.edges",
                "EDGE_VALUES_SHAPE",
                issues,
                edge_target_ids=set(nodes),
            )


def _validate_values_map(
    value_map: dict[str, Any] | None,
    path: str,
    code: str,
    issues: list[AgentValidationIssue],
    *,
    edge_target_ids: set[str] | None = None,
) -> None:
    if value_map is None:
        return
    for key, value in value_map.items():
        item_path = f"{path}.{key}"
        _validate_safe_id(key if isinstance(key, str) else None, item_path, "slot or edge key", issues)
        if not isinstance(value, dict) or not isinstance(value.get("values"), list):
            issues.append(
                _issue(
                    code,
                    item_path,
                    "Slot and edge values must use protobuf JSON shape {\"values\": [...]}.",
                    "Wrap the array in an object with a values key.",
                )
            )
            continue
        _reject_unknown_fields(value, item_path, {"values"}, issues)
        _check_count(
            value["values"],
            MAX_AGENT_RELATION_VALUES,
            f"{item_path}.values",
            "TOO_MANY_RELATION_VALUES",
            "slot or edge values",
            issues,
        )
        if edge_target_ids is not None:
            for index, target_id in enumerate(value["values"]):
                target_path = f"{item_path}.values[{index}]"
                if not _validate_safe_id(target_id if isinstance(target_id, str) else None, target_path, "edge target id", issues):
                    continue
                if target_id not in edge_target_ids:
                    issues.append(
                        _issue(
                            "UNKNOWN_EDGE_TARGET",
                            target_path,
                            f"Edge target {target_id} is not declared in substrate.nodes.",
                            "Use only declared substrate node ids in semantic edges.",
                        )
                    )


def _validate_regions(regions: list[Any], root_region: str | None, issues: list[AgentValidationIssue]) -> None:
    region_ids = {region["id"] for region in regions if isinstance(region, dict) and isinstance(region.get("id"), str) and region["id"]}
    parent_by_region: dict[str, str | None] = {}
    for index, region in enumerate(regions):
        path = f"$.view_spec.regions[{index}]"
        if not isinstance(region, dict):
            issues.append(_issue("INVALID_REGION", path, "Each region must be an object."))
            continue
        _reject_unknown_fields(region, path, REGION_ALLOWED_FIELDS, issues)
        region_id = region.get("id")
        parent = region.get("parent_region")
        _validate_safe_id(region_id if isinstance(region_id, str) else None, f"{path}.id", "region id", issues)
        if parent not in (None, ""):
            _validate_safe_id(parent if isinstance(parent, str) else None, f"{path}.parent_region", "parent region id", issues)
        if isinstance(region_id, str) and region_id:
            parent_by_region[region_id] = parent if isinstance(parent, str) and parent else None
        if parent not in (None, "") and parent not in region_ids:
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.parent_region",
                    f"Region {region.get('id')} declares unknown parent region {parent}.",
                    "Use a declared region id or null for the root region.",
                )
            )
        if isinstance(region_id, str) and region_id and parent == region_id:
            issues.append(
                _issue(
                    "REGION_PARENT_CYCLE",
                    f"{path}.parent_region",
                    f"Region {region_id} cannot parent itself.",
                    "Use an acyclic region tree rooted at view_spec.root_region.",
                )
            )
        _required_string(region, "role", path, issues)
        layout = _required_string(region, "layout", path, issues)
        if layout and layout not in SUPPORTED_AGENT_REGION_LAYOUTS:
            issues.append(
                _issue(
                    "UNSUPPORTED_REGION_LAYOUT",
                    f"{path}.layout",
                    f"Region {region.get('id')} uses unsupported layout {layout}.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_REGION_LAYOUTS)}.",
                )
            )
        min_children = _required_int(region, "min_children", path, issues)
        if min_children is not None and min_children < 0:
            issues.append(
                _issue(
                    "INVALID_REGION_CHILD_BOUNDS",
                    f"{path}.min_children",
                    f"Region {region.get('id')} min_children must be >= 0.",
                    "Use non-negative region child bounds.",
                )
            )
        max_children = _required_nullable_int(region, "max_children", path, issues)
        if min_children is not None and max_children is not None and max_children < min_children:
            issues.append(
                _issue(
                    "INVALID_REGION_CHILD_BOUNDS",
                    f"{path}.max_children",
                    f"Region {region.get('id')} max_children must be null or >= min_children.",
                    "Use null for an unbounded region or set max_children at least as high as min_children.",
                )
            )
    _validate_region_tree(parent_by_region, root_region, issues)


def _validate_region_tree(
    parent_by_region: dict[str, str | None],
    root_region: str | None,
    issues: list[AgentValidationIssue],
) -> None:
    if not root_region or root_region not in parent_by_region:
        return
    root_parent = parent_by_region[root_region]
    if root_parent is not None:
        issues.append(
            _issue(
                "ROOT_REGION_HAS_PARENT",
                "$.view_spec.regions",
                f"Root region {root_region} must not declare parent_region {root_parent}.",
                "Set the root region parent_region to an empty string or null.",
            )
        )

    for region_id, parent in parent_by_region.items():
        if region_id == root_region:
            continue
        if parent is None:
            issues.append(
                _issue(
                    "DETACHED_REGION",
                    "$.view_spec.regions",
                    f"Region {region_id} is not the root and has no parent_region.",
                    "Give every non-root region a parent chain that reaches view_spec.root_region.",
                )
            )
            continue
        seen: set[str] = set()
        cursor: str | None = region_id
        while cursor is not None and cursor != root_region:
            if cursor in seen:
                issues.append(
                    _issue(
                        "REGION_PARENT_CYCLE",
                        "$.view_spec.regions",
                        f"Region {region_id} is part of a parent_region cycle.",
                        "Use an acyclic region tree rooted at view_spec.root_region.",
                    )
                )
                break
            seen.add(cursor)
            cursor = parent_by_region.get(cursor)
        if cursor is None:
            issues.append(
                _issue(
                    "DETACHED_REGION",
                    "$.view_spec.regions",
                    f"Region {region_id} does not reach root region {root_region}.",
                    "Give every region a parent chain that reaches view_spec.root_region.",
                )
            )


def _validate_bindings(
    bindings: list[Any],
    binding_ids: set[str],
    region_ids: set[str],
    nodes: dict[str, Any] | None,
    issues: list[AgentValidationIssue],
) -> None:
    seen_exactly_once: dict[str, str] = {}
    for index, binding in enumerate(bindings):
        path = f"$.view_spec.bindings[{index}]"
        if not isinstance(binding, dict):
            issues.append(_issue("INVALID_BINDING", path, "Each binding must be an object."))
            continue
        _reject_unknown_fields(binding, path, BINDING_ALLOWED_FIELDS, issues)
        binding_id = _required_string(binding, "id", path, issues)
        address = _required_string(binding, "address", path, issues)
        target_region = _required_string(binding, "target_region", path, issues)
        present_as = _required_string(binding, "present_as", path, issues)
        cardinality = _required_string(binding, "cardinality", path, issues)
        _validate_safe_id(binding_id, f"{path}.id", "binding id", issues)
        _validate_safe_id(target_region, f"{path}.target_region", "target region id", issues)
        if target_region and target_region not in region_ids:
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.target_region",
                    f"Binding {binding_id} targets unknown region {target_region}.",
                    "Target a declared region id.",
                )
            )
        if present_as and present_as not in PRESENT_AS_TO_PRIMITIVE:
            issues.append(
                _issue(
                    "UNKNOWN_PRESENT_AS",
                    f"{path}.present_as",
                    f"Binding {binding_id} uses unknown present_as {present_as}.",
                    f"Use one of: {', '.join(sorted(PRESENT_AS_TO_PRIMITIVE))}.",
                )
            )
        if cardinality and cardinality not in SUPPORTED_AGENT_CARDINALITIES:
            issues.append(
                _issue(
                    "UNSUPPORTED_CARDINALITY",
                    f"{path}.cardinality",
                    f"Binding {binding_id} uses unsupported cardinality {cardinality}.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_CARDINALITIES)}.",
                )
            )
        if address and nodes is not None:
            _validate_address(address, nodes, f"{path}.address", binding_id or "binding", issues)
            if cardinality == "exactly_once":
                if address in seen_exactly_once:
                    issues.append(
                        _issue(
                            "DUPLICATE_EXACTLY_ONCE_ADDRESS",
                            f"{path}.address",
                            f"Binding {binding_id} duplicates exactly_once address {address} already used by {seen_exactly_once[address]}.",
                            "Use each source address once in the v1 agent contract.",
                        )
                    )
                else:
                    seen_exactly_once[address] = binding_id or address
    _ = binding_ids


def _validate_address(
    address: str,
    nodes: dict[str, Any],
    path: str,
    binding_id: str,
    issues: list[AgentValidationIssue],
) -> None:
    try:
        parts = parse_canonical_address(address)
    except ValueError as exc:
        issues.append(
            _issue(
                "INVALID_ADDRESS",
                path,
                f"Binding {binding_id} has invalid address {address}: {exc}",
                "Use node:id, node:id#attr:name, node:id#slot:name[index], or node:id#edge:name.",
            )
        )
        return

    node_id = parts["node_id"]
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        issues.append(
            _issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing node {node_id}.")
        )
        return
    attr = parts["attr"]
    slot = parts["slot"]
    edge = parts["edge"]
    if attr and attr not in (node.get("attrs") or {}):
        issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing attr {attr}."))
    if slot:
        slot_entry = (node.get("slots") or {}).get(slot)
        values = slot_entry.get("values") if isinstance(slot_entry, dict) else None
        if not isinstance(values, list):
            issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing slot {slot}."))
        elif parts["slot_index"] is not None and parts["slot_index"] >= len(values):
            issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing slot index {parts['slot_index']}."))
    if edge and edge not in (node.get("edges") or {}):
        issues.append(_issue("INVALID_ADDRESS", path, f"Binding {binding_id} references missing edge {edge}."))


def _validate_groups(
    groups: list[Any],
    binding_ids: set[str],
    region_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, group in enumerate(groups):
        path = f"$.view_spec.groups[{index}]"
        if not isinstance(group, dict):
            issues.append(_issue("INVALID_GROUP", path, "Each group must be an object."))
            continue
        _reject_unknown_fields(group, path, GROUP_ALLOWED_FIELDS, issues)
        group_id = _required_string(group, "id", path, issues)
        _validate_safe_id(group_id, f"{path}.id", "group id", issues)
        kind = _required_string(group, "kind", path, issues)
        if kind and kind not in SUPPORTED_AGENT_GROUP_KINDS:
            issues.append(
                _issue(
                    "UNSUPPORTED_GROUP_KIND",
                    f"{path}.kind",
                    f"Group {group.get('id')} uses unsupported kind {kind}.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_GROUP_KINDS)}.",
                )
            )
        members = _required_array(group, "members", path, issues)
        if "target_region" not in group:
            issues.append(_issue("MISSING_FIELD", f"{path}.target_region", "Missing required field target_region."))
        elif group["target_region"] not in (None, "") and group["target_region"] not in region_ids:
            _validate_safe_id(
                group["target_region"] if isinstance(group["target_region"], str) else None,
                f"{path}.target_region",
                "target region id",
                issues,
            )
            issues.append(
                _issue(
                    "UNKNOWN_REGION",
                    f"{path}.target_region",
                    f"Group {group.get('id')} targets unknown region {group['target_region']}.",
                    "Target a declared region id or use null.",
                )
            )
        if not _check_count(
            members,
            MAX_AGENT_BINDINGS,
            f"{path}.members",
            "TOO_MANY_GROUP_MEMBERS",
            "group members",
            issues,
        ):
            continue
        for member in members:
            _validate_safe_id(member if isinstance(member, str) else None, f"{path}.members", "group member id", issues)
            if member not in binding_ids:
                issues.append(
                    _issue(
                        "MISSING_GROUP_MEMBER",
                        f"{path}.members",
                        f"Group {group.get('id')} references missing binding {member}.",
                        "Use declared binding ids in group members.",
                    )
                )


def _validate_motifs(
    motifs: list[Any],
    binding_ids: set[str],
    binding_by_id: dict[str, dict[str, Any]],
    region_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    for index, motif in enumerate(motifs):
        path = f"$.view_spec.motifs[{index}]"
        if not isinstance(motif, dict):
            issues.append(_issue("INVALID_MOTIF", path, "Each motif must be an object."))
            continue
        _reject_unknown_fields(motif, path, MOTIF_ALLOWED_FIELDS, issues)
        motif_id = _required_string(motif, "id", path, issues)
        _validate_safe_id(motif_id, f"{path}.id", "motif id", issues)
        kind = _required_string(motif, "kind", path, issues)
        region = _required_string(motif, "region", path, issues)
        members = _required_array(motif, "members", path, issues)
        _validate_safe_id(region, f"{path}.region", "region id", issues)
        if kind and kind not in SUPPORTED_AGENT_MOTIFS:
            issues.append(
                _issue(
                    "UNSUPPORTED_MOTIF",
                    f"{path}.kind",
                    f"Motif kind '{kind}' is not supported by the v1 agent contract.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_MOTIFS)}.",
                )
            )
        if region and region not in region_ids:
            issues.append(_issue("UNKNOWN_REGION", f"{path}.region", f"Motif {motif.get('id')} targets unknown region {region}."))
        if not _check_count(
            members,
            MAX_AGENT_BINDINGS,
            f"{path}.members",
            "TOO_MANY_MOTIF_MEMBERS",
            "motif members",
            issues,
        ):
            continue
        missing_member = False
        for member in members:
            _validate_safe_id(member if isinstance(member, str) else None, f"{path}.members", "motif member id", issues)
            if member not in binding_ids:
                missing_member = True
                issues.append(
                    _issue(
                        "MISSING_MOTIF_MEMBER",
                        f"{path}.members",
                        f"Motif {motif.get('id')} references missing binding {member}.",
                        "Use declared binding ids in motif members.",
                    )
                )
        if kind in SUPPORTED_AGENT_MOTIFS and not missing_member:
            _validate_motif_completeness(kind, motif_id or str(motif.get("id") or index), members, binding_by_id, path, issues)


def _validate_motif_completeness(
    kind: str,
    motif_id: str,
    members: list[Any],
    binding_by_id: dict[str, dict[str, Any]],
    path: str,
    issues: list[AgentValidationIssue],
) -> None:
    member_bindings = [
        binding_by_id[member]
        for member in members
        if isinstance(member, str) and member in binding_by_id
    ]
    if not member_bindings:
        issues.append(
            _issue(
                "EMPTY_MOTIF",
                f"{path}.members",
                f"Motif {motif_id} has no declared binding members.",
                "Add at least one binding member that carries the motif's semantic content.",
            )
        )
        return

    if kind in {"hero", "empty_state"} and not _motif_has_title_binding(member_bindings):
        issues.append(
            _issue(
                "MOTIF_MISSING_TITLE",
                f"{path}.members",
                f"Motif {motif_id} needs a title, heading, headline, or label binding.",
                "Bind a node attr named title, heading, headline, or label and include that binding in motif.members.",
            )
        )

    if kind in {"loading_state", "error_state"}:
        title_count = _motif_title_binding_count(member_bindings)
        description_count = _motif_description_binding_count(member_bindings)
        if title_count != 1:
            issues.append(
                _issue(
                    "STATE_MOTIF_TITLE_REQUIRED",
                    f"{path}.members",
                    f"{kind} motif {motif_id} must include exactly one title, heading, headline, or label binding.",
                    "Use exactly one title-like binding in the state motif members.",
                )
            )
        if description_count > 1:
            issues.append(
                _issue(
                    "STATE_MOTIF_TOO_MANY_DESCRIPTIONS",
                    f"{path}.members",
                    f"{kind} motif {motif_id} may include at most one description, body, or message binding.",
                    "Keep at most one description-like binding in the state motif members.",
                )
            )

    if kind == "form" and not _motif_has_present_as(member_bindings, {"input"}):
        issues.append(
            _issue(
                "MOTIF_MISSING_INPUT",
                f"{path}.members",
                f"Form motif {motif_id} needs at least one input binding.",
                'Use present_as "input" for editable field values and include those bindings in motif.members.',
            )
        )

    if kind in {"table", "dashboard", "detail"}:
        if not _motif_has_present_as(member_bindings, {"label"}):
            issues.append(
                _issue(
                    "MOTIF_MISSING_LABEL",
                    f"{path}.members",
                    f"Motif {motif_id} needs at least one label binding.",
                    'Include a declared binding with present_as "label" in motif.members.',
                )
            )
        if not _motif_has_present_as(member_bindings, {"value", "badge", "text", "rich_text", "input"}):
            issues.append(
                _issue(
                    "MOTIF_MISSING_VALUE",
                    f"{path}.members",
                    f"Motif {motif_id} needs at least one value or text-style binding.",
                    'Include a declared binding with present_as "value", "badge", "text", "rich_text", or "input" in motif.members.',
                )
            )

    if kind == "comparison" and len(_motif_distinct_node_ids(member_bindings)) < 2:
        issues.append(
            _issue(
                "MOTIF_TOO_FEW_ITEMS",
                f"{path}.members",
                f"Comparison motif {motif_id} needs at least two distinct semantic items.",
                "Include bindings from at least two distinct substrate nodes in motif.members.",
            )
        )


def _motif_has_title_binding(bindings: list[dict[str, Any]]) -> bool:
    return any(_binding_address_part(binding) in {"title", "heading", "headline", "label"} for binding in bindings)


def _motif_title_binding_count(bindings: list[dict[str, Any]]) -> int:
    return sum(1 for binding in bindings if _binding_address_part(binding) in {"title", "heading", "headline", "label"})


def _motif_description_binding_count(bindings: list[dict[str, Any]]) -> int:
    return sum(1 for binding in bindings if _binding_address_part(binding) in {"description", "body", "message"})


def _validate_state_motif_conflicts(motifs: list[Any], issues: list[AgentValidationIssue]) -> None:
    state_motifs = [motif for motif in motifs if isinstance(motif, dict) and motif.get("kind") in STATE_MOTIF_KINDS]
    if len(state_motifs) > MAX_STATE_MOTIFS:
        issues.append(
            _issue(
                "TOO_MANY_STATE_MOTIFS",
                "$.view_spec.motifs",
                f"ViewSpec declares more than {MAX_STATE_MOTIFS} state motifs.",
                f"Keep state motifs at or below {MAX_STATE_MOTIFS}.",
            )
        )
    by_region: dict[str, list[dict[str, Any]]] = {}
    for motif in motifs:
        if not isinstance(motif, dict):
            continue
        region = motif.get("region")
        if isinstance(region, str):
            by_region.setdefault(region, []).append(motif)
    for region, region_motifs in by_region.items():
        has_collection = any(motif.get("kind") in COLLECTION_MOTIF_KINDS for motif in region_motifs)
        has_conflicting_state = any(motif.get("kind") in CONFLICTING_STATE_MOTIF_KINDS for motif in region_motifs)
        if has_collection and has_conflicting_state:
            issues.append(
                _issue(
                    "COLLECTION_STATE_CONFLICT",
                    "$.view_spec.motifs",
                    f"Region {region} mixes loading_state or error_state with a table/list collection.",
                    "Render either the loaded collection or the current state motif in this region, not both.",
                )
            )


def _motif_has_present_as(bindings: list[dict[str, Any]], present_as_values: set[str]) -> bool:
    return any(binding.get("present_as") in present_as_values for binding in bindings)


def _motif_distinct_node_ids(bindings: list[dict[str, Any]]) -> set[str]:
    node_ids: set[str] = set()
    for binding in bindings:
        address = binding.get("address")
        if not isinstance(address, str):
            continue
        try:
            parts = parse_canonical_address(address)
        except ValueError:
            continue
        node_id = parts.get("node_id")
        if isinstance(node_id, str):
            node_ids.add(node_id)
    return node_ids


def _binding_address_part(binding: dict[str, Any]) -> str:
    address = binding.get("address")
    if not isinstance(address, str):
        return ""
    try:
        parts = parse_canonical_address(address)
    except ValueError:
        return ""
    return str(parts.get("attr") or parts.get("slot") or "")


def _validate_styles(
    styles: list[Any],
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
    issues: list[AgentValidationIssue],
) -> None:
    aesthetic_styles: list[tuple[int, dict[str, Any]]] = []
    for index, style in enumerate(styles):
        path = f"$.view_spec.styles[{index}]"
        if not isinstance(style, dict):
            issues.append(_issue("INVALID_STYLE", path, "Each style must be an object."))
            continue
        _reject_unknown_fields(style, path, STYLE_ALLOWED_FIELDS, issues)
        style_id = _required_string(style, "id", path, issues)
        _validate_safe_id(style_id, f"{path}.id", "style id", issues)
        target = _required_string(style, "target", path, issues)
        token = _required_string(style, "token", path, issues)
        if token and is_aesthetic_profile_token(token) and token not in AESTHETIC_PROFILE_TOKENS:
            issues.append(
                _issue(
                    "AESTHETIC_PROFILE_UNKNOWN",
                    f"{path}.token",
                    f"Style {style.get('id')} uses unknown aesthetic profile {token}.",
                    f"Use one of: {', '.join(AESTHETIC_PROFILE_TOKENS)}.",
                )
            )
        elif token and token not in SUPPORTED_AGENT_STYLE_TOKENS:
            issues.append(
                _issue(
                    "UNSUPPORTED_STYLE_TOKEN",
                    f"{path}.token",
                    f"Style {style.get('id')} uses unsupported token {token}.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_STYLE_TOKENS)}.",
                )
            )
        if token and is_aesthetic_profile_token(token):
            aesthetic_styles.append((index, style))
            expected_target = f"view:{view_id}"
            if target != expected_target:
                issues.append(
                    _issue(
                        "AESTHETIC_PROFILE_TARGET_INVALID",
                        f"{path}.target",
                        f"Style {style.get('id')} must target exactly {expected_target}.",
                        "Use builder.set_aesthetic_profile(profile) or set target to the explicit view ref.",
                    )
                )
        _validate_target_ref(
            target,
            f"{path}.target",
            f"Style {style.get('id')}",
            view_id,
            region_ids,
            binding_ids,
            motif_ids,
            issues,
            unknown_code="UNKNOWN_STYLE_TARGET",
            allow_bare=True,
        )
    if len(aesthetic_styles) > 1:
        for index, style in aesthetic_styles[1:]:
            issues.append(
                _issue(
                    "AESTHETIC_PROFILE_MULTIPLE",
                    f"$.view_spec.styles[{index}].token",
                    "IntentBundle may declare at most one aesthetic.* style token.",
                    "Keep one view-level aesthetic profile and remove the rest.",
                )
            )


def _validate_actions(
    actions: list[Any],
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
    motif_kind_by_id: dict[str, str],
    issues: list[AgentValidationIssue],
) -> None:
    collection_action_counts: dict[str, int] = {}
    for index, action in enumerate(actions):
        path = f"$.view_spec.actions[{index}]"
        if not isinstance(action, dict):
            issues.append(_issue("INVALID_ACTION", path, "Each action must be an object."))
            continue
        _reject_unknown_fields(action, path, ACTION_ALLOWED_FIELDS, issues)
        action_id = _required_string(action, "id", path, issues)
        _validate_safe_id(action_id, f"{path}.id", "action id", issues)
        target_region = _required_string(action, "target_region", path, issues)
        kind = _required_string(action, "kind", path, issues)
        _required_string(action, "label", path, issues)
        target_ref = _required_nullable_string(action, "target_ref", path, issues)
        payload_bindings = _required_array(action, "payload_bindings", path, issues)
        _validate_safe_id(target_region, f"{path}.target_region", "target region id", issues)
        if kind and kind not in SUPPORTED_AGENT_ACTION_KINDS:
            issues.append(
                _issue(
                    "UNSUPPORTED_ACTION_KIND",
                    f"{path}.kind",
                    f"Action kind '{kind}' is not supported by the v1 agent contract.",
                    f"Use one of: {', '.join(SUPPORTED_AGENT_ACTION_KINDS)}.",
                )
            )
        if target_region and target_region not in region_ids:
            issues.append(
                _issue("UNKNOWN_ACTION_TARGET", f"{path}.target_region", f"Action {action.get('id')} targets unknown region {target_region}.")
            )
        if target_ref not in (None, ""):
            _validate_target_ref(
                target_ref,
                f"{path}.target_ref",
                f"Action {action.get('id')}",
                view_id,
                region_ids,
                binding_ids,
                motif_ids,
                issues,
                unknown_code="UNKNOWN_ACTION_TARGET",
                invalid_code="INVALID_ACTION_TARGET_REF",
                allow_bare=False,
            )
        if not _check_count(
            payload_bindings,
            MAX_AGENT_ACTION_PAYLOAD_BINDINGS,
            f"{path}.payload_bindings",
            "TOO_MANY_ACTION_PAYLOAD_BINDINGS",
            "action payload bindings",
            issues,
        ):
            continue
        for binding_id in payload_bindings:
            _validate_safe_id(binding_id if isinstance(binding_id, str) else None, f"{path}.payload_bindings", "payload binding id", issues)
            if binding_id not in binding_ids:
                issues.append(
                    _issue(
                        "UNKNOWN_ACTION_PAYLOAD_BINDING",
                        f"{path}.payload_bindings",
                        f"Action {action.get('id')} references missing payload binding {binding_id}.",
                    )
                )
        if kind in COLLECTION_ACTION_KINDS:
            collection_target = _collection_action_target_id(target_ref)
            if (
                collection_target is None
                or collection_target not in motif_ids
                or motif_kind_by_id.get(collection_target) not in COLLECTION_MOTIF_KINDS
            ):
                issues.append(
                    _issue(
                        "COLLECTION_ACTION_TARGET_INVALID",
                        f"{path}.target_ref",
                        f"Collection action {action.get('id')} must target a table or list motif by explicit motif:<id>.",
                        "Use target_ref motif:{collection_id} where the motif is a declared table or list.",
                    )
                )
            else:
                collection_action_counts[collection_target] = collection_action_counts.get(collection_target, 0) + 1
            if kind == "bulk_action":
                selection_bindings = [
                    binding_id
                    for binding_id in payload_bindings
                    if isinstance(binding_id, str)
                    and (binding_id.endswith("_selection") or binding_id.endswith("_selected_ids"))
                ]
                if not selection_bindings:
                    issues.append(
                        _issue(
                            "COLLECTION_BULK_SELECTION_REQUIRED",
                            f"{path}.payload_bindings",
                            f"Bulk action {action.get('id')} must declare exactly one _selection or _selected_ids payload binding.",
                            "Add exactly one declared selection payload binding.",
                        )
                    )
                elif len(selection_bindings) != 1 or len(payload_bindings) != 1:
                    issues.append(
                        _issue(
                            "COLLECTION_BULK_SELECTION_AMBIGUOUS",
                            f"{path}.payload_bindings",
                            f"Bulk action {action.get('id')} must not mix selection payload bindings with other payload bindings.",
                            "Keep exactly one payload binding ending with _selection or _selected_ids.",
                        )
                    )
            elif not (1 <= len(payload_bindings) <= MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS):
                issues.append(
                    _issue(
                        "COLLECTION_ACTION_PAYLOAD_REQUIRED",
                        f"{path}.payload_bindings",
                        f"Collection action {action.get('id')} must declare 1-{MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS} payload bindings.",
                        "Add declared payload bindings for host-owned collection operation parameters.",
                    )
                )
    for motif_id, count in sorted(collection_action_counts.items()):
        if count > MAX_COLLECTION_ACTIONS_PER_COLLECTION:
            issues.append(
                _issue(
                    "TOO_MANY_COLLECTION_ACTIONS",
                    "$.view_spec.actions",
                    f"Collection motif {motif_id} has {count} collection actions; the limit is {MAX_COLLECTION_ACTIONS_PER_COLLECTION}.",
                    f"Keep at most {MAX_COLLECTION_ACTIONS_PER_COLLECTION} collection actions per table/list motif.",
                )
            )


def _collection_action_target_id(target_ref: str | None) -> str | None:
    if not isinstance(target_ref, str) or ":" not in target_ref:
        return None
    target_kind, target_id = target_ref.split(":", 1)
    if target_kind != "motif":
        return None
    return target_id


def _target_matches(
    target: str,
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
) -> list[str]:
    if ":" in target:
        kind, target_id = target.split(":", 1)
        return [kind] if _target_kind_exists(kind, target_id, view_id, region_ids, binding_ids, motif_ids) else []
    matches: list[str] = []
    if target in region_ids:
        matches.append("region")
    if target in binding_ids:
        matches.append("binding")
    if target in motif_ids:
        matches.append("motif")
    if target == view_id:
        matches.append("view")
    return matches


def _target_kind_exists(
    kind: str,
    target_id: str,
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
) -> bool:
    return (
        (kind == "region" and target_id in region_ids)
        or (kind == "binding" and target_id in binding_ids)
        or (kind == "motif" and target_id in motif_ids)
        or (kind == "view" and target_id == view_id)
    )


def _validate_target_ref(
    target: str | None,
    path: str,
    owner: str,
    view_id: str | None,
    region_ids: set[str],
    binding_ids: set[str],
    motif_ids: set[str],
    issues: list[AgentValidationIssue],
    *,
    unknown_code: str,
    invalid_code: str | None = None,
    allow_bare: bool,
) -> None:
    if not target:
        return
    if ":" in target:
        kind, target_id = target.split(":", 1)
        if kind not in {"region", "binding", "motif", "view"} or not SAFE_AGENT_ID_RE.match(target_id):
            issues.append(
                _issue(
                    invalid_code or unknown_code,
                    path,
                    f"{owner} target {target} must use region:id, binding:id, motif:id, or view:id.",
                    "Use an explicit target reference with a supported kind and safe id.",
                )
            )
            return
        if not _target_kind_exists(kind, target_id, view_id, region_ids, binding_ids, motif_ids):
            issues.append(
                _issue(
                    unknown_code,
                    path,
                    f"{owner} targets unknown {target}.",
                    "Use a declared target ref like binding:save_button or motif:items.",
                )
            )
        return

    if not allow_bare:
        issues.append(
            _issue(
                invalid_code or unknown_code,
                path,
                f"{owner} target {target} must use kind:id form.",
                "Use region:id, binding:id, motif:id, or view:id.",
            )
        )
        return

    matches = _target_matches(target, view_id, region_ids, binding_ids, motif_ids)
    if not matches:
        issues.append(
            _issue(
                unknown_code,
                path,
                f"{owner} targets unknown {target}.",
                "Use region:id, binding:id, motif:id, view:id, or a declared unambiguous bare id.",
            )
        )
    elif len(matches) > 1:
        issues.append(
            _issue(
                "AMBIGUOUS_STYLE_TARGET",
                path,
                f"{owner} target {target} matches multiple namespaces: {', '.join(matches)}.",
                f"Use an explicit target reference such as binding:{target} or region:{target}.",
            )
        )


def _validate_safe_id(
    value: str | None,
    path: str,
    label: str,
    issues: list[AgentValidationIssue],
) -> bool:
    if not value:
        return False
    if SAFE_AGENT_ID_RE.match(value):
        return True
    issues.append(
        _issue(
            "INVALID_ID",
            path,
            f"{label} '{value}' must match {SAFE_AGENT_ID_PATTERN}.",
            "Use only letters, digits, underscore, dot, and dash. Do not use spaces, colons, slashes, markup, or path-like ids.",
        )
    )
    return False


def _collect_ids(
    values: list[Any],
    path: str,
    duplicate_code: str,
    issues: list[AgentValidationIssue],
) -> set[str]:
    ids: set[str] = set()
    for index, value in enumerate(values):
        item_path = f"{path}[{index}]"
        if not isinstance(value, dict):
            continue
        item_id = _required_string(value, "id", item_path, issues)
        if item_id:
            if item_id in ids:
                issues.append(_issue(duplicate_code, f"{item_path}.id", f"Duplicate id {item_id}."))
            ids.add(item_id)
    return ids


def _index_bindings_by_id(bindings: list[Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        binding_id = binding.get("id")
        if isinstance(binding_id, str) and binding_id not in indexed:
            indexed[binding_id] = binding
    return indexed


def _index_motif_kinds_by_id(motifs: list[Any]) -> dict[str, str]:
    indexed: dict[str, str] = {}
    for motif in motifs:
        if not isinstance(motif, dict):
            continue
        motif_id = motif.get("id")
        kind = motif.get("kind")
        if isinstance(motif_id, str) and isinstance(kind, str) and motif_id not in indexed:
            indexed[motif_id] = kind
    return indexed


def _check_count(
    value: dict[Any, Any] | list[Any],
    limit: int,
    path: str,
    code: str,
    label: str,
    issues: list[AgentValidationIssue],
) -> bool:
    count = len(value)
    if count <= limit:
        return True
    issues.append(
        _issue(
            code,
            path,
            f"IntentBundle declares {count} {label}; the v1 local limit is {limit}.",
            "Split the UI into smaller IntentBundles.",
        )
    )
    return False


def _required_object(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> dict[str, Any] | None:
    value = obj.get(key)
    if not isinstance(value, dict):
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required object field {key}."))
        return None
    return value


def _required_array(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required array field {key}."))
        return []
    return value


def _required_string(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or value == "":
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required string field {key}."))
        return None
    return value


def _required_nullable_string(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> str | None:
    if key not in obj:
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required string-or-null field {key}."))
        return None
    value = obj.get(key)
    if value is None or isinstance(value, str):
        return value
    issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Field {key} must be a string or null."))
    return None


def _required_nullable_int(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> int | None:
    if key not in obj:
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required integer-or-null field {key}."))
        return None
    value = obj.get(key)
    if value is None:
        return None
    if type(value) is int:
        return value
    issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Field {key} must be an integer or null."))
    return None


def _required_int(
    obj: dict[str, Any],
    key: str,
    path: str,
    issues: list[AgentValidationIssue],
) -> int | None:
    value = obj.get(key)
    if type(value) is not int:
        issues.append(_issue("MISSING_FIELD", f"{path}.{key}", f"Missing required integer field {key}."))
        return None
    return value


def _diagnostic_path(diagnostic: Any) -> str:
    if diagnostic.intent_ref:
        return f"intent_ref:{diagnostic.intent_ref}"
    if diagnostic.content_ref:
        return f"content_ref:{diagnostic.content_ref}"
    if diagnostic.region_id:
        return f"region:{diagnostic.region_id}"
    if diagnostic.node_id:
        return f"node:{diagnostic.node_id}"
    return "$"


def _issue(
    code: str,
    path: str,
    message: str,
    suggestion: str | None = None,
) -> AgentValidationIssue:
    return AgentValidationIssue("error", code, path, message, suggestion)
