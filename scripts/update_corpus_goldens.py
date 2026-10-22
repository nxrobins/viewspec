#!/usr/bin/env python3
"""Regenerate tests/fixtures/corpus_goldens.json (audit findings T-2 and C-6).

The golden file pins, for every case in conformance/verification/corpus.json and for the demo
AppBundle, per local compile target: the emitter ``artifact_hash``, the manifest's
``semantic_digest`` and the sha256 of every emitted file. ``tests/test_corpus_goldens.py``
recompiles the same inputs and fails on any drift, so an intentional output change has to land
together with a regenerated golden file, as a visible, reviewable diff.

Usage::

    python scripts/update_corpus_goldens.py            # rewrite the golden file in place
    python scripts/update_corpus_goldens.py --check    # exit 1 when the golden file is stale

Why the pins are portable:

* Inputs are staged under stable names (``<case-id>.intent.json``), so ``source_name`` and
  ``command_args`` in the manifests do not depend on the checkout path; outputs are hashed by
  content only, so the work directory does not matter either.
* The writers emit LF on every platform and no artifact embeds a path, a clock, the machine or
  the Python version. Only the Node reducer conformance could differ per machine, and the pinned
  AppBundle declares no state, so it never runs.
* ``provenance_manifest.json`` and ``viewspec_app_manifest.json`` embed the SDK version, so the
  file hashes of those two manifests (never ``artifact_hash`` or ``semantic_digest``) rotate on a
  version bump: regenerate the goldens in the release commit.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from viewspec import __version__
from viewspec.app_react import REACT_APP_TARGET
from viewspec.app_shell import APP_SHELL_TARGET
from viewspec.app_tools import compile_app_tool
from viewspec.conformance import ConformanceCase, load_conformance_corpus
from viewspec.intent_tools import INTENT_COMPILE_TARGETS, compile_intent_bundle_file_tool
from viewspec.local_tools_hash import bytes_hash, source_hash

ROOT = Path(__file__).resolve().parents[1]
CORPUS_MANIFEST = "conformance/verification/corpus.json"
GOLDENS_PATH = ROOT / "tests" / "fixtures" / "corpus_goldens.json"
REGENERATE_COMMAND = "python scripts/update_corpus_goldens.py"
SCHEMA_VERSION = 1
INTENT_TARGETS: tuple[str, ...] = tuple(INTENT_COMPILE_TARGETS)
APP_TARGETS: tuple[str, ...] = (APP_SHELL_TARGET, REACT_APP_TARGET)
# Top-level demo examples that are AppBundles. Their embedded screens are already corpus cases
# (app-queue, app-detail); this compiles the whole bundle with every local app target.
APP_BUNDLE_SOURCES: dict[str, str] = {
    "agent-app-example.internal-tool": "demos/agent-app-example.internal-tool.json",
}


def intent_source_text(case: ConformanceCase) -> str:
    """The IntentBundle JSON a corpus case compiles: the file itself or its embedded screen bundle."""
    text = case.source_path.read_text(encoding="utf-8")
    if case.kind == "intent":
        return text
    payload = json.loads(text)
    screen = next(item for item in payload["screens"] if item["id"] == case.screen_id)
    return json.dumps(screen["intent_bundle"], indent=2, sort_keys=True) + "\n"


def file_hashes(root: Path) -> dict[str, str]:
    """sha256 of every file under ``root``, keyed by its POSIX path relative to ``root``."""
    return {
        path.relative_to(root).as_posix(): bytes_hash(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _compile_failure(label: str, result: dict[str, Any]) -> RuntimeError:
    return RuntimeError(f"{label} did not compile:\n{json.dumps(result, indent=2, sort_keys=True)}")


def compile_intent_case(case: ConformanceCase, target: str, *, work_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile one corpus case for one local target; return the wrapped manifest and its pins."""
    source = work_dir / "sources" / f"{case.id}.intent.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(intent_source_text(case), encoding="utf-8", newline="\n")
    out_dir = work_dir / "intent" / case.id / target
    result = compile_intent_bundle_file_tool(source, out_dir, target=target, cwd=work_dir)
    if not result.get("ok"):
        raise _compile_failure(f"{case.id} ({target})", result)
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    pins = {
        "artifact_file": manifest["artifact_file"],
        "artifact_hash": manifest["artifact_hash"],
        "semantic_digest": manifest["semantic_digest"]["digest"],
        "files": file_hashes(out_dir),
    }
    return manifest, pins


