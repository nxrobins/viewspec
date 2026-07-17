from __future__ import annotations

import hashlib

import pytest

from viewspec.intent_patch import (
    IntentPatchError,
    patch_context_from_repair_plan,
    patch_context_from_review_batch,
)
from viewspec.repair import VerificationRepairPlan
from viewspec.review_contract import (
    ReviewBatch,
    ReviewContext,
    ReviewEvent,
    ReviewRevision,
    ReviewTarget,
    ReviewViewport,
    new_batch_id,
    new_event_id,
    new_review_id,
)
from viewspec.verification import VerificationDiagnostic, VerificationPlan, VerificationResult


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_batch() -> ReviewBatch:
    revision = ReviewRevision(
        number=3,
        source_kind="app_bundle",
        source_sha256=_sha("source"),
        design_sha256=None,
        target="react-tailwind-app",
        artifact_set_sha256=_sha("artifacts"),
        root_manifest_kind="app_build_manifest",
        root_manifest_sha256=_sha("manifest"),
        compiler_version="0.3.0b4",
        contract_profile="local_v1",
    )
    target = ReviewTarget(
        kind="source_node",
        screen_id="queue",
        ir_id="binding_inc_1043_status",
        source_ref="screen:queue/ir:binding_inc_1043_status",
        dom_id="dom-binding_inc_1043_status",
        binding_id="inc_1043_status",
        action_id=None,
        intent_refs=("viewspec:binding:inc_1043_status",),
        content_refs=("node:inc_1043#attr:status",),
        provenance_manifest_sha256=_sha("screen-manifest"),
        target_resolution="exact",
    )
    event = ReviewEvent(
        event_id=new_event_id(),
        sequence=1,
        actor="human",
        kind="change_request",
        body="Show this status as a badge.",
        revision=revision,
        target=target,
        context=ReviewContext(
            route="/",
            screen_id="queue",
            viewport=ReviewViewport.canonical("desktop"),
            selected_text=None,
            control_values=(),
            visibility="visible",
            evidence_refs=(),
        ),
    )
    return ReviewBatch.create((event,), review_id=new_review_id(), batch_id=new_batch_id())


def _repair_plan() -> VerificationRepairPlan:
    result = VerificationResult.create(
        artifact_sha256=_sha("artifact"),
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(
            VerificationDiagnostic(
                code="VERIFY_LAYOUT_OVERFLOW",
                severity="error",
                message="Queue overflows.",
                fix="Use a bounded grid layout.",
                source_ref="screen:queue/ir:region_main",
                viewport="mobile",
            ),
        ),
    )
    return VerificationRepairPlan.from_result(result)


def test_review_batch_becomes_source_bound_patch_proposal_context() -> None:
    batch = _review_batch()

    context = patch_context_from_review_batch(batch)
    payload = context.to_json()

    assert payload["source_kind"] == "app_bundle"
    assert payload["base_source_sha256"] == batch.revision.source_sha256
    assert payload["contract_profile"] == "local_v1"
    assert payload["origin"] == "review_batch"
    assert payload["evidence_refs"] == [
        f"review:{batch.review_id}:{batch.batch_id}",
        f"review_event:{batch.events[0].event_id}",
    ]
    assert payload["requests"] == [
        {
            "request_id": batch.events[0].event_id,
            "kind": "change_request",
            "instruction": "Show this status as a badge.",
            "screen_id": "queue",
            "source_ref": "screen:queue/ir:binding_inc_1043_status",
            "binding_id": "inc_1043_status",
            "action_id": None,
            "intent_refs": ["viewspec:binding:inc_1043_status"],
            "content_refs": ["node:inc_1043#attr:status"],
        }
    ]


def test_repair_plan_becomes_source_bound_patch_proposal_context() -> None:
    plan = _repair_plan()

    context = patch_context_from_repair_plan(
        plan,
        source_kind="app_bundle",
        base_source_sha256=_sha("source"),
    )
    payload = context.to_json()

    assert payload["origin"] == "verification_repair_plan"
    assert payload["evidence_refs"] == [
        f"verify:{plan.repair_plan_id}",
        f"verify_repair:{plan.directives[0].repair_id}",
    ]
    assert payload["requests"][0]["code"] == "VERIFY_LAYOUT_OVERFLOW"
    assert payload["requests"][0]["source_ref"] == "screen:queue/ir:region_main"
    assert payload["requests"][0]["viewports"] == ["mobile"]


def test_patch_context_rejects_nonrepair_or_nonlocal_inputs() -> None:
    done = VerificationRepairPlan.from_result(
        VerificationResult.create(
            artifact_sha256=_sha("artifact"),
            plan=VerificationPlan.default(),
            complete=True,
            diagnostics=(),
        )
    )
    with pytest.raises(IntentPatchError) as done_error:
        patch_context_from_repair_plan(done, source_kind="intent_bundle", base_source_sha256=_sha("source"))
    assert done_error.value.code == "PATCH_CONTEXT_EMPTY"

    batch = _review_batch()
    object.__setattr__(batch.revision, "contract_profile", "future_v2")
    with pytest.raises(IntentPatchError) as profile_error:
        patch_context_from_review_batch(batch)
    assert profile_error.value.code == "PATCH_PROFILE_UNSUPPORTED"
