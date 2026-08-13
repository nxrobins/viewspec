#!/usr/bin/env python3
"""Plan, run, and summarize reproducible ViewSpec agent-UI evaluations."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from typing import Any

from viewspec.agent_eval import (
    AGENT_UI_EVAL_ARMS,
    AGENT_UI_EVAL_SCHEMA_VERSION,
    AgentEvalProtocol,
    AgentEvalStep,
    AgentEvalTask,
    canonical_json,
    load_agent_eval_protocol,
    parse_codex_jsonl,
    summarize_agent_eval_session,
    summarize_agent_eval_study,
)
from viewspec.agent_eval_value import (
    apply_value_trial,
    checkpoint_envelope,
    load_mutation_manifest,
    seeded_arm_order,
    seeded_trial_order,
    source_snapshot_hash,
    validate_checkpoint,
    validate_stable_hooks,
)
from viewspec.app_freerange import FREERANGE_PACKAGE, FREERANGE_VERSION
from viewspec.app_pretext import PRETEXT_PACKAGE, PRETEXT_VERSION
from viewspec.native_agents import agent_instruction_block
from viewspec.node_runtime import materialize_prebuilt_node_modules


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "conformance" / "agent-ui-v2" / "protocol.json"


def _resolve_viewspec_cli() -> Path:
    """Locate the viewspec console script.

    Prefers the in-repo virtualenv so a developer's working tree wins, then falls
    back to whatever is on PATH, which is how CI and pipx installs expose it.
    """

    local = ROOT / ".venv" / "bin" / "viewspec"
    if local.is_file():
        return local
    discovered = shutil.which("viewspec")
    return Path(discovered) if discovered else local


VIEWSPEC = _resolve_viewspec_cli()
BROWSER_SCORER = ROOT / "scripts" / "agent_ui_browser_score.mjs"
NODE_MODULE_SEED = ROOT / "conformance" / "agent-ui-v2" / "react-dependencies" / "node_modules"
MUTATION_MANIFEST = (
    ROOT / "conformance" / "agent-ui-v2" / "mutations" / "field-dispatch-lifecycle.json"
)
REFERENCE_IMAGE_WIDTH = 1440
REFERENCE_IMAGE_HEIGHT = 1000


ARM_INSTRUCTIONS = {
    "code-first": """Use direct, hand-authored HTML, CSS, and JavaScript only. Do not use ViewSpec or generated code.
Your static artifact is submission/index.html. It must be standalone, make no network requests, use no remote
assets, and work when served as a static site. At the leverage turn the evaluator adds submission/react/; from
then on maintain the same product in both submission/index.html and submission/react/src/App.jsx. Do not replace
the pinned dependencies. The final source must contain each neutral data-eval-id exactly once:
action-show-guide, action-record-review, action-pause-intake, panel-escalation-guide, panel-review-count,
panel-intake-paused, job-j205-title, and job-j207.""",
    "viewspec-core": """Use viewspec.app.json as the only authored product source. Do not edit generated HTML,
React, CSS, or evaluator artifacts. Start from the provided AppBundle V4 example, replace its sample product with
the requested product, and use ./bin/viewspec validate-app viewspec.app.json --json as often as useful. Express
content, hierarchy, resource views, state mutations, visibility, and replay assertions in the AppBundle. The
evaluator owns compilation and proof generation after every turn. For this reference-sensitive screen, declare
screen.presentation rules and responsive anchors on stable region, motif, and binding IDs; use motif rule items
layouts for per-record/per-group field geometry and do not leave the final screen on APP_PRESENTATION_INFERRED.
Model the page eyebrow/title as a hero motif for native header/h1 markup and leave its inferred intrinsic width
unless the reference requires another bounded width. In operations_workspace, keep sibling
sidebar/main regions visible, stack them at compact/medium, use a wide rail_lg/fluid shell, give main compact lg,
medium 2xl, and wide xl padding, and scope any
sidebar/main same_row anchor to wide only. Use resource_view.repeat for repeated job fields; it owns every repeated
record-field in its target motif, so remove all hand-authored prototype record bindings there. Use action-oriented
replay events where applicable. Use exact IDs show_escalation_guide,
record_review, pause_intake, reveal_escalation_guide, increment_reviewed_count, show_escalation_panel,
show_review_count, show_intake_paused, J-205, and J-207. Put J-205's long copy in its title field and retain a
replay that applies increment_reviewed_count twice and expects reviewed_count = 2.""",
    "viewspec-deep": """Use viewspec.app.json as the only authored product source. Do not edit generated HTML,
React, CSS, or evaluator artifacts. Start from the provided AppBundle V4 example, replace its sample product with
the requested product, and use ./bin/viewspec validate-app viewspec.app.json --json as often as useful. Express
content, hierarchy, resource views, state mutations, visibility, and replay assertions in the AppBundle. Retain
at least one genuinely used numeric state operation for Freerange and compiler-owned native DOM text for Pretext.
The evaluator owns compilation plus the composed Freerange/Pretext proof after every turn. For this
reference-sensitive screen, declare screen.presentation rules and responsive anchors on stable region, motif, and
binding IDs; use motif rule items layouts for per-record/per-group field geometry and do not leave the final screen
on APP_PRESENTATION_INFERRED. Model the page eyebrow/title as a hero motif for native header/h1 markup and leave
its inferred intrinsic width unless the reference requires another bounded width. In
operations_workspace, keep sibling sidebar/main regions visible, stack them at compact/medium, use a wide two-track
rail_lg/fluid shell, give main compact lg, medium 2xl, and wide xl padding, and scope any sidebar/main same_row
anchor to wide only. Use resource_view.repeat for repeated
job fields; it owns every repeated record-field in its target motif, so remove all hand-authored prototype record
bindings there. Use action-oriented replay events where applicable. Use exact IDs
show_escalation_guide, record_review, pause_intake, reveal_escalation_guide, increment_reviewed_count,
show_escalation_panel, show_review_count, show_intake_paused, J-205, and J-207. Put J-205's long copy in its title
field and retain a replay that applies increment_reviewed_count twice and expects reviewed_count = 2.""",
}


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    merge_stderr: bool = True,
) -> tuple[subprocess.CompletedProcess[str], int]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return result, elapsed_ms


def _empty_output(path: Path) -> Path:
    output = path.resolve()
    if output.exists():
        if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
            raise FileExistsError(f"Evaluation output must be an empty directory: {output}")
    else:
        output.mkdir(parents=True)
    return output


def _protocol(path: str | Path) -> tuple[AgentEvalProtocol, Path]:
    protocol_path = Path(path).resolve()
    return load_agent_eval_protocol(protocol_path), protocol_path


def _reference_path(protocol_path: Path, task: AgentEvalTask) -> Path:
    path = protocol_path.parent / task.reference
    if not path.is_file():
        raise FileNotFoundError(f"Agent eval reference does not exist: {path}")
    return path.resolve()


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    if executable:
        temporary.chmod(0o755)
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_fact(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _trial_artifact_manifest(
    *,
    trial_root: Path,
    attempt_used: int,
    final_step_id: str,
    phase_timings_ms: dict[str, int],
) -> dict[str, Any]:
    artifact_root = (
        trial_root
        / f"attempt-{attempt_used}"
        / "artifacts"
        / f"10-{final_step_id}"
    )
    score = artifact_root / "browser-score.json"
    manifest: dict[str, Any] = {"root": str(trial_root)}
    if score.is_file():
        manifest.update(score_status="recorded", score=str(score))
        return manifest
    if "browser_score" in phase_timings_ms:
        raise RuntimeError(
            "browser scoring completed without its declared score artifact: "
            f"{score}"
        )
    detector_evidence = [
        str(path)
        for path in (artifact_root / "compile.log", artifact_root / "proof.log")
        if path.is_file()
    ]
    if not detector_evidence:
        raise RuntimeError(
            "evaluation stopped before browser scoring without retained compile/proof evidence: "
            f"{artifact_root}"
        )
    manifest.update(
        score_status="not_run_early_detector",
        detector_evidence=detector_evidence,
    )
    return manifest


def _value_evidence_artifact_integrity(
    evidence: dict[str, Any],
    *,
    relative_to: Path | None = None,
) -> dict[str, Any]:
    references: list[tuple[str, str, str]] = []
    errors: list[str] = []

    def add(label: str, value: Any, kind: str = "file") -> None:
        if not isinstance(value, str) or not value:
            errors.append(f"{label} must be a non-empty artifact path")
            return
        references.append((label, value, kind))

    manifest = evidence.get("manifest")
    if isinstance(manifest, dict) and "path" in manifest:
        add("manifest.path", manifest.get("path"))
    for collection_name in ("mutation_trials", "negative_control_trials"):
        trials = evidence.get(collection_name, [])
        if not isinstance(trials, list):
            errors.append(f"{collection_name} must be an array")
            continue
        for index, trial in enumerate(trials):
            if not isinstance(trial, dict) or trial.get("applicable", True) is not True:
                continue
            prefix = f"{collection_name}[{index}]"
            artifacts = trial.get("artifacts")
            if not isinstance(artifacts, dict):
                errors.append(f"{prefix}.artifacts must be an object")
                continue
            add(f"{prefix}.artifacts.root", artifacts.get("root"), "directory")
            score_status = artifacts.get("score_status")
            if score_status == "recorded":
                add(f"{prefix}.artifacts.score", artifacts.get("score"))
            elif score_status == "not_run_early_detector":
                if "score" in artifacts:
                    errors.append(
                        f"{prefix}.artifacts.score must be absent when browser scoring did not run"
                    )
                detector_evidence = artifacts.get("detector_evidence")
                if not isinstance(detector_evidence, list) or not detector_evidence:
                    errors.append(
                        f"{prefix}.artifacts.detector_evidence must retain at least one artifact"
                    )
                else:
                    for evidence_index, path in enumerate(detector_evidence):
                        add(
                            f"{prefix}.artifacts.detector_evidence[{evidence_index}]",
                            path,
                        )
            else:
                errors.append(f"{prefix}.artifacts.score_status is invalid")
            repair_artifacts = trial.get("repair_artifacts")
            if isinstance(repair_artifacts, dict) and "root" in repair_artifacts:
                add(f"{prefix}.repair_artifacts.root", repair_artifacts.get("root"), "directory")
    targets = evidence.get("target_trials", [])
    if not isinstance(targets, list):
        errors.append("target_trials must be an array")
    else:
        for index, target in enumerate(targets):
            if not isinstance(target, dict) or target.get("applicable", True) is not True:
                continue
            add(f"target_trials[{index}].score_artifact", target.get("score_artifact"))
            if "parity_artifact" in target:
                add(
                    f"target_trials[{index}].parity_artifact",
                    target.get("parity_artifact"),
                )
    repair_turns = evidence.get("repair_turns", [])
    if not isinstance(repair_turns, list):
        errors.append("repair_turns must be an array")
    else:
        for index, repair in enumerate(repair_turns):
            if not isinstance(repair, dict):
                continue
            prompt = repair.get("prompt")
            if isinstance(prompt, dict) and "path" in prompt:
                add(f"repair_turns[{index}].prompt.path", prompt.get("path"))

    missing: list[dict[str, str]] = []
    for label, raw_path, kind in references:
        path = Path(raw_path)
        if not path.is_absolute() and relative_to is not None:
            path = relative_to / path
        exists = path.is_dir() if kind == "directory" else path.is_file()
        if not exists:
            missing.append({"field": label, "path": raw_path, "expected": kind})
    return {
        "checked": True,
        "complete": not errors and not missing,
        "declared_reference_count": len(references),
        "missing": missing,
        "errors": errors,
    }


def _finalize_value_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    evidence["artifact_integrity"] = _value_evidence_artifact_integrity(evidence)
    return evidence


def _require_value_evidence_integrity(session: dict[str, Any]) -> None:
    evidence = session.get("value_evidence")
    if not isinstance(evidence, dict):
        return
    actual = _value_evidence_artifact_integrity(evidence)
    recorded = evidence.get("artifact_integrity")
    if recorded != actual:
        raise ValueError("value evidence artifact-integrity record is stale or missing")
    if actual["complete"] is not True:
        details = [*actual["errors"], *(item["path"] for item in actual["missing"])]
        raise ValueError(
            "value evidence contains invalid or dangling artifact references: "
            + "; ".join(details)
        )


def _prompt_fact(prompt: str, path: Path) -> dict[str, Any]:
    _write(path, prompt)
    fact = _file_fact(path)
    if fact is None:  # pragma: no cover - _write either succeeds or raises
        raise RuntimeError(f"prompt artifact was not written: {path}")
    return {**fact, "path": str(path)}


def _strict_jsonl(raw: str) -> tuple[str, str]:
    events: list[str] = []
    diagnostics: list[str] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            diagnostics.append(f"[stdout line {number}] {line}")
            continue
        if not isinstance(value, dict):
            diagnostics.append(f"[stdout line {number}] {line}")
            continue
        events.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return (
        "\n".join(events) + ("\n" if events else ""),
        "\n".join(diagnostics) + ("\n" if diagnostics else ""),
    )


def _diagnostics_path(events_path: Path) -> Path:
    return events_path.with_suffix(".diagnostics.log")


def _product_tree_fact() -> dict[str, Any]:
    excluded = {"__pycache__", "node_modules", "dist", "test-results", ".pytest_cache"}
    candidates = [ROOT / "pyproject.toml"]
    candidates.extend(
        path
        for path in (ROOT / "src" / "viewspec").rglob("*")
        if path.is_file()
        and path.suffix != ".pyc"
        and not any(part.startswith(".") or part in excluded for part in path.relative_to(ROOT).parts)
    )
    digest = hashlib.sha256()
    total_bytes = 0
    files = sorted(path for path in candidates if path.is_file())
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + content + b"\0")
        total_bytes += len(content)
    return {
        "scope": ["pyproject.toml", "src/viewspec/**"],
        "file_count": len(files),
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
    }


def _command_version(command: list[str], *, cwd: Path = ROOT) -> str | None:
    try:
        result, _elapsed = _run(command, cwd=cwd, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("WARNING:")]
    return lines[-1][:256] if result.returncode == 0 and lines else None


def _managed_instruction_fact() -> dict[str, Any]:
    """Bind the exact managed instruction block the ViewSpec arms received."""

    block = agent_instruction_block("codex")
    encoded = block.encode("utf-8")
    return {
        "target": "codex",
        "path": "AGENTS.md",
        "applies_to_arms": [arm for arm in AGENT_UI_EVAL_ARMS if arm != "code-first"],
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _git_fact(command: list[str]) -> str | None:
    result, _elapsed = _run(["git", *command], cwd=ROOT, timeout=20)
    return result.stdout.strip()[:512] if result.returncode == 0 else None


def _environment_telemetry(
    *,
    model: str,
    protocol_path: Path,
    reference: Path,
    ignore_user_config: bool,
    install: bool,
) -> dict[str, Any]:
    status = _git_fact(["status", "--porcelain"]) or ""
    return {
        "model": model,
        "versions": {
            "codex": _command_version(["codex", "--version"]),
            "viewspec": _command_version([str(VIEWSPEC), "--version"]),
            "python": sys.version.split()[0],
            "node": _command_version(["node", "--version"]),
            "npm": _command_version(["npm", "--version"]),
            "bun": _command_version(["bun", "--version"]),
        },
        "controls": {
            "ignore_user_config": ignore_user_config,
            "sandbox": "workspace-write",
            "network": "runner_does_not_request_network",
            "native_proof_install": install,
        },
        "inputs": {
            "protocol": _file_fact(protocol_path),
            "reference": _file_fact(reference),
            "runner": _file_fact(Path(__file__).resolve()),
            "browser_scorer": _file_fact(BROWSER_SCORER),
            "mutation_manifest": _file_fact(MUTATION_MANIFEST),
            "viewspec_product_tree": _product_tree_fact(),
            "managed_agent_instructions": _managed_instruction_fact(),
            "node_dependency_seed": {
                "sha256": _dependency_seed_hash(),
                "path": str(NODE_MODULE_SEED),
                "lock": _file_fact(NODE_MODULE_SEED.parent / "package-lock.json"),
                "integrations": {
                    PRETEXT_PACKAGE: PRETEXT_VERSION,
                    FREERANGE_PACKAGE: FREERANGE_VERSION,
                },
            },
        },
        "repository": {
            "commit": _git_fact(["rev-parse", "HEAD"]),
            "branch": _git_fact(["branch", "--show-current"]),
            "dirty": bool(status),
            "changed_path_count": len(status.splitlines()),
        },
    }


def _source_files(workspace: Path, arm: str) -> list[Path]:
    if arm == "code-first":
        root = workspace / "submission"
        excluded = {"node_modules", "dist"}
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and not any(
                part.startswith(".") or part in excluded
                for part in path.relative_to(root).parts
            )
        )
    source = workspace / "viewspec.app.json"
    return [source] if source.is_file() else []


def _capture_source(
    *,
    workspace: Path,
    artifact: Path,
    arm: str,
    previous: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str], int]:
    started = time.perf_counter()
    current: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    snapshot_root = artifact / "source"
    for source in _source_files(workspace, arm):
        relative = source.relative_to(workspace).as_posix()
        text = source.read_text(encoding="utf-8", errors="replace")
        current[relative] = text
        destination = snapshot_root / relative
        _write(destination, text)
        files.append(
            {
                "path": relative,
                "bytes": len(text.encode("utf-8")),
                "lines": len(text.splitlines()),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    digest = hashlib.sha256()
    for path, text in sorted(current.items()):
        digest.update(path.encode("utf-8") + b"\0" + text.encode("utf-8") + b"\0")
    diff_lines: list[str] = []
    for path in sorted(set(previous) | set(current)):
        diff_lines.extend(
            difflib.unified_diff(
                previous.get(path, "").splitlines(keepends=True),
                current.get(path, "").splitlines(keepends=True),
                fromfile=f"previous/{path}",
                tofile=f"current/{path}",
            )
        )
    diff_text = "".join(diff_lines)
    _write(artifact / "source.diff", diff_text)
    added = sum(line.startswith("+") and not line.startswith("+++") for line in diff_lines)
    removed = sum(line.startswith("-") and not line.startswith("---") for line in diff_lines)
    semantic: dict[str, Any] | None = None
    if arm != "code-first" and previous.get("viewspec.app.json") is not None and current.get("viewspec.app.json") is not None:
        previous_path = artifact / "previous-viewspec.app.json"
        current_path = snapshot_root / "viewspec.app.json"
        _write(previous_path, previous["viewspec.app.json"])
        result, semantic_ms = _run(
            [str(VIEWSPEC), "diff-app", "--json", "--no-compile-check", str(previous_path), str(current_path)],
            cwd=workspace,
            timeout=120,
        )
        _write(artifact / "semantic-diff.json", result.stdout)
        parsed = _read_json_output(result.stdout)
        semantic = {
            "ok": bool(parsed and parsed.get("ok")),
            "duration_ms": semantic_ms,
            "changed_fields": parsed.get("changed_fields", []) if parsed else [],
            "semantic_summary": parsed.get("semantic_summary", []) if parsed else [],
            "topology_similarity": parsed.get("topology_similarity") if parsed else None,
            "error_codes": [
                item.get("code")
                for item in (parsed.get("errors", []) if parsed else [])
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            ],
        }
    elapsed = round((time.perf_counter() - started) * 1000)
    telemetry = {
        "snapshot_sha256": digest.hexdigest(),
        "file_count": len(files),
        "bytes": sum(item["bytes"] for item in files),
        "lines": sum(item["lines"] for item in files),
        "files": files,
        "delta": {
            "added_lines": added,
            "removed_lines": removed,
            "diff_bytes": len(diff_text.encode("utf-8")),
            "diff_sha256": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
        },
        **({"semantic_diff": semantic} if semantic is not None else {}),
    }
    return telemetry, current, elapsed


def _source_texts(workspace: Path, arm: str) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        for path in _source_files(workspace, arm)
    }


def _restore_source_texts(
    workspace: Path,
    arm: str,
    sources: dict[str, str],
    *,
    prune: bool = False,
) -> None:
    expected = set(sources)
    current = {
        path.relative_to(workspace).as_posix(): path
        for path in _source_files(workspace, arm)
    }
    unexpected = sorted(set(current) - expected)
    if unexpected:
        if not prune:
            raise ValueError(f"source restore found unexpected authored files: {unexpected}")
        for relative in unexpected:
            current[relative].unlink()
    for relative, text in sources.items():
        _write(workspace / relative, text)


def _write_checkpoint(
    *,
    output: Path,
    protocol_path: Path,
    model: str,
    workspace: Path,
    arm: str,
    stage: str,
    next_index: int,
    feedback: str,
    previous_criteria: dict[str, bool],
    session: dict[str, Any],
) -> None:
    sources = _source_texts(workspace, arm)
    payload = checkpoint_envelope(
        {
            "schema_version": 1,
            "protocol_sha256": _sha256(protocol_path),
            "model": model,
            "arm_id": arm,
            "stage": stage,
            "next_index": next_index,
            "source_sha256": source_snapshot_hash(sources),
            "product_tree_sha256": _product_tree_fact()["sha256"],
            "feedback": feedback,
            "previous_criteria": previous_criteria,
        }
    )
    _write(output / "session.partial.json", canonical_json(session))
    _write(output / "checkpoint.json", canonical_json(payload))


def _workspace_instructions(task: AgentEvalTask, arm: str, seed: int) -> str:
    return f"""# Agent UI evaluation workspace

