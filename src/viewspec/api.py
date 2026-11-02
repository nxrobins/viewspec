"""The public verbs: compile, check and prove, over either bundle kind."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from viewspec.app_bundle import (
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_MAX_BYTES,
    APP_BUNDLE_TARGET,
    APP_SHELL_TARGET,
    AppBundle,
    compile_app,
    prove_app,
    validate_app_file,
)
from viewspec.app_react import REACT_APP_MANIFEST
from viewspec.app_reports import APP_BUNDLE_DEFAULT_REPORT
from viewspec.app_shell import APP_SHELL_INDEX, APP_SHELL_MANIFEST
from viewspec.compiler import compile as compile_intent
from viewspec.design_md import DesignSystemContext
from viewspec.html_verify import HTML_VERIFY_TARGETS, html_artifact_target, load_mcp_render
from viewspec.intent_tools import intent_error_payload, validate_intent_file
from viewspec.local_tools import MCP_APP_TARGET, check_artifact_dir, file_hash, resolve_cwd
from viewspec.local_tools_response import ProveFailure
from viewspec.local_verify import verify_local_artifact
from viewspec.motif_plugins import MotifPlugin, MotifRegistry
from viewspec.prove import PROVE_DEFAULT_OUT
from viewspec.prove import prove as prove_intent
from viewspec.types import ASTBundle, IntentBundle
from viewspec.verification import VerificationPlan

INIT_DEFAULT_OUT = {"intent": "viewspec.intent.json", "app": "viewspec.app.json", "design": "DESIGN.md"}
VERIFY_DEFAULT_OUT = ".viewspec-verification"


def source_kind(path: str | Path) -> str:
    """Return ``"app_bundle"`` or ``"intent_bundle"`` for a JSON source file.

    An unreadable or malformed file falls back to its name, so the validator for that kind can
    report the actual problem.
    """
    is_app_name = Path(path).name.endswith(".app.json")
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read(APP_BUNDLE_MAX_BYTES + 1))
    except (OSError, ValueError, RecursionError):
        return "app_bundle" if is_app_name else "intent_bundle"
    if not isinstance(payload, dict) or {"substrate", "view_spec"} & payload.keys():
        return "intent_bundle"
    return "app_bundle" if is_app_name or {"app", "routes", "screens"} & payload.keys() else "intent_bundle"


def default_proof_dir(source: str | Path | None) -> str:
    """Return where ``prove`` writes for ``source`` when no output directory is given."""
    if source is not None and Path(source).is_dir():
        return VERIFY_DEFAULT_OUT
    if source is not None and source_kind(source) == "app_bundle":
        return APP_BUNDLE_DEFAULT_OUT
    return PROVE_DEFAULT_OUT


def compile(
    bundle: IntentBundle | AppBundle,
    design: DesignSystemContext | str | os.PathLike[str] | None = None,
    *,
    out_dir: str | Path | None = None,
    target: str | None = None,
    strict_design: bool = False,
    force: bool = False,
    motif_plugins: tuple[MotifPlugin, ...] = (),
    motif_registry: MotifRegistry | None = None,
) -> ASTBundle | dict[str, Any]:
    """Compile a bundle.

    An ``IntentBundle`` compiles in memory to an ``ASTBundle``, with ``design`` read as documented
    on :func:`viewspec.compiler.compile`; ``out_dir``, ``target`` and ``force`` raise ``ValueError``
    because nothing is written. An ``AppBundle`` is a multi-file build: it writes to ``out_dir``,
    reads ``design`` as a DESIGN.md path, and returns the build report.
    """
    if not isinstance(bundle, AppBundle):
        if out_dir is not None or target is not None or force:
            raise ValueError("An IntentBundle compiles in memory; out_dir, target and force apply to an AppBundle.")
        return compile_intent(
            bundle,
            design,
            strict_design=strict_design,
            motif_plugins=motif_plugins,
            motif_registry=motif_registry,
        )
    if out_dir is None:
        raise ValueError("Compiling an AppBundle writes files; pass out_dir.")
    if isinstance(design, DesignSystemContext):
        raise TypeError("AppBundle compilation reads DESIGN.md itself; pass its path as design.")
    with tempfile.TemporaryDirectory(prefix="viewspec-app-") as scratch:
        source = Path(scratch) / "viewspec.app.json"
        source.write_text(json.dumps(bundle.to_json(), sort_keys=True), encoding="utf-8")
        return compile_app(
            source,
            out_dir=Path(out_dir).resolve(),
            design_path=Path(design).resolve() if design is not None else None,
            strict_design=strict_design,
            force=force,
            target=target or APP_SHELL_TARGET,
        )


def check(path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    """Run the fast local checks on a source file or a compiled artifact directory.

    A directory gets the provenance and security checks. A compiled AppBundle gets them screen by
    screen, and its other files are compared with the hashes in its manifest. A JSON file gets the
    contract checks for its bundle kind. ``report["ok"]`` carries the verdict; read failures are
    reported, not raised.
    """
    target = Path(path)
    if target.is_dir():
        if (target / "provenance_manifest.json").exists() or not (target / "screens").is_dir():
            return check_artifact_dir(target)
        return _check_app_artifact(target)
    if source_kind(target) == "app_bundle":
        return _check_app(target, compile_check)
    return _check_intent(target, compile_check)


def prove(
    source: str | Path | None = None,
    *,
    out_dir: str | Path | None = None,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str | None = None,
    kind: str = "dashboard",
    install: bool = False,
    force: bool = False,
    report_out: str | Path | None = None,
    with_shell: bool = False,
    freerange: bool = False,
    pretext: bool = False,
    plan: VerificationPlan | None = None,
    renders: Sequence[Any] = (),
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Prove a source or a compiled artifact and return the report.

    With no ``source``, proves a starter of ``kind``. An IntentBundle or AppBundle file compiles
    and proves that bundle. ``with_shell`` and ``freerange`` apply to AppBundles. ``pretext`` applies
    to an AppBundle, and to the html-tailwind and mcp-app targets of an IntentBundle or a compiled
    directory. A directory verifies an already compiled artifact in Chromium across the canonical
    viewports, following ``plan``. For html-tailwind and mcp-app, an IntentBundle stays a fast
    source proof unless ``install``, ``pretext`` or ``renders`` asks for that browser run.
    ``renders`` holds ``app.render()`` results, or paths to them as JSON, for an MCP App to swap in.
    ``cwd`` is the workspace: relative paths start there, and ``force`` will not replace it. It
    defaults to the process working directory.
    """
    if cwd is not None:
        root = resolve_cwd(cwd)
        source = root / source if source is not None else None
        out_dir = root / (out_dir or default_proof_dir(source))
        report_out = root / report_out if report_out is not None else None
    output = out_dir or default_proof_dir(source)
    directory = Path(source) if source is not None and Path(source).is_dir() else None
    is_app = source is not None and directory is None and source_kind(source) == "app_bundle"
    if not is_app:
        if with_shell:
            raise ValueError("with_shell requires an AppBundle source")
        if freerange:
            proved = html_artifact_target(directory) if directory else target or "html-tailwind"
            raise ProveFailure(
                "APP_FREERANGE_TARGET_UNSUPPORTED",
                f"Freerange proofs require an AppBundle source: {proved or 'this artifact'} emits no TypeScript "
                "numeric reducer kernel.",
                "Remove --freerange, or prove an AppBundle with --target react-tailwind-app.",
            )
    elif renders:
        raise ValueError("renders require an IntentBundle or a compiled mcp-app source")
    if not is_app and not directory and pretext and (target or "html-tailwind") not in HTML_VERIFY_TARGETS:
        raise ProveFailure(
            "APP_PRETEXT_TARGET_UNSUPPORTED",
            f"--pretext is not supported on {target}.",
            "Use html-tailwind, mcp-app, or an AppBundle with --target react-tailwind-app.",
        )
    if not is_app and not directory and renders and target != MCP_APP_TARGET:
        raise ProveFailure(
            "PROVE_RENDER_INVALID",
            "--render applies to the mcp-app target.",
            "Pass --target mcp-app, or remove --render.",
        )
    if directory:
        evidence = Path(output) / "evidence"
        # The host report marks a directory as ours, so --force never deletes anything else.
        if force and (evidence / "host-report.json").is_file():
            shutil.rmtree(evidence)
        result = verify_local_artifact(
            directory,
            plan=plan,
            evidence_dir=evidence,
            report_out=report_out or Path(output) / "result.json",
            install=install,
            pretext=pretext,
            renders=tuple(load_mcp_render(render) for render in renders),
        )
        return result.to_json()
    if source is not None and is_app:
        return prove_app(
            app_path=source,
            out_dir=output,
            design_path=design_path,
            strict_design=strict_design,
            force=force,
            report_out=report_out,
            with_shell=with_shell,
            target=target or APP_BUNDLE_TARGET,
            install=install,
            freerange=freerange,
            pretext=pretext,
            cwd=cwd,
        )
    return prove_intent(
        intent_path=source,
        out_dir=output,
        design_path=design_path,
        strict_design=strict_design,
        target=target or "html-tailwind",
        kind=kind,
        install=install,
        force=force,
        report_out=report_out,
        pretext=pretext,
        renders=renders,
        cwd=cwd,
    )


