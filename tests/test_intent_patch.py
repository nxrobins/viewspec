from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hypothesis import assume, given, strategies as st
from jsonschema import Draft202012Validator, ValidationError
import pytest

from viewspec.app_bundle import starter_react_app_bundle
from viewspec.intent_patch import (
    INTENT_PATCH_CONTRACT_PROFILE,
    INTENT_PATCH_JSON_SCHEMA,
    INTENT_PATCH_MAX_OPERATIONS,
    IntentPatchError,
    apply_intent_patch_file,
    parse_intent_patch,
    preview_intent_patch,
    source_sha256,
    starter_intent_patch_payload,
)
from viewspec.intent_tools import starter_intent_payload


def _source_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _patch_text(
    source_text: str,
    operations: list[dict[str, object]],
    *,
    source_kind: str = "intent_bundle",
    evidence_refs: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
            "source_kind": source_kind,
            "base_source_sha256": source_sha256(source_text),
            "operations": operations,
            "evidence_refs": evidence_refs or [],
        },
        sort_keys=True,
    )


def _semantic_attr_patch(source_text: str, old_value: object, value: object) -> str:
    return _patch_text(
        source_text,
        [
            {
                "op": "replace_semantic_attr",
                "node_id": "starter_dashboard",
                "attr": "title",
                "old_value": old_value,
                "value": value,
            }
        ],
    )


def test_patch_contract_is_closed_bounded_and_identity_addressed() -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    patch = parse_intent_patch(
        _semantic_attr_patch(source, "Starter Dashboard", "Operations Dashboard")
    )

    assert patch.patch_id.startswith("vpatch_")
    assert patch.source_kind == "intent_bundle"
    assert patch.contract_profile == "local_v1"
    assert patch.operations[0].op == "replace_semantic_attr"
    assert patch.operations[0].target_key == (
        "intent",
        "root",
        "semantic_attr",
        "starter_dashboard",
        "title",
    )
    assert parse_intent_patch(patch.to_json()).patch_id == patch.patch_id


def test_published_patch_schema_and_starter_match_the_runtime_contract() -> None:
    Draft202012Validator.check_schema(INTENT_PATCH_JSON_SCHEMA)
    validator = Draft202012Validator(INTENT_PATCH_JSON_SCHEMA)
    payload = starter_intent_patch_payload()

    validator.validate(payload)
    assert parse_intent_patch(payload).source_kind == "intent_bundle"
    assert payload["operations"][0]["old_value"] == "Starter Dashboard"

    with pytest.raises(ValidationError):
        validator.validate({**payload, "unknown": True})


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda payload: payload.update({"unknown": True}), "PATCH_FIELD_UNKNOWN"),
        (lambda payload: payload.update({"schema_version": 2}), "PATCH_SCHEMA_UNSUPPORTED"),
        (lambda payload: payload.update({"contract_profile": "future_v2"}), "PATCH_PROFILE_UNSUPPORTED"),
        (lambda payload: payload.update({"source_kind": "html"}), "PATCH_SOURCE_KIND_UNSUPPORTED"),
        (lambda payload: payload.update({"base_source_sha256": "bad"}), "PATCH_BASE_HASH_INVALID"),
        (lambda payload: payload.update({"operations": []}), "PATCH_OPERATION_LIMIT_EXCEEDED"),
    ],
)
def test_patch_contract_rejects_unknown_or_unbounded_shapes(mutation, code: str) -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    payload = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))
    mutation(payload)

    with pytest.raises(IntentPatchError) as exc_info:
        parse_intent_patch(payload)

    assert exc_info.value.code == code


def test_patch_contract_rejects_duplicate_json_keys_and_duplicate_targets() -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    valid = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))
    duplicate_key = json.dumps(valid).replace(
        '"schema_version": 1',
        '"schema_version": 1, "schema_version": 1',
        1,
    )
    with pytest.raises(IntentPatchError) as duplicate_key_error:
        parse_intent_patch(duplicate_key)
    assert duplicate_key_error.value.code == "PATCH_INVALID_JSON"

    valid["operations"] = [valid["operations"][0], valid["operations"][0]]
    with pytest.raises(IntentPatchError) as duplicate_target_error:
        parse_intent_patch(valid)
    assert duplicate_target_error.value.code == "PATCH_TARGET_CONFLICT"