You are participating in a controlled evaluation. Work only in this workspace. Do not search the internet, read
the ViewSpec repository, inspect evaluator output from other arms, or change TASK.md, AGENTS.md, bin/, or
artifacts/. The attached reference image is the only visual reference.

Replicate id: {seed}
Arm: {arm}

{ARM_INSTRUCTIONS[arm]}

Finish each turn only after saving the requested source and running any cheap local validation available to your
arm. Keep your final response short; the evaluator measures the files, not your claims.
"""


def _task_markdown(task: AgentEvalTask) -> str:
    lines = [f"# {task.title}", "", task.brief, "", "## Iterations", ""]
    for index, step in enumerate(task.steps, start=1):
        lines.extend([f"{index}. **{step.id}** (`{step.phase}`) — {step.prompt}", ""])
    return "\n".join(lines)


def _minimal_code_first() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Interface</title>
</head>
<body>
  <main></main>
</body>
</html>
"""


def _activate_code_first_react(workspace: Path) -> dict[str, Any]:
    react = workspace / "submission" / "react"
    if react.exists():
        return {"activated": False, "reason": "already_present"}
    package = {
        "name": "viewspec-eval-code-first-react",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {"build": "vite build"},
        "dependencies": {
            "@tailwindcss/vite": "4.3.0",
            "@vitejs/plugin-react": "6.0.2",
            "react": "19.2.7",
            "react-dom": "19.2.7",
            "tailwindcss": "4.3.0",
            "vite": "8.0.16",
        },
        "devDependencies": {
            "@playwright/test": "1.60.0",
            "@types/node": "25.5.0",
            "@types/react": "19.2.16",
            "@types/react-dom": "19.2.3",
            "typescript": "6.0.3",
        },
    }
    _write(react / "package.json", json.dumps(package, indent=2, sort_keys=True) + "\n")
    _write(react / "index.html", '<div id="root"></div><script type="module" src="/src/main.jsx"></script>\n')
    _write(
        react / "src" / "main.jsx",
        'import React from "react";\nimport {createRoot} from "react-dom/client";\n'
        'import App from "./App.jsx";\nimport "./style.css";\n'
        'createRoot(document.getElementById("root")).render(<App />);\n',
    )
    _write(
        react / "src" / "App.jsx",
        'export default function App(){return <main><p>Implement the matched React target.</p></main>}\n',
    )
    _write(react / "src" / "style.css", "html{font-family:system-ui,sans-serif}\nbody{margin:0}\n")
    return {
        "activated": True,
        "package_sha256": _sha256(react / "package.json"),
        "dependency_seed_sha256": _dependency_seed_hash(),
    }


def _dependency_seed_hash() -> str:
    package = NODE_MODULE_SEED.parent / "package-lock.json"
    return _sha256(package) if package.is_file() else hashlib.sha256(str(NODE_MODULE_SEED).encode()).hexdigest()


def _validate_value_runtime() -> dict[str, Any]:
    lock_path = NODE_MODULE_SEED.parent / "package-lock.json"
    required_packages = {
        PRETEXT_PACKAGE: PRETEXT_VERSION,
        FREERANGE_PACKAGE: FREERANGE_VERSION,
    }
    errors: list[str] = []
    if not lock_path.is_file():
        errors.append(f"missing dependency lock: {lock_path}")
    for package_name, version in required_packages.items():
        package_path = NODE_MODULE_SEED.joinpath(*package_name.split("/"), "package.json")
        try:
            installed = json.loads(package_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"{package_name} is unavailable: {exc}")
            continue
        if installed.get("name") != package_name or installed.get("version") != version:
            errors.append(f"{package_name} must be installed exactly at {version}")
    vite = NODE_MODULE_SEED / ".bin" / "vite"
    if not vite.is_file():
        errors.append("the pinned Vite executable is unavailable")
    bun = shutil.which("bun")
    if bun is None:
        errors.append("the pinned Deep proof requires Bun on PATH")
    if errors:
        raise RuntimeError("V2.1 value runtime preflight failed: " + "; ".join(errors))
    return {
        "ok": True,
        "lock": str(lock_path),
        "lock_sha256": _sha256(lock_path),
        "packages": required_packages,
        "bun": bun,
    }


def _validate_pinned_react_package(package: dict[str, Any]) -> None:
    template_package_path = ROOT / "src" / "viewspec" / "host_verify_template" / "package.json"
    template_package = json.loads(template_package_path.read_text(encoding="utf-8"))
    optional = {
        "dependencies": {PRETEXT_PACKAGE: PRETEXT_VERSION},
        "devDependencies": {FREERANGE_PACKAGE: FREERANGE_VERSION},
    }
    for key in ("dependencies", "devDependencies"):
        expected = template_package.get(key)
        actual = package.get(key)
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise ValueError(f"package.json {key} does not match the pinned shared target contract")
        if any(actual.get(name) != version for name, version in expected.items()):
            raise ValueError(f"package.json {key} does not match the pinned shared target contract")
        extras = {name: version for name, version in actual.items() if name not in expected}
        if extras not in ({}, optional[key]):
            raise ValueError(f"package.json {key} does not match the pinned shared target contract")


def _react_source_from_proof(proof: dict[str, Any] | None, artifact: Path) -> Path:
    paths = proof.get("paths") if isinstance(proof, dict) else None
    react_app = paths.get("react_app") if isinstance(paths, dict) else None
    if isinstance(react_app, str) and react_app:
        return Path(react_app)
    return artifact / "missing-react-source"


