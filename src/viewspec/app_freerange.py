"""Fail-closed Freerange analysis for generated React AppBundle numeric kernels.

Freerange 0.0.1 exposes a human-readable CLI rather than a versioned JSON API.  This
module is therefore deliberately a narrow, version-pinned protocol adapter.  It only
accepts generated, manifest-described kernel files and it validates the complete
findings and audit transcripts before returning proof evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


FREERANGE_PACKAGE = "@chenglou/freerange"
FREERANGE_VERSION = "0.0.1"
FREERANGE_BIN = "fr.ts"
FREERANGE_NPM_RESOLVED = "https://registry.npmjs.org/@chenglou/freerange/-/freerange-0.0.1.tgz"
FREERANGE_NPM_INTEGRITY = (
    "sha512-RCdvTZX66Dp5roRrld+2GH4tJV+uyo21nEsF/lxwDBjzDFagG9CnJ7go5Qim2ZDHTC40lQWNF1AprDxTDQTxfg=="
)
FREERANGE_TYPESCRIPT_VERSION = "6.0.3"
FREERANGE_TYPESCRIPT_RESOLVED = "https://registry.npmjs.org/typescript/-/typescript-6.0.3.tgz"
FREERANGE_TYPESCRIPT_INTEGRITY = (
    "sha512-y2TvuxSZPDyQakkFRPZHKFm+KKVqIisdg9/CZwm9ftvKXLP8NRWj38/ODjNbr43SsoXqNuAisEf1GdCxqWcdBw=="
)
FREERANGE_PACKAGE_TREE = {
    "sha256": "1e8956228ca6237072f1d048c4d439ed896d35bef7e78f64ba882e13f0948f10",
    "bytes": 559_988,
    "files": 36,
}
FREERANGE_TYPESCRIPT_TREE = {
    "sha256": "f2c05789b8cdc36eb97c5a4393d4d472d11aabb32d604e10c01e5ae342763764",
    "bytes": 24_346_827,
    "files": 140,
}
FREERANGE_PROTOCOL = "viewspec.freerange-text-v0.0.1"
FREERANGE_COMMAND_TIMEOUT_SECONDS = 30.0
FREERANGE_RUNTIME_TIMEOUT_SECONDS = 5.0
FREERANGE_COMMAND_OUTPUT_MAX_BYTES = 128 * 1024
FREERANGE_JSON_MAX_BYTES = 4 * 1024 * 1024
FREERANGE_SOURCE_MAX_BYTES = 2 * 1024 * 1024
FREERANGE_TOOL_TREE_MAX_BYTES = 96 * 1024 * 1024
FREERANGE_BUN_MAX_BYTES = 256 * 1024 * 1024
FREERANGE_MAX_FILES = 8
FREERANGE_MAX_CALL_SITES = 16
FREERANGE_MAX_FUNCTIONS = 64
FREERANGE_MAX_CONTRACTS_PER_FUNCTION = 32
FREERANGE_MAX_FINDINGS = 128

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FUNCTION_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$")
_FINDING_RE = re.compile(
    r"^(?P<file>.+)\((?P<line>[1-9][0-9]*),(?P<column>[1-9][0-9]*)\): "
    r"(?P<level>error|warning) \[(?P<rule>[a-z0-9-]+)\]: (?P<message>.+)$"
)
_FINDING_SUMMARY_RE = re.compile(
    r"^(?P<count>[0-9]+) (?P<finding_word>finding|findings) "
    r"\((?P<errors>[0-9]+) (?P<error_word>error|errors), "
    r"(?P<warnings>[0-9]+) (?P<warning_word>warning|warnings)\)\.$"
)
_FINDINGS_COVERAGE_RE = re.compile(
    r"^coverage: (?P<analyzed>[0-9]+)/(?P<functions>[0-9]+) named top-level function declarations fully analyzed; "
    r"(?P<partial>[0-9]+) partially supported; (?P<unsupported>[0-9]+) unsupported\.$"
)
_AUDIT_HEADER_RE = re.compile(r"^# (?P<file>.+) \((?P<coverage>.+)\)$")
_AUDIT_FUNCTION_COVERAGE_RE = re.compile(
    r"^(?P<analyzed>[0-9]+)/(?P<functions>[0-9]+) functions fully analyzed$"
)
_AUDIT_PARTIAL_RE = re.compile(r"^(?P<count>[0-9]+) partially supported$")
_AUDIT_UNSUPPORTED_RE = re.compile(r"^(?P<count>[0-9]+) unsupported$")
_AUDIT_SKIPPED_RE = re.compile(r"^(?P<count>[0-9]+) module (?P<word>statement|statements) skipped$")
_SUGGESTION_RE = re.compile(
    r"^(?P<file>.+)\((?P<line>[1-9][0-9]*),(?P<column>[1-9][0-9]*)\): "
    r"suggestion \[(?P<rule>[a-z0-9-]+)\]: (?P<message>.+)$"
)
_CONTRACT_LOCATION_RE = re.compile(
    r"^(?P<contract>.+) \((?P<label>[a-z][a-z -]*) at (?P<file>.+):(?P<line>[1-9][0-9]*):"
    r"(?P<column>[1-9][0-9]*)\)$"
)
_ALLOWED_FINDING_RULES = {
    "console-assert",
    "declared-requirement",
    "inferred-requirement",
    "non-exiting-loop",
    "out-of-bounds-read",
}
_ALLOWED_SUGGESTION_RULES = {
    "encode-input-rule",
    "guard-array-index",
    "guard-derived-value",
    "handle-missing-element",
    "use-direct-operands",
    "use-loop-for-aggregation",
    "write-explicit-condition",
}
_NUMERIC_SCOPE_PROFILE = "viewspec_numeric_kernel_v1"


class FreerangeFailure(ValueError):
    """A stable-code Freerange integration failure."""

    def __init__(
        self,
        code: str,
        message: str,
        fix: str,
        *,
        report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix
        self.report = report

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}

    def attach_report(self, report: dict[str, Any]) -> "FreerangeFailure":
        if self.report is None:
            self.report = report
        return self


@dataclass(frozen=True)
class _CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    duration_ms: int


@dataclass(frozen=True)
class _ScopeFile:
    path: str
    absolute_path: Path
    sha256: str
    required_functions: tuple[str, ...]
    function_sha256: Mapping[str, str]
    allowed_requires: Mapping[str, tuple[str, ...]]
    required_ensures: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class _ScopeCallSite:
    path: str
    absolute_path: Path
    sha256: str
    required_functions: tuple[str, ...]
    connection: str | None


@dataclass(frozen=True)
class _ValidatedScope:
    status: str
    files: tuple[_ScopeFile, ...]
    call_sites: tuple[_ScopeCallSite, ...]


@dataclass(frozen=True)
class _Installation:
    config_paths: tuple[Path, ...]
    tool_bin: Path
    tool_tree: Mapping[str, Any]
    typescript_tree: Mapping[str, Any]
    typescript: Mapping[str, str]


def freerange_readiness(
    app_dir: str | Path | None = None,
    *,
    bun_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only Freerange readiness report.

    The check may execute ``bun --version`` but never installs packages, mutates the
    application, or invokes a network-capable package runner.
    """

    engine = _engine_identity()
    runtime: dict[str, Any] = {"name": "bun", "status": "unavailable"}
    try:
        runtime = _runtime_identity(bun_executable)
        package: dict[str, Any]
        if app_dir is None:
            package = {"status": "not_checked"}
        else:
            app_path = _resolve_app_dir(app_dir)
            installation = _validate_installation(app_path)
            package = {
                "status": "ready",
                "package_json": FREERANGE_PACKAGE,
                "tool_tree_sha256": installation.tool_tree["sha256"],
                "typescript_version": installation.typescript["version"],
                "typescript_integrity": installation.typescript["integrity"],
                "typescript_tree_sha256": installation.typescript_tree["sha256"],
            }
        return {
            "ok": True,
            "status": "ready",
            "engine": engine,
            "runtime": runtime,
            "package": package,
            "errors": [],
        }
    except FreerangeFailure as error:
        if runtime.get("status") == "unavailable" and error.code == "APP_FREERANGE_RUNTIME_UNSUPPORTED":
            runtime = {"name": "bun", "status": "unsupported"}
        return {
            "ok": False,
            "status": "unavailable",
            "engine": engine,
            "runtime": runtime,
            "package": {"status": "unavailable", "error_code": error.code},
            "errors": [error.to_json()],
        }