def test_patch_contract_enforces_the_physical_operation_limit() -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    operation = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))["operations"][0]
    payload = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))
    payload["operations"] = [
        {**operation, "attr": f"attr_{index}", "old_value": None, "value": index}
        for index in range(INTENT_PATCH_MAX_OPERATIONS + 1)
    ]
    with pytest.raises(IntentPatchError) as exc_info:
        parse_intent_patch(payload)
    assert exc_info.value.code == "PATCH_OPERATION_LIMIT_EXCEEDED"


def test_patch_contract_rejects_per_operation_noops() -> None:
    source = _source_text(starter_intent_payload("dashboard"))

    with pytest.raises(IntentPatchError) as exc_info:
        parse_intent_patch(_semantic_attr_patch(source, "Starter Dashboard", "Starter Dashboard"))

    assert exc_info.value.code == "PATCH_NO_EFFECT"


def test_preview_applies_all_closed_intent_operations_and_returns_inverse() -> None:
    payload = starter_intent_payload("dashboard")
    payload["view_spec"]["styles"] = [
        {
            "id": "aesthetic_profile",
            "target": "view:starter_dashboard",
            "token": "aesthetic.calm_ops",
        },
        {"id": "density", "target": "view:starter_dashboard", "token": "density.regular"},
    ]
    payload["view_spec"]["regions"].append(
        {
            "id": "secondary",
            "parent_region": "root",
            "role": "aside",
            "layout": "stack",
            "min_children": 0,
            "max_children": None,
        }
    )
    source = _source_text(payload)
    patch = _patch_text(
        source,
        [
            {
                "op": "set_aesthetic_profile",
                "old_value": "aesthetic.calm_ops",
                "value": "aesthetic.data_dense",
            },
            {
                "op": "set_style_token",
                "style_id": "density",
                "old_value": "density.regular",
                "value": "density.compact",
            },
            {
                "op": "set_region_layout",
                "region_id": "main",
                "old_value": "stack",
                "value": "grid",
            },
            {
                "op": "reorder_region_children",
                "region_id": "root",
                "old_children": ["main", "secondary"],
                "children": ["secondary", "main"],
            },
            {
                "op": "move_region",
                "region_id": "secondary",
                "old_parent_id": "root",
                "parent_id": "main",
            },
            {
                "op": "set_binding_presentation",
                "binding_id": "revenue_value",
                "old_value": "value",
                "value": "text",
            },
            {
                "op": "replace_semantic_attr",
                "node_id": "starter_dashboard",
                "attr": "title",
                "old_value": "Starter Dashboard",
                "value": "Operations Dashboard",
            },
        ],
    )

    preview = preview_intent_patch(source, patch)
    candidate = json.loads(preview.candidate_text)

    assert preview.compile_check == {
        "status": "passed",
        "target": "html-tailwind",
        "artifact_check": "passed",
    }
    assert preview.verification == {"status": "not_run"}
    assert preview.semantic_diff["ok"] is True
    assert preview.approval_token.startswith("vapprove_")
    assert candidate["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] == "Operations Dashboard"
    styles = {item["id"]: item for item in candidate["view_spec"]["styles"]}
    assert styles["aesthetic_profile"]["token"] == "aesthetic.data_dense"
    assert styles["density"]["token"] == "density.compact"
    assert next(item for item in candidate["view_spec"]["regions"] if item["id"] == "main")["layout"] == "grid"
    assert next(item for item in candidate["view_spec"]["regions"] if item["id"] == "secondary")["parent_region"] == "main"
    assert next(item for item in candidate["view_spec"]["bindings"] if item["id"] == "revenue_value")["present_as"] == "text"

    inverse_preview = preview_intent_patch(preview.candidate_text, preview.inverse_patch.to_json())
    assert json.loads(inverse_preview.candidate_text) == json.loads(source)


def test_preview_applies_app_screen_fixture_and_visibility_operations() -> None:
    payload = starter_react_app_bundle()
    source = _source_text(payload)
    patch = _patch_text(
        source,
        [
            {
                "op": "replace_semantic_attr",
                "screen_id": "queue",
                "node_id": "inc_1043",
                "attr": "status",
                "old_value": "queued",
                "value": "mitigating",
            },
            {
                "op": "replace_fixture_scalar",
                "resource_id": "incidents",
                "record_id": "inc_1043",
                "field": "status",
                "old_value": "queued",
                "value": "mitigating",
            },
            {
                "op": "set_visibility_condition",
                "visibility_id": "show_triaged_status",
                "old_value": {"state": "selected_incident", "is": "truthy"},
                "value": {"state": "selected_incident", "equals": "inc_1043"},
            },
        ],
        source_kind="app_bundle",
    )

    preview = preview_intent_patch(source, patch)
    candidate = json.loads(preview.candidate_text)

    assert preview.compile_check["status"] == "passed"
    assert preview.compile_check["target"] == "html-tailwind-app"
    queue = next(item for item in candidate["screens"] if item["id"] == "queue")
    assert queue["intent_bundle"]["substrate"]["nodes"]["inc_1043"]["attrs"]["status"] == "mitigating"
    incidents = next(item for item in candidate["resources"] if item["id"] == "incidents")
    assert next(item for item in incidents["records"] if item["id"] == "inc_1043")["status"] == "mitigating"
    assert candidate["visibility"][0]["when"] == {
        "state": "selected_incident",
        "equals": "inc_1043",
    }
    inverse = preview_intent_patch(preview.candidate_text, preview.inverse_patch)
    assert json.loads(inverse.candidate_text) == json.loads(source)


@pytest.mark.parametrize(
    ("mutate_source", "code"),
    [
        (lambda source: source + " ", "PATCH_BASE_CHANGED"),
        (lambda source: source.replace("Starter Dashboard", "Already Changed"), "PATCH_PRECONDITION_FAILED"),
    ],
)
def test_preview_fails_closed_for_changed_base_or_old_value(mutate_source, code: str) -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    patch_payload = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))
    changed = mutate_source(source)
    if code == "PATCH_PRECONDITION_FAILED":
        patch_payload["base_source_sha256"] = source_sha256(changed)

    with pytest.raises(IntentPatchError) as exc_info:
        preview_intent_patch(changed, patch_payload)
    assert exc_info.value.code == code


