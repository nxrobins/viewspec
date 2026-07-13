from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from viewspec import starter_react_app_bundle
from viewspec.hosted_verification import (
    HostedVerificationContractError,
    HostedVerificationJob,
    build_verification_job_id,
    compile_until_conformant_remote,
    verification_request_payload,
)
from viewspec.verification import (
    EvidenceFile,
    RetryLineage,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_and_receipt(payload: dict) -> tuple[dict, dict]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"hosted-verification-test").digest())
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = f"vsk_{hashlib.sha256(public_bytes).hexdigest()[:16]}"
    key = {"algorithm": "ed25519", "key_id": key_id, "public_key": _encoded(public_bytes)}
    receipt = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "payload": payload,
        "signature": _encoded(private_key.sign(_canonical(payload))),
    }
    return key, receipt


def _completed_payload() -> tuple[dict, dict, dict]:
    bundle = starter_react_app_bundle()
    plan = VerificationPlan.default()
    request = verification_request_payload(bundle, plan=plan, lineage=RetryLineage.root())
    input_sha = hashlib.sha256(_canonical(request)).hexdigest()
    job_id = build_verification_job_id(input_sha)
    evidence_content = {
        "evidence/mobile.png": b"png-mobile",
        "evidence/mobile.dom.json": b'{"nodes":[]}',
    }
    evidence = tuple(
        EvidenceFile.from_content(
            path,
            "screenshot" if path.endswith(".png") else "dom",
            content,
        )
        for path, content in evidence_content.items()
    )
    result = VerificationResult.create(
        artifact_sha256="a" * 64,
        plan=plan,
        complete=True,
        diagnostics=(),
        evidence=evidence,
        lineage=RetryLineage.root(),
    )
    artifacts = [
        {
            **item.to_json(),
            "content_base64": _encoded(evidence_content[item.path]),
        }
        for item in result.evidence
    ]
    receipt_payload = {
        "receipt_type": "viewspec_verification_v1",
        "job_id": job_id,
        "verification_id": result.verification_id,
        "input_sha256": input_sha,
        "result_sha256": result.result_sha256,
        "artifact_sha256": result.artifact_sha256,
        "plan_sha256": result.plan.plan_sha256,
        "status": result.status,
        "issued_at": "2026-07-12T12:00:00Z",
    }
    key, receipt = _key_and_receipt(receipt_payload)
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "state": "succeeded",
        "input_sha256": input_sha,
        "result": result.to_json(),
        "artifacts": artifacts,
        "usage": {"tier": "pro", "usage": 1, "limit": 10_000},
        "receipt": receipt,
        "error": None,
    }
    return payload, key, request


def test_completed_hosted_verification_is_fully_client_verifiable(tmp_path):
    payload, key, request = _completed_payload()

    job = HostedVerificationJob.from_json(payload, expected_request=request, receipt_public_key=key)
    output = job.write_evidence_to(tmp_path / "evidence")

    assert job.state == "succeeded"
    assert job.result is not None
    assert job.result.status == "conformant"
    assert job.repair_plan.disposition == "done"
    assert job.result.result_sha256 == payload["receipt"]["payload"]["result_sha256"]
    assert output.joinpath("mobile.png").read_bytes() == b"png-mobile"
    assert output.joinpath("mobile.dom.json").read_bytes() == b'{"nodes":[]}'


@pytest.mark.parametrize(
    "mutation",
    [
        "job_id",
        "input_sha",
        "result_status",
        "result_hash",
        "artifact_content",
        "artifact_hash",
        "missing_artifact",
        "receipt_payload",
        "signature",
    ],
)
def test_completed_hosted_verification_rejects_tampering(mutation):
    payload, key, request = _completed_payload()
    payload = deepcopy(payload)
    if mutation == "job_id":
        payload["job_id"] = "vvj_" + "0" * 32
    elif mutation == "input_sha":
        payload["input_sha256"] = "0" * 64
    elif mutation == "result_status":
        payload["result"]["status"] = "nonconformant"
    elif mutation == "result_hash":
        payload["receipt"]["payload"]["result_sha256"] = "0" * 64
    elif mutation == "artifact_content":
        payload["artifacts"][0]["content_base64"] = _encoded(b"tampered")
    elif mutation == "artifact_hash":
        payload["artifacts"][0]["sha256"] = "0" * 64
    elif mutation == "missing_artifact":
        payload["artifacts"].pop()
    elif mutation == "receipt_payload":
        payload["receipt"]["payload"]["status"] = "nonconformant"
    elif mutation == "signature":
        payload["receipt"]["signature"] = "invalid"

    with pytest.raises(HostedVerificationContractError):
        HostedVerificationJob.from_json(payload, expected_request=request, receipt_public_key=key)


