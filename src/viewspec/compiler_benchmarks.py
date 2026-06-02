"""Internal structural benchmarks for local IntentBundle compiler quality.

These benchmarks are a compiler-quality floor, not a visual equivalence proof.
They intentionally use no browser or screenshot dependencies.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from viewspec.agent import validate_agent_intent_bundle
from viewspec.compiler import compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.intent_tools import wrap_intent_bundle_manifest
from viewspec.local_tools import check_artifact_dir, file_hash, source_hash
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import ASTBundle, IntentBundle, IRNode


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_SUMMARY_MAX_BYTES = 16 * 1024
BENCHMARK_TIMEOUT_SECONDS = 5.0
BENCHMARK_ERROR_CODES = frozenset(
    {
        "BENCHMARK_FIXTURE_TOO_SMALL",
        "BENCHMARK_ORACLE_TOO_SHALLOW",
        "BENCHMARK_METRIC_NOT_DERIVED",
        "BENCHMARK_LAYOUT_PRESSURE_MISSING",
        "UNEXPECTED_DIAGNOSTIC",
        "FULL_ARTIFACT_GOLDEN_FORBIDDEN",
        "EMITTER_PARITY_FAILED",
        "BENCHMARK_SUMMARY_TOO_LARGE",
        "BENCHMARK_ERROR_SHAPE_INVALID",
        "NONDETERMINISTIC_BENCHMARK_FIELD",
        "BENCHMARK_NEW_DEPENDENCY_FORBIDDEN",
        "BENCHMARK_TIMEOUT_EXCEEDED",
    }
)
QUALITY_CATEGORIES = frozenset(
    {
        "semantics",
        "layout",
        "provenance",
        "safety",
        "design_tokens",
        "emitter_parity",
        "determinism",
    }
)
ALLOWED_METRIC_SOURCES = frozenset({"artifact_hash", "artifact_text", "ast", "check", "diagnostics", "manifest"})
NETWORK_SURFACE_RE = re.compile(r"(?i)(https?://|url\s*\(|fetch\s*\(|XMLHttpRequest|WebSocket|EventSource)")
ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2}|/)")
ISO_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?")
TEMP_NAME_RE = re.compile(r"(?i)(?:^|[\\/])(?:tmp|temp|pytest-|viewspec-benchmark-)")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
HTML_TAG_RE = re.compile(r"<(?!/)([A-Za-z][A-Za-z0-9]*)\b")
REQUIRED_TAGS_BY_MOTIF = {
    "dashboard": set(),
    "detail": {"dd", "dl", "dt"},
    "form": {"input", "section"},
    "hero": {"h1", "header"},
    "list": {"li", "ul"},
    "table": {"table", "td", "th", "tr"},
}


class BenchmarkConstraintError(ValueError):
    """A fail-fast benchmark contract violation."""

    def __init__(self, code: str, fixture_id: str, message: str) -> None:
        if code not in BENCHMARK_ERROR_CODES or not fixture_id:
            code = "BENCHMARK_ERROR_SHAPE_INVALID"
            fixture_id = fixture_id or "<unknown>"
            message = "Benchmark errors must include one stable code and a non-empty fixture id."
        super().__init__(f"{code} [{fixture_id}]: {message}")
        self.code = code
        self.fixture_id = fixture_id
        self.message = message

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "fixture_id": self.fixture_id, "message": self.message}


@dataclass(frozen=True)
class BenchmarkFixture:
    id: str
    bundle: IntentBundle
    expected_diagnostic_codes: tuple[str, ...] = ()
    multi_region: bool = False
    full_artifact_golden: str | None = None
    benchmark_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmitterBenchmarkArtifact:
    target: str
    artifact_hash: str
    manifest_hash: str
    manifest: dict[str, Any]
    diagnostics: list[dict[str, Any]]
    artifact_text: str
    check_ok: bool
    check_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticInventory:
    visible_text: frozenset[str] = field(default_factory=frozenset)
    binding_ids: frozenset[str] = field(default_factory=frozenset)
    action_ids: frozenset[str] = field(default_factory=frozenset)
    motif_ids: frozenset[str] = field(default_factory=frozenset)
    manifest_node_ids: frozenset[str] = field(default_factory=frozenset)
    diagnostic_codes: frozenset[str] = field(default_factory=frozenset)
    provenance_refs: frozenset[str] = field(default_factory=frozenset)
    style_tokens: frozenset[str] = field(default_factory=frozenset)

    def to_json(self) -> dict[str, list[str]]:
        return {
            "visible_text": sorted(self.visible_text),
            "binding_ids": sorted(self.binding_ids),
            "action_ids": sorted(self.action_ids),
            "motif_ids": sorted(self.motif_ids),
            "manifest_node_ids": sorted(self.manifest_node_ids),
            "diagnostic_codes": sorted(self.diagnostic_codes),
            "provenance_refs": sorted(self.provenance_refs),
            "style_tokens": sorted(self.style_tokens),
        }


class _HtmlTagProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.text: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text.add(value)


def benchmark_fixtures() -> tuple[BenchmarkFixture, ...]:
    return (
        BenchmarkFixture("dashboard", _dashboard_fixture()),
        BenchmarkFixture("form", _form_fixture()),
        BenchmarkFixture("detail", _detail_fixture()),
        BenchmarkFixture("hero", _hero_fixture()),
        BenchmarkFixture("list", _list_fixture()),
        BenchmarkFixture("multi_region_product", _multi_region_fixture(), multi_region=True),
    )


def run_benchmark_suite(
    output_root: str | Path,
    *,
    timeout_seconds: float = BENCHMARK_TIMEOUT_SECONDS,
    benchmark_dependencies: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    started_at = time.perf_counter()
    assert_no_new_benchmark_dependencies(benchmark_dependencies, fixture_id="suite")
    summaries: list[dict[str, Any]] = []
    root = Path(output_root)
    for fixture in benchmark_fixtures():
        assert_benchmark_timeout(started_at, timeout_seconds, fixture_id=fixture.id)
        summaries.append(run_benchmark_fixture(fixture, root / fixture.id))
    return summaries


def run_benchmark_fixture(fixture: BenchmarkFixture, output_dir: str | Path) -> dict[str, Any]:
    _validate_fixture_contract(fixture)
    validation = validate_agent_intent_bundle(fixture.bundle.to_json())
    if not validation.valid:
        first_issue = validation.issues[0].code if validation.issues else "invalid"
        raise BenchmarkConstraintError(
            "BENCHMARK_FIXTURE_TOO_SMALL",
            fixture.id,
            f"Fixture is not a valid local IntentBundle: {first_issue}.",
        )
    ast = compile(fixture.bundle)
    assert_expected_diagnostics(
        fixture.id,
        actual_codes=tuple(diagnostic.code for diagnostic in ast.result.diagnostics),
        expected_codes=fixture.expected_diagnostic_codes,
    )

    output = Path(output_dir)
    html_artifact = _emit_checked_artifact(fixture, ast, output / "html", target="html-tailwind")
    react_artifact = _emit_checked_artifact(fixture, ast, output / "react", target="react-tsx")
    summary = _benchmark_summary(fixture, ast, html_artifact, react_artifact)
    assert_benchmark_summary(summary)
    return summary


def assert_no_new_benchmark_dependencies(dependency_names: tuple[str, ...] | list[str], *, fixture_id: str = "suite") -> None:
    if dependency_names:
        raise BenchmarkConstraintError(
            "BENCHMARK_NEW_DEPENDENCY_FORBIDDEN",
            fixture_id,
            f"Benchmark dependencies are forbidden in this PR: {', '.join(sorted(dependency_names))}.",
        )


def assert_benchmark_timeout(started_at: float, timeout_seconds: float, *, fixture_id: str) -> None:
    if time.perf_counter() - started_at > timeout_seconds:
        raise BenchmarkConstraintError(
            "BENCHMARK_TIMEOUT_EXCEEDED",
            fixture_id,
            f"Benchmark suite exceeded {timeout_seconds:g}s.",
        )


def assert_expected_diagnostics(fixture_id: str, *, actual_codes: tuple[str, ...], expected_codes: tuple[str, ...]) -> None:
    unexpected = sorted(set(actual_codes) - set(expected_codes))
    if unexpected:
        raise BenchmarkConstraintError(
            "UNEXPECTED_DIAGNOSTIC",
            fixture_id,
            f"Unexpected diagnostic code(s): {', '.join(unexpected)}.",
        )


def assert_emitter_parity(fixture_id: str, html_inventory: SemanticInventory, react_inventory: SemanticInventory) -> None:
    html_json = html_inventory.to_json()
    react_json = react_inventory.to_json()
    compared_keys = (
        "visible_text",
        "binding_ids",
        "action_ids",
        "motif_ids",
        "manifest_node_ids",
        "diagnostic_codes",
        "provenance_refs",
    )
    drift = [key for key in compared_keys if html_json[key] != react_json[key]]
    if drift:
        raise BenchmarkConstraintError(
            "EMITTER_PARITY_FAILED",
            fixture_id,
            f"HTML and React semantic inventory drifted for: {', '.join(drift)}.",
        )


def assert_benchmark_summary(summary: dict[str, Any]) -> None:
    fixture_id = str(summary.get("fixture_id") or "")
    sources = summary.get("metric_sources")
    if not isinstance(sources, dict) or not sources:
        raise BenchmarkConstraintError("BENCHMARK_METRIC_NOT_DERIVED", fixture_id, "Benchmark metric sources are missing.")
    for name, source in sources.items():
        if not isinstance(name, str) or source not in ALLOWED_METRIC_SOURCES:
            raise BenchmarkConstraintError(
                "BENCHMARK_METRIC_NOT_DERIVED",
                fixture_id,
                f"Metric {name!r} is not derived from an allowed artifact source.",
            )

    categories = summary.get("quality_categories")
    if not isinstance(categories, list) or len(set(categories) & QUALITY_CATEGORIES) < 4:
        raise BenchmarkConstraintError(
            "BENCHMARK_ORACLE_TOO_SHALLOW",
            fixture_id,
            "Each benchmark summary must prove at least four quality categories.",
        )

    encoded = stable_summary_json(summary)
    if len(encoded.encode("utf-8")) > BENCHMARK_SUMMARY_MAX_BYTES:
        raise BenchmarkConstraintError(
            "BENCHMARK_SUMMARY_TOO_LARGE",
            fixture_id,
            f"Benchmark summary exceeds {BENCHMARK_SUMMARY_MAX_BYTES} bytes.",
        )
    _reject_nondeterministic_fields(summary, fixture_id)


def stable_summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _dashboard_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_dashboard")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="metric_cards")
    dashboard.add_card(label="Revenue", value="$42.8K", id="revenue")
    dashboard.add_card(label="Activation", value="64%", id="activation")
    dashboard.add_card(label="Risk", value="Low", id="risk", value_present_as="badge")
    builder.add_style("dashboard_surface", "motif:metrics", "surface.strong")
    builder.add_style("dashboard_value_tone", "binding:revenue_value", "tone.positive")
    return builder.build_bundle()


def _form_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_form")
    form = builder.add_form("intake", region="main", group_id="fields")
    form.add_field(label="Name", value="", id="name")
    form.add_field(label="Email", value="", id="email")
    form.add_field(label="Company", value="Acme", id="company")
    builder.add_action(
        "submit_intake",
        "submit",
        "Submit request",
        target_region="main",
        target_ref="motif:intake",
        payload_bindings=["name_value", "email_value", "company_value"],
    )
    builder.add_style("form_surface", "motif:intake", "surface.subtle")
    return builder.build_bundle()


def _detail_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_detail")
    detail = builder.add_detail("account", region="main", group_id="fields")
    detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
    detail.add_field(label="Status", value="Ready", id="status")
    detail.add_field(label="Next step", value="Review compiled artifacts", id="next_step")
    builder.add_style("detail_density", "motif:account", "density.airy")
    return builder.build_bundle()


def _hero_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_hero")
    builder.add_hero(
        "intro",
        eyebrow="Agent-native UI",
        title="Describe intent, not DOM",
        description="ViewSpec compiles semantic UI intent into checked artifacts.",
        region="main",
        group_id="message",
    )
    builder.add_action("hero_cta", "navigate", "Open dashboard", target_region="main", target_ref="region:main")
    builder.add_style("hero_emphasis", "motif:intro", "rhythm.hierarchy")
    return builder.build_bundle()


def _list_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_list")
    steps = builder.add_list("next_steps", region="main", group_id="steps")
    steps.add_item(label="Capture intent", description="Write viewspec.intent.json.", id="intent")
    steps.add_item(label="Validate", description="Run validate-intent.", id="validate")
    steps.add_item(label="Compile", description="Emit checked artifacts.", id="compile")
    builder.add_style("list_flow", "motif:next_steps", "narrative.flow")
    return builder.build_bundle()


def _multi_region_fixture() -> IntentBundle:
    builder = ViewSpecBuilder(
        "benchmark_workspace",
        root_attrs={"title": "Compiler Quality Workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.add_region("header", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("body", parent_region="root", role="main", layout="grid", min_children=2)
    builder.add_region("main", parent_region="body", role="main", layout="stack", min_children=2)
    builder.add_region("side", parent_region="body", role="complementary", layout="stack", min_children=1)

    builder.add_hero(
        "workspace_intro",
        eyebrow="Compiler Quality",
        title="Benchmark the floor",
        description="Measure structural quality before changing layout behavior.",
        region="header",
        group_id="intro_message",
    )
    dashboard = builder.add_dashboard("workspace_metrics", region="main", group_id="metrics")
    dashboard.add_card(label="Fixtures", value="6", id="fixtures")
    dashboard.add_card(label="Emitters", value="2", id="emitters")
    form = builder.add_form("review_form", region="main", group_id="review_fields")
    form.add_field(label="Reviewer", value="", id="reviewer")
    form.add_field(label="Decision", value="approve", id="decision")
    detail = builder.add_detail("artifact_identity", region="side", group_id="identity_fields")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    detail.add_field(label="Network", value="none", id="network")
    builder.add_action(
        "submit_review",
        "submit",
        "Submit review",
        target_region="main",
        target_ref="motif:review_form",
        payload_bindings=["reviewer_value", "decision_value"],
    )
    builder.add_style("workspace_body_density", "region:body", "density.regular")
    builder.add_style("workspace_identity_surface", "motif:artifact_identity", "surface.strong")
    return builder.build_bundle()


def _validate_fixture_contract(fixture: BenchmarkFixture) -> None:
    if fixture.full_artifact_golden is not None:
        raise BenchmarkConstraintError(
            "FULL_ARTIFACT_GOLDEN_FORBIDDEN",
            fixture.id,
            "Full generated HTML/TSX golden oracles are forbidden.",
        )
    assert_no_new_benchmark_dependencies(fixture.benchmark_dependencies, fixture_id=fixture.id)

    bundle = fixture.bundle
    node_count = len(bundle.substrate.nodes)
    binding_count = len(bundle.view_spec.bindings)
    if node_count < 2 or binding_count < 2:
        raise BenchmarkConstraintError(
            "BENCHMARK_FIXTURE_TOO_SMALL",
            fixture.id,
            "Each benchmark fixture must contain at least 2 semantic nodes and 2 bindings.",
        )
    if fixture.multi_region:
        _validate_multi_region_pressure(fixture)


def _validate_multi_region_pressure(fixture: BenchmarkFixture) -> None:
    view_spec = fixture.bundle.view_spec
    if len(view_spec.regions) < 3 or len(view_spec.motifs) < 2 or len(view_spec.actions) < 1:
        raise BenchmarkConstraintError(
            "BENCHMARK_LAYOUT_PRESSURE_MISSING",
            fixture.id,
            "Multi-region fixture must contain at least 3 regions, 2 motifs, and 1 action.",
        )
    depth_by_region = _region_depths(fixture.bundle)
    sibling_counts: dict[str | None, int] = {}
    for region in view_spec.regions:
        sibling_counts[region.parent_region or None] = sibling_counts.get(region.parent_region or None, 0) + 1
    non_primary_motif = any(motif.region != "main" for motif in view_spec.motifs)
    if max(depth_by_region.values(), default=0) < 2 or max(sibling_counts.values(), default=0) < 2 or not non_primary_motif:
        raise BenchmarkConstraintError(
            "BENCHMARK_LAYOUT_PRESSURE_MISSING",
            fixture.id,
            "Multi-region fixture must have depth >= 2, sibling regions, and a motif outside main.",
        )


def _emit_checked_artifact(
    fixture: BenchmarkFixture,
    ast: ASTBundle,
    output_dir: Path,
    *,
    target: str,
) -> EmitterBenchmarkArtifact:
    if target == "html-tailwind":
        paths = HtmlTailwindEmitter().emit(ast, output_dir)
        artifact_path = Path(paths["html"])
        emitter = "html_tailwind"
    elif target == "react-tsx":
        paths = ReactTsxEmitter().emit(ast, output_dir)
        artifact_path = Path(paths["tsx"])
        emitter = "react_tsx"
    else:
        raise ValueError(f"Unsupported benchmark target: {target}")

    source_text = json.dumps(fixture.bundle.to_json(), ensure_ascii=True, sort_keys=True)
    wrap_intent_bundle_manifest(
        Path(paths["manifest"]),
        source_name=f"{fixture.id}.intent.json",
        raw_source_hash=source_hash(source_text),
        design=None,
        command_args=_command_args(fixture.id, target),
        artifact_path=artifact_path,
        emitter=emitter,
    )
    checked = check_artifact_dir(output_dir)
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    diagnostics = json.loads(Path(paths["diagnostics"]).read_text(encoding="utf-8"))
    artifact_text = artifact_path.read_text(encoding="utf-8")
    return EmitterBenchmarkArtifact(
        target=target,
        artifact_hash=file_hash(artifact_path),
        manifest_hash=file_hash(Path(paths["manifest"])),
        manifest=manifest,
        diagnostics=diagnostics,
        artifact_text=artifact_text,
        check_ok=bool(checked["ok"]),
        check_errors=tuple(str(item) for item in checked.get("errors", [])),
    )


def _command_args(fixture_id: str, target: str) -> list[str]:
    command = ["viewspec", "compile", f"{fixture_id}.intent.json"]
    if target != "html-tailwind":
        command.extend(["--target", target])
    command.extend(["--out", "<out>"])
    return command


def _benchmark_summary(
    fixture: BenchmarkFixture,
    ast: ASTBundle,
    html_artifact: EmitterBenchmarkArtifact,
    react_artifact: EmitterBenchmarkArtifact,
) -> dict[str, Any]:
    html_inventory = _manifest_inventory(html_artifact.manifest)
    react_inventory = _manifest_inventory(react_artifact.manifest)
    assert_emitter_parity(fixture.id, html_inventory, react_inventory)

    html_tags, html_text = _html_tags_and_text(html_artifact.artifact_text)
    react_tags = _react_tags(react_artifact.artifact_text)
    required_tags = _required_tags(fixture.bundle)
    semantic_tags_ok = required_tags.issubset(html_tags) and required_tags.issubset(react_tags)
    check_ok = html_artifact.check_ok and react_artifact.check_ok
    no_network_surfaces = not NETWORK_SURFACE_RE.search(html_artifact.artifact_text) and not NETWORK_SURFACE_RE.search(
        react_artifact.artifact_text
    )
    style_tokens = sorted(html_inventory.style_tokens | react_inventory.style_tokens)
    style_values = set(ast.style_values)
    quality_categories = sorted(
        category
        for category, ok in {
            "semantics": semantic_tags_ok,
            "layout": _ir_depth(ast.result.root.root) >= 2 and bool(_layout_counts(ast.result.root.root)),
            "provenance": _provenance_coverage(html_artifact.manifest) == 1.0
            and _provenance_coverage(react_artifact.manifest) == 1.0,
            "safety": check_ok and no_network_surfaces and not html_artifact.manifest.get("external_refs"),
            "design_tokens": bool(style_tokens) and set(style_tokens).issubset(style_values),
            "emitter_parity": True,
        }.items()
        if ok
    )
    if html_artifact.check_errors or react_artifact.check_errors:
        quality_categories = [category for category in quality_categories if category != "safety"]

    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "fixture_id": fixture.id,
        "baseline_role": "current_floor_not_quality_ceiling",
        "quality_categories": quality_categories,
        "source_hash": _bundle_hash(fixture.bundle),
        "metric_sources": {
            "artifact_hashes": "artifact_hash",
            "artifact_text": "artifact_text",
            "ast_shape": "ast",
            "checks": "check",
            "diagnostics": "diagnostics",
            "manifests": "manifest",
        },
        "metrics": {
            "ast": {
                "ir_depth": _ir_depth(ast.result.root.root),
                "layout_counts": _layout_counts(ast.result.root.root),
                "motif_kinds": sorted({motif.kind for motif in fixture.bundle.view_spec.motifs}),
                "region_depth": max(_region_depths(fixture.bundle).values(), default=0),
                "region_count": len(fixture.bundle.view_spec.regions),
            },
            "diagnostics": {
                "actual_codes": sorted({diagnostic.code for diagnostic in ast.result.diagnostics}),
                "expected_codes": sorted(fixture.expected_diagnostic_codes),
            },
            "html": {
                "artifact_hash": html_artifact.artifact_hash,
                "check_ok": html_artifact.check_ok,
                "html_tags": sorted(html_tags),
                "manifest_hash": html_artifact.manifest_hash,
                "visible_text_count": len(html_text),
            },
            "react": {
                "artifact_hash": react_artifact.artifact_hash,
                "check_ok": react_artifact.check_ok,
                "manifest_hash": react_artifact.manifest_hash,
                "tsx_tags": sorted(react_tags),
            },
            "parity": html_inventory.to_json(),
            "required_tags": sorted(required_tags),
            "style_tokens": style_tokens,
        },
    }
    return summary


def _bundle_hash(bundle: IntentBundle) -> str:
    encoded = json.dumps(bundle.to_json(), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_inventory(manifest: dict[str, Any]) -> SemanticInventory:
    nodes = manifest.get("nodes", {})
    diagnostics = manifest.get("diagnostics", [])
    visible_text: set[str] = set()
    binding_ids: set[str] = set()
    action_ids: set[str] = set()
    motif_ids: set[str] = set()
    provenance_refs: set[str] = set()
    style_tokens: set[str] = set()
    if isinstance(nodes, dict):
        for entry in nodes.values():
            if not isinstance(entry, dict):
                continue
            props = entry.get("props", {})
            if isinstance(props, dict):
                text = props.get("text")
                if isinstance(text, str) and text:
                    visible_text.add(text)
                binding_id = props.get("binding_id")
                if isinstance(binding_id, str) and binding_id:
                    binding_ids.add(binding_id)
                action_id = props.get("action_id")
                if isinstance(action_id, str) and action_id:
                    action_ids.add(action_id)
            for ref in entry.get("intent_refs", []):
                if isinstance(ref, str):
                    provenance_refs.add(ref)
                    if ref.startswith("viewspec:motif:"):
                        motif_ids.add(ref.rsplit(":", 1)[-1])
            for ref in entry.get("content_refs", []):
                if isinstance(ref, str):
                    provenance_refs.add(ref)
            for token in entry.get("style_tokens", []):
                if isinstance(token, str):
                    style_tokens.add(token)
    diagnostic_codes = {
        item["code"]
        for item in diagnostics
        if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code")
    }
    return SemanticInventory(
        visible_text=frozenset(visible_text),
        binding_ids=frozenset(binding_ids),
        action_ids=frozenset(action_ids),
        motif_ids=frozenset(motif_ids),
        manifest_node_ids=frozenset(nodes if isinstance(nodes, dict) else ()),
        diagnostic_codes=frozenset(diagnostic_codes),
        provenance_refs=frozenset(provenance_refs),
        style_tokens=frozenset(style_tokens),
    )


def _html_tags_and_text(html: str) -> tuple[set[str], set[str]]:
    parser = _HtmlTagProbe()
    parser.feed(html)
    parser.close()
    return parser.tags, parser.text


def _react_tags(tsx: str) -> set[str]:
    return {match.group(1).lower() for match in HTML_TAG_RE.finditer(tsx)}


def _required_tags(bundle: IntentBundle) -> set[str]:
    required: set[str] = set()
    for motif in bundle.view_spec.motifs:
        required.update(REQUIRED_TAGS_BY_MOTIF.get(motif.kind, set()))
    return required


def _ir_depth(node: IRNode) -> int:
    if not node.children:
        return 1
    return 1 + max(_ir_depth(child) for child in node.children)


def _layout_counts(node: IRNode) -> dict[str, int]:
    counts: dict[str, int] = {}
    if node.primitive in {"cluster", "grid", "root", "stack", "surface"}:
        counts[node.primitive] = counts.get(node.primitive, 0) + 1
    for child in node.children:
        for primitive, count in _layout_counts(child).items():
            counts[primitive] = counts.get(primitive, 0) + count
    return dict(sorted(counts.items()))


def _region_depths(bundle: IntentBundle) -> dict[str, int]:
    parent_by_region = {region.id: region.parent_region or None for region in bundle.view_spec.regions}

    def depth(region_id: str) -> int:
        parent = parent_by_region.get(region_id)
        if not parent:
            return 0
        return 1 + depth(parent)

    return {region_id: depth(region_id) for region_id in parent_by_region}


def _provenance_coverage(manifest: dict[str, Any]) -> float:
    nodes = manifest.get("nodes", {})
    if not isinstance(nodes, dict) or not nodes:
        return 0.0
    covered = 0
    for entry in nodes.values():
        if isinstance(entry, dict) and entry.get("intent_refs"):
            covered += 1
    return covered / len(nodes)


def _reject_nondeterministic_fields(value: Any, fixture_id: str) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_nondeterministic_fields(item, fixture_id)
        return
    if isinstance(value, list):
        for item in value:
            _reject_nondeterministic_fields(item, fixture_id)
        return
    if not isinstance(value, str):
        return
    if (
        ABSOLUTE_PATH_RE.search(value)
        or ISO_TIMESTAMP_RE.search(value)
        or TEMP_NAME_RE.search(value)
        or UUID_RE.search(value)
    ):
        raise BenchmarkConstraintError(
            "NONDETERMINISTIC_BENCHMARK_FIELD",
            fixture_id,
            "Benchmark summary contains an absolute path, timestamp, temp name, or UUID-like value.",
        )


__all__ = [
    "BENCHMARK_ERROR_CODES",
    "BENCHMARK_SCHEMA_VERSION",
    "BENCHMARK_SUMMARY_MAX_BYTES",
    "BENCHMARK_TIMEOUT_SECONDS",
    "BenchmarkConstraintError",
    "BenchmarkFixture",
    "benchmark_fixtures",
    "run_benchmark_fixture",
    "run_benchmark_suite",
    "assert_benchmark_summary",
    "assert_benchmark_timeout",
    "assert_emitter_parity",
    "assert_expected_diagnostics",
    "assert_no_new_benchmark_dependencies",
    "stable_summary_json",
]