def test_preview_cannot_smuggle_new_fields_through_null_preconditions() -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    patch = _semantic_attr_patch(source, None, "created")
    payload = json.loads(patch)
    payload["operations"][0]["attr"] = "undeclared_field"

    with pytest.raises(IntentPatchError) as exc_info:
        preview_intent_patch(source, payload)

    assert exc_info.value.code == "PATCH_TARGET_MISSING"


def test_preview_is_deterministic_across_patch_key_order() -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    payload = json.loads(_semantic_attr_patch(source, "Starter Dashboard", "Changed"))
    reordered = {key: payload[key] for key in reversed(payload)}

    left = preview_intent_patch(source, payload)
    right = preview_intent_patch(source, reordered)

    assert left.preview_id == right.preview_id
    assert left.approval_token == right.approval_token
    assert left.candidate_text == right.candidate_text
    assert left.inverse_patch.to_json() == right.inverse_patch.to_json()


def test_apply_requires_exact_approval_and_writes_receipt_atomically(tmp_path: Path) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    with pytest.raises(IntentPatchError) as approval_error:
        apply_intent_patch_file(source_path, patch_path, approval_token="vapprove_" + "0" * 64)
    assert approval_error.value.code == "PATCH_APPROVAL_INVALID"
    assert source_path.read_text(encoding="utf-8") == source

    receipt = apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert source_sha256(source_path.read_text(encoding="utf-8")) == preview.candidate_source_sha256
    assert receipt.status == "applied"
    assert receipt.preview_id == preview.preview_id
    assert receipt.approval_token == preview.approval_token
    assert receipt.receipt_path.is_file()
    assert json.loads(receipt.receipt_path.read_text(encoding="utf-8"))["status"] == "applied"
    assert receipt.inverse_patch.base_source_sha256 == preview.candidate_source_sha256

    retried = apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)
    assert retried.receipt_id == receipt.receipt_id
    assert retried.receipt_path == receipt.receipt_path


def test_apply_rolls_back_source_if_receipt_commit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    from viewspec import intent_patch as module

    original = module._write_receipt

    def fail_applied(path, payload):
        if payload.get("status") == "applied":
            raise OSError("simulated receipt fsync failure")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_receipt", fail_applied)
    with pytest.raises(IntentPatchError) as exc_info:
        apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert exc_info.value.code == "PATCH_APPLY_FAILED"
    assert source_path.read_text(encoding="utf-8") == source


