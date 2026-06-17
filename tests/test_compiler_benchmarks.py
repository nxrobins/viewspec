import json

import pytest

import viewspec.compiler_benchmarks as compiler_benchmarks
from viewspec.compiler_benchmarks import (
    BENCHMARK_SCHEMA_VERSION,
    BENCHMARK_SUMMARY_MAX_BYTES,
    BenchmarkConstraintError,
    BenchmarkFixture,
    SemanticInventory,
    assert_benchmark_summary,
    assert_benchmark_timeout,
    assert_emitter_parity,
    assert_expected_diagnostics,
    assert_no_new_benchmark_dependencies,
    assert_tailwind_semantic_parity,
    benchmark_fixtures,
    run_benchmark_fixture,
    run_benchmark_suite,
    stable_summary_json,
)
from viewspec.sdk.builder import ViewSpecBuilder


def test_compiler_quality_benchmark_fixtures_emit_checked_artifacts(tmp_path):
    summaries = run_benchmark_suite(tmp_path)

    assert len(summaries) == 11
    assert {summary["fixture_id"] for summary in summaries} == {
        "aesthetic_profile_workspace",
        "collection_error_state",
        "collection_loading_state",
        "dashboard",
        "detail",
        "form",
        "hero",
        "list",
        "multi_region_product",
        "stateful_collection",
        "tailwind_admin_workspace",
    }
    for summary in summaries:
        assert summary["schema_version"] == BENCHMARK_SCHEMA_VERSION
        assert summary["baseline_role"] == "current_floor_not_quality_ceiling"
        assert len(summary["quality_categories"]) >= 4
        assert set(summary["metrics"]["diagnostics"]["actual_codes"]) <= set(summary["metrics"]["diagnostics"]["expected_codes"])
        assert summary["metrics"]["html"]["check_ok"] is True
        assert summary["metrics"]["react"]["check_ok"] is True
        assert summary["metrics"]["tailwind"]["check_ok"] is True
        assert summary["metrics"]["html"]["artifact_hash"]
        assert summary["metrics"]["react"]["artifact_hash"]
        assert summary["metrics"]["tailwind"]["artifact_hash"]
        assert len(stable_summary_json(summary).encode("utf-8")) <= BENCHMARK_SUMMARY_MAX_BYTES
        assert_benchmark_summary(summary)


def test_compiler_quality_benchmarks_are_deterministic(tmp_path):
    fixture = next(item for item in benchmark_fixtures() if item.id == "multi_region_product")

    first = run_benchmark_fixture(fixture, tmp_path / "first")
    second = run_benchmark_fixture(fixture, tmp_path / "second")

    assert stable_summary_json(first) == stable_summary_json(second)
    assert first["metrics"]["html"]["artifact_hash"] == second["metrics"]["html"]["artifact_hash"]
    assert first["metrics"]["react"]["artifact_hash"] == second["metrics"]["react"]["artifact_hash"]
    assert first["metrics"]["tailwind"]["artifact_hash"] == second["metrics"]["tailwind"]["artifact_hash"]
    assert first["metrics"]["html"]["manifest_hash"] == second["metrics"]["html"]["manifest_hash"]
    assert first["metrics"]["react"]["manifest_hash"] == second["metrics"]["react"]["manifest_hash"]
    assert first["metrics"]["tailwind"]["manifest_hash"] == second["metrics"]["tailwind"]["manifest_hash"]


