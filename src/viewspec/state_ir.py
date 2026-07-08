from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


INTERACTIVE_STATE_PROFILE = "interactive_state_v0"
STATE_KIND_COLLECTION = "collection"
STATE_KIND_RECORD = "record"
STATE_KIND_SCALAR = "scalar"
STATE_KIND_SELECTION = "selection"
STATE_KINDS = frozenset({STATE_KIND_COLLECTION, STATE_KIND_RECORD, STATE_KIND_SCALAR, STATE_KIND_SELECTION})
STATE_SCOPES = frozenset({"app", "screen"})
MUTATION_OPS = frozenset({"set", "patch", "toggle", "append", "remove", "move", "increment"})
SELECTOR_OPS = frozenset({"filter_eq", "sort_by", "slice"})
APP_STATE_MAX_ENTRIES = 32
APP_STATE_MAX_MUTATIONS = 128
APP_STATE_MAX_OPS_PER_MUTATION = 16
APP_STATE_MAX_SELECTORS = 64
APP_STATE_MAX_SELECTOR_OPS = 8
APP_STATE_MAX_REPLAY_ASSERTIONS = 32
APP_STATE_MAX_EVENTS_PER_REPLAY = 32
APP_STATE_MAX_REDUCER_BYTES = 64 * 1024
APP_STATE_MAX_MANIFEST_BYTES = 64 * 1024
STATE_MANIFEST_SCHEMA_VERSION = 2
STATE_REDUCER_EXPORTS = (
    "VIEWSPEC_STATE_PROFILE",
    "initialState",
    "reduceViewSpecState",
    "selectViewSpecState",
)
SAFE_STATE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# Visibility V0 (AppBundle schema_version 4): bounded per-screen show/hide conditions over the
# declared state/selector vocabulary. `view:` targets are deliberately excluded — whole-screen
# visibility is the router's job.
APP_VISIBILITY_MAX_RULES = 64
VISIBILITY_TARGET_REF_RE = re.compile(r"^(region|binding|motif):[A-Za-z0-9_.-]+$")
STATE_REDUCER_VISIBILITY_EXPORT = "evaluateViewSpecVisibility"


