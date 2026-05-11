"""Command-line interface for the local ViewSpec SDK."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from viewspec._version import __version__
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
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

    return parser


def _compile_command(args: argparse.Namespace) -> int:
    data, source_name, input_path = _read_input(args.input, stdin_format=args.stdin_format)
    input_format = _input_format(args.input, args.stdin_format)
    out_dir = Path(args.out)
    design = _load_design(args.design, strict=args.strict_design)

    if input_format == "html":
        _ensure_no_input_overwrite(input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json", "lift.json"))
        result = compile_html(data, design=design, title=args.title, source_name=source_name)
        paths = write_html_compile_result(result, out_dir, include_lift=bool(args.lift_json))
        print(json.dumps(paths, indent=2, sort_keys=True))
        return 0

    payload = json.loads(data)
    bundle = IntentBundle.from_json(payload)
    ast = compile(bundle, design=design, strict_design=args.strict_design)
    _ensure_no_input_overwrite(input_path, out_dir, ("index.html", "provenance_manifest.json", "diagnostics.json"))
    paths = HtmlTailwindEmitter().emit(ast, out_dir)
    _wrap_bundle_manifest(
        Path(paths["manifest"]),
        source_name=source_name,
        source_hash=_source_hash(data),
        design=design,
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
    _atomic_write(output_path, json.dumps(result.to_json(), indent=2, sort_keys=True))
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
    return path.read_text(encoding="utf-8"), str(path), path


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


def _ensure_no_input_overwrite(input_path: Path | None, out_dir: Path, output_names: tuple[str, ...]) -> None:
    if input_path is None:
        return
    input_resolved = input_path.resolve()
    out_resolved = out_dir.resolve()
    for name in output_names:
        if input_resolved == (out_resolved / name).resolve():
            raise ValueError(f"Refusing to overwrite input file with output {name}")


def _wrap_bundle_manifest(
    manifest_path: Path,
    *,
    source_name: str | None,
    source_hash: str,
    design: DesignSystemContext | None,
) -> None:
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    wrapped: dict[str, Any] = {
        "version": 1,
        "kind": "intent_bundle_compile",
        "source_name": source_name,
        "source_hash": source_hash,
        "command": "compile",
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
    _atomic_write(manifest_path, json.dumps(wrapped, indent=2, sort_keys=True))


def _source_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        Path(temp_name).replace(path)
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise


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
