"""Command-line interface for the local ViewSpec SDK."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from viewspec._version import __version__
from viewspec.agent_assets import AgentAssetError, agent_asset_readiness, check_agent_assets, export_agent_assets
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.intent_tools import (
    STARTER_INTENT_KINDS,
    diff_intent_files,
    diff_intent_text,
    init_intent_file,
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
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency, mcp_dependency_available, run_mcp_server
from viewspec.native_agents import NativeAgentError, VALID_TARGETS, init_agent_instructions
from viewspec.raw_html import HtmlInputError, compile_html, diff_html, lift_html, write_html_compile_result
from viewspec.types import IntentBundle


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
        choices=("html-tailwind", "react-tsx"),
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

    check_parser = subparsers.add_parser("check", help="Validate a local ViewSpec artifact directory.")
    check_parser.add_argument("artifact_dir", help="Directory containing provenance_manifest.json and generated artifact output.")
    check_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    check_parser.set_defaults(func=_check_command)

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
        if args.target == "react-tsx"
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
    else:
        print("failed: IntentBundle diff could not be computed")
        for error in payload["errors"]:
            side = f"{error['side']}: " if "side" in error else ""
            print(f"{side}{error['code']}: {error['message']}")
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
    checks = {
        "viewspec": True,
        "version": __version__,
        "pyyaml": importlib.util.find_spec("yaml") is not None,
        "cli": callable(main),
        "intent_first_commands": {
            "init_intent": True,
            "validate_intent": True,
            "diff_intent": True,
            "compile": True,
            "check": True,
            "check_agent_assets": True,
            "init_design": True,
            "export_agent_assets": True,
        },
        "intent_pipeline": intent_pipeline,
        "local_network_policy": "no network calls for validate-intent/compile/lift/diff/diff-intent/check/check-agent-assets/init-intent/init-design/export-agent-assets",
    }
    if args.agents:
        checks.update(
            {
                "agent_instruction_templates": True,
                "agent_contract_assets": agent_asset_readiness(),
                "local_agent_assets": _doctor_local_agent_assets(Path(".viewspec")),
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
                "message": "starter IntentBundle failed validation",
            }
        diff = diff_intent_text(text, text, compile_check=False)
        ast = compile(bundle)
        return {
            "ok": bool(diff["ok"] and ast.result.root.root.id),
            "validate_intent": True,
            "compile_check": validation["compile_check"],
            "diff_intent": bool(diff["ok"]),
            "reference_compile": bool(ast.result.root.root.id),
        }
    except Exception as exc:
        return {
            "ok": False,
            "validate_intent": False,
            "compile_check": "failed",
            "diff_intent": False,
            "reference_compile": False,
            "message": str(exc),
        }


def _doctor_checks_ok(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return all(_doctor_checks_ok(item) for item in value.values())
    if isinstance(value, list):
        return all(_doctor_checks_ok(item) for item in value)
    return True


def _doctor_local_agent_assets(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.exists():
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
    return 0 if result["ok"] else 2


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
