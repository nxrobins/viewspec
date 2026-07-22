from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

import viewspec.app_freerange as app_freerange
from viewspec.app_freerange import (
    FREERANGE_BIN,
    FREERANGE_NPM_INTEGRITY,
    FREERANGE_NPM_RESOLVED,
    FREERANGE_PACKAGE,
    FREERANGE_TYPESCRIPT_INTEGRITY,
    FREERANGE_TYPESCRIPT_RESOLVED,
    FREERANGE_TYPESCRIPT_VERSION,
    FREERANGE_VERSION,
    FreerangeFailure,
    analyze_freerange_numeric_scope,
    freerange_readiness,
)
from viewspec.app_numeric import generate_numeric_typescript, numeric_function_hashes


KERNEL_PATH = "src/viewspec_numeric.ts"
CALL_SITE_PATH = "src/state_reducer.ts"
FUNCTION_NAME = "addFiniteNumbers"
REQUIREMENT = "Number.isFinite(current)"
ENSURE = "return is a finite number"
_TEST_FREERANGE_TREE = {
    "sha256": "ac16b7e631f0b4b94b4c3f14439ea1d98fe9a60adf439286891c422408db17c9",
    "bytes": 194,
    "files": 2,
}
_TEST_TYPESCRIPT_TREE = {
    "sha256": "f985e6ea285d41f4940243da86c9b59b6f612b556902a61f9ff4070ded720095",
    "bytes": 75,
    "files": 2,
}


@pytest.fixture(autouse=True)
def _pin_small_test_dependency_trees(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_freerange, "FREERANGE_PACKAGE_TREE", dict(_TEST_FREERANGE_TREE))
    monkeypatch.setattr(app_freerange, "FREERANGE_TYPESCRIPT_TREE", dict(_TEST_TYPESCRIPT_TREE))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _make_app(tmp_path: Path) -> tuple[Path, Path]:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _write_json(
        app_dir / "package.json",
        {
            "name": "generated-app",
            "private": True,
            "devDependencies": {
                FREERANGE_PACKAGE: FREERANGE_VERSION,
                "typescript": FREERANGE_TYPESCRIPT_VERSION,
            },
        },
    )
    _write_json(
        app_dir / "package-lock.json",
        {
            "name": "generated-app",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "name": "generated-app",
                    "devDependencies": {
                        FREERANGE_PACKAGE: FREERANGE_VERSION,
                        "typescript": FREERANGE_TYPESCRIPT_VERSION,
                    },
                },
                "node_modules/@chenglou/freerange": {
                    "version": FREERANGE_VERSION,
                    "resolved": FREERANGE_NPM_RESOLVED,
                    "integrity": FREERANGE_NPM_INTEGRITY,
                    "dependencies": {"typescript": "^6.0.2"},
                    "bin": {"fr": FREERANGE_BIN},
                },
                "node_modules/typescript": {
                    "version": FREERANGE_TYPESCRIPT_VERSION,
                    "resolved": FREERANGE_TYPESCRIPT_RESOLVED,
                    "integrity": FREERANGE_TYPESCRIPT_INTEGRITY,
                },
            },
        },
    )
    _write_json(
        app_dir / "tsconfig.json",
        {"compilerOptions": {"strict": True, "noEmit": True}, "include": ["src"]},
    )
    tool_root = app_dir / "node_modules" / "@chenglou" / "freerange"
    _write_json(
        tool_root / "package.json",
        {
            "name": FREERANGE_PACKAGE,
            "version": FREERANGE_VERSION,
            "type": "module",
            "bin": {"fr": FREERANGE_BIN},
            "dependencies": {"typescript": "^6.0.2"},
        },
    )
    (tool_root / FREERANGE_BIN).write_text("// pinned test analyzer entry\n", encoding="utf-8")
    _write_json(
        app_dir / "node_modules" / "typescript" / "package.json",
        {"name": "typescript", "version": FREERANGE_TYPESCRIPT_VERSION},
    )
    (app_dir / "node_modules" / "typescript" / "lib").mkdir()
    (app_dir / "node_modules" / "typescript" / "lib" / "typescript.js").write_text(
        "// pinned test typescript\n", encoding="utf-8"
    )
    kernel = app_dir / KERNEL_PATH
    kernel.parent.mkdir()
    kernel.write_text(
        "export function addFiniteNumbers(current: number, amount: number): number {\n"
        "  return current + amount\n"
        "}\n",
        encoding="utf-8",
    )
    call_site = app_dir / CALL_SITE_PATH
    call_site.write_text(
        "import {addFiniteNumbers} from './viewspec_numeric'\n"
        "export const next = addFiniteNumbers(1, 2)\n",
        encoding="utf-8",
    )
    bun = app_dir / "bun"
    bun.write_bytes(b"fake bun test executable\n")
    bun.chmod(0o755)
    return app_dir, bun


