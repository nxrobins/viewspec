"""The agent system prompt, generated from the contract registries.

``build_agent_system_prompt()`` joins ordered sections, and ``AGENT_SYSTEM_PROMPT`` is its result. Every
list the prompt states (motif kinds, present_as values, action kinds, style tokens, aesthetic profiles,
caps) and every JSON example in it comes from code, so the prompt never names a value the validator
rejects. ``viewspec.agent.AGENT_SYSTEM_PROMPT`` is the same string.

The sections, in order: the role and output rule, the common mistakes, the authoring format with a
worked compact example, the compact form, one compact example per motif kind, the canonical contract,
the repair loop, proof and handoff, the AppBundle sections, and optional reference grounding.
Everything before the AppBundle sections fits PROMPT_CONTRACT_BUDGET_BYTES, and the AppBundle sections
fit PROMPT_APP_BUDGET_BYTES. Tests pin both budgets and run ``viewspec check`` on every example.

This module imports the compact form and the AppBundle contract, and both import ``viewspec.agent``,
so ``viewspec.agent`` resolves its ``AGENT_SYSTEM_PROMPT`` from here on first access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from viewspec import app_agent_schema
from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import (
    CANONICAL_SOURCE_FORMAT,
    COMPACT_SOURCE_FORMAT,
    DEFAULT_AGENT_SOURCE_FILES,
    MAX_AGENT_INTENT_BYTES,
    REPAIR_LOOP_PROMPT_SECTION,
    SUPPORTED_AGENT_ACTION_KINDS,
    SUPPORTED_AGENT_CARDINALITIES,
    SUPPORTED_AGENT_GROUP_KINDS,
    SUPPORTED_AGENT_MOTIFS,
    SUPPORTED_AGENT_REGION_LAYOUTS,
    SUPPORTED_AGENT_STYLE_TOKENS,
)
from viewspec.app_validation import (
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_EXPRESSIONS_SCHEMA_VERSION,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
    APP_BUNDLE_VISIBILITY_SCHEMA_VERSION,
)
from viewspec.compact_form import (
    COMPACT_ACTION_KINDS,
    COMPACT_FORM_PROMPT_SECTION,
    COMPACT_FORMAT,
    COMPACT_MOTIF_EXAMPLES,
)
from viewspec.compiler import COLLECTION_ACTION_KINDS, MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS
from viewspec.state_ir import APP_STATE_MAX_EVENTS_PER_REPLAY, APP_STATE_TEXT_MAX_RULES, APP_VISIBILITY_MAX_RULES
from viewspec.types import INTENT_BUNDLE_SCHEMA_VERSION, PRESENT_AS_TO_PRIMITIVE

# The byte budgets that tests pin. The contract part is every section before the AppBundle sections,
# joined as the prompt joins them; the AppBundle part is the AppBundle sections alone.
PROMPT_CONTRACT_BUDGET_BYTES = 6000
PROMPT_APP_BUDGET_BYTES = 6000
SECTION_SEPARATOR = "\n\n"
# The worked example sits alone on the line between these markers, so a test can lift it out.
WORKED_EXAMPLE_START = "<example>"
WORKED_EXAMPLE_END = "</example>"
# The file names the prompt and the correction prompt use for each form.
COMPACT_SOURCE_FILE = DEFAULT_AGENT_SOURCE_FILES[COMPACT_SOURCE_FORMAT]
CANONICAL_SOURCE_FILE = DEFAULT_AGENT_SOURCE_FILES[CANONICAL_SOURCE_FORMAT]
# The action kinds that only the canonical form can express.
CANONICAL_ONLY_ACTION_KINDS: tuple[str, ...] = tuple(
    kind for kind in SUPPORTED_AGENT_ACTION_KINDS if kind not in COMPACT_ACTION_KINDS
)
_TABLE_EXAMPLE = COMPACT_MOTIF_EXAMPLES["table"]
# A complete compact document built from the motif examples: a dashboard, a table whose rows carry an
# extra attribute under values, and a search action over the table that reads an input.
WORKED_EXAMPLE: dict[str, Any] = {
    "format": COMPACT_FORMAT,
    "id": "desk",
    "motifs": [
        COMPACT_MOTIF_EXAMPLES["dashboard"],
        {**_TABLE_EXAMPLE, "rows": [{**row, "values": {"paid": "yes"}} for row in _TABLE_EXAMPLE["rows"]]},
    ],
    "inputs": [{"id": "query", "label": "Search"}],
    "actions": [
        {"id": "search", "kind": "search", "label": "Search", "collection": _TABLE_EXAMPLE["id"], "payload": ["query"]}
    ],
}
# COMPACT_FORM_PROMPT_SECTION ends with this lead and one line of example JSON. The worked example
# above the section replaces that line, so the prompt carries one compact document, not two.
_COMPACT_EXAMPLE_LEAD = " Example:"


@dataclass(frozen=True)
class CommonMistake:
    """One rule of the common-mistakes section and the issue codes it prevents."""

    rule: str
    codes: tuple[str, ...]


@dataclass(frozen=True)
class PromptSection:
    """One named section of the system prompt."""

    name: str
    text: str


# The common mistakes, most frequent first. The order is a seed: the compact mistakes lead because the
# compact form is the default, and the lossy canonical codes follow. Refresh the order from the top
# issue codes of an eval report (scripts/eval_agent_contract.py writes eval_report.json) once a
# baseline run exists, and keep 6 to 10 rules.
COMMON_MISTAKES: tuple[CommonMistake, ...] = (
    CommonMistake("Use each motif kind's item key.", ("COMPACT_MOTIF_ITEMS_MISSING", "COMPACT_UNKNOWN_FIELD")),
    CommonMistake("Target declared ids in actions.", ("COMPACT_ACTION_TARGET_UNKNOWN",)),
    CommonMistake(
        "Unknown fields are rejected instead of silently ignored.", ("UNKNOWN_FIELD", "COMPACT_UNKNOWN_FIELD")
    ),
    CommonMistake("Write substrate.nodes as an object keyed by node ID, never an array.", ("NODES_MUST_BE_OBJECT",)),
    CommonMistake("Make motif members name declared bindings.", ("MISSING_MOTIF_MEMBER",)),
    CommonMistake("Give exactly_once bindings unique addresses.", ("DUPLICATE_EXACTLY_ONCE_ADDRESS",)),
    CommonMistake(
        "Action target_ref must be empty/null or use region:id, binding:id, motif:id, or view:id.",
        ("INVALID_ACTION_TARGET_REF",),
    ),
    CommonMistake(
        "Region parent links must form one acyclic tree.",
        ("DETACHED_REGION", "REGION_PARENT_CYCLE", "ROOT_REGION_HAS_PARENT"),
    ),
)


def json_line(value: Any) -> str:
    """One line of compact JSON, as every example in the prompt is printed."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def _series(values: tuple[str, ...], conjunction: str = "and") -> str:
    """``a, b, and c``."""
    if len(values) <= 2:
        return f" {conjunction} ".join(values)
    return f"{', '.join(values[:-1])}, {conjunction} {values[-1]}"