@dataclass(frozen=True)
class StateValidationIssue:
    code: str
    path: str
    message: str
    suggestion: str = "Regenerate AppBundle V3 interactive_state_v0 using the bounded state contract."

    def to_json(self) -> dict[str, str]:
        return {
            "severity": "error",
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class StateEntry:
    id: str
    kind: str
    scope: str
    initial: dict[str, Any]
    screen_id: str | None = None


@dataclass(frozen=True)
class StateMutation:
    id: str
    trigger: dict[str, str]
    ops: tuple[dict[str, Any], ...]
    allowed_payload_bindings: frozenset[str] = frozenset()
    required_payload_bindings: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StateSelector:
    id: str
    source_state: str
    ops: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StateReplayAssertion:
    id: str
    events: tuple[dict[str, Any], ...]
    expect_state: dict[str, Any]
    expect_selectors: dict[str, Any]
    expect_visibility: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisibilityRule:
    id: str
    screen_id: str
    target_ref: str
    when: dict[str, Any]


@dataclass(frozen=True)
class StateIR:
    profile: str
    states: tuple[StateEntry, ...]
    mutations: tuple[StateMutation, ...]
    selectors: tuple[StateSelector, ...]
    replay_assertions: tuple[StateReplayAssertion, ...]
    visibility: tuple[VisibilityRule, ...] = ()


@dataclass(frozen=True)
class NormalizedStateIR:
    contract: dict[str, Any]
    contract_hash: str

    def to_json(self) -> dict[str, Any]:
        return {"contract_hash": self.contract_hash, **copy.deepcopy(self.contract)}


def validate_state_ir(app_payload: dict[str, Any]) -> tuple[StateIR | None, list[StateValidationIssue]]:
    issues: list[StateValidationIssue] = []
    profile = app_payload.get("interactive_state")
    if profile != INTERACTIVE_STATE_PROFILE:
        issues.append(
            StateValidationIssue(
                "APP_STATE_PROFILE_REQUIRED",
                "$.interactive_state",
                f"AppBundle V3/V4 must declare interactive_state {INTERACTIVE_STATE_PROFILE}.",
                "Set interactive_state to interactive_state_v0.",
            )
        )
    state_items = _required_array(app_payload, "state", "$", issues)
    mutation_items = _required_array(app_payload, "mutations", "$", issues)
    selector_items = _required_array(app_payload, "selectors", "$", issues)
    replay_items = app_payload.get("state_replay_assertions", [])
    if not isinstance(replay_items, list):
        issues.append(StateValidationIssue("APP_STATE_REPLAY_NOT_ARRAY", "$.state_replay_assertions", "state_replay_assertions must be an array."))
        replay_items = []

    _check_count(state_items, APP_STATE_MAX_ENTRIES, "$.state", "APP_STATE_LIMIT_EXCEEDED", "state entries", issues)
    _check_count(mutation_items, APP_STATE_MAX_MUTATIONS, "$.mutations", "APP_STATE_LIMIT_EXCEEDED", "mutations", issues)
    _check_count(selector_items, APP_STATE_MAX_SELECTORS, "$.selectors", "APP_STATE_LIMIT_EXCEEDED", "selectors", issues)
    _check_count(
        replay_items,
        APP_STATE_MAX_REPLAY_ASSERTIONS,
        "$.state_replay_assertions",
        "APP_STATE_LIMIT_EXCEEDED",
        "replay assertions",
        issues,
    )

    screen_ids = _screen_ids(app_payload)
    action_payload_bindings = _screen_action_payload_bindings(app_payload)
    resource_view_ids = _resource_view_ids(app_payload)
    states = _parse_state_entries(state_items, screen_ids, resource_view_ids, issues)
    state_ids = {state.id for state in states}
    mutations = _parse_mutations(mutation_items, state_ids, action_payload_bindings, issues)
    selectors = _parse_selectors(selector_items, state_ids, issues)
    visibility_rules: list[VisibilityRule] = []
    visibility_ids: set[str] | None = None
    if app_payload.get("schema_version") == 4:
        visibility_items = app_payload.get("visibility", [])
        if not isinstance(visibility_items, list):
            issues.append(StateValidationIssue("APP_VISIBILITY_NOT_ARRAY", "$.visibility", "visibility must be an array of rules."))
            visibility_items = []
        _check_count(visibility_items, APP_VISIBILITY_MAX_RULES, "$.visibility", "APP_VISIBILITY_LIMIT_EXCEEDED", "visibility rules", issues)
        visibility_rules = _parse_visibility_rules(
            visibility_items,
            screen_ids,
            {state.id: state for state in states},
            {selector.id: selector for selector in selectors},
            _screen_intent_target_ids(app_payload),
            issues,
        )
        visibility_ids = {rule.id for rule in visibility_rules}
        _validate_unique([rule.id for rule in visibility_rules], "$.visibility", "APP_VISIBILITY_DUPLICATE_ID", issues)
    replay_assertions = _parse_replay_assertions(
        replay_items, state_ids, {m.id: m for m in mutations}, issues, visibility_ids=visibility_ids
    )
    _validate_unique([state.id for state in states], "$.state", "APP_STATE_DUPLICATE_ID", issues)
    _validate_unique([mutation.id for mutation in mutations], "$.mutations", "APP_STATE_DUPLICATE_MUTATION_ID", issues)
    _validate_unique([selector.id for selector in selectors], "$.selectors", "APP_STATE_DUPLICATE_SELECTOR_ID", issues)
    _validate_unique([assertion.id for assertion in replay_assertions], "$.state_replay_assertions", "APP_STATE_DUPLICATE_REPLAY_ID", issues)

    if issues:
        return None, issues
    state_ir = StateIR(
        profile=INTERACTIVE_STATE_PROFILE,
        states=tuple(states),
        mutations=tuple(mutations),
        selectors=tuple(selectors),
        replay_assertions=tuple(replay_assertions),
        visibility=tuple(visibility_rules),
    )
    return state_ir, []


def state_ir_summary(app_payload: dict[str, Any]) -> dict[str, Any] | None:
    if app_payload.get("schema_version") not in {3, 4}:
        return None
    summary = {
        "profile": app_payload.get("interactive_state"),
        "state_count": len(app_payload.get("state", [])) if isinstance(app_payload.get("state"), list) else 0,
        "mutation_count": len(app_payload.get("mutations", [])) if isinstance(app_payload.get("mutations"), list) else 0,
        "selector_count": len(app_payload.get("selectors", [])) if isinstance(app_payload.get("selectors"), list) else 0,
        "replay_assertion_count": (
            len(app_payload.get("state_replay_assertions", []))
            if isinstance(app_payload.get("state_replay_assertions"), list)
            else 0
        ),
    }
    if app_payload.get("schema_version") == 4:
        # v4-only key: the v3 summary shape (5 keys) is pinned by tests and must stay byte-stable.
        summary["visibility_rule_count"] = (
            len(app_payload.get("visibility", [])) if isinstance(app_payload.get("visibility"), list) else 0
        )
    return summary


def normalize_state_ir(app_payload: dict[str, Any], state_ir: StateIR | None = None) -> NormalizedStateIR:
    if state_ir is None:
        state_ir, issues = validate_state_ir(app_payload)
        if state_ir is None:
            detail = "; ".join(issue.message for issue in issues)
            raise ValueError(f"Cannot normalize invalid state IR: {detail}")
    contract = {
        "profile": state_ir.profile,
        "state": [
            {
                "id": state.id,
                "kind": state.kind,
                "scope": state.scope,
                **({"screen_id": state.screen_id} if state.screen_id is not None else {}),
                "initial": copy.deepcopy(state.initial),
            }
            for state in state_ir.states
        ],
        "mutations": [
            {
                "id": mutation.id,
                "trigger": dict(mutation.trigger),
                "ops": [copy.deepcopy(op) for op in mutation.ops],
                "payload_refs": sorted(mutation.required_payload_bindings),
                "allowed_payload_bindings": sorted(mutation.allowed_payload_bindings),
                "required_payload_bindings": sorted(mutation.required_payload_bindings),
            }
            for mutation in state_ir.mutations
        ],
        "selectors": [
            {
                "id": selector.id,
                "source_state": selector.source_state,
                "ops": [copy.deepcopy(op) for op in selector.ops],
            }
            for selector in state_ir.selectors
        ],
        "state_event_schemas": state_event_schemas(state_ir),
        "replay_assertions": [
            {
                "id": assertion.id,
                "event_count": len(assertion.events),
                "events": [copy.deepcopy(event) for event in assertion.events],
                "expect_state_ids": list(assertion.expect_state),
                "expect_selector_ids": list(assertion.expect_selectors),
                # v4-only key: v3 contract bytes are hash-golden-pinned and must not change.
                **(
                    {"expect_visibility_ids": list(assertion.expect_visibility)}
                    if app_payload.get("schema_version") == 4
                    else {}
                ),
            }
            for assertion in state_ir.replay_assertions
        ],
    }
    if app_payload.get("schema_version") == 4:
        contract["visibility"] = [
            {
                "id": rule.id,
                "screen_id": rule.screen_id,
                "target_ref": rule.target_ref,
                "when": copy.deepcopy(rule.when),
            }
            for rule in state_ir.visibility
        ]
    return NormalizedStateIR(contract=contract, contract_hash=_hash_json(contract))


def state_contract_hash(app_payload: dict[str, Any], state_ir: StateIR | None = None) -> str:
    return normalize_state_ir(app_payload, state_ir).contract_hash


def state_event_schemas(state_ir: StateIR) -> list[dict[str, Any]]:
    return [
        {
            "mutation_id": mutation.id,
            "trigger": dict(mutation.trigger),
            "allowed_payload_bindings": sorted(mutation.allowed_payload_bindings),
            "required_payload_bindings": sorted(mutation.required_payload_bindings),
            "value_type": "json_value",
        }
        for mutation in state_ir.mutations
    ]


def replay_state_assertions(app_payload: dict[str, Any]) -> dict[str, Any]:
    state_ir, issues = validate_state_ir(app_payload)
    if state_ir is None:
        return {
            "ok": False,
            "profile": INTERACTIVE_STATE_PROFILE,
            "assertion_count": 0,
            "passed_count": 0,
            "failed_count": len(issues),
            "errors": [issue.to_json() for issue in issues],
            "assertions": [],
        }
    initial = initial_state(app_payload, state_ir)
    is_v4 = app_payload.get("schema_version") == 4
    assertions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for assertion in state_ir.replay_assertions:
        current = copy.deepcopy(initial)
        event_results: list[dict[str, Any]] = []
        for event in assertion.events:
            event_result = apply_event(current, state_ir, event)
            event_results.append(event_result)
            if not event_result["ok"]:
                errors.extend(event_result["errors"])
                break
        selector_values = evaluate_selectors(current, state_ir)
        state_matches = _json_values_equal(_project_expected(current, assertion.expect_state), assertion.expect_state)
        selector_matches = _json_values_equal(_project_expected(selector_values, assertion.expect_selectors), assertion.expect_selectors)
        visibility_matches = True
        if is_v4:
            visibility_values = evaluate_visibility(current, selector_values, state_ir)
            visibility_matches = _json_values_equal(
                _project_expected(visibility_values, assertion.expect_visibility), assertion.expect_visibility
            )
        status = (
            "passed"
            if state_matches and selector_matches and visibility_matches and all(result["ok"] for result in event_results)
            else "failed"
        )
        if status == "failed":
            if not state_matches:
                errors.append(
                    {
                        "code": "APP_STATE_REPLAY_STATE_MISMATCH",
                        "path": f"$.state_replay_assertions.{assertion.id}.expect_state",
                        "message": f"Replay assertion {assertion.id} final state did not match.",
                    }
                )
            if not selector_matches:
                errors.append(
                    {
                        "code": "APP_STATE_REPLAY_SELECTOR_MISMATCH",
                        "path": f"$.state_replay_assertions.{assertion.id}.expect_selectors",
                        "message": f"Replay assertion {assertion.id} selector values did not match.",
                    }
                )
            if not visibility_matches:
                errors.append(
                    {
                        "code": "APP_VISIBILITY_REPLAY_MISMATCH",
                        "path": f"$.state_replay_assertions.{assertion.id}.expect_visibility",
                        "message": f"Replay assertion {assertion.id} visibility verdicts did not match.",
                    }
                )
        entry = {
            "id": assertion.id,
            "status": status,
            "event_count": len(assertion.events),
            "state_matches": state_matches,
            "selector_matches": selector_matches,
        }
        if is_v4:
            # v4-only key: the v3 replay report shape must stay byte-stable.
            entry["visibility_matches"] = visibility_matches
        assertions.append(entry)
    failed_count = sum(1 for assertion in assertions if assertion["status"] != "passed")
    return {
        "ok": failed_count == 0 and not errors,
        "profile": INTERACTIVE_STATE_PROFILE,
        "assertion_count": len(assertions),
        "passed_count": len(assertions) - failed_count,
        "failed_count": failed_count,
        "errors": errors,
        "assertions": assertions,
    }


def initial_state(app_payload: dict[str, Any], state_ir: StateIR) -> dict[str, Any]:
    values: dict[str, Any] = {}
    resource_views = _resource_view_values(app_payload)
    for state in state_ir.states:
        initial = state.initial
        if "value" in initial:
            values[state.id] = copy.deepcopy(initial["value"])
        elif "from_resource_view" in initial:
            ref = initial["from_resource_view"]
            if isinstance(ref, dict):
                values[state.id] = copy.deepcopy(resource_views.get((str(ref.get("screen_id")), str(ref.get("view_id"))), []))
            else:
                values[state.id] = []
        else:
            values[state.id] = _default_state_value(state.kind)
    return values


def apply_event(current_state: dict[str, Any], state_ir: StateIR, event: dict[str, Any]) -> dict[str, Any]:
    mutation_id = event.get("mutation_id")
    if not isinstance(mutation_id, str):
        return _event_error("APP_STATE_EVENT_MUTATION_REQUIRED", "State replay events must declare mutation_id.")
    mutation = next((item for item in state_ir.mutations if item.id == mutation_id), None)
    if mutation is None:
        return _event_error("APP_STATE_EVENT_MUTATION_MISSING", f"State replay event references missing mutation {mutation_id}.")
    payload_values = event.get("payload_values", {})
    if not isinstance(payload_values, dict):
        return _event_error("APP_STATE_EVENT_PAYLOAD_INVALID", "State replay event payload_values must be an object.")
    payload_errors = _validate_event_payload(mutation, payload_values)
    if payload_errors:
        return {"ok": False, "mutation_id": mutation_id, "errors": payload_errors}
    errors: list[dict[str, str]] = []
    # Atomic per event: apply to a working copy and commit only if every op succeeds, so a
    # failed op leaves state unchanged -- mirroring the generated JS reducer, which builds a
    # clone and returns it only at the end (a thrown op discards the whole event's changes).
    # In-place mutation is preserved for callers on success via clear()+update().
    working = copy.deepcopy(current_state)
    for op in mutation.ops:
        try:
            _apply_op(working, op, payload_values)
        except (TypeError, ValueError) as exc:
            errors.append({"code": "APP_STATE_REDUCER_OP_FAILED", "path": f"$.mutations.{mutation.id}.ops", "message": str(exc)})
            break
    if not errors:
        current_state.clear()
        current_state.update(working)
    return {"ok": not errors, "mutation_id": mutation_id, "errors": errors}


def evaluate_selectors(current_state: dict[str, Any], state_ir: StateIR) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for selector in state_ir.selectors:
        value = copy.deepcopy(current_state.get(selector.source_state))
        for op in selector.ops:
            kind = op.get("op")
            if kind == "filter_eq":
                field = str(op.get("field"))
                expected = op.get("value")
                value = [item for item in value if isinstance(item, dict) and _js_strict_eq(item.get(field), expected)] if isinstance(value, list) else []
            elif kind == "sort_by":
                field = str(op.get("field"))
                direction = str(op.get("direction", "asc"))
                value = (
                    sorted(value, key=lambda item: _selector_sort_key(item, field), reverse=direction == "desc")
                    if isinstance(value, list)
                    else []
                )
            elif kind == "slice":
                start = int(op.get("start", 0))
                end = op.get("end")
                value = value[start : int(end)] if isinstance(value, list) and end is not None else value[start:] if isinstance(value, list) else []
        values[selector.id] = value
    return values


def evaluate_visibility(
    current_state: dict[str, Any],
    selector_values: dict[str, Any],
    state_ir: StateIR,
) -> dict[str, bool]:
    """Visibility verdict per rule id.

    Total over arbitrary JSON state values (SC-V3): the kind/source restrictions are
    validation-time advisories only — mutations can drive any state to any JSON shape — so the
    primitives here (Python truthiness, ``_js_strict_eq``, list length) must stay total and
    exactly mirror the generated ``evaluateViewSpecVisibility``.
    """
    return {rule.id: _visibility_condition_holds(rule.when, current_state, selector_values) for rule in state_ir.visibility}


def _visibility_condition_holds(
    when: dict[str, Any],
    current_state: dict[str, Any],
    selector_values: dict[str, Any],
) -> bool:
    if isinstance(when.get("selector"), str):
        value = selector_values.get(when["selector"])
        filled = isinstance(value, list) and len(value) > 0
        return not filled if when.get("is") == "empty" else filled
    if "equals" in when:
        return _js_strict_eq(current_state.get(str(when.get("state"))), when.get("equals"))
    truthy = bool(current_state.get(str(when.get("state"))))
    return not truthy if when.get("is") == "falsy" else truthy


def initial_visibility(app_payload: dict[str, Any], state_ir: StateIR) -> dict[str, bool]:
    """Visibility verdicts at the app's initial state — the single source for baked markers (SC-V1)."""
    initial = initial_state(app_payload, state_ir)
    return evaluate_visibility(initial, evaluate_selectors(initial, state_ir), state_ir)


def state_manifest(
    app_payload: dict[str, Any],
    *,
    reducer_hash: str,
    conformance_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_ir, issues = validate_state_ir(app_payload)
    if state_ir is None:
        detail = "; ".join(issue.message for issue in issues)
        raise ValueError(f"Cannot build state manifest for invalid state IR: {detail}")
    normalized = normalize_state_ir(app_payload, state_ir)
    replay_report = replay_state_assertions(app_payload)
    return {
        "schema_version": STATE_MANIFEST_SCHEMA_VERSION,
        "profile": INTERACTIVE_STATE_PROFILE,
        "app_schema_version": app_payload.get("schema_version"),
        "summary": state_ir_summary(app_payload),
        "normalized_contract": normalized.to_json(),
        "contract_hash": normalized.contract_hash,
        "state_event_schemas": state_event_schemas(state_ir),
        "state_ids": [item.get("id") for item in app_payload.get("state", []) if isinstance(item, dict)],
        "mutation_ids": [item.get("id") for item in app_payload.get("mutations", []) if isinstance(item, dict)],
        "selector_ids": [item.get("id") for item in app_payload.get("selectors", []) if isinstance(item, dict)],
        # v4-only key: the v3 manifest shape must stay byte-stable.
        **(
            {"visibility_rule_ids": [rule.id for rule in state_ir.visibility]}
            if app_payload.get("schema_version") == 4
            else {}
        ),
        "reducer_exports": list(state_reducer_exports(app_payload)),
        "reducer_hash": reducer_hash,
        "replay": replay_report,
        "reducer_conformance": conformance_report,
    }


def state_reducer_exports(app_payload: dict[str, Any]) -> tuple[str, ...]:
    """The generated artifact's export list — visibility joins only on v4 bundles."""
    if app_payload.get("schema_version") == 4:
        return (*STATE_REDUCER_EXPORTS, STATE_REDUCER_VISIBILITY_EXPORT)
    return STATE_REDUCER_EXPORTS


def generate_typescript_reducer(app_payload: dict[str, Any]) -> str:
    state_ir, issues = validate_state_ir(app_payload)
    if state_ir is None:
        detail = "; ".join(issue.message for issue in issues)
        raise ValueError(f"Cannot generate reducer for invalid state IR: {detail}")
    initial = initial_state(app_payload, state_ir)
    selectors = [
        {"id": selector.id, "sourceState": selector.source_state, "ops": list(selector.ops)}
        for selector in state_ir.selectors
    ]
    mutations = [
        {
            "id": mutation.id,
            "trigger": dict(mutation.trigger),
            "ops": list(mutation.ops),
            "allowedPayloadBindings": sorted(mutation.allowed_payload_bindings),
            "requiredPayloadBindings": sorted(mutation.required_payload_bindings),
        }
        for mutation in state_ir.mutations
    ]
    source = (
        "// Generated by ViewSpec AppBundle V3 interactive_state_v0. Do not edit.\n"
        "/** @typedef {Record<string, unknown>} ViewSpecState */\n"
        "/** @typedef {{ mutation_id: string, payload_values?: Record<string, unknown> }} ViewSpecStateEvent */\n\n"
        f"export const VIEWSPEC_STATE_PROFILE = {json.dumps(INTERACTIVE_STATE_PROFILE)};\n"
        f"export const initialState = {json.dumps(initial, indent=2, sort_keys=True)};\n"
        f"const mutations = {json.dumps(mutations, indent=2, sort_keys=True)};\n"
        f"const selectors = {json.dumps(selectors, indent=2, sort_keys=True)};\n\n"
        "const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));\n"
        "const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);\n"
        "const payload = (values, key) => values[key];\n"
        "const valueOf = (value, values) => {\n"
        "  if (value && typeof value === \"object\" && !Array.isArray(value) && Object.keys(value).length === 1 && \"from_payload\" in value) {\n"
        "    return payload(values, String(value.from_payload));\n"
        "  }\n"
        "  return clone(value);\n"
        "};\n\n"
        "const eventError = (code, values) => {\n"
        "  const suffix = Array.isArray(values) && values.length ? `:${values.join(\",\")}` : \"\";\n"
        "  const error = new Error(`${code}${suffix}`);\n"
        "  error.code = code;\n"
        "  error.values = values;\n"
        "  return error;\n"
        "};\n\n"
        "const pyTruthy = (x) => {\n"
        "  if (x === null || x === undefined || x === false || x === 0 || x === \"\") return false;\n"
        "  if (Array.isArray(x)) return x.length > 0;\n"
        "  if (typeof x === \"object\") return Object.keys(x).length > 0;\n"
        "  return Boolean(x);\n"
        "};\n"
        "const toFiniteNumber = (x) => {\n"
        "  const n = x === undefined ? 0 : x;\n"
        "  if (typeof n !== \"number\" || !Number.isFinite(n)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "  return n;\n"
        "};\n\n"
        "const assertPayload = (mutation, values) => {\n"
        "  if (!values || typeof values !== \"object\" || Array.isArray(values)) throw eventError(\"APP_STATE_EVENT_PAYLOAD_INVALID\", []);\n"
        "  const allowed = new Set(mutation.allowedPayloadBindings || []);\n"
        "  const required = mutation.requiredPayloadBindings || [];\n"
        "  const unknown = Object.keys(values).filter((key) => !allowed.has(key)).sort();\n"
        "  if (unknown.length) throw eventError(\"APP_STATE_EVENT_PAYLOAD_UNKNOWN\", unknown);\n"
        "  const missing = required.filter((key) => !hasOwn(values, key)).sort();\n"
        "  if (missing.length) throw eventError(\"APP_STATE_EVENT_PAYLOAD_MISSING\", missing);\n"
        "};\n\n"
        "/** @param {ViewSpecState} state @param {ViewSpecStateEvent} event @returns {ViewSpecState} */\n"
        "export function reduceViewSpecState(state, event) {\n"
        "  const next = clone(state);\n"
        "  const mutation = mutations.find((item) => item.id === event.mutation_id);\n"
        "  if (!mutation) return next;\n"
        "  const values = event.payload_values ?? {};\n"
        "  assertPayload(mutation, values);\n"
        "  for (const op of mutation.ops) {\n"
        "    const key = String(op.state ?? \"\");\n"
        "    const current = next[key];\n"
        "    if (op.op === \"set\") next[key] = valueOf(op.value, values);\n"
        "    if (op.op === \"append\") { if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []); next[key] = [...current, valueOf(op.value, values)]; }\n"
        "    if (op.op === \"remove\") { if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []); const rid = String(valueOf(op.item_id, values)); next[key] = current.filter((item) => !(item && typeof item === \"object\" && String(item.id) === rid)); }\n"
        "    if (op.op === \"move\") {\n"
        "      if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "      const id = String(valueOf(op.item_id, values));\n"
        "      const from = current.findIndex((item) => item && typeof item === \"object\" && String(item.id) === id);\n"
        "      const rawTo = valueOf(op.to_index, values);\n"
        "      const toNum = typeof rawTo === \"number\" ? rawTo : (typeof rawTo === \"string\" ? Number(rawTo) : NaN);\n"
        "      if (!Number.isFinite(toNum)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "      const to = Math.trunc(toNum);\n"
        "      if (from >= 0) { const copy = [...current]; const [item] = copy.splice(from, 1); copy.splice(Math.max(0, Math.min(to, copy.length)), 0, item); next[key] = copy; }\n"
        "    }\n"
        "    if (op.op === \"patch\") next[key] = patchValue(current, op, values);\n"
        "    if (op.op === \"toggle\") next[key] = toggleValue(current, op, values);\n"
        "    if (op.op === \"increment\") next[key] = incrementValue(current, op, values);\n"
        "  }\n"
        "  return next;\n"
        "}\n\n"
        "function patchValue(current, op, values) {\n"
        "  const patch = valueOf(op.value, values);\n"
        "  if (!(patch && typeof patch === \"object\" && !Array.isArray(patch))) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "  const idExpr = op.item_id;\n"
        "  if (idExpr !== undefined) {\n"
        "    if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "    const id = String(valueOf(idExpr, values));\n"
        "    return current.map((item) => item && typeof item === \"object\" && String(item.id) === id ? { ...item, ...patch } : item);\n"
        "  }\n"
        "  if (current && typeof current === \"object\" && !Array.isArray(current)) return { ...current, ...patch };\n"
        "  throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "}\n\n"
        "function toggleValue(current, op, values) {\n"
        "  const field = op.field;\n"
        "  const idExpr = op.item_id;\n"
        "  if (idExpr !== undefined && typeof field === \"string\") {\n"
        "    if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "    const id = String(valueOf(idExpr, values));\n"
        "    return current.map((item) => item && typeof item === \"object\" && String(item.id) === id ? { ...item, [field]: !pyTruthy(item[field]) } : item);\n"
        "  }\n"
        "  if (typeof field === \"string\" && current && typeof current === \"object\" && !Array.isArray(current)) return { ...current, [field]: !pyTruthy(current[field]) };\n"
        "  return !pyTruthy(current);\n"
        "}\n\n"
        "function incrementValue(current, op, values) {\n"
        "  const amount = toFiniteNumber(valueOf(op.amount ?? 1, values));\n"
        "  const field = op.field;\n"
        "  const idExpr = op.item_id;\n"
        "  if (idExpr !== undefined && typeof field === \"string\") {\n"
        "    if (!Array.isArray(current)) throw eventError(\"APP_STATE_REDUCER_OP_FAILED\", []);\n"
        "    const id = String(valueOf(idExpr, values));\n"
        "    return current.map((item) => item && typeof item === \"object\" && String(item.id) === id ? { ...item, [field]: toFiniteNumber(item[field]) + amount } : item);\n"
        "  }\n"
        "  if (typeof field === \"string\" && current && typeof current === \"object\" && !Array.isArray(current)) return { ...current, [field]: toFiniteNumber(current[field]) + amount };\n"
        "  return toFiniteNumber(current) + amount;\n"
        "}\n\n"
        "/** @param {ViewSpecState} state @returns {Record<string, unknown>} */\n"
        "export function selectViewSpecState(state) {\n"
        "  const result = {};\n"
        "  for (const selector of selectors) {\n"
        "    let value = clone(state[selector.sourceState]);\n"
        "    for (const op of selector.ops) {\n"
        "      if (op.op === \"filter_eq\" && Array.isArray(value)) value = value.filter((item) => item && typeof item === \"object\" && item[op.field] === op.value);\n"
        "      if (op.op === \"sort_by\" && Array.isArray(value)) {\n"
        "        const vsElemRank = (x) => (x !== null && typeof x === \"object\" && !Array.isArray(x)) ? 0 : 1;\n"
        "        const vsField = (x) => vsElemRank(x) === 0 ? x[op.field] : undefined;\n"
        "        const vsTypeRank = (v) => (v === null || v === undefined) ? 0 : typeof v === \"boolean\" ? 1 : typeof v === \"number\" ? 2 : typeof v === \"string\" ? 3 : 4;\n"
        "        const vsNum = (v) => typeof v === \"boolean\" ? (v ? 1 : 0) : typeof v === \"number\" ? v : 0;\n"
        "        const vsStr = (v) => typeof v === \"string\" ? v : \"\";\n"
        "        const vsDir = op.direction === \"desc\" ? -1 : 1;\n"
        "        value = value.map((item, index) => ({ item, index })).sort((a, b) => {\n"
        "          let cmp = vsElemRank(a.item) - vsElemRank(b.item);\n"
        "          if (cmp === 0) {\n"
        "            const va = vsField(a.item), vb = vsField(b.item);\n"
        "            cmp = vsTypeRank(va) - vsTypeRank(vb);\n"
        "            if (cmp === 0) { const na = vsNum(va), nb = vsNum(vb); cmp = na < nb ? -1 : na > nb ? 1 : 0; }\n"
        "            if (cmp === 0) { const sa = vsStr(va), sb = vsStr(vb); cmp = sa < sb ? -1 : sa > sb ? 1 : 0; }\n"
        "          }\n"
        "          return cmp !== 0 ? cmp * vsDir : a.index - b.index;\n"
        "        }).map((entry) => entry.item);\n"
        "      }\n"
        "      if (op.op === \"slice\" && Array.isArray(value)) value = value.slice(op.start ?? 0, op.end);\n"
        "    }\n"
        "    result[selector.id] = value;\n"
        "  }\n"
        "  return result;\n"
        "}\n"
    )
    if app_payload.get("schema_version") == 4:
        # v4-only block: v3 reducer bytes are hash-golden-pinned and must stay identical.
        visibility_rules = [{"id": rule.id, "when": copy.deepcopy(rule.when)} for rule in state_ir.visibility]
        source += (
            f"\nconst visibilityRules = {json.dumps(visibility_rules, indent=2, sort_keys=True)};\n"
            "/** @param {ViewSpecState} state @returns {Record<string, boolean>} */\n"
            "export function evaluateViewSpecVisibility(state) {\n"
            "  const selectorValues = selectViewSpecState(state);\n"
            "  const result = {};\n"
            "  for (const rule of visibilityRules) {\n"
            "    const w = rule.when;\n"
            "    let visible = false;\n"
            "    if (typeof w.selector === \"string\") {\n"
            "      const v = selectorValues[w.selector];\n"
            "      const filled = Array.isArray(v) && v.length > 0;\n"
            "      visible = w.is === \"empty\" ? !filled : filled;\n"
            "    } else if (hasOwn(w, \"equals\")) {\n"
            "      visible = state[String(w.state)] === w.equals;\n"
            "    } else {\n"
            "      const t = pyTruthy(state[String(w.state)]);\n"
            "      visible = w.is === \"falsy\" ? !t : t;\n"
            "    }\n"
            "    result[rule.id] = visible;\n"
            "  }\n"
            "  return result;\n"
            "}\n"
        )
    return source


def generate_browser_reducer_script(app_payload: dict[str, Any]) -> str:
    """Browser variant of the generated reducer for the V4 static shell.

    A purely textual transform of the exact ``generate_typescript_reducer`` output: strip the
    leading ``export `` prefixes (inline classic scripts cannot contain export declarations) and
    wrap in an IIFE exposing ``ViewSpecStateRuntime``. Zero new semantics — a structural test pins
    equality modulo the stripped prefixes and fixed wrapper lines.
    """
    source = generate_typescript_reducer(app_payload)
    stripped = source.replace("\nexport const ", "\nconst ").replace("\nexport function ", "\nfunction ")
    export_names = ", ".join(state_reducer_exports(app_payload))
    return "const ViewSpecStateRuntime = (() => {\n" + stripped + f"\nreturn {{ {export_names} }};\n}})();\n"


def check_reducer_conformance(
    app_payload: dict[str, Any],
    *,
    reducer_source: str | None = None,
    node_command: str = "node",
) -> dict[str, Any]:
    state_ir, issues = validate_state_ir(app_payload)
    if state_ir is None:
        return {
            "ok": False,
            "profile": INTERACTIVE_STATE_PROFILE,
            "runtime": "node",
            "assertion_count": 0,
            "passed_count": 0,
            "failed_count": len(issues),
            "reducer_source_hash": None,
            "export_names": [],
            "errors": [issue.to_json() for issue in issues],
            "replays": [],
        }
    source = reducer_source if reducer_source is not None else generate_typescript_reducer(app_payload)
    expected_replays = _expected_conformance_replays(app_payload, state_ir)
    node_report = _run_node_conformance(source, state_ir, node_command=node_command)
    errors: list[dict[str, Any]] = []
    if not node_report.get("ok"):
        errors.extend(node_report.get("errors") if isinstance(node_report.get("errors"), list) else [])
        return {
            "ok": False,
            "profile": INTERACTIVE_STATE_PROFILE,
            "runtime": "node",
            "assertion_count": len(expected_replays),
            "passed_count": 0,
            "failed_count": len(expected_replays) or 1,
            "reducer_source_hash": _hash_text(source),
            "export_names": node_report.get("export_names") if isinstance(node_report.get("export_names"), list) else [],
            "errors": errors or [_conformance_error("Node reducer conformance failed before replay.")],
            "replays": [],
        }
    export_names = [name for name in node_report.get("export_names", []) if isinstance(name, str)]
    missing_exports = [name for name in state_reducer_exports(app_payload) if name not in export_names]
    if node_report.get("profile") != INTERACTIVE_STATE_PROFILE:
        errors.append(_conformance_error("Generated reducer exported an unexpected VIEWSPEC_STATE_PROFILE."))
    if missing_exports:
        errors.append(_conformance_error(f"Generated reducer is missing export(s): {', '.join(missing_exports)}."))
    node_replays = {
        replay.get("id"): replay
        for replay in node_report.get("replays", [])
        if isinstance(replay, dict) and isinstance(replay.get("id"), str)
    }
    replay_reports: list[dict[str, Any]] = []
    for expected in expected_replays:
        actual = node_replays.get(expected["id"])
        visibility_expected = "visibility" in expected
        if actual is None:
            state_matches = False
            selector_matches = False
            visibility_matches = not visibility_expected
            event_ok = False
            errors.append(_conformance_error(f"Node reducer omitted replay assertion {expected['id']}."))
        else:
            state_matches = _json_values_equal(actual.get("state"), expected["state"])
            selector_matches = _json_values_equal(actual.get("selectors"), expected["selectors"])
            visibility_matches = (
                _json_values_equal(actual.get("visibility"), expected["visibility"]) if visibility_expected else True
            )
            event_ok = bool(actual.get("ok")) == bool(expected["ok"])
            actual_errors = actual.get("errors") if isinstance(actual.get("errors"), list) else []
            if not event_ok:
                errors.append(_conformance_error(f"Node reducer event status diverged for replay assertion {expected['id']}."))
            if not state_matches:
                errors.append(_conformance_error(f"Node reducer final state diverged for replay assertion {expected['id']}."))
            if not selector_matches:
                errors.append(_conformance_error(f"Node reducer selector values diverged for replay assertion {expected['id']}."))
            if not visibility_matches:
                errors.append(_conformance_error(f"Node reducer visibility verdicts diverged for replay assertion {expected['id']}."))
            if not bool(expected["ok"]) and actual_errors and expected.get("error_codes"):
                actual_codes = {str(error.get("code") or "").split(":", 1)[0] for error in actual_errors if isinstance(error, dict)}
                if not set(expected["error_codes"]).issubset(actual_codes):
                    errors.append(_conformance_error(f"Node reducer error codes diverged for replay assertion {expected['id']}."))
        replay_reports.append(
            {
                "id": expected["id"],
                "status": (
                    "passed"
                    if actual is not None and event_ok and state_matches and selector_matches and visibility_matches
                    else "failed"
                ),
                "event_count": expected["event_count"],
                "state_matches": state_matches,
                "selector_matches": selector_matches,
                # v4-only key: v3 conformance report shape stays byte-stable.
                **({"visibility_matches": visibility_matches} if visibility_expected else {}),
                "event_status_matches": event_ok,
            }
        )
    failed_count = sum(1 for replay in replay_reports if replay["status"] != "passed")
    return {
        "ok": failed_count == 0 and not errors,
        "profile": INTERACTIVE_STATE_PROFILE,
        "runtime": "node",
        "assertion_count": len(expected_replays),
        "passed_count": len(expected_replays) - failed_count,
        "failed_count": failed_count,
        "reducer_source_hash": _hash_text(source),
        "export_names": export_names,
        "errors": errors,
        "replays": replay_reports,
    }


def _parse_state_entries(
    items: list[Any],
    screen_ids: set[str],
    resource_view_ids: set[tuple[str, str]],
    issues: list[StateValidationIssue],
) -> list[StateEntry]:
    states: list[StateEntry] = []
    for index, item in enumerate(items):
        path = f"$.state[{index}]"
        if not isinstance(item, dict):
            issues.append(StateValidationIssue("APP_STATE_ENTRY_NOT_OBJECT", path, "Each state entry must be an object."))
            continue
        _reject_extra(item, {"id", "kind", "scope", "screen_id", "initial"}, path, issues)
        state_id = _required_string(item, "id", path, issues)
        kind = _required_string(item, "kind", path, issues)
        scope = _required_string(item, "scope", path, issues)
        screen_id = item.get("screen_id")
        initial = item.get("initial")
        _validate_safe_id(state_id, f"{path}.id", "state id", issues)
        if kind and kind not in STATE_KINDS:
            issues.append(StateValidationIssue("APP_STATE_KIND_UNSUPPORTED", f"{path}.kind", f"Unsupported state kind {kind}."))
        if scope and scope not in STATE_SCOPES:
            issues.append(StateValidationIssue("APP_STATE_SCOPE_UNSUPPORTED", f"{path}.scope", f"Unsupported state scope {scope}."))
        if scope == "screen":
            if not isinstance(screen_id, str):
                issues.append(StateValidationIssue("APP_STATE_SCREEN_REQUIRED", f"{path}.screen_id", "screen-scoped state requires screen_id."))
            elif screen_id not in screen_ids:
                issues.append(StateValidationIssue("APP_STATE_SCREEN_MISSING", f"{path}.screen_id", f"State references missing screen {screen_id}."))
        elif "screen_id" in item:
            issues.append(StateValidationIssue("APP_STATE_SCREEN_SCOPE_MISMATCH", f"{path}.screen_id", "Only screen-scoped state may declare screen_id."))
        if not isinstance(initial, dict):
            issues.append(StateValidationIssue("APP_STATE_INITIAL_INVALID", f"{path}.initial", "state.initial must be an object."))
            initial = {}
        else:
            keys = set(initial)
            if keys not in ({"value"}, {"from_resource_view"}):
                issues.append(
                    StateValidationIssue(
                        "APP_STATE_INITIAL_INVALID",
                        f"{path}.initial",
                        "state.initial must declare exactly one of value or from_resource_view.",
                    )
                )
            if "from_resource_view" in initial:
                ref = initial["from_resource_view"]
                if not isinstance(ref, dict):
                    issues.append(
                        StateValidationIssue(
                            "APP_STATE_RESOURCE_VIEW_REF_INVALID",
                            f"{path}.initial.from_resource_view",
                            "from_resource_view must be an object with screen_id and view_id.",
                        )
                    )
                else:
                    _reject_extra(ref, {"screen_id", "view_id"}, f"{path}.initial.from_resource_view", issues)
                    ref_screen = _required_string(ref, "screen_id", f"{path}.initial.from_resource_view", issues)
                    view_id = _required_string(ref, "view_id", f"{path}.initial.from_resource_view", issues)
                    if ref_screen and view_id and (ref_screen, view_id) not in resource_view_ids:
                        issues.append(
                            StateValidationIssue(
                                "APP_STATE_RESOURCE_VIEW_MISSING",
                                f"{path}.initial.from_resource_view",
                                f"State references missing resource_view {ref_screen}.{view_id}.",
                            )
                        )
        if state_id and kind and scope and isinstance(initial, dict):
            states.append(StateEntry(id=state_id, kind=kind, scope=scope, initial=copy.deepcopy(initial), screen_id=screen_id if isinstance(screen_id, str) else None))
    return states


def _parse_mutations(
    items: list[Any],
    state_ids: set[str],
    action_payload_bindings: dict[tuple[str, str], set[str]],
    issues: list[StateValidationIssue],
) -> list[StateMutation]:
    mutations: list[StateMutation] = []
    for index, item in enumerate(items):
        path = f"$.mutations[{index}]"
        if not isinstance(item, dict):
            issues.append(StateValidationIssue("APP_STATE_MUTATION_NOT_OBJECT", path, "Each mutation must be an object."))
            continue
        _reject_extra(item, {"id", "trigger", "ops"}, path, issues)
        mutation_id = _required_string(item, "id", path, issues)
        _validate_safe_id(mutation_id, f"{path}.id", "mutation id", issues)
        trigger = item.get("trigger")
        if not isinstance(trigger, dict):
            issues.append(StateValidationIssue("APP_STATE_TRIGGER_INVALID", f"{path}.trigger", "mutation.trigger must be an object."))
            trigger = {}
        else:
            _reject_extra(trigger, {"screen_id", "action_id"}, f"{path}.trigger", issues)
        screen_id = _required_string(trigger, "screen_id", f"{path}.trigger", issues)
        action_id = _required_string(trigger, "action_id", f"{path}.trigger", issues)
        trigger_key = (screen_id, action_id) if screen_id and action_id else None
        if trigger_key is not None and trigger_key not in action_payload_bindings:
            issues.append(
                StateValidationIssue(
                    "APP_STATE_TRIGGER_ACTION_MISSING",
                    f"{path}.trigger",
                    f"Mutation trigger references missing action {screen_id}.{action_id}.",
                )
            )
        payload_bindings = action_payload_bindings.get(trigger_key, set()) if trigger_key is not None else set()
        ops = _required_array(item, "ops", path, issues)
        _check_count(ops, APP_STATE_MAX_OPS_PER_MUTATION, f"{path}.ops", "APP_STATE_LIMIT_EXCEEDED", "ops per mutation", issues)
        parsed_ops: list[dict[str, Any]] = []
        required_payload_bindings: set[str] = set()
        for op_index, op in enumerate(ops):
            op_path = f"{path}.ops[{op_index}]"
            if not isinstance(op, dict):
                issues.append(StateValidationIssue("APP_STATE_OP_NOT_OBJECT", op_path, "Mutation ops must be objects."))
                continue
            kind = _required_string(op, "op", op_path, issues)
            if kind and kind not in MUTATION_OPS:
                issues.append(StateValidationIssue("APP_STATE_OP_UNSUPPORTED", f"{op_path}.op", f"Unsupported mutation op {kind}."))
            state_id = _required_string(op, "state", op_path, issues)
            if state_id and state_id not in state_ids:
                issues.append(StateValidationIssue("APP_STATE_OP_STATE_MISSING", f"{op_path}.state", f"Operation targets missing state {state_id}."))
            _validate_op_shape(op, op_path, kind, issues)
            for payload_id in _op_payload_refs(op):
                required_payload_bindings.add(payload_id)
                if payload_id not in payload_bindings:
                    issues.append(
                        StateValidationIssue(
                            "APP_STATE_PAYLOAD_BINDING_MISSING",
                            op_path,
                            f"Operation reads payload binding {payload_id}, but the trigger action does not declare it.",
                        )
                    )
            parsed_ops.append(copy.deepcopy(op))
        if mutation_id and screen_id and action_id:
            mutations.append(
                StateMutation(
                    id=mutation_id,
                    trigger={"screen_id": screen_id, "action_id": action_id},
                    ops=tuple(parsed_ops),
                    allowed_payload_bindings=frozenset(payload_bindings),
                    required_payload_bindings=frozenset(required_payload_bindings),
                )
            )
    return mutations


def _validate_op_shape(op: dict[str, Any], path: str, kind: str | None, issues: list[StateValidationIssue]) -> None:
    allowed = {
        "set": {"op", "state", "value"},
        "patch": {"op", "state", "item_id", "value"},
        "toggle": {"op", "state", "item_id", "field"},
        "append": {"op", "state", "value"},
        "remove": {"op", "state", "item_id"},
        "move": {"op", "state", "item_id", "to_index"},
        "increment": {"op", "state", "item_id", "field", "amount"},
    }.get(kind or "", {"op", "state"})
    _reject_extra(op, allowed, path, issues)
    required_by_op = {
        "set": ("value",),
        "patch": ("value",),
        "append": ("value",),
        "remove": ("item_id",),
        "move": ("item_id", "to_index"),
    }
    for key in required_by_op.get(kind or "", ()):
        if key not in op:
            issues.append(StateValidationIssue("APP_STATE_OP_FIELD_REQUIRED", f"{path}.{key}", f"Operation {kind} requires {key}."))


def _parse_selectors(items: list[Any], state_ids: set[str], issues: list[StateValidationIssue]) -> list[StateSelector]:
    selectors: list[StateSelector] = []
    for index, item in enumerate(items):
        path = f"$.selectors[{index}]"
        if not isinstance(item, dict):
            issues.append(StateValidationIssue("APP_STATE_SELECTOR_NOT_OBJECT", path, "Each selector must be an object."))
            continue
        _reject_extra(item, {"id", "source_state", "ops"}, path, issues)
        selector_id = _required_string(item, "id", path, issues)
        _validate_safe_id(selector_id, f"{path}.id", "selector id", issues)
        source_state = _required_string(item, "source_state", path, issues)
        if source_state and source_state not in state_ids:
            issues.append(StateValidationIssue("APP_STATE_SELECTOR_SOURCE_MISSING", f"{path}.source_state", f"Selector source state {source_state} is missing."))
        ops = _required_array(item, "ops", path, issues)
        _check_count(ops, APP_STATE_MAX_SELECTOR_OPS, f"{path}.ops", "APP_STATE_LIMIT_EXCEEDED", "selector ops", issues)
        parsed_ops: list[dict[str, Any]] = []
        for op_index, op in enumerate(ops):
            op_path = f"{path}.ops[{op_index}]"
            if not isinstance(op, dict):
                issues.append(StateValidationIssue("APP_STATE_SELECTOR_OP_NOT_OBJECT", op_path, "Selector ops must be objects."))
                continue
            kind = _required_string(op, "op", op_path, issues)
            if kind and kind not in SELECTOR_OPS:
                issues.append(StateValidationIssue("APP_STATE_SELECTOR_OP_UNSUPPORTED", f"{op_path}.op", f"Unsupported selector op {kind}."))
            allowed = {
                "filter_eq": {"op", "field", "value"},
                "sort_by": {"op", "field", "direction"},
                "slice": {"op", "start", "end"},
            }.get(kind or "", {"op"})
            _reject_extra(op, allowed, op_path, issues)
            if kind in {"filter_eq", "sort_by"}:
                _required_string(op, "field", op_path, issues)
            if kind == "sort_by" and op.get("direction", "asc") not in {"asc", "desc"}:
                issues.append(StateValidationIssue("APP_STATE_SELECTOR_DIRECTION_INVALID", f"{op_path}.direction", "sort_by direction must be asc or desc."))
            if kind == "slice":
                for bound_key in ("start", "end"):
                    if bound_key not in op:
                        continue
                    bound_value = op.get(bound_key)
                    if bound_key == "end" and bound_value is None:
                        continue
                    if not isinstance(bound_value, int) or isinstance(bound_value, bool) or bound_value < 0:
                        issues.append(StateValidationIssue("APP_STATE_SELECTOR_SLICE_INVALID", f"{op_path}.{bound_key}", "slice start/end must be non-negative integers."))
            parsed_ops.append(copy.deepcopy(op))
        if selector_id and source_state:
            selectors.append(StateSelector(id=selector_id, source_state=source_state, ops=tuple(parsed_ops)))
    return selectors


def _parse_replay_assertions(
    items: list[Any],
    state_ids: set[str],
    mutations_by_id: dict[str, StateMutation],
    issues: list[StateValidationIssue],
    *,
    visibility_ids: set[str] | None = None,
) -> list[StateReplayAssertion]:
    # visibility_ids is None for v3 bundles: expect_visibility stays an unknown field there
    # (v3 strictness preserved); a set (possibly empty) enables the v4 key.
    allowed_keys = {"id", "events", "expect_state", "expect_selectors"}
    if visibility_ids is not None:
        allowed_keys = allowed_keys | {"expect_visibility"}
    assertions: list[StateReplayAssertion] = []
    for index, item in enumerate(items):
        path = f"$.state_replay_assertions[{index}]"
        if not isinstance(item, dict):
            issues.append(StateValidationIssue("APP_STATE_REPLAY_NOT_OBJECT", path, "Each replay assertion must be an object."))
            continue
        _reject_extra(item, allowed_keys, path, issues)
        assertion_id = _required_string(item, "id", path, issues)
        _validate_safe_id(assertion_id, f"{path}.id", "replay assertion id", issues)
        events = _required_array(item, "events", path, issues)
        _check_count(events, APP_STATE_MAX_EVENTS_PER_REPLAY, f"{path}.events", "APP_STATE_LIMIT_EXCEEDED", "events per replay", issues)
        for event_index, event in enumerate(events):
            event_path = f"{path}.events[{event_index}]"
            if not isinstance(event, dict):
                issues.append(StateValidationIssue("APP_STATE_REPLAY_EVENT_NOT_OBJECT", event_path, "Replay events must be objects."))
                continue
            _reject_extra(event, {"mutation_id", "payload_values"}, event_path, issues)
            mutation_id = _required_string(event, "mutation_id", event_path, issues)
            mutation = mutations_by_id.get(mutation_id or "")
            if mutation_id and mutation is None:
                issues.append(StateValidationIssue("APP_STATE_REPLAY_MUTATION_MISSING", f"{event_path}.mutation_id", f"Replay references missing mutation {mutation_id}."))
            payload_values = event.get("payload_values", {})
            if not isinstance(payload_values, dict):
                issues.append(StateValidationIssue("APP_STATE_REPLAY_PAYLOAD_INVALID", f"{event_path}.payload_values", "payload_values must be an object."))
            elif mutation is not None:
                for payload_error in _validate_event_payload(mutation, payload_values):
                    issues.append(
                        StateValidationIssue(
                            str(payload_error["code"]),
                            f"{event_path}.payload_values",
                            str(payload_error["message"]),
                        )
                    )
        expect_state = item.get("expect_state", {})
        expect_selectors = item.get("expect_selectors", {})
        if not isinstance(expect_state, dict):
            issues.append(StateValidationIssue("APP_STATE_REPLAY_EXPECT_STATE_INVALID", f"{path}.expect_state", "expect_state must be an object."))
            expect_state = {}
        if not isinstance(expect_selectors, dict):
            issues.append(StateValidationIssue("APP_STATE_REPLAY_EXPECT_SELECTORS_INVALID", f"{path}.expect_selectors", "expect_selectors must be an object."))
            expect_selectors = {}
        for state_id in expect_state:
            if state_id not in state_ids:
                issues.append(StateValidationIssue("APP_STATE_REPLAY_STATE_MISSING", f"{path}.expect_state.{state_id}", f"Replay expects missing state {state_id}."))
        expect_visibility: dict[str, Any] = {}
        if visibility_ids is not None:
            expect_visibility = item.get("expect_visibility", {})
            if not isinstance(expect_visibility, dict):
                issues.append(
                    StateValidationIssue(
                        "APP_VISIBILITY_REPLAY_EXPECT_INVALID",
                        f"{path}.expect_visibility",
                        "expect_visibility must be an object mapping visibility rule ids to booleans.",
                    )
                )
                expect_visibility = {}
            else:
                for rule_id, expected_value in expect_visibility.items():
                    if rule_id not in visibility_ids:
                        issues.append(
                            StateValidationIssue(
                                "APP_VISIBILITY_REPLAY_RULE_MISSING",
                                f"{path}.expect_visibility.{rule_id}",
                                f"Replay expects missing visibility rule {rule_id}.",
                            )
                        )
                    if not isinstance(expected_value, bool):
                        issues.append(
                            StateValidationIssue(
                                "APP_VISIBILITY_REPLAY_EXPECT_INVALID",
                                f"{path}.expect_visibility.{rule_id}",
                                "expect_visibility values must be booleans.",
                            )
                        )
        if assertion_id:
            assertions.append(
                StateReplayAssertion(
                    id=assertion_id,
                    events=tuple(copy.deepcopy(events)),
                    expect_state=copy.deepcopy(expect_state),
                    expect_selectors=copy.deepcopy(expect_selectors),
                    expect_visibility=copy.deepcopy(expect_visibility),
                )
            )
    return assertions


_VISIBILITY_CONDITION_FORMS = (
    frozenset({"state", "is"}),
    frozenset({"state", "equals"}),
    frozenset({"selector", "is"}),
)


def _parse_visibility_rules(
    items: list[Any],
    screen_ids: set[str],
    states_by_id: dict[str, StateEntry],
    selectors_by_id: dict[str, StateSelector],
    screen_target_ids: dict[str, dict[str, set[str]]],
    issues: list[StateValidationIssue],
) -> list[VisibilityRule]:
    rules: list[VisibilityRule] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        path = f"$.visibility[{index}]"
        if not isinstance(item, dict):
            issues.append(StateValidationIssue("APP_VISIBILITY_RULE_NOT_OBJECT", path, "Each visibility rule must be an object."))
            continue
        extra = sorted(set(item) - {"id", "screen_id", "target_ref", "when"})
        if extra:
            issues.append(StateValidationIssue("APP_VISIBILITY_UNKNOWN_FIELD", path, f"Unsupported visibility field(s): {', '.join(extra)}."))
        missing = [key for key in ("id", "screen_id", "target_ref", "when") if key not in item]
        if missing:
            issues.append(StateValidationIssue("APP_VISIBILITY_FIELD_REQUIRED", path, f"Visibility rules require field(s): {', '.join(missing)}."))
        rule_id = item.get("id")
        if not isinstance(rule_id, str) or SAFE_STATE_ID_RE.fullmatch(rule_id) is None:
            if "id" in item:
                issues.append(StateValidationIssue("APP_VISIBILITY_INVALID_ID", f"{path}.id", f"Invalid visibility rule id {item.get('id')!r}."))
            rule_id = None
        screen_id = item.get("screen_id")
        if isinstance(screen_id, str) and screen_id not in screen_ids:
            issues.append(StateValidationIssue("APP_VISIBILITY_SCREEN_MISSING", f"{path}.screen_id", f"Visibility rule references missing screen {screen_id}."))
            screen_id = None
        elif not isinstance(screen_id, str):
            screen_id = None
        target_ref = item.get("target_ref")
        target_ok = isinstance(target_ref, str) and VISIBILITY_TARGET_REF_RE.fullmatch(target_ref) is not None
        if "target_ref" in item and not target_ok:
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_TARGET_REF_INVALID",
                    f"{path}.target_ref",
                    "target_ref must be region:<id>, binding:<id>, or motif:<id>.",
                )
            )
        if target_ok and screen_id is not None:
            kind, _, target_id = str(target_ref).partition(":")
            if target_id not in screen_target_ids.get(screen_id, {}).get(kind, set()):
                issues.append(
                    StateValidationIssue(
                        "APP_VISIBILITY_TARGET_MISSING",
                        f"{path}.target_ref",
                        f"Screen {screen_id} does not declare {kind} {target_id}.",
                    )
                )
            pair = (screen_id, str(target_ref))
            if pair in seen_targets:
                issues.append(
                    StateValidationIssue(
                        "APP_VISIBILITY_DUPLICATE_TARGET",
                        f"{path}.target_ref",
                        f"Screen {screen_id} already has a visibility rule for {target_ref}.",
                    )
                )
            seen_targets.add(pair)
        when = item.get("when")
        when_ok = _validate_visibility_condition(when, path, states_by_id, selectors_by_id, issues)
        if rule_id and screen_id is not None and target_ok and when_ok:
            rules.append(
                VisibilityRule(id=rule_id, screen_id=screen_id, target_ref=str(target_ref), when=copy.deepcopy(when))
            )
    return rules