def _check_app_artifact(root: Path) -> dict[str, Any]:
    """Check every compiled screen of an AppBundle output; the manifests live one level down."""
    screens = {path.parent.name: check_artifact_dir(path) for path in sorted(root.glob("screens/*/artifact"))}
    errors = [f"{screen}: {error}" for screen, report in screens.items() for error in report["errors"]]
    warnings = [f"{screen}: {warning}" for screen, report in screens.items() for warning in report["warnings"]]
    if screens:
        errors.extend(_app_file_errors(root))
    else:
        errors.append("no compiled screens under screens/*/artifact")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "screens": screens}


def _app_file_errors(root: Path) -> list[str]:
    """Compare the files above the screens with the hashes the compile recorded for them."""
    try:
        if (root / APP_SHELL_MANIFEST).is_file():
            shell = json.loads((root / APP_SHELL_MANIFEST).read_text(encoding="utf-8"))
            recorded = [{"path": APP_SHELL_INDEX, "sha256": shell["shell_artifact_hash"]}]
        elif (root / REACT_APP_MANIFEST).is_file():
            react = json.loads((root / REACT_APP_MANIFEST).read_text(encoding="utf-8"))
            views = [{"path": view["tsx"], "sha256": view["artifact_hash"]} for view in react["screen_artifacts"]]
            recorded = [*react["files"], *views]
        elif (root / APP_BUNDLE_DEFAULT_REPORT).is_file():
            return []  # A proof directory holds only the screens.
        else:
            return [f"missing {APP_SHELL_MANIFEST} or {REACT_APP_MANIFEST}"]
        return [
            f"{entry['path']}: does not match the hash in its manifest"
            for entry in recorded
            if file_hash(root / entry["path"]) != entry["sha256"]
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"could not verify the compiled files: {exc!r}"]