def style_token_families(tokens: tuple[str, ...]) -> str:
    """The tokens grouped by family in brace form, in first-seen order: ``align.{start,center,end}``."""
    families: dict[str, list[str]] = {}
    for token in tokens:
        family, _, name = token.partition(".")
        families.setdefault(family, []).append(name)
    return ", ".join(
        f"{family}.{names[0]}" if len(names) == 1 else f"{family}.{{{','.join(names)}}}"
        for family, names in families.items()
    )


ROLE_SECTION = (
    "You are a ViewSpec IntentBundle compiler. Output strict JSON only: no markdown, prose, HTML, CSS, or "
    "React. CompositionIR is compiler output only."
)


def _common_mistakes_section() -> str:
    rules = "\n".join(f"{number}. {mistake.rule}" for number, mistake in enumerate(COMMON_MISTAKES, start=1))
    return f"Common mistakes:\n{rules}"


def _authoring_format_section() -> str:
    return (
        f"Write the compact form by default, as {COMPACT_SOURCE_FILE}. Write the canonical form only for "
        f"{_series(CANONICAL_ONLY_ACTION_KINDS)}, as {CANONICAL_SOURCE_FILE}, and in AppBundle screens.\n"
        f"{WORKED_EXAMPLE_START}\n{json_line(WORKED_EXAMPLE)}\n{WORKED_EXAMPLE_END}"
    )