def _validate_visibility_condition(
    when: Any,
    path: str,
    states_by_id: dict[str, StateEntry],
    selectors_by_id: dict[str, StateSelector],
    issues: list[StateValidationIssue],
) -> bool:
    if not isinstance(when, dict) or frozenset(when) not in _VISIBILITY_CONDITION_FORMS:
        if when is not None:
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_CONDITION_INVALID",
                    f"{path}.when",
                    "when must be exactly one of {state, is}, {state, equals}, or {selector, is}.",
                )
            )
        return False
    ok = True
    if "selector" in when:
        selector_id = when.get("selector")
        selector = selectors_by_id.get(selector_id) if isinstance(selector_id, str) else None
        if selector is None:
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_SELECTOR_MISSING",
                    f"{path}.when.selector",
                    f"Visibility condition references missing selector {selector_id!r}.",
                )
            )
            ok = False
        else:
            source = states_by_id.get(selector.source_state)
            if source is not None and source.kind not in {STATE_KIND_COLLECTION, STATE_KIND_SELECTION}:
                issues.append(
                    StateValidationIssue(
                        "APP_VISIBILITY_SELECTOR_SOURCE_UNSUPPORTED",
                        f"{path}.when.selector",
                        f"Selector {selector_id} sources {source.kind} state; selector conditions need collection or selection sources.",
                    )
                )
                ok = False
        if when.get("is") not in {"non_empty", "empty"}:
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_CONDITION_INVALID",
                    f"{path}.when.is",
                    "Selector conditions require is: non_empty or empty.",
                )
            )
            ok = False
        return ok
    state_id = when.get("state")
    state = states_by_id.get(state_id) if isinstance(state_id, str) else None
    if state is None:
        issues.append(
            StateValidationIssue(
                "APP_VISIBILITY_STATE_MISSING",
                f"{path}.when.state",
                f"Visibility condition references missing state {state_id!r}.",
            )
        )
        ok = False
    if "equals" in when:
        if state is not None and state.kind != STATE_KIND_SCALAR:
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_STATE_KIND_UNSUPPORTED",
                    f"{path}.when.state",
                    f"equals conditions need scalar state; {state_id} is {state.kind}.",
                )
            )
            ok = False
        equals = when.get("equals")
        if isinstance(equals, (dict, list)) or (isinstance(equals, str) and len(equals) > 2048):
            issues.append(
                StateValidationIssue(
                    "APP_VISIBILITY_EQUALS_NOT_SCALAR",
                    f"{path}.when.equals",
                    "equals must be a JSON scalar (string <= 2048 chars, number, boolean, or null).",
                )
            )
            ok = False
        return ok
    if state is not None and state.kind not in {STATE_KIND_SCALAR, STATE_KIND_SELECTION}:
        issues.append(
            StateValidationIssue(
                "APP_VISIBILITY_STATE_KIND_UNSUPPORTED",
                f"{path}.when.state",
                f"is conditions need scalar or selection state; {state_id} is {state.kind}.",
            )
        )
        ok = False
    if when.get("is") not in {"truthy", "falsy"}:
        issues.append(
            StateValidationIssue(
                "APP_VISIBILITY_CONDITION_INVALID",
                f"{path}.when.is",
                "State conditions require is: truthy or falsy.",
            )
        )
        ok = False
    return ok