def analyze_freerange_numeric_scope(
    app_dir: str | Path,
    scope: Mapping[str, Any],
    *,
    bun_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze a generated numeric scope and return bounded machine evidence.

    ``scope`` has the following manifest-oriented shape::

        {
          "status": "applicable" | "not_applicable",
          "files": [{
            "path": "src/viewspec_numeric.ts",
            "sha256": "...",
            "required_functions": ["clampMoveIndex"],
            "allowed_requires": {"clampMoveIndex": ["Number.isFinite(rawIndex)"]},
            "required_ensures": {"clampMoveIndex": ["return is ..."]},
          }],
          "call_sites": [{"path": "src/state_reducer.ts", "sha256": "..."}],
        }

    Requirement locations are normalized away before allowlist comparison; the semantic
    expression is compared exactly.  Missing allowlists allow no requirements.  Every
    ``assumes`` contract is rejected.
    """

    started = time.perf_counter()
    try:
        return _analyze_freerange_numeric_scope(
            app_dir,
            scope,
            bun_executable=bun_executable,
            started=started,
        )
    except FreerangeFailure as error:
        if error.report is None:
            error.attach_report(_minimal_failure_report(error, started))
        raise


def _analyze_freerange_numeric_scope(
    app_dir: str | Path,
    scope: Mapping[str, Any],
    *,
    bun_executable: str | Path | None,
    started: float,
) -> dict[str, Any]:
    app_path = _resolve_app_dir(app_dir)
    validated_scope = _validate_scope(app_path, scope)
    engine = _engine_identity()
    if validated_scope.status == "not_applicable":
        return {
            "engine": engine,
            "runtime": {"name": "bun", "status": "not_required"},
            "status": "not_applicable",
            "required_functions": [],
            "scope": {"status": "not_applicable", "files": [], "call_sites": []},
            "coverage": {
                "required": 0,
                "observed": 0,
                "analyzed": 0,
                "fully_analyzed": 0,
                "partial": 0,
                "unsupported": 0,
            },
            "findings": [],
            "source_hashes": {"analyzed_sources": [], "call_sites": [], "configuration": [], "tools": []},
            "findings_transcript_sha256": None,
            "audit_transcript_sha256": None,
            "findings_transcript_hash": None,
            "audit_transcript_hash": None,
            "timings_ms": {"total": _elapsed_ms(started)},
            "errors": [],
        }

    runtime_started = time.perf_counter()
    try:
        runtime = _runtime_identity(bun_executable)
    except FreerangeFailure as error:
        runtime_ms = _elapsed_ms(runtime_started)
        unavailable_runtime = {
            "name": "bun",
            "status": "unsupported" if error.code == "APP_FREERANGE_RUNTIME_UNSUPPORTED" else "unavailable",
        }
        raise error.attach_report(
            _preflight_failure_report(
                engine=engine,
                runtime=unavailable_runtime,
                scope=validated_scope,
                timings={"runtime": runtime_ms, "total": _elapsed_ms(started)},
                error=error,
            )
        )
    runtime_ms = _elapsed_ms(runtime_started)
    install_started = time.perf_counter()
    try:
        installation = _validate_installation(app_path)
    except FreerangeFailure as error:
        installation_ms = _elapsed_ms(install_started)
        raise error.attach_report(
            _preflight_failure_report(
                engine=engine,
                runtime=runtime,
                scope=validated_scope,
                timings={
                    "runtime": runtime_ms,
                    "installation": installation_ms,
                    "total": _elapsed_ms(started),
                },
                error=error,
            )
        )
    installation_ms = _elapsed_ms(install_started)
    snapshots = _capture_snapshots(validated_scope, installation, Path(runtime["executable"]))

    file_evidence: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    findings_transcripts: list[tuple[str, bytes]] = []
    audit_transcripts: list[tuple[str, bytes]] = []
    findings_ms = 0
    audit_ms = 0
    deferred_error: FreerangeFailure | None = None
    try:
        for source in validated_scope.files:
            finding_result = _run_bounded_command(
                [runtime["executable"], str(installation.tool_bin), source.path],
                cwd=app_path,
                timeout_seconds=FREERANGE_COMMAND_TIMEOUT_SECONDS,
            )
            findings_ms += finding_result.duration_ms
            findings_transcripts.append((source.path, finding_result.stdout))
            findings_output = _decode_command_output(finding_result, mode="findings")
            parsed_findings = _parse_findings(findings_output, source.path)
            all_findings.extend(parsed_findings["findings"])

            audit_result = _run_bounded_command(
                [runtime["executable"], str(installation.tool_bin), "--audit", source.path],
                cwd=app_path,
                timeout_seconds=FREERANGE_COMMAND_TIMEOUT_SECONDS,
            )
            audit_ms += audit_result.duration_ms
            audit_transcripts.append((source.path, audit_result.stdout))
            audit_output = _decode_command_output(audit_result, mode="audit")
            parsed_audit = _parse_audit(audit_output, source.path)

            current_evidence = {
                "path": source.path,
                "sha256": source.sha256,
                "required_functions": [
                    {
                        "name": name,
                            "sha256": source.function_sha256.get(
                                name,
                                _function_identity_sha256(source.path, source.sha256, name),
                            ),
                            "hash_kind": (
                                "source_declaration_sha256"
                                if name in source.function_sha256
                                else "file_bound_identity_sha256"
                            ),
                    }
                    for name in source.required_functions
                ],
                "coverage": _coverage_with_alias(parsed_audit["coverage"]),
                "contracts": _bounded_contract_evidence(parsed_audit["contracts"]),
                "findings_transcript_sha256": hashlib.sha256(finding_result.stdout).hexdigest(),
                "audit_transcript_sha256": hashlib.sha256(audit_result.stdout).hexdigest(),
            }
            file_evidence.append(current_evidence)

            _validate_command_status(finding_result, parsed_findings, mode="findings")
            _validate_command_status(audit_result, parsed_audit, mode="audit")
            _validate_coverage(source, parsed_findings, parsed_audit)
            contracts = _validate_contracts(source, parsed_audit)
            current_evidence["contracts"] = contracts
            if parsed_findings["errors"] > 0 and deferred_error is None:
                deferred_error = FreerangeFailure(
                    "APP_FREERANGE_FINDINGS",
                    f"Freerange reported {parsed_findings['errors']} error finding(s) in {source.path}.",
                    "Fix the reported numeric defect, regenerate the AppBundle, and rerun the proof.",
                )
    except FreerangeFailure as caught:
        error = caught
        try:
            _assert_snapshots_unchanged(snapshots)
        except FreerangeFailure as changed:
            error = changed
        raise error.attach_report(
            _failure_report(
                engine=engine,
                runtime=runtime,
                scope=validated_scope,
                installation=installation,
                snapshots=snapshots,
                file_evidence=file_evidence,
                findings=all_findings,
                findings_transcripts=findings_transcripts,
                audit_transcripts=audit_transcripts,
                timings={
                    "runtime": runtime_ms,
                    "installation": installation_ms,
                    "findings": findings_ms,
                    "audit": audit_ms,
                    "total": _elapsed_ms(started),
                },
                error=error,
            )
        )

    try:
        _assert_snapshots_unchanged(snapshots)
    except FreerangeFailure as error:
        raise error.attach_report(
            _failure_report(
                engine=engine,
                runtime=runtime,
                scope=validated_scope,
                installation=installation,
                snapshots=snapshots,
                file_evidence=file_evidence,
                findings=all_findings,
                findings_transcripts=findings_transcripts,
                audit_transcripts=audit_transcripts,
                timings={
                    "runtime": runtime_ms,
                    "installation": installation_ms,
                    "findings": findings_ms,
                    "audit": audit_ms,
                    "total": _elapsed_ms(started),
                },
                error=error,
            )
        )
    if deferred_error is not None:
        raise deferred_error.attach_report(
            _failure_report(
                engine=engine,
                runtime=runtime,
                scope=validated_scope,
                installation=installation,
                snapshots=snapshots,
                file_evidence=file_evidence,
                findings=all_findings,
                findings_transcripts=findings_transcripts,
                audit_transcripts=audit_transcripts,
                timings={
                    "runtime": runtime_ms,
                    "installation": installation_ms,
                    "findings": findings_ms,
                    "audit": audit_ms,
                    "total": _elapsed_ms(started),
                },
                error=deferred_error,
            )
        )

    totals = {
        "required": sum(len(source.required_functions) for source in validated_scope.files),
        "observed": sum(item["coverage"]["functions"] for item in file_evidence),
        "analyzed": sum(item["coverage"]["analyzed"] for item in file_evidence),
        "partial": sum(item["coverage"]["partial"] for item in file_evidence),
        "unsupported": sum(item["coverage"]["unsupported"] for item in file_evidence),
    }
    totals["fully_analyzed"] = totals["analyzed"]
    call_site_evidence = [_call_site_evidence(item) for item in validated_scope.call_sites]
    config_evidence = [snapshots[str(path)]["evidence"] for path in installation.config_paths]
    tool_evidence = [
        snapshots[str(installation.tool_bin)]["evidence"],
        {
            "path": "node_modules/@chenglou/freerange",
            "sha256": installation.tool_tree["sha256"],
            "bytes": installation.tool_tree["bytes"],
            "files": installation.tool_tree["files"],
        },
        {
            "path": "node_modules/typescript",
            "sha256": installation.typescript_tree["sha256"],
            "bytes": installation.typescript_tree["bytes"],
            "files": installation.typescript_tree["files"],
        },
    ]
    return {
        "engine": engine,
        "runtime": runtime,
        "status": "passed",
        "required_functions": [name for source in validated_scope.files for name in source.required_functions],
        "scope": {"status": "applicable", "files": file_evidence, "call_sites": call_site_evidence},
        "coverage": totals,
        "findings": all_findings,
        "source_hashes": {
            "analyzed_sources": [{"path": item.path, "sha256": item.sha256} for item in validated_scope.files],
            "call_sites": call_site_evidence,
            "configuration": config_evidence,
            "tools": tool_evidence,
        },
        "findings_transcript_sha256": _transcript_set_sha256(findings_transcripts),
        "audit_transcript_sha256": _transcript_set_sha256(audit_transcripts),
        "findings_transcript_hash": _transcript_set_sha256(findings_transcripts),
        "audit_transcript_hash": _transcript_set_sha256(audit_transcripts),
        "timings_ms": {
            "runtime": runtime_ms,
            "installation": installation_ms,
            "findings": findings_ms,
            "audit": audit_ms,
            "total": _elapsed_ms(started),
        },
        "errors": [],
    }


def _preflight_failure_report(
    *,
    engine: Mapping[str, Any],
    runtime: Mapping[str, Any],
    scope: _ValidatedScope,
    timings: Mapping[str, int],
    error: FreerangeFailure,
) -> dict[str, Any]:
    required = [name for source in scope.files for name in source.required_functions]
    files = [
        {
            "path": source.path,
            "sha256": source.sha256,
            "required_functions": [
                {
                    "name": name,
                    "sha256": source.function_sha256.get(
                        name,
                        _function_identity_sha256(source.path, source.sha256, name),
                    ),
                    "hash_kind": (
                        "source_declaration_sha256"
                        if name in source.function_sha256
                        else "file_bound_identity_sha256"
                    ),
                }
                for name in source.required_functions
            ],
        }
        for source in scope.files
    ]
    call_sites = [_call_site_evidence(item) for item in scope.call_sites]
    return {
        "engine": dict(engine),
        "runtime": dict(runtime),
        "status": "failed",
        "required_functions": required,
        "scope": {"status": scope.status, "files": files, "call_sites": call_sites},
        "coverage": {
            "required": len(required),
            "observed": 0,
            "analyzed": 0,
            "fully_analyzed": 0,
            "partial": 0,
            "unsupported": 0,
        },
        "findings": [],
        "source_hashes": {
            "analyzed_sources": [
                {"path": source.path, "sha256": source.sha256} for source in scope.files
            ],
            "call_sites": call_sites,
            "configuration": [],
            "tools": [],
        },
        "findings_transcript_sha256": None,
        "audit_transcript_sha256": None,
        "findings_transcript_hash": None,
        "audit_transcript_hash": None,
        "timings_ms": dict(timings),
        "errors": [error.to_json()],
    }


def _minimal_failure_report(error: FreerangeFailure, started: float) -> dict[str, Any]:
    return {
        "engine": _engine_identity(),
        "runtime": {"name": "bun", "status": "unavailable"},
        "status": "failed",
        "required_functions": [],
        "scope": {"status": "unknown", "files": [], "call_sites": []},
        "coverage": {
            "required": 0,
            "observed": 0,
            "analyzed": 0,
            "fully_analyzed": 0,
            "partial": 0,
            "unsupported": 0,
        },
        "findings": [],
        "source_hashes": {"analyzed_sources": [], "call_sites": [], "configuration": [], "tools": []},
        "findings_transcript_sha256": None,
        "audit_transcript_sha256": None,
        "findings_transcript_hash": None,
        "audit_transcript_hash": None,
        "timings_ms": {"total": _elapsed_ms(started)},
        "errors": [error.to_json()],
    }


def _engine_identity() -> dict[str, str]:
    return {
        "name": "freerange",
        "package": FREERANGE_PACKAGE,
        "version": FREERANGE_VERSION,
        "bin": FREERANGE_BIN,
        "integrity": FREERANGE_NPM_INTEGRITY,
        "package_tree_sha256": FREERANGE_PACKAGE_TREE["sha256"],
        "protocol": FREERANGE_PROTOCOL,
    }


def _resolve_app_dir(app_dir: str | Path) -> Path:
    path = Path(app_dir).expanduser().resolve()
    if not path.is_dir():
        raise FreerangeFailure(
            "APP_FREERANGE_SCOPE_INVALID",
            f"AppBundle directory does not exist: {path}",
            "Pass the generated react-tailwind-app directory.",
        )
    return path


def _validate_scope(app_dir: Path, scope: Mapping[str, Any]) -> _ValidatedScope:
    base_keys = {"status", "files", "call_sites"}
    metadata_keys = {
        "schema_version",
        "profile",
        "kernel_path",
        "required_functions",
        "allowed_requires",
        "required_ensures",
    }
    if (
        not isinstance(scope, Mapping)
        or not base_keys.issubset(scope)
        or not set(scope).issubset(base_keys | metadata_keys)
    ):
        raise _scope_failure("Scope contains missing or unsupported top-level fields.")
    status_value = scope.get("status")
    if status_value not in {"applicable", "not_applicable"}:
        raise _scope_failure("Scope status must be applicable or not_applicable.")
    files_value = scope.get("files")
    call_sites_value = scope.get("call_sites")
    if not isinstance(files_value, list) or not isinstance(call_sites_value, list):
        raise _scope_failure("Scope files and call_sites must be arrays.")
    if status_value == "not_applicable":
        if files_value or call_sites_value:
            raise _scope_failure("A not_applicable scope must not name files or call sites.")
        metadata_present = set(scope) - base_keys
        expected_metadata = {"schema_version", "profile", "required_functions", "allowed_requires", "required_ensures"}
        if metadata_present and metadata_present != expected_metadata:
            raise _scope_failure("A generated not_applicable scope has incomplete metadata.")
        if metadata_present and (
            scope.get("schema_version") != 1
            or scope.get("profile") != _NUMERIC_SCOPE_PROFILE
            or scope.get("required_functions") != []
            or scope.get("allowed_requires") != {}
            or scope.get("required_ensures") != {}
        ):
            raise _scope_failure("A generated not_applicable scope has invalid metadata.")
        return _ValidatedScope("not_applicable", (), ())
    if not files_value or len(files_value) > FREERANGE_MAX_FILES:
        raise _scope_failure(f"An applicable scope requires 1-{FREERANGE_MAX_FILES} kernel files.")
    if not call_sites_value or len(call_sites_value) > FREERANGE_MAX_CALL_SITES:
        raise _scope_failure(f"An applicable scope requires 1-{FREERANGE_MAX_CALL_SITES} runtime call sites.")

    files: list[_ScopeFile] = []
    call_sites: list[_ScopeCallSite] = []
    seen_paths: set[str] = set()
    total_functions = 0
    for item in files_value:
        allowed_keys = {
            "path",
            "sha256",
            "required_functions",
            "function_sha256",
            "allowed_requires",
            "required_ensures",
        }
        if not isinstance(item, Mapping) or not {"path", "sha256", "required_functions"}.issubset(item):
            raise _scope_failure("Every scope file needs path, sha256, and required_functions.")
        if not set(item).issubset(allowed_keys):
            raise _scope_failure("Scope file contains an unsupported field.")
        path, absolute, digest = _validate_hashed_scope_path(app_dir, item, seen_paths)
        if not path.endswith((".ts", ".tsx")):
            raise _scope_failure(f"Freerange kernel must be TypeScript: {path}")
        names = item.get("required_functions")
        if not isinstance(names, list) or not names:
            raise _scope_failure(f"Kernel {path} must require at least one function.")
        if any(not isinstance(name, str) or _FUNCTION_RE.fullmatch(name) is None for name in names):
            raise _scope_failure(f"Kernel {path} has an invalid required function name.")
        if len(set(names)) != len(names):
            raise _scope_failure(f"Kernel {path} repeats a required function name.")
        total_functions += len(names)
        if total_functions > FREERANGE_MAX_FUNCTIONS:
            raise _scope_failure(f"Scope exceeds {FREERANGE_MAX_FUNCTIONS} required functions.")
        allowed_requires = _validate_contract_map(item.get("allowed_requires", {}), names, "allowed_requires")
        required_ensures = _validate_contract_map(item.get("required_ensures", {}), names, "required_ensures")
        function_sha256 = _validate_function_hashes(item.get("function_sha256", {}), names)
        files.append(
            _ScopeFile(
                path=path,
                absolute_path=absolute,
                sha256=digest,
                required_functions=tuple(names),
                function_sha256=function_sha256,
                allowed_requires=allowed_requires,
                required_ensures=required_ensures,
            )
        )
    for item in call_sites_value:
        if not isinstance(item, Mapping) or not {"path", "sha256"}.issubset(item):
            raise _scope_failure("Every call site must contain path and sha256.")
        enriched = {"required_functions", "connection"}.intersection(item)
        if set(item) not in ({"path", "sha256"}, {"path", "sha256", "required_functions", "connection"}):
            raise _scope_failure("Call site contains incomplete or unsupported connection metadata.")
        path, absolute, digest = _validate_hashed_scope_path(app_dir, item, seen_paths)
        if enriched:
            required = item.get("required_functions")
            if (
                not isinstance(required, list)
                or any(not isinstance(name, str) for name in required)
                or item.get("connection") != "generated_import_and_call_v1"
            ):
                raise _scope_failure("Call site connection metadata is invalid.")
            required_functions = tuple(required)
        else:
            required_functions = ()
        call_sites.append(
            _ScopeCallSite(
                path=path,
                absolute_path=absolute,
                sha256=digest,
                required_functions=required_functions,
                connection=item.get("connection") if enriched else None,
            )
        )
    flattened_functions = tuple(name for source in files for name in source.required_functions)
    for call_site in call_sites:
        if call_site.required_functions and call_site.required_functions != flattened_functions:
            raise _scope_failure("Call site required_functions do not match the analyzed kernel inventory.")
        if call_site.connection == "generated_import_and_call_v1":
            _validate_generated_call_site_connection(call_site)
    metadata_present = set(scope) - base_keys
    if metadata_present:
        if metadata_present != metadata_keys:
            raise _scope_failure("Generated applicable scope metadata is incomplete.")
        if (
            scope.get("schema_version") != 1
            or scope.get("profile") != _NUMERIC_SCOPE_PROFILE
            or scope.get("required_functions") != list(flattened_functions)
            or len(files) != 1
            or scope.get("kernel_path") != files[0].path
        ):
            raise _scope_failure("Generated applicable scope metadata disagrees with its kernel file.")
        top_allowed = _validate_contract_map(scope.get("allowed_requires"), flattened_functions, "allowed_requires")
        top_ensures = _validate_contract_map(scope.get("required_ensures"), flattened_functions, "required_ensures")
        if top_allowed != files[0].allowed_requires or top_ensures != files[0].required_ensures:
            raise _scope_failure("Top-level numeric contracts disagree with the kernel file contracts.")
        from viewspec.app_numeric import generate_numeric_typescript

        try:
            actual_source = files[0].absolute_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as error:
            raise _scope_failure("Generated numeric kernel is not readable UTF-8.") from error
        if actual_source != generate_numeric_typescript(scope):
            raise _scope_failure("Generated numeric kernel source differs from its declared function contract.")
    return _ValidatedScope("applicable", tuple(files), tuple(call_sites))


def _validate_generated_call_site_connection(call_site: _ScopeCallSite) -> None:
    try:
        source = call_site.absolute_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        raise _scope_failure(f"Generated numeric call site is not readable UTF-8: {call_site.path}") from error
    import_line = f'import {{ {", ".join(call_site.required_functions)} }} from "./viewspec_numeric";'
    if source.count(import_line) != 1:
        raise _scope_failure(f"Generated numeric call site has no exact kernel import: {call_site.path}")
    source_without_import = source.replace(import_line, "", 1)
    code = _typescript_code_tokens(source_without_import)
    disconnected = [
        name
        for name in call_site.required_functions
        if re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(name)}\(", code) is None
    ]
    if disconnected:
        raise _scope_failure(
            f"Generated numeric call site does not call required function(s): {', '.join(disconnected)}"
        )


def _typescript_code_tokens(source: str) -> str:
    """Blank comments and string literals for the bounded generated-call token check."""

    output: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if current == "/" and following == "/":
                output.extend("  ")
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                output.extend("  ")
                index += 2
                state = "block_comment"
                continue
            if current in {"'", '"', "`"}:
                quote = current
                output.append(" ")
                index += 1
                state = "string"
                continue
            output.append(current)
            index += 1
            continue
        if state == "line_comment":
            if current == "\n":
                output.append("\n")
                state = "code"
            else:
                output.append(" ")
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                output.extend("  ")
                index += 2
                state = "code"
            else:
                output.append("\n" if current == "\n" else " ")
                index += 1
            continue
        if current == "\\":
            output.append(" ")
            if following:
                output.append("\n" if following == "\n" else " ")
                index += 2
            else:
                index += 1
            continue
        output.append("\n" if current == "\n" else " ")
        index += 1
        if current == quote:
            state = "code"
    return "".join(output)


def _validate_function_hashes(value: Any, names: Sequence[str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or any(key not in names for key in value):
        raise _scope_failure("function_sha256 must be an object keyed only by required function name.")
    if value and set(value) != set(names):
        raise _scope_failure("function_sha256 must identify every required function.")
    result: dict[str, str] = {}
    for name, digest in value.items():
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise _scope_failure(f"function_sha256.{name} is not a lowercase SHA-256 value.")
        result[name] = digest
    if result:
        from viewspec.app_numeric import numeric_function_hashes

        expected = numeric_function_hashes({"status": "applicable", "required_functions": list(names)})
        if result != expected:
            raise _scope_failure("function_sha256 does not match the generated numeric function contract.")
    return result


def _validate_contract_map(value: Any, names: Sequence[str], label: str) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise _scope_failure(f"{label} must be an object keyed by required function name.")
    if any(key not in names for key in value):
        raise _scope_failure(f"{label} names a function that is not required.")
    result: dict[str, tuple[str, ...]] = {}
    for name in names:
        entries = value.get(name, [])
        if not isinstance(entries, list) or len(entries) > FREERANGE_MAX_CONTRACTS_PER_FUNCTION:
            raise _scope_failure(f"{label}.{name} must be a bounded array.")
        if any(
            not isinstance(entry, str)
            or not entry
            or len(entry) > 1000
            or entry != entry.strip()
            or "\n" in entry
            or "\r" in entry
            for entry in entries
        ):
            raise _scope_failure(f"{label}.{name} contains an invalid contract expression.")
        if len(set(entries)) != len(entries):
            raise _scope_failure(f"{label}.{name} repeats a contract expression.")
        result[name] = tuple(entries)
    return result


def _validate_hashed_scope_path(
    app_dir: Path,
    item: Mapping[str, Any],
    seen_paths: set[str],
) -> tuple[str, Path, str]:
    raw_path = item.get("path")
    digest = item.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise _scope_failure("Every scope path needs a canonical relative path and lowercase SHA-256.")
    pure = PurePosixPath(raw_path)
    if (
        not raw_path
        or pure.is_absolute()
        or raw_path != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in raw_path
    ):
        raise _scope_failure(f"Scope path is not canonical and relative: {raw_path!r}")
    if raw_path in seen_paths:
        raise _scope_failure(f"Scope path is repeated: {raw_path}")
    seen_paths.add(raw_path)
    absolute = app_dir.joinpath(*pure.parts)
    _require_regular_file(absolute, "APP_FREERANGE_SCOPE_INVALID", f"Scope file is missing: {raw_path}")
    current = app_dir
    for part in pure.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise _scope_failure(f"Scope path traverses a symlink: {raw_path}")
    if not absolute.resolve().is_relative_to(app_dir):
        raise _scope_failure(f"Scope path escapes the AppBundle: {raw_path}")
    actual = _hash_file(absolute, FREERANGE_SOURCE_MAX_BYTES)
    if actual["sha256"] != digest:
        raise FreerangeFailure(
            "APP_FREERANGE_SOURCE_CHANGED",
            f"Scope hash does not match {raw_path}.",
            "Regenerate the AppBundle and use its current numeric-scope manifest.",
        )
    return raw_path, absolute, digest


def _scope_failure(message: str) -> FreerangeFailure:
    return FreerangeFailure(
        "APP_FREERANGE_SCOPE_INVALID",
        message,
        "Regenerate the AppBundle; do not hand-edit its numeric-scope manifest.",
    )


def _runtime_identity(bun_executable: str | Path | None) -> dict[str, Any]:
    candidate: str | None
    if bun_executable is None:
        candidate = shutil.which("bun")
    else:
        raw = os.fspath(bun_executable)
        candidate = shutil.which(raw) if os.path.dirname(raw) == "" else raw
    if candidate is None:
        raise FreerangeFailure(
            "APP_FREERANGE_RUNTIME_MISSING",
            "Bun is required for the pinned Freerange analyzer but was not found.",
            "Install Bun explicitly, then rerun viewspec doctor --freerange.",
        )
    executable = Path(candidate).expanduser().resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FreerangeFailure(
            "APP_FREERANGE_RUNTIME_MISSING",
            f"Bun executable is unavailable or not executable: {executable}",
            "Pass an executable Bun path or install Bun explicitly.",
        )
    before = _hash_file(executable, FREERANGE_BUN_MAX_BYTES)
    result = _run_bounded_command(
        [str(executable), "--version"],
        cwd=executable.parent,
        timeout_seconds=FREERANGE_RUNTIME_TIMEOUT_SECONDS,
    )
    after = _hash_file(executable, FREERANGE_BUN_MAX_BYTES)
    if before != after:
        raise FreerangeFailure(
            "APP_FREERANGE_SOURCE_CHANGED",
            "The Bun executable changed during the readiness check.",
            "Stabilize the runtime installation and rerun the proof.",
        )
    stdout = _decode_utf8(result.stdout, "Bun version output")
    stderr = _decode_utf8(result.stderr, "Bun version error output")
    version = stdout.removesuffix("\n")
    match = _SEMVER_RE.fullmatch(version)
    stable_version = ".".join(match.groups()[:3]) if match is not None else None
    if (
        result.returncode != 0
        or stderr
        or match is None
        or "\n" in version
        or version != stable_version
    ):
        raise FreerangeFailure(
            "APP_FREERANGE_RUNTIME_UNSUPPORTED",
            "Bun did not return one supported semantic version line.",
            "Use a stable Bun 1.x or newer executable and retry.",
        )
    if int(match.group(1)) < 1:
        raise FreerangeFailure(
            "APP_FREERANGE_RUNTIME_UNSUPPORTED",
            f"Bun {version} is unsupported; Freerange requires Bun 1.x or newer.",
            "Upgrade Bun explicitly and rerun viewspec doctor --freerange.",
        )
    return {
        "name": "bun",
        "status": "ready",
        "executable": str(executable),
        "version": version,
        "sha256": before["sha256"],
        "bytes": before["bytes"],
    }


def _validate_installation(app_dir: Path) -> _Installation:
    package_path = app_dir / "package.json"
    lock_path = app_dir / "package-lock.json"
    tsconfig_path = app_dir / "tsconfig.json"
    for path in (package_path, lock_path, tsconfig_path):
        _require_regular_file(
            path,
            "APP_FREERANGE_PACKAGE_MISSING",
            f"Required Freerange proof input is missing: {path.name}",
        )
    package = _read_json(package_path)
    lock = _read_json(lock_path)
    tsconfig = _read_json(tsconfig_path)
    if "extends" in tsconfig or tsconfig.get("references") not in (None, []):
        raise FreerangeFailure(
            "APP_FREERANGE_DEPENDENCY_DRIFT",
            "The Freerange proof tsconfig must be self-contained.",
            "Regenerate the AppBundle with the supported self-contained tsconfig.",
        )
    package_dependencies = package.get("devDependencies")
    if not isinstance(package_dependencies, Mapping) or package_dependencies.get(FREERANGE_PACKAGE) != FREERANGE_VERSION:
        raise FreerangeFailure(
            "APP_FREERANGE_VERSION_MISMATCH",
            f"package.json must pin {FREERANGE_PACKAGE} exactly to {FREERANGE_VERSION}.",
            "Regenerate with the supported ViewSpec Freerange dependency set.",
        )
    if package_dependencies.get("typescript") != FREERANGE_TYPESCRIPT_VERSION:
        raise FreerangeFailure(
            "APP_FREERANGE_DEPENDENCY_DRIFT",
            f"package.json must pin TypeScript exactly to {FREERANGE_TYPESCRIPT_VERSION}.",
            "Regenerate with the supported ViewSpec TypeScript dependency set.",
        )
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), Mapping):
        raise FreerangeFailure(
            "APP_FREERANGE_PACKAGE_INTEGRITY",
            "package-lock.json is not the supported npm lockfile v3 shape.",
            "Regenerate the AppBundle lockfile; do not update it independently.",
        )
    packages = lock["packages"]
    root = packages.get("")
    freerange_lock = packages.get("node_modules/@chenglou/freerange")
    typescript_lock = packages.get("node_modules/typescript")
    if not isinstance(root, Mapping) or not isinstance(root.get("devDependencies"), Mapping):
        raise _package_integrity_failure("package-lock.json has no root devDependency map.")
    root_deps = root["devDependencies"]
    if root_deps.get(FREERANGE_PACKAGE) != FREERANGE_VERSION:
        raise FreerangeFailure(
            "APP_FREERANGE_VERSION_MISMATCH",
            f"package-lock.json does not pin {FREERANGE_PACKAGE} {FREERANGE_VERSION}.",
            "Regenerate the AppBundle lockfile from the supported dependency set.",
        )
    expected_lock = {
        "version": FREERANGE_VERSION,
        "resolved": FREERANGE_NPM_RESOLVED,
        "integrity": FREERANGE_NPM_INTEGRITY,
    }
    if not isinstance(freerange_lock, Mapping) or any(freerange_lock.get(key) != value for key, value in expected_lock.items()):
        raise _package_integrity_failure("The Freerange lock entry version, URL, or npm integrity is not pinned.")
    if freerange_lock.get("bin") != {"fr": FREERANGE_BIN} or freerange_lock.get("dependencies") != {
        "typescript": "^6.0.2"
    }:
        raise _package_integrity_failure("The Freerange lock entry dependency or binary contract drifted.")
    if root_deps.get("typescript") != FREERANGE_TYPESCRIPT_VERSION:
        raise _dependency_drift_failure("package-lock.json does not pin the supported TypeScript version.")
    expected_typescript_lock = {
        "version": FREERANGE_TYPESCRIPT_VERSION,
        "resolved": FREERANGE_TYPESCRIPT_RESOLVED,
        "integrity": FREERANGE_TYPESCRIPT_INTEGRITY,
    }
    if not isinstance(typescript_lock, Mapping) or any(
        typescript_lock.get(key) != value for key, value in expected_typescript_lock.items()
    ):
        raise _dependency_drift_failure("The resolved TypeScript dependency differs from the supported lock entry.")

    tool_root = app_dir / "node_modules" / "@chenglou" / "freerange"
    typescript_root = app_dir / "node_modules" / "typescript"
    tool_package_path = tool_root / "package.json"
    tool_bin = tool_root / FREERANGE_BIN
    typescript_package_path = typescript_root / "package.json"
    for path in (tool_package_path, tool_bin, typescript_package_path):
        _require_regular_file(
            path,
            "APP_FREERANGE_PACKAGE_MISSING",
            f"Installed Freerange proof dependency is missing: {path.relative_to(app_dir)}",
        )
    tool_package = _read_json(tool_package_path)
    if (
        tool_package.get("name") != FREERANGE_PACKAGE
        or tool_package.get("version") != FREERANGE_VERSION
        or tool_package.get("bin") != {"fr": FREERANGE_BIN}
        or tool_package.get("dependencies") != {"typescript": "^6.0.2"}
    ):
        raise FreerangeFailure(
            "APP_FREERANGE_VERSION_MISMATCH",
            "The installed Freerange package metadata does not match 0.0.1.",
            "Run the opt-in install step from the unchanged generated lockfile.",
        )
    typescript_package = _read_json(typescript_package_path)
    if typescript_package.get("name") != "typescript" or typescript_package.get("version") != FREERANGE_TYPESCRIPT_VERSION:
        raise _dependency_drift_failure("The installed TypeScript package differs from the pinned version.")
    tool_tree = _tree_hash(tool_root, FREERANGE_TOOL_TREE_MAX_BYTES)
    typescript_tree = _tree_hash(typescript_root, FREERANGE_TOOL_TREE_MAX_BYTES)
    if tool_tree != FREERANGE_PACKAGE_TREE:
        raise _package_integrity_failure(
            "The installed Freerange package tree does not match the exact 0.0.1 npm artifact."
        )
    if typescript_tree != FREERANGE_TYPESCRIPT_TREE:
        raise _dependency_drift_failure(
            "The installed TypeScript package tree does not match the exact 6.0.3 npm artifact."
        )
    return _Installation(
        config_paths=(package_path, lock_path, tsconfig_path, tool_package_path, typescript_package_path),
        tool_bin=tool_bin,
        tool_tree=tool_tree,
        typescript_tree=typescript_tree,
        typescript={
            "version": FREERANGE_TYPESCRIPT_VERSION,
            "integrity": FREERANGE_TYPESCRIPT_INTEGRITY,
        },
    )


def _package_integrity_failure(message: str) -> FreerangeFailure:
    return FreerangeFailure(
        "APP_FREERANGE_PACKAGE_INTEGRITY",
        message,
        "Regenerate the AppBundle and install only from its unchanged package-lock.json.",
    )


def _dependency_drift_failure(message: str) -> FreerangeFailure:
    return FreerangeFailure(
        "APP_FREERANGE_DEPENDENCY_DRIFT",
        message,
        "Regenerate and reinstall the exact ViewSpec-pinned proof dependencies.",
    )


def _parse_findings(output: str, expected_path: str) -> dict[str, Any]:
    lines = _protocol_lines(output, "findings")
    if len(lines) < 5 or lines[-1] != "Run `fr --audit [file]` for every function's contracts and refactoring suggestions.":
        raise _protocol_failure("Freerange findings footer is missing or changed.")
    coverage_match = _FINDINGS_COVERAGE_RE.fullmatch(lines[-2])
    summary_match = _FINDING_SUMMARY_RE.fullmatch(lines[-3])
    if coverage_match is None or summary_match is None or lines[-4] != "":
        raise _protocol_failure("Freerange findings summary or coverage line is malformed.")
    finding_lines = lines[:-4]
    findings: list[dict[str, Any]] = []
    if finding_lines == ["No lint findings."]:
        pass
    else:
        if not finding_lines or len(finding_lines) > FREERANGE_MAX_FINDINGS:
            raise _protocol_failure("Freerange findings list is empty or exceeds its bound.")
        for line in finding_lines:
            match = _FINDING_RE.fullmatch(line)
            if match is None or match.group("file") != expected_path:
                raise _protocol_failure("Freerange emitted an unrecognized or out-of-scope finding.")
            rule = match.group("rule")
            level = match.group("level")
            if rule not in _ALLOWED_FINDING_RULES:
                raise _protocol_failure(f"Freerange emitted an unknown finding rule: {rule}")
            if (rule == "non-exiting-loop") != (level == "warning"):
                raise _protocol_failure("Freerange finding severity does not match the pinned protocol.")
            findings.append(
                {
                    "path": expected_path,
                    "line": int(match.group("line")),
                    "column": int(match.group("column")),
                    "level": level,
                    "rule": rule,
                    "message": match.group("message"),
                }
            )
    count = int(summary_match.group("count"))
    errors = int(summary_match.group("errors"))
    warnings = int(summary_match.group("warnings"))
    if (
        summary_match.group("finding_word") != ("finding" if count == 1 else "findings")
        or summary_match.group("error_word") != ("error" if errors == 1 else "errors")
        or summary_match.group("warning_word") != ("warning" if warnings == 1 else "warnings")
    ):
        raise _protocol_failure("Freerange findings count grammar differs from the pinned protocol.")
    actual_errors = sum(item["level"] == "error" for item in findings)
    actual_warnings = sum(item["level"] == "warning" for item in findings)
    if count != len(findings) or errors != actual_errors or warnings != actual_warnings or count != errors + warnings:
        raise _protocol_failure("Freerange findings counts disagree with the parsed findings.")
    coverage = {key: int(coverage_match.group(key)) for key in ("analyzed", "functions", "partial", "unsupported")}
    if coverage["functions"] != coverage["analyzed"] + coverage["partial"] + coverage["unsupported"]:
        raise _protocol_failure("Freerange findings coverage is arithmetically inconsistent.")
    return {"findings": findings, "errors": errors, "warnings": warnings, "coverage": coverage}


def _parse_audit(output: str, expected_path: str) -> dict[str, Any]:
    lines = _protocol_lines(output, "audit")
    header = _AUDIT_HEADER_RE.fullmatch(lines[0]) if lines else None
    if header is None or header.group("file") != expected_path:
        raise _protocol_failure("Freerange audit header is missing or names another file.")
    coverage = _parse_audit_coverage(header.group("coverage"))
    has_initializer_entry = coverage["initializer"] != "analyzed" or coverage["initializer_skips"] != 0
    if coverage["functions"] == 0 and not has_initializer_entry:
        if len(lines) != 1:
            raise _protocol_failure("A zero-function audit contains unexpected sections.")
        return {"coverage": coverage, "contracts": [], "suggestions": []}
    if len(lines) < 5 or lines[1:4] != ["", "## Contracts", ""]:
        raise _protocol_failure("Freerange audit Contracts section is missing or malformed.")

    suggestion_index = next((index for index, line in enumerate(lines[4:], 4) if line == "## Refactoring suggestions"), None)
    if suggestion_index is None:
        contract_lines = lines[4:]
        suggestion_lines: list[str] = []
    else:
        if suggestion_index == 4 or lines[suggestion_index - 1] != "" or suggestion_index + 1 >= len(lines):
            raise _protocol_failure("Freerange audit suggestions section has invalid boundaries.")
        contract_lines = lines[4 : suggestion_index - 1]
        if lines[suggestion_index + 1] != "":
            raise _protocol_failure("Freerange audit suggestions section is malformed.")
        suggestion_lines = lines[suggestion_index + 2 :]
    contracts = _parse_contract_paragraphs(contract_lines, expected_path)
    suggestions = _parse_suggestions(suggestion_lines, expected_path)
    expected_entries = coverage["functions"] + int(has_initializer_entry)
    if len(contracts) != expected_entries:
        raise _protocol_failure("Freerange audit function entries disagree with its coverage header.")
    return {"coverage": coverage, "contracts": contracts, "suggestions": suggestions}


def _parse_audit_coverage(value: str) -> dict[str, Any]:
    parts = value.split("; ")
    first = parts.pop(0)
    if first == "no named function declarations":
        analyzed = functions = 0
    else:
        match = _AUDIT_FUNCTION_COVERAGE_RE.fullmatch(first)
        if match is None:
            raise _protocol_failure("Freerange audit coverage header is malformed.")
        analyzed = int(match.group("analyzed"))
        functions = int(match.group("functions"))
        if functions == 0:
            raise _protocol_failure("Freerange audit used the wrong zero-function coverage form.")
    partial = 0
    unsupported = 0
    initializer = "analyzed"
    skipped = 0
    stage = 0
    for part in parts:
        partial_match = _AUDIT_PARTIAL_RE.fullmatch(part)
        unsupported_match = _AUDIT_UNSUPPORTED_RE.fullmatch(part)
        skipped_match = _AUDIT_SKIPPED_RE.fullmatch(part)
        if partial_match is not None and stage < 1:
            partial = int(partial_match.group("count"))
            if partial == 0:
                raise _protocol_failure("Freerange audit printed an empty partial-coverage clause.")
            stage = 1
        elif unsupported_match is not None and stage < 2:
            unsupported = int(unsupported_match.group("count"))
            if unsupported == 0:
                raise _protocol_failure("Freerange audit printed an empty unsupported-coverage clause.")
            stage = 2
        elif part == "module setup partially supported" and stage < 3:
            initializer = "partial"
            stage = 3
        elif skipped_match is not None and stage < 4:
            skipped = int(skipped_match.group("count"))
            if skipped == 0 or skipped_match.group("word") != ("statement" if skipped == 1 else "statements"):
                raise _protocol_failure("Freerange audit module-skip count grammar is invalid.")
            stage = 4
        else:
            raise _protocol_failure("Freerange audit coverage contains an unknown or out-of-order clause.")
    if functions != analyzed + partial + unsupported:
        raise _protocol_failure("Freerange audit coverage is arithmetically inconsistent.")
    return {
        "functions": functions,
        "analyzed": analyzed,
        "partial": partial,
        "unsupported": unsupported,
        "initializer": initializer,
        "initializer_skips": skipped,
    }


def _parse_contract_paragraphs(lines: Sequence[str], expected_path: str) -> list[dict[str, Any]]:
    if not lines or lines[0] == "" or lines[-1] == "":
        raise _protocol_failure("Freerange audit contract entries are empty or malformed.")
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line == "":
            if not current:
                raise _protocol_failure("Freerange audit contains repeated blank contract separators.")
            paragraphs.append(current)
            current = []
        else:
            current.append(line)
    if not current:
        raise _protocol_failure("Freerange audit contract section ends unexpectedly.")
    paragraphs.append(current)
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        name = paragraph[0]
        if (name != "module initialization" and _FUNCTION_RE.fullmatch(name) is None) or name in seen:
            raise _protocol_failure("Freerange audit contains an invalid or duplicate function entry.")
        seen.add(name)
        entry: dict[str, Any] = {
            "name": name,
            "requires": [],
            "ensures": [],
            "proves": [],
            "assumes": [],
            "unsafe_assertions": [],
            "unsupported": [],
            "partially_supported": [],
            "skipped": [],
            "observed": [],
        }
        phase = 0
        for line in paragraph[1:]:
            if not line.startswith("  ") or len(line) > 4000:
                raise _protocol_failure("Freerange audit contains a malformed contract line.")
            body = line[2:]
            matched = False
            for prefix, key, order in (
                ("requires: ", "requires", 1),
                ("proves: ", "proves", 2),
                ("ensures: ", "ensures", 3),
                ("assumes: ", "assumes", 4),
            ):
                if body.startswith(prefix):
                    if order < phase:
                        raise _protocol_failure("Freerange audit contract lines are out of protocol order.")
                    phase = order
                    raw = body[len(prefix) :]
                    entry[key].append({"raw": raw, "normalized": _normalize_contract(raw, expected_path)})
                    matched = True
                    break
            if matched:
                continue
            unsafe_prefixes = (
                "assertion can fail: ",
                "assertion unproven: ",
                "unreachable assertion: ",
                "assertion blocked: ",
            )
            if body.startswith(unsafe_prefixes):
                entry["unsafe_assertions"].append(body)
                continue
            for prefix, key in (
                ("unsupported: ", "unsupported"),
                ("partially supported: ", "partially_supported"),
                ("skipped: ", "skipped"),
                ("on analyzed paths: ", "observed"),
            ):
                if body.startswith(prefix):
                    entry[key].append(body[len(prefix) :])
                    matched = True
                    break
            if matched:
                continue
            raise _protocol_failure("Freerange audit contains an unknown contract line.")
        contracts.append(entry)
    return contracts


def _parse_suggestions(lines: Sequence[str], expected_path: str) -> list[dict[str, Any]]:
    if not lines:
        return []
    if lines[0] == "" or lines[-1] == "":
        raise _protocol_failure("Freerange suggestions contain an empty boundary.")
    suggestions: list[dict[str, Any]] = []
    previous_blank = False
    for line in lines:
        if line == "":
            if previous_blank:
                raise _protocol_failure("Freerange suggestions contain repeated blank lines.")
            previous_blank = True
            continue
        previous_blank = False
        match = _SUGGESTION_RE.fullmatch(line)
        if (
            match is None
            or match.group("file") != expected_path
            or match.group("rule") not in _ALLOWED_SUGGESTION_RULES
        ):
            raise _protocol_failure("Freerange emitted an unrecognized or out-of-scope suggestion.")
        suggestions.append(
            {
                "path": expected_path,
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "rule": match.group("rule"),
                "message": match.group("message"),
            }
        )
    return suggestions


def _normalize_contract(value: str, expected_path: str) -> str:
    match = _CONTRACT_LOCATION_RE.fullmatch(value)
    if match is None:
        return value
    if match.group("file") != expected_path:
        raise _protocol_failure("Freerange contract references a source outside the analyzed kernel.")
    return match.group("contract")


def _validate_command_status(result: _CommandResult, parsed: Mapping[str, Any], *, mode: str) -> None:
    if mode == "findings":
        expected = 1 if parsed["errors"] else 0
        if result.returncode != expected:
            raise FreerangeFailure(
                "APP_FREERANGE_EXECUTION_FAILED",
                "Freerange findings exit status contradicts its parsed findings.",
                "Use the exact pinned analyzer and retry from an unchanged AppBundle.",
            )
    elif result.returncode != 0:
        raise FreerangeFailure(
            "APP_FREERANGE_EXECUTION_FAILED",
            f"Freerange audit exited with status {result.returncode}.",
            "Resolve TypeScript or analyzer execution errors and rerun the proof.",
        )


def _validate_coverage(source: _ScopeFile, findings: Mapping[str, Any], audit: Mapping[str, Any]) -> None:
    findings_coverage = findings["coverage"]
    audit_coverage = audit["coverage"]
    for key in ("functions", "analyzed", "partial", "unsupported"):
        if findings_coverage[key] != audit_coverage[key]:
            raise FreerangeFailure(
                "APP_FREERANGE_COVERAGE_MISMATCH",
                f"Freerange findings and audit coverage disagree for {source.path}.",
                "Use the exact pinned analyzer and rerun from unchanged sources.",
            )
    if audit_coverage["initializer"] != "analyzed" or audit_coverage["initializer_skips"] != 0:
        raise FreerangeFailure(
            "APP_FREERANGE_INITIALIZER_INCOMPLETE",
            f"Freerange did not completely analyze module setup for {source.path}.",
            "Keep the generated numeric kernel module setup inside Freerange's supported subset.",
        )
    observed_names = [item["name"] for item in audit["contracts"]]
    required_names = list(source.required_functions)
    missing = [name for name in required_names if name not in observed_names]
    if not observed_names:
        raise FreerangeFailure(
            "APP_FREERANGE_REQUIRED_FUNCTION_MISSING",
            f"Freerange analyzed zero functions in required kernel {source.path}.",
            "Regenerate the numeric kernel with named top-level required functions.",
        )
    if missing:
        raise FreerangeFailure(
            "APP_FREERANGE_REQUIRED_FUNCTION_MISSING",
            f"Freerange did not report required function(s) in {source.path}: {', '.join(missing)}",
            "Regenerate the AppBundle and do not rename required numeric functions.",
        )
    if set(observed_names) != set(required_names) or len(observed_names) != len(required_names):
        raise FreerangeFailure(
            "APP_FREERANGE_COVERAGE_MISMATCH",
            f"Kernel {source.path} contains a named function outside its manifest scope.",
            "Regenerate the dedicated numeric kernel; every declaration must be manifest-required.",
        )
    if audit_coverage["partial"] or audit_coverage["unsupported"] or audit_coverage["analyzed"] != len(required_names):
        raise FreerangeFailure(
            "APP_FREERANGE_REQUIRED_FUNCTION_INCOMPLETE",
            f"Freerange coverage is incomplete for required kernel {source.path}.",
            "Refactor every required function into Freerange's fully supported subset.",
        )


def _validate_contracts(source: _ScopeFile, audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for contract in audit["contracts"]:
        name = contract["name"]
        incomplete_details = [
            (label, detail)
            for label, key in (
                ("unsupported", "unsupported"),
                ("partially supported", "partially_supported"),
                ("skipped", "skipped"),
            )
            for detail in contract[key]
        ]
        if incomplete_details:
            label, detail = incomplete_details[0]
            raise FreerangeFailure(
                "APP_FREERANGE_REQUIRED_FUNCTION_INCOMPLETE",
                f"Freerange reported a {label} analysis path in {name}: {detail}",
                "Refactor every required function into Freerange's fully supported subset.",
            )
        if contract["unsafe_assertions"]:
            raise FreerangeFailure(
                "APP_FREERANGE_ASSERTION_UNPROVEN",
                f"Freerange did not prove every assertion in {name}.",
                "Fix or simplify the assertion until the pinned analyzer reports it as proven.",
            )
        if contract["assumes"]:
            raise FreerangeFailure(
                "APP_FREERANGE_UNSAFE_CONTRACT",
                f"Freerange relies on an unverified assumption in {name}.",
                "Remove the assumption by validating or normalizing the runtime input in supported code.",
            )
        observed_requires = [item["normalized"] for item in contract["requires"]]
        allowed_requires = source.allowed_requires.get(name, ())
        unexpected_requires = [value for value in observed_requires if value not in allowed_requires]
        if unexpected_requires:
            raise FreerangeFailure(
                "APP_FREERANGE_UNSAFE_CONTRACT",
                f"Freerange inferred an unapproved caller requirement in {name}: {unexpected_requires[0]}",
                "Regenerate the manifest allowlist only after the runtime call boundary enforces that requirement.",
            )
        observed_ensures = [item["normalized"] for item in contract["ensures"]]
        missing_ensures = [value for value in source.required_ensures.get(name, ()) if value not in observed_ensures]
        if missing_ensures:
            raise FreerangeFailure(
                "APP_FREERANGE_COVERAGE_MISMATCH",
                f"Freerange did not establish a required guarantee for {name}: {missing_ensures[0]}",
                "Restore the generated numeric kernel and rerun the pinned analysis.",
            )
        evidence.append(
            {
                "function": name,
                "requires": observed_requires,
                "ensures": observed_ensures,
                "proves": [item["normalized"] for item in contract["proves"]],
                "assumes": [],
            }
        )
    return evidence


def _bounded_contract_evidence(contracts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert parsed contracts into the same bounded shape used by successful reports."""

    return [
        {
            "function": contract["name"],
            "requires": [item["normalized"] for item in contract["requires"]],
            "ensures": [item["normalized"] for item in contract["ensures"]],
            "proves": [item["normalized"] for item in contract["proves"]],
            "assumes": [item["normalized"] for item in contract["assumes"]],
            "unproven_assertions": list(contract["unsafe_assertions"]),
            "unsupported": list(contract["unsupported"]),
            "partially_supported": list(contract["partially_supported"]),
            "skipped": list(contract["skipped"]),
            "observed": list(contract["observed"]),
        }
        for contract in contracts
    ]


def _failure_report(
    *,
    engine: Mapping[str, Any],
    runtime: Mapping[str, Any],
    scope: _ValidatedScope,
    installation: _Installation,
    snapshots: Mapping[str, Mapping[str, Any]],
    file_evidence: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    findings_transcripts: Sequence[tuple[str, bytes]],
    audit_transcripts: Sequence[tuple[str, bytes]],
    timings: Mapping[str, int],
    error: FreerangeFailure,
) -> dict[str, Any]:
    coverage = {
        "required": sum(len(source.required_functions) for source in scope.files),
        "observed": sum(item["coverage"]["functions"] for item in file_evidence),
        "analyzed": sum(item["coverage"]["analyzed"] for item in file_evidence),
        "partial": sum(item["coverage"]["partial"] for item in file_evidence),
        "unsupported": sum(item["coverage"]["unsupported"] for item in file_evidence),
    }
    coverage["fully_analyzed"] = coverage["analyzed"]
    call_sites = [_call_site_evidence(item) for item in scope.call_sites]
    configuration = [
        snapshots[str(path)]["evidence"]
        for path in installation.config_paths
        if str(path) in snapshots and "evidence" in snapshots[str(path)]
    ]
    tool_inputs: list[dict[str, Any]] = []
    tool_bin_snapshot = snapshots.get(str(installation.tool_bin))
    if tool_bin_snapshot is not None and "evidence" in tool_bin_snapshot:
        tool_inputs.append(dict(tool_bin_snapshot["evidence"]))
    tool_inputs.extend(
        [
            {
                "path": "node_modules/@chenglou/freerange",
                "sha256": installation.tool_tree["sha256"],
                "bytes": installation.tool_tree["bytes"],
                "files": installation.tool_tree["files"],
            },
            {
                "path": "node_modules/typescript",
                "sha256": installation.typescript_tree["sha256"],
                "bytes": installation.typescript_tree["bytes"],
                "files": installation.typescript_tree["files"],
            },
        ]
    )
    return {
        "engine": dict(engine),
        "runtime": dict(runtime),
        "status": "failed",
        "required_functions": [name for source in scope.files for name in source.required_functions],
        "scope": {"status": "applicable", "files": list(file_evidence), "call_sites": call_sites},
        "coverage": coverage,
        "findings": [dict(item) for item in findings],
        "source_hashes": {
            "analyzed_sources": [{"path": item.path, "sha256": item.sha256} for item in scope.files],
            "call_sites": call_sites,
            "configuration": configuration,
            "tools": tool_inputs,
        },
        "findings_transcript_sha256": (
            _transcript_set_sha256(findings_transcripts) if findings_transcripts else None
        ),
        "audit_transcript_sha256": _transcript_set_sha256(audit_transcripts) if audit_transcripts else None,
        "findings_transcript_hash": (
            _transcript_set_sha256(findings_transcripts) if findings_transcripts else None
        ),
        "audit_transcript_hash": _transcript_set_sha256(audit_transcripts) if audit_transcripts else None,
        "timings_ms": dict(timings),
        "errors": [error.to_json()],
    }


def _decode_command_output(result: _CommandResult, *, mode: str) -> str:
    stderr = _decode_utf8(result.stderr, f"Freerange {mode} stderr")
    if stderr:
        raise FreerangeFailure(
            "APP_FREERANGE_EXECUTION_FAILED",
            f"Freerange {mode} wrote unexpected stderr output.",
            "Resolve TypeScript or analyzer execution errors and rerun with the pinned toolchain.",
        )
    return _decode_utf8(result.stdout, f"Freerange {mode} stdout")


def _decode_utf8(value: bytes, label: str) -> str:
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _protocol_failure(f"{label} is not valid UTF-8.") from error
    if "\x00" in decoded or "\x1b" in decoded or "\r" in decoded:
        raise _protocol_failure(f"{label} contains forbidden control or color bytes.")
    return decoded


def _protocol_lines(output: str, label: str) -> list[str]:
    if not output.endswith("\n") or output.endswith("\n\n"):
        raise _protocol_failure(f"Freerange {label} transcript has an invalid boundary.")
    lines = output[:-1].split("\n")
    if any(len(line) > 8000 for line in lines):
        raise _protocol_failure(f"Freerange {label} transcript contains an oversized line.")
    return lines


def _protocol_failure(message: str) -> FreerangeFailure:
    return FreerangeFailure(
        "APP_FREERANGE_PROTOCOL_INVALID",
        message,
        f"Use exactly {FREERANGE_PACKAGE}@{FREERANGE_VERSION} with the ViewSpec-pinned dependency lock.",
    )


def _capture_snapshots(
    scope: _ValidatedScope,
    installation: _Installation,
    bun_executable: Path,
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for item in (*scope.files, *scope.call_sites):
        evidence = _hash_file(item.absolute_path, FREERANGE_SOURCE_MAX_BYTES)
        evidence["path"] = item.path
        snapshots[str(item.absolute_path)] = {
            "kind": "file",
            "path": item.absolute_path,
            "limit": FREERANGE_SOURCE_MAX_BYTES,
            "expected": evidence.copy(),
            "evidence": evidence,
        }
    for path in installation.config_paths:
        evidence = _hash_file(path, FREERANGE_JSON_MAX_BYTES)
        if "node_modules" in path.parts:
            index = path.parts.index("node_modules")
            evidence["path"] = PurePosixPath(*path.parts[index:]).as_posix()
        else:
            evidence["path"] = path.name
        snapshots[str(path)] = {
            "kind": "file",
            "path": path,
            "limit": FREERANGE_JSON_MAX_BYTES,
            "expected": evidence.copy(),
            "evidence": evidence,
        }
    tool_bin_evidence = _hash_file(installation.tool_bin, FREERANGE_SOURCE_MAX_BYTES)
    tool_bin_evidence["path"] = "node_modules/@chenglou/freerange/fr.ts"
    snapshots[str(installation.tool_bin)] = {
        "kind": "file",
        "path": installation.tool_bin,
        "limit": FREERANGE_SOURCE_MAX_BYTES,
        "expected": tool_bin_evidence.copy(),
        "evidence": tool_bin_evidence,
    }
    bun_evidence = _hash_file(bun_executable, FREERANGE_BUN_MAX_BYTES)
    bun_evidence["path"] = str(bun_executable)
    snapshots[str(bun_executable)] = {
        "kind": "file",
        "path": bun_executable,
        "limit": FREERANGE_BUN_MAX_BYTES,
        "expected": bun_evidence.copy(),
        "evidence": bun_evidence,
    }
    tool_root = installation.tool_bin.parent
    typescript_root = tool_root.parents[1] / "typescript"
    snapshots[f"tree:{tool_root}"] = {
        "kind": "tree",
        "path": tool_root,
        "limit": FREERANGE_TOOL_TREE_MAX_BYTES,
        "expected": dict(installation.tool_tree),
    }
    snapshots[f"tree:{typescript_root}"] = {
        "kind": "tree",
        "path": typescript_root,
        "limit": FREERANGE_TOOL_TREE_MAX_BYTES,
        "expected": dict(installation.typescript_tree),
    }
    return snapshots


def _assert_snapshots_unchanged(snapshots: Mapping[str, Mapping[str, Any]]) -> None:
    for snapshot in snapshots.values():
        if snapshot["kind"] == "file":
            current = _hash_file(snapshot["path"], snapshot["limit"])
            expected = {key: value for key, value in snapshot["expected"].items() if key != "path"}
        else:
            current = _tree_hash(snapshot["path"], snapshot["limit"])
            expected = snapshot["expected"]
        if current != expected:
            raise FreerangeFailure(
                "APP_FREERANGE_SOURCE_CHANGED",
                f"Proof input changed during Freerange analysis: {snapshot['path']}",
                "Rerun the proof from a stable, unchanged generated AppBundle and toolchain.",
            )


def _hash_file(path: Path, max_bytes: int) -> dict[str, Any]:
    _require_regular_file(path, "APP_FREERANGE_SOURCE_CHANGED", f"Proof input is missing: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise FreerangeFailure(
            "APP_FREERANGE_OUTPUT_LIMIT",
            f"Proof input exceeds its {max_bytes}-byte bound: {path}",
            "Regenerate a bounded AppBundle and remove unexpected generated or tool content.",
        )
    digest = hashlib.sha256()
    counted = 0
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            counted += len(chunk)
            if counted > max_bytes:
                raise FreerangeFailure(
                    "APP_FREERANGE_OUTPUT_LIMIT",
                    f"Proof input grew beyond its bound while hashing: {path}",
                    "Stabilize and regenerate the bounded AppBundle.",
                )
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": counted}


def _tree_hash(root: Path, max_bytes: int) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise FreerangeFailure(
            "APP_FREERANGE_PACKAGE_INTEGRITY",
            f"Installed proof dependency is missing or symlinked: {root}",
            "Reinstall from the unchanged generated package-lock.json.",
        )
    paths: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            if (current / name).is_symlink():
                raise _package_integrity_failure(f"Installed proof dependency contains a symlink: {current / name}")
        for name in file_names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise _package_integrity_failure(f"Installed proof dependency contains a non-regular file: {path}")
            paths.append(path)
    paths.sort(key=lambda path: path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    total = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        file_hash = _hash_file(path, max_bytes)
        total += file_hash["bytes"]
        if total > max_bytes:
            raise FreerangeFailure(
                "APP_FREERANGE_OUTPUT_LIMIT",
                f"Installed proof dependency exceeds its {max_bytes}-byte tree bound: {root}",
                "Reinstall the exact pinned proof dependencies without extra files.",
            )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(file_hash["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash["sha256"]))
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "bytes": total, "files": len(paths)}


def _read_json(path: Path) -> Mapping[str, Any]:
    raw = _hash_file(path, FREERANGE_JSON_MAX_BYTES)
    del raw
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
        raise _package_integrity_failure(f"Cannot read proof configuration as UTF-8 JSON: {path}") from error

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, ValueError) as error:
        raise _package_integrity_failure(f"Proof configuration is not strict JSON: {path}") from error
    if not isinstance(value, Mapping):
        raise _package_integrity_failure(f"Proof configuration root must be an object: {path}")
    return value


def _require_regular_file(path: Path, code: str, message: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise FreerangeFailure(code, message, "Regenerate or reinstall the exact bounded proof input.") from error
    if not stat.S_ISREG(mode):
        raise FreerangeFailure(code, message, "Regenerate or reinstall the exact bounded proof input.")


def _run_bounded_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> _CommandResult:
    """Run without a shell or network-capable package runner and bound captured bytes."""

    if not argv or not Path(argv[0]).is_absolute():
        raise FreerangeFailure(
            "APP_FREERANGE_EXECUTION_FAILED",
            "Freerange commands require an explicit absolute Bun executable.",
            "Use the discovered Bun identity returned by viewspec doctor --freerange.",
        )
    environment: dict[str, str] = {
        "no_color".upper(): "1",
        "CI": "1",
        "LANG": "C",
        "lc_all".upper(): "C",
        "TERM": "dumb",
    }
    for name in ("PATH", "SYSTEMROOT", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        raise FreerangeFailure(
            "APP_FREERANGE_EXECUTION_FAILED",
            f"Could not start Bun for Freerange analysis: {error}",
            "Verify the explicit Bun executable and pinned local dependencies.",
        ) from error

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    overflow = threading.Event()
    lock = threading.Lock()

    def drain(name: str, pipe: Any) -> None:
        nonlocal total
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            with lock:
                remaining = max(0, FREERANGE_COMMAND_OUTPUT_MAX_BYTES - total)
                if remaining:
                    buffers[name].extend(chunk[:remaining])
                total += len(chunk)
                if total > FREERANGE_COMMAND_OUTPUT_MAX_BYTES:
                    overflow.set()
                    _terminate_process(process)
                    return

    threads = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        _terminate_process(process)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        for thread in threads:
            thread.join(timeout=1)
        raise FreerangeFailure(
            "APP_FREERANGE_TIMEOUT",
            f"Freerange command exceeded its {timeout_seconds:g}-second bound.",
            "Simplify the generated numeric kernel or stabilize the local runtime, then retry.",
        ) from error
    for thread in threads:
        thread.join(timeout=1)
    if overflow.is_set():
        raise FreerangeFailure(
            "APP_FREERANGE_OUTPUT_LIMIT",
            f"Freerange command exceeded its {FREERANGE_COMMAND_OUTPUT_MAX_BYTES}-byte output bound.",
            "Reduce the numeric scope or fix unexpectedly verbose analyzer output.",
        )
    return _CommandResult(
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        returncode=returncode,
        duration_ms=_elapsed_ms(started),
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _function_identity_sha256(path: str, source_sha256: str, name: str) -> str:
    material = f"viewspec.freerange-function-v1\0{path}\0{source_sha256}\0{name}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _coverage_with_alias(coverage: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(coverage)
    result["fully_analyzed"] = result["analyzed"]
    return result


def _call_site_evidence(item: _ScopeCallSite) -> dict[str, Any]:
    evidence: dict[str, Any] = {"path": item.path, "sha256": item.sha256}
    if item.connection is not None:
        evidence.update(
            {
                "connection": item.connection,
                "required_functions": list(item.required_functions),
            }
        )
    return evidence


def _transcript_set_sha256(transcripts: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(b"viewspec.freerange-transcripts-v1\0")
    for path, transcript in transcripts:
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(transcript).to_bytes(8, "big"))
        digest.update(transcript)
    return digest.hexdigest()


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


__all__ = [
    "FREERANGE_BIN",
    "FREERANGE_NPM_INTEGRITY",
    "FREERANGE_PACKAGE",
    "FREERANGE_PACKAGE_TREE",
    "FREERANGE_PROTOCOL",
    "FREERANGE_VERSION",
    "FreerangeFailure",
    "analyze_freerange_numeric_scope",
    "freerange_readiness",
]
