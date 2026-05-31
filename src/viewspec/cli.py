"""Command-line interface for the local ViewSpec SDK."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.local_tools import (
    atomic_write,
    check_artifact_dir,
    ensure_no_input_overwrite,
    init_design_file,
    source_hash,
)
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency, mcp_dependency_available, run_mcp_server
from viewspec.native_agents import VALID_TARGETS, init_agent_instructions
from viewspec.raw_html import (
    MANIFEST_SCHEMA_VERSION,
    HtmlInputError,
    compile_html,
    diff_html,
    lift_html,
    write_html_compile_result,
)
from viewspec.types import IntentBundle


BUNDLE_POLICY_VERSION = "viewspec-intent-bundle@1"


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

    compile_parser = subparsers.add_parser("compile", help="Compile raw HTML or an IntentBundle JSON file locally.")
    compile_parser.add_argument("input", help="Input .html/.htm/.json file, or '-' for stdin.")
    compile_parser.add_argument("--design", help="Optional DESIGN.md file to apply locally.")
    compile_parser.add_argument("--strict-design", action="store_true", help="Fail on DESIGN.md warnings as well as errors.")
    compile_parser.add_argument("--out", required=True, help="Output directory.")
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

    init_parser = subparsers.add_parser("init-design", help="Write a starter strict DESIGN.md file.")
    init_parser.add_argument("--out", default="DESIGN.md", help="Output DESIGN.md path.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_parser.set_defaults(func=_init_design_command)

    doctor_parser = subparsers.add_parser("doctor", help="Check local ViewSpec SDK readiness.")
    doctor_parser.add_argument("--agents", action="store_true", help="Also check native agent integration readiness.")
    doctor_parser.set_defaults(func=_doctor_command)

    check_parser = subparsers.add_parser("check", help="Validate a local ViewSpec artifact directory.")
    check_parser.add_argument("artifact_dir", help="Directory containing index.html and provenance_manifest.json.")
    check_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    check_parser.set_defaults(func=_check_command)

    init_agent_parser = subparsers.add_parser("init-agent", help="Install managed ViewSpec instructions for coding agents.")
    init_agent_parser.add_argument("--target", required=True, choices=VALID_TARGETS, help="Agent instruction target to update.")
    init_agent_parser.add_argument("--root", default=".", help="Repository root for instruction files.")
    init_agent_parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    init_agent_parser.set_defaults(func=_init_agent_command)

    mcp_parser = subparsers.add_parser("mcp", help="Start the optional ViewSpec stdio MCP server.")
    mcp_parser.add_argument("--cwd", default=".", help="MCP path sandbox root.")
    mcp_parser.add_argument("--allow-outside-cwd", action="store_true", help="Allow MCP tools to read/write outside --cwd.")
    mcp_parser.set_defaults(func=_mcp_command)

    return parser


def _compile_command(args: argparse.Namespace) -> int:
    data, source_name, input_path = _read_input(args.input, stdin_format=args.stdin_format)
    input_format = _input_format(args.input, args.stdin_format)
    out_dir = Path(args.out)
    design = _load_design(args.design, strict=args.strict_design)

    if input_format == "html":
        ensure_no_input_overwrite(input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json"))
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

    payload = json.loads(data)
    bundle = IntentBundle.from_json(payload)
    ast = compile(bundle, design=design, strict_design=args.strict_design)
    ensure_no_input_overwrite(input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json"))
    paths = HtmlTailwindEmitter().emit(ast, out_dir)
    _wrap_bundle_manifest(
        Path(paths["manifest"]),
        source_name=source_name,
        raw_source_hash=source_hash(data),
        design=design,
        command_args=_compile_command_args(args, source_name),
    )
    print(json.dumps(paths, indent=2, sort_keys=True))
    return 0


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
    result = diff_html(
        left.read_text(encoding="utf-8"),
        right.read_text(encoding="utf-8"),
        left_name=str(left),
        right_name=str(right),
    )
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


def _init_design_command(args: argparse.Namespace) -> int:
    path = init_design_file(args.out, force=args.force)
    print(str(path))
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    checks = {
        "viewspec": True,
        "version": __version__,
        "pyyaml": importlib.util.find_spec("yaml") is not None,
        "cli": callable(main),
        "local_network_policy": "no network calls for compile/lift/diff",
    }
    if args.agents:
        checks.update(
            {
                "agent_instruction_templates": True,
                "mcp_dependency": mcp_dependency_available(),
                "mcp_install_hint": MCP_INSTALL_HINT,
                "path_policy": "cwd containment by default",
            }
        )
    ok = all(value is not False for value in checks.values())
    print(json.dumps({"ok": ok, "checks": checks}, indent=2, sort_keys=True))
    return 0 if ok else 2


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


def _wrap_bundle_manifest(
    manifest_path: Path,
    *,
    source_name: str | None,
    raw_source_hash: str,
    design: DesignSystemContext | None,
    command_args: list[str],
) -> None:
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    html_path = manifest_path.with_name("index.html")
    artifact_hash = source_hash(html_path.read_text(encoding="utf-8")) if html_path.exists() else None
    wrapped: dict[str, Any] = {
        "version": 1,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "intent_bundle_compile",
        "sdk_version": __version__,
        "source_name": source_name,
        "raw_source_hash": raw_source_hash,
        "source_hash": raw_source_hash,
        "design_hash": design.design_hash if design else None,
        "artifact_hash": artifact_hash,
        "command": "compile",
        "command_args": command_args,
        "policy_version": BUNDLE_POLICY_VERSION,
        "guarantees": {
            "sdk_network_calls": "none",
            "artifact_autofetch_network": "none",
            "network_calls": "none",
            "decompilation": "not_applicable",
        },
        "nodes": existing,
        "diagnostics": json.loads(manifest_path.with_name("diagnostics.json").read_text(encoding="utf-8")),
        "external_refs": [],
    }
    if design is not None:
        wrapped["design"] = design.to_meta()
    atomic_write(manifest_path, json.dumps(wrapped, indent=2, sort_keys=True))


def _compile_command_args(args: argparse.Namespace, source_name: str | None) -> list[str]:
    command = ["viewspec", "compile", source_name or "<stdin>"]
    if args.design:
        command.extend(["--design", Path(args.design).name])
    if args.strict_design:
        command.append("--strict-design")
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