def _apply_op(current_state: dict[str, Any], op: dict[str, Any], payload_values: dict[str, Any]) -> None:
    state_id = str(op.get("state") or "")
    if state_id not in current_state:
        raise ValueError(f"Operation targets missing state {state_id}.")
    kind = op.get("op")
    current = current_state[state_id]
    if kind == "set":
        current_state[state_id] = _resolve_expr(op.get("value"), payload_values)
    elif kind == "append":
        if not isinstance(current, list):
            raise TypeError(f"append requires array state {state_id}.")
        current_state[state_id] = [*current, _resolve_expr(op.get("value"), payload_values)]
    elif kind == "remove":
        item_id = _js_id_string(_resolve_expr(op.get("item_id"), payload_values))
        current_state[state_id] = [item for item in _require_list(current, "remove") if not (isinstance(item, dict) and _js_id_string(item.get("id")) == item_id)]
    elif kind == "move":
        item_id = _js_id_string(_resolve_expr(op.get("item_id"), payload_values))
        to_index = int(_resolve_expr(op.get("to_index"), payload_values))
        items = list(_require_list(current, "move"))
        from_index = next((index for index, item in enumerate(items) if isinstance(item, dict) and _js_id_string(item.get("id")) == item_id), -1)
        if from_index < 0:
            return
        item = items.pop(from_index)
        items.insert(max(0, min(to_index, len(items))), item)
        current_state[state_id] = items
    elif kind == "patch":
        patch = _resolve_expr(op.get("value"), payload_values)
        if not isinstance(patch, dict):
            raise TypeError("patch value must resolve to an object.")
        if "item_id" in op:
            item_id = _js_id_string(_resolve_expr(op.get("item_id"), payload_values))
            current_state[state_id] = [
                {**item, **patch} if isinstance(item, dict) and _js_id_string(item.get("id")) == item_id else item
                for item in _require_list(current, "patch")
            ]
        elif isinstance(current, dict):
            current_state[state_id] = {**current, **patch}
        else:
            raise TypeError("patch without item_id requires record state.")
    elif kind == "toggle":
        current_state[state_id] = _toggle(current, op, payload_values)
    elif kind == "increment":
        current_state[state_id] = _increment(current, op, payload_values)
    else:
        raise ValueError(f"Unsupported op {kind}.")