def _screen_pins(out_dir: Path) -> dict[str, dict[str, str]]:
    """``artifact_hash`` and ``semantic_digest`` of every per-screen provenance manifest in an app output."""
    pins: dict[str, dict[str, str]] = {}
    for manifest_path in sorted(out_dir.rglob("provenance_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pins[manifest_path.parent.relative_to(out_dir).as_posix()] = {
            "artifact_hash": manifest["artifact_hash"],
            "semantic_digest": manifest["semantic_digest"]["digest"],
        }
    return pins


def compile_app_bundle(bundle_id: str, source: Path, target: str, *, work_dir: Path) -> dict[str, Any]:
    """Compile one AppBundle for one local app target and return its pins."""
    staged = work_dir / "sources" / f"{bundle_id}.app.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(source.read_bytes())
    out_dir = work_dir / "app" / bundle_id / target
    result = compile_app_tool(staged, out_dir, target=target, cwd=work_dir)
    if not result.get("ok"):
        raise _compile_failure(f"{bundle_id} ({target})", result)
    metadata = result["metadata"]
    artifact_path = Path(result["paths"].get("app") or result["paths"]["index"])
    artifact_hash = metadata.get("app_artifact_hash") or metadata.get("shell_artifact_hash")
    if artifact_hash != bytes_hash(artifact_path.read_bytes()):
        raise RuntimeError(f"{bundle_id} ({target}): reported artifact hash does not match {artifact_path.name}")
    return {
        "artifact_file": artifact_path.relative_to(out_dir).as_posix(),
        "artifact_hash": artifact_hash,
        "screens": _screen_pins(out_dir),
        "files": file_hashes(out_dir),
    }


def build_goldens(work_dir: Path, *, root: Path = ROOT) -> dict[str, Any]:
    """Compile every corpus case and AppBundle under ``work_dir`` and return the golden document."""
    # Resolve once so every derived path shares one spelling. On Windows a temporary directory
    # can be handed out in 8.3 short form (RUNNER~1) while the compiler returns the long form,
    # which makes relative_to() fail although both name the same directory.
    work_dir = Path(work_dir).resolve()
    corpus = load_conformance_corpus(str(root / CORPUS_MANIFEST))
    cases: dict[str, Any] = {}
    for case in corpus.cases:
        case_source_hash = source_hash(intent_source_text(case))
        targets: dict[str, Any] = {}
        for target in INTENT_TARGETS:
            manifest, pins = compile_intent_case(case, target, work_dir=work_dir)
            if manifest["source_hash"] != case_source_hash:
                raise RuntimeError(f"{case.id} ({target}): manifest source_hash does not match the staged source")
            targets[target] = pins
        cases[case.id] = {
            "kind": case.kind,
            "source": case.source,
            "screen_id": case.screen_id,
            "source_hash": case_source_hash,
            "targets": targets,
        }
    app_bundles: dict[str, Any] = {}
    for bundle_id, relative in sorted(APP_BUNDLE_SOURCES.items()):
        source = root / relative
        app_bundles[bundle_id] = {
            "source": relative,
            "source_hash": source_hash(source.read_text(encoding="utf-8")),
            "targets": {
                target: compile_app_bundle(bundle_id, source, target, work_dir=work_dir) for target in APP_TARGETS
            },
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "regenerate": REGENERATE_COMMAND,
        "sdk_version": __version__,
        "corpus": CORPUS_MANIFEST,
        "intent_targets": list(INTENT_TARGETS),
        "app_targets": list(APP_TARGETS),
        "cases": cases,
        "app_bundles": app_bundles,
    }


def render_goldens(document: dict[str, Any]) -> str:
    """The one canonical text form of the golden file: sorted keys, two-space indent, trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 100 else text[:97] + "..."


def diff_goldens(expected: Any, actual: Any, prefix: str = "") -> list[str]:
    """Human-readable differences between a pinned sub-tree and the freshly compiled one."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            path = f"{prefix}{key}"
            if key not in actual:
                lines.append(f"{path}: pinned but not produced (expected {_short(expected[key])})")
            elif key not in expected:
                lines.append(f"{path}: produced but not pinned (actual {_short(actual[key])})")
            else:
                lines.extend(diff_goldens(expected[key], actual[key], f"{path}."))
        return lines
    if expected != actual:
        return [f"{prefix.rstrip('.') or '<root>'}: expected {_short(expected)}, actual {_short(actual)}"]
    return []


def pinned_output_count(document: dict[str, Any]) -> int:
    return sum(len(case["targets"]) for case in document["cases"].values()) + sum(
        len(bundle["targets"]) for bundle in document["app_bundles"].values()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=GOLDENS_PATH, help="Golden file to write or check.")
    parser.add_argument("--check", action="store_true", help="Do not write; exit 1 when the golden file is stale.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Empty scratch directory for the compiles (default: a temporary directory that is removed afterwards).",
    )
    args = parser.parse_args(argv)

    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        document = build_goldens(args.work_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="viewspec-corpus-goldens-") as temp_dir:
            document = build_goldens(Path(temp_dir))
    rendered = render_goldens(document)
    summary = (
        f"{len(document['cases'])} corpus cases x {len(INTENT_TARGETS)} intent targets + "
        f"{len(document['app_bundles'])} app bundle(s) x {len(APP_TARGETS)} app targets = "
        f"{pinned_output_count(document)} pinned outputs"
    )

    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current == rendered:
            print(f"{args.output} is up to date ({summary}).")
            return 0
        for line in diff_goldens(json.loads(current) if current else {}, document):
            print(line)
        print(f"{args.output} is stale; run `{REGENERATE_COMMAND}`.")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output} ({summary}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