def test_multi_region_fixture_has_required_layout_pressure(tmp_path):
    summary = run_benchmark_fixture(
        next(item for item in benchmark_fixtures() if item.id == "multi_region_product"),
        tmp_path,
    )

    assert summary["metrics"]["ast"]["region_count"] >= 3
    assert summary["metrics"]["ast"]["region_depth"] >= 2
    assert summary["metrics"]["ast"]["workspace_surface"] == "workspace_dashboard_v1"
    assert summary["metrics"]["ast"]["action_rows"] == ["planner_review_form_actions"]
    assert summary["metrics"]["ast"]["product_role_counts"] == {
        "action_row": 1,
        "app_header": 1,
        "app_shell": 1,
        "content_grid": 1,
        "detail_panel": 1,
        "field_group": 2,
        "form_panel": 1,
        "metric_card": 2,
        "metric_grid": 1,
        "page_header": 1,
        "primary_column": 1,
        "side_rail": 1,
    }
    assert summary["metrics"]["html"]["manifest_product_role_counts"] == summary["metrics"]["ast"]["product_role_counts"]
    assert summary["metrics"]["react"]["manifest_product_role_counts"] == summary["metrics"]["ast"]["product_role_counts"]
    assert summary["metrics"]["tailwind"]["manifest_product_role_counts"] == summary["metrics"]["ast"]["product_role_counts"]
    assert summary["metrics"]["tailwind"]["recipe_pack"] == "tailwind_app_v1"
    assert "vs-role-action-row" in summary["metrics"]["html"]["role_classes"]
    assert summary["metrics"]["html"]["role_classes"] == summary["metrics"]["react"]["role_classes"]
    assert summary["metrics"]["ast"]["planner_nodes"]["region_body"] == {
        "columns": 2,
        "layout_strategy": "region_grid_v0",
        "primitive": "grid",
    }
    assert summary["metrics"]["ast"]["planner_nodes"]["motif_workspace_metrics"] == {
        "columns": 2,
        "layout_strategy": "dashboard_grid_v0",
        "primitive": "grid",
    }
    assert summary["metrics"]["ast"]["planner_nodes"]["action_submit_review"] == {
        "placement": "motif_local",
        "primitive": "button",
    }
    assert summary["metrics"]["ast"]["planner_nodes"]["planner_review_form_actions"] == {
        "layout_strategy": "action_row_v1",
        "primitive": "cluster",
    }
    assert len(summary["metrics"]["ast"]["motif_kinds"]) >= 2
    assert summary["metrics"]["parity"]["action_ids"] == ["submit_review"]
    assert "vs-role-content-grid" in summary["metrics"]["parity"]["class_inventory"]


def test_dashboard_fixture_records_planner_grid_strategy(tmp_path):
    summary = run_benchmark_fixture(next(item for item in benchmark_fixtures() if item.id == "dashboard"), tmp_path)

    assert summary["metrics"]["ast"]["planner_nodes"]["motif_metrics"] == {
        "columns": 2,
        "layout_strategy": "dashboard_grid_v0",
        "primitive": "grid",
    }


def test_stateful_collection_fixture_records_collection_action_bar(tmp_path):
    summary = run_benchmark_fixture(next(item for item in benchmark_fixtures() if item.id == "stateful_collection"), tmp_path)

    assert summary["metrics"]["ast"]["motif_kinds"] == ["form", "table"]
    assert summary["metrics"]["ast"]["action_rows"] == ["planner_request_queue_collection_actions"]
    assert summary["metrics"]["ast"]["planner_nodes"]["planner_request_queue_collection_actions"] == {
        "layout_strategy": "collection_action_bar_v1",
        "primitive": "cluster",
    }
    assert {
        "bulk_assign_queue",
        "filter_queue",
        "page_queue",
        "search_queue",
        "sort_queue",
    } == set(summary["metrics"]["parity"]["action_ids"])
    assert summary["metrics"]["required_tags"] == ["input", "section", "table", "td", "th", "tr"]
    assert summary["metrics"]["tailwind"]["recipe_pack"] == "tailwind_app_v1"


def test_aesthetic_profile_fixture_records_manifest_profile_floor(tmp_path):
    fixture = next(item for item in benchmark_fixtures() if item.id == "aesthetic_profile_workspace")
    summary = run_benchmark_fixture(fixture, tmp_path)

    assert "aesthetic_profiles" in summary["quality_categories"]
    aesthetic = summary["metrics"]["aesthetic"]
    assert aesthetic["available"] is True
    assert aesthetic["profile"] == "aesthetic.premium_saas"
    assert aesthetic["profile_consistent"] is True
    assert aesthetic["style_summary_consistent"] is True
    assert aesthetic["layout_summary_consistent"] is True
    assert aesthetic["profiles_by_target"] == {
        "html-tailwind": "aesthetic.premium_saas",
        "react-tsx": "aesthetic.premium_saas",
        "react-tailwind-tsx": "aesthetic.premium_saas",
    }
    for target in ("html-tailwind", "react-tsx", "react-tailwind-tsx"):
        assert aesthetic["style"][target] == {
            "profile": "aesthetic.premium_saas",
            "changed_token_count": 13,
            "category_count": 8,
            "declaration_count": 29,
        }
        assert aesthetic["layout"][target]["content_grid"] == {
            "profile": "aesthetic.premium_saas",
            "columns": 2,
            "node_count": 1,
        }
        assert aesthetic["layout"][target]["metric_grid"] == {
            "profile": "aesthetic.premium_saas",
            "columns": 2,
            "node_count": 1,
        }
        assert aesthetic["layout"][target]["metric_card"] == {
            "profile": "aesthetic.premium_saas",
            "span_columns": 2,
            "node_count": 1,
        }