def _build_react_target(source: Path, artifact: Path) -> tuple[dict[str, Any], int, str]:
    started = time.perf_counter()
    node_modules = source / "node_modules"
    package_path = source / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        _validate_pinned_react_package(package)
        if not node_modules.exists():
            materialize_prebuilt_node_modules(node_modules, NODE_MODULE_SEED)
        result, command_ms = _run(
            [str(node_modules / ".bin" / "vite"), "build"],
            cwd=source,
            timeout=180,
        )
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = subprocess.CompletedProcess([], 1, str(exc))
        command_ms = 0
    log = result.stdout
    _write(artifact / "react-build.log", log)
    source_dist = source / "dist"
    retained = artifact / "react-dist"
    if result.returncode == 0 and source_dist.joinpath("index.html").is_file():
        shutil.copytree(source_dist, retained)
    elapsed = round((time.perf_counter() - started) * 1000)
    files = []
    if retained.is_dir():
        files = [
            {
                "path": path.relative_to(retained).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(retained.rglob("*"))
            if path.is_file()
        ]
    return (
        {
            "ok": result.returncode == 0 and bool(files),
            "exit_code": result.returncode,
            "duration_ms": elapsed,
            "command_duration_ms": command_ms,
            "command": "node_modules/.bin/vite build",
            "dependency_seed_sha256": _dependency_seed_hash(),
            "package_sha256": _sha256(package_path) if package_path.is_file() else None,
            "dist": {"path": str(retained), "files": files},
        },
        elapsed,
        log,
    )


def _install_managed_agent_instructions(workspace: Path) -> None:
    """Materialize the shipped managed instruction block into the arm workspace.

    ViewSpec arms must evaluate the guidance ViewSpec actually ships, not a copy
    maintained inside this runner. `init-agent` appends its own marked block to the
    runner-authored AGENTS.md, so arm rules stay first and the product block follows.
    Agents still never read the ViewSpec repository; the instructions are local files,
    exactly as a real adopter receives them.
    """

    result, _elapsed = _run(
        [str(VIEWSPEC), "init-agent", "--target", "codex"],
        cwd=workspace,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"Unable to install managed ViewSpec agent instructions:\n{result.stdout}")


def _prepare_workspace(output: Path, task: AgentEvalTask, arm: str, seed: int) -> Path:
    workspace = output / "workspace"
    workspace.mkdir()
    _write(workspace / "AGENTS.md", _workspace_instructions(task, arm, seed))
    _write(workspace / "TASK.md", _task_markdown(task))
    wrapper = f'#!/bin/sh\nexec "{VIEWSPEC}" "$@"\n'
    _write(workspace / "bin" / "viewspec", wrapper, executable=True)
    if arm == "code-first":
        _write(workspace / "submission" / "index.html", _minimal_code_first())
    else:
        result, _elapsed = _run(
            [str(VIEWSPEC), "init-app", "--template", "react-app", "--out", "viewspec.app.json"],
            cwd=workspace,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(f"Unable to scaffold the evaluation AppBundle:\n{result.stdout}")
        _install_managed_agent_instructions(workspace)
    git, _elapsed = _run(["git", "init", "-q"], cwd=workspace, timeout=30)
    if git.returncode:
        raise RuntimeError(f"Unable to initialize evaluation workspace:\n{git.stdout}")
    return workspace


def _score_spec(task: AgentEvalTask, step: AgentEvalStep, path: Path) -> None:
    payload = step.to_score_spec()
    payload["visual_anchors"] = list(task.visual_anchors)
    if task.primary_heading is not None:
        payload["primary_heading"] = task.primary_heading
    _write(path, canonical_json(payload))


def _browser_score(
    *,
    candidate: Path,
    candidate_entry: str,
    reference: Path,
    task: AgentEvalTask,
    step: AgentEvalStep,
    step_index: int,
    artifact_dir: Path,
) -> tuple[dict[str, Any], int, str]:
    spec_path = artifact_dir / "score-spec.json"
    report_path = artifact_dir / "browser-score.json"
    evidence = artifact_dir / "browser-evidence"
    _score_spec(task, step, spec_path)
    command = [
        "node",
        str(BROWSER_SCORER),
        "--candidate",
        str(candidate),
        "--candidate-entry",
        candidate_entry,
        "--reference",
        str(reference),
        "--reference-step",
        str(step_index),
        "--spec",
        str(spec_path),
        "--out",
        str(report_path),
        "--evidence",
        str(evidence),
    ]
    result, elapsed = _run(command, cwd=ROOT, timeout=120)
    if report_path.is_file():
        score = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        score = {
            "schema_version": 1,
            "ok": False,
            "passed": 0,
            "total": 1,
            "dimensions": {},
            "errors": [result.stdout[-4000:]],
        }
    return score, elapsed, result.stdout


def _render_reference(reference: Path, task: AgentEvalTask, output: Path) -> Path:
    artifact = output / "reference-render"
    artifact.mkdir()
    score, _elapsed, browser_output = _browser_score(
        candidate=reference.parent,
        candidate_entry=reference.name,
        reference=reference,
        task=task,
        step=task.steps[0],
        step_index=0,
        artifact_dir=artifact,
    )
    if not score.get("ok"):
        raise RuntimeError(f"The checked-in reference does not pass its own initial acceptance spec:\n{browser_output}")
    image = artifact / "browser-evidence" / f"{REFERENCE_IMAGE_WIDTH}x{REFERENCE_IMAGE_HEIGHT}.png"
    if not image.is_file():
        raise RuntimeError("The browser scorer did not capture the desktop reference")
    return image


def _initial_prompt(task: AgentEvalTask, arm: str, step: AgentEvalStep) -> str:
    contract = (
        "Save the complete result to submission/index.html."
        if arm == "code-first"
        else "Save the complete semantic source to viewspec.app.json. Do not edit generated output."
    )
    return f"""Run evaluation step 1/{len(task.steps)}: {step.id}.

Lifecycle phase: {step.phase}

Product brief: {task.brief}

Change request: {step.prompt}

{contract} Inspect the attached reference carefully. Implement the interface now, validate what you can locally,
and do not merely propose code or describe future work."""


def _followup_prompt(
    task: AgentEvalTask,
    arm: str,
    step: AgentEvalStep,
    step_index: int,
    previous_feedback: str,
) -> str:
    contract = (
        "submission/index.html and the matched submission/react/src/App.jsx target"
        if arm == "code-first" and step.phase in {"leverage", "assurance", "repair"}
        else "submission/index.html"
        if arm == "code-first"
        else "viewspec.app.json"
    )
    return f"""Run evaluation step {step_index + 1}/{len(task.steps)}: {step.id}.

Lifecycle phase: {step.phase}

Change request: {step.prompt}

The prior evaluator feedback was:
{previous_feedback}

Update {contract} in place. Preserve every accepted prior requirement, fix relevant regressions, validate locally,
and finish with the working source saved to disk."""


def _codex_turn(
    *,
    workspace: Path,
    prompt: str,
    events_path: Path,
    reference_image: Path | None,
    thread_id: str | None,
    model: str | None,
    ignore_user_config: bool,
    timeout: int,
) -> tuple[dict[str, Any], int, int, str]:
    if thread_id is None:
        command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "-C",
            str(workspace),
        ]
        if ignore_user_config:
            command.append("--ignore-user-config")
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        if reference_image is not None:
            command.extend(["-i", str(reference_image)])
    else:
        command = ["codex", "exec", "resume", "--json", "--skip-git-repo-check"]
        if ignore_user_config:
            command.append("--ignore-user-config")
        command.extend(["-c", 'sandbox_mode="workspace-write"'])
        if model:
            command.extend(["--model", model])
        command.extend([thread_id, prompt])
    result, elapsed = _run(
        command,
        cwd=workspace,
        timeout=timeout,
        merge_stderr=False,
    )
    events_path.parent.mkdir(parents=True, exist_ok=True)
    strict_events, invalid_stdout = _strict_jsonl(result.stdout)
    diagnostics = invalid_stdout
    if result.stderr:
        diagnostics += "[stderr]\n" + result.stderr
        if not diagnostics.endswith("\n"):
            diagnostics += "\n"
    _write(events_path, strict_events)
    _write(_diagnostics_path(events_path), diagnostics)
    parsed = parse_codex_jsonl(strict_events)
    return parsed, elapsed, result.returncode, result.stdout


def _read_json_output(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        value = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _proof_feedback(proof: dict[str, Any] | None) -> str:
    if proof is None:
        return "No native proof report was produced."
    if proof.get("ok") is True:
        details = ["Native proof passed."]
        for key in ("static_analysis", "text_layout"):
            value = proof.get(key)
            if isinstance(value, dict):
                details.append(f"{key}: {value.get('status')}")
        return " ".join(details)
    errors = proof.get("errors")
    if not isinstance(errors, list):
        return "Native proof failed without structured errors."
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in errors:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("code") or "UNKNOWN"), str(item.get("path") or ""))
        grouped.setdefault(key, []).append(item)
    rendered: list[str] = []
    for (code, path), matching in list(grouped.items())[:6]:
        representative = matching[0]
        context = f" at {path}" if path else ""
        remainder = f" (+{len(matching) - 1} similar)" if len(matching) > 1 else ""
        rendered.append(
            f"{code}{context}: {representative.get('message')}{remainder}"
        )
    return "Native proof failed: " + " | ".join(rendered)


def _proof_telemetry(proof: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(proof, dict):
        return None
    paths = proof.get("paths") if isinstance(proof.get("paths"), dict) else {}
    artifacts: dict[str, Any] = {}
    for key in ("report", "proof_summary", "support_bundle", "analysis_evidence"):
        raw = paths.get(key)
        if isinstance(raw, str):
            fact = _file_fact(Path(raw))
            if fact is not None:
                artifacts[key] = fact
    analyses: dict[str, Any] = {}
    for name, key in (("freerange", "static_analysis"), ("pretext", "text_layout")):
        report = proof.get(key)
        if not isinstance(report, dict):
            continue
        analyses[name] = {
            "status": report.get("status"),
            "coverage": report.get("coverage") if isinstance(report.get("coverage"), dict) else {},
            "engine": report.get("engine") if isinstance(report.get("engine"), dict) else {},
            **({"cache": report["cache"]} if isinstance(report.get("cache"), dict) else {}),
        }
    host = proof.get("host_report") if isinstance(proof.get("host_report"), dict) else {}
    return {
        "ok": bool(proof.get("ok")),
        "target": proof.get("target"),
        "proof_level": proof.get("proof_level"),
        "error_codes": [
            item.get("code")
            for item in (proof.get("errors") if isinstance(proof.get("errors"), list) else [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        ],
        "phases": host.get("phases") if isinstance(host.get("phases"), dict) else {},
        "timings_ms": proof.get("timings_ms") if isinstance(proof.get("timings_ms"), dict) else {},
        "analyses": analyses,
        "artifacts": artifacts,
    }


def _criterion_states(
    score: dict[str, Any],
    *,
    excluded_dimensions: set[str] | None = None,
) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for viewport in score.get("viewports", []) if isinstance(score.get("viewports"), list) else []:
        if not isinstance(viewport, dict):
            continue
        size = viewport.get("viewport") if isinstance(viewport.get("viewport"), dict) else {}
        for item in viewport.get("criteria", []) if isinstance(viewport.get("criteria"), list) else []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("passed"), bool)
                and item.get("dimension") not in (excluded_dimensions or set())
            ):
                states[f"{size.get('width')}x{size.get('height')}:{item['id']}"] = item["passed"]
    return states


def _eligibility_rank(eligibility: dict[str, Any]) -> tuple[int, float, int, int, int, float]:
    hooks = eligibility.get("stable_hooks")
    counts = hooks.get("counts") if isinstance(hooks, dict) else {}
    if isinstance(counts, dict) and counts and all(isinstance(value, dict) for value in counts.values()):
        hook_count = sum(count == 1 for target in counts.values() for count in target.values())
    elif isinstance(counts, dict):
        hook_count = sum(count == 1 for count in counts.values())
    else:
        hook_count = 0
    targets = eligibility.get("targets") if isinstance(eligibility.get("targets"), list) else []
    healthy_targets = sum(
        item.get("build_ok") is True and item.get("passed") is True
        for item in targets
        if isinstance(item, dict)
    )
    return (
        int(eligibility.get("eligible") is True),
        float(eligibility.get("functional_acceptance") or 0.0),
        hook_count,
        int(eligibility.get("native_proof_healthy") is True),
        healthy_targets,
        float(eligibility.get("layout_fidelity") or 0.0),
    )


def _selected_delivery_turn(session: dict[str, Any]) -> dict[str, Any]:
    lifecycle = session.get("turns")
    if not isinstance(lifecycle, list) or not lifecycle:
        raise ValueError("session has no lifecycle delivery turn")
    qualification = session.get("qualification")
    selected = qualification.get("selected_turn") if isinstance(qualification, dict) else None
    if not isinstance(selected, dict):
        turns = session.get("qualification_turns")
        return turns[-1] if isinstance(turns, list) and turns else lifecycle[-1]
    kind = selected.get("kind")
    index = selected.get("index")
    collection = lifecycle if kind == "lifecycle" else session.get("qualification_turns")
    if (
        kind not in {"lifecycle", "qualification"}
        or not isinstance(collection, list)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(collection)
    ):
        raise ValueError("qualification selected_turn is invalid")
    return collection[index]


def _criterion_transition(previous: dict[str, bool], current: dict[str, bool]) -> dict[str, list[str]]:
    return {
        "lost": sorted(key for key, passed in previous.items() if passed and current.get(key) is False),
        "newly_passed": sorted(key for key, passed in current.items() if passed and key not in previous),
        "recovered": sorted(key for key, passed in current.items() if passed and previous.get(key) is False),
    }


def _score_feedback(
    score: dict[str, Any],
    *,
    layout_target: float | None = None,
) -> str:
    layout_by_viewport = {
        int(viewport["viewport"]["width"]): float(viewport["layout_fidelity"])
        for viewport in score.get("viewports", [])
        if isinstance(viewport, dict)
        and isinstance(viewport.get("viewport"), dict)
        and isinstance(viewport["viewport"].get("width"), int)
        and isinstance(viewport.get("layout_fidelity"), (int, float))
    }
    layout_feedback = ""
    if layout_by_viewport:
        values = ", ".join(
            f"{width}px={value:.6f}"
            for width, value in sorted(layout_by_viewport.items())
        )
        floor = min(layout_by_viewport.values())
        target = (
            f", target={layout_target:.6f}, met={floor >= layout_target}"
            if layout_target is not None
            else ""
        )
        layout_feedback = f" Reference layout by viewport: {values}; floor={floor:.6f}{target}."
    if score.get("ok") is True:
        return (
            f"Browser acceptance passed {score.get('passed')}/{score.get('total')}."
            + layout_feedback
        )
    failures: list[str] = []
    viewports = score.get("viewports")
    if isinstance(viewports, list):
        for viewport in viewports:
            if not isinstance(viewport, dict):
                continue
            size = viewport.get("viewport", {})
            for item in viewport.get("criteria", []):
                if isinstance(item, dict) and item.get("passed") is False:
                    detail = ""
                    criterion_id = str(item.get("id") or "")
                    if criterion_id.startswith("anchor:"):
                        candidate = item.get("candidate_anchor")
                        reference = item.get("reference_anchor")

                        def geometry(value: Any) -> str:
                            if not isinstance(value, dict):
                                return "missing"
                            return "/".join(
                                f"{float(value.get(key, 0)):.2f}"
                                for key in ("x", "y", "width", "height")
                            )

                        similarity = item.get("similarity")
                        detail = (
                            f" sim={float(similarity):.2f}" if isinstance(similarity, (int, float)) else ""
                        ) + f" candidate={geometry(candidate)} reference={geometry(reference)}"
                    elif criterion_id.startswith("text-geometry:"):
                        expected = item.get("expected")
                        observed = item.get("observed")
                        if isinstance(observed, dict):
                            detail = (
                                f" observed(lines={observed.get('line_count')},width={observed.get('client_width')},"
                                f"fragmented={len(observed.get('fragmented_words', []))})"
                            )
                        if isinstance(expected, dict):
                            detail += (
                                f" expected(lines={expected.get('minimum_lines')}..{expected.get('maximum_lines')},"
                                f"min_width={expected.get('minimum_width_px')})"
                            )
                    failures.append(f"{size.get('width')}px {criterion_id}{detail}")
    compact = ", ".join(dict.fromkeys(failures[:12])) or "browser scorer did not produce criteria"
    return (
        f"Browser acceptance passed {score.get('passed', 0)}/{score.get('total', 0)}; "
        f"failures: {compact}." + layout_feedback
    )


def _stable_hook_feedback(hooks: dict[str, Any]) -> str:
    if hooks.get("ok") is True:
        return "Stable evaluation hooks passed for both maintained targets."
    errors = hooks.get("errors")
    rendered = (
        "; ".join(str(item) for item in errors[:12])
        if isinstance(errors, list) and errors
        else "stable evaluation hooks were unhealthy"
    )
    return f"Stable evaluation hooks failed: {rendered}."


def _dimension_score(score: dict[str, Any], *, excluded: set[str] | None = None) -> float:
    excluded = excluded or set()
    criteria = [
        item
        for viewport in score.get("viewports", [])
        if isinstance(viewport, dict)
        for item in viewport.get("criteria", [])
        if isinstance(item, dict) and item.get("dimension") not in excluded
    ]
    return (
        sum(item.get("passed") is True for item in criteria) / len(criteria)
        if criteria
        else 0.0
    )


def _layout_fidelity(score: dict[str, Any]) -> float | None:
    values = [
        viewport.get("layout_fidelity")
        for viewport in score.get("viewports", [])
        if isinstance(viewport, dict)
        and isinstance(viewport.get("layout_fidelity"), (int, float))
    ]
    return min(values) if values else None


def _parity_by_viewport(score: dict[str, Any]) -> dict[str, float]:
    return {
        str(viewport["viewport"]["width"]): float(viewport["layout_fidelity"])
        for viewport in score.get("viewports", [])
        if isinstance(viewport, dict)
        and isinstance(viewport.get("viewport"), dict)
        and isinstance(viewport["viewport"].get("width"), int)
        and isinstance(viewport.get("layout_fidelity"), (int, float))
    }


def _evaluate_turn(
    *,
    workspace: Path,
    output: Path,
    protocol_path: Path,
    task: AgentEvalTask,
    arm: str,
    step: AgentEvalStep,
    step_index: int,
    install: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, int],
    str,
    list[dict[str, Any]],
]:
    artifact = output / "artifacts" / f"{step_index + 1:02d}-{step.id}"
    artifact.mkdir(parents=True, exist_ok=True)
    phases_ms: dict[str, int] = {}
    logs: list[str] = []
    proof: dict[str, Any] | None = None
    target_trials: list[dict[str, Any]] = []
    presentation_feedback = ""
    if arm == "code-first":
        candidate = workspace / "submission"
        if not (candidate / "index.html").is_file():
            score = {"schema_version": 1, "ok": False, "passed": 0, "total": 1, "dimensions": {}, "errors": ["submission/index.html is missing"]}
            return score, proof, phases_ms, "submission/index.html is missing.", target_trials
    else:
        source = workspace / "viewspec.app.json"
        site = artifact / "site"
        compile_result, compile_ms = _run(
            [
                str(VIEWSPEC),
                "compile-app",
                str(source),
                "--target",
                "html-tailwind-app",
                "--out",
                str(site),
                "--json",
            ],
            cwd=workspace,
            timeout=120,
        )
        phases_ms["compile"] = compile_ms
        _write(artifact / "compile.log", compile_result.stdout)
        logs.append(compile_result.stdout)
        compile_payload = _read_json_output(compile_result.stdout)
        diagnostics = (
            compile_payload.get("presentation_plan_diagnostics")
            if isinstance(compile_payload, dict)
            else None
        )
        if isinstance(diagnostics, list) and diagnostics:
            rendered = [
                f"{item.get('code')}: {item.get('message')}"
                for item in diagnostics[:4]
                if isinstance(item, dict)
            ]
            if rendered:
                presentation_feedback = " PresentationPlan: " + " | ".join(rendered)
        candidate = site
        proof_dir = artifact / "proof"
        proof_command = [
            str(VIEWSPEC),
            "prove-app",
            "--app",
            str(source),
            "--out",
            str(proof_dir),
            "--target",
            "react-tailwind-app",
            "--json",
        ]
        if install:
            proof_command.append("--install")
        if arm == "viewspec-deep":
            proof_command.extend(["--freerange", "--pretext"])
        proof_env = os.environ.copy()
        proof_env["VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR"] = str(NODE_MODULE_SEED)
        proof_result, proof_ms = _run(
            proof_command,
            cwd=workspace,
            timeout=420,
            env=proof_env,
        )
        phases_ms["native_proof"] = proof_ms
        _write(artifact / "proof.log", proof_result.stdout)
        logs.append(proof_result.stdout)
        report_path = proof_dir / "app_proof_report.json"
        if report_path.is_file():
            proof = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            proof = _read_json_output(proof_result.stdout)
        if compile_result.returncode or not (candidate / "index.html").is_file():
            score = {
                "schema_version": 1,
                "ok": False,
                "passed": 0,
                "total": 1,
                "dimensions": {},
                "errors": ["AppBundle did not compile to a browser artifact"],
            }
            feedback = "Compilation failed. " + _proof_feedback(proof) + presentation_feedback
            return score, proof, phases_ms, feedback, target_trials
    reference = _reference_path(protocol_path, task)
    score, browser_ms, browser_output = _browser_score(
        candidate=candidate,
        candidate_entry="index.html",
        reference=reference,
        task=task,
        step=step,
        step_index=step_index,
        artifact_dir=artifact,
    )
    phases_ms["browser_score"] = browser_ms
    _write(artifact / "browser.log", browser_output)
    static_functional = _dimension_score(score, excluded={"layout_fidelity"})
    static_trial = {
        "id": "static-shell",
        "applicable": True,
        "build": {
            "ok": True,
            "entry": str(candidate / "index.html"),
            "command": "authored-static"
            if arm == "code-first"
            else "viewspec compile-app --target html-tailwind-app",
            "duration_ms": phases_ms.get("compile", 0),
            "entry_sha256": _sha256(candidate / "index.html"),
        },
        "functional_acceptance": static_functional,
        "layout_fidelity": _layout_fidelity(score),
        "passed": static_functional == 1.0,
        "parity": 1.0,
        "parity_by_viewport": {"390": 1.0, "768": 1.0, "1440": 1.0},
        "commands": ["local Chromium score"],
        "score_artifact": str(artifact / "browser-score.json"),
    }
    target_trials.append(static_trial)
    if step.phase in {"leverage", "assurance", "repair"}:
        react_source = (
            workspace / "submission" / "react"
            if arm == "code-first"
            else _react_source_from_proof(proof, artifact)
        )
        build, react_build_ms, react_build_log = _build_react_target(
            react_source,
            artifact,
        )
        phases_ms["react_build"] = react_build_ms
        react_dist = artifact / "react-dist"
        if build["ok"]:
            react_score, react_browser_ms, react_browser_output = _browser_score(
                candidate=react_dist,
                candidate_entry="index.html",
                reference=reference,
                task=task,
                step=step,
                step_index=step_index,
                artifact_dir=artifact / "react-score",
            )
            phases_ms["react_browser_score"] = react_browser_ms
            _write(artifact / "react-browser.log", react_browser_output)
            parity_score, parity_ms, parity_output = _browser_score(
                candidate=react_dist,
                candidate_entry="index.html",
                reference=candidate / "index.html",
                task=task,
                step=step,
                step_index=step_index,
                artifact_dir=artifact / "parity-score",
            )
            phases_ms["direct_parity_score"] = parity_ms
            _write(artifact / "parity-browser.log", parity_output)
            react_functional = _dimension_score(
                react_score,
                excluded={"layout_fidelity"},
            )
            parity = _layout_fidelity(parity_score)
        else:
            react_score = {
                "schema_version": 1,
                "ok": False,
                "passed": 0,
                "total": 1,
                "errors": [react_build_log[-4000:]],
            }
            react_functional = 0.0
            parity = None
        target_trials.append(
            {
                "id": "native-react",
                "applicable": True,
                "build": build,
                "functional_acceptance": react_functional,
                "layout_fidelity": _layout_fidelity(react_score),
                "layout_by_viewport": (
                    _parity_by_viewport(react_score) if build["ok"] else {}
                ),
                "passed": build["ok"] and react_functional == 1.0,
                "parity": parity,
                "parity_by_viewport": (
                    _parity_by_viewport(parity_score) if build["ok"] else {}
                ),
                "commands": [
                    "node_modules/.bin/vite build",
                    "local Chromium target score",
                    "local Chromium direct parity score",
                ],
                "score_artifact": str(artifact / "react-score" / "browser-score.json"),
                "parity_artifact": str(artifact / "parity-score" / "browser-score.json"),
            }
        )
    protocol = load_agent_eval_protocol(protocol_path)
    layout_target = float(
        protocol.success_criteria.get("minimum_layout_fidelity", 0.0)
    )
    feedback = _score_feedback(score, layout_target=layout_target)
    if arm != "code-first":
        feedback += " " + _proof_feedback(proof) + presentation_feedback
    if len(target_trials) == 2:
        react_trial = target_trials[1]
        feedback += (
            f" Matched React target passed={react_trial['passed']};"
            f" layout_floor={react_trial['layout_fidelity']};"
            f" parity={react_trial['parity']}."
        )
        hooks = validate_stable_hooks(arm, _source_texts(workspace, arm))
        feedback += " " + _stable_hook_feedback(hooks)
    return score, proof, phases_ms, feedback, target_trials


