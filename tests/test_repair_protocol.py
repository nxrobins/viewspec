from __future__ import annotations

import hashlib

from hypothesis import given, strategies as st
import pytest

from viewspec.repair import SourceNodePath, VerificationRepairPlan
from viewspec.verification import (
    EvidenceFile,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _result(
    diagnostics: tuple[VerificationDiagnostic, ...],
    *,
    complete: bool = True,
) -> VerificationResult:
    refs = sorted({ref for item in diagnostics for ref in item.evidence_refs})
    return VerificationResult.create(
        artifact_sha256=_sha("artifact"),
        plan=VerificationPlan.default(),
        complete=complete,
        diagnostics=diagnostics,
        evidence=tuple(
            EvidenceFile.from_content(ref, "screenshot", ref.encode())
            for ref in refs
        ),
    )


def test_repair_plan_groups_one_source_defect_across_viewports():
    diagnostics = tuple(
        VerificationDiagnostic(
            code="VERIFY_LAYOUT_OVERFLOW",
            severity="error",
            message=f"Queue grid overflows at {viewport}.",
            fix="Constrain the queue grid width.",
            source_ref="screen:queue/ir:queue-grid",
            viewport=viewport,
            evidence_refs=(f"evidence/{viewport}.png",),
        )
        for viewport in ("desktop", "mobile", "tablet")
    )
    result = _result(diagnostics)

    repair = VerificationRepairPlan.from_result(result)

    assert repair.disposition == "repair"
    assert repair.previous_verification_id == result.verification_id
    assert repair.next_lineage.to_json() == {
        "attempt": 2,
        "parent_verification_id": result.verification_id,
    }
    assert len(repair.directives) == 1
    directive = repair.directives[0]
    assert directive.repair_id.startswith("vfix_")
    assert directive.code == "VERIFY_LAYOUT_OVERFLOW"
    assert directive.source_path == SourceNodePath("queue", "queue-grid")
    assert directive.viewports == ("mobile", "tablet", "desktop")
    assert directive.evidence_refs == (
        "evidence/desktop.png",
        "evidence/mobile.png",
        "evidence/tablet.png",
    )
    assert VerificationRepairPlan.from_json(repair.to_json()) == repair


def test_repair_plan_distinguishes_retry_done_and_artifact_repair():
    retry_result = _result(
        (
            VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message="Chromium exited.",
                fix="Retry on the hosted browser worker.",
            ),
        ),
        complete=False,
    )
    retry = VerificationRepairPlan.from_result(retry_result)
    done = VerificationRepairPlan.from_result(_result(()))

    assert retry.disposition == "retry"
    assert retry.directives == ()
    assert retry.retry_reason_codes == ("VERIFY_BROWSER_EXECUTION_FAILED",)
    assert retry.next_lineage.attempt == 2
    assert done.disposition == "done"
    assert done.directives == ()
    assert done.retry_reason_codes == ()
    assert done.next_lineage is None


def test_repair_plan_rejects_noncanonical_source_paths_and_forged_identity():
    result = _result(
        (
            VerificationDiagnostic(
                code="VERIFY_CONTENT_MISSING",
                severity="error",
                message="Content is missing.",
                fix="Restore the source node.",
                source_ref="../../component",
            ),
        )
    )

    with pytest.raises(ValueError, match="source node path"):
        VerificationRepairPlan.from_result(result)

    valid = VerificationRepairPlan.from_result(_result(()))
    forged = valid.to_json()
    forged["repair_plan_id"] = "vrp_" + "0" * 32
    with pytest.raises(ValueError, match="repair_plan_id"):
        VerificationRepairPlan.from_json(forged)


@given(st.permutations(("mobile", "tablet", "desktop")))
def test_repair_plan_is_invariant_to_diagnostic_order(viewports):
    diagnostics = tuple(
        VerificationDiagnostic(
            code="VERIFY_LAYOUT_OVERLAP",
            severity="error",
            message=f"Nodes overlap at {viewport}.",
            fix="Separate the sibling nodes.",
            source_ref="ir:content-grid",
            viewport=viewport,
            evidence_refs=(f"evidence/{viewport}.png",),
        )
        for viewport in viewports
    )

    plan = VerificationRepairPlan.from_result(_result(diagnostics))

    assert plan == VerificationRepairPlan.from_result(_result(tuple(reversed(diagnostics))))


def test_recurrence_fingerprint_ignores_prose_and_evidence_churn():
    first = VerificationDiagnostic(
        code="VERIFY_A11Y_VIOLATION",
        severity="error",
        message="Button has no name.",
        fix="Add an accessible name.",
        source_ref="ir:submit",
        viewport="mobile",
        evidence_refs=("evidence/first.png",),
    )
    second = VerificationDiagnostic(
        code="VERIFY_A11Y_VIOLATION",
        severity="error",
        message="The submit control remains unnamed.",
        fix="Set aria-label or visible text.",
        source_ref="ir:submit",
        viewport="mobile",
        evidence_refs=("evidence/second.png",),
    )

    first_plan = VerificationRepairPlan.from_result(_result((first,)))
    second_plan = VerificationRepairPlan.from_result(_result((second,)))

    assert first_plan.directives[0].recurrence_fingerprint == (
        second_plan.directives[0].recurrence_fingerprint
    )
