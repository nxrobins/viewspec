"""Command-line interface for the local ViewSpec SDK."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from viewspec._version import __version__
from viewspec.app_bundle import (
    APP_BUNDLE_ALLOWED_KINDS,
    APP_BUNDLE_DEFAULT_OUT,
    APP_BUNDLE_RESOURCE_BINDING,
    APP_BUNDLE_RESOURCE_BINDING_READONLY,
    APP_SHELL_DEFAULT_OUT,
    APP_SHELL_TARGET,
    app_semantic_change_lines,
    compile_app,
    diff_app_files,
    diff_app_text,
    init_app_file,
    prove_app,
    starter_app_bundle,
    validate_app_file,
    validate_app_text,
)
from viewspec.agent_assets import AgentAssetError, agent_asset_readiness, check_agent_assets, export_agent_assets
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.host_verify import HOST_VERIFY_TARGET, verify_host_artifact_dir, verify_host_intent_file
from viewspec.intent_tools import (
    STARTER_INTENT_KINDS,
    diff_intent_files,
    diff_intent_text,
    init_intent_file,
    intent_semantic_change_lines,
    intent_diff_error_payload,
    intent_error_payload,
    starter_intent_bundle,
    validate_intent_file,
    validate_intent_text,
    wrap_intent_bundle_manifest,
)
from viewspec.local_tools import (
    atomic_write,
    check_artifact_dir,
    ensure_no_input_overwrite,
    init_design_file,
    source_hash,
)
from viewspec.prove import PROVE_DEFAULT_OUT, prove
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency, mcp_dependency_available, run_mcp_server
from viewspec.native_agents import NativeAgentError, VALID_TARGETS, init_agent_instructions
from viewspec.raw_html import HtmlInputError, compile_html, diff_html, lift_html, write_html_compile_result
from viewspec.types import IntentBundle

DoctorProfileDiff = tuple[dict[str, object], str, dict[str, object], dict[str, object]]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except DesignSystemError as exc:
        _print_design_error(exc)
        return 2
    except HtmlInputError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 2
    except AgentAssetError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 2
    except NativeAgentError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="viewspec", description="Local ViewSpec SDK tools.")
    parser.add_argument("--version", action="version", version=f"viewspec {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-intent", help="Validate a ViewSpec IntentBundle JSON file.")
    validate_parser.add_argument("input", help="Input strict IntentBundle .json file.")
    validate_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    validate_parser.add_argument(
        "--no-compile-check",
        action="store_true",
        help="Skip local compiler support validation and only validate the agent contract.",
    )
    validate_parser.set_defaults(func=_validate_intent_command)

    compile_parser = subparsers.add_parser("compile", help="Compile an IntentBundle JSON or imported raw HTML locally.")
    compile_parser.add_argument("input", help="Input .json IntentBundle, imported .html/.htm file, or '-' for stdin.")
    compile_parser.add_argument("--design", help="Optional DESIGN.md file to apply locally.")
    compile_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    compile_parser.add_argument("--out", required=True, help="Output directory.")
    compile_parser.add_argument(
        "--target",
        default="html-tailwind",
        choices=("html-tailwind", "react-tsx", "react-tailwind-tsx"),
        help="Renderer target for IntentBundle JSON input. Raw HTML import supports html-tailwind only.",
    )
    compile_parser.add_argument("--stdin-format", choices=("html", "json"), help="Required when input is '-'.")
    compile_parser.add_argument("--title", help="Optional HTML document title for raw HTML input.")
    compile_parser.add_argument("--lift-json", action="store_true", help="Also write lift.json for raw HTML input.")
    compile_parser.set_defaults(func=_compile_command)

    lift_parser = subparsers.add_parser("lift", help="Lift raw HTML into local semantic signals.")
    lift_parser.add_argument("input", help="Input .html/.htm file, or '-' for stdin.")
    lift_parser.add_argument("--out", required=True, help="Output lift.json file.")
    lift_parser.add_argument("--stdin-format", choices=("html",), help="Required when input is '-'.")
    lift_parser.set_defaults(func=_lift_command)

    diff_parser = subparsers.add_parser("diff", help="Diff two raw HTML files semantically.")
    diff_parser.add_argument("left", help="Old/left HTML file.")
    diff_parser.add_argument("right", help="New/right HTML file.")
    diff_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    diff_parser.set_defaults(func=_diff_command)

    diff_intent_parser = subparsers.add_parser("diff-intent", help="Diff two ViewSpec IntentBundle JSON files semantically.")
    diff_intent_parser.add_argument("left", help="Old/left IntentBundle .json file.")
    diff_intent_parser.add_argument("right", help="New/right IntentBundle .json file.")
    diff_intent_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    diff_intent_parser.add_argument(
        "--no-compile-check",
        action="store_true",
        help="Skip local compiler support validation and only validate the agent contract before diffing.",
    )
    diff_intent_parser.set_defaults(func=_diff_intent_command)

    init_app_parser = subparsers.add_parser("init-app", help="Write a starter local AppBundle JSON file.")
    init_app_parser.add_argument("--out", default="viewspec.app.json", help="Output AppBundle JSON path.")
    init_app_parser.add_argument("--kind", default="internal_tool", choices=APP_BUNDLE_ALLOWED_KINDS, help="Starter app kind.")
    init_app_parser.add_argument(
        "--resource-binding",
        default="unbound-v0",
        choices=("unbound-v0", "fixture-readonly-v0"),
        help="Starter resource binding mode. fixture-readonly-v0 writes schema_version 2.",
    )
    init_app_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_app_parser.set_defaults(func=_init_app_command)

    validate_app_parser = subparsers.add_parser("validate-app", help="Validate a local AppBundle JSON file.")
    validate_app_parser.add_argument("input", help="Input strict AppBundle .json file.")
    validate_app_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    validate_app_parser.add_argument(
        "--no-compile-check",
        action="store_true",
        help="Skip local compiler support validation for embedded screen intents.",
    )
    validate_app_parser.set_defaults(func=_validate_app_command)

    diff_app_parser = subparsers.add_parser("diff-app", help="Diff two local AppBundle JSON files semantically.")
    diff_app_parser.add_argument("left", help="Old/left AppBundle .json file.")
    diff_app_parser.add_argument("right", help="New/right AppBundle .json file.")
    diff_app_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    diff_app_parser.add_argument(
        "--no-compile-check",
        action="store_true",
        help="Skip local compiler support validation for changed embedded screen intents.",
    )
    diff_app_parser.set_defaults(func=_diff_app_command)

    compile_app_parser = subparsers.add_parser("compile-app", help="Compile a local AppBundle into a Static Shell V0 artifact.")
    compile_app_parser.add_argument("input", help="Input strict AppBundle .json file.")
    compile_app_parser.add_argument("--out", default=APP_SHELL_DEFAULT_OUT, help="Static app shell output directory.")
    compile_app_parser.add_argument("--design", help="Optional DESIGN.md file to apply to every embedded screen intent.")
    compile_app_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    compile_app_parser.add_argument("--force", action="store_true", help="Replace an existing app shell output directory after safety checks.")
    compile_app_parser.add_argument("--target", default=APP_SHELL_TARGET, choices=(APP_SHELL_TARGET,), help="Static shell target.")
    compile_app_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    compile_app_parser.set_defaults(func=_compile_app_command)

    init_parser = subparsers.add_parser("init-design", help="Write a starter strict DESIGN.md file.")
    init_parser.add_argument("--out", default="DESIGN.md", help="Output DESIGN.md path.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_parser.set_defaults(func=_init_design_command)

    init_intent_parser = subparsers.add_parser("init-intent", help="Write a starter ViewSpec IntentBundle JSON file.")
    init_intent_parser.add_argument("--out", default="viewspec.intent.json", help="Output IntentBundle JSON path.")
    init_intent_parser.add_argument("--kind", default="dashboard", choices=STARTER_INTENT_KINDS, help="Starter motif kind.")
    init_intent_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_intent_parser.set_defaults(func=_init_intent_command)

    doctor_parser = subparsers.add_parser("doctor", help="Check local ViewSpec SDK readiness.")
    doctor_parser.add_argument("--agents", action="store_true", help="Also check native agent integration readiness.")
    doctor_parser.set_defaults(func=_doctor_command)

    prove_parser = subparsers.add_parser("prove", help="Run a first ViewSpec proof and write a proof bundle.")
    prove_parser.add_argument("--intent", help="Optional existing IntentBundle JSON file.")
    prove_parser.add_argument("--out", default=PROVE_DEFAULT_OUT, help="Proof workspace output directory.")
    prove_parser.add_argument("--design", help="Optional DESIGN.md file to apply locally.")
    prove_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    prove_parser.add_argument(
        "--target",
        default="html-tailwind",
        choices=("html-tailwind", "react-tsx", "react-tailwind-tsx"),
        help="Proof target. html-tailwind is Python-only; react-tailwind-tsx can also run the bounded host proof.",
    )
    prove_parser.add_argument("--kind", default="dashboard", choices=STARTER_INTENT_KINDS, help="Starter motif kind when --intent is omitted.")
    prove_parser.add_argument("--install", action="store_true", help="Allow npm ci --ignore-scripts for react-tailwind-tsx host proof.")
    prove_parser.add_argument("--force", action="store_true", help="Replace an existing proof output directory after safety checks.")
    prove_parser.add_argument("--report-out", help="Optional JSON proof report path. Defaults to <out>/proof_report.json.")
    prove_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    prove_parser.set_defaults(func=_prove_command)

    prove_app_parser = subparsers.add_parser("prove-app", help="Run a local AppBundle proof and write an app proof bundle.")
    prove_app_parser.add_argument("--app", required=True, help="Input AppBundle JSON file.")
    prove_app_parser.add_argument("--out", default=APP_BUNDLE_DEFAULT_OUT, help="App proof workspace output directory.")
    prove_app_parser.add_argument("--design", help="Optional DESIGN.md file to apply to every embedded screen intent.")
    prove_app_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    prove_app_parser.add_argument("--force", action="store_true", help="Replace an existing app proof output directory after safety checks.")
    prove_app_parser.add_argument("--report-out", help="Optional JSON app proof report path. Defaults to <out>/app_proof_report.json.")
    prove_app_parser.add_argument("--with-shell", action="store_true", help="Also write and prove a Static Shell V0 artifact under <out>/app-shell/.")
    prove_app_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    prove_app_parser.set_defaults(func=_prove_app_command)

    check_parser = subparsers.add_parser("check", help="Validate a local ViewSpec artifact directory.")
    check_parser.add_argument("artifact_dir", help="Directory containing provenance_manifest.json and generated artifact output.")
    check_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    check_parser.set_defaults(func=_check_command)

    verify_host_parser = subparsers.add_parser("verify-host", help="Verify a React Tailwind artifact in a bounded host app.")
    verify_host_parser.add_argument("artifact_dir", nargs="?", help="Checked react-tailwind-tsx artifact directory.")
    verify_host_parser.add_argument("--intent", help="IntentBundle JSON file to compile before host verification.")
    verify_host_parser.add_argument("--out", help="Output artifact directory for --intent compile mode.")
    verify_host_parser.add_argument("--design", help="Optional DESIGN.md file to apply in --intent compile mode.")
    verify_host_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    verify_host_parser.add_argument("--target", default=HOST_VERIFY_TARGET, choices=(HOST_VERIFY_TARGET,), help="Host verification target.")
    verify_host_parser.add_argument("--install", action="store_true", help="Run npm ci --ignore-scripts in the isolated host template.")
    verify_host_parser.add_argument("--report-out", help="Optional JSON proof report output path.")
    verify_host_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    verify_host_parser.set_defaults(func=_verify_host_command)

    init_agent_parser = subparsers.add_parser("init-agent", help="Install managed ViewSpec instructions for coding agents.")
    init_agent_parser.add_argument("--target", required=True, choices=VALID_TARGETS, help="Agent instruction target to update.")
    init_agent_parser.add_argument("--root", default=".", help="Repository root for instruction files.")
    init_agent_parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    init_agent_parser.set_defaults(func=_init_agent_command)

    agent_assets_parser = subparsers.add_parser(
        "export-agent-assets",
        help="Export local agent prompt, IntentBundle JSON schema, valid example, and asset manifest files.",
    )
    agent_assets_parser.add_argument("--out", default=".viewspec", help="Output directory for local agent contract assets.")
    agent_assets_parser.add_argument("--force", action="store_true", help="Replace existing generated assets.")
    agent_assets_parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    agent_assets_parser.set_defaults(func=_export_agent_assets_command)

    check_agent_assets_parser = subparsers.add_parser(
        "check-agent-assets",
        help="Verify exported local agent contract assets against the current SDK.",
    )
    check_agent_assets_parser.add_argument("asset_dir", nargs="?", default=".viewspec", help="Agent asset directory to verify.")
    check_agent_assets_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    check_agent_assets_parser.set_defaults(func=_check_agent_assets_command)

    mcp_parser = subparsers.add_parser("mcp", help="Start the optional ViewSpec stdio MCP server.")
    mcp_parser.add_argument("--cwd", default=".", help="MCP path sandbox root.")
    mcp_parser.add_argument("--allow-outside-cwd", action="store_true", help="Allow MCP tools to read/write outside --cwd.")
    mcp_parser.set_defaults(func=_mcp_command)

    return parser


def _compile_command(args: argparse.Namespace) -> int:
    data, source_name, input_path = _read_input(args.input, stdin_format=args.stdin_format)
    input_format = _input_format(args.input, args.stdin_format)
    out_dir = Path(args.out)

    if input_format == "html":
        if args.target != "html-tailwind":
            if args.target == "react-tailwind-tsx":
                raise ValueError("TAILWIND_IMPORT_NOT_SUPPORTED: Raw HTML import only supports --target html-tailwind")
            raise ValueError("Raw HTML import only supports --target html-tailwind")
        ensure_no_input_overwrite(input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json"))
        design = _load_design(args.design, strict=args.strict_design)
        result = compile_html(
            data,
            design=design,
            title=args.title,
            source_name=source_name,
            command_args=_compile_command_args(args, source_name),
        )
        paths = write_html_compile_result(result, out_dir, include_lift=bool(args.lift_json))
        print(json.dumps(paths, indent=2, sort_keys=True))
        return 0

    validation = validate_intent_text(data, compile_check=True)
    if not validation["ok"]:
        _print_intent_validation_failure(validation)
        return 2
    output_names = (
        ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json")
        if args.target in {"react-tsx", "react-tailwind-tsx"}
        else ("index.html", "provenance_manifest.json", "diagnostics.json")
    )
    ensure_no_input_overwrite(input_path, out_dir, output_names)
    design = _load_design(args.design, strict=args.strict_design)
    payload = json.loads(data)
    bundle = IntentBundle.from_json(payload)
    ast = compile(bundle, design=design, strict_design=args.strict_design)
    if args.target == "react-tsx":
        paths = ReactTsxEmitter().emit(ast, out_dir)
        artifact_path = Path(paths["tsx"])
        emitter = "react_tsx"
    elif args.target == "react-tailwind-tsx":
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
        command_args=_compile_command_args(args, source_name),
        artifact_path=artifact_path,
        emitter=emitter,
    )
    print(json.dumps(paths, indent=2, sort_keys=True))
    return 0


def _validate_intent_command(args: argparse.Namespace) -> int:
    compile_check = not args.no_compile_check
    try:
        payload = validate_intent_file(args.input, compile_check=compile_check)
    except FileNotFoundError:
        payload = intent_error_payload(
            "INTENT_FILE_NOT_FOUND",
            f"Intent file not found: {args.input}",
            "Create viewspec.intent.json or pass the correct intent file path.",
            compile_check=compile_check,
        )
    except OSError as exc:
        payload = intent_error_payload(
            "INTENT_FILE_READ_ERROR",
            f"Could not read intent file {args.input}: {exc}",
            "Use a readable local IntentBundle JSON file.",
            compile_check=compile_check,
        )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print(f"ok: compile_check={payload['compile_check']}")
    else:
        print(f"failed: compile_check={payload['compile_check']}")
        for issue in payload["issues"]:
            print(f"{issue['severity']}: {issue['code']} at {issue['path']}: {issue['message']}")
        if payload["correction_prompt"]:
            print("correction_prompt:")
            print(payload["correction_prompt"])
    return 0 if payload["ok"] else 2


def _print_intent_validation_failure(payload: dict[str, object]) -> None:
    print("error: IntentBundle validation failed", file=sys.stderr)
    print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)


def _lift_command(args: argparse.Namespace) -> int:
    data, source_name, input_path = _read_input(args.input, stdin_format=args.stdin_format)
    output_path = Path(args.out)
    if input_path is not None and input_path.resolve() == output_path.resolve():
        raise ValueError("Refusing to overwrite input file")
    result = lift_html(data, source_name=source_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output_path, json.dumps(result.to_json(), indent=2, sort_keys=True))
    print(str(output_path))
    return 0


def _diff_command(args: argparse.Namespace) -> int:
    left = Path(args.left)
    right = Path(args.right)
    try:
        left_html = left.read_text(encoding="utf-8")
        right_html = right.read_text(encoding="utf-8")
    except OSError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "diff_version": 1,
                        "basis": "lift_v1",
                        "ok": False,
                        "errors": [
                            {
                                "code": "DIFF_INPUT_READ_ERROR",
                                "message": str(exc),
                                "fix": "Pass two readable local HTML files.",
                            }
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
        raise
    result = diff_html(left_html, right_html, left_name=str(left), right_name=str(right))
    payload = result.to_json()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"topology_similarity: {payload['topology_similarity']}")
        for key in ("changed_headings", "changed_values", "changed_actions"):
            section = payload[key]
            if section["added"] or section["removed"]:
                print(f"{key}: +{section['added']} -{section['removed']}")
    return 0


def _diff_intent_command(args: argparse.Namespace) -> int:
    compile_check = not args.no_compile_check
    try:
        payload = diff_intent_files(args.left, args.right, compile_check=compile_check)
    except OSError as exc:
        payload = intent_diff_error_payload(
            "DIFF_INPUT_READ_ERROR",
            str(exc),
            "Pass two readable local IntentBundle JSON files.",
        )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print(f"topology_similarity: {payload['topology_similarity']}")
        for section, changes in payload["changes"].items():
            if changes["added"] or changes["removed"] or changes["changed"]:
                print(
                    f"{section}: +{changes['added']} -{changes['removed']} changed={changes['changed']}"
                )
        semantic_lines = intent_semantic_change_lines(payload.get("semantic_changes"))
        if semantic_lines:
            print("semantic_changes:")
            for line in semantic_lines:
                print(f"  {line}")
    else:
        print("failed: IntentBundle diff could not be computed")
        for error in payload["errors"]:
            side = f"{error['side']}: " if "side" in error else ""
            print(f"{side}{error['code']}: {error['message']}")
    return 0 if payload["ok"] else 2


def _init_app_command(args: argparse.Namespace) -> int:
    binding_mode = {
        "unbound-v0": APP_BUNDLE_RESOURCE_BINDING,
        "fixture-readonly-v0": APP_BUNDLE_RESOURCE_BINDING_READONLY,
    }[args.resource_binding]
    path = init_app_file(args.out, kind=args.kind, force=args.force, resource_binding=binding_mode)
    print(str(path))
    return 0


def _validate_app_command(args: argparse.Namespace) -> int:
    compile_check = not args.no_compile_check
    try:
        payload = validate_app_file(args.input, compile_check=compile_check)
    except FileNotFoundError:
        payload = {
            "schema_version": 1,
            "ok": False,
            "compile_check": "skipped" if not compile_check else "failed",
            "resource_binding": "unknown",
            "summary": None,
            "route_assertions": None,
            "raw_bytes": 0,
            "limits": {},
            "issues": [
                {
                    "severity": "error",
                    "code": "APP_FILE_NOT_FOUND",
                    "path": "$",
                    "message": f"AppBundle file not found: {args.input}",
                    "suggestion": "Create viewspec.app.json or pass the correct AppBundle file path.",
                }
            ],
        }
    except OSError as exc:
        payload = {
            "schema_version": 1,
            "ok": False,
            "compile_check": "skipped" if not compile_check else "failed",
            "resource_binding": "unknown",
            "summary": None,
            "route_assertions": None,
            "raw_bytes": 0,
            "limits": {},
            "issues": [
                {
                    "severity": "error",
                    "code": "APP_FILE_READ_ERROR",
                    "path": "$",
                    "message": f"Could not read AppBundle file {args.input}: {exc}",
                    "suggestion": "Use a readable local AppBundle JSON file.",
                }
            ],
        }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        summary = payload.get("summary") or {}
        print(f"ok: compile_check={payload['compile_check']} screens={summary.get('screen_count')} routes={summary.get('route_count')}")
    else:
        print(f"failed: compile_check={payload['compile_check']}")
        for issue in payload["issues"]:
            print(f"{issue['severity']}: {issue['code']} at {issue['path']}: {issue['message']}")
    return 0 if payload["ok"] else 2


def _diff_app_command(args: argparse.Namespace) -> int:
    compile_check = not args.no_compile_check
    try:
        payload = diff_app_files(args.left, args.right, compile_check=compile_check)
    except OSError as exc:
        payload = {
            "schema_version": 1,
            "diff_version": 1,
            "basis": "app_bundle_v0",
            "ok": False,
            "compile_check": "failed",
            "validation": {"left": None, "right": None},
            "changes": {
                "app": {"added": [], "removed": [], "changed": []},
                "routes": {"added": [], "removed": [], "changed": []},
                "resources": {"added": [], "removed": [], "changed": []},
                "screens": {"added": [], "removed": [], "changed": []},
            },
            "changed_fields": [],
            "semantic_changes": {"app_metadata": [], "routes": [], "resources": [], "screens": [], "screen_intents": []},
            "semantic_summary": [],
            "screen_intent_diffs": {},
            "counts": {"routes": {"left": 0, "right": 0}, "resources": {"left": 0, "right": 0}, "screens": {"left": 0, "right": 0}},
            "topology_similarity": 0.0,
            "errors": [{"code": "APP_DIFF_INPUT_READ_ERROR", "message": str(exc), "fix": "Pass two readable local AppBundle JSON files."}],
        }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print(f"topology_similarity: {payload['topology_similarity']}")
        for section, changes in payload["changes"].items():
            if changes["added"] or changes["removed"] or changes["changed"]:
                print(f"{section}: +{changes['added']} -{changes['removed']} ~{changes['changed']}")
        for line in app_semantic_change_lines(payload.get("semantic_changes")):
            print(f"  {line}")
    else:
        print("failed: AppBundle diff could not be computed")
        for error in payload["errors"]:
            print(f"{error['code']}: {error['message']}")
    return 0 if payload["ok"] else 2


def _compile_app_command(args: argparse.Namespace) -> int:
    payload = compile_app(
        args.input,
        out_dir=args.out,
        design_path=args.design,
        strict_design=args.strict_design,
        force=args.force,
        target=args.target,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
        print(f"ok: target={payload['target']} app={app.get('id')} routes={app.get('route_count')} screens={app.get('screen_count')}")
        print(f"shell_artifact_hash: {payload.get('shell_artifact_hash')}")
    else:
        print("failed: Static Shell V0 compile could not be completed")
        for error in payload["errors"]:
            print(f"{error['code']}: {error['message']}")
    return 0 if payload["ok"] else 2


def _init_design_command(args: argparse.Namespace) -> int:
    path = init_design_file(args.out, force=args.force)
    print(str(path))
    return 0


def _init_intent_command(args: argparse.Namespace) -> int:
    path = init_intent_file(args.out, kind=args.kind, force=args.force)
    print(str(path))
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    intent_pipeline = _doctor_intent_pipeline()
    app_bundle_pipeline = _doctor_app_bundle_pipeline()
    checks = {
        "viewspec": True,
        "version": __version__,
        "pyyaml": importlib.util.find_spec("yaml") is not None,
        "cli": callable(main),
        "intent_first_commands": {
            "init_intent": True,
            "validate_intent": True,
            "diff_intent": True,
            "init_app": True,
            "validate_app": True,
            "diff_app": True,
            "compile_app": True,
            "prove_app": True,
            "compile": True,
            "check": True,
            "prove": True,
            "check_agent_assets": True,
            "init_design": True,
            "export_agent_assets": True,
        },
        "intent_pipeline": intent_pipeline,
        "app_bundle_pipeline": app_bundle_pipeline,
        "local_network_policy": "no network calls for validate-intent/validate-app/compile-app/compile/lift/diff/diff-intent/diff-app/check/prove/prove-app/check-agent-assets/init-intent/init-app/init-design/export-agent-assets by default",
    }
    if args.agents:
        checks.update(
            {
                "agent_instruction_templates": True,
                "agent_contract_assets": agent_asset_readiness(),
                "local_agent_assets": _doctor_local_agent_assets(Path(".viewspec")),
                "published_agent_assets": _doctor_published_agent_assets(Path("demos")),
                "mcp_dependency": mcp_dependency_available(),
                "mcp_install_hint": MCP_INSTALL_HINT,
                "path_policy": "cwd containment by default",
            }
        )
    ok = _doctor_checks_ok(checks)
    print(json.dumps({"ok": ok, "checks": checks}, indent=2, sort_keys=True))
    return 0 if ok else 2


def _doctor_intent_pipeline() -> dict[str, object]:
    try:
        bundle = starter_intent_bundle("dashboard")
        text = json.dumps(bundle.to_json(), sort_keys=True)
        validation = validate_intent_text(text, compile_check=True)
        if not validation["ok"]:
            return {
                "ok": False,
                "validate_intent": False,
                "compile_check": validation["compile_check"],
                "diff_intent": False,
                "aesthetic_profile_diff": False,
                "message": "starter IntentBundle failed validation",
            }
        diff = diff_intent_text(text, text, compile_check=False)
        profile_diff = _doctor_profile_diff_smoke()
        aesthetic_profile_diff = _doctor_aesthetic_profile_diff_smoke(profile_diff)
        semantic_summary = _doctor_semantic_summary_smoke(profile_diff)
        ast = compile(bundle)
        return {
            "ok": bool(diff["ok"] and aesthetic_profile_diff and semantic_summary["ok"] and ast.result.root.root.id),
            "validate_intent": True,
            "compile_check": validation["compile_check"],
            "diff_intent": bool(diff["ok"]),
            "aesthetic_profile_diff": aesthetic_profile_diff,
            "semantic_summary": semantic_summary,
            "reference_compile": bool(ast.result.root.root.id),
        }
    except Exception as exc:
        return {
            "ok": False,
            "validate_intent": False,
            "compile_check": "failed",
            "diff_intent": False,
            "aesthetic_profile_diff": False,
            "semantic_summary": {"ok": False, "helper": "intent_semantic_change_lines", "message": str(exc)},
            "reference_compile": False,
            "message": str(exc),
        }


def _doctor_app_bundle_pipeline() -> dict[str, object]:
    try:
        bundle = starter_app_bundle("internal_tool")
        text = json.dumps(bundle, sort_keys=True)
        validation = validate_app_text(text, compile_check=True)
        bound_bundle = starter_app_bundle("internal_tool", resource_binding=APP_BUNDLE_RESOURCE_BINDING_READONLY)
        bound_validation = validate_app_text(json.dumps(bound_bundle, sort_keys=True), compile_check=True)
        if not validation["ok"]:
            return {
                "ok": False,
                "validate_app": False,
                "compile_check": validation["compile_check"],
                "diff_app": False,
                "message": "starter AppBundle failed validation",
            }
        diff = diff_app_text(text, text, compile_check=False)
        semantic_summary = app_semantic_change_lines(diff.get("semantic_changes"))
        return {
            "ok": bool(diff["ok"] and semantic_summary == [] and bound_validation["ok"]),
            # String, not bool: node absence must NOT hard-fail doctor (V1/V2 and all IntentBundle
            # flows are Python-only). Node is required only for V3 interactive_state conformance.
            "node_available": "yes" if shutil.which("node") is not None else "no",
            "node_requirement": "Node.js (>=18) is required only for V3 interactive_state reducer conformance; V1/V2 are Python-only",
            "validate_app": True,
            "validate_bound_app": bool(bound_validation["ok"]),
            "compile_check": validation["compile_check"],
            "bound_compile_check": bound_validation["compile_check"],
            "diff_app": bool(diff["ok"]),
            "compile_app": True,
            "static_shell_target": APP_SHELL_TARGET,
            "route_navigation": "static_shell_v0",
            "semantic_summary": {
                "ok": semantic_summary == [],
                "semantic_changes_key": "semantic_changes",
                "mcp_result_key": "semantic_summary",
                "python_helper": "app_semantic_change_lines",
                "semantic_change_count": len(semantic_summary),
                "summary_lines": semantic_summary,
            },
            "resource_binding": validation["resource_binding"],
            "fixture_readonly_resource_binding": bound_validation["resource_binding"],
            "binding_scope": bound_validation.get("binding_scope"),
            "binding_assertion_count": (
                bound_validation.get("resource_binding_validation", {}).get("assertion_count")
                if isinstance(bound_validation.get("resource_binding_validation"), dict)
                else 0
            ),
            "route_assertions": validation["route_assertions"],
            "screen_count": validation["summary"]["screen_count"] if isinstance(validation.get("summary"), dict) else 0,
        }
    except Exception as exc:
        return {
            "ok": False,
            "validate_app": False,
            "compile_check": "failed",
            "diff_app": False,
            "semantic_summary": {"ok": False, "helper": "app_semantic_change_lines", "message": str(exc)},
            "message": str(exc),
        }


def _doctor_aesthetic_profile_diff_smoke(profile_diff: DoctorProfileDiff | None = None) -> bool:
    diff, _view_id, expected_profile_change, expected_style_change = profile_diff or _doctor_profile_diff_smoke()
    semantic_changes = diff.get("semantic_changes", {})
    profile_changes = semantic_changes.get("aesthetic_profiles")
    profile_change = profile_changes[0] if isinstance(profile_changes, list) and len(profile_changes) == 1 else {}
    impact_delta = profile_change.get("impact_delta") if isinstance(profile_change, dict) else {}
    layout_delta = impact_delta.get("layout") if isinstance(impact_delta, dict) else None
    style_delta = impact_delta.get("style") if isinstance(impact_delta, dict) else {}
    return bool(
        diff.get("ok")
        and isinstance(profile_change, dict)
        and all(profile_change.get(key) == value for key, value in expected_profile_change.items())
        and style_delta.get("declaration_count") == {"left": 28, "right": 30}
        and {"role": "metric_card", "change": "added", "right": {"span_columns": 2, "layout_emphasis": "featured"}}
        in (layout_delta if isinstance(layout_delta, list) else [])
        and expected_style_change in semantic_changes.get("styles", [])
    )


def _doctor_semantic_summary_smoke(profile_diff: DoctorProfileDiff | None = None) -> dict[str, object]:
    try:
        diff, view_id, _expected_profile_change, _expected_style_change = profile_diff or _doctor_profile_diff_smoke()
        summary = intent_semantic_change_lines(diff.get("semantic_changes"))
        expected_summary = [
            (
                "aesthetic_profiles: profile_changed aesthetic.calm_ops -> aesthetic.executive_review "
                f"target=view:{view_id} style_delta=declarations 28 -> 30 "
                "layout_delta=metric_card added layout_emphasis=featured span_columns=2"
            ),
            "styles.aesthetic_profile: token_changed aesthetic.calm_ops -> aesthetic.executive_review",
        ]
        return {
            "ok": bool(diff.get("ok") and summary == expected_summary),
            "semantic_changes_key": "semantic_changes",
            "mcp_result_key": "semantic_summary",
            "python_helper": "intent_semantic_change_lines",
            "semantic_change_count": len(summary),
            "summary_lines": summary,
        }
    except Exception as exc:
        return {
            "ok": False,
            "semantic_changes_key": "semantic_changes",
            "mcp_result_key": "semantic_summary",
            "python_helper": "intent_semantic_change_lines",
            "semantic_change_count": 0,
            "summary_lines": [],
            "message": str(exc),
        }


def _doctor_profile_diff_smoke() -> DoctorProfileDiff:
    left = starter_intent_bundle("dashboard").to_json()
    right = starter_intent_bundle("dashboard").to_json()
    view_id = left["view_spec"]["id"]
    left["view_spec"]["styles"] = [
        {"id": "aesthetic_profile", "target": f"view:{view_id}", "token": "aesthetic.calm_ops"}
    ]
    right["view_spec"]["styles"] = [
        {"id": "aesthetic_profile", "target": f"view:{view_id}", "token": "aesthetic.executive_review"}
    ]
    diff = diff_intent_text(
        json.dumps(left, sort_keys=True),
        json.dumps(right, sort_keys=True),
        compile_check=False,
    )
    expected_profile_change = {
        "change": "profile_changed",
        "left": "aesthetic.calm_ops",
        "right": "aesthetic.executive_review",
        "left_style_id": "aesthetic_profile",
        "right_style_id": "aesthetic_profile",
        "left_target": f"view:{view_id}",
        "right_target": f"view:{view_id}",
    }
    expected_style_change = {
        "id": "aesthetic_profile",
        "change": "token_changed",
        "left": "aesthetic.calm_ops",
        "right": "aesthetic.executive_review",
    }
    return diff, view_id, expected_profile_change, expected_style_change


def _doctor_checks_ok(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return all(_doctor_checks_ok(item) for item in value.values())
    if isinstance(value, list):
        return all(_doctor_checks_ok(item) for item in value)
    return True


def _prove_command(args: argparse.Namespace) -> int:
    result = prove(
        intent_path=args.intent,
        out_dir=args.out,
        design_path=args.design,
        strict_design=bool(args.strict_design),
        target=args.target,
        kind=args.kind,
        install=bool(args.install),
        force=bool(args.force),
        report_out=args.report_out,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
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


def _prove_app_command(args: argparse.Namespace) -> int:
    result = prove_app(
        app_path=args.app,
        out_dir=args.out,
        design_path=args.design,
        strict_design=bool(args.strict_design),
        force=bool(args.force),
        report_out=args.report_out,
        with_shell=bool(args.with_shell),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
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
    if result["ok"]:
        return 0
    if any(error.get("code") in {"APP_PROOF_INTERNAL_ERROR", "APP_SHELL_INTERNAL_ERROR"} for error in result["errors"]):
        return 1
    return 2


def _doctor_local_agent_assets(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.exists():
        return {"ok": True, "status": "not_found", "path": str(resolved)}
    result = check_agent_assets(resolved)
    return {"status": "present", **result}


def _doctor_published_agent_assets(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    manifest = resolved / "agent-assets.json"
    if not manifest.exists():
        return {"ok": True, "status": "not_found", "path": str(resolved)}
    result = check_agent_assets(resolved)
    return {"status": "present", **result}


def _check_command(args: argparse.Namespace) -> int:
    result = check_artifact_dir(Path(args.artifact_dir))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ok" if result["ok"] else "failed")
        for error in result["errors"]:
            print(f"error: {error}")
        for warning in result["warnings"]:
            print(f"warning: {warning}")
        for line in _check_manifest_summary_lines(result.get("manifest_summary")):
            print(line)
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


def _verify_host_command(args: argparse.Namespace) -> int:
    compile_mode = args.intent is not None or args.out is not None
    if compile_mode:
        if args.artifact_dir is not None or args.intent is None or args.out is None:
            raise ValueError("verify-host compile mode requires --intent and --out, with no positional artifact_dir")
        result = verify_host_intent_file(
            args.intent,
            args.out,
            design_path=args.design,
            strict_design=bool(args.strict_design),
            target=args.target,
            install=bool(args.install),
            report_out=args.report_out,
        )
    else:
        if args.artifact_dir is None:
            raise ValueError("verify-host requires ARTIFACT_DIR or --intent with --out")
        if args.design or args.strict_design:
            raise ValueError("--design and --strict-design are only valid with --intent compile mode")
        result = verify_host_artifact_dir(
            args.artifact_dir,
            target=args.target,
            install=bool(args.install),
            report_out=args.report_out,
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ok" if result["ok"] else "failed")
        for error in result["errors"]:
            print(f"error: {error['code']}: {error['message']}")
        for line in _check_manifest_summary_lines(result.get("manifest_summary")):
            print(line)
        for line in _host_assertion_summary_lines(result.get("assertions"), result.get("assertion_requirements")):
            print(line)
    return 0 if result["ok"] else 2


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
    lines.extend(_host_assertion_summary_lines(host_report.get("assertions"), host_report.get("assertion_requirements")))
    return lines


def _init_agent_command(args: argparse.Namespace) -> int:
    result = init_agent_instructions(args.root, args.target, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _export_agent_assets_command(args: argparse.Namespace) -> int:
    result = export_agent_assets(args.out, force=args.force, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _check_agent_assets_command(args: argparse.Namespace) -> int:
    result = check_agent_assets(args.asset_dir)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ok" if result["ok"] else "failed")
        for error in result["errors"]:
            print(f"error: {error}")
    return 0 if result["ok"] else 2


def _mcp_command(args: argparse.Namespace) -> int:
    try:
        run_mcp_server(cwd=args.cwd, allow_outside_cwd=args.allow_outside_cwd)
    except MissingMCPDependency:
        print(MCP_INSTALL_HINT, file=sys.stderr)
        return 2
    return 0


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


def _compile_command_args(args: argparse.Namespace, source_name: str | None) -> list[str]:
    command = ["viewspec", "compile", source_name or "<stdin>"]
    if args.design:
        command.extend(["--design", Path(args.design).name])
    if args.strict_design:
        command.append("--strict-design")
    if args.target != "html-tailwind":
        command.extend(["--target", args.target])
    command.extend(["--out", "<out>"])
    if args.stdin_format:
        command.extend(["--stdin-format", args.stdin_format])
    if args.title:
        command.extend(["--title", args.title])
    if args.lift_json:
        command.append("--lift-json")
    return command


def _print_design_error(exc: DesignSystemError) -> None:
    print(f"error: {exc}", file=sys.stderr)
    if exc.report is None:
        return
    summary = exc.report.summary()
    print(
        f"DESIGN.md lint: {summary['errors']} error(s), {summary['warnings']} warning(s), {summary['info']} info",
        file=sys.stderr,
    )
    for finding in exc.report.findings:
        print(f"{finding.severity}: {finding.code} at {finding.path}: {finding.message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