def test_benchmark_contract_is_not_weakened():
    assert len(benchmark_fixtures()) == 11
    assert compiler_benchmarks.BENCHMARK_SUMMARY_MAX_BYTES == 16 * 1024
    assert len(compiler_benchmarks.QUALITY_CATEGORIES) == 9
    assert {
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
    }.issubset(compiler_benchmarks.BENCHMARK_ERROR_CODES)


def test_benchmark_unexpected_diagnostics_fail_fast():
    with pytest.raises(BenchmarkConstraintError) as exc_info:
        assert_expected_diagnostics("fixture", actual_codes=("STYLE_TARGET_UNKNOWN",), expected_codes=())

    assert exc_info.value.code == "UNEXPECTED_DIAGNOSTIC"
    assert exc_info.value.fixture_id == "fixture"


def test_benchmark_emitter_parity_fail_fast():
    html = SemanticInventory(visible_text=frozenset({"Alpha"}))
    react = SemanticInventory(visible_text=frozenset({"Beta"}))

    with pytest.raises(BenchmarkConstraintError) as exc_info:
        assert_emitter_parity("fixture", html, react)

    assert exc_info.value.code == "EMITTER_PARITY_FAILED"
    assert exc_info.value.fixture_id == "fixture"

    html_classes = SemanticInventory(class_inventory=frozenset({"vs-role-app-shell"}))
    react_classes = SemanticInventory(class_inventory=frozenset({"vs-role-content-grid"}))
    with pytest.raises(BenchmarkConstraintError) as class_exc:
        assert_emitter_parity("fixture", html_classes, react_classes)
    assert class_exc.value.code == "EMITTER_ROLE_CLASS_DRIFT"

    with pytest.raises(BenchmarkConstraintError) as tailwind_exc:
        assert_tailwind_semantic_parity(
            "fixture",
            SemanticInventory(visible_text=frozenset({"Alpha"})),
            SemanticInventory(visible_text=frozenset({"Beta"})),
        )
    assert tailwind_exc.value.code == "TAILWIND_SEMANTIC_DRIFT"


def test_benchmark_rejects_shallow_and_non_derived_summary(tmp_path):
    summary = run_benchmark_fixture(next(item for item in benchmark_fixtures() if item.id == "dashboard"), tmp_path)

    shallow = {**summary, "quality_categories": ["semantics"]}
    with pytest.raises(BenchmarkConstraintError) as shallow_exc:
        assert_benchmark_summary(shallow)
    assert shallow_exc.value.code == "BENCHMARK_ORACLE_TOO_SHALLOW"

    non_derived = {**summary, "metric_sources": {**summary["metric_sources"], "vanity": "fixture"}}
    with pytest.raises(BenchmarkConstraintError) as derived_exc:
        assert_benchmark_summary(non_derived)
    assert derived_exc.value.code == "BENCHMARK_METRIC_NOT_DERIVED"

    planner_summary = run_benchmark_fixture(
        next(item for item in benchmark_fixtures() if item.id == "multi_region_product"),
        tmp_path / "planner",
    )
    weak_planner_sources = {
        **planner_summary,
        "metric_sources": {**planner_summary["metric_sources"], "workspace_surface": "manifest"},
    }
    with pytest.raises(BenchmarkConstraintError) as planner_exc:
        assert_benchmark_summary(weak_planner_sources)
    assert planner_exc.value.code == "PLANNER_METRIC_NOT_DERIVED"


