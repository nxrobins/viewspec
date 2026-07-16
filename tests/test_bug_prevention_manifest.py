from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "bug_prevention_manifest.json"
EXPECTED_BUG_IDS = {f"VSBUG-{number:03d}" for number in range(1, 41)}
PROPERTY_REQUIRED = {
    "VSBUG-008",
    "VSBUG-016",
    "VSBUG-023",
    "VSBUG-024",
    "VSBUG-025",
    "VSBUG-026",
    "VSBUG-028",
    "VSBUG-030",
    "VSBUG-031",
    "VSBUG-037",
    "VSBUG-040",
}
BROWSER_REQUIRED = {
    "VSBUG-001",
    "VSBUG-003",
    "VSBUG-004",
    "VSBUG-005",
    "VSBUG-006",
    "VSBUG-007",
    "VSBUG-009",
    "VSBUG-010",
    "VSBUG-011",
    "VSBUG-012",
    "VSBUG-013",
    "VSBUG-014",
    "VSBUG-038",
    "VSBUG-039",
}


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _python_functions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _is_hypothesis_property(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (isinstance(decorator, ast.Name) and decorator.id == "given")
        or (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id == "given")
        for decorator in node.decorator_list
    )


def test_historical_bug_prevention_manifest_is_closed_and_machine_checked() -> None:
    manifest = _load_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["scope"] == "viewspec repository history through pull request 135"
    entries = manifest["bugs"]
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)), "bug prevention ids must be unique"
    assert set(ids) == EXPECTED_BUG_IDS, "the lifetime catalogue is closed; omissions require an explicit test change"

    for entry in entries:
        assert entry["name"].strip()
        assert entry["invariant"].strip()
        assert entry["fixed_by"].startswith("https://github.com/nxrobins/viewspec/")
        assert entry["guards"], f"{entry['id']} has no executable prevention guard"


def test_historical_fix_evidence_resolves_inside_the_declared_history_boundary() -> None:
    manifest = _load_manifest()
    tip = manifest["history_tip"]
    fixes = manifest["fix_commits"]
    assert re.fullmatch(r"[0-9a-f]{40}", tip)
    assert set(fixes) == EXPECTED_BUG_IDS

    for bug_id, commits in fixes.items():
        assert commits, f"{bug_id} has no authoritative fixing commit"
        assert len(commits) == len(set(commits)), f"{bug_id} repeats a fixing commit"
        for commit in commits:
            assert re.fullmatch(r"[0-9a-f]{40}", commit), f"{bug_id} has an abbreviated or invalid commit id"
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, tip],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"{bug_id} fixing commit is absent or outside the declared history: {commit}"


def test_every_manifest_guard_resolves_to_executable_evidence() -> None:
    manifest = _load_manifest()
    python_cache: dict[Path, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}

    for entry in manifest["bugs"]:
        for guard in entry["guards"]:
            path = ROOT / guard["path"]
            assert path.is_file(), f"{entry['id']} guard file does not exist: {guard['path']}"
            if guard["kind"] in {"pytest", "property"}:
                functions = python_cache.setdefault(path, _python_functions(path))
                symbol = guard["symbol"]
                assert symbol in functions, f"{entry['id']} guard function does not exist: {guard['path']}::{symbol}"
                assert symbol.startswith("test_"), f"{entry['id']} guard is not collected by pytest: {symbol}"
                if guard["kind"] == "property":
                    assert _is_hypothesis_property(functions[symbol]), f"{entry['id']} property guard lacks @given"
            else:
                anchor = guard["anchor"]
                assert anchor in path.read_text(encoding="utf-8"), (
                    f"{entry['id']} {guard['kind']} guard anchor disappeared from {guard['path']}"
                )


def test_high_variance_classes_require_properties_and_browser_classes_require_runtime_proof() -> None:
    manifest = _load_manifest()
    by_id = {entry["id"]: entry for entry in manifest["bugs"]}
    for bug_id in PROPERTY_REQUIRED:
        assert any(guard["kind"] == "property" for guard in by_id[bug_id]["guards"]), (
            f"{bug_id} needs generative coverage, not a finite example table"
        )
    for bug_id in BROWSER_REQUIRED:
        assert any(guard["kind"] == "browser" for guard in by_id[bug_id]["guards"]), (
            f"{bug_id} is browser-observable and needs a real-browser CI guard"
        )


def test_ci_keeps_every_prevention_gate_mandatory() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    required = {
        "ruff check .",
        "python -m compileall src tests examples scripts",
        "python -m pytest -q",
        "node tests/landing_payload_smoke.mjs",
        "node tests/landing_config_smoke.mjs",
        "node tests/landing_compile_smoke.mjs",
        "node tests/commercial_contract_smoke.mjs",
        "node tests/checkout_claim_smoke.mjs",
        "node tests/seo_static_smoke.mjs",
        "python -m build",
        "python scripts/check_distribution.py dist",
        "npm run test:site",
        "git diff --check $(git hash-object -t tree /dev/null) HEAD",
    }
    missing = sorted(command for command in required if command not in workflow)
    assert not missing, f"CI prevention gates were removed: {missing}"
    assert "continue-on-error" not in workflow
    assert "fetch-depth: 0" in workflow, "history-backed bug evidence requires a full checkout"

    package = json.loads((ROOT / "tests" / "react-tailwind-host" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["test:site"] == "playwright test -c site-playwright.config.ts"
    site_config = (ROOT / "tests" / "react-tailwind-host" / "site-playwright.config.ts").read_text(encoding="utf-8")
    assert 'testMatch: "site-regressions.spec.ts"' in site_config
    assert 'command: "python -m http.server 4178 --bind 127.0.0.1 --directory ../../demos"' in site_config
    assert workflow.index("npx playwright install --with-deps chromium") < workflow.index("npm run test:site")
