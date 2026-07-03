"""Conformance oracle between the published JSON Schemas and the hand validators.

The project deliberately ships NO runtime JSON-Schema dependency — all validation
is hand-rolled (agent.py / app_validation.py). But it also *publishes* JSON Schemas
(AGENT_INTENT_BUNDLE_SCHEMA, AGENT_APP_BUNDLE_SCHEMA) as the agent-facing contract.
Those two can silently drift. This test uses `jsonschema` (a dev-only dependency)
purely as an oracle to pin three properties:

1. Well-formedness: each published schema is a valid Draft 2020-12 schema.
2. No lie (E2): every canonical valid starter is accepted by BOTH the published
   schema and the hand validator. A schema that rejects valid output is a worse
   lie than drift.
3. Strictness (E1): every bundle the published schema rejects, the hand validator
   also rejects. i.e. the published contract is a lower bound on validator
   strictness. Asserted non-vacuously — each corpus member is confirmed
   schema-invalid first, so the antecedent always fires.
4. Bounded reverse (E4): over the SAME curated, schema-EXPRESSIBLE corpora, every
   bundle the hand validator rejects the published schema also rejects — so the
   schema has not drifted LOOSER than the validator. Sound precisely because every
   corpus member is schema-invalid (asserted in (3)); asserted non-vacuously
   (validator rejection confirmed first).

The GENERAL reverse (validator rejects for ANY reason => schema rejects) is intentionally
NOT asserted: the hand validator is legitimately stricter via semantic checks (address
resolution, "hero requires a title binding", root-route-resolves-to-one) that JSON Schema
cannot express. (E4) is bounded to the curated schema-expressible corpora only.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator

from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA
from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA, starter_app_bundle, validate_app_text
from viewspec.intent_tools import STARTER_INTENT_KINDS, starter_intent_bundle, validate_intent_text

# The V3 (interactive_state) shape has no public starter; reuse the canonical
# example from the sibling app-bundle test module. Ensure this directory is on
# the path so the import works regardless of pytest's import mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_bundle import _stateful_app_bundle  # noqa: E402

_INTENT_SCHEMA = Draft202012Validator(AGENT_INTENT_BUNDLE_SCHEMA)
_APP_SCHEMA = Draft202012Validator(AGENT_APP_BUNDLE_SCHEMA)


def _intent(kind: str = "dashboard") -> dict[str, Any]:
    return starter_intent_bundle(kind).to_json()


def _valid_app_bundles() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("v1", starter_app_bundle("internal_tool")),
        ("v2", starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")),
        ("v3", _stateful_app_bundle()),
    ]


def _mutated(base: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    clone = copy.deepcopy(base)
    mutate(clone)
    return clone


# --- Well-formedness --------------------------------------------------------


def test_published_schemas_are_wellformed():
    Draft202012Validator.check_schema(AGENT_INTENT_BUNDLE_SCHEMA)
    Draft202012Validator.check_schema(AGENT_APP_BUNDLE_SCHEMA)


# --- E2 (Invariant 3): the schema does not lie ------------------------------


@pytest.mark.parametrize("kind", sorted(STARTER_INTENT_KINDS))
def test_valid_intent_starter_accepted_by_schema_and_validator(kind):
    bundle = starter_intent_bundle(kind).to_json()
    assert _INTENT_SCHEMA.is_valid(bundle), f"published intent schema rejects the valid '{kind}' starter"
    assert validate_intent_text(json.dumps(bundle))["ok"], f"hand validator rejects the valid '{kind}' starter"


@pytest.mark.parametrize("label,bundle", _valid_app_bundles())
def test_valid_app_starter_accepted_by_schema_and_validator(label, bundle):
    assert _APP_SCHEMA.is_valid(bundle), f"published app schema rejects the valid '{label}' starter"
    assert validate_app_text(json.dumps(bundle))["ok"], f"hand validator rejects the valid '{label}' starter"


# --- E1 (Invariants 1 + 2): strictness, asserted non-vacuously --------------
# Each corpus member is a deliberately schema-INVALID bundle. We assert both that
# the published schema rejects it (antecedent fires -> non-vacuous) and that the
# hand validator also rejects it (strictness).


def _intent_corpus() -> list[tuple[str, dict[str, Any]]]:
    def nodes_as_list(b):
        b["substrate"]["nodes"] = list(b["substrate"]["nodes"].values())

    def bad_present_as(b):
        b["view_spec"]["bindings"][0]["present_as"] = "bogus"

    def bad_layout(b):
        b["view_spec"]["regions"][0]["layout"] = "bogus"

    def bad_motif_kind(b):
        b["view_spec"]["motifs"][0]["kind"] = "bogus"

    def unknown_top_field(b):
        b["zzz_unknown"] = 1

    def missing_view_spec(b):
        b.pop("view_spec")

    def too_many_regions(b):
        b["view_spec"]["regions"] = [
            {"id": f"r{i}", "parent_region": "root", "layout": "stack", "min_children": 0} for i in range(40)
        ]

    # --- broadened: id / address patterns ---
    def bad_binding_id(b):
        b["view_spec"]["bindings"][0]["id"] = "bad id!"

    def bad_binding_address(b):
        b["view_spec"]["bindings"][0]["address"] = "not-an-address"

    # --- broadened: hosted-only-field not/anyOf guard (top-level + view_spec) ---
    def hosted_field_design(b):
        b["design"] = {"x": 1}

    def hosted_field_motif_library(b):
        b["motif_library"] = {"x": 1}

    def view_spec_inputs(b):
        b["view_spec"]["inputs"] = []

    # --- broadened: NESTED additionalProperties:false ---
    def nested_extra_key_binding(b):
        b["view_spec"]["bindings"][0]["zzz"] = 1

    def nested_extra_key_region(b):
        b["view_spec"]["regions"][0]["zzz"] = 1

    # --- broadened: minLength / minimum bounds ---
    def empty_node_kind(b):
        node_id = next(iter(b["substrate"]["nodes"]))
        b["substrate"]["nodes"][node_id]["kind"] = ""

    def negative_min_children(b):
        b["view_spec"]["regions"][0]["min_children"] = -1

    def zero_complexity_tier(b):
        b["view_spec"]["complexity_tier"] = 0

    base = _intent()
    return [
        (name, _mutated(base, fn))
        for name, fn in [
            ("nodes_as_list", nodes_as_list),
            ("bad_present_as", bad_present_as),
            ("bad_layout", bad_layout),
            ("bad_motif_kind", bad_motif_kind),
            ("unknown_top_field", unknown_top_field),
            ("missing_view_spec", missing_view_spec),
            ("too_many_regions", too_many_regions),
            ("bad_binding_id_pattern", bad_binding_id),
            ("bad_binding_address_pattern", bad_binding_address),
            ("hosted_field_design_not_guard", hosted_field_design),
            ("hosted_field_motif_library_not_guard", hosted_field_motif_library),
            ("view_spec_inputs_not_guard", view_spec_inputs),
            ("nested_extra_key_binding", nested_extra_key_binding),
            ("nested_extra_key_region", nested_extra_key_region),
            ("empty_node_kind_minlength", empty_node_kind),
            ("negative_min_children_minimum", negative_min_children),
            ("zero_complexity_tier_minimum", zero_complexity_tier),
        ]
    ]


def _app_corpus() -> list[tuple[str, dict[str, Any]]]:
    v2 = starter_app_bundle("internal_tool", resource_binding="fixture_readonly_v0")
    v3 = _stateful_app_bundle()

    def empty_record_ids(b):
        for screen in b["screens"]:
            for view in screen.get("resource_views", []):
                view["record_ids"] = []

    def empty_routes(b):
        b["routes"] = []

    def empty_screens(b):
        b["screens"] = []

    def bad_schema_version(b):
        b["schema_version"] = 99

    def wrong_type_name(b):
        b["app"]["name"] = 123

    def bad_mutation_op(b):
        b["mutations"][0]["ops"][0]["op"] = "bogus"

    # --- broadened: safe_id / route-path patterns + maxLength ---
    def bad_app_id(b):
        b["app"]["id"] = "bad id"

    def bad_root_route(b):
        b["app"]["root_route"] = "no-leading-slash"

    def oversize_app_id(b):
        b["app"]["id"] = "a" * 97

    # --- broadened: NESTED additionalProperties:false (route / resource_view / state_entry / op) ---
    def nested_extra_key_route(b):
        b["routes"][0]["zzz"] = 1

    def nested_extra_key_resource_view(b):
        for screen in b["screens"]:
            for view in screen.get("resource_views", []):
                view["zzz"] = 1

    def nested_extra_key_state_entry(b):
        b["state"][0]["zzz"] = 1

    def nested_extra_key_mutation_op(b):
        b["mutations"][0]["ops"][0]["zzz"] = 1

    # --- broadened: minItems / minimum / enum / const ---
    def empty_fields(b):
        for screen in b["screens"]:
            for view in screen.get("resource_views", []):
                view["fields"] = []

    def bad_resource_view_mode(b):
        for screen in b["screens"]:
            for view in screen.get("resource_views", []):
                view["mode"] = "table"

    def bad_state_kind(b):
        b["state"][0]["kind"] = "bogus"

    def negative_slice_start(b):
        b["selectors"][0]["ops"].append({"op": "slice", "start": -1})

    return [
        ("empty_record_ids", _mutated(v2, empty_record_ids)),
        ("empty_routes", _mutated(v2, empty_routes)),
        ("empty_screens", _mutated(v2, empty_screens)),
        ("bad_schema_version", _mutated(v2, bad_schema_version)),
        ("wrong_type_name", _mutated(v2, wrong_type_name)),
        ("bad_mutation_op_v3", _mutated(v3, bad_mutation_op)),
        ("bad_app_id_pattern", _mutated(v2, bad_app_id)),
        ("bad_root_route_pattern", _mutated(v2, bad_root_route)),
        ("oversize_app_id_maxlength", _mutated(v2, oversize_app_id)),
        ("nested_extra_key_route", _mutated(v2, nested_extra_key_route)),
        ("nested_extra_key_resource_view", _mutated(v2, nested_extra_key_resource_view)),
        ("empty_fields_minitems", _mutated(v2, empty_fields)),
        ("bad_resource_view_mode_const", _mutated(v2, bad_resource_view_mode)),
        ("nested_extra_key_state_entry_v3", _mutated(v3, nested_extra_key_state_entry)),
        ("bad_state_kind_enum_v3", _mutated(v3, bad_state_kind)),
        ("negative_slice_start_minimum_v3", _mutated(v3, negative_slice_start)),
        ("nested_extra_key_mutation_op_v3", _mutated(v3, nested_extra_key_mutation_op)),
    ]


@pytest.mark.parametrize("label,bundle", _intent_corpus())
def test_schema_invalid_intent_is_also_validator_invalid(label, bundle):
    assert not _INTENT_SCHEMA.is_valid(bundle), f"corpus member '{label}' is not actually schema-invalid (vacuous)"
    assert not validate_intent_text(json.dumps(bundle))["ok"], (
        f"drift: published schema rejects '{label}' but the hand validator accepts it"
    )


@pytest.mark.parametrize("label,bundle", _app_corpus())
def test_schema_invalid_app_is_also_validator_invalid(label, bundle):
    assert not _APP_SCHEMA.is_valid(bundle), f"corpus member '{label}' is not actually schema-invalid (vacuous)"
    assert not validate_app_text(json.dumps(bundle))["ok"], (
        f"drift: published schema rejects '{label}' but the hand validator accepts it"
    )


# --- E4: bounded reverse -- the schema is not LOOSER than the validator -----
# Over the SAME curated corpora (every member is schema-EXPRESSIBLE and, per the forward
# tests above, provably schema-invalid), assert validator-invalid => schema-invalid. This
# catches the dangerous drift the forward direction is blind to: a published schema that
# has grown looser than the hand validator, so an agent trusting the schema emits something
# the validator rejects. The GENERAL reverse is still NOT asserted (see module docstring).


@pytest.mark.parametrize("label,bundle", _intent_corpus())
def test_validator_invalid_intent_is_also_schema_invalid(label, bundle):
    assert not validate_intent_text(json.dumps(bundle))["ok"], (
        f"corpus member '{label}' is not actually validator-invalid (vacuous)"
    )
    assert not _INTENT_SCHEMA.is_valid(bundle), (
        f"looser-schema drift: hand validator rejects '{label}' but the published schema accepts it"
    )


@pytest.mark.parametrize("label,bundle", _app_corpus())
def test_validator_invalid_app_is_also_schema_invalid(label, bundle):
    assert not validate_app_text(json.dumps(bundle))["ok"], (
        f"corpus member '{label}' is not actually validator-invalid (vacuous)"
    )
    assert not _APP_SCHEMA.is_valid(bundle), (
        f"looser-schema drift: hand validator rejects '{label}' but the published schema accepts it"
    )