def test_benchmark_rejects_nondeterministic_and_oversized_summary(tmp_path):
    summary = run_benchmark_fixture(next(item for item in benchmark_fixtures() if item.id == "list"), tmp_path)

    absolute_path = {**summary, "machine_path": r"D:\viewspec\.tmp\artifact.html"}
    with pytest.raises(BenchmarkConstraintError) as path_exc:
        assert_benchmark_summary(absolute_path)
    assert path_exc.value.code == "NONDETERMINISTIC_BENCHMARK_FIELD"

    timestamp = {**summary, "generated_at": "2026-06-01T12:00:00"}
    with pytest.raises(BenchmarkConstraintError) as timestamp_exc:
        assert_benchmark_summary(timestamp)
    assert timestamp_exc.value.code == "NONDETERMINISTIC_BENCHMARK_FIELD"

    uuid_like = {**summary, "random_id": "123e4567-e89b-12d3-a456-426614174000"}
    with pytest.raises(BenchmarkConstraintError) as uuid_exc:
        assert_benchmark_summary(uuid_like)
    assert uuid_exc.value.code == "NONDETERMINISTIC_BENCHMARK_FIELD"

    oversized = {**summary, "padding": "x" * BENCHMARK_SUMMARY_MAX_BYTES}
    with pytest.raises(BenchmarkConstraintError) as size_exc:
        assert_benchmark_summary(oversized)
    assert size_exc.value.code == "BENCHMARK_SUMMARY_TOO_LARGE"


def test_benchmark_rejects_full_artifact_goldens_new_deps_and_too_small_fixtures(tmp_path):
    fixture = next(item for item in benchmark_fixtures() if item.id == "hero")
    with pytest.raises(BenchmarkConstraintError) as golden_exc:
        run_benchmark_fixture(BenchmarkFixture("golden", fixture.bundle, full_artifact_golden="<html></html>"), tmp_path)
    assert golden_exc.value.code == "FULL_ARTIFACT_GOLDEN_FORBIDDEN"

    with pytest.raises(BenchmarkConstraintError) as dep_exc:
        assert_no_new_benchmark_dependencies(("playwright",), fixture_id="suite")
    assert dep_exc.value.code == "BENCHMARK_NEW_DEPENDENCY_FORBIDDEN"

    tiny_builder = ViewSpecBuilder("tiny")
    tiny_builder.add_style("tone", "view:tiny", "tone.muted")
    with pytest.raises(BenchmarkConstraintError) as tiny_exc:
        run_benchmark_fixture(BenchmarkFixture("tiny", tiny_builder.build_bundle()), tmp_path)
    assert tiny_exc.value.code == "BENCHMARK_FIXTURE_TOO_SMALL"


def test_benchmark_rejects_multi_region_without_layout_pressure(tmp_path):
    builder = ViewSpecBuilder("weak_multi")
    dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
    dashboard.add_card(label="One", value="1", id="one")
    dashboard.add_card(label="Two", value="2", id="two")

    weak = BenchmarkFixture("weak_multi", builder.build_bundle(), multi_region=True)
    with pytest.raises(BenchmarkConstraintError) as exc_info:
        run_benchmark_fixture(weak, tmp_path)

    assert exc_info.value.code == "BENCHMARK_LAYOUT_PRESSURE_MISSING"


def test_benchmark_timeout_and_error_shape_are_stable():
    with pytest.raises(BenchmarkConstraintError) as timeout_exc:
        assert_benchmark_timeout(0.0, -1.0, fixture_id="fixture")
    assert timeout_exc.value.code == "BENCHMARK_TIMEOUT_EXCEEDED"

    invalid = BenchmarkConstraintError("NOT_A_CODE", "", "bad")
    assert invalid.code == "BENCHMARK_ERROR_SHAPE_INVALID"
    assert invalid.fixture_id == "<unknown>"
    assert set(invalid.to_json()) == {"code", "fixture_id", "message"}


def test_benchmark_summary_json_is_compact_and_stable(tmp_path):
    summary = run_benchmark_fixture(next(item for item in benchmark_fixtures() if item.id == "form"), tmp_path)

    encoded = stable_summary_json(summary)
    decoded = json.loads(encoded)

    assert decoded == summary
    assert "\n" not in encoded
    assert ": " not in encoded
    assert ", " not in encoded
