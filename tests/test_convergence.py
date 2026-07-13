from __future__ import annotations

import hashlib
import json

from hypothesis import given, strategies as st
import pytest

from viewspec.convergence import run_until_conformant
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


def _result(
    bundle: dict,
    lineage: RetryLineage,
    *,
    mode: str,
    plan: VerificationPlan | None = None,
) -> VerificationResult:
    diagnostics = ()
    complete = True
    if mode == "repair":
        diagnostics = (
            VerificationDiagnostic(
                code="VERIFY_LAYOUT_OVERFLOW",
                severity="error",
                message="The grid overflows.",
                fix="Constrain the grid width.",
                source_ref="screen:queue/ir:grid",
                viewport="mobile",
            ),
        )
    elif mode == "retry":
        complete = False
        diagnostics = (
            VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message="The browser worker restarted.",
                fix="Retry the same artifact.",
            ),
        )
    return VerificationResult.create(
        artifact_sha256=_sha(bundle),
        plan=plan or VerificationPlan.default(),
        complete=complete,
        diagnostics=diagnostics,
        lineage=lineage,
    )


def test_compile_until_conformant_repairs_with_exact_lineage():
    seen_lineage = []

    def verify(bundle, lineage):
        seen_lineage.append(lineage)
        return _result(
            bundle,
            lineage,
            mode="done" if bundle.get("fixed") else "repair",
        )

    def repair(bundle, plan):
        assert plan.disposition == "repair"
        assert plan.directives[0].source_path.to_text() == "screen:queue/ir:grid"
        return {**bundle, "fixed": True}

    run = run_until_conformant(
        {"schema_version": 4, "fixed": False},
        verify_attempt=verify,
        repair_attempt=repair,
        max_attempts=3,
    )

    assert run.status == "conformant"
    assert len(run.attempts) == 2
    assert seen_lineage[0] == RetryLineage.root()
    assert seen_lineage[1].attempt == 2
    assert seen_lineage[1].parent_verification_id == (
        run.attempts[0].result.verification_id
    )
    assert run.final_result.status == "conformant"
    assert run.to_json()["attempts"][0]["repair_plan"]["disposition"] == "repair"


def test_compile_until_conformant_stops_when_repair_makes_no_change():
    run = run_until_conformant(
        {"schema_version": 4},
        verify_attempt=lambda bundle, lineage: _result(bundle, lineage, mode="repair"),
        repair_attempt=lambda bundle, plan: dict(bundle),
        max_attempts=5,
    )

    assert run.status == "stalled"
    assert len(run.attempts) == 1
    assert run.final_result.status == "nonconformant"


def test_compile_until_conformant_retries_indeterminate_without_repairing():
    calls = 0

    def verify(bundle, lineage):
        nonlocal calls
        calls += 1
        return _result(bundle, lineage, mode="retry" if calls == 1 else "done")

    def unexpected_repair(bundle, plan):
        raise AssertionError("indeterminate verification must retry unchanged input")

    run = run_until_conformant(
        {"schema_version": 4},
        verify_attempt=verify,
        repair_attempt=unexpected_repair,
        max_attempts=2,
    )

    assert run.status == "conformant"
    assert len(run.attempts) == 2
    assert run.attempts[0].input_sha256 == run.attempts[1].input_sha256


@given(st.integers(min_value=1, max_value=6))
def test_compile_until_conformant_never_exceeds_attempt_bound(max_attempts):
    def verify(bundle, lineage):
        return _result(bundle, lineage, mode="repair")

    def repair(bundle, plan):
        return {**bundle, "revision": bundle.get("revision", 0) + 1}

    run = run_until_conformant(
        {"schema_version": 4, "revision": 0},
        verify_attempt=verify,
        repair_attempt=repair,
        max_attempts=max_attempts,
    )

    assert run.status == "exhausted"
    assert len(run.attempts) == max_attempts


def test_compile_until_conformant_rejects_plan_drift():
    calls = 0

    def verify(bundle, lineage):
        nonlocal calls
        calls += 1
        plan = (
            VerificationPlan.default()
            if calls == 1
            else VerificationPlan(checks=("layout",))
        )
        return _result(bundle, lineage, mode="retry", plan=plan)

    with pytest.raises(ValueError, match="plan changed"):
        run_until_conformant(
            {"schema_version": 4},
            verify_attempt=verify,
            repair_attempt=lambda bundle, plan: bundle,
            max_attempts=2,
        )
