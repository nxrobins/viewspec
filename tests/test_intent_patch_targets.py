from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from viewspec import cli
from viewspec.app_bundle import starter_app_bundle
from viewspec.intent_patch import (
    INTENT_PATCH_CONTRACT_PROFILE,
    INTENT_PATCH_OPERATION_KINDS,
    INTENT_PATCH_TARGET_LIMIT_MAX,
    IntentPatchError,
    _apply_operation,
    intent_patch_targets,
    intent_patch_targets_file,
    parse_intent_patch,
    preview_intent_patch,
    source_sha256,
)
from viewspec.intent_patch_tools import list_intent_patch_targets_tool
from viewspec.intent_tools import starter_intent_payload


def _source(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _intent_source() -> str:
    return _source(starter_intent_payload("dashboard"))


def _app_source() -> str:
    return _source(starter_app_bundle("internal_tool"))


def _regions(base: dict, source_kind: str, screen_id: str | None) -> list[dict]:
    if source_kind == "intent_bundle":
        return base["view_spec"]["regions"]
    for screen in base["screens"]:
        if screen["id"] == screen_id:
            return screen["intent_bundle"]["view_spec"]["regions"]
    return []


def _legal_parent(base: dict, source_kind: str, target: dict) -> str | None:
    """Pick a parent that is neither the region itself, its current parent, nor a descendant."""

    fixed = target["fixed_fields"]
    region_id = fixed["region_id"]
    regions = _regions(base, source_kind, fixed.get("screen_id"))
    children: dict[str, list[str]] = {}
    for region in regions:
        children.setdefault(region.get("parent_region"), []).append(region["id"])
    descendants: set[str] = set()
    stack = [region_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in descendants:
                descendants.add(child)
                stack.append(child)
    for region in regions:
        if region["id"] not in descendants and region["id"] not in {region_id, fixed["old_parent_id"]}:
            return region["id"]
    return None


def _replacement(target: dict, base: dict, source_kind: str) -> object:
    fixed = target["fixed_fields"]
    if "allowed_values" in target:
        return next(value for value in target["allowed_values"] if value != fixed.get("old_value"))
    if target["op"] == "move_region":
        return _legal_parent(base, source_kind, target)
    if target["op"] == "reorder_region_children":
        return list(reversed(fixed["old_children"]))
    if target["op"] == "set_visibility_condition":
        return {"state": "unused_state_for_test", "is": "truthy"}
    old = fixed.get("old_value")
    if isinstance(old, bool):
        return not old
    if isinstance(old, str):
        return f"{old} (revised)"
    if isinstance(old, (int, float)):
        return old + 1
    return "replacement"


def _operation(target: dict, base: dict, source_kind: str) -> dict | None:
    value = _replacement(target, base, source_kind)
    if value is None:
        return None
    return {"op": target["op"], **target["fixed_fields"], target["replacement_field"]: value}


def _patch(listing: dict, operation: dict) -> dict:
    return {
        "schema_version": 1,
        "contract_profile": listing["contract_profile"],
        "source_kind": listing["source_kind"],
        "base_source_sha256": listing["base_source_sha256"],
        "operations": [operation],
        "evidence_refs": [],
    }


@pytest.mark.parametrize("source_text", [_intent_source(), _app_source()], ids=["intent_bundle", "app_bundle"])
def test_every_listed_target_resolves_and_satisfies_its_precondition(source_text: str) -> None:
    """Enumeration must not drift from `_apply_operation`; each stub must apply against these bytes."""

    listing = intent_patch_targets(source_text, limit=INTENT_PATCH_TARGET_LIMIT_MAX)
    base = json.loads(source_text)
    assert listing["counts"]["total"] > 0

    applied = 0
    for target in listing["targets"]:
        operation = _operation(target, base, listing["source_kind"])
        if operation is None:
            continue
        patch = parse_intent_patch(_patch(listing, operation))
        _apply_operation(copy.deepcopy(base), patch.operations[0], listing["source_kind"])
        applied += 1
    assert applied >= len(listing["targets"]) - 2


def test_listed_target_previews_through_the_public_api() -> None:
    source_text = _intent_source()
    listing = intent_patch_targets(source_text, op="replace_semantic_attr")
    target = listing["targets"][0]
    operation = _operation(target, json.loads(source_text), listing["source_kind"])

    preview = preview_intent_patch(source_text, _patch(listing, operation))

    assert preview.candidate_source_sha256 != source_sha256(source_text)
    assert preview.approval_token.startswith("vapprove_")


def test_listing_reports_exact_source_hash_and_is_deterministic() -> None:
    source_text = _intent_source()

    first = intent_patch_targets(source_text)
    second = intent_patch_targets(source_text)

    assert first == second
    assert first["base_source_sha256"] == source_sha256(source_text)
    assert first["contract_profile"] == INTENT_PATCH_CONTRACT_PROFILE
    assert set(first["counts"]["by_op"]) <= INTENT_PATCH_OPERATION_KINDS


def test_targets_carry_only_the_replacement_field_as_the_authoring_gap() -> None:
    listing = intent_patch_targets(_intent_source())

    for target in listing["targets"]:
        assert target["replacement_field"] not in target["fixed_fields"]
        assert "op" not in target["fixed_fields"]
        assert target["target_key"]


def test_ambiguous_aesthetic_profile_is_rejected_with_the_source() -> None:
    """A second profile makes the source invalid, so no target is ever offered for it."""

    payload = starter_intent_payload("dashboard")
    view_id = payload["view_spec"]["id"]
    payload["view_spec"]["styles"].extend(
        [
            {"id": "profile_a", "target": f"view:{view_id}", "token": "aesthetic.calm_ops"},
            {"id": "profile_b", "target": f"view:{view_id}", "token": "aesthetic.brutalist"},
        ]
    )

    with pytest.raises(IntentPatchError) as ambiguous:
        intent_patch_targets(_source(payload), limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    assert ambiguous.value.code == "PATCH_SOURCE_INVALID"


def test_declared_aesthetic_profile_is_offered_once_with_its_current_token() -> None:
    payload = starter_intent_payload("dashboard")
    view_id = payload["view_spec"]["id"]
    payload["view_spec"]["styles"].append(
        {"id": "aesthetic_profile", "target": f"view:{view_id}", "token": "aesthetic.calm_ops"}
    )

    listing = intent_patch_targets(_source(payload), limit=INTENT_PATCH_TARGET_LIMIT_MAX)
    profiles = [target for target in listing["targets"] if target["op"] == "set_aesthetic_profile"]

    assert len(profiles) == 1
    assert profiles[0]["fixed_fields"]["old_value"] == "aesthetic.calm_ops"
    assert all(target["fixed_fields"].get("style_id") != "aesthetic_profile" for target in listing["targets"])


def test_non_scalar_and_oversized_attrs_are_skipped() -> None:
    payload = starter_intent_payload("dashboard")
    node_id = next(iter(payload["substrate"]["nodes"]))
    payload["substrate"]["nodes"][node_id]["attrs"]["structured"] = {"nested": True}
    payload["substrate"]["nodes"][node_id]["attrs"]["oversized"] = "x" * 9000

    listing = intent_patch_targets(_source(payload), op="replace_semantic_attr", limit=INTENT_PATCH_TARGET_LIMIT_MAX)
    attrs = {target["fixed_fields"]["attr"] for target in listing["targets"]}

    assert "structured" not in attrs
    assert "oversized" not in attrs


def test_root_region_is_never_movable() -> None:
    source_text = _intent_source()
    root_region = json.loads(source_text)["view_spec"]["root_region"]

    listing = intent_patch_targets(source_text, op="move_region", limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    assert listing["counts"]["total"] > 0
    assert all(target["fixed_fields"]["region_id"] != root_region for target in listing["targets"])


def test_reorder_requires_at_least_two_children() -> None:
    listing = intent_patch_targets(_intent_source(), op="reorder_region_children", limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    assert all(len(target["fixed_fields"]["old_children"]) >= 2 for target in listing["targets"])


def test_truncation_keeps_pre_truncation_counts() -> None:
    full = intent_patch_targets(_intent_source(), limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    limited = intent_patch_targets(_intent_source(), limit=2)

    assert limited["truncated"] is True
    assert limited["counts"]["returned"] == 2
    assert limited["counts"]["total"] == full["counts"]["total"]
    assert limited["counts"]["by_op"] == full["counts"]["by_op"]
    assert limited["targets"] == full["targets"][:2]


def test_screen_filter_scopes_app_bundle_targets() -> None:
    source_text = _app_source()
    screen_id = json.loads(source_text)["screens"][0]["id"]

    listing = intent_patch_targets(source_text, screen_id=screen_id, limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    assert listing["counts"]["total"] > 0
    assert all(target["fixed_fields"].get("screen_id") == screen_id for target in listing["targets"])


def test_app_level_targets_are_never_screen_scoped() -> None:
    listing = intent_patch_targets(_app_source(), op="replace_fixture_scalar", limit=INTENT_PATCH_TARGET_LIMIT_MAX)

    assert listing["counts"]["total"] > 0
    assert all("screen_id" not in target["fixed_fields"] for target in listing["targets"])
    assert all(target["fixed_fields"]["field"] != "id" for target in listing["targets"])


def test_unsupported_filters_fail_closed() -> None:
    with pytest.raises(IntentPatchError) as unsupported_op:
        intent_patch_targets(_intent_source(), op="rewrite_everything")
    assert unsupported_op.value.code == "PATCH_OPERATION_UNSUPPORTED"

    with pytest.raises(IntentPatchError) as intent_screen:
        intent_patch_targets(_intent_source(), screen_id="queue")
    assert intent_screen.value.code == "PATCH_TARGET_INVALID"

    with pytest.raises(IntentPatchError) as missing_screen:
        intent_patch_targets(_app_source(), screen_id="not_a_screen")
    assert missing_screen.value.code == "PATCH_TARGET_INVALID"

    with pytest.raises(IntentPatchError) as bad_limit:
        intent_patch_targets(_intent_source(), limit=0)
    assert bad_limit.value.code == "PATCH_VALUE_INVALID"


def test_invalid_source_is_rejected_before_enumeration(tmp_path: Path) -> None:
    with pytest.raises(IntentPatchError) as unknown_shape:
        intent_patch_targets(json.dumps({"not": "a bundle"}))
    assert unknown_shape.value.code == "PATCH_SOURCE_INVALID"

    broken = tmp_path / "viewspec.intent.json"
    payload = starter_intent_payload("dashboard")
    payload["view_spec"]["regions"] = []
    broken.write_text(_source(payload), encoding="utf-8")
    with pytest.raises(IntentPatchError) as invalid:
        intent_patch_targets_file(broken)
    assert invalid.value.code == "PATCH_SOURCE_INVALID"


def test_cli_lists_targets_without_mutating_source(tmp_path: Path, capsys) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    original = _intent_source()
    source_path.write_text(original, encoding="utf-8")

    exit_code = cli.main(["patch-targets", str(source_path), "--op", "set_region_layout", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["targets"]["base_source_sha256"] == source_sha256(original)
    assert payload["targets"]["filters"]["op"] == "set_region_layout"
    assert source_path.read_text(encoding="utf-8") == original


def test_cli_rejects_unsupported_operation_filter(tmp_path: Path, capsys) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    original = _intent_source()
    source_path.write_text(original, encoding="utf-8")

    exit_code = cli.main(["patch-targets", str(source_path), "--op", "delete_region", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "PATCH_OPERATION_UNSUPPORTED" in captured.out + captured.err
    assert source_path.read_text(encoding="utf-8") == original


def test_mcp_tool_returns_targets_inside_the_path_boundary(tmp_path: Path) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    source_path.write_text(_intent_source(), encoding="utf-8")

    response = list_intent_patch_targets_tool("viewspec.intent.json", cwd=tmp_path)

    assert response["ok"] is True
    assert response["targets"]["source_kind"] == "intent_bundle"
    assert response["targets"]["base_source_sha256"] == source_sha256(_intent_source())
    assert any("replacement_field" in action for action in response["next_actions"])


def test_mcp_tool_rejects_paths_outside_the_boundary(tmp_path: Path) -> None:
    source_path = tmp_path / "viewspec.intent.json"
    source_path.write_text(_intent_source(), encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()

    response = list_intent_patch_targets_tool(str(source_path), cwd=nested)

    assert response["ok"] is False
    assert response["errors"][0]["code"] == "PATH_OUTSIDE_CWD"