def compact_form_reference() -> str:
    """COMPACT_FORM_PROMPT_SECTION as the prompt carries it: without its closing example line."""
    text = COMPACT_FORM_PROMPT_SECTION.strip()
    body, _, last_line = text.rpartition("\n")
    if body.endswith(_COMPACT_EXAMPLE_LEAD) and last_line.startswith("{"):
        return body[: -len(_COMPACT_EXAMPLE_LEAD)]
    return text


def _motif_examples_section() -> str:
    lines = "\n".join(json_line(COMPACT_MOTIF_EXAMPLES[kind]) for kind in SUPPORTED_AGENT_MOTIFS)
    return f"Use only these {len(SUPPORTED_AGENT_MOTIFS)} motif kinds:\n{lines}"


def _canonical_contract_section() -> str:
    style_tokens = tuple(token for token in SUPPORTED_AGENT_STYLE_TOKENS if token not in AESTHETIC_PROFILE_TOKENS)
    payload_kinds = tuple(
        kind
        for kind in SUPPORTED_AGENT_ACTION_KINDS
        if kind in COLLECTION_ACTION_KINDS and kind not in CANONICAL_ONLY_ACTION_KINDS
    )
    return "\n".join(
        (
            "Canonical contract. Use stable ids and object keys matching this pattern only: [A-Za-z0-9_.-]+. "
            f"The root has schema_version {INTENT_BUNDLE_SCHEMA_VERSION}, substrate {{id, root_id, nodes}}, and "
            "view_spec {id, substrate_id, complexity_tier, root_region, regions, bindings, groups, motifs, styles, "
            "actions}. A node has id, kind, attrs, slots, and edges. Semantic edges must reference declared "
            "substrate node IDs. Bind an attribute as node:{id}#attr:{name}.",
            f"Use only: present_as {', '.join(PRESENT_AS_TO_PRIMITIVE)}; "
            f"action kinds {', '.join(SUPPORTED_AGENT_ACTION_KINDS)}; "
            f"cardinality {', '.join(SUPPORTED_AGENT_CARDINALITIES)}; "
            f"region layouts {', '.join(SUPPORTED_AGENT_REGION_LAYOUTS)}; "
            f"group kind {', '.join(SUPPORTED_AGENT_GROUP_KINDS)}; "
            f"style tokens {style_token_families(style_tokens)}.",
            "Aesthetic profile tokens are deterministic art-direction handles, not CSS. Use at most one, on "
            f"view:{{view_spec.id}}: {_series(AESTHETIC_PROFILE_TOKENS, 'or')}.",
            "Motifs must be semantically complete, as the motif examples are; form fields also bind an input. "
            "Loading_state and error_state motifs need exactly one title binding and at most one description.",
            "Stateful collection actions are bounded. They target a table or list motif. "
            f"{_series(payload_kinds).capitalize()} take 1-{MAX_COLLECTION_ACTION_PAYLOAD_BINDINGS} "
            f"payload_bindings; {_series(CANONICAL_ONLY_ACTION_KINDS)} takes one ending in _selection. "
            "Region min_children must be >= 0. "
            f"Stay inside the v1 local contract caps: {MAX_AGENT_INTENT_BYTES // 1024}KB of JSON.",
        )
    )


PROOF_AND_HANDOFF_SECTION = (
    f"Generated JSON is not a finished ViewSpec proof. Run viewspec check {COMPACT_SOURCE_FILE} --json, then "
    f"viewspec prove {COMPACT_SOURCE_FILE} --out .viewspec-proof. Read .viewspec-proof/PROOF.md, "
    "proof_report.json, and support_bundle.json. Proof is not pixel-perfect visual regression. To revise source, "
    "regenerate the bundle or apply a standard JSON Patch; never edit generated output."
)

