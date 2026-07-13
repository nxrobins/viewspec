"""Stable contracts for local and hosted ViewSpec conformance verification."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping


VERIFICATION_SCHEMA_VERSION = 1
VERIFICATION_ID_RE = re.compile(r"^vvr_[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CHECKS = frozenset({"accessibility", "content", "interaction", "layout", "runtime"})
ALLOWED_SEVERITIES = frozenset({"info", "warning", "error"})
ALLOWED_STATUSES = frozenset({"conformant", "nonconformant", "indeterminate"})
ALLOWED_EVIDENCE_ROLES = frozenset({"accessibility", "dom", "log", "screenshot", "trace"})
VERIFICATION_DIAGNOSTIC_CODES = frozenset(
    {
        "VERIFY_A11Y_VIOLATION",
        "VERIFY_ARTIFACT_INVALID",
        "VERIFY_BROWSER_EXECUTION_FAILED",
        "VERIFY_CONTENT_MISSING",
        "VERIFY_INTERACTION_FAILED",
        "VERIFY_LAYOUT_CONFORMANCE_FAILED",
        "VERIFY_LAYOUT_OVERFLOW",
        "VERIFY_LAYOUT_OVERLAP",
        "VERIFY_RESULT_INDETERMINATE",
        "VERIFY_RUNTIME_ERROR",
    }
)
MAX_VIEWPORTS = 8
MAX_EVIDENCE_FILES = 128
MAX_DIAGNOSTICS = 1024


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _required_text(value: Any, name: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\0" in value:
        raise ValueError(f"{name} must be non-empty trimmed text")
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _safe_evidence_path(value: Any) -> str:
    candidate = _required_text(value, "evidence path", max_length=512)
    if "\\" in candidate:
        raise ValueError("evidence path must be a POSIX path")
    path = PurePosixPath(candidate)
    if path.is_absolute() or path.as_posix() != candidate or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("evidence path must be canonical and relative")
    return candidate


@dataclass(frozen=True, order=True)
class VerificationViewport:
    """One deterministic browser viewport in a verification plan."""

    width: int
    height: int
    name: str = field(compare=False)

    def __post_init__(self) -> None:
        _required_text(self.name, "viewport name", max_length=64)
        if type(self.width) is not int or not 240 <= self.width <= 7680:
            raise ValueError("viewport width must be an integer between 240 and 7680")
        if type(self.height) is not int or not 240 <= self.height <= 7680:
            raise ValueError("viewport height must be an integer between 240 and 7680")

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "width": self.width, "height": self.height}

    @classmethod
    def from_json(cls, payload: Any) -> VerificationViewport:
        data = _mapping(payload, "verification viewport")
        return cls(name=data.get("name"), width=data.get("width"), height=data.get("height"))


DEFAULT_VIEWPORTS = (
    VerificationViewport(name="mobile", width=390, height=844),
    VerificationViewport(name="tablet", width=768, height=1024),
    VerificationViewport(name="desktop", width=1440, height=1000),
)


@dataclass(frozen=True)
class VerificationPlan:
    """Canonical checks and viewports used to judge conformance."""

    viewports: tuple[VerificationViewport, ...] = DEFAULT_VIEWPORTS
    checks: tuple[str, ...] = tuple(sorted(ALLOWED_CHECKS))
    schema_version: int = VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VERIFICATION_SCHEMA_VERSION:
            raise ValueError("Unsupported verification plan schema_version")
        viewports = tuple(sorted(self.viewports))
        if not viewports or len(viewports) > MAX_VIEWPORTS:
            raise ValueError(f"verification plan requires between 1 and {MAX_VIEWPORTS} viewports")
        if len({item.name for item in viewports}) != len(viewports):
            raise ValueError("verification viewport names must be unique")
        if len({(item.width, item.height) for item in viewports}) != len(viewports):
            raise ValueError("verification viewport dimensions must be unique")
        checks = tuple(sorted(set(self.checks)))
        if not checks:
            raise ValueError("verification plan requires at least one check")
        unknown = [item for item in checks if item not in ALLOWED_CHECKS]
        if unknown:
            raise ValueError(f"Unsupported verification check: {unknown[0]}")
        object.__setattr__(self, "viewports", viewports)
        object.__setattr__(self, "checks", checks)

    @classmethod
    def default(cls) -> VerificationPlan:
        return cls()

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_json())).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "viewports": [item.to_json() for item in self.viewports],
            "checks": list(self.checks),
        }

    @classmethod
    def from_json(cls, payload: Any) -> VerificationPlan:
        data = _mapping(payload, "verification plan")
        raw_viewports = data.get("viewports")
        raw_checks = data.get("checks")
        if not isinstance(raw_viewports, list) or not isinstance(raw_checks, list):
            raise ValueError("verification plan viewports and checks must be arrays")
        if not all(isinstance(item, str) for item in raw_checks):
            raise ValueError("verification plan checks must be strings")
        return cls(
            schema_version=data.get("schema_version"),
            viewports=tuple(VerificationViewport.from_json(item) for item in raw_viewports),
            checks=tuple(raw_checks),
        )


def build_verification_id(artifact_sha256: str, plan_sha256: str) -> str:
    """Build a deterministic, domain-separated verification identifier."""
    material = {
        "artifact_sha256": _sha256(artifact_sha256, "artifact_sha256"),
        "plan_sha256": _sha256(plan_sha256, "plan_sha256"),
        "type": "viewspec_verification_v1",
    }
    return f"vvr_{hashlib.sha256(_canonical_json(material)).hexdigest()[:32]}"


@dataclass(frozen=True)
class RetryLineage:
    """Monotonic ancestry for agent repair attempts."""

    attempt: int
    parent_verification_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("retry attempt must be a positive integer")
        if self.attempt == 1 and self.parent_verification_id is not None:
            raise ValueError("retry attempt 1 cannot have a parent verification")
        if self.attempt > 1 and (
            not isinstance(self.parent_verification_id, str)
            or not VERIFICATION_ID_RE.fullmatch(self.parent_verification_id)
        ):
            raise ValueError("retry attempts after 1 require a valid parent verification id")

    @classmethod
    def root(cls) -> RetryLineage:
        return cls(attempt=1)

    def next_attempt(self, parent_verification_id: str) -> RetryLineage:
        return RetryLineage(attempt=self.attempt + 1, parent_verification_id=parent_verification_id)

    def to_json(self) -> dict[str, Any]:
        return {"attempt": self.attempt, "parent_verification_id": self.parent_verification_id}

    @classmethod
    def from_json(cls, payload: Any) -> RetryLineage:
        data = _mapping(payload, "retry lineage")
        return cls(attempt=data.get("attempt"), parent_verification_id=data.get("parent_verification_id"))


@dataclass(frozen=True)
class EvidenceFile:
    """Integrity metadata for one verification evidence artifact."""

    path: str
    role: str
    sha256: str
    bytes: int
    content_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _safe_evidence_path(self.path))
        if self.role not in ALLOWED_EVIDENCE_ROLES:
            raise ValueError(f"Unsupported evidence role: {self.role}")
        _sha256(self.sha256, "evidence sha256")
        if type(self.bytes) is not int or self.bytes < 0:
            raise ValueError("evidence bytes must be a non-negative integer")
        _required_text(self.content_type, "evidence content_type", max_length=128)

    @classmethod
    def from_content(cls, path: str, role: str, content: bytes, content_type: str | None = None) -> EvidenceFile:
        if not isinstance(content, bytes):
            raise TypeError("evidence content must be bytes")
        inferred = content_type or {
            "accessibility": "application/json",
            "dom": "application/json",
            "log": "text/plain",
            "screenshot": "image/png",
            "trace": "application/zip",
        }.get(role, "application/octet-stream")
        return cls(
            path=path,
            role=role,
            sha256=hashlib.sha256(content).hexdigest(),
            bytes=len(content),
            content_type=inferred,
        )

    def verify(self, content: bytes) -> bool:
        return isinstance(content, bytes) and len(content) == self.bytes and hashlib.sha256(content).hexdigest() == self.sha256

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "content_type": self.content_type,
        }

    @classmethod
    def from_json(cls, payload: Any) -> EvidenceFile:
        data = _mapping(payload, "verification evidence")
        return cls(
            path=data.get("path"),
            role=data.get("role"),
            sha256=data.get("sha256"),
            bytes=data.get("bytes"),
            content_type=data.get("content_type"),
        )


@dataclass(frozen=True)
class VerificationDiagnostic:
    """Stable, source-addressable feedback suitable for an agent repair loop."""

    code: str
    severity: str
    message: str
    fix: str
    source_ref: str | None = None
    viewport: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        code = _required_text(self.code, "diagnostic code", max_length=96)
        if not code.startswith("VERIFY_") or not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValueError("verification diagnostic code must use the stable VERIFY_* namespace")
        if self.severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"Unsupported diagnostic severity: {self.severity}")
        _required_text(self.message, "diagnostic message")
        _required_text(self.fix, "diagnostic fix")
        if self.source_ref is not None:
            _required_text(self.source_ref, "diagnostic source_ref", max_length=512)
        if self.viewport is not None:
            _required_text(self.viewport, "diagnostic viewport", max_length=64)
        refs = tuple(sorted({_safe_evidence_path(item) for item in self.evidence_refs}))
        object.__setattr__(self, "evidence_refs", refs)

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
            "source_ref": self.source_ref,
            "viewport": self.viewport,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_json(cls, payload: Any) -> VerificationDiagnostic:
        data = _mapping(payload, "verification diagnostic")
        raw_refs = data.get("evidence_refs", [])
        if not isinstance(raw_refs, list) or not all(isinstance(item, str) for item in raw_refs):
            raise ValueError("diagnostic evidence_refs must be an array of paths")
        return cls(
            code=data.get("code"),
            severity=data.get("severity"),
            message=data.get("message"),
            fix=data.get("fix"),
            source_ref=data.get("source_ref"),
            viewport=data.get("viewport"),
            evidence_refs=tuple(raw_refs),
        )


def _diagnostic_key(item: VerificationDiagnostic) -> tuple[str, str, str, str, str]:
    return (item.viewport or "", item.source_ref or "", item.code, item.severity, item.message)


def _derived_status(complete: bool, diagnostics: Iterable[VerificationDiagnostic]) -> str:
    if not complete:
        return "indeterminate"
    return "nonconformant" if any(item.severity == "error" for item in diagnostics) else "conformant"


@dataclass(frozen=True)
class VerificationResult:
    """Canonical local or hosted conformance result."""

    verification_id: str
    status: str
    complete: bool
    artifact_sha256: str
    plan: VerificationPlan
    diagnostics: tuple[VerificationDiagnostic, ...] = ()
    evidence: tuple[EvidenceFile, ...] = ()
    lineage: RetryLineage = field(default_factory=RetryLineage.root)
    schema_version: int = VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VERIFICATION_SCHEMA_VERSION:
            raise ValueError("Unsupported verification result schema_version")
        artifact_sha = _sha256(self.artifact_sha256, "artifact_sha256")
        expected_id = build_verification_id(artifact_sha, self.plan.plan_sha256)
        if self.verification_id != expected_id:
            raise ValueError("verification_id does not match artifact and plan")
        diagnostics = tuple(sorted(self.diagnostics, key=_diagnostic_key))
        evidence = tuple(sorted(self.evidence, key=lambda item: item.path))
        if len(diagnostics) > MAX_DIAGNOSTICS:
            raise ValueError("verification result contains too many diagnostics")
        if len(evidence) > MAX_EVIDENCE_FILES:
            raise ValueError("verification result contains too many evidence files")
        if len({item.path for item in evidence}) != len(evidence):
            raise ValueError("verification evidence paths must be unique")
        evidence_paths = {item.path for item in evidence}
        missing_refs = sorted({ref for item in diagnostics for ref in item.evidence_refs} - evidence_paths)
        if missing_refs:
            raise ValueError(f"verification diagnostic references missing evidence: {missing_refs[0]}")
        expected_status = _derived_status(self.complete, diagnostics)
        if self.status not in ALLOWED_STATUSES or self.status != expected_status:
            raise ValueError(f"verification status must be derived as {expected_status}")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "evidence", evidence)

    @classmethod
    def create(
        cls,
        *,
        artifact_sha256: str,
        plan: VerificationPlan,
        complete: bool,
        diagnostics: Iterable[VerificationDiagnostic],
        evidence: Iterable[EvidenceFile] = (),
        lineage: RetryLineage | None = None,
    ) -> VerificationResult:
        if type(complete) is not bool:
            raise ValueError("verification completeness must be boolean")
        diagnostic_items = tuple(diagnostics)
        return cls(
            verification_id=build_verification_id(artifact_sha256, plan.plan_sha256),
            status=_derived_status(complete, diagnostic_items),
            complete=complete,
            artifact_sha256=artifact_sha256,
            plan=plan,
            diagnostics=diagnostic_items,
            evidence=tuple(evidence),
            lineage=lineage or RetryLineage.root(),
        )

    @property
    def result_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_json())).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verification_id": self.verification_id,
            "status": self.status,
            "complete": self.complete,
            "artifact_sha256": self.artifact_sha256,
            "plan": self.plan.to_json(),
            "plan_sha256": self.plan.plan_sha256,
            "diagnostics": [item.to_json() for item in self.diagnostics],
            "evidence": [item.to_json() for item in self.evidence],
            "lineage": self.lineage.to_json(),
        }

    @classmethod
    def from_json(cls, payload: Any) -> VerificationResult:
        data = _mapping(payload, "verification result")
        if type(data.get("complete")) is not bool:
            raise ValueError("verification completeness must be boolean")
        raw_diagnostics = data.get("diagnostics")
        raw_evidence = data.get("evidence")
        if not isinstance(raw_diagnostics, list) or not isinstance(raw_evidence, list):
            raise ValueError("verification diagnostics and evidence must be arrays")
        plan = VerificationPlan.from_json(data.get("plan"))
        if data.get("plan_sha256") != plan.plan_sha256:
            raise ValueError("verification plan_sha256 does not match plan")
        return cls(
            schema_version=data.get("schema_version"),
            verification_id=data.get("verification_id"),
            status=data.get("status"),
            complete=data.get("complete"),
            artifact_sha256=data.get("artifact_sha256"),
            plan=plan,
            diagnostics=tuple(VerificationDiagnostic.from_json(item) for item in raw_diagnostics),
            evidence=tuple(EvidenceFile.from_json(item) for item in raw_evidence),
            lineage=RetryLineage.from_json(data.get("lineage")),
        )


__all__ = [
    "ALLOWED_CHECKS",
    "DEFAULT_VIEWPORTS",
    "EvidenceFile",
    "RetryLineage",
    "VERIFICATION_SCHEMA_VERSION",
    "VERIFICATION_DIAGNOSTIC_CODES",
    "VerificationDiagnostic",
    "VerificationPlan",
    "VerificationResult",
    "VerificationViewport",
    "build_verification_id",
]