def _failed_criterion_ids(score: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item["id"])
            for viewport in score.get("viewports", [])
            if isinstance(viewport, dict)
            for item in viewport.get("criteria", [])
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("passed") is False
        }
    )


def _observed_detectors(
    trial_id: str,
    *,
    arm: str,
    score: dict[str, Any],
    proof: dict[str, Any] | None,
) -> list[str]:
    failed = _failed_criterion_ids(score)
    proof_errors = proof.get("errors") if isinstance(proof, dict) else None
    error_items = [
        item
        for item in (proof_errors if isinstance(proof_errors, list) else [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ]

    def has_proof_error(code: str, *markers: str) -> bool:
        for item in error_items:
            if item.get("code") != code:
                continue
            rendered = json.dumps(item, sort_keys=True)
            if all(marker in rendered for marker in markers):
                return True
        return False

    observed: list[str] = []
    if trial_id in {"break-escalation-action", "break-escalation-visibility"}:
        browser_detected = any(
            item.startswith("interaction:Show escalation guide") for item in failed
        )
        if browser_detected:
            observed.append("browser:interaction:Show escalation guide")
        if (
            trial_id == "break-escalation-action"
            and has_proof_error(
                "APP_STATE_TRIGGER_ACTION_MISSING",
                "broken_escalation_action",
            )
        ):
            observed.append("compile-or-replay:reveal_escalation_guide")
        elif (
            trial_id == "break-escalation-visibility"
            and (
                has_proof_error(
                    "APP_VISIBILITY_REPLAY_MISMATCH",
                    "show_escalation_panel",
                )
                or (
                    browser_detected
                    and has_proof_error("APP_STATE_REPLAY_ASSERTION_FAILED")
                )
            )
        ):
            observed.append("replay-or-browser:show_escalation_panel")
    elif trial_id == "corrupt-reviewed-count":
        browser_detected = any(
            item.startswith("interaction:Record review") for item in failed
        )
        if browser_detected:
            observed.append("browser:interaction:Record review")
        if has_proof_error(
            "APP_STATE_REPLAY_STATE_MISMATCH",
            "increment_reviewed_count",
            "reviewed_count",
        ) or (
            browser_detected
            and has_proof_error("APP_STATE_REPLAY_ASSERTION_FAILED")
        ):
            observed.append("replay:increment_reviewed_count")
    elif trial_id == "duplicate-j207-resource":
        if any(
            item in {"resource:job-j207", "unique-text:J-207"}
            for item in failed
        ):
            observed.append("browser:unique-hook:job-j207")
        if has_proof_error("APP_RESOURCE_BINDING_DUPLICATE_RECORD_ID"):
            observed.append("compile:resource-identity")
    elif trial_id == "break-j205-mobile-geometry":
        if any(item.startswith("text-geometry:") for item in failed):
            observed.append("browser:text-geometry:J-205")
            if arm == "viewspec-deep":
                observed.append("pretext-or-browser:text-geometry:J-205")
    return sorted(set(observed))


def _evaluation_healthy(
    *,
    arm: str,
    score: dict[str, Any],
    proof: dict[str, Any] | None,
    targets: list[dict[str, Any]],
    hooks: dict[str, Any],
) -> bool:
    return _eligibility_report(
        arm=arm,
        score=score,
        proof=proof,
        targets=targets,
        hooks=hooks,
    )["eligible"]


def _eligibility_feedback(
    feedback: str,
    eligibility: dict[str, Any],
    *,
    label: str,
) -> str:
    status = "passed" if eligibility.get("eligible") is True else "failed"
    reasons = eligibility.get("reasons")
    rendered_reasons = (
        "; ".join(str(item) for item in reasons[:8])
        if isinstance(reasons, list) and reasons
        else "none"
    )
    return f"{feedback} {label} {status}; reasons: {rendered_reasons}.".strip()


def _eligibility_report(
    *,
    arm: str,
    score: dict[str, Any],
    proof: dict[str, Any] | None,
    targets: list[dict[str, Any]],
    hooks: dict[str, Any],
) -> dict[str, Any]:
    functional_acceptance = _dimension_score(score, excluded={"layout_fidelity"})
    layout_fidelity = _layout_fidelity(score)
    proof_healthy = arm == "code-first" or (
        isinstance(proof, dict) and proof.get("ok") is True
    )
    target_facts = [
        {
            "id": item.get("id"),
            "build_ok": isinstance(item.get("build"), dict)
            and item["build"].get("ok") is True,
            "functional_acceptance": item.get("functional_acceptance"),
            "passed": item.get("passed") is True,
        }
        for item in targets
    ]
    targets_healthy = len(targets) == 2 and all(
        item["build_ok"] and item["passed"] for item in target_facts
    )
    reasons: list[str] = []
    if functional_acceptance != 1.0:
        reasons.append(f"functional acceptance was {functional_acceptance:.6f}, expected 1.0")
    if hooks.get("ok") is not True:
        errors = hooks.get("errors")
        if isinstance(errors, list):
            reasons.extend(f"stable hook: {item}" for item in errors if isinstance(item, str))
        else:
            reasons.append("stable evaluation hooks were unhealthy")
    if not proof_healthy:
        reasons.append("native proof was unhealthy")
    if len(targets) != 2:
        reasons.append(f"target trial count was {len(targets)}, expected 2")
    for target in target_facts:
        if not target["build_ok"]:
            reasons.append(f"target {target['id']} build was unhealthy")
        elif not target["passed"]:
            reasons.append(
                f"target {target['id']} functional acceptance was "
                f"{target['functional_acceptance']}, expected 1.0"
            )
    return {
        "eligible": (
            functional_acceptance == 1.0
            and hooks.get("ok") is True
            and proof_healthy
            and targets_healthy
        ),
        "functional_acceptance": functional_acceptance,
        "layout_fidelity": layout_fidelity,
        "stable_hooks": hooks,
        "native_proof_healthy": proof_healthy,
        "targets": target_facts,
        "reasons": reasons,
    }


def _copy_isolated_workspace(source: Path, destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {".git", "node_modules", "dist", "artifacts"}}

    shutil.copytree(source, destination, ignore=ignore)


def _evaluation_infrastructure_error(
    *,
    arm: str,
    trial_id: str,
    evaluation: tuple[
        dict[str, Any],
        dict[str, Any] | None,
        dict[str, int],
        str,
        list[dict[str, Any]],
    ],
) -> str | None:
    score, proof = evaluation[0], evaluation[1]
    has_viewports = isinstance(score.get("viewports"), list) and bool(score["viewports"])
    attributed_detector = arm != "code-first" and bool(
        _observed_detectors(
            trial_id,
            arm=arm,
            score=score,
            proof=proof,
        )
    )
    if not has_viewports and not attributed_detector:
        return "browser_or_scorer_did_not_produce_viewports"
    return None


def _repair_prompt(
    task: AgentEvalTask,
    arm: str,
    feedback: str,
) -> str:
    contract = (
        "Repair both submission/index.html and submission/react/src/App.jsx."
        if arm == "code-first"
        else "Repair viewspec.app.json only; do not edit generated artifacts."
    )
    return f"""Repair the provided interface against the complete contract in TASK.md.

The compact evaluator feedback is:
{feedback}

{contract} Preserve every previously accepted functional criterion, both build targets, stable evaluation
identities, and responsive behavior. Validate locally where possible and save the repaired source. You have one
turn; do not inspect paths outside this isolated workspace."""


def _qualification_prompt(
    *,
    arm: str,
    attempt: int,
    maximum: int,
    evaluator_feedback: str,
    eligibility: dict[str, Any],
    layout_target: float,
) -> str:
    contract = (
        "Maintain submission/index.html and submission/react/src/App.jsx as matched targets."
        if arm == "code-first"
        else "Edit viewspec.app.json only; generated artifacts are evaluator-owned."
    )
    compact = canonical_json(
        {
            "functional_acceptance": eligibility["functional_acceptance"],
            "layout_fidelity": eligibility.get("layout_fidelity"),
            "layout_fidelity_target": layout_target,
            "stable_hooks": eligibility["stable_hooks"],
            "native_proof_healthy": eligibility["native_proof_healthy"],
            "targets": eligibility["targets"],
            "reasons": eligibility["reasons"],
        }
    ).strip()
    return f"""Baseline qualification attempt {attempt} of {maximum}.

The complete product contract remains in TASK.md. Repair every remaining non-layout functional criterion, stable
evaluation identity, applicable native proof, and static/React target build. Then improve reference-anchor layout
fidelity to at least {layout_target:.4f} without regressing those gates. Layout remains separate from assurance
eligibility and will not suppress value trials after this bounded refinement; responsive clipping and text-geometry
contracts are functional requirements.

Compact evaluator feedback:
{evaluator_feedback}

Machine-readable eligibility feedback:
{compact}

{contract} Preserve all already healthy criteria and finish only after the available local checks pass."""


def _run_baseline_qualification(
    *,
    args: argparse.Namespace,
    protocol: AgentEvalProtocol,
    protocol_path: Path,
    task: AgentEvalTask,
    workspace: Path,
    output: Path,
    session: dict[str, Any],
    final_turn: dict[str, Any],
    thread_id: str | None,
    feedback: str,
    previous_source: dict[str, str],
    previous_criteria: dict[str, bool],
) -> tuple[
    dict[str, Any],
    str | None,
    str,
    dict[str, str],
    dict[str, bool],
]:
    qualification_turns = session.setdefault("qualification_turns", [])
    if not isinstance(qualification_turns, list):
        raise ValueError("session qualification_turns must be an array")
    if qualification_turns:
        final_turn = _selected_delivery_turn(session)
    selected_sources = _source_texts(workspace, args.arm)
    selected_feedback = feedback
    selected_criteria = _criterion_states(final_turn["score"])
    selected_functional_criteria = _criterion_states(
        final_turn["score"],
        excluded_dimensions={"layout_fidelity"},
    )
    previous_source = selected_sources
    previous_criteria = selected_criteria
    hooks = validate_stable_hooks(args.arm, selected_sources)
    eligibility = _eligibility_report(
        arm=args.arm,
        score=final_turn["score"],
        proof=final_turn.get("proof"),
        targets=final_turn.get("target_trials", []),
        hooks=hooks,
    )
    layout_target = float(protocol.success_criteria.get("minimum_layout_fidelity", 0.0))
    layout_fidelity = float(eligibility.get("layout_fidelity") or 0.0)
    quality_satisfied = eligibility["eligible"] and layout_fidelity >= layout_target
    qualification = session.setdefault(
        "qualification",
        {
            "schema_version": 1,
            "max_turns": protocol.qualification_max_turns,
            "triggered": not quality_satisfied,
            "initial_eligibility": eligibility,
            "final_eligibility": eligibility,
            "selected_turn": {
                "kind": "lifecycle",
                "index": len(session.get("turns", [])) - 1,
                "step_id": final_turn.get("step_id"),
                "source_sha256": source_snapshot_hash(selected_sources),
            },
            "selection_policy": "monotonic_non_layout_then_eligibility_and_layout",
            "layout_fidelity_target": layout_target,
            "layout_target_met": layout_fidelity >= layout_target,
        },
    )
    if not isinstance(qualification, dict):
        raise ValueError("session qualification must be an object")
    maximum = protocol.qualification_max_turns
    final_step = task.steps[-1]
    selected_rank = _eligibility_rank(eligibility)
    for attempt_index in range(len(qualification_turns), maximum):
        selected_layout = float(eligibility.get("layout_fidelity") or 0.0)
        if eligibility["eligible"] and selected_layout >= layout_target:
            break
        attempt = attempt_index + 1
        prompt = _qualification_prompt(
            arm=args.arm,
            attempt=attempt,
            maximum=maximum,
            evaluator_feedback=feedback,
            eligibility=eligibility,
            layout_target=layout_target,
        )
        print(
            f"[{args.arm}] baseline qualification turn {attempt}/{maximum}",
            flush=True,
        )
        sequence = len(task.steps) + attempt
        events_path = output / "events" / f"{sequence:02d}-qualification-{attempt}.jsonl"
        prompt_fact = _prompt_fact(
            prompt,
            output / "prompts" / f"{sequence:02d}-qualification-{attempt}.txt",
        )
        parsed, wall_ms, exit_code, _raw = _codex_turn(
            workspace=workspace,
            prompt=prompt,
            events_path=events_path,
            reference_image=None,
            thread_id=thread_id,
            model=args.model,
            ignore_user_config=not args.allow_user_config,
            timeout=args.turn_timeout,
        )
        if thread_id is None:
            thread_id = parsed.get("thread_id")
        artifact = output / "artifacts" / f"{sequence:02d}-{final_step.id}"
        artifact.mkdir(parents=True, exist_ok=True)
        source, previous_source, source_ms = _capture_source(
            workspace=workspace,
            artifact=artifact,
            arm=args.arm,
            previous=previous_source,
        )
        score, proof, phases_ms, candidate_feedback, target_trials = _evaluate_turn(
            workspace=workspace,
            output=output,
            protocol_path=protocol_path,
            task=task,
            arm=args.arm,
            step=final_step,
            step_index=sequence - 1,
            install=not args.no_install,
        )
        phases_ms = {"source_snapshot": source_ms, **phases_ms}
        current_criteria = _criterion_states(score)
        transitions = _criterion_transition(selected_criteria, current_criteria)
        current_functional_criteria = _criterion_states(
            score,
            excluded_dimensions={"layout_fidelity"},
        )
        functional_regressions = sorted(
            key
            for key, passed in selected_functional_criteria.items()
            if passed and current_functional_criteria.get(key) is not True
        )
        candidate_sources = _source_texts(workspace, args.arm)
        hooks = validate_stable_hooks(args.arm, candidate_sources)
        next_eligibility = _eligibility_report(
            arm=args.arm,
            score=score,
            proof=proof,
            targets=target_trials,
            hooks=hooks,
        )
        next_rank = _eligibility_rank(next_eligibility)
        candidate_layout = float(next_eligibility.get("layout_fidelity") or 0.0)
        layout_regressed = candidate_layout + 0.02 < selected_layout
        eligibility_improved = next_rank > selected_rank
        refining_layout = eligibility["eligible"] and selected_layout < layout_target
        layout_non_regressing = candidate_layout >= selected_layout
        selected = (
            exit_code == 0
            and parsed.get("completed") is True
            and not functional_regressions
            and next_rank >= selected_rank
            and (
                next_eligibility["eligible"] and layout_non_regressing
                if refining_layout
                else not layout_regressed or eligibility_improved
            )
        )
        selection_reason = (
            "candidate met eligibility and reference-layout target"
            if selected
            and next_eligibility["eligible"]
            and candidate_layout >= layout_target
            else "non-regressing reference-layout refinement"
            if selected and refining_layout
            else "eligible candidate"
            if selected and next_eligibility["eligible"]
            else "non-regressing candidate with equal or improved eligibility rank"
            if selected
            else "model turn did not complete"
            if exit_code or parsed.get("completed") is not True
            else "candidate regressed previously passing non-layout criteria"
            if functional_regressions
            else "candidate exceeded the allowed 0.02 reference-layout regression"
            if layout_regressed
            else "candidate did not preserve or improve reference-layout fidelity"
            if refining_layout and not layout_non_regressing
            else "candidate did not match the selected eligibility rank"
        )
        turn = {
            "step_id": f"baseline-qualification-{attempt}",
            "phase": "qualification",
            "assurance_tags": ["baseline-eligibility"],
            "qualification_attempt": attempt,
            "agent_exit_code": exit_code,
            "agent_completed": parsed.get("completed") is True,
            "agent_message": parsed.get("agent_message", ""),
            "usage": parsed.get("usage", {}),
            "agent_telemetry": parsed.get("telemetry", {}),
            "events": _file_fact(events_path),
            "event_diagnostics": _file_fact(_diagnostics_path(events_path)),
            "prompt": prompt_fact,
            "wall_time_ms": wall_ms,
            "deterministic_ms": sum(phases_ms.values()),
            "phase_timings_ms": phases_ms,
            "source": source,
            "score": score,
            "criterion_transitions": transitions,
            "proof": proof,
            "proof_telemetry": _proof_telemetry(proof),
            "target_trials": target_trials,
            "eligibility_before": eligibility,
            "eligibility_after": next_eligibility,
            "evaluator_feedback": candidate_feedback,
            "selected": selected,
            "selection_reason": selection_reason,
            "selection_rank": list(next_rank),
            "functional_regressions_against_selected": functional_regressions,
            "layout_regressed_against_selected": layout_regressed,
            "artifact_root": str(artifact),
            "browser_target": {
                "kind": (
                    "authored_static_site"
                    if args.arm == "code-first"
                    else "compiled_static_shell"
                ),
                "entry": "index.html",
            },
        }
        qualification_turns.append(turn)
        qualification.setdefault("selection_log", []).append(
            {
                "attempt": attempt,
                "selected": selected,
                "reason": selection_reason,
                "functional_regressions": functional_regressions,
                "layout_regressed": layout_regressed,
                "candidate_rank": list(next_rank),
            }
        )
        qualification["attempted_final_eligibility"] = next_eligibility
        if selected:
            final_turn = turn
            eligibility = next_eligibility
            selected_rank = next_rank
            selected_sources = candidate_sources
            selected_feedback = candidate_feedback
            selected_criteria = current_criteria
            selected_functional_criteria = current_functional_criteria
            qualification["selected_turn"] = {
                "kind": "qualification",
                "index": len(qualification_turns) - 1,
                "step_id": turn["step_id"],
                "source_sha256": source_snapshot_hash(selected_sources),
            }
        else:
            _restore_source_texts(
                workspace,
                args.arm,
                selected_sources,
                prune=True,
            )
        previous_source = selected_sources
        previous_criteria = selected_criteria
        feedback = selected_feedback
        qualification["final_eligibility"] = eligibility
        qualification["turn_count"] = len(qualification_turns)
        qualification["layout_target_met"] = (
            float(eligibility.get("layout_fidelity") or 0.0) >= layout_target
        )
        qualification["exhausted"] = (
            not (
                eligibility["eligible"]
                and float(eligibility.get("layout_fidelity") or 0.0)
                >= layout_target
            )
            and len(qualification_turns) >= maximum
        )
        session["thread_id"] = thread_id
        _write_checkpoint(
            output=output,
            protocol_path=protocol_path,
            model=args.model,
            workspace=workspace,
            arm=args.arm,
            stage="qualification",
            next_index=attempt,
            feedback=feedback,
            previous_criteria=previous_criteria,
            session=session,
        )
        print(
            f"[{args.arm}] qualification selected={selected} eligible={eligibility['eligible']}: "
            + "; ".join((eligibility["reasons"] or [selection_reason])[:6]),
            flush=True,
        )
        if exit_code or not parsed.get("completed"):
            break
    qualification["final_eligibility"] = eligibility
    qualification.setdefault("turn_count", len(qualification_turns))
    qualification["layout_target_met"] = (
        float(eligibility.get("layout_fidelity") or 0.0) >= layout_target
    )
    qualification.setdefault("exhausted", False)
    return (
        final_turn,
        thread_id,
        feedback,
        previous_source,
        previous_criteria,
    )


def _verify_repair_once(
    *,
    args: argparse.Namespace,
    protocol_path: Path,
    task: AgentEvalTask,
    final_step: AgentEvalStep,
    trial_id: str,
    expected: list[str],
    record: dict[str, Any],
    repair_workspace: Path,
    repair_root: Path,
    repaired_sources: dict[str, str],
    repaired_hash: str,
    parsed: dict[str, Any],
    model_ms: int,
    exit_code: int,
    repair_model_error: str | None,
    prompt_fact: dict[str, Any],
    events: Path,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    repair_evaluation = None
    repair_infrastructure_errors: list[str] = []
    for repair_attempt in range(2):
        try:
            repair_evaluation = _evaluate_turn(
                workspace=repair_workspace,
                output=repair_root / f"evaluation-attempt-{repair_attempt + 1}",
                protocol_path=protocol_path,
                task=task,
                arm=args.arm,
                step=final_step,
                step_index=len(task.steps) - 1,
                install=not args.no_install,
            )
            infrastructure_error = _evaluation_infrastructure_error(
                arm=args.arm,
                trial_id=trial_id,
                evaluation=repair_evaluation,
            )
            if infrastructure_error is None:
                break
            repair_infrastructure_errors.append(infrastructure_error)
            repair_evaluation = None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            repair_infrastructure_errors.append(f"{type(exc).__name__}: {exc}")
    repair_turn = {
        "trial_id": trial_id,
        "thread_id": parsed.get("thread_id"),
        "usage": parsed.get("usage", {}),
        "agent_telemetry": parsed.get("telemetry", {}),
        "wall_time_ms": model_ms,
        "deterministic_ms": 0,
        "exit_code": exit_code,
        "model_error": repair_model_error,
        "prompt": prompt_fact,
        "events": _file_fact(events),
        "event_diagnostics": _file_fact(_diagnostics_path(events)),
    }
    if repair_evaluation is None:
        record.update(
            {
                "repaired": None,
                "invalid_reason": "repeated_repair_verification_infrastructure_failure",
                "repair_infrastructure_errors": repair_infrastructure_errors,
                "repair_usage": parsed.get("usage", {}),
                "repair_wall_time_ms": model_ms,
            }
        )
        return record, repair_turn, 0
    (
        repaired_score,
        repaired_proof,
        repaired_phases,
        repaired_feedback,
        repaired_targets,
    ) = repair_evaluation
    repaired_hooks = validate_stable_hooks(args.arm, repaired_sources)
    remaining = _observed_detectors(
        trial_id,
        arm=args.arm,
        score=repaired_score,
        proof=repaired_proof,
    )
    repair_eligibility = _eligibility_report(
        arm=args.arm,
        score=repaired_score,
        proof=repaired_proof,
        targets=repaired_targets,
        hooks=repaired_hooks,
    )
    repaired = (
        exit_code == 0
        and parsed.get("completed") is True
        and not (set(remaining) & set(expected))
        and repair_eligibility["eligible"]
    )
    repair_deterministic_ms = sum(repaired_phases.values())
    repair_turn["deterministic_ms"] = repair_deterministic_ms
    record.update(
        {
            "repaired": repaired,
            "repaired_sha256": repaired_hash,
            "repair_remaining_detectors": remaining,
            "repair_eligibility": repair_eligibility,
            "repair_usage": parsed.get("usage", {}),
            "repair_wall_time_ms": model_ms,
            "repair_deterministic_ms": repair_deterministic_ms,
            "repair_feedback": _eligibility_feedback(
                repaired_feedback,
                repair_eligibility,
                label="Repair eligibility",
            ),
            "repair_artifacts": {"root": str(repair_root)},
        }
    )
    return record, repair_turn, repair_deterministic_ms


def _run_value_trials(
    *,
    args: argparse.Namespace,
    protocol: AgentEvalProtocol,
    protocol_path: Path,
    task: AgentEvalTask,
    workspace: Path,
    output: Path,
    session: dict[str, Any],
    final_turn: dict[str, Any],
) -> dict[str, Any]:
    manifest = load_mutation_manifest(MUTATION_MANIFEST)
    healthy_sources = _source_texts(workspace, args.arm)
    baseline_hash = source_snapshot_hash(healthy_sources)
    hooks = validate_stable_hooks(args.arm, healthy_sources)
    final_targets = final_turn.get("target_trials", [])
    eligibility = _eligibility_report(
        arm=args.arm,
        score=final_turn["score"],
        proof=final_turn.get("proof"),
        targets=final_targets,
        hooks=hooks,
    )
    eligible = eligibility["eligible"]
    mutations = {item["id"]: item for item in manifest["mutations"]}
    controls = {item["id"]: item for item in manifest["controls"]}
    trial_order = seeded_trial_order(args.seed, [*mutations, *controls])
    fresh_evidence: dict[str, Any] = {
        "schema_version": 1,
        "manifest": {
            "path": str(MUTATION_MANIFEST),
            "sha256": _sha256(MUTATION_MANIFEST),
            "declared_sha256": manifest["manifest_sha256"],
        },
        "baseline": {
            **eligibility,
            "source_sha256": baseline_hash,
            "layout_fidelity": _layout_fidelity(final_turn["score"]),
        },
        "trial_order": trial_order,
        "mutation_trials": [],
        "negative_control_trials": [],
        "target_trials": final_targets,
        "repair_turns": [],
        "deterministic_overhead_ms": 0,
    }
    existing = session.get("value_evidence")
    if isinstance(existing, dict) and existing.get("trial_order") == trial_order:
        if existing.get("baseline", {}).get("source_sha256") != baseline_hash:
            raise ValueError("resumed value evidence baseline hash mismatch")
        evidence = existing
    else:
        evidence = fresh_evidence
    if not eligible:
        reason = "baseline_ineligible"
        for trial_id in trial_order:
            item = {
                "id": trial_id,
                "order": trial_order.index(trial_id),
                "applicable": False,
                "invalid_reason": reason,
                "baseline_sha256": baseline_hash,
            }
            destination = (
                evidence["negative_control_trials"]
                if trial_id in controls
                else evidence["mutation_trials"]
            )
            destination.append(item)
        return evidence
    final_step = task.steps[-1]
    completed_ids = {
        item.get("id")
        for item in [
            *evidence["mutation_trials"],
            *evidence["negative_control_trials"],
        ]
        if isinstance(item, dict)
    }
    for order, trial_id in enumerate(trial_order):
        if trial_id in completed_ids:
            continue
        trial = mutations.get(trial_id) or controls[trial_id]
        trial_root = output / "value-trials" / f"{order + 1:02d}-{trial_id}"
        repair_root = trial_root / "repair"
        repair_workspace = repair_root / "workspace"
        repair_attempt = repair_root / "repair-attempt-checkpoint.json"
        repair_result = repair_root / "repair-result-checkpoint.json"
        if repair_attempt.is_file():
            pending = json.loads(repair_attempt.read_text(encoding="utf-8"))
            if (
                pending.get("trial_id") != trial_id
                or pending.get("order") != order
                or pending.get("baseline_sha256") != baseline_hash
                or pending.get("model") != args.model
            ):
                raise ValueError("pending repair checkpoint provenance mismatch")
            record = dict(pending.get("record", {}))
            expected = list(record.get("expected_detectors", []))
            evidence["deterministic_overhead_ms"] = max(
                evidence["deterministic_overhead_ms"],
                int(pending.get("deterministic_overhead_ms", 0)),
            )
            events = repair_root / "events.jsonl"
            checkpoint_repaired_hash = None
            if repair_result.is_file():
                result_checkpoint = json.loads(repair_result.read_text(encoding="utf-8"))
                if (
                    result_checkpoint.get("trial_id") != trial_id
                    or result_checkpoint.get("model") != args.model
                ):
                    raise ValueError("pending repair result provenance mismatch")
                parsed = dict(result_checkpoint.get("parsed", {}))
                model_ms = int(result_checkpoint.get("model_wall_time_ms", 0))
                exit_code = int(result_checkpoint.get("exit_code", 1))
                repair_model_error = result_checkpoint.get("model_error")
                checkpoint_repaired_hash = result_checkpoint.get("repaired_sha256")
            else:
                raw_events = events.read_text(encoding="utf-8") if events.is_file() else ""
                parsed = parse_codex_jsonl(raw_events)
                model_ms = 0
                exit_code = 0 if parsed.get("completed") is True else 125
                repair_model_error = "repair result checkpoint was interrupted"
            if repair_workspace.is_dir():
                repaired_sources = _source_texts(repair_workspace, args.arm)
                repaired_hash = source_snapshot_hash(repaired_sources)
                if (
                    isinstance(checkpoint_repaired_hash, str)
                    and checkpoint_repaired_hash != repaired_hash
                ):
                    raise ValueError("pending repair source hash mismatch")
                for relative, text in repaired_sources.items():
                    _write(repair_root / "repaired-source" / relative, text)
                record, repair_turn, repair_deterministic_ms = _verify_repair_once(
                    args=args,
                    protocol_path=protocol_path,
                    task=task,
                    final_step=final_step,
                    trial_id=trial_id,
                    expected=expected,
                    record=record,
                    repair_workspace=repair_workspace,
                    repair_root=repair_root,
                    repaired_sources=repaired_sources,
                    repaired_hash=repaired_hash,
                    parsed=parsed,
                    model_ms=model_ms,
                    exit_code=exit_code,
                    repair_model_error=repair_model_error,
                    prompt_fact=dict(pending.get("prompt", {})),
                    events=events,
                )
                evidence["deterministic_overhead_ms"] += repair_deterministic_ms
            else:
                record.update(
                    {
                        "repaired": None,
                        "invalid_reason": "repair_workspace_missing_after_model_attempt",
                        "repair_usage": parsed.get("usage", {}),
                        "repair_wall_time_ms": model_ms,
                    }
                )
                repair_turn = {
                    "trial_id": trial_id,
                    "thread_id": parsed.get("thread_id"),
                    "usage": parsed.get("usage", {}),
                    "agent_telemetry": parsed.get("telemetry", {}),
                    "wall_time_ms": model_ms,
                    "deterministic_ms": 0,
                    "exit_code": exit_code,
                    "model_error": repair_model_error,
                    "prompt": dict(pending.get("prompt", {})),
                    "events": _file_fact(events),
                }
            evidence["repair_turns"].append(repair_turn)
            evidence["mutation_trials"].append(record)
            _restore_source_texts(workspace, args.arm, healthy_sources)
            session["value_evidence"] = evidence
            _write_checkpoint(
                output=output,
                protocol_path=protocol_path,
                model=args.model,
                workspace=workspace,
                arm=args.arm,
                stage="value_trials",
                next_index=order + 1,
                feedback="",
                previous_criteria={},
                session=session,
            )
            if repair_workspace.is_dir():
                shutil.rmtree(repair_workspace)
            continue
        _restore_source_texts(workspace, args.arm, healthy_sources)
        if source_snapshot_hash(_source_texts(workspace, args.arm)) != baseline_hash:
            raise RuntimeError("healthy source restoration hash mismatch")
        mutated_sources, mutation_fact = apply_value_trial(
            arm=args.arm,
            trial_id=trial_id,
            sources=healthy_sources,
        )
        _restore_source_texts(workspace, args.arm, mutated_sources)
        for relative, text in mutated_sources.items():
            _write(trial_root / "mutated-source" / relative, text)
        evaluation: tuple[
            dict[str, Any],
            dict[str, Any] | None,
            dict[str, int],
            str,
            list[dict[str, Any]],
        ] | None = None
        infrastructure_errors: list[str] = []
        attempt_used = 0
        for attempt in range(2):
            try:
                evaluation = _evaluate_turn(
                    workspace=workspace,
                    output=trial_root / f"attempt-{attempt + 1}",
                    protocol_path=protocol_path,
                    task=task,
                    arm=args.arm,
                    step=final_step,
                    step_index=len(task.steps) - 1,
                    install=not args.no_install,
                )
                infrastructure_error = _evaluation_infrastructure_error(
                    arm=args.arm,
                    trial_id=trial_id,
                    evaluation=evaluation,
                )
                if infrastructure_error is None:
                    attempt_used = attempt + 1
                    break
                infrastructure_errors.append(infrastructure_error)
                evaluation = None
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                infrastructure_errors.append(f"{type(exc).__name__}: {exc}")
        if evaluation is None:
            record = {
                "id": trial_id,
                "class": trial.get("class"),
                "order": order,
                "applicable": False,
                "invalid_reason": "repeated_infrastructure_failure",
                "infrastructure_errors": infrastructure_errors,
                **mutation_fact,
            }
            (evidence["mutation_trials"] if trial_id in mutations else evidence["negative_control_trials"]).append(record)
            _restore_source_texts(workspace, args.arm, healthy_sources)
            session["value_evidence"] = evidence
            _write_checkpoint(
                output=output,
                protocol_path=protocol_path,
                model=args.model,
                workspace=workspace,
                arm=args.arm,
                stage="value_trials",
                next_index=order + 1,
                feedback="",
                previous_criteria={},
                session=session,
            )
            continue
        score, proof, phases, feedback, targets = evaluation
        deterministic_ms = sum(phases.values())
        evidence["deterministic_overhead_ms"] += deterministic_ms
        observed = (
            _observed_detectors(
                trial_id,
                arm=args.arm,
                score=score,
                proof=proof,
            )
            if trial_id in mutations
            else []
        )
        expected = (
            list(trial["expected_detectors"][args.arm])
            if trial_id in mutations
            else []
        )
        if trial_id in mutations:
            detected = bool(set(observed) & set(expected))
        else:
            mutated_hooks = validate_stable_hooks(args.arm, mutated_sources)
            detected = not _evaluation_healthy(
                arm=args.arm,
                score=score,
                proof=proof,
                targets=targets,
                hooks=mutated_hooks,
            )
        record: dict[str, Any] = {
            "id": trial_id,
            "class": trial.get("class"),
            "observable_failure": trial.get("observable_failure"),
            "order": order,
            "applicable": True,
            "repair_applicable": bool(trial.get("repairable")) and detected,
            "baseline_sha256": baseline_hash,
            "mutated_sha256": mutation_fact["mutated_sha256"],
            "operator": mutation_fact["operator"],
            "changed_path": mutation_fact.get("changed_path"),
            "expected_detectors": expected,
            "observed_detectors": observed,
            "detected": detected,
            "deterministic_ms": deterministic_ms,
            "phase_timings_ms": phases,
            "commands": ["compile/prove", "vite build", "local Chromium score"],
            "artifacts": _trial_artifact_manifest(
                trial_root=trial_root,
                attempt_used=attempt_used,
                final_step_id=final_step.id,
                phase_timings_ms=phases,
            ),
            "infrastructure_attempts": attempt_used,
        }
        cleanup_repair_workspace = False
        if trial_id in mutations and detected and trial.get("repairable") is True:
            _copy_isolated_workspace(workspace, repair_workspace)
            _restore_source_texts(workspace, args.arm, healthy_sources)
            if source_snapshot_hash(_source_texts(workspace, args.arm)) != baseline_hash:
                raise RuntimeError("pre-repair healthy source restoration hash mismatch")
            prompt = _repair_prompt(task, args.arm, feedback)
            prompt_fact = _prompt_fact(prompt, repair_root / "prompt.txt")
            events = repair_root / "events.jsonl"
            _write(
                repair_attempt,
                canonical_json(
                    {
                        "schema_version": 1,
                        "trial_id": trial_id,
                        "order": order,
                        "baseline_sha256": baseline_hash,
                        "mutated_sha256": mutation_fact["mutated_sha256"],
                        "model": args.model,
                        "deterministic_overhead_ms": evidence["deterministic_overhead_ms"],
                        "prompt": prompt_fact,
                        "record": record,
                    }
                ),
            )
            repair_model_error = None
            try:
                parsed, model_ms, exit_code, _raw = _codex_turn(
                    workspace=repair_workspace,
                    prompt=prompt,
                    events_path=events,
                    reference_image=None,
                    thread_id=None,
                    model=args.model,
                    ignore_user_config=not args.allow_user_config,
                    timeout=args.turn_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                repair_model_error = f"TimeoutExpired: {exc}"
                model_ms = args.turn_timeout * 1000
                exit_code = 124
                parsed = {
                    "thread_id": None,
                    "completed": False,
                    "usage": {},
                    "telemetry": {},
                }
                _write(events, "")
                _write(_diagnostics_path(events), repair_model_error + "\n")
            repaired_sources = _source_texts(repair_workspace, args.arm)
            repaired_hash = source_snapshot_hash(repaired_sources)
            for relative, text in repaired_sources.items():
                _write(repair_root / "repaired-source" / relative, text)
            _write(
                repair_result,
                canonical_json(
                    {
                        "schema_version": 1,
                        "trial_id": trial_id,
                        "model": args.model,
                        "parsed": parsed,
                        "model_wall_time_ms": model_ms,
                        "exit_code": exit_code,
                        "model_error": repair_model_error,
                        "repaired_sha256": repaired_hash,
                    }
                ),
            )
            record, repair_turn, repair_deterministic_ms = _verify_repair_once(
                args=args,
                protocol_path=protocol_path,
                task=task,
                final_step=final_step,
                trial_id=trial_id,
                expected=expected,
                record=record,
                repair_workspace=repair_workspace,
                repair_root=repair_root,
                repaired_sources=repaired_sources,
                repaired_hash=repaired_hash,
                parsed=parsed,
                model_ms=model_ms,
                exit_code=exit_code,
                repair_model_error=repair_model_error,
                prompt_fact=prompt_fact,
                events=events,
            )
            evidence["deterministic_overhead_ms"] += repair_deterministic_ms
            evidence["repair_turns"].append(repair_turn)
            cleanup_repair_workspace = True
        elif trial_id in mutations:
            record["repaired"] = False if detected else None
        (evidence["mutation_trials"] if trial_id in mutations else evidence["negative_control_trials"]).append(record)
        _restore_source_texts(workspace, args.arm, healthy_sources)
        if source_snapshot_hash(_source_texts(workspace, args.arm)) != baseline_hash:
            raise RuntimeError("post-trial healthy source restoration hash mismatch")
        session["value_evidence"] = evidence
        _write_checkpoint(
            output=output,
            protocol_path=protocol_path,
            model=args.model,
            workspace=workspace,
            arm=args.arm,
            stage="value_trials",
            next_index=order + 1,
            feedback="",
            previous_criteria={},
            session=session,
        )
        if cleanup_repair_workspace and repair_workspace.is_dir():
            shutil.rmtree(repair_workspace)
    return evidence


def _run_session(args: argparse.Namespace) -> int:
    protocol, protocol_path = _protocol(args.protocol)
    if args.arm not in protocol.arms:
        raise ValueError(f"Unknown arm: {args.arm}")
    if args.seed not in protocol.seeds:
        raise ValueError(f"Seed {args.seed} was not pre-registered")
    if args.with_value_trials and protocol.schema_version != 2:
        raise ValueError("value trials require a V2 protocol")
    if args.with_value_trials:
        _validate_value_runtime()
    task = protocol.task(args.task)
    reference = _reference_path(protocol_path, task)
    if args.resume:
        output = Path(args.out).resolve()
        workspace = output / "workspace"
        session = json.loads((output / "session.partial.json").read_text(encoding="utf-8"))
        checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
        current_sources = _source_texts(workspace, args.arm)
        validate_checkpoint(
            checkpoint,
            protocol_sha256=_sha256(protocol_path),
            model=args.model,
            source_sha256=source_snapshot_hash(current_sources),
            product_tree_sha256=_product_tree_fact()["sha256"],
        )
        if checkpoint.get("arm_id") != args.arm:
            raise ValueError("checkpoint arm_id mismatch")
        turns = session["turns"]
        session.setdefault("qualification_turns", [])
        thread_id = session.get("thread_id")
        feedback = checkpoint.get("feedback", "")
        previous_criteria = dict(checkpoint.get("previous_criteria", {}))
        previous_source = current_sources
        start_index = (
            int(checkpoint["next_index"])
            if checkpoint.get("stage") == "lifecycle"
            else len(task.steps)
        )
        reference_image = (
            output
            / "reference-render"
            / "browser-evidence"
            / f"{REFERENCE_IMAGE_WIDTH}x{REFERENCE_IMAGE_HEIGHT}.png"
        )
        if checkpoint.get("stage") == "complete" and (output / "session.json").is_file():
            completed_session = json.loads(
                (output / "session.json").read_text(encoding="utf-8")
            )
            _require_value_evidence_integrity(completed_session)
            summary = summarize_agent_eval_session(completed_session)
            print(canonical_json(summary), end="", flush=True)
            return 0
    else:
        output = _empty_output(Path(args.out))
        setup_started = time.perf_counter()
        workspace = _prepare_workspace(output, task, args.arm, args.seed)
        workspace_setup_ms = round((time.perf_counter() - setup_started) * 1000)
        environment_started = time.perf_counter()
        environment = _environment_telemetry(
            model=args.model,
            protocol_path=protocol_path,
            reference=reference,
            ignore_user_config=not args.allow_user_config,
            install=not args.no_install,
        )
        environment_capture_ms = round((time.perf_counter() - environment_started) * 1000)
        _write(output / "environment.json", canonical_json(environment))
        print("Rendering the blinded reference image...", flush=True)
        reference_started = time.perf_counter()
        reference_image = _render_reference(reference, task, output)
        reference_render_ms = round((time.perf_counter() - reference_started) * 1000)
        turns = []
        thread_id = None
        feedback = "No prior evaluation; this is the initial build."
        previous_source = {}
        previous_criteria = {}
        start_index = 0
        session = {
            "schema_version": AGENT_UI_EVAL_SCHEMA_VERSION,
            "protocol_id": protocol.id,
            "task_id": task.id,
            "arm_id": args.arm,
            "seed": args.seed,
            "model": args.model,
            "environment": environment,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "thread_id": thread_id,
            "setup_timings_ms": {
                "workspace": workspace_setup_ms,
                "environment": environment_capture_ms,
                "reference_render": reference_render_ms,
                "total": workspace_setup_ms + environment_capture_ms + reference_render_ms,
            },
            "turns": turns,
            "qualification_turns": [],
        }
    for index in range(start_index, len(task.steps)):
        step = task.steps[index]
        react_activation = None
        if args.arm == "code-first" and step.phase == "leverage":
            activation_started = time.perf_counter()
            react_activation = _activate_code_first_react(workspace)
            react_activation["duration_ms"] = round(
                (time.perf_counter() - activation_started) * 1000
            )
        prompt = (
            _initial_prompt(task, args.arm, step)
            if index == 0
            else _followup_prompt(task, args.arm, step, index, feedback)
        )
        print(f"[{args.arm}] model turn {index + 1}/{len(task.steps)}: {step.id}", flush=True)
        events_path = output / "events" / f"{index + 1:02d}-{step.id}.jsonl"
        prompt_fact = _prompt_fact(
            prompt,
            output / "prompts" / f"{index + 1:02d}-{step.id}.txt",
        )
        parsed, wall_ms, exit_code, _raw = _codex_turn(
            workspace=workspace,
            prompt=prompt,
            events_path=events_path,
            reference_image=reference_image if index == 0 else None,
            thread_id=thread_id,
            model=args.model,
            ignore_user_config=not args.allow_user_config,
            timeout=args.turn_timeout,
        )
        if thread_id is None:
            thread_id = parsed.get("thread_id")
        artifact = output / "artifacts" / f"{index + 1:02d}-{step.id}"
        artifact.mkdir(parents=True, exist_ok=True)
        source, previous_source, source_ms = _capture_source(
            workspace=workspace,
            artifact=artifact,
            arm=args.arm,
            previous=previous_source,
        )
        print(f"[{args.arm}] deterministic proof and browser checks for {step.id}", flush=True)
        score, proof, phases_ms, feedback, target_trials = _evaluate_turn(
            workspace=workspace,
            output=output,
            protocol_path=protocol_path,
            task=task,
            arm=args.arm,
            step=step,
            step_index=index,
            install=not args.no_install,
        )
        phases_ms = {"source_snapshot": source_ms, **phases_ms}
        deterministic_ms = sum(phases_ms.values())
        current_criteria = _criterion_states(score)
        transitions = _criterion_transition(previous_criteria, current_criteria)
        previous_criteria = current_criteria
        turns.append(
            {
                "step_id": step.id,
                "phase": step.phase,
                "assurance_tags": list(step.assurance_tags),
                "agent_exit_code": exit_code,
                "agent_completed": parsed.get("completed") is True,
                "agent_message": parsed.get("agent_message", ""),
                "usage": parsed.get("usage", {}),
                "agent_telemetry": parsed.get("telemetry", {}),
                "events": _file_fact(events_path),
                "event_diagnostics": _file_fact(_diagnostics_path(events_path)),
                "prompt": prompt_fact,
                "wall_time_ms": wall_ms,
                "deterministic_ms": deterministic_ms,
                "phase_timings_ms": phases_ms,
                "source": source,
                "score": score,
                "criterion_transitions": transitions,
                "proof": proof,
                "proof_telemetry": _proof_telemetry(proof),
                "target_trials": target_trials,
                "artifact_root": str(artifact),
                **({"react_activation": react_activation} if react_activation is not None else {}),
                "browser_target": {
                    "kind": "authored_static_site" if args.arm == "code-first" else "compiled_static_shell",
                    "entry": "index.html",
                },
            }
        )
        session["thread_id"] = thread_id
        _write_checkpoint(
            output=output,
            protocol_path=protocol_path,
            model=args.model,
            workspace=workspace,
            arm=args.arm,
            stage="lifecycle",
            next_index=index + 1,
            feedback=feedback,
            previous_criteria=previous_criteria,
            session=session,
        )
        print(f"[{args.arm}] {feedback}", flush=True)
        if exit_code or not parsed.get("completed"):
            print(f"[{args.arm}] stopping because the model turn did not complete", flush=True)
            break
    if len(turns) == len(task.steps) and args.with_value_trials:
        final_turn = _selected_delivery_turn(session)
        (
            final_turn,
            thread_id,
            feedback,
            previous_source,
            previous_criteria,
        ) = _run_baseline_qualification(
            args=args,
            protocol=protocol,
            protocol_path=protocol_path,
            task=task,
            workspace=workspace,
            output=output,
            session=session,
            final_turn=final_turn,
            thread_id=thread_id,
            feedback=feedback,
            previous_source=previous_source,
            previous_criteria=previous_criteria,
        )
        print(f"[{args.arm}] running deterministic value trials", flush=True)
        session["value_evidence"] = _finalize_value_evidence(
            _run_value_trials(
                args=args,
                protocol=protocol,
                protocol_path=protocol_path,
                task=task,
                workspace=workspace,
                output=output,
                session=session,
                final_turn=final_turn,
            )
        )
        _require_value_evidence_integrity(session)
    _write(output / "session.json", canonical_json(session))
    summary = summarize_agent_eval_session(session)
    _write(output / "summary.json", canonical_json(summary))
    if len(turns) == len(task.steps):
        _write_checkpoint(
            output=output,
            protocol_path=protocol_path,
            model=args.model,
            workspace=workspace,
            arm=args.arm,
            stage="complete",
            next_index=len(task.steps) + len(session.get("qualification_turns", [])),
            feedback=feedback,
            previous_criteria=previous_criteria,
            session=session,
        )
    print(canonical_json(summary), end="", flush=True)
    return 0 if len(turns) == len(task.steps) else 2


def _build_review_packet(
    *,
    output: Path,
    arm_order: list[str],
    task: AgentEvalTask,
    seed: int,
) -> dict[str, Any]:
    packet = output / "review-packet"
    assets = packet / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    labels = {
        arm: f"Candidate {chr(ord('A') + index)}"
        for index, arm in enumerate(seeded_arm_order(seed + 1, tuple(arm_order)))
    }
    samples: list[dict[str, Any]] = []
    for arm in arm_order:
        session_path = output / arm / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        final_turn = _selected_delivery_turn(session)
        artifact_root = final_turn.get("artifact_root")
        root = (
            Path(artifact_root)
            if isinstance(artifact_root, str) and artifact_root
            else output
            / arm
            / "artifacts"
            / f"{len(task.steps):02d}-{task.steps[-1].id}"
        )
        for target, source_root in (
            ("static", root / "browser-evidence"),
            ("react", root / "react-score" / "browser-evidence"),
        ):
            for width, height in ((390, 844), (1440, 1000)):
                source = source_root / f"{width}x{height}.png"
                if not source.is_file():
                    continue
                name = f"{labels[arm].replace(' ', '-').lower()}-{target}-{width}.png"
                destination = assets / name
                shutil.copyfile(source, destination)
                samples.append(
                    {
                        "candidate": labels[arm],
                        "target": target,
                        "viewport": width,
                        "image": f"assets/{name}",
                        "sha256": _sha256(destination),
                    }
                )
    public = {
        "schema_version": 1,
        "task_id": task.id,
        "seed": seed,
        "exploratory_only": True,
        "human_ratings_are_a_pass_gate": False,
        "samples": samples,
    }
    _write(packet / "packet.json", canonical_json(public))
    _write(
        packet / "index.html",
        "<!doctype html><meta charset=utf-8><title>Blinded V2 review</title>"
        "<style>body{font:16px system-ui;margin:2rem}article{margin:2rem 0}"
        "img{max-width:min(100%,900px);border:1px solid #bbb}</style><main><h1>"
        "Blinded V2 exploratory review</h1>"
        + "".join(
            f"<article><h2>{item['candidate']} · {item['target']} · {item['viewport']}px</h2>"
            f"<img src=\"{item['image']}\" alt=\"Blinded interface screenshot\"></article>"
            for item in samples
        )
        + "</main>",
    )
    key = {"labels": labels, "seed": seed, "packet_sha256": _sha256(packet / "packet.json")}
    _write(output / "blinding-key.json", canonical_json(key))
    return {**public, "packet": str(packet / "index.html"), "blinding_key": str(output / "blinding-key.json")}


def _run_pair(args: argparse.Namespace) -> int:
    protocol, protocol_path = _protocol(args.protocol)
    if protocol.schema_version != 2 and args.with_value_trials:
        raise ValueError("value trials are rejected for V1 protocols")
    if args.with_value_trials:
        _validate_value_runtime()
    if args.seed not in protocol.seeds:
        raise ValueError(f"Seed {args.seed} was not pre-registered")
    task = protocol.task(args.task)
    output = Path(args.out).resolve()
    arm_order = seeded_arm_order(args.seed, protocol.arms)
    product_tree = _product_tree_fact()
    mutation_count = (
        len(load_mutation_manifest(MUTATION_MANIFEST)["mutations"])
        if args.with_value_trials
        else 0
    )
    model_call_budget = {
        "lifecycle_per_arm": len(task.steps),
        "lifecycle_total": len(task.steps) * len(protocol.arms),
        "qualification_per_arm_max": protocol.qualification_max_turns,
        "qualification_total_max": protocol.qualification_max_turns * len(protocol.arms),
        "repair_per_eligible_arm_max": mutation_count,
        "repair_total_max": mutation_count * len(protocol.arms),
        "maximum_total_calls": (
            len(task.steps) * len(protocol.arms)
            + protocol.qualification_max_turns * len(protocol.arms)
            + mutation_count * len(protocol.arms)
        ),
    }
    if args.resume:
        pair_manifest = json.loads((output / "pair-manifest.json").read_text(encoding="utf-8"))
        if pair_manifest.get("arm_order") != arm_order:
            raise ValueError("pair checkpoint arm order mismatch")
        if pair_manifest.get("protocol_sha256") != _sha256(protocol_path):
            raise ValueError("pair checkpoint protocol hash mismatch")
        if pair_manifest.get("model") != args.model:
            raise ValueError("pair checkpoint model mismatch")
        if pair_manifest.get("viewspec_product_tree") != product_tree:
            raise ValueError("pair checkpoint product tree mismatch")
        if pair_manifest.get("model_call_budget") != model_call_budget:
            raise ValueError("pair checkpoint model call budget mismatch")
    else:
        output = _empty_output(output)
        pair_manifest = {
            "schema_version": 1,
            "protocol_id": protocol.id,
            "protocol_sha256": _sha256(protocol_path),
            "task_id": task.id,
            "seed": args.seed,
            "model": args.model,
            "arm_order": arm_order,
            "with_value_trials": args.with_value_trials,
            "sandbox": "workspace-write",
            "ignore_user_config": not args.allow_user_config,
            "target_build_network": "disabled",
            "qualification": dict(protocol.qualification or {}),
            "model_call_budget": model_call_budget,
            "viewspec_product_tree": product_tree,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write(output / "pair-manifest.json", canonical_json(pair_manifest))
    exit_code = 0
    for arm in arm_order:
        arm_out = output / arm
        arm_resume = args.resume and (arm_out / "checkpoint.json").is_file()
        if arm_resume:
            arm_checkpoint = json.loads(
                (arm_out / "checkpoint.json").read_text(encoding="utf-8")
            )
            if arm_checkpoint.get("stage") == "complete" and (arm_out / "session.json").is_file():
                continue
        child = argparse.Namespace(
            protocol=args.protocol,
            task=args.task,
            arm=arm,
            seed=args.seed,
            out=str(arm_out),
            model=args.model,
            allow_user_config=args.allow_user_config,
            turn_timeout=args.turn_timeout,
            no_install=args.no_install,
            with_value_trials=args.with_value_trials,
            resume=arm_resume,
        )
        code = _run_session(child)
        if code:
            exit_code = code
            break
    session_paths = [output / arm / "session.json" for arm in protocol.arms]
    if all(path.is_file() for path in session_paths):
        sessions = [json.loads(path.read_text(encoding="utf-8")) for path in session_paths]
        for session in sessions:
            _require_value_evidence_integrity(session)
        report = summarize_agent_eval_study(
            sessions,
            success_criteria=protocol.success_criteria,
            minimum_sessions_per_arm=protocol.minimum_sessions_per_arm,
            evaluation_mode=protocol.evaluation_mode,
            primary_arm=protocol.primary_arm,
        )
        report["review_packet"] = _build_review_packet(
            output=output,
            arm_order=arm_order,
            task=task,
            seed=args.seed,
        )
        _write(output / "study-report.json", canonical_json(report))
        print(canonical_json(report), end="")
    return exit_code


def _plan(args: argparse.Namespace) -> int:
    protocol, _path = _protocol(args.protocol)
    sessions = [
        {
            "task_id": task.id,
            "arm_id": arm,
            "seed": seed,
            "lifecycle_turn_count": len(task.steps),
            "qualification_turn_budget": protocol.qualification_max_turns,
        }
        for task in protocol.tasks
        for seed in protocol.seeds
        for arm in protocol.arms
    ]
    print(
        canonical_json(
            {
                "schema_version": AGENT_UI_EVAL_SCHEMA_VERSION,
                "protocol_schema_version": protocol.schema_version,
                "protocol_id": protocol.id,
                "evaluation_mode": protocol.evaluation_mode,
                "primary_arm": protocol.primary_arm,
                "minimum_sessions_per_arm": protocol.minimum_sessions_per_arm,
                "session_count": len(sessions),
                "lifecycle_turn_count": sum(
                    item["lifecycle_turn_count"] for item in sessions
                ),
                "maximum_qualification_turn_count": sum(
                    item["qualification_turn_budget"] for item in sessions
                ),
                "success_criteria": dict(protocol.success_criteria),
                "study_design": protocol.study_design,
                "qualification": protocol.qualification,
                "sessions": sessions,
            }
        ),
        end="",
    )
    return 0


def _summarize(args: argparse.Namespace) -> int:
    protocol, _path = _protocol(args.protocol)
    run_root = Path(args.runs).resolve()
    paths = sorted(run_root.glob("**/session.json"))
    if not paths:
        raise FileNotFoundError(f"No session.json records found under {run_root}")
    sessions = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for session in sessions:
        _require_value_evidence_integrity(session)
    report = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=protocol.minimum_sessions_per_arm,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )
    output = Path(args.out).resolve() if args.out else run_root / "study-report.json"
    if output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        for key in ("review_packet", "evidence_migration", "evidence_refresh"):
            if key in previous:
                report[key] = previous[key]
    _write(output, canonical_json(report))
    print(canonical_json(report), end="")
    return 0


def _migrate_trial_artifact_record(record: dict[str, Any]) -> str | None:
    if record.get("applicable", True) is not True:
        return None
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"trial {record.get('id')} has no artifact manifest")
    status = artifacts.get("score_status")
    if status in {"recorded", "not_run_early_detector"}:
        return None
    raw_score = artifacts.get("score")
    if not isinstance(raw_score, str) or not raw_score:
        raise ValueError(f"trial {record.get('id')} has no legacy score reference")
    score = Path(raw_score)
    if score.is_file():
        artifacts["score_status"] = "recorded"
        return "recorded"
    phase_timings = record.get("phase_timings_ms", {})
    if isinstance(phase_timings, dict) and "browser_score" in phase_timings:
        raise ValueError(
            f"trial {record.get('id')} ran browser scoring but its score artifact is missing"
        )
    detector_evidence = [
        str(path)
        for path in (score.parent / "compile.log", score.parent / "proof.log")
        if path.is_file()
    ]
    if not detector_evidence:
        raise ValueError(
            f"trial {record.get('id')} has a dangling score and no early-detector evidence"
        )
    artifacts.pop("score", None)
    artifacts["score_status"] = "not_run_early_detector"
    artifacts["detector_evidence"] = detector_evidence
    return "not_run_early_detector"


def _migrate_evidence(args: argparse.Namespace) -> int:
    protocol, protocol_path = _protocol(args.protocol)
    run_root = Path(args.run_root).resolve()
    migration_path = run_root / "evidence-migration.json"
    if migration_path.exists():
        raise FileExistsError(f"evidence migration already exists: {migration_path}")
    pair_manifest_path = run_root / "pair-manifest.json"
    if not pair_manifest_path.is_file():
        raise FileNotFoundError(f"paired-run manifest is missing: {pair_manifest_path}")
    pair_manifest = json.loads(pair_manifest_path.read_text(encoding="utf-8"))
    if pair_manifest.get("protocol_id") != protocol.id:
        raise ValueError("paired-run protocol id does not match the migration protocol")
    if pair_manifest.get("protocol_sha256") != _sha256(protocol_path):
        raise ValueError("paired-run protocol hash does not match the migration protocol")

    source_hashes: dict[str, str] = {}
    migrated_files: list[dict[str, Any]] = []
    status_counts = {"recorded": 0, "not_run_early_detector": 0}
    evidence_paths = sorted(run_root.glob("*/session.json")) + sorted(
        run_root.glob("*/session.partial.json")
    )
    checkpoint_paths = sorted(
        run_root.glob("*/value-trials/*/repair/repair-attempt-checkpoint.json")
    )
    if len(list(run_root.glob("*/session.json"))) != len(protocol.arms):
        raise ValueError("migration requires one completed session for every protocol arm")

    prepared_files: list[tuple[Path, dict[str, Any], str, list[dict[str, Any]]]] = []
    for path in [*evidence_paths, *checkpoint_paths]:
        before = _sha256(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        if path.name in {"session.json", "session.partial.json"}:
            evidence = payload.get("value_evidence")
            if not isinstance(evidence, dict):
                raise ValueError(f"value evidence is missing from {path}")
            for collection in ("mutation_trials", "negative_control_trials"):
                raw_records = evidence.get(collection, [])
                if not isinstance(raw_records, list):
                    raise ValueError(f"{collection} is invalid in {path}")
                records.extend(item for item in raw_records if isinstance(item, dict))
            baseline = evidence.get("baseline")
            if path.name == "session.json" and isinstance(baseline, dict):
                source_hash = baseline.get("source_sha256")
                if isinstance(source_hash, str):
                    source_hashes[str(payload.get("arm_id"))] = source_hash
        else:
            record = payload.get("record")
            if not isinstance(record, dict):
                raise ValueError(f"repair attempt record is invalid in {path}")
            records.append(record)
        changes = []
        for record in records:
            status = _migrate_trial_artifact_record(record)
            if status is not None:
                status_counts[status] += 1
                changes.append({"trial_id": record.get("id"), "score_status": status})
        if path.name in {"session.json", "session.partial.json"}:
            payload["value_evidence"] = _finalize_value_evidence(payload["value_evidence"])
            _require_value_evidence_integrity(payload)
        prepared_files.append((path, payload, before, changes))

    for path, payload, before, changes in prepared_files:
        _write(path, canonical_json(payload))
        migrated_files.append(
            {
                "path": str(path),
                "before_sha256": before,
                "after_sha256": _sha256(path),
                "changes": changes,
            }
        )

    sessions: list[dict[str, Any]] = []
    for arm in protocol.arms:
        session_path = run_root / arm / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        _require_value_evidence_integrity(session)
        sessions.append(session)
        summary_path = run_root / arm / "summary.json"
        before = _sha256(summary_path) if summary_path.is_file() else None
        _write(summary_path, canonical_json(summarize_agent_eval_session(session)))
        migrated_files.append(
            {
                "path": str(summary_path),
                "before_sha256": before,
                "after_sha256": _sha256(summary_path),
                "changes": [{"kind": "regenerated_summary"}],
            }
        )
    report_path = run_root / "study-report.json"
    previous_report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file()
        else {}
    )
    report_before = _sha256(report_path) if report_path.is_file() else None
    report = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=protocol.minimum_sessions_per_arm,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )
    if isinstance(previous_report.get("review_packet"), dict):
        report["review_packet"] = previous_report["review_packet"]
    report["evidence_migration"] = {
        "id": "v2.2-artifact-integrity-v1",
        "model_calls_added": 0,
        "manifest": str(migration_path),
    }
    _write(report_path, canonical_json(report))
    migrated_files.append(
        {
            "path": str(report_path),
            "before_sha256": report_before,
            "after_sha256": _sha256(report_path),
            "changes": [{"kind": "regenerated_study_report"}],
        }
    )

    original_runner_hashes = {
        session["environment"]["inputs"]["runner"]["sha256"]
        for session in sessions
    }
    migration = {
        "schema_version": 1,
        "id": "v2.2-artifact-integrity-v1",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": (
            "Replace legacy dangling browser-score references with explicit early-detector "
            "status/evidence, validate every declared artifact, and regenerate summaries with "
            "selected-deliverable proof health separated from intermediate detector activity."
        ),
        "protocol_id": protocol.id,
        "protocol_sha256": _sha256(protocol_path),
        "seed": pair_manifest.get("seed"),
        "model": pair_manifest.get("model"),
        "model_calls_added": 0,
        "source_sha256_by_arm": source_hashes,
        "original_runner_sha256": (
            next(iter(original_runner_hashes))
            if len(original_runner_hashes) == 1
            else sorted(original_runner_hashes)
        ),
        "migration_runner_sha256": _sha256(Path(__file__)),
        "migration_summary_sha256": _sha256(ROOT / "src" / "viewspec" / "agent_eval.py"),
        "status_counts_across_migrated_files": status_counts,
        "files": migrated_files,
    }
    _write(migration_path, canonical_json(migration))
    print(canonical_json(migration), end="")
    return 0


def _freeze_evidence(args: argparse.Namespace) -> int:
    protocol, protocol_path = _protocol(args.protocol)
    run_root = Path(args.run_root).resolve()
    freeze_path = run_root / "evidence-freeze.json"
    if freeze_path.exists():
        raise FileExistsError(f"evidence freeze already exists: {freeze_path}")
    required_paths = {
        "pair_manifest": run_root / "pair-manifest.json",
        "migration": run_root / "evidence-migration.json",
        "study_report": run_root / "study-report.json",
    }
    for label, path in required_paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} is missing: {path}")
    pair_manifest = json.loads(
        required_paths["pair_manifest"].read_text(encoding="utf-8")
    )
    migration = json.loads(required_paths["migration"].read_text(encoding="utf-8"))
    report = json.loads(required_paths["study_report"].read_text(encoding="utf-8"))
    if report.get("shakedown_exit", {}).get("pass") is not True:
        raise ValueError("one-seed product-regression exit has not passed")
    if migration.get("model_calls_added") != 0:
        raise ValueError("evidence migration must not add model calls")
    if pair_manifest.get("protocol_id") != protocol.id:
        raise ValueError("paired-run protocol id does not match the freeze protocol")
    if pair_manifest.get("protocol_sha256") != _sha256(protocol_path):
        raise ValueError("paired-run protocol hash does not match the freeze protocol")

    evidence_files: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, str] = {}
    for arm in protocol.arms:
        for name in ("session.json", "summary.json", "checkpoint.json"):
            path = run_root / arm / name
            if not path.is_file():
                raise FileNotFoundError(f"freeze input is missing: {path}")
            evidence_files[f"{arm}/{name}"] = {
                "path": str(path),
                "sha256": _sha256(path),
            }
        session = json.loads(
            (run_root / arm / "session.json").read_text(encoding="utf-8")
        )
        _require_value_evidence_integrity(session)
        baseline = session.get("value_evidence", {}).get("baseline", {})
        source_hash = baseline.get("source_sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ValueError(f"{arm} selected source hash is missing")
        source_hashes[arm] = source_hash
    for label, path in required_paths.items():
        evidence_files[path.name] = {"path": str(path), "sha256": _sha256(path)}

    original_inputs = json.loads(
        (run_root / protocol.arms[0] / "environment.json").read_text(encoding="utf-8")
    ).get("inputs", {})
    original_hashes = {
        name: value.get("sha256")
        for name, value in original_inputs.items()
        if isinstance(value, dict) and isinstance(value.get("sha256"), str)
    }
    freeze = {
        "schema_version": 1,
        "id": "seed-104729-v2.2-product-regression-freeze",
        "status": "frozen",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "protocol_id": protocol.id,
        "protocol_sha256": _sha256(protocol_path),
        "task_id": pair_manifest.get("task_id"),
        "seed": pair_manifest.get("seed"),
        "model": pair_manifest.get("model"),
        "shakedown_exit": report["shakedown_exit"],
        "population_study": {
            "status": report.get("gates", {}).get("status"),
            "pass": report.get("gates", {}).get("pass"),
            "sample_size_met": report.get("gates", {}).get("sample_size_met"),
        },
        "continuation": {
            "eligible_for_preregistered_seeds": True,
            "requires_fresh_external_egress_approval": True,
            "seeds_not_run": [130363, 155921],
        },
        "model_calls_added_after_controlled_run": 0,
        "selected_source_sha256_by_arm": source_hashes,
        "original_execution_hashes": original_hashes,
        "original_product_tree": pair_manifest.get("viewspec_product_tree"),
        "post_migration_evaluator_hashes": {
            "runner": _sha256(Path(__file__)),
            "session_summary": _sha256(ROOT / "src" / "viewspec" / "agent_eval.py"),
            "browser_scorer": _sha256(BROWSER_SCORER),
            "mutation_manifest": _sha256(MUTATION_MANIFEST),
        },
        "evidence_files": evidence_files,
    }
    _write(freeze_path, canonical_json(freeze))
    print(canonical_json(freeze), end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--task", required=True)
    run.add_argument("--arm", choices=AGENT_UI_EVAL_ARMS, required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--model", required=True, help="Exact Codex model id; required for reproducible comparisons")
    run.add_argument(
        "--allow-user-config",
        action="store_true",
        help="Load user Codex config/plugins (disabled by default to keep arms controlled)",
    )
    run.add_argument("--turn-timeout", type=int, default=900)
    run.add_argument("--no-install", action="store_true", help="Do not install pinned React proof dependencies")
    run.add_argument("--with-value-trials", action="store_true")
    run.add_argument("--resume", action="store_true")
    pair = commands.add_parser("run-pair")
    pair.add_argument("--task", required=True)
    pair.add_argument("--seed", type=int, required=True)
    pair.add_argument("--out", required=True)
    pair.add_argument("--model", required=True, help="Exact Codex model id")
    pair.add_argument("--with-value-trials", action="store_true")
    pair.add_argument("--resume", action="store_true")
    pair.add_argument("--allow-user-config", action="store_true")
    pair.add_argument("--turn-timeout", type=int, default=900)
    pair.add_argument("--no-install", action="store_true")
    summarize = commands.add_parser("summarize")
    summarize.add_argument("--runs", required=True)
    summarize.add_argument("--out")
    migrate = commands.add_parser("migrate-evidence")
    migrate.add_argument("--run-root", required=True)
    freeze = commands.add_parser("freeze-evidence")
    freeze.add_argument("--run-root", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        return _plan(args)
    if args.command == "run":
        return _run_session(args)
    if args.command == "run-pair":
        return _run_pair(args)
    if args.command == "migrate-evidence":
        return _migrate_evidence(args)
    if args.command == "freeze-evidence":
        return _freeze_evidence(args)
    return _summarize(args)


if __name__ == "__main__":
    raise SystemExit(main())