def test_pending_job_has_no_result_artifacts_or_receipt():
    _, _, request = _completed_payload()
    input_sha = hashlib.sha256(_canonical(request)).hexdigest()
    payload = {
        "schema_version": 1,
        "job_id": build_verification_job_id(input_sha),
        "state": "queued",
        "input_sha256": input_sha,
        "result": None,
        "artifacts": [],
        "usage": {"tier": "pro", "usage": 0, "limit": 10_000},
        "receipt": None,
        "error": None,
    }

    job = HostedVerificationJob.from_json(payload, expected_request=request)

    assert job.state == "queued"
    assert job.result is None
    with pytest.raises(HostedVerificationContractError, match="terminal result"):
        _ = job.repair_plan


def test_failed_job_requires_a_stable_error_and_cannot_claim_result():
    _, _, request = _completed_payload()
    input_sha = hashlib.sha256(_canonical(request)).hexdigest()
    payload = {
        "schema_version": 1,
        "job_id": build_verification_job_id(input_sha),
        "state": "failed",
        "input_sha256": input_sha,
        "result": None,
        "artifacts": [],
        "usage": {"tier": "pro", "usage": 1, "limit": 10_000},
        "receipt": None,
        "error": {
            "code": "VERIFY_BROWSER_EXECUTION_FAILED",
            "message": "The isolated browser worker failed.",
            "retryable": True,
        },
    }

    job = HostedVerificationJob.from_json(payload, expected_request=request)

    assert job.state == "failed"
    assert job.error is not None
    assert job.error.code == "VERIFY_BROWSER_EXECUTION_FAILED"
    payload["error"] = None
    with pytest.raises(HostedVerificationContractError, match="error"):
        HostedVerificationJob.from_json(payload, expected_request=request)


def test_job_id_is_deterministic_and_domain_separated():
    digest = hashlib.sha256(b"request").hexdigest()

    assert build_verification_job_id(digest) == build_verification_job_id(digest)
    assert build_verification_job_id(digest).startswith("vvj_")
    assert build_verification_job_id(digest) != f"vvr_{digest[:32]}"


def test_compile_until_conformant_remote_uses_paid_verification_lineage(monkeypatch):
    calls = []

    def fake_submit(
        bundle,
        *,
        plan,
        lineage,
        api_url,
        api_key,
        receipt_public_key,
    ):
        calls.append((bundle, lineage, api_url, api_key))
        diagnostics = ()
        if not bundle.get("fixed"):
            diagnostics = (
                VerificationDiagnostic(
                    code="VERIFY_LAYOUT_OVERFLOW",
                    severity="error",
                    message="The grid overflows.",
                    fix="Constrain the grid.",
                    source_ref="screen:queue/ir:grid",
                    viewport="mobile",
                ),
            )
        result = VerificationResult.create(
            artifact_sha256=hashlib.sha256(_canonical(bundle)).hexdigest(),
            plan=plan,
            complete=True,
            diagnostics=diagnostics,
            lineage=lineage,
        )
        return SimpleNamespace(state="succeeded", result=result, error=None)

    monkeypatch.setattr(
        "viewspec.hosted_verification.submit_verification_remote",
        fake_submit,
    )

    run = compile_until_conformant_remote(
        {"schema_version": 4, "fixed": False},
        repair_attempt=lambda bundle, repair: {**bundle, "fixed": True},
        api_url="https://api.example.test",
        api_key="secret",
        receipt_public_key={"test": "key"},
        max_attempts=3,
    )

    assert run.status == "conformant"
    assert len(calls) == 2
    assert calls[0][1] == RetryLineage.root()
    assert calls[1][1].parent_verification_id == run.attempts[0].result.verification_id