def _scope(app_dir: Path) -> dict[str, Any]:
    return {
        "status": "applicable",
        "files": [
            {
                "path": KERNEL_PATH,
                "sha256": _sha(app_dir / KERNEL_PATH),
                "required_functions": [FUNCTION_NAME],
                "allowed_requires": {FUNCTION_NAME: [REQUIREMENT]},
                "required_ensures": {FUNCTION_NAME: [ENSURE]},
            }
        ],
        "call_sites": [{"path": CALL_SITE_PATH, "sha256": _sha(app_dir / CALL_SITE_PATH)}],
    }


def _findings(*, analyzed: int = 1, functions: int = 1, partial: int = 0, unsupported: int = 0) -> str:
    return (
        "No lint findings.\n\n"
        "0 findings (0 errors, 0 warnings).\n"
        f"coverage: {analyzed}/{functions} named top-level function declarations fully analyzed; "
        f"{partial} partially supported; {unsupported} unsupported.\n"
        "Run `fr --audit [file]` for every function's contracts and refactoring suggestions.\n"
    )


def _audit(*, extra_contract_lines: tuple[str, ...] = ()) -> str:
    contract_lines = (
        f"  requires: {REQUIREMENT} (input at {KERNEL_PATH}:1:41)",
        f"  ensures: {ENSURE}",
        *extra_contract_lines,
    )
    return (
        f"# {KERNEL_PATH} (1/1 functions fully analyzed)\n\n"
        "## Contracts\n\n"
        f"{FUNCTION_NAME}\n"
        + "\n".join(contract_lines)
        + "\n"
    )


def _command_result(stdout: str, *, returncode: int = 0, stderr: str = "") -> app_freerange._CommandResult:
    return app_freerange._CommandResult(
        stdout=stdout.encode("utf-8"),
        stderr=stderr.encode("utf-8"),
        returncode=returncode,
        duration_ms=2,
    )


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    findings: str | None = None,
    audit: str | None = None,
    findings_returncode: int = 0,
    audit_returncode: int = 0,
    after_audit: Any = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, cwd: Path, timeout_seconds: float) -> app_freerange._CommandResult:
        del cwd, timeout_seconds
        calls.append(list(argv))
        if argv[-1] == "--version":
            return _command_result("1.2.3\n")
        if "--audit" in argv:
            result = _command_result(audit if audit is not None else _audit(), returncode=audit_returncode)
            if after_audit is not None:
                after_audit()
            return result
        return _command_result(findings if findings is not None else _findings(), returncode=findings_returncode)

    monkeypatch.setattr(app_freerange, "_run_bounded_command", fake_run)
    return calls


def _assert_failure(code: str, operation: Any) -> FreerangeFailure:
    with pytest.raises(FreerangeFailure) as caught:
        operation()
    assert caught.value.code == code
    assert caught.value.fix
    assert caught.value.report is not None
    assert caught.value.report["status"] == "failed"
    assert caught.value.report["errors"][0]["code"] == code
    return caught.value


def test_valid_numeric_kernel_returns_complete_bounded_evidence(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    calls = _install_fake_runner(monkeypatch)

    report = analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun)

    assert report["status"] == "passed"
    assert report["engine"] == {
        "name": "freerange",
        "package": FREERANGE_PACKAGE,
        "version": FREERANGE_VERSION,
        "bin": FREERANGE_BIN,
        "integrity": FREERANGE_NPM_INTEGRITY,
        "package_tree_sha256": _TEST_FREERANGE_TREE["sha256"],
        "protocol": "viewspec.freerange-text-v0.0.1",
    }
    assert report["runtime"]["version"] == "1.2.3"
    assert report["coverage"] == {
        "required": 1,
        "observed": 1,
        "analyzed": 1,
        "fully_analyzed": 1,
        "partial": 0,
        "unsupported": 0,
    }
    file_report = report["scope"]["files"][0]
    assert file_report["contracts"] == [
        {
            "function": FUNCTION_NAME,
            "requires": [REQUIREMENT],
            "ensures": [ENSURE],
            "proves": [],
            "assumes": [],
        }
    ]
    assert len(file_report["required_functions"][0]["sha256"]) == 64
    assert file_report["required_functions"][0]["hash_kind"] == "file_bound_identity_sha256"
    assert len(report["findings_transcript_sha256"]) == 64
    assert len(report["audit_transcript_sha256"]) == 64
    assert report["findings_transcript_hash"] == report["findings_transcript_sha256"]
    assert report["audit_transcript_hash"] == report["audit_transcript_sha256"]
    assert report["required_functions"] == [FUNCTION_NAME]
    assert report["findings"] == []
    assert report["errors"] == []
    assert [call[-2:] for call in calls] == [[str(bun), "--version"], [str(app_dir / "node_modules/@chenglou/freerange/fr.ts"), KERNEL_PATH], ["--audit", KERNEL_PATH]]