def _toggle(current: Any, op: dict[str, Any], payload_values: dict[str, Any]) -> Any:
    field = op.get("field")
    if "item_id" in op and isinstance(field, str):
        item_id = _js_id_string(_resolve_expr(op.get("item_id"), payload_values))
        return [
            {**item, field: not bool(item.get(field))} if isinstance(item, dict) and _js_id_string(item.get("id")) == item_id else item
            for item in _require_list(current, "toggle")
        ]
    if isinstance(field, str) and isinstance(current, dict):
        return {**current, field: not bool(current.get(field))}
    return not bool(current)


def _incr_number(value: Any) -> int | float:
    # Strict: a real JSON number only. bool is excluded (bool is an int subclass in
    # Python, but JS `typeof true !== "number"` rejects it, so both sides must reject to
    # stay conformant); strings / null / objects are rejected too, mirroring the strict JS
    # toFiniteNumber. Callers default a missing field to 0 before calling this, so only a
    # present non-number (incl. an explicit null) raises.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("increment requires a numeric value.")
    return value


def _increment(current: Any, op: dict[str, Any], payload_values: dict[str, Any]) -> Any:
    amount = _incr_number(_resolve_expr(op.get("amount", 1), payload_values))
    field = op.get("field")
    if "item_id" in op and isinstance(field, str):
        item_id = _js_id_string(_resolve_expr(op.get("item_id"), payload_values))
        return [
            {**item, field: _incr_number(item.get(field, 0)) + amount} if isinstance(item, dict) and _js_id_string(item.get("id")) == item_id else item
            for item in _require_list(current, "increment")
        ]
    if isinstance(field, str) and isinstance(current, dict):
        return {**current, field: _incr_number(current.get(field, 0)) + amount}
    return _incr_number(current) + amount