def test_apply_never_overwrites_a_noncooperating_edit_during_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))
    concurrent = source.replace("Starter Dashboard", "Concurrent Human Edit")

    from viewspec import intent_patch as module

    original = module._write_receipt

    def edit_after_prepare(path, payload):
        original(path, payload)
        if payload.get("status") == "prepared":
            source_path.write_text(concurrent, encoding="utf-8")

    monkeypatch.setattr(module, "_write_receipt", edit_after_prepare)
    with pytest.raises(IntentPatchError) as exc_info:
        apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert exc_info.value.code == "PATCH_APPLY_FAILED"
    assert source_path.read_text(encoding="utf-8") == concurrent


def test_apply_recovers_process_death_after_source_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    from viewspec import intent_patch as module

    original = module._write_receipt

    def interrupt_applied(path, payload):
        if payload.get("status") == "applied":
            raise KeyboardInterrupt("simulated process death")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_receipt", interrupt_applied)
    with pytest.raises(KeyboardInterrupt):
        apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)
    assert source_sha256(source_path.read_text(encoding="utf-8")) == preview.candidate_source_sha256

    monkeypatch.setattr(module, "_write_receipt", original)
    receipt = apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert receipt.status == "applied"
    assert receipt.preview_id == preview.preview_id
    assert json.loads(receipt.receipt_path.read_text(encoding="utf-8"))["status"] == "applied"
    assert not list(tmp_path.glob(".*.backup"))


def test_apply_fails_fast_when_another_source_transaction_holds_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    from viewspec import intent_patch as module

    monkeypatch.setattr(module, "INTENT_PATCH_LOCK_TIMEOUT_SECONDS", 0.05)
    with module._source_transaction_lock(source_path):
        with pytest.raises(IntentPatchError) as exc_info:
            apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert exc_info.value.code == "PATCH_LOCK_TIMEOUT"
    assert source_path.read_text(encoding="utf-8") == source


def test_recovery_refuses_to_overwrite_a_third_source_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = _source_text(starter_intent_payload("dashboard"))
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(_semantic_attr_patch(source, "Starter Dashboard", "Changed"), encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    from viewspec import intent_patch as module

    original = module._write_receipt

    def interrupt_applied(path, payload):
        if payload.get("status") == "applied":
            raise KeyboardInterrupt("simulated process death")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_receipt", interrupt_applied)
    with pytest.raises(KeyboardInterrupt):
        apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)
    third_state = source_path.read_text(encoding="utf-8") + " "
    source_path.write_text(third_state, encoding="utf-8")

    monkeypatch.setattr(module, "_write_receipt", original)
    with pytest.raises(IntentPatchError) as exc_info:
        apply_intent_patch_file(source_path, patch_path, approval_token=preview.approval_token)

    assert exc_info.value.code == "PATCH_RECOVERY_REQUIRED"
    assert source_path.read_text(encoding="utf-8") == third_state


_JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=64).filter(lambda value: "\x00" not in value),
)


@given(value=_JSON_SCALARS)
def test_property_semantic_scalar_patch_and_inverse_round_trip(value: object) -> None:
    assume(value != "before")
    payload = starter_intent_payload("dashboard")
    payload["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] = "before"
    source = _source_text(payload)
    preview = preview_intent_patch(source, _semantic_attr_patch(source, "before", value))
    inverse = preview_intent_patch(preview.candidate_text, preview.inverse_patch.to_json())

    assert json.loads(inverse.candidate_text) == json.loads(source)
    assert source_sha256(preview.candidate_text) == preview.candidate_source_sha256


@given(suffix=st.binary(min_size=1, max_size=64))
def test_property_every_changed_base_is_rejected_before_mutation(suffix: bytes) -> None:
    source = _source_text(starter_intent_payload("dashboard"))
    patch = _semantic_attr_patch(source, "Starter Dashboard", "Changed")
    changed = source + suffix.hex()
    assert hashlib.sha256(changed.encode("utf-8")).hexdigest() != source_sha256(source)

    with pytest.raises(IntentPatchError) as exc_info:
        preview_intent_patch(changed, patch)
    assert exc_info.value.code == "PATCH_BASE_CHANGED"
