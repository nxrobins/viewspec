"""Lossless fixes, the lossless/lossy classification, and the repair-loop prompt section.

Every lossless fix is applied before validation, reported with its path, idempotent, and leaves a
document that passes check and compiles to the same output as a document written correctly. Every
lossy code is never fixed: ``fixes`` stays empty and ``ok`` stays false.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from viewspec import compile
from viewspec.agent import (
    AGENT_FIX_CODES,
    AGENT_SYSTEM_PROMPT,
    ISSUE_CODE_CLASSIFICATION,
    LOSSLESS_FIXABLE_ISSUE,
    LOSSY_ISSUE,
    MAX_AGENT_INTENT_BYTES,
    PRESENT_AS_ALIASES,
    REPAIR_LOOP_PROMPT_SECTION,
    AgentValidationResult,
    agent_correction_prompt,
    apply_lossless_fixes,
    classify_issue_code,
    validate_agent_intent_bundle,
)
from viewspec.compiler import MAX_STATE_MOTIFS, CompilerInputError
from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import check_intent_source, starter_intent_payload
from viewspec.types import PRESENT_AS_TO_PRIMITIVE, IntentBundle

ROOT = Path(__file__).resolve().parents[1]
AGENT_SOURCE = ROOT / "src" / "viewspec" / "agent.py"
COMPILER_SOURCES = (ROOT / "src" / "viewspec" / "compiler.py", ROOT / "src" / "viewspec" / "motif_compilers.py")
TOKEN_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")


def _starter(kind: str = "dashboard") -> dict[str, Any]:
    return starter_intent_payload(kind)


def _compiled(document: dict[str, Any]) -> dict[str, Any]:
    return compile(IntentBundle.from_json(document)).to_json()


def _assert_lossless(original: dict[str, Any], expected: list[tuple[str, str]]) -> dict[str, Any]:
    """The fix list, idempotence, a clean check, and an untouched input; returns the fixed document."""
    before = deepcopy(original)
    fixed, fixes = apply_lossless_fixes(original)

    assert original == before, "apply_lossless_fixes must not mutate its input"
    assert [(fix.code, fix.path) for fix in fixes] == expected
    assert all(fix.lossy is False and fix.message and fix.code in ERROR_CODES for fix in fixes)
    again, more = apply_lossless_fixes(fixed)
    assert again == fixed and more == []
    assert apply_lossless_fixes(original) == (fixed, fixes), "the same input gives the same output"

    result = validate_agent_intent_bundle(original)
    assert result.valid, [issue.to_json() for issue in result.issues]
    assert [(fix.code, fix.path) for fix in result.fixes] == expected
    assert result.document == fixed
    assert validate_agent_intent_bundle(fixed).fixes == []
    return fixed


# One test per lossless fix --------------------------------------------------------------------


def test_missing_schema_version_is_added_without_changing_the_compiled_output() -> None:
    complete = _starter()
    document = deepcopy(complete)
    del document["schema_version"]

    fixed = _assert_lossless(document, [("FIX_SCHEMA_VERSION_ADDED", "$.schema_version")])

    assert fixed == complete
    assert _compiled(fixed) == _compiled(document)


def test_missing_slots_and_edges_maps_are_added_without_changing_the_compiled_output() -> None:
    complete = _starter()
    document = deepcopy(complete)
    del document["substrate"]["nodes"]["starter_dashboard"]["slots"]
    del document["substrate"]["nodes"]["revenue"]["edges"]
    # Fixes follow the document's node order, whatever that order is.
    dropped = {"starter_dashboard": "slots", "revenue": "edges"}
    expected = [
        ("FIX_EMPTY_RELATIONS_ADDED", f"$.substrate.nodes.{node}.{dropped[node]}")
        for node in document["substrate"]["nodes"]
        if node in dropped
    ]

    fixed = _assert_lossless(document, expected)

    assert fixed == complete
    # The compiler already reads an absent map as empty, so the fix is provably lossless.
    assert _compiled(fixed) == _compiled(document)


@pytest.mark.parametrize("parent", ["missing", None], ids=["missing", "null"])
def test_root_region_parent_is_set_to_the_empty_string(parent: object) -> None:
    complete = _starter()
    document = deepcopy(complete)
    if parent == "missing":
        del document["view_spec"]["regions"][0]["parent_region"]
    else:
        document["view_spec"]["regions"][0]["parent_region"] = None

    fixed = _assert_lossless(document, [("FIX_ROOT_PARENT_REGION_SET", "$.view_spec.regions[0].parent_region")])

    assert fixed == complete
    assert _compiled(fixed) == _compiled(document)


def test_root_region_with_a_real_parent_is_not_fixed() -> None:
    document = _starter()
    document["view_spec"]["regions"][0]["parent_region"] = "main"

    result = validate_agent_intent_bundle(document)

    assert result.fixes == []
    assert "ROOT_REGION_HAS_PARENT" in {issue.code for issue in result.issues}


def test_fields_one_level_too_high_or_too_low_move_to_their_only_legal_position() -> None:
    complete = _starter()
    document = deepcopy(complete)
    document["regions"] = document["view_spec"].pop("regions")
    document["root_id"] = document["substrate"].pop("root_id")
    document["view_spec"]["schema_version"] = document.pop("schema_version")

    fixed = _assert_lossless(
        document,
        [
            ("FIX_FIELD_RELOCATED", "$.view_spec.regions"),
            ("FIX_FIELD_RELOCATED", "$.substrate.root_id"),
            ("FIX_FIELD_RELOCATED", "$.schema_version"),
        ],
    )

    assert fixed == complete


def test_a_field_that_already_exists_at_its_legal_position_is_not_relocated() -> None:
    document = _starter()
    document["regions"] = []  # view_spec.regions is still there: moving would overwrite it.
    document["id"] = "ambiguous"  # legal in substrate and in view_spec, so it has no single position.

    result = validate_agent_intent_bundle(document)

    assert result.fixes == []
    assert {(issue.code, issue.path) for issue in result.issues} == {
        ("UNKNOWN_FIELD", "$.regions"),
        ("UNKNOWN_FIELD", "$.id"),
    }


@pytest.mark.parametrize(("alias", "canonical"), sorted(PRESENT_AS_ALIASES.items()))
def test_present_as_aliases_are_spelled_canonically(alias: str, canonical: str) -> None:
    complete = _starter()
    index = next(i for i, binding in enumerate(complete["view_spec"]["bindings"]) if binding["id"] == "revenue_value")
    complete["view_spec"]["bindings"][index]["present_as"] = canonical
    document = deepcopy(complete)
    document["view_spec"]["bindings"][index]["present_as"] = alias

    fixed = _assert_lossless(document, [("FIX_PRESENT_AS_ALIAS", f"$.view_spec.bindings[{index}].present_as")])

    assert fixed == complete
    assert _compiled(fixed) == _compiled(complete)


def test_the_alias_table_is_closed_and_canonical() -> None:
    assert not set(PRESENT_AS_ALIASES) & set(PRESENT_AS_TO_PRIMITIVE)
    assert set(PRESENT_AS_ALIASES.values()) <= set(PRESENT_AS_TO_PRIMITIVE)
    assert "chart" not in PRESENT_AS_ALIASES  # an unknown present_as stays an UNKNOWN_PRESENT_AS issue


def test_all_fixes_compose_in_document_order_and_fix_false_reports_them_as_issues() -> None:
    document = _starter()
    del document["schema_version"]
    del document["substrate"]["nodes"]["users"]["edges"]
    document["view_spec"]["regions"][0]["parent_region"] = None
    document["view_spec"]["bindings"][1]["present_as"] = "metric"
    document["actions"] = document["view_spec"].pop("actions")

    fixed = _assert_lossless(
        document,
        [
            ("FIX_FIELD_RELOCATED", "$.view_spec.actions"),
            ("FIX_SCHEMA_VERSION_ADDED", "$.schema_version"),
            ("FIX_EMPTY_RELATIONS_ADDED", "$.substrate.nodes.users.edges"),
            ("FIX_ROOT_PARENT_REGION_SET", "$.view_spec.regions[0].parent_region"),
            ("FIX_PRESENT_AS_ALIAS", "$.view_spec.bindings[1].present_as"),
        ],
    )
    unfixed = validate_agent_intent_bundle(document, fix=False)

    assert fixed == _starter()
    assert not unfixed.valid
    assert unfixed.fixes == []
    assert {issue.code for issue in unfixed.issues} == {"UNKNOWN_FIELD", "MISSING_FIELD", "UNKNOWN_PRESENT_AS"}
    assert unfixed.to_json()["fixes"] == []
    assert validate_agent_intent_bundle(json.dumps(document)).to_json()["fixes"][0] == {
        "code": "FIX_FIELD_RELOCATED",
        "path": "$.view_spec.actions",
        "message": "Moved actions from $ to $.view_spec, its only legal position.",
        "lossy": False,
    }


def test_fixes_never_apply_to_a_document_that_is_not_an_intent_bundle() -> None:
    for payload in ({}, {"primitive": "root", "children": []}, {"substrate": {}}, {"view_spec": {}}):
        assert apply_lossless_fixes(payload) == (payload, [])


# The correction prompt --------------------------------------------------------------------------


def test_correction_prompt_lists_only_remaining_issues_and_names_the_applied_fixes() -> None:
    document = _starter()
    del document["schema_version"]
    document["view_spec"]["bindings"][0]["present_as"] = "chart"

    result = validate_agent_intent_bundle(document)
    prompt = agent_correction_prompt(result)
    report = json.loads(prompt.splitlines()[-1])

    assert not result.valid
    assert "already applied; fix only the remaining issues" in prompt
    assert report["issue_codes"] == ["UNKNOWN_PRESENT_AS"]
    assert report["applied_fix_count"] == 1
    assert report["applied_fixes"] == [{"code": "FIX_SCHEMA_VERSION_ADDED", "path": "$.schema_version"}]
    assert report["patch_command"] == "viewspec check viewspec.intent.json --patch OPS.json --json"
    assert report["repair_mode"] == "regenerate_full_intent_bundle"
    assert "FIX_SCHEMA_VERSION_ADDED" not in report["issue_codes"]

    plain = agent_correction_prompt(validate_agent_intent_bundle(_starter() | {"view_spec": {}}))
    assert "already applied" not in plain
    assert json.loads(plain.splitlines()[-1])["applied_fixes"] == []


# The repair-loop prompt section -----------------------------------------------------------------


def test_repair_loop_prompt_section_is_bounded_and_says_what_the_loop_does() -> None:
    assert len(REPAIR_LOOP_PROMPT_SECTION.encode("utf-8")) <= 800
    for phrase in (
        "lossless",
        "fixes",
        "lossy",
        "regenerating the full IntentBundle",
        "JSON Patch",
        "--patch",
        "--no-fix",
    ):
        assert phrase in REPAIR_LOOP_PROMPT_SECTION
    assert "never fixes a lossy problem" in REPAIR_LOOP_PROMPT_SECTION
    # The generated system prompt carries the section verbatim (deliberate pin move: it was absent).
    assert REPAIR_LOOP_PROMPT_SECTION.strip() in AGENT_SYSTEM_PROMPT
    assert not TOKEN_RE.findall(REPAIR_LOOP_PROMPT_SECTION)


# The classification table -----------------------------------------------------------------------


def _codes_in(path: Path, *, skip_assignments: set[str] = frozenset(), skip_prompts: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped: set[int] = set()
    for statement in tree.body:
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        if any(isinstance(target, ast.Name) and target.id in skip_assignments for target in targets):
            skipped.update(id(node) for node in ast.walk(statement))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skipped or not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if skip_prompts and node.value in (AGENT_SYSTEM_PROMPT, REPAIR_LOOP_PROMPT_SECTION):
            continue
        codes.update(token for token in TOKEN_RE.findall(node.value) if token in ERROR_CODES)
    return codes


def _validator_codes() -> set[str]:
    return _codes_in(AGENT_SOURCE, skip_assignments={"ISSUE_CODE_CLASSIFICATION"}, skip_prompts=True) - set(
        AGENT_FIX_CODES
    )


def test_every_code_the_validator_emits_is_classified() -> None:
    validator_codes = _validator_codes()
    compiler_codes = set().union(*(_codes_in(path) for path in COMPILER_SOURCES))

    assert validator_codes, "the scan must find the validator's codes"
    assert validator_codes <= set(ISSUE_CODE_CLASSIFICATION), sorted(validator_codes - set(ISSUE_CODE_CLASSIFICATION))
    assert compiler_codes <= set(ISSUE_CODE_CLASSIFICATION), sorted(compiler_codes - set(ISSUE_CODE_CLASSIFICATION))
    assert set(ISSUE_CODE_CLASSIFICATION) <= ERROR_CODES
    assert set(ISSUE_CODE_CLASSIFICATION.values()) == {LOSSLESS_FIXABLE_ISSUE, LOSSY_ISSUE}
    assert not set(ISSUE_CODE_CLASSIFICATION) & set(AGENT_FIX_CODES)


def test_only_the_three_relocatable_shapes_are_lossless_fixable_and_unknown_codes_are_lossy() -> None:
    fixable = {code for code, kind in ISSUE_CODE_CLASSIFICATION.items() if kind == LOSSLESS_FIXABLE_ISSUE}

    assert fixable == {"MISSING_FIELD", "UNKNOWN_FIELD", "UNKNOWN_PRESENT_AS"}
    assert classify_issue_code("MISSING_FIELD") == LOSSLESS_FIXABLE_ISSUE
    assert classify_issue_code("DETACHED_REGION") == LOSSY_ISSUE
    assert classify_issue_code("NOT_A_REAL_CODE_AT_ALL") == LOSSY_ISSUE


# Every lossy code: fixes stay empty and ok stays false ------------------------------------------


def _with(kind: str, mutate: Callable[[dict[str, Any]], None]) -> Callable[[Any], dict[str, Any]]:
    def recipe(_monkeypatch: Any) -> dict[str, Any]:
        document = _starter(kind)
        mutate(document)
        return document

    return recipe


def _dashboard(mutate: Callable[[dict[str, Any]], None]) -> Callable[[Any], dict[str, Any]]:
    return _with("dashboard", mutate)


def _action(**overrides: Any) -> dict[str, Any]:
    action = {
        "id": "extra_action",
        "kind": "select",
        "label": "Extra",
        "target_region": "main",
        "target_ref": None,
        "payload_bindings": [],
    }
    action.update(overrides)
    return action


def _region(region_id: str, parent: str | None) -> dict[str, Any]:
    return {
        "id": region_id,
        "parent_region": parent,
        "role": "section",
        "layout": "stack",
        "min_children": 0,
        "max_children": None,
    }


def _drop_members(document: dict[str, Any], suffix: str, keep: bool = False) -> None:
    motif = document["view_spec"]["motifs"][0]
    motif["members"] = [member for member in motif["members"] if member.endswith(suffix) is keep]


def _many(prefix: str, count: int, template: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**template, "id": f"{prefix}_{index}"} for index in range(count)]


def _raise_compiler_input_error(monkeypatch: Any) -> dict[str, Any]:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise CompilerInputError("SemanticSubstrate.root_id is required.")

    monkeypatch.setattr("viewspec.agent.compile", broken)
    return _starter()


def _too_many_state_motifs(document: dict[str, Any]) -> None:
    state = _starter("loading_state")
    document["substrate"]["nodes"].update(state["substrate"]["nodes"])
    document["view_spec"]["bindings"].extend(state["view_spec"]["bindings"])
    motif = state["view_spec"]["motifs"][0]
    document["view_spec"]["motifs"].extend({**motif, "id": f"state_{index}"} for index in range(MAX_STATE_MOTIFS + 1))


def _collection_state_conflict(document: dict[str, Any]) -> None:
    state = _starter("loading_state")
    document["substrate"]["nodes"].update(state["substrate"]["nodes"])
    document["view_spec"]["bindings"].extend(state["view_spec"]["bindings"])
    document["view_spec"]["groups"].extend(state["view_spec"]["groups"])
    document["view_spec"]["motifs"].extend(state["view_spec"]["motifs"])


def _too_many_descriptions(document: dict[str, Any]) -> None:
    document["substrate"]["nodes"]["loading_results"]["attrs"]["message"] = "Second description"
    document["view_spec"]["bindings"].append(
        {
            "id": "loading_results_message",
            "address": "node:loading_results#attr:message",
            "target_region": "main",
            "present_as": "text",
            "cardinality": "exactly_once",
        }
    )
    document["view_spec"]["motifs"][0]["members"].append("loading_results_message")


def _ambiguous_style_target(document: dict[str, Any]) -> None:
    document["substrate"]["nodes"]["style_source"] = {
        "id": "style_source",
        "kind": "metric",
        "attrs": {"label": "Style source"},
        "slots": {},
        "edges": {},
    }
    document["view_spec"]["bindings"].append(
        {
            "id": "main",
            "address": "node:style_source#attr:label",
            "target_region": "main",
            "present_as": "label",
            "cardinality": "exactly_once",
        }
    )
    document["view_spec"]["styles"].append({"id": "ambiguous_style", "target": "main", "token": "tone.muted"})


def _bulk_selection_ambiguous(document: dict[str, Any]) -> None:
    document["substrate"]["nodes"]["alpha"]["attrs"]["selected"] = False
    document["view_spec"]["bindings"].append(
        {
            "id": "items_selection",
            "address": "node:alpha#attr:selected",
            "target_region": "main",
            "present_as": "input",
            "cardinality": "exactly_once",
        }
    )
    document["view_spec"]["actions"].append(
        _action(kind="bulk_action", target_ref="motif:items", payload_bindings=["items_selection", "alpha_label"])
    )


def _style(style_id: str, target: str, token: str) -> dict[str, str]:
    return {"id": style_id, "target": target, "token": token}


def _aesthetic(document: dict[str, Any], *styles: dict[str, str]) -> None:
    document["view_spec"]["styles"].extend(styles)


LOSSY_RECIPES: dict[str, Callable[[Any], dict[str, Any] | str]] = {
    "AESTHETIC_PROFILE_MULTIPLE": _dashboard(
        lambda d: _aesthetic(
            d,
            _style("a", "view:starter_dashboard", "aesthetic.calm_ops"),
            _style("b", "view:starter_dashboard", "aesthetic.data_dense"),
        )
    ),
    "AESTHETIC_PROFILE_TARGET_INVALID": _dashboard(
        lambda d: _aesthetic(d, _style("a", "motif:metrics", "aesthetic.calm_ops"))
    ),
    "AESTHETIC_PROFILE_UNKNOWN": _dashboard(
        lambda d: _aesthetic(d, _style("a", "view:starter_dashboard", "aesthetic.noir"))
    ),
    "AMBIGUOUS_STYLE_TARGET": _dashboard(_ambiguous_style_target),
    "COLLECTION_ACTION_PAYLOAD_REQUIRED": _with(
        "table", lambda d: d["view_spec"]["actions"].append(_action(kind="search", target_ref="motif:items"))
    ),
    "COLLECTION_ACTION_TARGET_INVALID": _with(
        "table",
        lambda d: d["view_spec"]["actions"].append(
            _action(kind="filter", target_ref="motif:missing", payload_bindings=["alpha_label"])
        ),
    ),
    "COLLECTION_BULK_SELECTION_AMBIGUOUS": _with("table", _bulk_selection_ambiguous),
    "COLLECTION_BULK_SELECTION_REQUIRED": _with(
        "table",
        lambda d: d["view_spec"]["actions"].append(
            _action(kind="bulk_action", target_ref="motif:items", payload_bindings=["alpha_label"])
        ),
    ),
    "COLLECTION_STATE_CONFLICT": _with("table", _collection_state_conflict),
    "COMPILER_INPUT_ERROR": _raise_compiler_input_error,
    "COMPOSITION_IR_INPUT": lambda _m: {"id": "root", "primitive": "root", "children": []},
    "DETACHED_REGION": _dashboard(lambda d: d["view_spec"]["regions"].append(_region("aside", ""))),
    "DUPLICATE_ACTION_ID": _dashboard(lambda d: d["view_spec"]["actions"].extend([_action(), _action()])),
    "DUPLICATE_BINDING_ID": _dashboard(
        lambda d: d["view_spec"]["bindings"].append(deepcopy(d["view_spec"]["bindings"][0]))
    ),
    "DUPLICATE_EXACTLY_ONCE_ADDRESS": _dashboard(
        lambda d: d["view_spec"]["bindings"].append({**d["view_spec"]["bindings"][0], "id": "second_copy"})
    ),
    "DUPLICATE_GROUP_ID": _dashboard(lambda d: d["view_spec"]["groups"].append(deepcopy(d["view_spec"]["groups"][0]))),
    "DUPLICATE_MOTIF_ID": _dashboard(lambda d: d["view_spec"]["motifs"].append(deepcopy(d["view_spec"]["motifs"][0]))),
    "DUPLICATE_REGION_ID": _dashboard(
        lambda d: d["view_spec"]["regions"].append(deepcopy(d["view_spec"]["regions"][1]))
    ),
    "DUPLICATE_STYLE_ID": _dashboard(
        lambda d: _aesthetic(
            d, _style("s", "binding:revenue_value", "tone.muted"), _style("s", "binding:users_value", "tone.muted")
        )
    ),
    "EDGE_VALUES_SHAPE": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("edges", {"next": ["users"]})
    ),
    "EMPTY_MOTIF": _dashboard(lambda d: d["view_spec"]["motifs"][0].__setitem__("members", [])),
    "INTEGER_OUT_OF_SAFE_RANGE": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"]["attrs"].__setitem__("big", 2**53 + 1)
    ),
    "INTENT_BUNDLE_PARSE_ERROR": _dashboard(lambda d: d["view_spec"].__setitem__("complexity_tier", 2**63)),
    "INTENT_TOO_LARGE": lambda _m: " " * (MAX_AGENT_INTENT_BYTES + 1),
    "INVALID_ACTION": _dashboard(lambda d: d["view_spec"].__setitem__("actions", [1])),
    "INVALID_ACTION_TARGET_REF": _dashboard(
        lambda d: d["view_spec"]["actions"].append(_action(target_ref="revenue_value"))
    ),
    "INVALID_ADDRESS": _dashboard(
        lambda d: d["view_spec"]["bindings"][0].__setitem__("address", "node:missing#attr:value")
    ),
    "INVALID_BINDING": _dashboard(lambda d: d["view_spec"].__setitem__("bindings", [1])),
    "INVALID_COMPLEXITY_TIER": _dashboard(lambda d: d["view_spec"].__setitem__("complexity_tier", 0)),
    "INVALID_GROUP": _dashboard(lambda d: d["view_spec"].__setitem__("groups", [1])),
    "INVALID_ID": _dashboard(lambda d: d["view_spec"].__setitem__("id", "bad/view")),
    "INVALID_JSON": lambda _m: "{",
    "INVALID_JSON_VALUE": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"]["attrs"].__setitem__("bad", object())
    ),
    "INVALID_MOTIF": _dashboard(lambda d: d["view_spec"].__setitem__("motifs", [1])),
    "INVALID_NODE": _dashboard(lambda d: d["substrate"]["nodes"].__setitem__("broken", 1)),
    "INVALID_PAYLOAD": lambda _m: "[]",
    "INVALID_REGION": _dashboard(lambda d: d["view_spec"].__setitem__("regions", [1])),
    "INVALID_REGION_CHILD_BOUNDS": _dashboard(lambda d: d["view_spec"]["regions"][0].__setitem__("min_children", -1)),
    "INVALID_STYLE": _dashboard(lambda d: d["view_spec"].__setitem__("styles", [1])),
    "MISSING_GROUP_MEMBER": _dashboard(lambda d: d["view_spec"]["groups"][0]["members"].append("missing")),
    "MISSING_MOTIF_MEMBER": _dashboard(lambda d: d["view_spec"]["motifs"][0]["members"].append("missing")),
    "MISSING_ROOT_REGION": _dashboard(lambda d: d["view_spec"].__setitem__("root_region", "missing")),
    "MISSING_SUBSTRATE_ROOT": _dashboard(lambda d: d["substrate"].__setitem__("root_id", "missing")),
    "MOTIF_MISSING_INPUT": _with("form", lambda d: _drop_members(d, "_value")),
    "MOTIF_MISSING_LABEL": _dashboard(lambda d: _drop_members(d, "_value", keep=True)),
    "MOTIF_MISSING_TITLE": _with("hero", lambda d: _drop_members(d, "_title")),
    "MOTIF_MISSING_VALUE": _dashboard(lambda d: _drop_members(d, "_label", keep=True)),
    "MOTIF_TOO_FEW_ITEMS": _with(
        "comparison", lambda d: _drop_members(d, "_label", keep=True) or _drop_members(d, "standard_label", keep=True)
    ),
    "NODES_MUST_BE_OBJECT": _dashboard(
        lambda d: d["substrate"].__setitem__("nodes", list(d["substrate"]["nodes"].values()))
    ),
    "NODE_KEY_MISMATCH": _dashboard(
        lambda d: d["substrate"]["nodes"].__setitem__("other", deepcopy(d["substrate"]["nodes"]["revenue"]))
    ),
    "REGION_PARENT_CYCLE": _dashboard(
        lambda d: d["view_spec"]["regions"].extend([_region("left", "right"), _region("right", "left")])
    ),
    "ROOT_REGION_HAS_PARENT": _dashboard(lambda d: d["view_spec"]["regions"][0].__setitem__("parent_region", "main")),
    "SLOT_VALUES_SHAPE": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("slots", {"items": ["x"]})
    ),
    "STATE_MOTIF_TITLE_REQUIRED": _with("loading_state", lambda d: _drop_members(d, "_title")),
    "STATE_MOTIF_TOO_MANY_DESCRIPTIONS": _with("loading_state", _too_many_descriptions),
    "SUBSTRATE_ID_MISMATCH": _dashboard(lambda d: d["view_spec"].__setitem__("substrate_id", "other")),
    "TOO_MANY_ACTIONS": _dashboard(lambda d: d["view_spec"].__setitem__("actions", _many("a", 65, _action()))),
    "TOO_MANY_ACTION_PAYLOAD_BINDINGS": _dashboard(
        lambda d: d["view_spec"]["actions"].append(_action(payload_bindings=[f"b_{i}" for i in range(65)]))
    ),
    "TOO_MANY_BINDINGS": _dashboard(
        lambda d: d["view_spec"].__setitem__("bindings", _many("b", 401, d["view_spec"]["bindings"][0]))
    ),
    "TOO_MANY_COLLECTION_ACTIONS": _with(
        "table",
        lambda d: d["view_spec"]["actions"].extend(
            _many("search", 9, _action(kind="search", target_ref="motif:items", payload_bindings=["alpha_label"]))
        ),
    ),
    "TOO_MANY_GROUPS": _dashboard(
        lambda d: d["view_spec"].__setitem__("groups", _many("g", 65, d["view_spec"]["groups"][0]))
    ),
    "TOO_MANY_GROUP_MEMBERS": _dashboard(
        lambda d: d["view_spec"]["groups"][0].__setitem__("members", [f"m_{i}" for i in range(401)])
    ),
    "TOO_MANY_MOTIFS": _dashboard(
        lambda d: d["view_spec"].__setitem__("motifs", _many("m", 33, d["view_spec"]["motifs"][0]))
    ),
    "TOO_MANY_MOTIF_MEMBERS": _dashboard(
        lambda d: d["view_spec"]["motifs"][0].__setitem__("members", [f"m_{i}" for i in range(401)])
    ),
    "TOO_MANY_NODES": _dashboard(
        lambda d: d["substrate"]["nodes"].update(
            {f"n_{i}": {"id": f"n_{i}", "kind": "item", "attrs": {}, "slots": {}, "edges": {}} for i in range(201)}
        )
    ),
    "TOO_MANY_NODE_ATTRS": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"]["attrs"].update({f"a_{i}": i for i in range(65)})
    ),
    "TOO_MANY_NODE_EDGES": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("edges", {f"e_{i}": {"values": []} for i in range(65)})
    ),
    "TOO_MANY_NODE_SLOTS": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("slots", {f"s_{i}": {"values": []} for i in range(65)})
    ),
    "TOO_MANY_REGIONS": _dashboard(
        lambda d: d["view_spec"]["regions"].extend(_region(f"r_{i}", "root") for i in range(33))
    ),
    "TOO_MANY_RELATION_VALUES": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("slots", {"items": {"values": list(range(201))}})
    ),
    "TOO_MANY_STATE_MOTIFS": _with("table", _too_many_state_motifs),
    "TOO_MANY_STYLES": _dashboard(
        lambda d: d["view_spec"].__setitem__(
            "styles", _many("s", 401, _style("s", "binding:revenue_value", "tone.muted"))
        )
    ),
    "UNKNOWN_ACTION_PAYLOAD_BINDING": _dashboard(
        lambda d: d["view_spec"]["actions"].append(_action(payload_bindings=["missing"]))
    ),
    "UNKNOWN_ACTION_TARGET": _dashboard(lambda d: d["view_spec"]["actions"].append(_action(target_region="missing"))),
    "UNKNOWN_EDGE_TARGET": _dashboard(
        lambda d: d["substrate"]["nodes"]["revenue"].__setitem__("edges", {"next": {"values": ["missing_node"]}})
    ),
    "UNKNOWN_REGION": _dashboard(lambda d: d["view_spec"]["bindings"][0].__setitem__("target_region", "missing")),
    "UNKNOWN_STYLE_TARGET": _dashboard(lambda d: _aesthetic(d, _style("s", "binding:missing", "tone.muted"))),
    "UNSUPPORTED_ACTION_KIND": _dashboard(
        lambda d: d["view_spec"]["actions"].append(_action(kind="delete_everything"))
    ),
    "UNSUPPORTED_CARDINALITY": _dashboard(
        lambda d: d["view_spec"]["bindings"][0].__setitem__("cardinality", "optional")
    ),
    "UNSUPPORTED_GROUP_KIND": _dashboard(lambda d: d["view_spec"]["groups"][0].__setitem__("kind", "unordered")),
    "UNSUPPORTED_MOTIF": _dashboard(lambda d: d["view_spec"]["motifs"][0].__setitem__("kind", "chat")),
    "UNSUPPORTED_REGION_LAYOUT": _dashboard(lambda d: d["view_spec"]["regions"][1].__setitem__("layout", "masonry")),
    "UNSUPPORTED_SCHEMA_VERSION": _dashboard(lambda d: d.__setitem__("schema_version", 2)),
    "UNSUPPORTED_STYLE_TOKEN": _dashboard(
        lambda d: _aesthetic(d, _style("s", "binding:revenue_value", "css.position.fixed"))
    ),
}

# Codes the check command reports for the file or the patch rather than for the document.
FILE_RECIPES: dict[str, Callable[[Path], dict[str, Any]]] = {
    "INTENT_FILE_NOT_FOUND": lambda tmp: check_intent_source(tmp / "missing.intent.json"),
    "INTENT_FILE_READ_ERROR": lambda tmp: check_intent_source(tmp),
    "PATCH_APPLY_FAILED": lambda tmp: _check_patched(tmp, [{"op": "remove", "path": "/view_spec/nope"}]),
    "PATCH_INVALID": lambda tmp: _check_patched(tmp, [{"op": "frob", "path": "/x"}]),
    "PATCH_RESULT_TOO_LARGE": lambda tmp: _check_patched(
        tmp, [{"op": "add", "path": "/view_spec/blob", "value": "x" * MAX_AGENT_INTENT_BYTES}]
    ),
    "PATCH_TARGET_FORBIDDEN": lambda tmp: check_intent_source(tmp, patch=[]),
    "PATCH_TEST_FAILED": lambda tmp: _check_patched(tmp, [{"op": "test", "path": "/schema_version", "value": 2}]),
    "PATCH_TOO_MANY_OPERATIONS": lambda tmp: _check_patched(tmp, [{"op": "add", "path": "/x", "value": 1}] * 257),
}


def _check_patched(tmp: Path, operations: list[dict[str, Any]]) -> dict[str, Any]:
    source = tmp / "viewspec.intent.json"
    source.write_text(json.dumps(_starter()), encoding="utf-8")
    return check_intent_source(source, patch=operations)


def test_every_lossy_validator_code_has_a_recipe() -> None:
    lossy = {code for code in _validator_codes() if ISSUE_CODE_CLASSIFICATION[code] == LOSSY_ISSUE}
    file_level = {code for code in ISSUE_CODE_CLASSIFICATION if code.startswith(("INTENT_FILE_", "PATCH_"))}

    assert lossy <= set(LOSSY_RECIPES), sorted(lossy - set(LOSSY_RECIPES))
    assert file_level == set(FILE_RECIPES)
    assert not set(LOSSY_RECIPES) & set(FILE_RECIPES)
    assert all(ISSUE_CODE_CLASSIFICATION[code] == LOSSY_ISSUE for code in (*LOSSY_RECIPES, *FILE_RECIPES))


@pytest.mark.parametrize("code", sorted(LOSSY_RECIPES))
def test_lossy_codes_are_reported_and_never_fixed(code: str, monkeypatch) -> None:
    payload = LOSSY_RECIPES[code](monkeypatch)

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert code in {issue.code for issue in result.issues}, [issue.to_json() for issue in result.issues]
    assert result.fixes == []
    assert result.to_json()["fixes"] == []


@pytest.mark.parametrize("code", sorted(FILE_RECIPES))
def test_file_and_patch_codes_are_reported_and_never_fixed(code: str, tmp_path) -> None:
    report = FILE_RECIPES[code](tmp_path)

    assert report["ok"] is False
    assert [issue["code"] for issue in report["issues"]] == [code]
    assert report["fixes"] == []
    assert report["correction_prompt"]


@pytest.mark.parametrize(
    ("code", "mutate"),
    [
        ("MISSING_FIELD", lambda d: d["view_spec"]["bindings"][0].pop("id")),
        ("UNKNOWN_FIELD", lambda d: d.__setitem__("design", {"format": "design.md"})),
        ("UNKNOWN_PRESENT_AS", lambda d: d["view_spec"]["bindings"][0].__setitem__("present_as", "chart")),
    ],
)
def test_an_instance_of_a_fixable_code_that_cannot_be_fixed_losslessly_stays_an_issue(code: str, mutate) -> None:
    document = _starter()
    mutate(document)

    result = validate_agent_intent_bundle(document)

    assert not result.valid
    assert result.fixes == []
    assert code in {issue.code for issue in result.issues}


def test_validation_result_json_carries_fixes_beside_issues() -> None:
    result = AgentValidationResult(valid=True, bundle=None, issues=[])

    assert result.to_json() == {"valid": True, "issues": [], "fixes": [], "repair_checklist": []}
    assert result.document is None
