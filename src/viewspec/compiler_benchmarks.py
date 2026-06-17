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
from viewspec.compiler import PRODUCT_SURFACE_PLANNER_V1_SURFACE, compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
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
        "PLANNER_FIXTURE_ID_BRANCH",
        "PLANNER_ROLE_METADATA_ONLY",
        "UNSAFE_ROLE_CLASS",
        "EMITTER_ROLE_CLASS_DRIFT",
        "ACTION_ROW_CONTRACT_DRIFT",
        "DUPLICATE_ACTION_ROW",
        "BENCHMARK_CONTRACT_WEAKENED",
        "PLANNER_METRIC_NOT_DERIVED",
        "PLANNER_STYLE_AUTOFETCH",
        "PLANNER_SYNTHETIC_CONTENT",
        "PLANNER_PASS_BOUNDARY_BROKEN",
        "TAILWIND_ACTIVE_SURFACE_FORBIDDEN",
        "TAILWIND_DYNAMIC_CLASS",
        "TAILWIND_GENERIC_FALLBACK_EXCEEDED",
        "TAILWIND_HOST_CONFIG_DEPENDENCY",
        "TAILWIND_INLINE_STYLE_FORBIDDEN",
        "TAILWIND_INVENTORY_MISMATCH",
        "TAILWIND_RECIPE_REGISTRY_DIGEST_MISMATCH",
        "TAILWIND_RECIPE_UNREACHABLE",
        "TAILWIND_SEMANTIC_DRIFT",
        "TAILWIND_TARGET_REGRESSION",
        "TAILWIND_TSX_INVALID",
        "TAILWIND_UNSAFE_CLASS_SOURCE",
        "COLLECTION_ACTION_TARGET_INVALID",
        "COLLECTION_ACTION_PAYLOAD_REQUIRED",
        "COLLECTION_ACTION_PAYLOAD_TOO_LARGE",
        "COLLECTION_BULK_SELECTION_REQUIRED",
        "COLLECTION_BULK_SELECTION_AMBIGUOUS",
        "COLLECTION_BULK_SELECTION_TOO_LARGE",
        "COLLECTION_ACTION_BAR_DUPLICATE",
        "COLLECTION_ACTION_BAR_PLACEMENT_INVALID",
        "COLLECTION_STATE_CONFLICT",
        "STATE_MOTIF_TITLE_REQUIRED",
        "STATE_MOTIF_TOO_MANY_DESCRIPTIONS",
        "TOO_MANY_STATE_MOTIFS",
        "TOO_MANY_COLLECTION_ACTIONS",
        "STATEFUL_COLLECTIONS_PUBLIC_CONTRACT_DRIFT",
        "TAILWIND_STATEFUL_COLLECTION_RECIPE_MISSING",
        "STATEFUL_COLLECTIONS_EMITTER_PARITY_FAILED",
        "STATEFUL_COLLECTIONS_ACTION_PAYLOAD_MISMATCH",
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
        "tailwind_compatibility",
        "aesthetic_profiles",
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
ROLE_CLASS_RE = re.compile(r"\bvs-role-[a-z0-9-]+\b")
REQUIRED_TAGS_BY_MOTIF = {
    "dashboard": set(),
    "detail": {"dd", "dl", "dt"},
    "form": {"input", "section"},
    "hero": {"h1", "header"},
    "list": {"li", "ul"},
    "loading_state": {"h2", "section"},
    "error_state": {"h2", "section"},
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
    manifest_summary: dict[str, Any]
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
    class_inventory: frozenset[str] = field(default_factory=frozenset)

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
            "class_inventory": sorted(self.class_inventory),
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
        BenchmarkFixture("stateful_collection", _stateful_collection_fixture()),
        BenchmarkFixture("collection_loading_state", _collection_loading_state_fixture()),
        BenchmarkFixture("collection_error_state", _collection_error_state_fixture()),
        BenchmarkFixture("multi_region_product", _multi_region_fixture(), multi_region=True),
        BenchmarkFixture("aesthetic_profile_workspace", _aesthetic_profile_workspace_fixture(), multi_region=True),
        BenchmarkFixture("tailwind_admin_workspace", _tailwind_admin_workspace_fixture(), multi_region=True),
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
    completed_fixture_ids: list[str] = []
    root = Path(output_root)
    for fixture in benchmark_fixtures():
        assert_benchmark_timeout(
            started_at,
            timeout_seconds,
            fixture_id=fixture.id,
            phase="before_fixture",
            completed_fixture_ids=tuple(completed_fixture_ids),
        )
        summaries.append(run_benchmark_fixture(fixture, root / fixture.id))
        completed_fixture_ids.append(fixture.id)
        assert_benchmark_timeout(
            started_at,
            timeout_seconds,
            fixture_id=fixture.id,
            phase="after_fixture",
            completed_fixture_ids=tuple(completed_fixture_ids),
        )
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
    tailwind_artifact = _emit_checked_artifact(fixture, ast, output / "react-tailwind", target="react-tailwind-tsx")
    summary = _benchmark_summary(fixture, ast, html_artifact, react_artifact, tailwind_artifact)
    assert_benchmark_summary(summary)
    return summary


def assert_no_new_benchmark_dependencies(dependency_names: tuple[str, ...] | list[str], *, fixture_id: str = "suite") -> None:
    if dependency_names:
        raise BenchmarkConstraintError(
            "BENCHMARK_NEW_DEPENDENCY_FORBIDDEN",
            fixture_id,
            f"Benchmark dependencies are forbidden in this PR: {', '.join(sorted(dependency_names))}.",
        )


def assert_benchmark_timeout(
    started_at: float,
    timeout_seconds: float,
    *,
    fixture_id: str,
    phase: str = "before_fixture",
    completed_fixture_ids: tuple[str, ...] = (),
) -> None:
    elapsed_seconds = time.perf_counter() - started_at
    if elapsed_seconds > timeout_seconds:
        completed = ", ".join(completed_fixture_ids) if completed_fixture_ids else "none"
        raise BenchmarkConstraintError(
            "BENCHMARK_TIMEOUT_EXCEEDED",
            fixture_id,
            (
                f"Benchmark suite exceeded {timeout_seconds:g}s "
                f"({elapsed_seconds:.3f}s elapsed, phase={phase}, "
                f"completed_count={len(completed_fixture_ids)}, completed={completed})."
            ),
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
        "class_inventory",
    )
    drift = [key for key in compared_keys if html_json[key] != react_json[key]]
    if drift:
        code = "EMITTER_ROLE_CLASS_DRIFT" if drift == ["class_inventory"] else "EMITTER_PARITY_FAILED"
        raise BenchmarkConstraintError(
            code,
            fixture_id,
            f"HTML and React semantic inventory drifted for: {', '.join(drift)}.",
        )


def assert_tailwind_semantic_parity(
    fixture_id: str,
    baseline_inventory: SemanticInventory,
    tailwind_inventory: SemanticInventory,
) -> None:
    baseline_json = baseline_inventory.to_json()
    tailwind_json = tailwind_inventory.to_json()
    compared_keys = (
        "visible_text",
        "binding_ids",
        "action_ids",
        "motif_ids",
        "manifest_node_ids",
        "diagnostic_codes",
        "provenance_refs",
        "style_tokens",
    )
    drift = [key for key in compared_keys if baseline_json[key] != tailwind_json[key]]
    if drift:
        raise BenchmarkConstraintError(
            "TAILWIND_SEMANTIC_DRIFT",
            fixture_id,
            f"Tailwind semantic inventory drifted for: {', '.join(drift)}.",
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
    _assert_planner_metrics_are_derived(summary, sources)

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


def _assert_planner_metrics_are_derived(summary: dict[str, Any], sources: dict[str, Any]) -> None:
    fixture_id = str(summary.get("fixture_id") or "")
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        return
    ast_metrics = metrics.get("ast")
    if not isinstance(ast_metrics, dict) or not ast_metrics.get("workspace_surface"):
        return
    if ast_metrics.get("workspace_surface") != PRODUCT_SURFACE_PLANNER_V1_SURFACE:
        raise BenchmarkConstraintError(
            "PLANNER_METRIC_NOT_DERIVED",
            fixture_id,
            "Planner workspace surface metric is not the compiler-owned V1 marker.",
        )
    required_sources = {
        "workspace_surface": "ast",
        "product_role_counts": "ast",
        "action_rows": "ast",
        "html_manifest_product_role_counts": "manifest",
        "react_manifest_product_role_counts": "manifest",
        "html_role_classes": "artifact_text",
        "react_role_classes": "artifact_text",
        "tailwind_manifest_product_role_counts": "manifest",
        "tailwind_recipe_inventory": "manifest",
    }
    missing = [
        metric_name
        for metric_name, source in required_sources.items()
        if sources.get(metric_name) != source
    ]
    if missing:
        raise BenchmarkConstraintError(
            "PLANNER_METRIC_NOT_DERIVED",
            fixture_id,
            f"Planner metric source(s) missing or weak: {', '.join(sorted(missing))}.",
        )


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


def _stateful_collection_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_stateful_collection")
    control_members: list[str] = []
    for node_id, label, attr, value, binding_id in (
        ("queue_query_control", "Search", "value", "", "queue_query"),
        ("queue_status_control", "Status", "value", "open", "queue_status"),
        ("queue_sort_control", "Sort", "value", "created_desc", "queue_sort"),
        ("queue_page_control", "Page", "value", "next", "queue_page"),
        ("queue_selection_control", "Selected rows", "selected_ids", "request_a,request_b", "queue_selected_ids"),
    ):
        builder.add_node(node_id, "collection_control", attrs={"label": label, attr: value})
        control_members.append(builder.bind_attr(f"{node_id}_label", node_id, "label", region="main", present_as="label"))
        control_members.append(builder.bind_attr(binding_id, node_id, attr, region="main", present_as="input"))
    builder.add_group("queue_control_fields", "ordered", control_members, target_region="main")
    builder.add_motif("queue_controls", "form", "main", control_members)

    table = builder.add_table("request_queue", region="main", group_id="request_rows")
    table.add_row(label="Request A", value="Ready", id="request_a")
    table.add_row(label="Request B", value="Waiting", id="request_b")
    table.add_row(label="Request C", value="Blocked", id="request_c")
    builder.add_collection_action(
        "search_queue",
        "search",
        "Search",
        collection_id="request_queue",
        payload_bindings=["queue_query"],
    )
    builder.add_collection_action(
        "filter_queue",
        "filter",
        "Filter",
        collection_id="request_queue",
        payload_bindings=["queue_status"],
    )
    builder.add_collection_action(
        "sort_queue",
        "sort",
        "Sort",
        collection_id="request_queue",
        payload_bindings=["queue_sort"],
    )
    builder.add_collection_action(
        "page_queue",
        "paginate",
        "Next page",
        collection_id="request_queue",
        payload_bindings=["queue_page"],
    )
    builder.add_collection_action(
        "bulk_assign_queue",
        "bulk_action",
        "Assign selected",
        collection_id="request_queue",
        payload_bindings=["queue_selected_ids"],
    )
    builder.add_style("queue_table_surface", "motif:request_queue", "surface.strong")
    return builder.build_bundle()


def _collection_loading_state_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_collection_loading")
    builder.add_loading_state(
        "queue_loading",
        title="Loading requests",
        description="Fetching the current collection.",
        region="main",
        group_id="message",
    )
    builder.add_style("loading_surface", "motif:queue_loading", "surface.subtle")
    return builder.build_bundle()


def _collection_error_state_fixture() -> IntentBundle:
    builder = ViewSpecBuilder("benchmark_collection_error")
    builder.add_error_state(
        "queue_error",
        title="Unable to load requests",
        description="Retry after the source data is available.",
        region="main",
        group_id="message",
    )
    builder.add_style("error_surface", "motif:queue_error", "tone.warning")
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


def _tailwind_admin_workspace_fixture() -> IntentBundle:
    builder = ViewSpecBuilder(
        "benchmark_tailwind_admin",
        root_attrs={"title": "Tailwind Admin Workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.add_region("header", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("body", parent_region="root", role="workspace", layout="grid", min_children=2)
    builder.add_region("main", parent_region="body", role="primary", layout="stack", min_children=3)
    builder.add_region("side", parent_region="body", role="sidebar", layout="stack", min_children=2)

    builder.add_hero(
        "admin_intro",
        eyebrow="Operations",
        title="Review requests",
        description="Inspect queue state, filter open work, and submit reviewer decisions.",
        region="header",
        group_id="admin_intro_message",
    )
    nav = builder.add_list("admin_nav", region="side", group_id="admin_nav_items")
    nav.add_item(label="Inbox", description="Open requests", id="inbox")
    nav.add_item(label="Escalations", description="Needs review", id="escalations")
    metrics = builder.add_dashboard("admin_metrics", region="main", group_id="admin_metric_cards")
    metrics.add_card(label="Open", value="18", id="open")
    metrics.add_card(label="Blocked", value="3", id="blocked", value_present_as="badge")
    table = builder.add_table("request_queue", region="main", group_id="request_rows")
    table.add_row(label="Request A", value="Ready", id="request_a")
    table.add_row(label="Request B", value="Waiting", id="request_b")
    filters = builder.add_form("queue_filters", region="main", group_id="filter_fields")
    filters.add_field(label="Search", value="", id="search")
    filters.add_field(label="Owner", value="Team", id="owner")
    detail = builder.add_detail("selected_request", region="side", group_id="selected_request_fields")
    detail.add_field(label="Reviewer", value="Ada", id="reviewer")
    detail.add_field(label="Priority", value="High", id="priority")
    builder.add_empty_state(
        "empty_queue",
        title="No matching requests",
        description="Adjust filters to widen the queue.",
        region="main",
        group_id="empty_queue_message",
    )
    builder.add_action(
        "apply_filters",
        "submit",
        "Apply filters",
        target_region="main",
        target_ref="motif:queue_filters",
        payload_bindings=["search_value", "owner_value"],
    )
    builder.add_style("admin_body_density", "region:body", "density.regular")
    builder.add_style("admin_detail_surface", "motif:selected_request", "surface.strong")
    return builder.build_bundle()


def _aesthetic_profile_workspace_fixture() -> IntentBundle:
    builder = ViewSpecBuilder(
        "benchmark_aesthetic_profile",
        root_attrs={"title": "Aesthetic Profile Benchmark"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile("aesthetic.premium_saas")
    builder.add_region("hero", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("workspace", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("primary", parent_region="workspace", role="primary", layout="stack", min_children=2)
    builder.add_region("review", parent_region="workspace", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "profile_intro",
        eyebrow="Aesthetic profile",
        title="Governed style projection",
        description="Benchmark profile style and bounded layout metadata across emitters.",
        region="hero",
        group_id="profile_intro_copy",
    )
    metrics = builder.add_dashboard("profile_metrics", region="primary", group_id="profile_metric_cards")
    metrics.add_card(label="Confidence", value="92%", id="confidence")
    metrics.add_card(label="Risk", value="Low", id="risk", value_present_as="badge")
    metrics.add_card(label="Evidence", value="Checked", id="evidence")
    detail = builder.add_detail("profile_review", region="review", group_id="profile_review_fields")
    detail.add_field(label="Manifest", value="summary aligned", id="manifest")
    detail.add_field(label="Layout", value="bounded grid metadata", id="layout")
    decision = builder.add_form("profile_decision", region="primary", group_id="profile_decision_fields")
    decision.add_field(label="Reviewer", value="", id="reviewer")
    decision.add_field(label="Decision", value="approve", id="decision")
    builder.add_action(
        "approve_profile",
        "submit",
        "Approve profile",
        target_region="primary",
        target_ref="motif:profile_decision",
        payload_bindings=["reviewer_value", "decision_value"],
    )
    builder.add_style("profile_review_surface", "motif:profile_review", "surface.strong")
    builder.add_style("profile_risk_tone", "binding:risk_value", "tone.accent")
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
    elif target == "react-tailwind-tsx":
        paths = ReactTailwindTsxEmitter().emit(ast, output_dir)
        artifact_path = Path(paths["tsx"])
        emitter = "react_tailwind_tsx"
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
        manifest_summary=checked.get("manifest_summary", {}),
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
    tailwind_artifact: EmitterBenchmarkArtifact,
) -> dict[str, Any]:
    html_inventory = _manifest_inventory(html_artifact.manifest)
    react_inventory = _manifest_inventory(react_artifact.manifest)
    tailwind_inventory = _manifest_inventory(tailwind_artifact.manifest)
    assert_emitter_parity(fixture.id, html_inventory, react_inventory)
    assert_tailwind_semantic_parity(fixture.id, html_inventory, tailwind_inventory)

    html_tags, html_text = _html_tags_and_text(html_artifact.artifact_text)
    react_tags = _react_tags(react_artifact.artifact_text)
    tailwind_tags = _react_tags(tailwind_artifact.artifact_text)
    required_tags = _required_tags(fixture.bundle)
    semantic_tags_ok = required_tags.issubset(html_tags) and required_tags.issubset(react_tags) and required_tags.issubset(
        tailwind_tags
    )
    check_ok = html_artifact.check_ok and react_artifact.check_ok and tailwind_artifact.check_ok
    no_network_surfaces = not NETWORK_SURFACE_RE.search(html_artifact.artifact_text) and not NETWORK_SURFACE_RE.search(
        react_artifact.artifact_text
    ) and not NETWORK_SURFACE_RE.search(
        tailwind_artifact.artifact_text
    )
    style_tokens = sorted(html_inventory.style_tokens | react_inventory.style_tokens | tailwind_inventory.style_tokens)
    style_values = set(ast.style_values)
    aesthetic_metrics = _aesthetic_profile_metrics(html_artifact, react_artifact, tailwind_artifact)
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
            "tailwind_compatibility": tailwind_artifact.check_ok
            and tailwind_artifact.manifest.get("tailwind_recipe_inventory", {}).get("recipe_pack") == "tailwind_app_v1",
            "aesthetic_profiles": aesthetic_metrics["available"]
            and aesthetic_metrics["profile_consistent"]
            and aesthetic_metrics["style_summary_consistent"]
            and aesthetic_metrics["layout_summary_consistent"],
        }.items()
        if ok
    )
    if html_artifact.check_errors or react_artifact.check_errors or tailwind_artifact.check_errors:
        quality_categories = [category for category in quality_categories if category != "safety"]

    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "fixture_id": fixture.id,
        "baseline_role": "current_floor_not_quality_ceiling",
        "quality_categories": quality_categories,
        "source_hash": _bundle_hash(fixture.bundle),
        "metric_sources": {
            "action_rows": "ast",
            "artifact_hashes": "artifact_hash",
            "artifact_text": "artifact_text",
            "ast_shape": "ast",
            "checks": "check",
            "diagnostics": "diagnostics",
            "html_manifest_product_role_counts": "manifest",
            "html_role_classes": "artifact_text",
            "manifests": "manifest",
            "product_role_counts": "ast",
            "react_manifest_product_role_counts": "manifest",
            "react_role_classes": "artifact_text",
            "tailwind_manifest_product_role_counts": "manifest",
            "tailwind_recipe_inventory": "manifest",
            "workspace_surface": "ast",
            "aesthetic_profile": "manifest",
            "aesthetic_style": "manifest",
            "aesthetic_layout": "manifest",
        },
        "metrics": {
            "ast": {
                "action_rows": _action_row_ids(ast.result.root.root),
                "ir_depth": _ir_depth(ast.result.root.root),
                "layout_counts": _layout_counts(ast.result.root.root),
                "motif_kinds": sorted({motif.kind for motif in fixture.bundle.view_spec.motifs}),
                "planner_nodes": _planner_nodes(ast.result.root.root),
                "product_role_counts": _product_role_counts_from_ast(ast.result.root.root),
                "region_depth": max(_region_depths(fixture.bundle).values(), default=0),
                "region_count": len(fixture.bundle.view_spec.regions),
                "workspace_surface": _workspace_surface(ast.result.root.root),
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
                "manifest_product_role_counts": _product_role_counts_from_manifest(html_artifact.manifest),
                "role_classes": _role_classes_from_artifact(html_artifact.artifact_text),
                "visible_text_count": len(html_text),
            },
            "react": {
                "artifact_hash": react_artifact.artifact_hash,
                "check_ok": react_artifact.check_ok,
                "manifest_hash": react_artifact.manifest_hash,
                "manifest_product_role_counts": _product_role_counts_from_manifest(react_artifact.manifest),
                "role_classes": _role_classes_from_artifact(react_artifact.artifact_text),
                "tsx_tags": sorted(react_tags),
            },
            "tailwind": {
                "artifact_hash": tailwind_artifact.artifact_hash,
                "check_ok": tailwind_artifact.check_ok,
                "manifest_hash": tailwind_artifact.manifest_hash,
                "manifest_product_role_counts": _product_role_counts_from_manifest(tailwind_artifact.manifest),
                "recipe_count": tailwind_artifact.manifest.get("tailwind_recipe_inventory", {}).get("recipe_count"),
                "recipe_pack": tailwind_artifact.manifest.get("tailwind_recipe_inventory", {}).get("recipe_pack"),
                "tsx_tags": sorted(tailwind_tags),
            },
            "aesthetic": aesthetic_metrics,
            "parity": html_inventory.to_json(),
            "tailwind_parity": tailwind_inventory.to_json(),
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
    class_inventory: set[str] = set()
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
            for class_name in entry.get("classes", []):
                if isinstance(class_name, str):
                    class_inventory.add(class_name)
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
        class_inventory=frozenset(class_inventory),
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


def _planner_nodes(node: IRNode) -> dict[str, dict[str, object]]:
    matches: dict[str, dict[str, object]] = {}
    layout_strategy = node.props.get("layout_strategy")
    placement = node.props.get("placement")
    if layout_strategy or placement:
        entry: dict[str, object] = {
            "primitive": node.primitive,
        }
        if layout_strategy:
            entry["layout_strategy"] = str(layout_strategy)
        if placement:
            entry["placement"] = str(placement)
        if "columns" in node.props:
            entry["columns"] = int(node.props["columns"])
        matches[node.id] = entry
    for child in node.children:
        matches.update(_planner_nodes(child))
    return dict(sorted(matches.items()))


def _workspace_surface(node: IRNode) -> str:
    surface = node.props.get("planner_surface")
    return surface if isinstance(surface, str) else ""


def _product_role_counts_from_ast(node: IRNode) -> dict[str, int]:
    counts: dict[str, int] = {}
    role = node.props.get("product_role")
    if isinstance(role, str) and role:
        counts[role] = counts.get(role, 0) + 1
    for child in node.children:
        for child_role, count in _product_role_counts_from_ast(child).items():
            counts[child_role] = counts.get(child_role, 0) + count
    return dict(sorted(counts.items()))


def _product_role_counts_from_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    nodes = manifest.get("nodes", {})
    if not isinstance(nodes, dict):
        return counts
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props", {})
        if not isinstance(props, dict):
            continue
        role = props.get("product_role")
        if isinstance(role, str) and role:
            counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))


def _aesthetic_profile_metrics(*artifacts: EmitterBenchmarkArtifact) -> dict[str, Any]:
    profiles = {artifact.target: artifact.manifest_summary.get("aesthetic_profile") for artifact in artifacts}
    concrete_profiles = {profile for profile in profiles.values() if isinstance(profile, str) and profile}
    profile = next(iter(sorted(concrete_profiles)), None)
    style_summaries = {
        artifact.target: _compact_aesthetic_style(artifact.manifest_summary.get("aesthetic_style"))
        for artifact in artifacts
    }
    layout_summaries = {
        artifact.target: _compact_aesthetic_layout(artifact.manifest_summary.get("aesthetic_layout"))
        for artifact in artifacts
    }
    concrete_styles = [style for style in style_summaries.values() if style]
    concrete_layouts = [layout for layout in layout_summaries.values() if layout]
    return {
        "available": bool(concrete_profiles),
        "profile": profile,
        "profiles_by_target": profiles,
        "profile_consistent": bool(profile) and all(value == profile for value in profiles.values()),
        "style_summary_consistent": len(concrete_styles) == len(artifacts)
        and len({stable_summary_json(style) for style in concrete_styles}) == 1,
        "layout_summary_consistent": len(concrete_layouts) == len(artifacts)
        and len({stable_summary_json(layout) for layout in concrete_layouts}) == 1,
        "style": style_summaries,
        "layout": layout_summaries,
    }


def _compact_aesthetic_style(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("available"):
        return {}
    return {
        "profile": value.get("profile"),
        "changed_token_count": value.get("changed_token_count"),
        "category_count": value.get("category_count"),
        "declaration_count": value.get("declaration_count"),
    }


def _compact_aesthetic_layout(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(role): {
            key: item.get(key)
            for key in ("profile", "columns", "span_columns", "node_count")
            if key in item
        }
        for role, item in sorted(value.items())
        if isinstance(item, dict)
    }


def _role_classes_from_artifact(artifact_text: str) -> list[str]:
    return sorted(set(ROLE_CLASS_RE.findall(artifact_text)))


def _action_row_ids(node: IRNode) -> list[str]:
    ids = [node.id] if node.props.get("product_role") == "action_row" else []
    for child in node.children:
        ids.extend(_action_row_ids(child))
    return sorted(ids)


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
    "assert_tailwind_semantic_parity",
    "stable_summary_json",
]
