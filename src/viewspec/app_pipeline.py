"""AppBundle compile/prove orchestration pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from viewspec.app_errors import AppBundleProofFailure, _normalize_proof_errors
from viewspec.app_paths import (
    _assert_report_under_output,
    _prepare_app_output_dir,
    _prepare_app_shell_output_dir,
    _should_write_app_proof_failure,
)
from viewspec.app_prepared import _PreparedAppProof, _PreparedAppShell
from viewspec.app_reports import (
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_DEFAULT_REPORT,
    APP_BUNDLE_DEFAULT_SUMMARY,
    APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE,
    APP_BUNDLE_TARGET,
    _app_proof_failure_report,
    _app_proof_report,
    _app_shell_failure_report,
    _app_shell_report,
    _write_app_proof,
)
from viewspec.app_resource_binding import _resource_binding_assertion_report
from viewspec.app_screens import _prove_app_screens
from viewspec.app_shell import (
    APP_SHELL_DEFAULT_OUT,
    APP_SHELL_DIAGNOSTICS,
    APP_SHELL_DIR_NAME,
    APP_SHELL_INDEX,
    APP_SHELL_MANIFEST,
    APP_SHELL_TARGET,
)
from viewspec.app_shell_writer import _write_static_app_shell
from viewspec.app_starters import starter_app_bundle
from viewspec.app_validation import (
    APP_BUNDLE_RESOURCE_BINDING,
    _reject_json_constant,
    _route_assertions,
    validate_app_text,
)
from viewspec.local_tools import atomic_write, resolve_cwd, resolve_local_path
from viewspec.state_ir import check_reducer_conformance, generate_typescript_reducer, state_manifest


def init_app_file(
    path: str | Path = "viewspec.app.json",
    *,
    kind: str = "internal_tool",
    force: bool = False,
    resource_binding: str = APP_BUNDLE_RESOURCE_BINDING,
) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    payload = starter_app_bundle(kind, resource_binding=resource_binding)
    atomic_write(output, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return output


def prove_app(
    *,
    app_path: str | Path,
    out_dir: str | Path = APP_BUNDLE_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    with_shell: bool = False,
    cwd: str | Path | None = None,
    _generate_reducer: Any = generate_typescript_reducer,
    _check_conformance: Any = check_reducer_conformance,
    _build_manifest: Any = state_manifest,
) -> dict[str, Any]:
    timings: dict[str, int] = {}
    root = resolve_cwd(cwd)
    output_dir = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=True)
    report_path = resolve_local_path(report_out, cwd=root, allow_outside_cwd=True) if report_out else output_dir / APP_BUNDLE_DEFAULT_REPORT
    try:
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=True, must_exist=True)
            if design_path is not None
            else None
        )
        app_text = source.read_text(encoding="utf-8")
        validation = _time_phase(timings, "validate_app", lambda: validate_app_text(app_text, compile_check=True))
        if not validation["ok"]:
            return _app_proof_failure_report(
                output_dir=output_dir,
                report_path=report_path,
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Fix the AppBundle and retry prove-app.",
                    }
                    for issue in validation["issues"]
                ],
                timings=timings,
                validation=validation,
                write=False,
            )
        payload = json.loads(app_text, parse_constant=_reject_json_constant)
        _assert_report_under_output(report_path, output_dir)
        _prepare_app_output_dir(output_dir, root=root, force=force, raw_out=out_dir)
        prepared = _PreparedAppProof(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            report_path=report_path,
            summary_path=output_dir / APP_BUNDLE_DEFAULT_SUMMARY,
            support_path=output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE,
        )
        screen_reports = _time_phase(
            timings,
            "screens",
            lambda: _prove_app_screens(
                payload,
                prepared.output_dir,
                design_path=prepared.design_path,
                root=root,
                strict_design=strict_design,
                target=APP_BUNDLE_TARGET,
            ),
        )
        errors = [error for screen in screen_reports for error in screen.get("errors", []) if isinstance(error, dict)]
        binding_report: dict[str, Any] | None = None
        if not errors:
            binding_report = _time_phase(timings, "resource_binding", lambda: _resource_binding_assertion_report(payload, screen_reports))
            if isinstance(binding_report, dict) and not binding_report.get("ok"):
                errors.extend(_normalize_proof_errors(binding_report.get("errors")))
        shell_report: dict[str, Any] | None = None
        if with_shell and not errors:
            shell_report = _time_phase(
                timings,
                "shell",
                lambda: _write_static_app_shell(
                    payload,
                    screen_reports,
                    _PreparedAppShell(
                        output_dir=prepared.output_dir / APP_SHELL_DIR_NAME,
                        app_path=source,
                        design_path=design,
                        manifest_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_MANIFEST,
                        diagnostics_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_DIAGNOSTICS,
                        index_path=prepared.output_dir / APP_SHELL_DIR_NAME / APP_SHELL_INDEX,
                    ),
                    root=root,
                    force=False,
                    raw_out=APP_SHELL_DIR_NAME,
                    strict_design=strict_design,
                    validation=validation,
                    clean_output=True,
                    resource_binding_report=binding_report,
                    generate_reducer=_generate_reducer,
                    check_conformance=_check_conformance,
                    build_manifest=_build_manifest,
                ),
            )
            if not shell_report.get("ok"):
                errors.extend(_normalize_proof_errors(shell_report.get("errors")))
        route_assertions = _route_assertions(payload)
        report = _app_proof_report(
            ok=not errors,
            prepared=prepared,
            app_payload=payload,
            validation=validation,
            route_assertions=route_assertions,
            screen_reports=screen_reports,
            errors=_normalize_proof_errors(errors),
            timings=timings,
            strict_design=strict_design,
            shell=shell_report,
            resource_binding_report=binding_report,
        )
        return _write_app_proof(report, prepared)
    except AppBundleProofFailure as exc:
        report = _app_proof_failure_report(
            output_dir=output_dir,
            report_path=report_path,
            errors=[{"code": exc.code, "message": exc.message, "fix": exc.fix}],
            timings=timings,
            validation=None,
            write=_should_write_app_proof_failure(output_dir, exc.code),
        )
        if _should_write_app_proof_failure(output_dir, exc.code):
            prepared = _PreparedAppProof(output_dir, Path(app_path), None, report_path, output_dir / APP_BUNDLE_DEFAULT_SUMMARY, output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE)
            return _write_app_proof(report, prepared)
        return report
    except Exception as exc:
        report = _app_proof_failure_report(
            output_dir=output_dir,
            report_path=report_path,
            errors=[
                {
                    "code": "APP_PROOF_INTERNAL_ERROR",
                    "message": str(exc),
                    "fix": "Fix the local AppBundle proof environment or paths and retry prove-app.",
                }
            ],
            timings=timings,
            validation=None,
            write=output_dir.exists(),
        )
        if output_dir.exists():
            prepared = _PreparedAppProof(output_dir, Path(app_path), None, report_path, output_dir / APP_BUNDLE_DEFAULT_SUMMARY, output_dir / APP_BUNDLE_DEFAULT_SUPPORT_BUNDLE)
            return _write_app_proof(report, prepared)
        return report


def compile_app(
    app_path: str | Path,
    *,
    out_dir: str | Path = APP_SHELL_DEFAULT_OUT,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    force: bool = False,
    target: str = APP_SHELL_TARGET,
    cwd: str | Path | None = None,
    _generate_reducer: Any = generate_typescript_reducer,
    _check_conformance: Any = check_reducer_conformance,
    _build_manifest: Any = state_manifest,
) -> dict[str, Any]:
    timings: dict[str, int] = {}
    root = resolve_cwd(cwd)
    output_dir = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=True)
    if target != APP_SHELL_TARGET:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[
                {
                    "code": "APP_SHELL_TARGET_UNSUPPORTED",
                    "message": f"Static Shell V0 supports {APP_SHELL_TARGET} only.",
                    "fix": "Use --target html-tailwind-app.",
                }
            ],
            timings=timings,
            validation=None,
        )
    try:
        source = resolve_local_path(app_path, cwd=root, allow_outside_cwd=True, must_exist=True)
        design = (
            resolve_local_path(design_path, cwd=root, allow_outside_cwd=True, must_exist=True)
            if design_path is not None
            else None
        )
        app_text = source.read_text(encoding="utf-8")
        validation = _time_phase(timings, "validate_app", lambda: validate_app_text(app_text, compile_check=True))
        if not validation["ok"]:
            return _app_shell_failure_report(
                output_dir=output_dir,
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Fix the AppBundle and retry compile-app.",
                    }
                    for issue in validation["issues"]
                ],
                timings=timings,
                validation=validation,
            )
        payload = json.loads(app_text, parse_constant=_reject_json_constant)
        prepared_shell = _PreparedAppShell(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            manifest_path=output_dir / APP_SHELL_MANIFEST,
            diagnostics_path=output_dir / APP_SHELL_DIAGNOSTICS,
            index_path=output_dir / APP_SHELL_INDEX,
        )
        _prepare_app_shell_output_dir(output_dir, root=root, force=force, raw_out=out_dir)
        prepared_proof = _PreparedAppProof(
            output_dir=output_dir,
            app_path=source,
            design_path=design,
            report_path=output_dir / "unused_app_proof_report.json",
            summary_path=output_dir / "unused_APP_PROOF.md",
            support_path=output_dir / "unused_support_bundle.json",
        )
        screen_reports = _time_phase(
            timings,
            "screens",
            lambda: _prove_app_screens(
                payload,
                prepared_proof.output_dir,
                design_path=prepared_proof.design_path,
                root=root,
                strict_design=strict_design,
                target=APP_BUNDLE_TARGET,
            ),
        )
        errors = [error for screen in screen_reports for error in screen.get("errors", []) if isinstance(error, dict)]
        binding_report: dict[str, Any] | None = None
        if not errors:
            binding_report = _time_phase(timings, "resource_binding", lambda: _resource_binding_assertion_report(payload, screen_reports))
            if isinstance(binding_report, dict) and not binding_report.get("ok"):
                errors.extend(_normalize_proof_errors(binding_report.get("errors")))
        if errors:
            return _app_shell_report(
                ok=False,
                prepared=prepared_shell,
                app_payload=payload,
                validation=validation,
                screen_reports=screen_reports,
                errors=_normalize_proof_errors(errors),
                timings=timings,
                strict_design=strict_design,
                shell_payload=None,
                resource_binding_report=binding_report,
            )
        shell_report = _time_phase(
            timings,
            "shell",
            lambda: _write_static_app_shell(
                payload,
                screen_reports,
                prepared_shell,
                root=root,
                force=False,
                raw_out=out_dir,
                strict_design=strict_design,
                validation=validation,
                clean_output=False,
                resource_binding_report=binding_report,
                generate_reducer=_generate_reducer,
                check_conformance=_check_conformance,
                build_manifest=_build_manifest,
            ),
        )
        if not shell_report.get("ok"):
            return _app_shell_report(
                ok=False,
                prepared=prepared_shell,
                app_payload=payload,
                validation=validation,
                screen_reports=screen_reports,
                errors=_normalize_proof_errors(shell_report.get("errors")),
                timings=timings,
                strict_design=strict_design,
                shell_payload=shell_report,
                resource_binding_report=binding_report,
            )
        return _app_shell_report(
            ok=True,
            prepared=prepared_shell,
            app_payload=payload,
            validation=validation,
            screen_reports=screen_reports,
            errors=[],
            timings=timings,
            strict_design=strict_design,
            shell_payload=shell_report,
            resource_binding_report=binding_report,
        )
    except AppBundleProofFailure as exc:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[{"code": exc.code, "message": exc.message, "fix": exc.fix}],
            timings=timings,
            validation=None,
        )
    except Exception as exc:
        return _app_shell_failure_report(
            output_dir=output_dir,
            errors=[
                {
                    "code": "APP_SHELL_INTERNAL_ERROR",
                    "message": str(exc),
                    "fix": "Fix the local AppBundle shell environment or paths and retry compile-app.",
                }
            ],
            timings=timings,
            validation=None,
        )

def _time_phase(timings: dict[str, int], phase: str, fn: Any) -> Any:
    started = time.perf_counter()
    try:
        return fn()
    finally:
        timings[phase] = timings.get(phase, 0) + int((time.perf_counter() - started) * 1000)


__all__ = ["compile_app", "init_app_file", "prove_app"]