def _check_intent(path: Path, compile_check: bool) -> dict[str, Any]:
    try:
        return validate_intent_file(path, compile_check=compile_check)
    except FileNotFoundError:
        return intent_error_payload(
            "INTENT_FILE_NOT_FOUND",
            f"Intent file not found: {path}",
            "Create viewspec.intent.json or pass the correct intent file path.",
            compile_check=compile_check,
        )
    except OSError as exc:
        return intent_error_payload(
            "INTENT_FILE_READ_ERROR",
            f"Could not read intent file {path}: {exc}",
            "Use a readable local IntentBundle JSON file.",
            compile_check=compile_check,
        )


def _check_app(path: Path, compile_check: bool) -> dict[str, Any]:
    try:
        return validate_app_file(path, compile_check=compile_check)
    except FileNotFoundError:
        return _app_read_failure(
            "APP_FILE_NOT_FOUND",
            f"AppBundle file not found: {path}",
            "Create viewspec.app.json or pass the correct AppBundle file path.",
            compile_check,
        )
    except OSError as exc:
        return _app_read_failure(
            "APP_FILE_READ_ERROR",
            f"Could not read AppBundle file {path}: {exc}",
            "Use a readable local AppBundle JSON file.",
            compile_check,
        )


def _app_read_failure(code: str, message: str, suggestion: str, compile_check: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": False,
        "compile_check": "failed" if compile_check else "skipped",
        "resource_binding": "unknown",
        "summary": None,
        "route_assertions": None,
        "raw_bytes": 0,
        "limits": {},
        "issues": [{"severity": "error", "code": code, "path": "$", "message": message, "suggestion": suggestion}],
    }


__all__ = ["INIT_DEFAULT_OUT", "VERIFY_DEFAULT_OUT", "check", "compile", "default_proof_dir", "prove", "source_kind"]