def test_generated_manifest_scope_is_cross_checked_against_the_kernel_contract(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    generated_scope = {
        "schema_version": 1,
        "profile": "viewspec_numeric_kernel_v1",
        "status": "applicable",
        "kernel_path": KERNEL_PATH,
        "required_functions": [FUNCTION_NAME],
        "allowed_requires": {FUNCTION_NAME: [REQUIREMENT, "Number.isFinite(amount)"]},
        "required_ensures": {FUNCTION_NAME: [ENSURE]},
    }
    (app_dir / KERNEL_PATH).write_text(generate_numeric_typescript(generated_scope), encoding="utf-8")
    (app_dir / CALL_SITE_PATH).write_text(
        f'import {{ {FUNCTION_NAME} }} from "./viewspec_numeric";\n'
        f"export const next = {FUNCTION_NAME}(1, 2);\n",
        encoding="utf-8",
    )
    generated_scope.update(
        {
            "files": [
                {
                    "path": KERNEL_PATH,
                    "sha256": _sha(app_dir / KERNEL_PATH),
                    "required_functions": [FUNCTION_NAME],
                    "function_sha256": numeric_function_hashes(generated_scope),
                    "allowed_requires": generated_scope["allowed_requires"],
                    "required_ensures": generated_scope["required_ensures"],
                }
            ],
            "call_sites": [
                {
                    "path": CALL_SITE_PATH,
                    "sha256": _sha(app_dir / CALL_SITE_PATH),
                    "required_functions": [FUNCTION_NAME],
                    "connection": "generated_import_and_call_v1",
                }
            ],
        }
    )
    audit = (
        f"# {KERNEL_PATH} (1/1 functions fully analyzed)\n\n"
        "## Contracts\n\n"
        f"{FUNCTION_NAME}\n"
        f"  requires: {REQUIREMENT} (input at {KERNEL_PATH}:4:34)\n"
        f"  requires: Number.isFinite(amount) (input at {KERNEL_PATH}:4:51)\n"
        f"  ensures: {ENSURE}\n"
    )
    _install_fake_runner(monkeypatch, audit=audit)

    report = analyze_freerange_numeric_scope(app_dir, generated_scope, bun_executable=bun)

    required = report["scope"]["files"][0]["required_functions"][0]
    assert required["hash_kind"] == "source_declaration_sha256"
    assert required["sha256"] == numeric_function_hashes(generated_scope)[FUNCTION_NAME]
    assert report["scope"]["call_sites"][0]["connection"] == "generated_import_and_call_v1"


def test_generated_connection_metadata_requires_exact_import_and_real_calls(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    generated_scope = {
        "schema_version": 1,
        "profile": "viewspec_numeric_kernel_v1",
        "status": "applicable",
        "kernel_path": KERNEL_PATH,
        "required_functions": [FUNCTION_NAME],
        "allowed_requires": {FUNCTION_NAME: [REQUIREMENT, "Number.isFinite(amount)"]},
        "required_ensures": {FUNCTION_NAME: [ENSURE]},
    }
    (app_dir / KERNEL_PATH).write_text(generate_numeric_typescript(generated_scope), encoding="utf-8")
    (app_dir / CALL_SITE_PATH).write_text(
        f'import {{ {FUNCTION_NAME} }} from "./viewspec_numeric";\n'
        f"// the required runtime call was removed: {FUNCTION_NAME}(1, 2)\n",
        encoding="utf-8",
    )
    generated_scope.update(
        {
            "files": [
                {
                    "path": KERNEL_PATH,
                    "sha256": _sha(app_dir / KERNEL_PATH),
                    "required_functions": [FUNCTION_NAME],
                    "function_sha256": numeric_function_hashes(generated_scope),
                    "allowed_requires": generated_scope["allowed_requires"],
                    "required_ensures": generated_scope["required_ensures"],
                }
            ],
            "call_sites": [
                {
                    "path": CALL_SITE_PATH,
                    "sha256": _sha(app_dir / CALL_SITE_PATH),
                    "required_functions": [FUNCTION_NAME],
                    "connection": "generated_import_and_call_v1",
                }
            ],
        }
    )
    _install_fake_runner(monkeypatch)

    _assert_failure(
        "APP_FREERANGE_SCOPE_INVALID",
        lambda: analyze_freerange_numeric_scope(app_dir, generated_scope, bun_executable=bun),
    )


def test_division_by_zero_finding_fails_with_structured_evidence(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    finding = (
        f"{KERNEL_PATH}(2,10): error [inferred-requirement]: "
        f"division has a divisor that is definitely zero in {FUNCTION_NAME}\n\n"
        "1 finding (1 error, 0 warnings).\n"
        "coverage: 1/1 named top-level function declarations fully analyzed; 0 partially supported; 0 unsupported.\n"
        "Run `fr --audit [file]` for every function's contracts and refactoring suggestions.\n"
    )
    _install_fake_runner(monkeypatch, findings=finding, findings_returncode=1)

    failure = _assert_failure(
        "APP_FREERANGE_FINDINGS",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )

    assert failure.report["coverage"]["analyzed"] == 1
    assert failure.report["findings"][0]["rule"] == "inferred-requirement"
    assert failure.report["audit_transcript_sha256"]


def test_zero_coverage_is_not_a_successful_exit(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(
        monkeypatch,
        findings=_findings(analyzed=0, functions=0),
        audit=f"# {KERNEL_PATH} (no named function declarations)\n",
    )

    _assert_failure(
        "APP_FREERANGE_REQUIRED_FUNCTION_MISSING",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


@pytest.mark.parametrize(
    ("findings", "audit"),
    [
        (
            _findings(analyzed=0, functions=1, partial=1),
            f"# {KERNEL_PATH} (0/1 functions fully analyzed; 1 partially supported)\n\n"
            "## Contracts\n\n"
            f"{FUNCTION_NAME}\n"
            "  partially supported: unsupported operation at src/viewspec_numeric.ts:2:3\n"
            "  on analyzed paths: return is a finite number\n",
        ),
        (
            _findings(analyzed=0, functions=1, unsupported=1),
            f"# {KERNEL_PATH} (0/1 functions fully analyzed; 1 unsupported)\n\n"
            "## Contracts\n\n"
            f"{FUNCTION_NAME}\n"
            "  unsupported: async function at src/viewspec_numeric.ts:1:1\n",
        ),
    ],
)
def test_partial_or_unsupported_required_function_fails(tmp_path, monkeypatch, findings, audit):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(monkeypatch, findings=findings, audit=audit)

    _assert_failure(
        "APP_FREERANGE_REQUIRED_FUNCTION_INCOMPLETE",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


@pytest.mark.parametrize(
    "detail",
    [
        "  unsupported: async function at src/viewspec_numeric.ts:1:1",
        "  partially supported: unsupported operation at src/viewspec_numeric.ts:2:3",
        "  skipped: unsupported branch at src/viewspec_numeric.ts:2:3",
    ],
)
def test_contract_detail_cannot_contradict_full_coverage(tmp_path, monkeypatch, detail):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(monkeypatch, audit=_audit(extra_contract_lines=(detail,)))

    _assert_failure(
        "APP_FREERANGE_REQUIRED_FUNCTION_INCOMPLETE",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_skipped_module_setup_is_rejected_even_with_full_function_coverage(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    audit = (
        f"# {KERNEL_PATH} (1/1 functions fully analyzed; 1 module statement skipped)\n\n"
        "## Contracts\n\n"
        "module initialization\n"
        "  skipped: unsupported module statement at src/viewspec_numeric.ts:1:1\n\n"
        f"{FUNCTION_NAME}\n"
        f"  requires: {REQUIREMENT} (input at {KERNEL_PATH}:1:41)\n"
        f"  ensures: {ENSURE}\n"
    )
    _install_fake_runner(monkeypatch, audit=audit)

    _assert_failure(
        "APP_FREERANGE_INITIALIZER_INCOMPLETE",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_missing_runtime_is_actionable_in_readiness_and_analysis(tmp_path, monkeypatch):
    app_dir, _bun = _make_app(tmp_path)
    monkeypatch.setattr(app_freerange.shutil, "which", lambda _name: None)

    readiness = freerange_readiness(app_dir)
    assert readiness["ok"] is False
    assert readiness["errors"][0]["code"] == "APP_FREERANGE_RUNTIME_MISSING"

    _assert_failure(
        "APP_FREERANGE_RUNTIME_MISSING",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir)),
    )


def test_version_and_protocol_drift_fail_closed(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    package = json.loads((app_dir / "package.json").read_text(encoding="utf-8"))
    package["devDependencies"][FREERANGE_PACKAGE] = "0.0.2"
    _write_json(app_dir / "package.json", package)
    _install_fake_runner(monkeypatch)

    failure = _assert_failure(
        "APP_FREERANGE_VERSION_MISMATCH",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )
    assert failure.report["runtime"]["status"] == "ready"
    assert failure.report["runtime"]["version"] == "1.2.3"
    assert failure.report["scope"]["status"] == "applicable"
    assert failure.report["coverage"]["required"] == 1
    assert failure.report["source_hashes"]["analyzed_sources"][0]["sha256"] == _sha(
        app_dir / KERNEL_PATH
    )

    readiness = freerange_readiness(app_dir, bun_executable=bun)
    assert readiness["runtime"]["status"] == "ready"
    assert readiness["package"]["error_code"] == "APP_FREERANGE_VERSION_MISMATCH"

    package["devDependencies"][FREERANGE_PACKAGE] = FREERANGE_VERSION
    _write_json(app_dir / "package.json", package)
    _install_fake_runner(monkeypatch, findings=_findings() + "unexpected\n")
    _assert_failure(
        "APP_FREERANGE_PROTOCOL_INVALID",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_prerelease_bun_version_is_not_accepted_as_stable(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)

    def fake_run(argv, *, cwd, timeout_seconds):
        del cwd, timeout_seconds
        assert argv[-1] == "--version"
        return _command_result("1.2.3-canary.1\n")

    monkeypatch.setattr(app_freerange, "_run_bounded_command", fake_run)

    failure = _assert_failure(
        "APP_FREERANGE_RUNTIME_UNSUPPORTED",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )
    assert failure.report["runtime"]["status"] == "unsupported"
    assert failure.report["scope"]["status"] == "applicable"


def test_installed_package_tree_must_match_the_pinned_npm_artifacts(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(monkeypatch)
    (app_dir / "node_modules" / "@chenglou" / "freerange" / FREERANGE_BIN).write_text(
        "// substituted analyzer\n", encoding="utf-8"
    )

    _assert_failure(
        "APP_FREERANGE_PACKAGE_INTEGRITY",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )

    typescript_case = tmp_path / "typescript-case"
    typescript_case.mkdir()
    app_dir, bun = _make_app(typescript_case)
    (app_dir / "node_modules" / "typescript" / "lib" / "typescript.js").write_text(
        "// substituted typescript\n", encoding="utf-8"
    )
    _assert_failure(
        "APP_FREERANGE_DEPENDENCY_DRIFT",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_not_applicable_returns_before_runtime_or_package_discovery(tmp_path, monkeypatch):
    app_dir = tmp_path / "empty-app"
    app_dir.mkdir()
    monkeypatch.setattr(
        app_freerange,
        "_runtime_identity",
        lambda _value: pytest.fail("not_applicable must not discover Bun"),
    )

    report = analyze_freerange_numeric_scope(
        app_dir,
        {"status": "not_applicable", "files": [], "call_sites": []},
    )

    assert report["status"] == "not_applicable"
    assert report["runtime"] == {"name": "bun", "status": "not_required"}
    assert report["coverage"]["required"] == 0


def test_source_mutation_after_audit_invalidates_the_proof(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)

    def mutate_source() -> None:
        (app_dir / KERNEL_PATH).write_text("export function changed(): number { return 9 }\n", encoding="utf-8")

    _install_fake_runner(monkeypatch, after_audit=mutate_source)

    _assert_failure(
        "APP_FREERANGE_SOURCE_CHANGED",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_output_bound_failure_is_preserved_and_reported(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    calls = 0

    def fake_run(argv: list[str], *, cwd: Path, timeout_seconds: float) -> app_freerange._CommandResult:
        nonlocal calls
        del cwd, timeout_seconds
        calls += 1
        if argv[-1] == "--version":
            return _command_result("1.2.3\n")
        raise FreerangeFailure(
            "APP_FREERANGE_OUTPUT_LIMIT",
            "bounded test output exceeded",
            "reduce output",
        )

    monkeypatch.setattr(app_freerange, "_run_bounded_command", fake_run)

    _assert_failure(
        "APP_FREERANGE_OUTPUT_LIMIT",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )
    assert calls == 2


def test_command_runner_enforces_real_output_and_timeout_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(app_freerange, "FREERANGE_COMMAND_OUTPUT_MAX_BYTES", 1024)
    with pytest.raises(FreerangeFailure) as oversized:
        app_freerange._run_bounded_command(
            [sys.executable, "-c", "import os; os.write(1, b'x' * 65536)"],
            cwd=tmp_path,
            timeout_seconds=2,
        )
    assert oversized.value.code == "APP_FREERANGE_OUTPUT_LIMIT"

    with pytest.raises(FreerangeFailure) as timed_out:
        app_freerange._run_bounded_command(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            cwd=tmp_path,
            timeout_seconds=0.01,
        )
    assert timed_out.value.code == "APP_FREERANGE_TIMEOUT"


@pytest.mark.parametrize(
    ("extra_line", "code"),
    [
        (
            "  assumes: every array element is finite (input at src/viewspec_numeric.ts:1:1)",
            "APP_FREERANGE_UNSAFE_CONTRACT",
        ),
        (
            "  assertion unproven: could not prove result >= 0 (at src/viewspec_numeric.ts:2:3)",
            "APP_FREERANGE_ASSERTION_UNPROVEN",
        ),
    ],
)
def test_unsafe_assumptions_and_unproven_assertions_are_rejected(tmp_path, monkeypatch, extra_line, code):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(monkeypatch, audit=_audit(extra_contract_lines=(extra_line,)))

    _assert_failure(
        code,
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_unapproved_requirement_and_missing_required_ensure_are_rejected(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    scope = _scope(app_dir)
    scope["files"][0]["allowed_requires"] = {FUNCTION_NAME: []}
    _install_fake_runner(monkeypatch)
    _assert_failure(
        "APP_FREERANGE_UNSAFE_CONTRACT",
        lambda: analyze_freerange_numeric_scope(app_dir, scope, bun_executable=bun),
    )

    scope = _scope(app_dir)
    scope["files"][0]["required_ensures"] = {FUNCTION_NAME: ["return is at least 0"]}
    _assert_failure(
        "APP_FREERANGE_COVERAGE_MISMATCH",
        lambda: analyze_freerange_numeric_scope(app_dir, scope, bun_executable=bun),
    )


def test_pinned_refactoring_suggestion_section_is_fully_consumed():
    audit = (
        _audit().removesuffix("\n")
        + "\n\n## Refactoring suggestions\n\n"
        + f"{KERNEL_PATH}(1,41): suggestion [encode-input-rule]: "
        + "Validate the numeric input before calculation. Keep runtime behavior explicit.\n"
    )

    parsed = app_freerange._parse_audit(audit, KERNEL_PATH)

    assert parsed["suggestions"][0]["rule"] == "encode-input-rule"


def test_real_0_0_1_six_helper_transcript_fixture_parses_completely():
    # Captured from the exact @chenglou/freerange 0.0.1 package against the generated
    # six-helper kernel. This guards the pinned text adapter on hosts without Bun.
    findings = (
        "No lint findings.\n\n"
        "0 findings (0 errors, 0 warnings).\n"
        "coverage: 6/6 named top-level function declarations fully analyzed; "
        "0 partially supported; 0 unsupported.\n"
        "Run `fr --audit [file]` for every function's contracts and refactoring suggestions.\n"
    )
    audit = """# src/viewspec_numeric.ts (6/6 functions fully analyzed)

## Contracts

clampMoveIndex
  requires: Number.isFinite(rawIndex) (input at src/viewspec_numeric.ts:4:32)
  requires: Number.isFinite(length) (input at src/viewspec_numeric.ts:4:50)
  ensures: return is a finite integer number at least 0

addFiniteNumbers
  requires: Number.isFinite(current) (input at src/viewspec_numeric.ts:11:34)
  requires: Number.isFinite(amount) (input at src/viewspec_numeric.ts:11:51)
  ensures: return is a finite number

compareFiniteNumbers
  requires: Number.isFinite(left) (input at src/viewspec_numeric.ts:18:38)
  requires: Number.isFinite(right) (input at src/viewspec_numeric.ts:18:52)
  ensures: return is a finite number

applySortDirection
  requires: Number.isFinite(comparison) (input at src/viewspec_numeric.ts:25:36)
  requires: Number.isFinite(direction) (input at src/viewspec_numeric.ts:25:56)
  ensures: return is a finite number

stableSortIndexDelta
  requires: Number.isFinite(leftIndex) (input at src/viewspec_numeric.ts:33:38)
  requires: Number.isFinite(rightIndex) (input at src/viewspec_numeric.ts:33:57)
  ensures: return is a finite integer number

normalizeSliceIndex
  requires: Number.isFinite(index) (input at src/viewspec_numeric.ts:41:37)
  ensures: return is a finite integer number at least 0
"""

    parsed_findings = app_freerange._parse_findings(findings, KERNEL_PATH)
    parsed_audit = app_freerange._parse_audit(audit, KERNEL_PATH)

    assert parsed_findings["findings"] == []
    assert parsed_findings["coverage"] == {
        "analyzed": 6,
        "functions": 6,
        "partial": 0,
        "unsupported": 0,
    }
    assert parsed_audit["coverage"] == {
        "functions": 6,
        "analyzed": 6,
        "partial": 0,
        "unsupported": 0,
        "initializer": "analyzed",
        "initializer_skips": 0,
    }
    assert [contract["name"] for contract in parsed_audit["contracts"]] == [
        "clampMoveIndex",
        "addFiniteNumbers",
        "compareFiniteNumbers",
        "applySortDirection",
        "stableSortIndexDelta",
        "normalizeSliceIndex",
    ]


def test_findings_and_audit_must_agree_and_kernel_may_not_hide_extra_functions(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    disagreement = _findings(analyzed=0, functions=1, unsupported=1)
    _install_fake_runner(monkeypatch, findings=disagreement)
    _assert_failure(
        "APP_FREERANGE_COVERAGE_MISMATCH",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )

    extra_audit = (
        f"# {KERNEL_PATH} (2/2 functions fully analyzed)\n\n"
        "## Contracts\n\n"
        f"{FUNCTION_NAME}\n"
        f"  requires: {REQUIREMENT} (input at {KERNEL_PATH}:1:41)\n"
        f"  ensures: {ENSURE}\n\n"
        "shadowCertificate\n"
        "  ensures: return is exactly 1\n"
    )
    _install_fake_runner(monkeypatch, findings=_findings(analyzed=2, functions=2), audit=extra_audit)
    _assert_failure(
        "APP_FREERANGE_COVERAGE_MISMATCH",
        lambda: analyze_freerange_numeric_scope(app_dir, _scope(app_dir), bun_executable=bun),
    )


def test_scope_hashes_paths_and_required_functions_are_validated_before_runtime(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    _install_fake_runner(monkeypatch)
    scope = _scope(app_dir)
    scope["files"][0]["sha256"] = "0" * 64
    _assert_failure(
        "APP_FREERANGE_SOURCE_CHANGED",
        lambda: analyze_freerange_numeric_scope(app_dir, scope, bun_executable=bun),
    )

    scope = _scope(app_dir)
    scope["files"][0]["path"] = "../escape.ts"
    _assert_failure(
        "APP_FREERANGE_SCOPE_INVALID",
        lambda: analyze_freerange_numeric_scope(app_dir, scope, bun_executable=bun),
    )


def test_readiness_is_read_only_and_records_pinned_runtime_and_package(tmp_path, monkeypatch):
    app_dir, bun = _make_app(tmp_path)
    before = {path.relative_to(app_dir).as_posix(): _sha(path) for path in app_dir.rglob("*") if path.is_file()}
    calls = _install_fake_runner(monkeypatch)

    report = freerange_readiness(app_dir, bun_executable=bun)

    after = {path.relative_to(app_dir).as_posix(): _sha(path) for path in app_dir.rglob("*") if path.is_file()}
    assert report["ok"] is True
    assert report["package"]["tool_tree_sha256"]
    assert report["package"]["typescript_version"] == FREERANGE_TYPESCRIPT_VERSION
    assert before == after
    assert len(calls) == 1
