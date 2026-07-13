"""Client-verifiable contracts for hosted ViewSpec verification jobs."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from typing import Any, Mapping

from viewspec.convergence import ConvergenceRun, RepairAttempt, run_until_conformant
from viewspec.hosted_receipts import ReceiptPublicKey, verify_signed_receipt
from viewspec.repair import VerificationRepairPlan
from viewspec.verification import EvidenceFile, RetryLineage, VerificationPlan, VerificationResult


HOSTED_VERIFICATION_SCHEMA_VERSION = 1
HOSTED_VERIFICATION_TARGET = "react-tailwind-app"
MAX_HOSTED_EVIDENCE_FILES = 128
MAX_HOSTED_EVIDENCE_FILE_BYTES = 5_000_000
MAX_HOSTED_EVIDENCE_TOTAL_BYTES = 20_000_000
JOB_STATES = frozenset({"queued", "running", "succeeded", "failed"})


class HostedVerificationContractError(ValueError):
    """Raised when a hosted verification response cannot be trusted."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HostedVerificationContractError(f"{name} must be an object")
    return value


def _decode_base64(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise HostedVerificationContractError("Hosted evidence content_base64 must be non-empty text")
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise HostedVerificationContractError("Hosted evidence content_base64 is invalid") from exc


def verification_request_payload(
    app_bundle: dict,
    *,
    plan: VerificationPlan | None = None,
    lineage: RetryLineage | None = None,
) -> dict[str, Any]:
    """Build the canonical idempotent hosted verification request."""
    if not isinstance(app_bundle, dict):
        raise TypeError("app_bundle must be a JSON object")
    return {
        "schema_version": HOSTED_VERIFICATION_SCHEMA_VERSION,
        "target": HOSTED_VERIFICATION_TARGET,
        "app_bundle": app_bundle,
        "plan": (plan or VerificationPlan.default()).to_json(),
        "lineage": (lineage or RetryLineage.root()).to_json(),
    }


def build_verification_job_id(input_sha256: str) -> str:
    if not isinstance(input_sha256, str) or len(input_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in input_sha256):
        raise HostedVerificationContractError("Verification input_sha256 must be a lowercase SHA-256 digest")
    material = {"input_sha256": input_sha256, "type": "viewspec_verification_job_v1"}
    return f"vvj_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"


@dataclass(frozen=True)
class HostedEvidenceArtifact:
    metadata: EvidenceFile
    content: bytes

    @classmethod
    def from_json(cls, payload: Any) -> HostedEvidenceArtifact:
        data = _mapping(payload, "hosted verification artifact")
        try:
            metadata = EvidenceFile.from_json(data)
        except ValueError as exc:
            raise HostedVerificationContractError(str(exc)) from exc
        content = _decode_base64(data.get("content_base64"))
        if len(content) > MAX_HOSTED_EVIDENCE_FILE_BYTES:
            raise HostedVerificationContractError(f"Hosted evidence {metadata.path} exceeds the file size limit")
        if not metadata.verify(content):
            raise HostedVerificationContractError(f"Hosted evidence {metadata.path} integrity metadata does not match content")
        return cls(metadata=metadata, content=content)

    def to_json(self) -> dict[str, Any]:
        return {
            **self.metadata.to_json(),
            "content_base64": base64.urlsafe_b64encode(self.content).rstrip(b"=").decode("ascii"),
        }


@dataclass(frozen=True)
class HostedVerificationUsage:
    tier: str
    usage: int
    limit: int | None

    @classmethod
    def from_json(cls, payload: Any) -> HostedVerificationUsage:
        data = _mapping(payload, "hosted verification usage")
        tier = data.get("tier")
        usage = data.get("usage")
        limit = data.get("limit")
        if not isinstance(tier, str) or not tier or type(usage) is not int or usage < 0:
            raise HostedVerificationContractError("Hosted verification usage metadata is invalid")
        if limit is not None and (type(limit) is not int or limit < 0):
            raise HostedVerificationContractError("Hosted verification usage limit is invalid")
        return cls(tier=tier, usage=usage, limit=limit)


@dataclass(frozen=True)
class HostedVerificationError:
    code: str
    message: str
    retryable: bool

    @classmethod
    def from_json(cls, payload: Any) -> HostedVerificationError:
        data = _mapping(payload, "hosted verification error")
        code = data.get("code")
        message = data.get("message")
        retryable = data.get("retryable")
        if (
            not isinstance(code, str)
            or not code.startswith("VERIFY_")
            or not isinstance(message, str)
            or not message
            or type(retryable) is not bool
        ):
            raise HostedVerificationContractError("Hosted verification error is invalid")
        return cls(code=code, message=message, retryable=retryable)


@dataclass(frozen=True)
class HostedVerificationJob:
    job_id: str
    state: str
    input_sha256: str
    result: VerificationResult | None
    artifacts: tuple[HostedEvidenceArtifact, ...]
    usage: HostedVerificationUsage
    receipt: Mapping[str, Any] | None
    error: HostedVerificationError | None
    schema_version: int = HOSTED_VERIFICATION_SCHEMA_VERSION

    @property
    def repair_plan(self) -> VerificationRepairPlan:
        if self.result is None:
            raise HostedVerificationContractError(
                "Hosted verification requires a terminal result before deriving a repair plan"
            )
        return VerificationRepairPlan.from_result(self.result)

    @classmethod
    def from_json(
        cls,
        payload: Any,
        *,
        expected_request: dict,
        receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
    ) -> HostedVerificationJob:
        data = _mapping(payload, "hosted verification job")
        if data.get("schema_version") != HOSTED_VERIFICATION_SCHEMA_VERSION:
            raise HostedVerificationContractError("Unsupported hosted verification schema_version")
        input_sha = hashlib.sha256(_canonical(expected_request)).hexdigest()
        if data.get("input_sha256") != input_sha:
            raise HostedVerificationContractError("Hosted verification input hash does not match the request")
        job_id = build_verification_job_id(input_sha)
        if data.get("job_id") != job_id:
            raise HostedVerificationContractError("Hosted verification job_id does not match the request")
        state = data.get("state")
        if state not in JOB_STATES:
            raise HostedVerificationContractError("Hosted verification state is invalid")
        usage = HostedVerificationUsage.from_json(data.get("usage"))
        raw_artifacts = data.get("artifacts")
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) > MAX_HOSTED_EVIDENCE_FILES:
            raise HostedVerificationContractError("Hosted verification artifacts must be a bounded array")

        if state in {"queued", "running"}:
            if any((data.get("result") is not None, raw_artifacts, data.get("receipt") is not None, data.get("error") is not None)):
                raise HostedVerificationContractError("Pending hosted verification cannot include terminal fields")
            return cls(job_id, state, input_sha, None, (), usage, None, None)

        if state == "failed":
            if data.get("result") is not None or raw_artifacts or data.get("receipt") is not None:
                raise HostedVerificationContractError("Failed hosted verification cannot include a result, artifacts, or receipt")
            if data.get("error") is None:
                raise HostedVerificationContractError("Failed hosted verification requires an error")
            return cls(
                job_id,
                state,
                input_sha,
                None,
                (),
                usage,
                None,
                HostedVerificationError.from_json(data.get("error")),
            )

        if data.get("error") is not None:
            raise HostedVerificationContractError("Successful hosted verification cannot include an error")
        try:
            result = VerificationResult.from_json(data.get("result"))
        except ValueError as exc:
            raise HostedVerificationContractError(str(exc)) from exc
        artifacts = tuple(HostedEvidenceArtifact.from_json(item) for item in raw_artifacts)
        if len({item.metadata.path for item in artifacts}) != len(artifacts):
            raise HostedVerificationContractError("Hosted verification artifact paths must be unique")
        if sum(item.metadata.bytes for item in artifacts) > MAX_HOSTED_EVIDENCE_TOTAL_BYTES:
            raise HostedVerificationContractError("Hosted verification artifacts exceed the total size limit")
        result_evidence = [item.to_json() for item in result.evidence]
        artifact_evidence = [item.metadata.to_json() for item in artifacts]
        if result_evidence != artifact_evidence:
            raise HostedVerificationContractError("Hosted verification artifacts do not exactly match result evidence")
        receipt = _mapping(data.get("receipt"), "hosted verification receipt")
        receipt_payload = receipt.get("payload")
        expected_receipt = {
            "receipt_type": "viewspec_verification_v1",
            "job_id": job_id,
            "verification_id": result.verification_id,
            "input_sha256": input_sha,
            "result_sha256": result.result_sha256,
            "artifact_sha256": result.artifact_sha256,
            "plan_sha256": result.plan.plan_sha256,
            "status": result.status,
        }
        if not isinstance(receipt_payload, dict) or any(
            receipt_payload.get(key) != value for key, value in expected_receipt.items()
        ):
            raise HostedVerificationContractError("Signed verification receipt does not match the result")
        if not isinstance(receipt_payload.get("issued_at"), str):
            raise HostedVerificationContractError("Signed verification receipt has no issued_at timestamp")
        if receipt_public_key is None or not verify_signed_receipt(receipt, receipt_public_key):
            raise HostedVerificationContractError("Signed verification receipt is invalid")
        return cls(job_id, state, input_sha, result, artifacts, usage, receipt, None)

    def write_evidence_to(self, output_dir: str | Path) -> Path:
        if self.state != "succeeded" or self.result is None:
            raise HostedVerificationContractError("Only a successful verification has evidence")
        destination = Path(output_dir).resolve()
        if destination.exists():
            raise FileExistsError(f"Verification evidence output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
        try:
            for artifact in self.artifacts:
                parts = PurePosixPath(artifact.metadata.path).parts
                if len(parts) < 2 or parts[0] != "evidence":
                    raise HostedVerificationContractError("Hosted verification evidence must be below evidence/")
                path = staging.joinpath(*parts[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(artifact.content)
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination


def submit_verification_remote(
    app_bundle: dict,
    *,
    plan: VerificationPlan | None = None,
    lineage: RetryLineage | None = None,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
) -> HostedVerificationJob:
    """Submit one idempotent hosted verification job."""
    return _request_job(
        "post",
        f"{api_url.rstrip('/')}/v1/verifications",
        expected_request=verification_request_payload(app_bundle, plan=plan, lineage=lineage),
        api_url=api_url,
        api_key=api_key,
        receipt_public_key=receipt_public_key,
    )


def get_verification_remote(
    job_id: str,
    *,
    expected_request: dict,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
) -> HostedVerificationJob:
    """Read and verify one hosted job state."""
    return _request_job(
        "get",
        f"{api_url.rstrip('/')}/v1/verifications/{job_id}",
        expected_request=expected_request,
        api_url=api_url,
        api_key=api_key,
        receipt_public_key=receipt_public_key,
    )


def wait_verification_remote(
    job: HostedVerificationJob,
    *,
    expected_request: dict,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
    timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 1.0,
) -> HostedVerificationJob:
    """Poll a hosted job until it reaches a verified terminal state."""
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    current = job
    while current.state in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Hosted verification {job.job_id} did not finish before timeout")
        time.sleep(max(0.05, poll_interval_seconds))
        current = get_verification_remote(
            job.job_id,
            expected_request=expected_request,
            api_url=api_url,
            api_key=api_key,
            receipt_public_key=receipt_public_key,
        )
    return current


def compile_until_conformant_remote(
    app_bundle: dict,
    *,
    repair_attempt: RepairAttempt,
    plan: VerificationPlan | None = None,
    max_attempts: int = 3,
    api_url: str = "https://api.viewspec.dev",
    api_key: str | None = None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None = None,
    timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 1.0,
) -> ConvergenceRun:
    """Compile and verify paid hosted attempts until conformant or bounded."""
    selected_plan = plan or VerificationPlan.default()

    def verify_attempt(bundle: dict, lineage: RetryLineage) -> VerificationResult:
        expected_request = verification_request_payload(
            bundle,
            plan=selected_plan,
            lineage=lineage,
        )
        job = submit_verification_remote(
            bundle,
            plan=selected_plan,
            lineage=lineage,
            api_url=api_url,
            api_key=api_key,
            receipt_public_key=receipt_public_key,
        )
        if job.state in {"queued", "running"}:
            job = wait_verification_remote(
                job,
                expected_request=expected_request,
                api_url=api_url,
                api_key=api_key,
                receipt_public_key=receipt_public_key,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        if job.state == "failed":
            code = job.error.code if job.error else "VERIFY_BROWSER_EXECUTION_FAILED"
            message = job.error.message if job.error else "Hosted verification failed"
            raise HostedVerificationContractError(f"{code}: {message}")
        if job.state != "succeeded" or job.result is None:
            raise HostedVerificationContractError(
                "Hosted verification reached an invalid terminal state"
            )
        return job.result

    return run_until_conformant(
        app_bundle,
        verify_attempt=verify_attempt,
        repair_attempt=repair_attempt,
        max_attempts=max_attempts,
    )


def _request_job(
    method: str,
    url: str,
    *,
    expected_request: dict,
    api_url: str,
    api_key: str | None,
    receipt_public_key: ReceiptPublicKey | Mapping[str, Any] | None,
) -> HostedVerificationJob:
    from viewspec.compiler import CompilerAPIError, _compiler_api_error

    try:
        import httpx
    except ImportError:
        raise ImportError("httpx is required for hosted verification. Install it: pip install viewspec[remote]") from None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        if method == "post":
            response = httpx.post(url, json=expected_request, headers=headers, timeout=30.0)
        else:
            response = httpx.get(url, headers=headers, timeout=30.0)
        data = response.json()
    except httpx.HTTPError as exc:
        raise CompilerAPIError(f"Hosted verification request failed: {exc}", code="network_error") from exc
    except ValueError as exc:
        raise CompilerAPIError("Hosted verification response was not valid JSON") from exc
    if response.status_code not in {200, 202}:
        raise _compiler_api_error(response, data)
    if isinstance(data, dict) and data.get("state") == "succeeded" and receipt_public_key is None:
        try:
            key_response = httpx.get(f"{api_url.rstrip('/')}/v1/receipt-key", timeout=15.0)
            key_data = key_response.json()
        except httpx.HTTPError as exc:
            raise CompilerAPIError(f"Receipt key request failed: {exc}", code="network_error") from exc
        if key_response.status_code != 200:
            raise _compiler_api_error(key_response, key_data)
        receipt_public_key = key_data
    return HostedVerificationJob.from_json(
        data,
        expected_request=expected_request,
        receipt_public_key=receipt_public_key,
    )


__all__ = [
    "HOSTED_VERIFICATION_SCHEMA_VERSION",
    "HOSTED_VERIFICATION_TARGET",
    "HostedEvidenceArtifact",
    "HostedVerificationContractError",
    "HostedVerificationError",
    "HostedVerificationJob",
    "HostedVerificationUsage",
    "build_verification_job_id",
    "compile_until_conformant_remote",
    "get_verification_remote",
    "submit_verification_remote",
    "verification_request_payload",
    "wait_verification_remote",
]
