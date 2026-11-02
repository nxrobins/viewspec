"""Opt-in proof that ``viewspec prove`` judges html-tailwind and mcp-app documents in real Chromium.

Set ``VIEWSPEC_RUN_HTML_VERIFY_E2E=1`` after ``npm ci --ignore-scripts`` and ``npx playwright install
chromium`` in ``src/viewspec/html_verify_template``. Pointing ``VIEWSPEC_HTML_VERIFY_NODE_MODULES_DIR``
at that install skips a network ``npm ci`` per proof. It needs the ``agents`` extra, because it sends
what ``render()`` really returns, and it fails rather than skips without it.

A defect here is a change the compiler did not make, written so that ``check`` still passes: the
manifest hash is refreshed, and the change sits where ``check`` cannot see it. So every failure below
comes from the browser. ``_inject`` refuses a change that does nothing, because a defect that never
landed passes as easily as a healthy document.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from viewspec import ViewSpecBuilder, api, compile_mcp_app
from viewspec.cli import main as cli_main
from viewspec.intent_tools import STARTER_INTENT_KINDS, init_intent_file
from viewspec.local_tools import check_artifact_dir, file_hash
from viewspec.mcp_apps import MCPAppArtifact
from viewspec.types import IntentBundle

E2E_OPT_IN = "VIEWSPEC_RUN_HTML_VERIFY_E2E"
TARGETS = ("html-tailwind", "mcp-app")
VIEWPORTS = ("desktop", "mobile", "tablet")
URI = "ui://ops/console"
PROFILE = "aesthetic.executive_review"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get(E2E_OPT_IN) != "1", reason=f"set {E2E_OPT_IN}=1 to run the Chromium proofs"),
]


def _console_bundle(rows: int = 2) -> IntentBundle:
    builder = ViewSpecBuilder("console", root_attrs={"title": "Incident console"})
    builder.set_aesthetic_profile(PROFILE)
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="Open", value="18", id="open")
    dashboard.add_card(label="Ready", value="7", id="ready")
    dashboard.add_card(label="Late", value="2", id="late")
    table = builder.add_table("incidents", region="main", group_id="rows")
    for number in range(1, rows + 1):
        table.add_row(id=f"inc{number}", label=f"INC-{400 + number}", value=f"Replica lag on shard {number} (14s)")
    query = builder.add_text_input("query", label="Filter", region="main")
    builder.add_collection_action(
        "refresh", kind="filter", label="Refresh", collection_id="incidents", payload_bindings=[query]
    )
    return builder.build_bundle()


@pytest.fixture(scope="module")
def intent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("source") / "viewspec.intent.json"
    path.write_text(json.dumps(_console_bundle().to_json()), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def compiled(intent: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("compiled")
    artifacts = {target: root / target for target in TARGETS}
    for target, out in artifacts.items():
        assert cli_main(["compile", str(intent), "--target", target, "--out", str(out)]) == 0
    return artifacts


@pytest.fixture(scope="module")
def app() -> MCPAppArtifact:
    return compile_mcp_app(_console_bundle(), uri=URI, profile=PROFILE, action_tools={"refresh": "refresh_incidents"})


def _copy(compiled: dict[str, Path], target: str, tmp_path: Path) -> Path:
    artifact = tmp_path / "artifact"
    shutil.copytree(compiled[target], artifact)
    return artifact


def _inject(artifact: Path, change: Callable[[str], str]) -> Path:
    html = artifact / "index.html"
    before = html.read_text(encoding="utf-8")
    after = change(before)
    assert after != before, "this defect changed nothing"
    html.write_text(after, encoding="utf-8")
    manifest_path = artifact / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_hash"] = file_hash(html)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = check_artifact_dir(artifact)
    assert report["ok"], f"check caught this defect first, so the browser proved nothing: {report['errors']}"
    return artifact


def _css(rule: str) -> Callable[[str], str]:
    return lambda html: html.replace("</style>", f"{rule}\n</style>", 1)


def _prove(artifact: Path, tmp_path: Path, *flags: str, name: str = "proof") -> tuple[int, dict[str, Any], Path]:
    out = tmp_path / name
    code = cli_main(["prove", str(artifact), "--install", "--out", str(out), *flags])
    return code, json.loads((out / "result.json").read_text(encoding="utf-8")), out


def _host_report(out: Path) -> dict[str, Any]:
    return json.loads((out / "evidence" / "host-report.json").read_text(encoding="utf-8"))


def _codes(result: dict[str, Any]) -> set[str]:
    return {item["code"] for item in result["diagnostics"] if item["severity"] == "error"}


def _first_error(result: dict[str, Any], code: str) -> dict[str, Any]:
    return next(item for item in result["diagnostics"] if item["code"] == code and item["severity"] == "error")


def _wire(result: Any) -> dict[str, Any]:
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def _render_file(tmp_path: Path, name: str, result: dict[str, Any]) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return str(path)


def _render_with(app: MCPAppArtifact, change: Callable[[str], str], rows: int = 5) -> dict[str, Any]:
    result = _wire(app.render(_console_bundle(rows)))
    before = result["structuredContent"]["viewspecRoot"]
    after = change(before)
    assert after != before, "this defect changed nothing"
    result["structuredContent"]["viewspecRoot"] = after
    return result


# ---- Healthy documents


@pytest.mark.parametrize("target", TARGETS)
def test_a_healthy_document_is_conformant_with_pretext(compiled, target, tmp_path):
    artifact = _copy(compiled, target, tmp_path)
    before = {path.name: file_hash(path) for path in artifact.iterdir()}

    code, result, out = _prove(artifact, tmp_path, "--pretext")

    assert code == 0, result["diagnostics"]
    assert result["status"] == "conformant"
    assert sorted(item["name"] for item in result["plan"]["viewports"]) == sorted(VIEWPORTS)
    # Pretext reached the page by evaluation: the document and the pinned bridge are untouched.
    assert {path.name: file_hash(path) for path in artifact.iterdir()} == before

    report = _host_report(out)
    assert report["target"] == target
    assert report["freerange"] == "not_applicable"
    assert report["text_layout"]["status"] == "passed"
    assert report["text_layout"]["engine"]["version"] == "0.0.8"
    coverage = report["text_layout"]["coverage"]
    assert coverage["measured"] == coverage["required"] > 0
    assert coverage["unsupported"] == coverage["failed"] == 0
    assert report["assertions"]["dom_count"] > 0
    assert report["assertions"]["grid_column_assertion_count"] > 0
    assert report["assertions"]["grid_span_assertion_count"] > 0
    assert report["assertions"]["aesthetic_layout_assertion_count"] > 0
    assert report["assertions"]["aesthetic_profile_assertion_count"] > 0
    assert report["assertions"]["handshake_count"] == (3 if target == "mcp-app" else 0)
    assert report["assertions"]["swap_count"] == (3 if target == "mcp-app" else 0)

    paths = {item["path"] for item in result["evidence"]}
    for viewport in VIEWPORTS:
        assert {f"evidence/{viewport}.{kind}" for kind in ("png", "dom.json", "a11y.json")} <= paths
    assert "evidence/pretext-report.json" in paths


@pytest.mark.parametrize("target", TARGETS)
def test_a_source_proof_reaches_the_same_verdict(intent, target, tmp_path):
    out = tmp_path / "proof"

    assert cli_main(["prove", str(intent), "--target", target, "--install", "--pretext", "--out", str(out)]) == 0

    report = json.loads((out / "proof_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["verification"]["status"] == "conformant"
    assert report["checks"]["pretext"] == "passed"
    assert report["checks"]["freerange"] == "not_applicable"


@pytest.mark.parametrize("target", TARGETS)
def test_a_line_chromium_wraps_inside_the_fit_tolerance_is_not_a_mismatch(target, tmp_path):
    # At 390 pixels "replica lag (14s)" fits the probe's tolerant width and not the measured one.
    builder = ViewSpecBuilder("tolerance", root_attrs={"title": "Tolerance"})
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    table = builder.add_table("incidents", region="main", group_id="rows")
    table.add_row(id="inc1", label="INC-402", value="Database replica lag (14s)")
    builder.add_text_input("query", label="Filter", region="main")
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    artifact = tmp_path / "artifact"
    assert cli_main(["compile", str(source), "--target", target, "--out", str(artifact)]) == 0

    code, result, _ = _prove(artifact, tmp_path, "--pretext")

    assert code == 0, result["diagnostics"]


def _small_document(tmp_path: Path, target: str) -> Path:
    builder = ViewSpecBuilder("small", root_attrs={"title": "Small"})
    builder.add_empty_state("nothing", title="Nothing here", region="main")
    source = tmp_path / "small.intent.json"
    source.write_text(json.dumps(builder.build_bundle().to_json()), encoding="utf-8")
    artifact = tmp_path / "small"
    assert cli_main(["compile", str(source), "--target", target, "--out", str(artifact)]) == 0
    return artifact


@pytest.mark.parametrize("target", TARGETS)
def test_the_smallest_document_clears_the_style_assertion_floor(target, tmp_path):
    code, result, out = _prove(_small_document(tmp_path, target), tmp_path)

    assert code == 0, result["diagnostics"]
    report = _host_report(out)
    assert report["assertions"]["style_assertion_count"] >= report["assertion_requirements"]["style_assertion_count"]


@pytest.mark.parametrize("target", TARGETS)
def test_one_viewport_is_a_complete_plan(target, tmp_path):
    plan = tmp_path / "plan.json"
    viewports = [{"name": "only", "width": 800, "height": 600}]
    checks = ["accessibility", "content", "interaction", "layout", "runtime"]
    plan.write_text(json.dumps({"schema_version": 1, "viewports": viewports, "checks": checks}), encoding="utf-8")

    code, result, _ = _prove(_small_document(tmp_path, target), tmp_path, "--plan", str(plan))

    assert code == 0, result["diagnostics"]
    assert [item["name"] for item in result["plan"]["viewports"]] == ["only"]


@pytest.mark.parametrize("kind", STARTER_INTENT_KINDS)
@pytest.mark.parametrize("target", TARGETS)
def test_every_starter_is_conformant(target, kind, tmp_path):
    source = tmp_path / "viewspec.intent.json"
    init_intent_file(source, kind=kind, force=False)
    artifact = tmp_path / "artifact"
    assert cli_main(["compile", str(source), "--target", target, "--out", str(artifact)]) == 0

    code, result, _ = _prove(artifact, tmp_path, "--pretext")

    assert code == 0, result["diagnostics"]


# ---- Defects in the document


DEFECTS = {
    "overflow": (_css(".vs-root { width: 2000px; }"), "VERIFY_LAYOUT_OVERFLOW"),
    "overlap": (_css(".vs-surface { margin-bottom: -60px; }"), "VERIFY_LAYOUT_OVERLAP"),
    "grid columns": (
        lambda html: html.replace("grid-template-columns: repeat(3,", "grid-template-columns: repeat(5,", 1),
        "VERIFY_LAYOUT_CONFORMANCE_FAILED",
    ),
    "grid display": (_css(".vs-grid { display: block !important; }"), "VERIFY_LAYOUT_CONFORMANCE_FAILED"),
    "aesthetic span": (_css(".vs-span-2 { grid-column: auto !important; }"), "VERIFY_LAYOUT_CONFORMANCE_FAILED"),
    "unnamed button": (
        lambda html: html.replace("</main>", '<button type="button"></button></main>', 1),
        "VERIFY_A11Y_VIOLATION",
    ),
    "unnamed input": (
        lambda html: html.replace("</main>", '<input type="text"></main>', 1),
        "VERIFY_A11Y_VIOLATION",
    ),
}


@pytest.mark.parametrize("name", DEFECTS)
@pytest.mark.parametrize("target", TARGETS)
def test_a_defective_document_is_nonconformant(compiled, target, name, tmp_path):
    change, expected = DEFECTS[name]
    artifact = _inject(_copy(compiled, target, tmp_path), change)

    code, result, _ = _prove(artifact, tmp_path)

    assert code == 1, result["diagnostics"]
    assert result["status"] == "nonconformant"
    assert expected in _codes(result)
    finding = _first_error(result, expected)
    assert finding["viewport"] in VIEWPORTS
    assert finding["evidence_refs"]
    assert set(finding["evidence_refs"]) <= {item["path"] for item in result["evidence"]}


@pytest.mark.parametrize("target", TARGETS)
def test_pretext_finds_text_the_layout_clips(compiled, target, tmp_path):
    artifact = _inject(_copy(compiled, target, tmp_path), _css(".vs-value { max-height: 4px; overflow: hidden; }"))

    code, result, out = _prove(artifact, tmp_path, "--pretext")

    assert code == 1, result["diagnostics"]
    assert "VERIFY_LAYOUT_OVERFLOW" in _codes(result)
    assert _host_report(out)["text_layout"]["status"] == "failed"
    finding = next(item for item in result["diagnostics"] if "Pretext" in item["message"])
    assert finding["source_ref"].startswith("ir:")
    assert finding["evidence_refs"] == ["evidence/pretext-report.json"]


@pytest.mark.parametrize("target", TARGETS)
def test_clipped_text_stays_out_of_the_proof_without_pretext(compiled, target, tmp_path):
    # Pretext is what sees this one, so the opt-in flag is doing real work.
    artifact = _inject(_copy(compiled, target, tmp_path), _css(".vs-value { max-height: 4px; overflow: hidden; }"))

    code, result, _ = _prove(artifact, tmp_path)

    assert code == 0, result["diagnostics"]


# ---- The MCP App handshake


def test_a_view_that_cannot_finish_the_handshake_is_nonconformant(compiled, tmp_path):
    def drop_tool_map(html: str) -> str:
        return re.sub(r'<meta name="viewspec-mcp-tools"[^>]*>\n', "", html)

    artifact = _inject(_copy(compiled, "mcp-app", tmp_path), drop_tool_map)

    code, result, out = _prove(artifact, tmp_path)

    assert code == 1, result["diagnostics"]
    assert "VERIFY_RUNTIME_ERROR" in _codes(result)
    assert any("ui/initialize" in item["message"] for item in result["diagnostics"])
    assert _host_report(out)["assertions"]["handshake_count"] == 0


# ---- app.render() roots swapped into a live view


def test_real_render_results_swap_in_and_stay_conformant(app, compiled, tmp_path):
    renders = [
        _render_file(tmp_path, "five", _wire(app.render(_console_bundle(5)))),
        _render_file(tmp_path, "one", _wire(app.render(_console_bundle(1)))),
    ]
    artifact = _copy(compiled, "mcp-app", tmp_path)

    code, result, out = _prove(artifact, tmp_path, *[part for path in renders for part in ("--render", path)])

    assert code == 0, result["diagnostics"]
    assert _host_report(out)["assertions"]["swap_count"] == 3 * (1 + len(renders))
    paths = {item["path"] for item in result["evidence"]}
    assert {f"evidence/{viewport}.render-2.png" for viewport in VIEWPORTS} <= paths
    # A swap really happened: the evidence shows the five rows the compiled document does not have.
    five = json.loads((out / "evidence" / "mobile.render-1.dom.json").read_text(encoding="utf-8"))
    assert sum(node["tag"] == "tr" for node in five["nodes"]) == 5


def test_render_results_are_accepted_from_python(app, compiled, tmp_path):
    artifact = _copy(compiled, "mcp-app", tmp_path)

    result = api.prove(artifact, out_dir=tmp_path / "proof", install=True, renders=[app.render(_console_bundle(5))])

    assert result["status"] == "conformant", result["diagnostics"]


RENDER_DEFECTS = {
    "overflow": (lambda root: root.replace("<main ", '<main style="width: 2000px" ', 1), "VERIFY_LAYOUT_OVERFLOW"),
    "unnamed input": (lambda root: root.replace(' aria-label="Filter"', "", 1), "VERIFY_A11Y_VIOLATION"),
    "grid columns": (
        lambda root: root.replace("grid-template-columns: repeat(3, minmax(0, 1fr));", "", 1),
        "VERIFY_LAYOUT_CONFORMANCE_FAILED",
    ),
}


@pytest.mark.parametrize("name", RENDER_DEFECTS)
def test_a_defective_render_is_nonconformant(app, compiled, name, tmp_path):
    change, expected = RENDER_DEFECTS[name]
    render = _render_file(tmp_path, "render", _render_with(app, change))

    code, result, _ = _prove(_copy(compiled, "mcp-app", tmp_path), tmp_path, "--render", render)

    assert code == 1, result["diagnostics"]
    assert expected in _codes(result)
    finding = _first_error(result, expected)
    assert finding["message"].startswith("After render-1: ")
    assert "render-1" in finding["evidence_refs"][0]


def test_a_root_the_bridge_refuses_is_nonconformant(compiled, tmp_path):
    forged = {
        "content": [],
        "structuredContent": {"viewspecRoot": '<main class="vs-root"><img src=x onerror="window.pwned=1"></main>'},
    }
    render = _render_file(tmp_path, "forged", forged)

    code, result, out = _prove(_copy(compiled, "mcp-app", tmp_path), tmp_path, "--render", render)

    assert code == 1, result["diagnostics"]
    assert any("refused the root" in item["message"] for item in result["diagnostics"])
    # The compiled root swapped and the forged one did not, at each viewport.
    assert _host_report(out)["assertions"]["swap_count"] == 3


def test_a_defect_in_the_compiled_root_shows_after_a_swap_too(compiled, tmp_path):
    artifact = _inject(_copy(compiled, "mcp-app", tmp_path), _css(".vs-root { width: 2000px; }"))

    _, result, _ = _prove(artifact, tmp_path)

    assert any(item["message"].startswith("After swap: ") for item in result["diagnostics"])