_EMBEDDED_INTENT_SCHEMA = app_agent_schema.AGENT_APP_BUNDLE_SCHEMA["x-viewspec-embedded-intent-schema"]
_V4 = APP_BUNDLE_VISIBILITY_SCHEMA_VERSION
# The AppBundle contract through schema_version 4. A limit that a later version lifts is stated as a
# fact of schema_version 4, so the text stays true beside the later version's section.
APPBUNDLE_V4_PROMPT_SECTION = "\n".join(
    (
        "AppBundle. For a multi-screen internal tool, emit AppBundle JSON as viewspec.app.json instead of a "
        "router or app scaffold. Each screen holds a canonical IntentBundle "
        f"({_EMBEDDED_INTENT_SCHEMA.rsplit('/', 1)[-1]}). schema_version {APP_BUNDLE_SCHEMA_VERSION} has "
        f"unbound fixture context. schema_version {APP_BUNDLE_BOUND_SCHEMA_VERSION} adds resource_binding "
        f'"{APP_BUNDLE_RESOURCE_BINDING_READONLY}" and per-screen resource_views. '
        f"schema_version {APP_BUNDLE_STATE_SCHEMA_VERSION} adds bounded interactive_state_v0 reducers. "
        f"schema_version {_V4} adds visibility, scalar state text, and the runnable React app path. "
        f"schema_version {APP_BUNDLE_EXPRESSIONS_SCHEMA_VERSION} adds condition composition, multi-placeholder "
        "text, and formatters. Start with "
        "viewspec init app, or viewspec init app --resource-binding fixture-readonly-v0 --out viewspec.app.json. "
        "Validate with viewspec check viewspec.app.json --json. Compile the Static Shell V0 proof with viewspec "
        "compile viewspec.app.json --out app-dist --target html-tailwind-app --json. Prove it with viewspec "
        "prove viewspec.app.json --out .viewspec-app-proof --with-shell --json. For a runnable React tool, run "
        "viewspec init app --template react-app --out viewspec.app.json, replace the sample intent, and run "
        "viewspec compile viewspec.app.json --target react-tailwind-app --out app-dist. Edit viewspec.app.json "
        "and recompile with --force; never edit generated React. Before handoff, run viewspec prove "
        "viewspec.app.json --target react-tailwind-app --install when npm install and local Chromium are "
        "allowed. Authentication, persistence, API clients, optimistic updates, backend behavior, and "
        "deployment stay host-owned.",
        "Declare screen.presentation for a reference-sensitive or composition-sensitive screen. Pick a bounded "
        "profile and target stable region:, motif:, or binding: IDs. Put shared properties in base, and "
        "breakpoint areas, columns, order, spans, spacing, sizing, visibility, and wrapping in compact, medium, "
        "or wide variants. A motif rule styles its container; items.base and items.variants lay out each "
        "direct record or group. A grid's areas matrix names child regions or the areas child rules claim. In "
        "an operations_workspace, keep sidebar and main visible, stack them at compact and medium, and use a "
        "rail_lg/fluid wide shell. Use a hero motif for the page eyebrow and "
        "title. Declare an anchor only at viewports where its relation holds; a sidebar/main same_row anchor is "
        "usually wide-only. Inferred presentation emits APP_PRESENTATION_INFERRED and has no anchor proof. "
        "React proof checks anchors at 390, 768, and 1440 pixels.",
        "For a list resource_view, use repeat to render the same fields for every declared record. repeat owns "
        "every repeated record-field in target_motif_id, so drop any hand-authored prototype record and its "
        "bindings. repeat.field_presentations covers resource_view.fields once each with badge, label, text, or "
        "value; an optional repeat.group_id joins an ordered group. ViewSpec generates stable rvn_ nodes and "
        "rvb_ bindings named rvb_{resource_view_id}__{record_id}__{field}, and proves values by record "
        "identity, not text.",
        "A replay event names mutation_id, or action_id with optional screen_id and repeat "
        f"(1-{APP_STATE_MAX_EVENTS_PER_REPLAY}). A failed replay names the assertion, event, mutation, state "
        "path, and expected and actual values; repair that declaration.",
        f"schema_version {_V4} adds a root visibility array of at most {APP_VISIBILITY_MAX_RULES} rules {{id, "
        "screen_id, target_ref, when}. target_ref is a region:, motif:, or binding: id in that screen. A leaf "
        'condition is one of {"state": id, "is": "truthy"|"falsy"}, {"state": id, "equals": <JSON scalar>}, or '
        f'{{"selector": id, "is": "non_empty"|"empty"}}. In schema_version {_V4}, when is one leaf condition, '
        "with no composition. Declare at most one rule per (screen_id, target_ref). The generated reducer exports "
        "evaluateViewSpecVisibility(state), and compiled screens bake the initial state into "
        "data-visibility-rule markers. Add expect_visibility to state_replay_assertions for every rule. "
        "Visibility has no animation or focus management.",
        f"For scalar state shown as text, add a root state_text array of at most {APP_STATE_TEXT_MAX_RULES} "
        "rules. A single-value rule has id, screen_id, target_ref, state, and template: target_ref is one "
        "exactly-once text-like binding, state is scalar, and template holds exactly one {value} placeholder. "
        f"In schema_version {_V4}, every rule is single-value, with no formatter. State text has no "
        "expressions, paths, HTML, or localization. Add expect_text to the replay assertion. The reducer "
        "exports evaluateViewSpecText(state) for both targets.",
        "The default html-tailwind-app Static Shell V0 is a local proof artifact, not a deployable app. It "
        "performs bounded visibility and scalar state-text updates but does not prove live resource "
        "rebinding. The react-tailwind-app target emits a Vite/React/Tailwind package whose Playwright proof "
        "builds it and exercises routes, mutations, live resource rebinding, selectors, visibility, and state "
        "text in Chromium.",
    )
)
# The AppBundle part of the prompt: one section per contract generation, in order.
APP_PROMPT_SECTIONS: tuple[str, ...] = (APPBUNDLE_V4_PROMPT_SECTION, app_agent_schema.APPBUNDLE_V5_PROMPT_SECTION)

