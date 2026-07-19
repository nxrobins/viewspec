from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st
from jsonschema import Draft202012Validator, ValidationError
import pytest

from viewspec.app_bundle import starter_app_bundle, starter_react_app_bundle
from viewspec.converge_sessions import (
    CONVERGE_MAX_ATTEMPTS,
    CONVERGENCE_TASK_JSON_SCHEMA,
    ConvergeError,
    ProgressCertificate,
    approve_convergence_preview,
    get_convergence_status,
    reject_convergence_preview,
    start_convergence_session,
    submit_convergence_patch,
    starter_convergence_task_payload,
)
from viewspec.intent_patch import IntentPatchContext, source_sha256
from viewspec.intent_tools import starter_intent_payload
from viewspec.verification import (
    RetryLineage,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "viewspec.intent.json"
    text = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return path, text


def _review_context(text: str, *, binding_id: str = "revenue_value") -> IntentPatchContext:
    content_ref = "node:revenue#attr:value" if binding_id == "revenue_value" else "node:missing#attr:value"
    return IntentPatchContext(
        origin="review_batch",
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile="local_v1",
        evidence_refs=("review:vrw_example:batch_example", "review_event:event_example"),
        requests=(
            {
                "request_id": "event_example",
                "kind": "change_request",
                "instruction": "Show the revenue as a badge.",
                "screen_id": None,
                "source_ref": f"ir:binding_{binding_id}",
                "binding_id": binding_id,
                "action_id": None,
                "intent_refs": [f"viewspec:binding:{binding_id}"],
                "content_refs": [content_ref],
            },
        ),
    )


def _result(
    diagnostics: tuple[VerificationDiagnostic, ...] = (),
    *,
    plan: VerificationPlan | None = None,
    complete: bool = True,
    lineage: RetryLineage | None = None,
    artifact: str = "artifact",
) -> VerificationResult:
    return VerificationResult.create(
        artifact_sha256=_sha(artifact),
        plan=plan or VerificationPlan.default(),
        complete=complete,
        diagnostics=diagnostics,
        lineage=lineage,
    )


def _error(code: str, source_ref: str, viewport: str = "mobile") -> VerificationDiagnostic:
    return VerificationDiagnostic(
        code=code,
        severity="error",
        message=f"{code} at {source_ref}.",
        fix="Repair the source-addressed failure.",
        source_ref=source_ref,
        viewport=viewport,
    )


def _repair_context(text: str, baseline: VerificationResult) -> IntentPatchContext:
    from viewspec.intent_patch import patch_context_from_repair_plan
    from viewspec.repair import VerificationRepairPlan

    return patch_context_from_repair_plan(
        VerificationRepairPlan.from_result(baseline),
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(text),
    )


def _patch(text: str, *, value: str = "badge", binding_id: str = "revenue_value") -> dict:
    return {
        "schema_version": 1,
        "contract_profile": "local_v1",
        "source_kind": "intent_bundle",
        "base_source_sha256": source_sha256(text),
        "operations": [
            {
                "op": "set_binding_presentation",
                "binding_id": binding_id,
                "old_value": "value",
                "value": value,
            }
        ],
        "evidence_refs": ["review:vrw_example:batch_example", "review_event:event_example"],
    }


def test_progress_certificate_requires_strict_setwise_improvement() -> None:
    first = _error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value")
    second = _error("VERIFY_A11Y_VIOLATION", "ir:binding_users_value")
    baseline = _result((first, second))

    improved = ProgressCertificate.compare(baseline, _result((second,), plan=baseline.plan))
    assert improved.accepted is True
    assert len(improved.fixed_obligations) == 1
    assert len(improved.remaining_obligations) == 1
    assert improved.introduced_obligations == ()
    assert improved.plan_sha256 == baseline.plan.plan_sha256

    unchanged = ProgressCertificate.compare(baseline, _result((first, second), plan=baseline.plan))
    assert unchanged.accepted is False
    assert unchanged.reason == "no_strict_progress"

    regression = ProgressCertificate.compare(
        baseline,
        _result((second, _error("VERIFY_RUNTIME_ERROR", "ir:binding_users_label")), plan=baseline.plan),
    )
    assert regression.accepted is False
    assert regression.reason == "introduced_error"


def test_published_convergence_task_schema_matches_runtime_example() -> None:
    Draft202012Validator.check_schema(CONVERGENCE_TASK_JSON_SCHEMA)
    validator = Draft202012Validator(CONVERGENCE_TASK_JSON_SCHEMA)
    payload = starter_convergence_task_payload()
    validator.validate(payload)
    assert payload["targets"][0]["legal_operations"]
    with pytest.raises(ValidationError):
        validator.validate({**payload, "unknown": True})


def test_progress_certificate_rejects_indeterminate_and_plan_drift() -> None:
    baseline = _result((_error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value"),))
    indeterminate = _result(
        (),
        plan=baseline.plan,
        complete=False,
        artifact="candidate-indeterminate",
    )
    assert ProgressCertificate.compare(baseline, indeterminate).reason == "candidate_indeterminate"

    drifted = _result((), plan=VerificationPlan(checks=("layout",)), artifact="candidate-drift")
    assert ProgressCertificate.compare(baseline, drifted).reason == "verification_plan_changed"


def test_verifier_start_rejects_fabricated_requests_under_real_plan_id(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    baseline = _result((_error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value"),))
    canonical = _repair_context(text, baseline)
    fabricated = IntentPatchContext(
        origin=canonical.origin,
        source_kind=canonical.source_kind,
        base_source_sha256=canonical.base_source_sha256,
        contract_profile=canonical.contract_profile,
        evidence_refs=canonical.evidence_refs,
        requests=(
            {
                "request_id": "invented",
                "code": "VERIFY_LAYOUT_OVERFLOW",
                "instruction": "Change an unrelated binding.",
                "source_ref": "ir:binding_users_value",
                "binding_id": "users_value",
                "intent_refs": ["viewspec:binding:users_value"],
                "content_refs": ["node:users#attr:value"],
            },
        ),
    )

    with pytest.raises(ConvergeError) as rejected:
        start_convergence_session(
            source,
            fabricated,
            baseline_result=baseline,
            state_root=tmp_path / "state",
        )
    assert rejected.value.code == "CONVERGE_BASELINE_INVALID"


@given(
    baseline_keys=st.sets(st.integers(min_value=0, max_value=12), min_size=1),
    candidate_keys=st.sets(st.integers(min_value=0, max_value=12)),
)
def test_property_progress_acceptance_is_exact_proper_subset(
    baseline_keys: set[int], candidate_keys: set[int]
) -> None:
    def diagnostics(keys: set[int]) -> tuple[VerificationDiagnostic, ...]:
        return tuple(
            _error("VERIFY_LAYOUT_OVERFLOW", f"ir:region_{key}") for key in sorted(keys)
        )

    plan = VerificationPlan.default()
    certificate = ProgressCertificate.compare(
        _result(diagnostics(baseline_keys), plan=plan, artifact="baseline"),
        _result(diagnostics(candidate_keys), plan=plan, artifact="candidate"),
    )
    assert certificate.accepted is (candidate_keys < baseline_keys)


def test_start_builds_exact_old_value_authoring_task_without_mutating_source(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    session = start_convergence_session(
        source,
        _review_context(text),
        state_root=tmp_path / "state",
    )

    assert session.status == "awaiting_proposal"
    assert session.mode == "review"
    assert source.read_text(encoding="utf-8") == text
    assert session.task is not None
    operations = [item for target in session.task.targets for item in target.legal_operations]
    binding = next(item for item in operations if item["op"] == "set_binding_presentation")
    assert binding["fixed_fields"] == {
        "binding_id": "revenue_value",
        "old_value": "value",
    }
    assert binding["replacement_field"] == "value"
    assert "badge" in binding["allowed_values"]
    scalar = next(
        item
        for item in operations
        if item["op"] == "replace_semantic_attr" and item["fixed_fields"]["attr"] == "value"
    )
    assert scalar["fixed_fields"]["node_id"] == "revenue"
    assert scalar["fixed_fields"]["attr"] == "value"
    assert scalar["fixed_fields"]["old_value"] == "$12.4K"
    assert session.to_json()["source_path_sha256"] == hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    assert str(source.resolve()) not in json.dumps(session.to_json())


def test_start_declares_full_revision_when_evidence_has_no_legal_target(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    session = start_convergence_session(
        source,
        _review_context(text, binding_id="missing_binding"),
        state_root=tmp_path / "state",
    )
    assert session.status == "full_revision_required"
    assert session.task is not None
    assert session.task.targets == ()


def test_app_session_scopes_legal_operation_and_apply_to_exact_screen(tmp_path: Path) -> None:
    source = tmp_path / "viewspec.app.json"
    text = json.dumps(starter_app_bundle("internal_tool"), indent=2, sort_keys=True) + "\n"
    source.write_text(text, encoding="utf-8")
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="app_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile="local_v1",
        evidence_refs=("review:vrw_app:batch_app", "review_event:event_app"),
        requests=(
            {
                "request_id": "event_app",
                "kind": "change_request",
                "instruction": "Show the first incident value as a badge.",
                "screen_id": "queue",
                "source_ref": "screen:queue/ir:binding_inc_1042_value",
                "binding_id": "inc_1042_value",
                "action_id": None,
                "intent_refs": ["viewspec:binding:inc_1042_value"],
                "content_refs": ["node:inc_1042#attr:value"],
            },
        ),
    )
    state = tmp_path / "state"
    started = start_convergence_session(source, context, state_root=state)
    operation = next(
        item
        for target in started.task.targets
        for item in target.legal_operations
        if item["op"] == "set_binding_presentation"
    )
    assert operation["fixed_fields"]["screen_id"] == "queue"

    pending = submit_convergence_patch(
        source,
        {
            "schema_version": 1,
            "contract_profile": "local_v1",
            "source_kind": "app_bundle",
            "base_source_sha256": source_sha256(text),
            "operations": [
                {
                    "op": "set_binding_presentation",
                    "screen_id": "queue",
                    "binding_id": "inc_1042_value",
                    "old_value": "value",
                    "value": "badge",
                }
            ],
            "evidence_refs": list(context.evidence_refs),
        },
        state_root=state,
    )
    applied = approve_convergence_preview(
        source,
        pending.pending_preview.approval_token,
        state_root=state,
    )
    assert applied.status == "applied"
    payload = json.loads(source.read_text(encoding="utf-8"))
    queue = next(screen for screen in payload["screens"] if screen["id"] == "queue")
    binding = next(
        item
        for item in queue["intent_bundle"]["view_spec"]["bindings"]
        if item["id"] == "inc_1042_value"
    )
    assert binding["present_as"] == "badge"


def test_app_task_exposes_aesthetic_fixture_and_visibility_operations(tmp_path: Path) -> None:
    source = tmp_path / "viewspec.app.json"
    text = json.dumps(starter_react_app_bundle(), indent=2, sort_keys=True) + "\n"
    source.write_text(text, encoding="utf-8")
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="app_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile="local_v1",
        evidence_refs=("review:vrw_app_ops:batch_app_ops", "review_event:event_app_ops"),
        requests=(
            {
                "request_id": "aesthetic",
                "kind": "change_request",
                "instruction": "Apply a supported aesthetic profile.",
                "screen_id": "queue",
                "style_id": "aesthetic_profile",
                "intent_refs": ["viewspec:style:aesthetic_profile"],
                "content_refs": [],
            },
            {
                "request_id": "fixture",
                "kind": "change_request",
                "instruction": "Update the fixture status.",
                "screen_id": "queue",
                "resource_id": "incidents",
                "record_id": "inc_1043",
                "field": "status",
                "intent_refs": ["viewspec:fixture:incidents/inc_1043/status"],
                "content_refs": ["node:inc_1043#attr:status"],
            },
            {
                "request_id": "visibility",
                "kind": "change_request",
                "instruction": "Change the visibility condition.",
                "visibility_id": "show_triaged_status",
                "intent_refs": ["viewspec:visibility:show_triaged_status"],
                "content_refs": [],
            },
        ),
    )

    session = start_convergence_session(source, context, state_root=tmp_path / "state")
    operations = {
        operation["op"]
        for target in session.task.targets
        for operation in target.legal_operations
    }
    assert {
        "set_aesthetic_profile",
        "replace_fixture_scalar",
        "set_visibility_condition",
    } <= operations
    target_kinds = {target.kind for target in session.task.targets}
    assert {"fixture", "style", "visibility"} <= target_kinds

    previewed = submit_convergence_patch(
        source,
        {
            "schema_version": 1,
            "contract_profile": "local_v1",
            "source_kind": "app_bundle",
            "base_source_sha256": source_sha256(text),
            "operations": [
                {
                    "op": "set_aesthetic_profile",
                    "screen_id": "queue",
                    "old_value": None,
                    "value": "aesthetic.calm_ops",
                },
                {
                    "op": "replace_fixture_scalar",
                    "resource_id": "incidents",
                    "record_id": "inc_1043",
                    "field": "status",
                    "old_value": "queued",
                    "value": "resolved",
                },
                {
                    "op": "replace_semantic_attr",
                    "screen_id": "queue",
                    "node_id": "inc_1043",
                    "attr": "status",
                    "old_value": "queued",
                    "value": "resolved",
                },
                {
                    "op": "set_visibility_condition",
                    "visibility_id": "show_triaged_status",
                    "old_value": {"state": "selected_incident", "is": "truthy"},
                    "value": {"selector": "active_incidents", "is": "non_empty"},
                },
            ],
            "evidence_refs": list(context.evidence_refs),
        },
        state_root=tmp_path / "state",
    )
    assert previewed.status == "awaiting_approval"


def test_review_session_preview_requires_exact_convergence_approval_before_write(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    start_convergence_session(source, _review_context(text), state_root=state)

    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    assert previewed.status == "awaiting_approval"
    assert previewed.pending_preview is not None
    assert previewed.pending_preview.progress_certificate.mode == "human_review"
    assert source.read_text(encoding="utf-8") == text

    with pytest.raises(ConvergeError) as rejected:
        approve_convergence_preview(source, "bad", state_root=state)
    assert rejected.value.code == "CONVERGE_APPROVAL_INVALID"
    assert source.read_text(encoding="utf-8") == text

    applied = approve_convergence_preview(
        source,
        previewed.pending_preview.approval_token,
        state_root=state,
    )
    assert applied.status == "applied"
    assert applied.attempts[0].receipt is not None
    assert json.loads(source.read_text(encoding="utf-8"))["view_spec"]["bindings"][1]["present_as"] == "badge"
    assert get_convergence_status(source, state_root=state) == applied


def test_patch_must_be_bound_to_session_evidence_and_legal_targets(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    start_convergence_session(source, _review_context(text), state_root=state)
    missing_evidence = _patch(text)
    missing_evidence["evidence_refs"] = []
    with pytest.raises(ConvergeError) as evidence_error:
        submit_convergence_patch(source, missing_evidence, state_root=state)
    assert evidence_error.value.code == "CONVERGE_EVIDENCE_MISMATCH"

    unrelated = _patch(text, binding_id="users_value")
    with pytest.raises(ConvergeError) as target_error:
        submit_convergence_patch(source, unrelated, state_root=state)
    assert target_error.value.code == "CONVERGE_TARGET_OUTSIDE_TASK"


def test_verifier_session_binds_progress_certificate_into_approval_and_reverifies(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    baseline = _result((_error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value"),))
    context = _repair_context(text, baseline)
    calls: list[RetryLineage] = []

    def verifier(candidate_text, source_kind, plan, lineage):
        assert source_kind == "intent_bundle"
        assert plan == baseline.plan
        calls.append(lineage)
        return _result((), plan=plan, lineage=lineage, artifact=source_sha256(candidate_text))

    start_convergence_session(source, context, baseline_result=baseline, state_root=state)
    patch = _patch(text)
    patch["evidence_refs"] = list(context.evidence_refs)
    previewed = submit_convergence_patch(source, patch, verifier=verifier, state_root=state)
    assert previewed.status == "awaiting_approval"
    assert previewed.pending_preview.progress_certificate.accepted is True
    assert len(previewed.pending_preview.progress_certificate.fixed_obligations) == 1

    applied = approve_convergence_preview(
        source,
        previewed.pending_preview.approval_token,
        verifier=verifier,
        state_root=state,
    )
    assert applied.status == "conformant"
    assert len(calls) == 2
    assert calls[0].attempt == 2
    assert calls[1].attempt == 2


def test_status_recovers_approved_source_after_session_state_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import viewspec.converge_sessions as converge_module

    source, text = _source(tmp_path)
    state = tmp_path / "state"
    context = _review_context(text)
    start_convergence_session(source, context, state_root=state)
    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    original_write = converge_module._write_state
    failed = False

    def fail_first_applied_write(source_path, state_root, session):
        nonlocal failed
        if session.status == "applied" and not failed:
            failed = True
            raise OSError("simulated convergence state fsync failure")
        return original_write(source_path, state_root, session)

    monkeypatch.setattr(converge_module, "_write_state", fail_first_applied_write)
    with pytest.raises(OSError, match="state fsync failure"):
        approve_convergence_preview(
            source,
            previewed.pending_preview.approval_token,
            state_root=state,
        )

    assert json.loads(source.read_text(encoding="utf-8"))["view_spec"]["bindings"][1]["present_as"] == "badge"
    recovered = get_convergence_status(source, state_root=state)
    assert recovered.status == "applied"
    assert recovered.current_source_sha256 == source_sha256(source.read_text(encoding="utf-8"))
    assert recovered.attempts[-1].receipt is not None


def test_status_resumes_verification_after_post_apply_verifier_failure(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    baseline = _result((_error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value"),))
    context = _repair_context(text, baseline)
    calls = 0

    def verifier(candidate_text, source_kind, plan, lineage):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated post-apply verifier crash")
        return _result(
            (),
            plan=plan,
            lineage=lineage,
            artifact=source_sha256(candidate_text),
        )

    start_convergence_session(source, context, baseline_result=baseline, state_root=state)
    patch = _patch(text)
    patch["evidence_refs"] = list(context.evidence_refs)
    previewed = submit_convergence_patch(source, patch, verifier=verifier, state_root=state)
    with pytest.raises(ConvergeError) as failure:
        approve_convergence_preview(
            source,
            previewed.pending_preview.approval_token,
            verifier=verifier,
            state_root=state,
        )
    assert failure.value.code == "CONVERGE_VERIFIER_FAILED"
    assert json.loads(source.read_text(encoding="utf-8"))["view_spec"]["bindings"][1]["present_as"] == "badge"

    resumed = get_convergence_status(source, verifier=verifier, state_root=state)
    assert resumed.status == "conformant"
    assert calls == 3


def test_nonprogressing_candidate_stalls_without_writing(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    diagnostic = _error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value")
    baseline = _result((diagnostic,))
    context = _repair_context(text, baseline)
    start_convergence_session(source, context, baseline_result=baseline, state_root=state)
    patch = _patch(text)
    patch["evidence_refs"] = list(context.evidence_refs)

    stalled = submit_convergence_patch(
        source,
        patch,
        verifier=lambda candidate, kind, plan, lineage: _result(
            (diagnostic,), plan=plan, lineage=lineage, artifact="still-broken"
        ),
        state_root=state,
    )
    assert stalled.status == "stalled"
    assert stalled.terminal_reason == "no_strict_progress"
    assert source.read_text(encoding="utf-8") == text


def test_partial_progress_reopens_authoring_task_and_cycle_is_rejected(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    first = _error("VERIFY_LAYOUT_OVERFLOW", "ir:binding_revenue_value")
    second = _error("VERIFY_A11Y_VIOLATION", "ir:binding_revenue_value")
    baseline = _result((first, second))
    context = _repair_context(text, baseline)
    candidate_result = _result((second,), plan=baseline.plan, artifact="candidate")

    def verifier(candidate, kind, plan, lineage):
        return VerificationResult.create(
            artifact_sha256=candidate_result.artifact_sha256,
            plan=plan,
            complete=True,
            diagnostics=candidate_result.diagnostics,
            lineage=lineage,
        )

    start_convergence_session(source, context, baseline_result=baseline, state_root=state)
    patch = _patch(text)
    patch["evidence_refs"] = list(context.evidence_refs)
    previewed = submit_convergence_patch(source, patch, verifier=verifier, state_root=state)
    continued = approve_convergence_preview(
        source,
        previewed.pending_preview.approval_token,
        verifier=verifier,
        state_root=state,
    )
    assert continued.status == "awaiting_proposal"
    assert continued.baseline_result.status == "nonconformant"
    assert continued.task is not None
    assert continued.attempt_count == 1

    inverse = continued.attempts[0].receipt["inverse_patch"]
    inverse["evidence_refs"] = list(continued.context.evidence_refs)
    cycled = submit_convergence_patch(source, inverse, verifier=verifier, state_root=state)
    assert cycled.status == "stalled"
    assert cycled.terminal_reason == "candidate_cycle"


def test_attempt_bound_and_human_rejection_are_terminal(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    session = start_convergence_session(source, _review_context(text), state_root=state)
    assert CONVERGE_MAX_ATTEMPTS == 3
    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    rejected = reject_convergence_preview(
        source,
        previewed.pending_preview.preview_id,
        state_root=state,
    )
    assert rejected.status == "rejected"
    assert rejected.terminal_reason == "human_rejected"
    assert source.read_text(encoding="utf-8") == text
    assert session.session_id == rejected.session_id


def test_start_archives_complete_terminal_session_before_replacement(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    first = start_convergence_session(source, _review_context(text), state_root=state)
    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    rejected = reject_convergence_preview(
        source,
        previewed.pending_preview.preview_id,
        state_root=state,
    )

    second = start_convergence_session(source, _review_context(text), state_root=state)
    source_path_sha = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()
    archive_path = state / "archive" / source_path_sha / f"{first.session_id}.json"
    assert archive_path.is_file()
    envelope = json.loads(archive_path.read_text(encoding="utf-8"))
    assert envelope["payload"]["session_id"] == first.session_id
    assert envelope["payload"]["status"] == "rejected"
    assert envelope["payload"]["terminal_reason"] == "human_rejected"
    assert envelope["payload_sha256"] == hashlib.sha256(
        json.dumps(
            envelope["payload"],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert rejected.session_id != second.session_id
    assert get_convergence_status(source, state_root=state).session_id == second.session_id


def test_deadline_source_drift_and_corrupt_state_all_fail_closed(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    start_convergence_session(source, _review_context(text), state_root=state, clock=lambda: 100.0)
    previewed = submit_convergence_patch(source, _patch(text), state_root=state, clock=lambda: 101.0)

    expired = get_convergence_status(source, state_root=state, clock=lambda: 701.0)
    assert expired.status == "exhausted"
    assert expired.terminal_reason == "deadline_exceeded"
    with pytest.raises(ConvergeError) as approval_error:
        approve_convergence_preview(
            source,
            previewed.pending_preview.approval_token,
            state_root=state,
            clock=lambda: 701.0,
        )
    assert approval_error.value.code == "CONVERGE_SESSION_EXPIRED"
    assert source.read_text(encoding="utf-8") == text

    state_file = next(state.glob("*.json"))
    state_file.write_text(state_file.read_text(encoding="utf-8") + "{}", encoding="utf-8")
    with pytest.raises(ConvergeError) as state_error:
        get_convergence_status(source, state_root=state)
    assert state_error.value.code == "CONVERGE_STATE_INVALID"


def test_active_session_symlink_and_out_of_band_source_changes_are_rejected(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / "state"
    start_convergence_session(source, _review_context(text), state_root=state)
    with pytest.raises(ConvergeError) as active_error:
        start_convergence_session(source, _review_context(text), state_root=state)
    assert active_error.value.code == "CONVERGE_SESSION_ACTIVE"

    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    source.write_text(text + " ", encoding="utf-8")
    with pytest.raises(ConvergeError) as drift_error:
        approve_convergence_preview(
            source,
            previewed.pending_preview.approval_token,
            state_root=state,
        )
    assert drift_error.value.code == "CONVERGE_SOURCE_CHANGED"

    symlink = tmp_path / "linked.intent.json"
    symlink.symlink_to(source)
    with pytest.raises(ConvergeError) as symlink_error:
        get_convergence_status(symlink, state_root=state)
    assert symlink_error.value.code == "CONVERGE_PATH_INVALID"

    real_state = tmp_path / "real-state"
    real_state.mkdir()
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(real_state, target_is_directory=True)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    fresh_source, fresh_text = _source(fresh)
    with pytest.raises(ConvergeError) as state_symlink_error:
        start_convergence_session(
            fresh_source,
            _review_context(fresh_text),
            state_root=linked_state,
        )
    assert state_symlink_error.value.code == "CONVERGE_STATE_UNSAFE"


def test_existing_shared_state_directory_is_rejected_without_chmod(tmp_path: Path) -> None:
    source, text = _source(tmp_path)
    shared_state = tmp_path / "shared-state"
    shared_state.mkdir(mode=0o755)
    shared_state.chmod(0o755)
    before = shared_state.stat().st_mode & 0o777

    with pytest.raises(ConvergeError) as rejected:
        start_convergence_session(
            source,
            _review_context(text),
            state_root=shared_state,
        )
    assert rejected.value.code == "CONVERGE_STATE_UNSAFE"
    assert shared_state.stat().st_mode & 0o777 == before
    assert list(shared_state.iterdir()) == []

    private_state = tmp_path / "private-state"
    private_state.mkdir(mode=0o700)
    private_state.chmod(0o700)
    session = start_convergence_session(
        source,
        _review_context(text),
        state_root=private_state,
    )
    assert session.status == "awaiting_proposal"


@given(st.binary(min_size=1, max_size=32))
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_no_incorrect_approval_token_can_mutate_source(tmp_path: Path, token: bytes) -> None:
    source, text = _source(tmp_path)
    state = tmp_path / token.hex()
    start_convergence_session(source, _review_context(text), state_root=state)
    previewed = submit_convergence_patch(source, _patch(text), state_root=state)
    supplied = "vcapprove_" + token.hex()
    if supplied == previewed.pending_preview.approval_token:
        pytest.skip("generated the exact random authority")
    with pytest.raises(ConvergeError):
        approve_convergence_preview(source, supplied, state_root=state)
    assert source.read_text(encoding="utf-8") == text
