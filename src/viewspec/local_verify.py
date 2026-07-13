"""Local ViewSpec conformance verification orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable

from viewspec.host_verify import verify_host_artifact_dir
from viewspec.local_tools import atomic_write, check_artifact_dir
from viewspec.verification import (
    EvidenceFile,
    RetryLineage,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
)


MAX_ARTIFACT_IDENTITY_FILES = 128
MAX_ARTIFACT_IDENTITY_BYTES = 20_000_000


@dataclass(frozen=True)
class BrowserEvidence:
    """A browser-produced file that must be re-hashed by the SDK."""

    path: str
    role: str
    content_type: str | None = None

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "evidence":
            raise ValueError("browser evidence paths must be canonical paths below evidence/")
        if path.as_posix() != self.path or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("browser evidence path must be canonical")


@dataclass(frozen=True)
class BrowserPlanOutcome:
    """Bounded executor output before evidence integrity is established."""

    complete: bool
    diagnostics: tuple[VerificationDiagnostic, ...] = ()
    evidence: tuple[BrowserEvidence, ...] = ()

    def __post_init__(self) -> None:
        if type(self.complete) is not bool:
            raise ValueError("browser plan completeness must be boolean")


def verify_local_artifact(
    artifact_dir: str | Path,
    *,
    plan: VerificationPlan | None = None,
    evidence_dir: str | Path = ".viewspec-verification/evidence",
    report_out: str | Path | None = None,
    install: bool = False,
    lineage: RetryLineage | None = None,
) -> VerificationResult:
    """Verify a checked artifact and return a canonical conformance result."""
    artifact_path = Path(artifact_dir).resolve()
    checked = check_artifact_dir(artifact_path)
    if not checked.get("ok"):
        messages = "; ".join(str(item) for item in checked.get("errors", []))
        raise ValueError(f"Artifact preflight failed: {messages or 'unknown artifact error'}")
    selected_plan = plan or VerificationPlan.default()
    artifact_sha256 = _artifact_identity(artifact_path)
    evidence_path = Path(evidence_dir).resolve()
    _prepare_evidence_dir(evidence_path, artifact_path)
    outcome = _execute_browser_plan(artifact_path, selected_plan, evidence_path, install=install)
    evidence = _collect_evidence(evidence_path, outcome.evidence)
    result = VerificationResult.create(
        artifact_sha256=artifact_sha256,
        plan=selected_plan,
        complete=outcome.complete,
        diagnostics=outcome.diagnostics,
        evidence=evidence,
        lineage=lineage,
    )
    if report_out is not None:
        report_path = Path(report_out).resolve()
        if report_path == artifact_path or artifact_path in report_path.parents:
            raise ValueError("verification report cannot be written inside the verified artifact")
        atomic_write(report_path, json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n")
    return result


def _prepare_evidence_dir(evidence_dir: Path, artifact_dir: Path) -> None:
    if evidence_dir == artifact_dir or artifact_dir in evidence_dir.parents:
        raise ValueError("verification evidence directory cannot be inside the verified artifact")
    if evidence_dir.exists():
        if not evidence_dir.is_dir() or any(evidence_dir.iterdir()):
            raise FileExistsError(f"Verification evidence directory is not empty: {evidence_dir}")
    else:
        evidence_dir.mkdir(parents=True)


def _artifact_identity(artifact_dir: Path) -> str:
    files = sorted(path for path in artifact_dir.rglob("*") if path.is_file())
    if not files or len(files) > MAX_ARTIFACT_IDENTITY_FILES:
        raise ValueError("verified artifact has an unsupported file count")
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in files:
        if path.is_symlink():
            raise ValueError("verified artifact cannot contain symbolic links")
        relative = path.relative_to(artifact_dir).as_posix()
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > MAX_ARTIFACT_IDENTITY_BYTES:
            raise ValueError("verified artifact exceeds the identity size limit")
        entries.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _collect_evidence(evidence_dir: Path, declarations: Iterable[BrowserEvidence]) -> tuple[EvidenceFile, ...]:
    collected: list[EvidenceFile] = []
    seen: set[str] = set()
    for declaration in declarations:
        if declaration.path in seen:
            raise ValueError(f"browser executor declared duplicate evidence: {declaration.path}")
        seen.add(declaration.path)
        relative = PurePosixPath(declaration.path)
        disk_path = evidence_dir.joinpath(*relative.parts[1:])
        if not disk_path.is_file() or disk_path.is_symlink():
            raise ValueError(f"browser executor did not write declared evidence: {declaration.path}")
        collected.append(
            EvidenceFile.from_content(
                declaration.path,
                declaration.role,
                disk_path.read_bytes(),
                declaration.content_type,
            )
        )
    unexpected = sorted(
        f"evidence/{path.relative_to(evidence_dir).as_posix()}"
        for path in evidence_dir.rglob("*")
        if path.is_file() and f"evidence/{path.relative_to(evidence_dir).as_posix()}" not in seen
    )
    if unexpected:
        raise ValueError(f"browser executor wrote undeclared evidence: {unexpected[0]}")
    return tuple(collected)


_HOST_CODE_MAP = {
    "HOST_VERIFY_ACTION_COUNT_MISMATCH": "VERIFY_INTERACTION_FAILED",
    "HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING": "VERIFY_LAYOUT_CONFORMANCE_FAILED",
    "HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING": "VERIFY_CONTENT_MISSING",
    "HOST_VERIFY_ARTIFACT_CHECK_FAILED": "VERIFY_ARTIFACT_INVALID",
    "HOST_VERIFY_DOM_NODE_MISSING": "VERIFY_CONTENT_MISSING",
    "HOST_VERIFY_PAYLOAD_VALUE_MISMATCH": "VERIFY_INTERACTION_FAILED",
    "HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK": "VERIFY_LAYOUT_CONFORMANCE_FAILED",
}
_HOST_CONFORMANCE_CODES = frozenset(_HOST_CODE_MAP)


def _execute_browser_plan(
    artifact_dir: Path,
    plan: VerificationPlan,
    evidence_dir: Path,
    *,
    install: bool,
) -> BrowserPlanOutcome:
    """Execute the legacy bounded host proof behind the new stable contract."""
    host_report_path = evidence_dir / "host-report.json"
    report = verify_host_artifact_dir(
        artifact_dir,
        install=install,
        report_out=host_report_path,
        verification_plan=plan,
        evidence_dir=evidence_dir,
    )
    errors = report.get("errors", []) if isinstance(report.get("errors"), list) else []
    diagnostics: list[VerificationDiagnostic] = []
    raw_browser_diagnostics = report.get("verification_diagnostics", [])
    if isinstance(raw_browser_diagnostics, list):
        diagnostics.extend(
            VerificationDiagnostic.from_json(item)
            for item in raw_browser_diagnostics
            if isinstance(item, dict)
        )
    complete = bool(report.get("ok"))
    for error in errors:
        if not isinstance(error, dict):
            continue
        host_code = str(error.get("code") or "HOST_VERIFY_BROWSER_RUNTIME_ERROR")
        is_conformance = host_code in _HOST_CONFORMANCE_CODES
        complete = complete or is_conformance
        diagnostics.append(
            VerificationDiagnostic(
                code=_HOST_CODE_MAP.get(host_code, "VERIFY_BROWSER_EXECUTION_FAILED"),
                severity="error" if is_conformance else "warning",
                message=_bounded_error_text(
                    error.get("message"),
                    "Browser verification failed.",
                ),
                fix=_bounded_error_text(
                    error.get("fix"),
                    "Repair the artifact or browser environment and retry.",
                ),
                evidence_refs=("evidence/host-report.json",),
            )
        )
    if not report.get("ok") and not diagnostics:
        diagnostics.append(
            VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message="Browser verification did not produce a conformance result.",
                fix="Inspect the host report, repair the environment, and retry.",
                evidence_refs=("evidence/host-report.json",),
            )
        )
    evidence = [BrowserEvidence("evidence/host-report.json", "log", "application/json")]
    raw_evidence = report.get("evidence", [])
    if isinstance(raw_evidence, list):
        evidence.extend(
            BrowserEvidence(
                path=str(item.get("path")),
                role=str(item.get("role")),
                content_type=str(item.get("content_type")) if item.get("content_type") else None,
            )
            for item in raw_evidence
            if isinstance(item, dict)
        )
    return BrowserPlanOutcome(
        complete=complete,
        diagnostics=tuple(diagnostics),
        evidence=tuple(evidence),
    )


def _bounded_error_text(value: object, fallback: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text[:4096].rstrip() or fallback


__all__ = ["BrowserEvidence", "BrowserPlanOutcome", "verify_local_artifact"]