REFERENCE_GROUNDING_SECTION = (
    "Optional reference grounding. Do not call remote reference libraries by default. Use one only when the "
    "user asks for research or the repository configures an approved source. Use it for semantic intent "
    "only: sections, hierarchy, typical fields, and motif choices. Never copy pixel layouts, hardcoded copy, "
    "design tokens, screenshot URLs, image bytes, or external references into the output. Proceed without "
    "it when it is unavailable."
)


def agent_prompt_sections() -> tuple[PromptSection, ...]:
    """The prompt's sections in order; the prompt joins their text with blank lines."""
    return (
        PromptSection("role", ROLE_SECTION),
        PromptSection("common_mistakes", _common_mistakes_section()),
        PromptSection("authoring_format", _authoring_format_section()),
        PromptSection("compact_form", compact_form_reference()),
        PromptSection("motif_examples", _motif_examples_section()),
        PromptSection("canonical_contract", _canonical_contract_section()),
        PromptSection("repair_loop", REPAIR_LOOP_PROMPT_SECTION.strip()),
        PromptSection("proof_and_handoff", PROOF_AND_HANDOFF_SECTION),
        *(PromptSection("app_bundle", section.strip()) for section in APP_PROMPT_SECTIONS),
        PromptSection("reference_grounding", REFERENCE_GROUNDING_SECTION),
    )


def _joined_size(sections: tuple[PromptSection, ...]) -> int:
    return len(SECTION_SEPARATOR.join(section.text for section in sections).encode("utf-8"))


def prompt_part_sizes() -> dict[str, int]:
    """UTF-8 byte sizes: the contract part, the AppBundle part, the part after it, and the whole prompt."""
    sections = agent_prompt_sections()
    app = [index for index, section in enumerate(sections) if section.name == "app_bundle"]
    return {
        "contract": _joined_size(sections[: app[0]]),
        "app_bundle": _joined_size(sections[app[0] : app[-1] + 1]),
        "after_app_bundle": _joined_size(sections[app[-1] + 1 :]),
        "total": len(build_agent_system_prompt().encode("utf-8")),
    }


def build_agent_system_prompt() -> str:
    """Join the sections into the system prompt. The result is a pure function of the code."""
    return SECTION_SEPARATOR.join(section.text for section in agent_prompt_sections()) + "\n"


AGENT_SYSTEM_PROMPT = build_agent_system_prompt()

__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "APPBUNDLE_V4_PROMPT_SECTION",
    "APP_PROMPT_SECTIONS",
    "CANONICAL_ONLY_ACTION_KINDS",
    "COMMON_MISTAKES",
    "PROMPT_APP_BUDGET_BYTES",
    "PROMPT_CONTRACT_BUDGET_BYTES",
    "WORKED_EXAMPLE",
    "WORKED_EXAMPLE_END",
    "WORKED_EXAMPLE_START",
    "CommonMistake",
    "PromptSection",
    "agent_prompt_sections",
    "build_agent_system_prompt",
    "compact_form_reference",
    "json_line",
    "prompt_part_sizes",
    "style_token_families",
]