def _resolve_expr(value: Any, payload_values: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value) == {"from_payload"} and isinstance(value.get("from_payload"), str):
        return copy.deepcopy(payload_values.get(value["from_payload"]))
    return copy.deepcopy(value)


def _js_strict_eq(a: Any, b: Any) -> bool:
    # Mirror JS === for JSON scalars so the Python reference matches the generated reducer's
    # filter_eq (`item[field] === op.value`): bool is distinct from number, numbers compare by
    # value (1 === 1.0), strings/null by value, and containers are never equal (JS === is by
    # reference). Python's `==` treated `1 == True`, which diverged from the shipped reducer.
    a_bool, b_bool = isinstance(a, bool), isinstance(b, bool)
    if a_bool or b_bool:
        return a_bool and b_bool and a == b
    a_num, b_num = isinstance(a, (int, float)), isinstance(b, (int, float))
    if a_num or b_num:
        return a_num and b_num and a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if a is None or b is None:
        return a is None and b is None
    return False


def _js_id_string(value: Any) -> str:
    # Mirror JS String() for the id-match domain so str()/String() cannot drift: an
    # integer-valued float id (1.0) formats as "1" like String(1.0), not "1.0" like str(1.0).
    # str/int already match String(); bool and null are formatted the JS way for completeness.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and math.isfinite(value) else repr(value)
    if value is None:
        return "null"
    return str(value)


