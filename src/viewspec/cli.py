"""Command-line interface for the local ViewSpec SDK."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.api import check, source_kind
from viewspec.api import prove as prove_source
from viewspec.app_bundle import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_SHELL_TARGET,
    REACT_APP_TARGET,
    compile_app,
    init_app_file,
    validate_app_file,
)
from viewspec.compiler import compile as compile_intent
from viewspec.compiler_errors import LocalToolContractError
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.intent_tools import (
    INTENT_COMPILE_TARGETS,
    STARTER_INTENT_KINDS,
    init_intent_file,
    validate_intent_text,
    wrap_intent_bundle_manifest,
)
from viewspec.local_tools import (
    ensure_no_input_overwrite,
    init_design_file,
    source_hash,
    tool_error_response,
    tool_response,
)
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency, run_mcp_server
from viewspec.raw_html import HtmlInputError, compile_html, write_html_compile_result
from viewspec.studio import STUDIO_DEFAULT_PORT, Studio, open_studio
from viewspec.types import IntentBundle
from viewspec.verification import VerificationPlan

INIT_DEFAULT_OUT = {"intent": "viewspec.intent.json", "app": "viewspec.app.json", "design": "DESIGN.md"}
COMPILE_TARGETS = (*INTENT_COMPILE_TARGETS, APP_SHELL_TARGET, REACT_APP_TARGET)
PROVE_TARGETS = (*INTENT_COMPILE_TARGETS, REACT_APP_TARGET)


@dataclass(frozen=True)
class _JsonFailureDefaults:
    """How one subcommand reports a raised failure under --json."""

    code: str
    fix: str
    to_stderr: bool = False


# With --json every raised failure is a standard tool envelope (`ok: false`,
# `errors: [{code, message, fix}]`) instead of stderr text, with the same exit code as human mode.
# The code and fix here apply when the exception carries none of its own.
_JSON_FAILURE_DEFAULTS: dict[str, _JsonFailureDefaults] = {
    "init": _JsonFailureDefaults("IO_ERROR", "Choose a writable path, or pass --force to replace the file."),
    "compile": _JsonFailureDefaults(
        "COMPILE_FAILED", "Fix the input, DESIGN.md, or path issue and rerun viewspec compile."
    ),
    "check": _JsonFailureDefaults("CHECK_FAILED", "Fix the reported path and rerun viewspec check."),
    "prove": _JsonFailureDefaults("PROVE_INVALID_REQUEST", "Fix the reported option and rerun viewspec prove."),
    "studio": _JsonFailureDefaults("STUDIO_START_FAILED", "Fix the reported problem and rerun viewspec studio."),
    # stdout belongs to the MCP protocol.
    "mcp": _JsonFailureDefaults(
        "MCP_SERVER_FAILED", "Fix the reported problem and restart viewspec mcp.", to_stderr=True
    ),
}


@dataclass(frozen=True)
class _CliFailure:
    """One raised failure: its exit code, its human stderr text, and its JSON error fields."""

    exit_code: int
    text: str
    message: str
    code: str | None = None
    fix: str | None = None
    diagnostics: tuple[dict[str, str], ...] = ()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        failure = _cli_failure(exc)
        defaults = _JSON_FAILURE_DEFAULTS.get(args.command) if getattr(args, "json", False) else None
        if defaults is None:
            print(failure.text, file=sys.stderr)
        else:
            _print_json_failure(failure, defaults)
        return failure.exit_code


def _cli_failure(exc: Exception) -> _CliFailure:
    """Classify a raised exception; exit codes: 2 for input or contract errors, 1 for internal ones."""
    if isinstance(exc, DesignSystemError):
        return _CliFailure(
            2,
            "\n".join(_design_error_lines(exc)),
            _failure_message(exc),
            fix="Fix DESIGN.md and retry.",
            diagnostics=tuple(finding.to_json() for finding in exc.report.findings) if exc.report is not None else (),
        )
    if isinstance(exc, HtmlInputError):
        return _CliFailure(2, f"error: {exc.code}: {exc}", _failure_message(exc), code=exc.code)
    code = getattr(exc, "code", None)
    fix = getattr(exc, "fix", None)
    message = getattr(exc, "message", None)
    if isinstance(exc, (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError)):
        exit_code, text = 2, f"error: {exc}"
    else:
        exit_code, text = 1, f"internal error: {exc}"
    return _CliFailure(
        exit_code,
        text,
        # Coded exceptions such as StableCodeError format str(exc) as "CODE: message" and expose the
        # bare text as .message; the code has its own JSON field, so it is not repeated in the message.
        message if isinstance(code, str) and isinstance(message, str) and message else _failure_message(exc),
        code=code if isinstance(code, str) else None,
        fix=fix if isinstance(fix, str) else None,
    )


def _failure_message(exc: BaseException) -> str:
    """The JSON error message for ``exc``: text that names what failed, never an empty string.

    An exception group stands for its first leaf exception. anyio wraps an error that stops the
    MCP server, such as a BrokenPipeError when the client goes away, in a group whose own text,
    "unhandled errors in a TaskGroup (1 sub-exception)", names nothing.
    """
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return str(exc) or type(exc).__name__


def _print_json_failure(failure: _CliFailure, defaults: _JsonFailureDefaults) -> None:
    payload = tool_error_response(
        failure.code or defaults.code,
        failure.message,
        failure.fix or defaults.fix,
        diagnostics=failure.diagnostics,
        metadata=_cli_metadata(),
    )
    if defaults.to_stderr:
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    else:
        _print_json(payload)


def _cli_metadata(**extra: object) -> dict[str, object]:
    return {"sdk_version": __version__, "network_calls": "none", **extra}


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="viewspec", description="Local ViewSpec SDK tools.")
    parser.add_argument("--version", action="version", version=f"viewspec {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Write a starter IntentBundle, AppBundle, or DESIGN.md.")
    init_parser.add_argument(
        "what", nargs="?", default="intent", choices=tuple(INIT_DEFAULT_OUT), help="What to write (default: intent)."
    )
    init_parser.add_argument("--out", help="Output path (default: viewspec.intent.json, viewspec.app.json, DESIGN.md).")
    init_parser.add_argument(
        "--kind",
        choices=(*STARTER_INTENT_KINDS, *APP_BUNDLE_ALLOWED_KINDS),
        help="Starter kind: a motif for intent (default: dashboard), an app kind for app (default: internal_tool).",
    )
    init_parser.add_argument(
        "--resource-binding",
        default="unbound-v0",
        choices=("unbound-v0", "fixture-readonly-v0"),
        help="App resource binding mode. fixture-readonly-v0 writes schema_version 2.",
    )
    init_parser.add_argument(
        "--template",
        default="contract",
        choices=("contract", "react-app"),
        help="App starter shape. react-app writes the runnable AppBundle V4 golden path.",
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    init_parser.set_defaults(func=_init_command)

    compile_parser = subparsers.add_parser(
        "compile", help="Compile an IntentBundle, AppBundle, or imported raw HTML locally."
    )
    compile_parser.add_argument("input", help="Input .json bundle, imported .html/.htm file, or '-' for stdin.")
    compile_parser.add_argument("--out", required=True, help="Output directory.")
    compile_parser.add_argument(
        "--target",
        choices=COMPILE_TARGETS,
        help=f"Renderer target (default: html-tailwind, or {APP_SHELL_TARGET} for an AppBundle).",
    )
    compile_parser.add_argument("--design", help="Optional DESIGN.md file to apply locally.")
    compile_parser.add_argument(
        "--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors."
    )
    compile_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing AppBundle output directory after safety checks.",
    )
    compile_parser.add_argument("--stdin-format", choices=("html", "json"), help="Required when input is '-'.")
    compile_parser.add_argument("--title", help="Optional HTML document title for raw HTML input.")
    compile_parser.add_argument("--lift-json", action="store_true", help="Also write lift.json for raw HTML input.")
    compile_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    compile_parser.set_defaults(func=_compile_command)

    check_parser = subparsers.add_parser(
        "check", help="Run fast static, provenance, and security checks on a source file or artifact directory."
    )
    check_parser.add_argument("path", help="IntentBundle or AppBundle .json file, or a compiled artifact directory.")
    check_parser.add_argument(
        "--no-compile-check",
        action="store_true",
        help="For a source file, skip local compiler support validation.",
    )
    check_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    check_parser.set_defaults(func=_check_command)

    prove_parser = subparsers.add_parser(
        "prove", help="Compile and verify a source, or browser-verify a compiled artifact, and write a proof bundle."
    )
    prove_parser.add_argument(
        "source",
        nargs="?",
        help="IntentBundle or AppBundle .json file, or a compiled artifact directory (default: a starter).",
    )
    prove_parser.add_argument("--out", help="Proof workspace output directory.")
    prove_parser.add_argument(
        "--target",
        choices=PROVE_TARGETS,
        help="Proof target. react-tailwind-tsx and react-tailwind-app add a browser proof with --install.",
    )
    prove_parser.add_argument("--design", help="Optional DESIGN.md file to apply locally.")
    prove_parser.add_argument(
        "--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors."
    )
    prove_parser.add_argument(
        "--kind", default="dashboard", choices=STARTER_INTENT_KINDS, help="Starter motif kind when SOURCE is omitted."
    )
    prove_parser.add_argument(
        "--install", action="store_true", help="Allow npm ci --ignore-scripts for the browser proof."
    )
    prove_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing proof output directory after safety checks.",
    )
    prove_parser.add_argument("--report-out", help="Optional JSON proof report path.")
    prove_parser.add_argument(
        "--with-shell", action="store_true", help="AppBundle: also write and prove a Static Shell V0 artifact."
    )
    prove_parser.add_argument(
        "--freerange",
        action="store_true",
        help="AppBundle: opt in to the pinned Bun/Freerange proof of runtime-connected numeric helpers.",
    )
    prove_parser.add_argument(
        "--pretext",
        action="store_true",
        help="AppBundle: opt in to pinned Pretext text-layout evidence for compiler-owned native DOM text.",
    )
    prove_parser.add_argument("--plan", help="Artifact directory: optional verification plan JSON file.")
    prove_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    prove_parser.set_defaults(func=_prove_command)

    studio_parser = subparsers.add_parser("studio", help="Serve a live-reloading local preview of a source file.")
    studio_parser.add_argument(
        "source",
        nargs="?",
        help="IntentBundle or AppBundle JSON; auto-detected when exactly one canonical source exists.",
    )
    studio_parser.add_argument("--design", help="Optional DESIGN.md file to watch with the source.")
    studio_parser.add_argument(
        "--port",
        type=int,
        default=STUDIO_DEFAULT_PORT,
        help=f"Unprivileged 127.0.0.1 port (default: {STUDIO_DEFAULT_PORT}).",
    )
    studio_parser.add_argument("--no-open", action="store_true", help="Do not launch a browser.")
    studio_parser.add_argument("--json", action="store_true", help="Print one JSON line once the preview is serving.")
    studio_parser.set_defaults(func=_studio_command)

    mcp_parser = subparsers.add_parser(
        "mcp", help="Serve init, compile, check, prove, and studio to an agent over stdio (needs viewspec[agents])."
    )
    mcp_parser.add_argument("--cwd", default=".", help="Workspace root; tools cannot reach paths outside it.")
    mcp_parser.add_argument(
        "--allow-outside-cwd", action="store_true", help="Allow tools to read and write outside --cwd."
    )
    mcp_parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Report a failure that stops the server, at startup or later, as one line of JSON on stderr, "
            "and keep the MCP SDK's per-request INFO logs off stderr; stdout stays reserved for the MCP protocol."
        ),
    )
    mcp_parser.set_defaults(func=_mcp_command)

    return parser


def _init_command(args: argparse.Namespace) -> int:
    out = args.out or INIT_DEFAULT_OUT[args.what]
    if args.what == "app":
        return _init_app(args, out)
    if args.what == "design":
        return _init_design(args, out)
    return _init_intent(args, out)


def _init_app(args: argparse.Namespace, out: str) -> int:
    kind = args.kind or "internal_tool"
    if kind not in APP_BUNDLE_ALLOWED_KINDS:
        raise ValueError(f"Unknown app kind: {kind}")
    binding_mode = {
        "unbound-v0": APP_BUNDLE_RESOURCE_BINDING,
        "fixture-readonly-v0": APP_BUNDLE_RESOURCE_BINDING_READONLY,
    }[args.resource_binding]
    path = init_app_file(
        out,
        kind=kind,
        force=args.force,
        resource_binding=binding_mode,
        template=args.template,
    )
    if args.json:
        # Report the binding of the bundle that was written, read back from the file: the react-app
        # template writes a fixture_readonly_v0 AppBundle V4 whatever
        # --resource-binding says, so the argument alone can be wrong.
        written = validate_app_file(path, compile_check=False)
        _print_json(
            tool_response(
                True,
                "Wrote starter AppBundle.",
                paths={"app": str(path)},
                next_actions=[
                    "Replace the sample labels and records with real app context.",
                    f"Run viewspec check {path} --json.",
                ],
                metadata=_cli_metadata(
                    kind=kind,
                    template=args.template,
                    resource_binding=written.get("resource_binding"),
                    binding_scope=written.get("binding_scope"),
                ),
            )
        )
    else:
        print(str(path))
    return 0


def _init_design(args: argparse.Namespace, out: str) -> int:
    path = init_design_file(out, force=args.force)
    if args.json:
        _print_json(
            tool_response(
                True,
                "Wrote starter DESIGN.md.",
                paths={"design": str(path)},
                next_actions=[f"Compile with --design {path}."],
                metadata=_cli_metadata(),
            )
        )
    else:
        print(str(path))
    return 0


def _init_intent(args: argparse.Namespace, out: str) -> int:
    kind = args.kind or "dashboard"
    path = init_intent_file(out, kind=kind, force=args.force)
    if args.json:
        _print_json(
            tool_response(
                True,
                "Wrote starter IntentBundle.",
                paths={"intent": str(path)},
                next_actions=[
                    "Replace sample labels and values with real user intent.",
                    f"Run viewspec check {path} --json.",
                ],
                metadata=_cli_metadata(kind=kind),
            )
        )
    else:
        print(str(path))
    return 0


def _compile_command(args: argparse.Namespace) -> int:
    input_format = _input_format(args.input, args.stdin_format)
    if input_format == "json" and args.input != "-" and source_kind(args.input) == "app_bundle":
        return _compile_app(args)
    target = args.target or "html-tailwind"
    if target not in INTENT_COMPILE_TARGETS:
        raise ValueError(f"--target {target} requires an AppBundle source")
    data, source_name, input_path = _read_input(args.input, stdin_format=args.stdin_format)
    out_dir = Path(args.out)

    if input_format == "html":
        if target != "html-tailwind":
            if target == "react-tailwind-tsx":
                raise LocalToolContractError(
                    "TAILWIND_IMPORT_NOT_SUPPORTED", "Raw HTML import only supports --target html-tailwind"
                )
            raise ValueError("Raw HTML import only supports --target html-tailwind")
        ensure_no_input_overwrite(
            input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json")
        )
        design = _load_design(args.design, strict=args.strict_design)
        result = compile_html(
            data,
            design=design,
            title=args.title,
            source_name=source_name,
            command_args=_compile_command_args(args, source_name, target),
        )
        paths = write_html_compile_result(result, out_dir, include_lift=bool(args.lift_json))
        if args.json:
            _print_json(
                tool_response(
                    True,
                    "Compiled imported HTML artifact.",
                    diagnostics=result.diagnostics,
                    external_refs=result.manifest.get("external_refs", []),
                    paths=paths,
                    next_actions=[f"Run viewspec check {out_dir} --json before using the artifact."],
                    metadata=_cli_metadata(input_format="html", target=target),
                )
            )
        else:
            print(json.dumps(paths, indent=2, sort_keys=True))
        return 0

    validation = validate_intent_text(data, compile_check=True)
    if not validation["ok"]:
        if args.json:
            _print_json(_intent_validation_failure_response(validation))
        else:
            _print_intent_validation_failure(validation)
        return 2
    output_names = (
        ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json")
        if target in {"react-tsx", "react-tailwind-tsx"}
        else ("index.html", "provenance_manifest.json", "diagnostics.json")
    )
    ensure_no_input_overwrite(input_path, out_dir, output_names)
    design = _load_design(args.design, strict=args.strict_design)
    payload = json.loads(data)
    bundle = IntentBundle.from_json(payload)
    ast = compile_intent(bundle, design=design, strict_design=args.strict_design)
    if target == "react-tsx":
        paths = ReactTsxEmitter().emit(ast, out_dir)
        artifact_path = Path(paths["tsx"])
        emitter = "react_tsx"
    elif target == "react-tailwind-tsx":
        paths = ReactTailwindTsxEmitter().emit(ast, out_dir)
        artifact_path = Path(paths["tsx"])
        emitter = "react_tailwind_tsx"
    else:
        paths = HtmlTailwindEmitter().emit(ast, out_dir)
        artifact_path = Path(paths["html"])
        emitter = "html_tailwind"
    wrap_intent_bundle_manifest(
        Path(paths["manifest"]),
        source_name=source_name,
        raw_source_hash=source_hash(data),
        design=design,
        command_args=_compile_command_args(args, source_name, target),
        artifact_path=artifact_path,
        emitter=emitter,
    )
    if args.json:
        _print_json(
            tool_response(
                True,
                "Compiled IntentBundle artifact.",
                paths=paths,
                next_actions=[f"Run viewspec check {out_dir} --json before using the artifact."],
                metadata=_cli_metadata(input_format="json", target=target, emitter=emitter),
            )
        )
    else:
        print(json.dumps(paths, indent=2, sort_keys=True))
    return 0


def _compile_app(args: argparse.Namespace) -> int:
    target = args.target or APP_SHELL_TARGET
    if target not in {APP_SHELL_TARGET, REACT_APP_TARGET}:
        raise ValueError(f"--target {target} requires an IntentBundle source")
    payload = compile_app(
        args.input,
        out_dir=args.out,
        design_path=args.design,
        strict_design=args.strict_design,
        force=args.force,
        target=target,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
        print(
            f"ok: target={payload['target']} app={app.get('id')} routes={app.get('route_count')} screens={app.get('screen_count')}"
        )
        artifact_hash = payload.get("shell_artifact_hash") or payload.get("app_artifact_hash")
        print(f"artifact_hash: {artifact_hash}")
    else:
        print("failed: AppBundle compile could not be completed")
        for error in payload["errors"]:
            print(f"{error['code']}: {error['message']}")
    return 0 if payload["ok"] else 2


def _print_intent_validation_failure(payload: dict[str, object]) -> None:
    print("error: IntentBundle validation failed", file=sys.stderr)
    print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)


def _intent_validation_failure_response(validation: dict[str, Any]) -> dict[str, Any]:
    """The compile --json failure for an invalid IntentBundle."""
    return tool_error_response(
        "COMPILE_FAILED",
        "IntentBundle validation failed.",
        "Regenerate the IntentBundle using correction_prompt, then rerun viewspec compile.",
        errors=[
            {
                "code": issue["code"],
                "message": issue["message"],
                "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
            }
            for issue in validation["issues"]
        ],
        data={"validation": validation, "correction_prompt": validation["correction_prompt"]},
        metadata=_cli_metadata(),
    )


def _check_command(args: argparse.Namespace) -> int:
    path = Path(args.path)
    result = check(path, compile_check=not args.no_compile_check)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif path.is_dir():
        print("ok" if result["ok"] else "failed")
        for error in result["errors"]:
            print(f"error: {error}")
        for warning in result["warnings"]:
            print(f"warning: {warning}")
        for line in _check_manifest_summary_lines(result.get("manifest_summary")):
            print(line)
    elif result["ok"]:
        summary = result.get("summary") or {}
        counts = (
            f" screens={summary['screen_count']} routes={summary['route_count']}" if "route_count" in summary else ""
        )
        print(f"ok: compile_check={result['compile_check']}{counts}")
    else:
        print(f"failed: compile_check={result['compile_check']}")
        for issue in result["issues"]:
            print(f"{issue['severity']}: {issue['code']} at {issue['path']}: {issue['message']}")
        if result.get("correction_prompt"):
            print("correction_prompt:")
            print(result["correction_prompt"])
    return 0 if result["ok"] else 2


def _check_manifest_summary_lines(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return []
    if summary.get("available") is not True:
        reason = _cli_summary_value(summary.get("reason", "unknown"))
        return [f"manifest: unavailable ({reason})"]
    lines = [
        "manifest: "
        f"kind={_cli_summary_value(summary.get('kind'))} "
        f"emitter={_cli_summary_value(summary.get('emitter'))} "
        f"artifact={_cli_summary_value(summary.get('artifact_file'))} "
        f"nodes={_cli_summary_value(summary.get('node_count'))}"
    ]
    aesthetic_profile = summary.get("aesthetic_profile")
    if isinstance(aesthetic_profile, str) and aesthetic_profile:
        lines.append(f"aesthetic_profile: {aesthetic_profile}")
    style = summary.get("aesthetic_style")
    if isinstance(style, dict) and style:
        if style.get("available") is False:
            lines.append(
                "aesthetic_style: "
                f"profile={_cli_summary_value(style.get('profile'))} "
                f"unavailable={_cli_summary_value(style.get('reason'))}"
            )
        else:
            lines.append(
                "aesthetic_style: "
                f"profile={_cli_summary_value(style.get('profile'))} "
                f"changed_tokens={_cli_summary_value(style.get('changed_token_count'))} "
                f"categories={_cli_summary_value(style.get('category_count'))} "
                f"declarations={_cli_summary_value(style.get('declaration_count'))}"
            )
    layout = summary.get("aesthetic_layout")
    if isinstance(layout, dict) and layout:
        lines.append("aesthetic_layout:")
        for role in sorted(layout):
            entry = layout.get(role)
            if not isinstance(entry, dict):
                continue
            mixed = " mixed=true" if entry.get("mixed") is True else ""
            facts = []
            if "columns" in entry:
                facts.append(f"columns={_cli_summary_value(entry.get('columns'))}")
            if "layout_emphasis" in entry:
                facts.append(f"layout_emphasis={_cli_summary_value(entry.get('layout_emphasis'))}")
            if "span_columns" in entry:
                facts.append(f"span_columns={_cli_summary_value(entry.get('span_columns'))}")
            facts.append(f"nodes={_cli_summary_value(entry.get('node_count'))}")
            facts.append(f"profile={_cli_summary_value(entry.get('profile'))}")
            lines.append(f"  {role}: {' '.join(facts)}{mixed}")
    return lines


def _cli_summary_value(value: object) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _prove_command(args: argparse.Namespace) -> int:
    source = args.source
    is_artifact = source is not None and Path(source).is_dir()
    is_app = not is_artifact and source is not None and source_kind(source) == "app_bundle"
    if not is_artifact and args.plan:
        raise ValueError("--plan requires a compiled artifact directory")
    plan = None
    if args.plan:
        plan = VerificationPlan.from_json(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    result = prove_source(
        source,
        out_dir=args.out,
        design_path=args.design,
        strict_design=bool(args.strict_design),
        target=args.target,
        kind=args.kind,
        install=bool(args.install),
        force=bool(args.force),
        report_out=args.report_out,
        with_shell=bool(args.with_shell),
        freerange=bool(args.freerange),
        pretext=bool(args.pretext),
        plan=plan,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    if is_artifact:
        return _finish_artifact_proof(result, quiet=args.json)
    if is_app:
        return _finish_app_proof(result, quiet=args.json)
    return _finish_intent_proof(result, quiet=args.json)


def _finish_intent_proof(result: dict[str, Any], *, quiet: bool) -> int:
    if not quiet:
        print("ok" if result["ok"] else "failed")
        print(f"proof_level: {result['proof_level']}")
        for key, value in result.get("paths", {}).items():
            if value:
                print(f"{key}: {value}")
        for error in result["errors"]:
            print(f"error: {error['code']}: {error['message']}")
        for line in _check_manifest_summary_lines(result.get("manifest_summary")):
            print(line)
        for line in _host_report_summary_lines(result.get("host_report")):
            print(line)
    if result["ok"]:
        return 0
    if any(error.get("code") == "PROVE_INTERNAL_ERROR" for error in result["errors"]):
        return 1
    return 2


def _finish_app_proof(result: dict[str, Any], *, quiet: bool) -> int:
    if not quiet:
        print("ok" if result["ok"] else "failed")
        print(f"proof_level: {result['proof_level']}")
        print(f"target: {result['target']}")
        app = result.get("app") or {}
        if isinstance(app, dict):
            print(f"app: {app.get('id')} screens={app.get('screen_count')} routes={app.get('route_count')}")
        for key, value in result.get("paths", {}).items():
            if value:
                print(f"{key}: {value}")
        for error in result["errors"]:
            print(f"error: {error['code']}: {error['message']}")
        static_analysis = result.get("static_analysis")
        if isinstance(static_analysis, dict):
            coverage = static_analysis.get("coverage") if isinstance(static_analysis.get("coverage"), dict) else {}
            print(
                "static_analysis: "
                f"{static_analysis.get('status')} "
                f"coverage={coverage.get('fully_analyzed', coverage.get('analyzed', 0))}/{coverage.get('required', 0)}"
            )
        text_layout = result.get("text_layout")
        if isinstance(text_layout, dict):
            coverage = text_layout.get("coverage") if isinstance(text_layout.get("coverage"), dict) else {}
            print(
                "text_layout: "
                f"{text_layout.get('status')} "
                f"coverage={coverage.get('measured', 0)}/{coverage.get('required', 0)}"
            )
    if result["ok"]:
        return 0
    if any(error.get("code") in {"APP_PROOF_INTERNAL_ERROR", "APP_SHELL_INTERNAL_ERROR"} for error in result["errors"]):
        return 1
    return 2


def _finish_artifact_proof(result: dict[str, Any], *, quiet: bool) -> int:
    if not quiet:
        print(result["status"])
        print(f"verification_id: {result['verification_id']}")
        print(f"evidence_files: {len(result['evidence'])}")
        for diagnostic in result["diagnostics"]:
            location = f" [{diagnostic['viewport']}]" if diagnostic.get("viewport") else ""
            origin = f" {diagnostic['source_ref']}" if diagnostic.get("source_ref") else ""
            print(f"{diagnostic['severity']}: {diagnostic['code']}{location}{origin}: {diagnostic['message']}")
    return {"conformant": 0, "nonconformant": 1, "indeterminate": 2}[result["status"]]


def _host_assertion_summary_lines(assertions: object, requirements: object = None) -> list[str]:
    if not isinstance(assertions, dict):
        return []
    normalized = {
        str(key): int(value)
        for key, value in assertions.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if not normalized or not any(value for value in normalized.values()):
        return []
    lines = ["host_assertions:"]
    for key in sorted(normalized):
        lines.append(f"  {key}: {normalized[key]}")
    if isinstance(requirements, dict):
        normalized_requirements = {
            str(key): int(value)
            for key, value in requirements.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if normalized_requirements:
            lines.append("host_assertion_requirements:")
            for key in sorted(normalized_requirements):
                lines.append(f"  {key}: {normalized_requirements[key]}")
    return lines


def _host_report_summary_lines(host_report: object) -> list[str]:
    if not isinstance(host_report, dict):
        return []
    lines = [f"host_verification: {'passed' if host_report.get('ok') else 'failed'}"]
    lines.extend(
        _host_assertion_summary_lines(host_report.get("assertions"), host_report.get("assertion_requirements"))
    )
    return lines


def _studio_command(args: argparse.Namespace) -> int:
    def announce(studio: Studio) -> None:
        if args.json:
            print(json.dumps({"ok": True, "url": studio.url, "source": str(studio.source), "errors": studio.errors}))
        else:
            print(f"ViewSpec Studio: {studio.url}")
            for error in studio.errors:
                print(f"error: {error}", file=sys.stderr)
        sys.stdout.flush()

    open_studio(args.source, design=args.design, port=args.port, no_open=args.no_open, on_ready=announce)
    return 0


def _mcp_command(args: argparse.Namespace) -> int:
    try:
        # Under --json, keep the SDK's per-request INFO logs off stderr, where a failure that
        # stops the server is reported as one JSON line.
        run_mcp_server(cwd=args.cwd, allow_outside_cwd=args.allow_outside_cwd, log_level="ERROR" if args.json else None)
    except MissingMCPDependency:
        if args.json:
            # stdout belongs to the MCP protocol even when the server never starts.
            payload = tool_error_response(
                "MCP_DEPENDENCY_MISSING",
                "The optional MCP SDK is not installed, or its mcp.server.fastmcp module does not import.",
                MCP_INSTALL_HINT,
                metadata=_cli_metadata(),
            )
            print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        else:
            print(MCP_INSTALL_HINT, file=sys.stderr)
        return 2
    finally:
        _release_broken_stdout()
    return 0


def _release_broken_stdout() -> None:
    """Point stdout at the null device when it is a broken pipe, so the flush at exit cannot fail.

    The MCP server writes the protocol to stdout, so a client that goes away leaves a broken pipe
    that can still hold unwritten bytes. Python flushes them at exit, and on Python 3.14 each failed
    flush adds a BrokenPipeError report to stderr and turns the exit status into 120, which would
    break the one-line --json failure report and the exit code both modes share.
    """
    stdout = sys.stdout
    if stdout is None:
        # fd 1 was closed when Python started, so there is nothing to flush at exit, and the server's
        # own failure must not be replaced by this one.
        return
    try:
        stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError, ValueError):
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, stdout.fileno())
            finally:
                os.close(devnull)
    except (OSError, ValueError):
        # Another write error, or a closed stdout, which Python skips at exit: nothing to release,
        # and the server's own failure must not be replaced by this one.
        return


def _load_design(path: str | None, *, strict: bool) -> DesignSystemContext | None:
    if not path:
        return None
    return load_design_system(path=path, strict=strict)


def _read_input(path_arg: str, *, stdin_format: str | None) -> tuple[str, str | None, Path | None]:
    if path_arg == "-":
        if stdin_format is None:
            raise ValueError("--stdin-format is required when input is '-'")
        return sys.stdin.read(), "<stdin>", None
    path = Path(path_arg)
    return path.read_text(encoding="utf-8"), path.name, path


def _input_format(path_arg: str, stdin_format: str | None) -> str:
    if path_arg == "-":
        if stdin_format is None:
            raise ValueError("--stdin-format is required when input is '-'")
        return stdin_format
    suffix = Path(path_arg).suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json":
        return "json"
    raise ValueError("Input must be .html, .htm, .json, or '-' with --stdin-format")


def _compile_command_args(args: argparse.Namespace, source_name: str | None, target: str) -> list[str]:
    command = ["viewspec", "compile", source_name or "<stdin>"]
    if args.design:
        command.extend(["--design", Path(args.design).name])
    if args.strict_design:
        command.append("--strict-design")
    if target != "html-tailwind":
        command.extend(["--target", target])
    command.extend(["--out", "<out>"])
    if args.stdin_format:
        command.extend(["--stdin-format", args.stdin_format])
    if args.title:
        command.extend(["--title", args.title])
    if args.lift_json:
        command.append("--lift-json")
    return command


def _design_error_lines(exc: DesignSystemError) -> list[str]:
    lines = [f"error: {exc}"]
    if exc.report is None:
        return lines
    summary = exc.report.summary()
    lines.append(
        f"DESIGN.md lint: {summary['errors']} error(s), {summary['warnings']} warning(s), {summary['info']} info"
    )
    lines.extend(
        f"{finding.severity}: {finding.code} at {finding.path}: {finding.message}" for finding in exc.report.findings
    )
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
