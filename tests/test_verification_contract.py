from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from hypothesis import given, strategies as st
import pytest

from viewspec.verification import (
    DEFAULT_VIEWPORTS,
    EvidenceFile,
    RetryLineage,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
    build_verification_id,
)


SHA256 = "a" * 64


def _diagnostic(
    *,
    severity: str = "error",
    code: str = "VERIFY_LAYOUT_OVERFLOW",
    evidence_refs: tuple[str, ...] = (),
) -> VerificationDiagnostic:
    return VerificationDiagnostic(
        code=code,
        severity=severity,
        message="Content overflows its viewport.",
        fix="Constrain the source node width and retry verification.",
        source_ref="ir:content-grid",
        viewport="mobile",
        evidence_refs=evidence_refs,
    )


def test_default_plan_is_a_canonical_cross_viewport_contract():
    plan = VerificationPlan.default()

    assert plan.schema_version == 1
    assert plan.viewports == DEFAULT_VIEWPORTS
    assert [(item.name, item.width, item.height) for item in plan.viewports] == [
        ("mobile", 390, 844),
        ("tablet", 768, 1024),
        ("desktop", 1440, 1000),
    ]
    assert plan.checks == (
        "accessibility",
        "content",
        "interaction",
        "layout",
        "runtime",
    )


def test_plan_rejects_duplicate_names_dimensions_and_unknown_checks():
    with pytest.raises(ValueError, match="viewport names"):
        VerificationPlan.from_json(
            {
                "schema_version": 1,
                "viewports": [
                    {"name": "mobile", "width": 390, "height": 844},
                    {"name": "mobile", "width": 412, "height": 915},
                ],
                "checks": ["layout"],
            }
        )
    with pytest.raises(ValueError, match="viewport dimensions"):
        VerificationPlan.from_json(
            {
                "schema_version": 1,
                "viewports": [
                    {"name": "small", "width": 390, "height": 844},
                    {"name": "also-small", "width": 390, "height": 844},
                ],
                "checks": ["layout"],
            }
        )
    with pytest.raises(ValueError, match="Unsupported verification check"):
        VerificationPlan.from_json(
            {
                "schema_version": 1,
                "viewports": [{"name": "mobile", "width": 390, "height": 844}],
                "checks": ["looks_good_to_me"],
            }
        )


@given(
    artifact_sha=st.binary(min_size=0, max_size=128).map(lambda value: hashlib.sha256(value).hexdigest()),
    plan_sha=st.binary(min_size=0, max_size=128).map(lambda value: hashlib.sha256(value).hexdigest()),
)
def test_verification_id_is_deterministic_and_domain_separated(artifact_sha: str, plan_sha: str):
    first = build_verification_id(artifact_sha, plan_sha)
    second = build_verification_id(artifact_sha, plan_sha)

    assert first == second
    assert first.startswith("vvr_")
    assert len(first) == 36
    assert first != build_verification_id(plan_sha, artifact_sha) or artifact_sha == plan_sha


@given(
    names=st.lists(
        st.sampled_from(["mobile", "tablet", "desktop"]),
        min_size=3,
        max_size=3,
        unique=True,
    )
)
def test_plan_hash_is_stable_regardless_of_input_viewport_order(names: list[str]):
    by_name = {item.name: item for item in DEFAULT_VIEWPORTS}
    plan = VerificationPlan(viewports=tuple(by_name[name] for name in names))

    assert plan.plan_sha256 == VerificationPlan.default().plan_sha256


def test_result_status_is_derived_from_completeness_and_diagnostic_severity():
    conformant = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(),
    )
    warning_only = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(_diagnostic(severity="warning", code="VERIFY_A11Y_WARNING"),),
    )
    nonconformant = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(_diagnostic(),),
    )
    indeterminate = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=False,
        diagnostics=(_diagnostic(severity="warning", code="VERIFY_BROWSER_UNAVAILABLE"),),
    )

    assert conformant.status == "conformant"
    assert warning_only.status == "conformant"
    assert nonconformant.status == "nonconformant"
    assert indeterminate.status == "indeterminate"


def test_result_rejects_forged_status_and_unreferenced_evidence():
    result = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(_diagnostic(evidence_refs=("evidence/mobile.png",)),),
        evidence=(EvidenceFile.from_content("evidence/mobile.png", "screenshot", b"png"),),
    )
    payload = result.to_json()
    payload["status"] = "conformant"
    with pytest.raises(ValueError, match="status"):
        VerificationResult.from_json(payload)

    payload = result.to_json()
    payload["diagnostics"][0]["evidence_refs"] = ["evidence/missing.png"]
    with pytest.raises(ValueError, match="evidence"):
        VerificationResult.from_json(payload)


def test_result_round_trips_as_canonical_json():
    result = VerificationResult.create(
        artifact_sha256=SHA256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(_diagnostic(evidence_refs=("evidence/mobile.png",)),),
        evidence=(EvidenceFile.from_content("evidence/mobile.png", "screenshot", b"png"),),
        lineage=RetryLineage.root(),
    )

    restored = VerificationResult.from_json(json.loads(json.dumps(result.to_json())))

    assert restored == result
    assert restored.result_sha256 == result.result_sha256


def test_retry_lineage_is_monotonic_and_binds_parent_verification():
    root = RetryLineage.root()
    child = root.next_attempt("vvr_0123456789abcdef0123456789abcdef")

    assert root.attempt == 1
    assert root.parent_verification_id is None
    assert child.attempt == 2
    assert child.parent_verification_id == "vvr_0123456789abcdef0123456789abcdef"
    with pytest.raises(ValueError, match="attempt"):
        replace(child, attempt=1)


@given(
    content=st.binary(max_size=2048),
    path=st.sampled_from(["evidence/mobile.png", "evidence/dom.json", "evidence/a11y.json"]),
)
def test_evidence_integrity_metadata_is_exact(content: bytes, path: str):
    evidence = EvidenceFile.from_content(path, "screenshot", content)

    assert evidence.bytes == len(content)
    assert evidence.sha256 == hashlib.sha256(content).hexdigest()
    assert evidence.verify(content)
    assert not evidence.verify(content + b"tampered")


@given(
    code=st.sampled_from(
        [
            "VERIFY_A11Y_VIOLATION",
            "VERIFY_CONTENT_MISSING",
            "VERIFY_INTERACTION_FAILED",
            "VERIFY_LAYOUT_OVERFLOW",
            "VERIFY_LAYOUT_OVERLAP",
            "VERIFY_RUNTIME_ERROR",
        ]
    ),
    source_ref=st.text(min_size=1, max_size=80).filter(lambda value: value.strip() == value and "\x00" not in value),
)
def test_fixable_diagnostics_preserve_stable_source_addresses(code: str, source_ref: str):
    diagnostic = VerificationDiagnostic(
        code=code,
        severity="error",
        message="Verification failed.",
        fix="Repair the referenced source node.",
        source_ref=source_ref,
        viewport="desktop",
    )

    assert VerificationDiagnostic.from_json(diagnostic.to_json()) == diagnostic
    assert diagnostic.source_ref == source_ref