def _resource_view_values(app_payload: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    resources_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for resource in app_payload.get("resources", []) if isinstance(app_payload.get("resources"), list) else []:
        if isinstance(resource, dict) and isinstance(resource.get("id"), str):
            records = resource.get("records") if isinstance(resource.get("records"), list) else []
            resources_by_id[resource["id"]] = {
                str(record.get("id")): record
                for record in records
                if isinstance(record, dict) and isinstance(record.get("id"), str)
            }
    values: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for screen in app_payload.get("screens", []) if isinstance(app_payload.get("screens"), list) else []:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        screen_id = screen["id"]
        for view in screen.get("resource_views", []) if isinstance(screen.get("resource_views"), list) else []:
            if not isinstance(view, dict) or not isinstance(view.get("id"), str):
                continue
            records = resources_by_id.get(str(view.get("resource_id")), {})
            fields = [field for field in view.get("fields", []) if isinstance(field, str)]
            view_records: list[dict[str, Any]] = []
            for record_id in view.get("record_ids", []) if isinstance(view.get("record_ids"), list) else []:
                record = records.get(str(record_id))
                if isinstance(record, dict):
                    view_records.append({field: copy.deepcopy(record.get(field)) for field in fields if field in record})
            values[(screen_id, view["id"])] = view_records
    return values


def _screen_ids(app_payload: dict[str, Any]) -> set[str]:
    return {
        screen["id"]
        for screen in app_payload.get("screens", [])
        if isinstance(screen, dict) and isinstance(screen.get("id"), str)
    }


def _screen_action_payload_bindings(app_payload: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
    action_payloads: dict[tuple[str, str], set[str]] = {}
    for screen in app_payload.get("screens", []) if isinstance(app_payload.get("screens"), list) else []:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        view_spec = screen.get("intent_bundle", {}).get("view_spec") if isinstance(screen.get("intent_bundle"), dict) else {}
        actions = view_spec.get("actions") if isinstance(view_spec, dict) and isinstance(view_spec.get("actions"), list) else []
        for action in actions:
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                bindings = action.get("payload_bindings") if isinstance(action.get("payload_bindings"), list) else []
                action_payloads[(screen["id"], action["id"])] = {item for item in bindings if isinstance(item, str)}
    return action_payloads


def _screen_intent_target_ids(app_payload: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    """Declared region/binding/motif ids per screen — the namespaces visibility targets resolve in."""
    targets: dict[str, dict[str, set[str]]] = {}
    for screen in app_payload.get("screens", []) if isinstance(app_payload.get("screens"), list) else []:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        view_spec = screen.get("intent_bundle", {}).get("view_spec") if isinstance(screen.get("intent_bundle"), dict) else {}
        if not isinstance(view_spec, dict):
            view_spec = {}
        declared: dict[str, set[str]] = {}
        for kind, field_name in (("region", "regions"), ("binding", "bindings"), ("motif", "motifs")):
            items = view_spec.get(field_name) if isinstance(view_spec.get(field_name), list) else []
            declared[kind] = {item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)}
        targets[screen["id"]] = declared
    return targets


def _op_payload_refs(op: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("value", "item_id", "to_index", "amount"):
        value = op.get(key)
        if isinstance(value, dict) and set(value) == {"from_payload"} and isinstance(value.get("from_payload"), str):
            refs.add(value["from_payload"])
    return refs


def _resource_view_ids(app_payload: dict[str, Any]) -> set[tuple[str, str]]:
    ids: set[tuple[str, str]] = set()
    for screen in app_payload.get("screens", []) if isinstance(app_payload.get("screens"), list) else []:
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        for view in screen.get("resource_views", []) if isinstance(screen.get("resource_views"), list) else []:
            if isinstance(view, dict) and isinstance(view.get("id"), str):
                ids.add((screen["id"], view["id"]))
    return ids


def _required_array(obj: dict[str, Any], key: str, path: str, issues: list[StateValidationIssue]) -> list[Any]:
    value = obj.get(key)
    if isinstance(value, list):
        return value
    issues.append(StateValidationIssue("APP_STATE_FIELD_REQUIRED", f"{path}.{key}", f"Missing required array field {key}."))
    return []


def _required_string(obj: dict[str, Any], key: str, path: str, issues: list[StateValidationIssue]) -> str | None:
    value = obj.get(key)
    if isinstance(value, str) and value:
        return value
    issues.append(StateValidationIssue("APP_STATE_FIELD_REQUIRED", f"{path}.{key}", f"Missing required string field {key}."))
    return None


def _check_count(
    values: list[Any],
    limit: int,
    path: str,
    code: str,
    label: str,
    issues: list[StateValidationIssue],
) -> None:
    if len(values) > limit:
        issues.append(StateValidationIssue(code, path, f"AppBundle declares {len(values)} {label}; the V3 limit is {limit}."))


def _reject_extra(obj: dict[str, Any], allowed: set[str], path: str, issues: list[StateValidationIssue]) -> None:
    extra = sorted(set(obj) - allowed)
    if extra:
        issues.append(StateValidationIssue("APP_STATE_UNKNOWN_FIELD", path, f"Unsupported state field(s): {', '.join(extra)}."))


def _validate_safe_id(value: str | None, path: str, label: str, issues: list[StateValidationIssue]) -> None:
    if value is None:
        return
    if SAFE_STATE_ID_RE.fullmatch(value) is None:
        issues.append(StateValidationIssue("APP_STATE_INVALID_ID", path, f"Invalid {label} {value!r}."))


def _validate_unique(ids: list[str], path: str, code: str, issues: list[StateValidationIssue]) -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            issues.append(StateValidationIssue(code, path, f"Duplicate id {item_id}."))
        seen.add(item_id)


def _require_list(value: Any, op: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{op} requires array state.")
    return value


def _default_state_value(kind: str) -> Any:
    if kind in {STATE_KIND_COLLECTION, STATE_KIND_SELECTION}:
        return []
    if kind == STATE_KIND_RECORD:
        return {}
    return None


def _project_expected(values: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(values.get(key)) for key in expected}


def _expected_conformance_replays(app_payload: dict[str, Any], state_ir: StateIR) -> list[dict[str, Any]]:
    initial = initial_state(app_payload, state_ir)
    is_v4 = app_payload.get("schema_version") == 4
    replays: list[dict[str, Any]] = []
    for assertion in state_ir.replay_assertions:
        current = copy.deepcopy(initial)
        errors: list[dict[str, Any]] = []
        for event in assertion.events:
            result = apply_event(current, state_ir, event)
            if not result["ok"]:
                errors.extend(result["errors"])
                break
        selector_values = evaluate_selectors(current, state_ir)
        entry = {
            "id": assertion.id,
            "ok": not errors,
            "event_count": len(assertion.events),
            "state": current,
            "selectors": selector_values,
            "errors": errors,
            "error_codes": [str(error.get("code")) for error in errors if isinstance(error, dict)],
        }
        if is_v4:
            entry["visibility"] = evaluate_visibility(current, selector_values, state_ir)
        replays.append(entry)
    return replays


def _run_node_conformance(source: str, state_ir: StateIR, *, node_command: str) -> dict[str, Any]:
    input_payload = {
        "assertions": [
            {"id": assertion.id, "events": [copy.deepcopy(event) for event in assertion.events]}
            for assertion in state_ir.replay_assertions
        ],
        "exports": list(STATE_REDUCER_EXPORTS),
    }
    runner_source = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const reducerPath = process.argv[2];
const inputPath = process.argv[3];
const input = JSON.parse(readFileSync(inputPath, "utf8"));
const mod = await import(pathToFileURL(reducerPath).href);
const exportNames = Object.keys(mod).sort();
const replays = [];

for (const assertion of input.assertions || []) {
  let state = mod.initialState;
  const errors = [];
  for (const event of assertion.events || []) {
    try {
      state = mod.reduceViewSpecState(state, event);
    } catch (error) {
      errors.push({ code: error?.code || String(error?.message || error), message: String(error?.message || error) });
      break;
    }
  }
  let selectors = {};
  try {
    selectors = mod.selectViewSpecState(state);
  } catch (error) {
    errors.push({ code: "APP_STATE_REDUCER_CONFORMANCE_FAILED", message: String(error?.message || error) });
  }
  let visibility = null;
  try {
    if (typeof mod.evaluateViewSpecVisibility === "function") visibility = mod.evaluateViewSpecVisibility(state);
  } catch (error) {
    errors.push({ code: "APP_STATE_REDUCER_CONFORMANCE_FAILED", message: String(error?.message || error) });
  }
  replays.push({ id: assertion.id, ok: errors.length === 0, state, selectors, visibility, errors });
}

console.log(JSON.stringify({
  ok: true,
  profile: mod.VIEWSPEC_STATE_PROFILE,
  export_names: exportNames,
  replays
}));
""".lstrip()
    with tempfile.TemporaryDirectory(prefix="viewspec-state-") as tmp:
        tmp_path = Path(tmp)
        reducer_path = tmp_path / "state_reducer.mjs"
        runner_path = tmp_path / "state_runner.mjs"
        input_path = tmp_path / "state_input.json"
        reducer_path.write_text(source, encoding="utf-8")
        runner_path.write_text(runner_source, encoding="utf-8")
        input_path.write_text(_stable_json(input_payload), encoding="utf-8")
        try:
            completed = subprocess.run(
                [node_command, str(runner_path), str(reducer_path), str(input_path)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "export_names": [],
                "errors": [
                    {
                        "code": "APP_STATE_REDUCER_NODE_UNAVAILABLE",
                        "path": "$.interactive_state",
                        "message": f"Node.js runtime {node_command!r} was not found; V3 reducer conformance requires Node.js.",
                        "fix": "Install Node.js (>=18) on PATH for V3 reducer conformance, or use a V1/V2 AppBundle (no Node required).",
                    }
                ],
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "export_names": [],
                "errors": [_conformance_error("Node reducer conformance timed out.")],
            }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "export_names": [],
            "errors": [_conformance_error(f"Node reducer conformance exited with {completed.returncode}: {detail}")],
        }
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "export_names": [],
            "errors": [_conformance_error(f"Node reducer conformance returned invalid JSON: {exc}")],
        }
    return parsed if isinstance(parsed, dict) else {"ok": False, "errors": [_conformance_error("Node reducer conformance returned a non-object report.")]}


def _validate_event_payload(mutation: StateMutation, payload_values: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    unknown = sorted(set(payload_values) - set(mutation.allowed_payload_bindings))
    missing = sorted(payload_id for payload_id in mutation.required_payload_bindings if payload_id not in payload_values)
    if unknown:
        errors.append(
            {
                "code": "APP_STATE_EVENT_PAYLOAD_UNKNOWN",
                "path": f"$.mutations.{mutation.id}.trigger.payload_values",
                "message": f"State event payload includes undeclared binding(s): {', '.join(unknown)}.",
            }
        )
    if missing:
        errors.append(
            {
                "code": "APP_STATE_EVENT_PAYLOAD_MISSING",
                "path": f"$.mutations.{mutation.id}.trigger.payload_values",
                "message": f"State event payload is missing required binding(s): {', '.join(missing)}.",
            }
        )
    return errors


def _selector_sort_key(item: Any, field: str) -> tuple[int, int, Any, str]:
    if not isinstance(item, dict):
        return (1, 0, 0, "")
    return (0, *_sort_key_component(item.get(field)))


def _sort_key_component(value: Any) -> tuple[int, Any, str]:
    # Typed sort sub-key shared byte-for-byte with the generated JS reducer's
    # vsTypeRank/vsNum/vsStr. Heterogeneous JSON types are never stringified (that is
    # how Python str() and JS String(x ?? "") drifted for bool/null/number/object);
    # numbers compare numerically, so there is no float-repr string to disagree on.
    # The numeric/string slots are filled with 0/"" outside their bucket so every
    # element's key is a type-consistent 4-tuple (sorted() never compares str vs int).
    if value is None:
        return (0, 0, "")
    if value is True or value is False:  # bool before int: bool is an int subclass
        return (1, 1 if value else 0, "")
    if isinstance(value, (int, float)):
        return (2, value, "")
    if isinstance(value, str):
        return (3, 0, value)
    return (4, 0, "")  # dict / list / other JSON container


def _json_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_json_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_values_equal(left_item, right_item) for left_item, right_item in zip(left, right))
    if _is_json_number(left) and _is_json_number(right):
        return float(left) == float(right)
    return left == right


def _is_json_number(value: Any) -> bool:
    return type(value) in {int, float}


def _event_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "errors": [{"code": code, "path": "$.state_replay_assertions.events", "message": message}]}


def _conformance_error(message: str) -> dict[str, str]:
    return {
        "code": "APP_STATE_REDUCER_CONFORMANCE_FAILED",
        "path": "$.interactive_state",
        "message": message,
    }


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_text(_stable_json(value))


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


__all__ = [
    "APP_STATE_MAX_ENTRIES",
    "APP_STATE_MAX_EVENTS_PER_REPLAY",
    "APP_STATE_MAX_MANIFEST_BYTES",
    "APP_STATE_MAX_MUTATIONS",
    "APP_STATE_MAX_OPS_PER_MUTATION",
    "APP_STATE_MAX_REDUCER_BYTES",
    "APP_STATE_MAX_REPLAY_ASSERTIONS",
    "APP_STATE_MAX_SELECTOR_OPS",
    "APP_STATE_MAX_SELECTORS",
    "INTERACTIVE_STATE_PROFILE",
    "NormalizedStateIR",
    "STATE_MANIFEST_SCHEMA_VERSION",
    "STATE_REDUCER_EXPORTS",
    "StateEntry",
    "StateIR",
    "StateMutation",
    "StateReplayAssertion",
    "StateSelector",
    "StateValidationIssue",
    "check_reducer_conformance",
    "evaluate_selectors",
    "generate_typescript_reducer",
    "initial_state",
    "normalize_state_ir",
    "replay_state_assertions",
    "state_contract_hash",
    "state_event_schemas",
    "state_ir_summary",
    "state_manifest",
    "validate_state_ir",
]
