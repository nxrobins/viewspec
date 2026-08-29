from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from viewspec.app_bundle import compile_app, starter_react_app_bundle, validate_app_text
from viewspec.app_react_verify import verify_react_app_artifact_dir
from viewspec.presentation_plan import (
    build_presentation_plan,
    presentation_plan_css,
    presentation_plan_hash,
)


ROOT = Path(__file__).resolve().parents[1]
REACT_APP_TARGET = "react-tailwind-app"
PRESENTATION_E2E_OPT_IN = "VIEWSPEC_RUN_PRESENTATION_E2E"


def _write_app(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _app_with_declared_presentation() -> dict:
    app = starter_react_app_bundle("internal_tool")
    app["screens"][0]["presentation"] = {
        "profile": "operations_workspace",
        "rules": [
            {
                "id": "queue_shell",
                "target_ref": "region:root",
                "base": {
                    "display": "grid",
                    "columns": 1,
                    "areas": [["main"]],
                    "gap": "none",
                    "padding": "none",
                },
                "variants": {
                    "compact": {"columns": 1, "areas": [["main"]]},
                    "medium": {"columns": 1, "areas": [["main"]]},
                    "wide": {"columns": 1, "areas": [["main"]]},
                },
            },
            {
                "id": "queue_content",
                "target_ref": "region:main",
                "base": {
                    "display": "flex",
                    "direction": "column",
                    "gap": "md",
                    "max_width": "content_lg",
                    "min_inline_size": "zero",
                    "padding": "lg",
                },
                "variants": {
                    "compact": {"padding": "sm"},
                    "medium": {"padding": "md"},
                    "wide": {"padding": "xl"},
                },
            },
            {
                "id": "queue_collection",
                "target_ref": "motif:incidents",
                "base": {"display": "block", "min_inline_size": "zero"},
                "variants": {},
                "items": {
                    "base": {"display": "grid", "columns": 2, "gap": "sm"},
                    "variants": {
                        "compact": {"columns": 1},
                        "medium": {"columns": 2},
                        "wide": {"columns": 2},
                    },
                },
            },
        ],
        "anchors": [
            {
                "id": "queue_inside_content",
                "target_ref": "motif:incidents",
                "relation": "inside",
                "anchor_ref": "region:main",
                "viewports": ["compact", "medium", "wide"],
            }
        ],
    }
    return app


def test_presentation_plan_is_deterministic_and_target_neutral(tmp_path):
    payload = _app_with_declared_presentation()
    app_path = tmp_path / "viewspec.app.json"
    static_dir = tmp_path / "static"
    react_dir = tmp_path / "react"
    _write_app(app_path, payload)

    static = compile_app(app_path, out_dir=static_dir, cwd=tmp_path)
    react = compile_app(app_path, out_dir=react_dir, target=REACT_APP_TARGET, cwd=tmp_path)

    assert static["ok"] is True, static.get("errors")
    assert react["ok"] is True, react.get("errors")
    assert static["presentation_plan_hash"] == react["presentation_plan_hash"]
    assert (static_dir / "presentation_plan.json").read_bytes() == (
        react_dir / "presentation_plan.json"
    ).read_bytes()

    plan = build_presentation_plan(payload)
    assert presentation_plan_hash(plan) == static["presentation_plan_hash"]
    assert plan["screens"][0]["source"] == "declared"
    assert plan["screens"][1]["source"] == "inferred"
    assert static["presentation_plan_diagnostics"] == react["presentation_plan_diagnostics"]
    assert "APP_PRESENTATION_INFERRED" in {
        item["code"] for item in static["presentation_plan_diagnostics"]
    }

    shared_css = presentation_plan_css(plan)
    assert shared_css in (static_dir / "index.html").read_text(encoding="utf-8")
    assert shared_css in (react_dir / "src" / "index.css").read_text(encoding="utf-8")
    assert '[data-ir-id="motif_incidents"] > [data-ir-id]' in shared_css
    assert (
        '[data-ir-id="motif_incidents"] > :is(tbody, thead, tfoot) > [data-ir-id]'
        in shared_css
    )
    assert (
        '[data-ir-id="motif_incidents"] > :is(tbody, thead, tfoot) '
        "{ display: grid; gap: 0.625rem; width: 100%; min-width: 0; }"
        in shared_css
    )

    static_manifest = json.loads((static_dir / "shell_manifest.json").read_text(encoding="utf-8"))
    react_manifest = json.loads((react_dir / "viewspec_app_manifest.json").read_text(encoding="utf-8"))
    assert static_manifest["presentation_plan"] == react_manifest["presentation_plan"]


def test_declared_presentation_validates_targets_areas_and_anchors():
    payload = _app_with_declared_presentation()
    assert validate_app_text(json.dumps(payload))["ok"] is True

    binding_id = payload["screens"][0]["intent_bundle"]["view_spec"]["bindings"][0]["id"]
    payload["screens"][0]["presentation"]["rules"].append(
        {
            "id": "first_binding_area",
            "target_ref": f"binding:{binding_id}",
            "base": {"area": "primary"},
        }
    )
    payload["screens"][0]["presentation"]["rules"][2]["items"]["base"].update(
        {"columns": 1, "areas": [["primary"]]}
    )
    assert validate_app_text(json.dumps(payload))["ok"] is True

    payload = _app_with_declared_presentation()
    payload["screens"][0]["presentation"]["rules"][0]["base"]["areas"] = [["missing"]]
    invalid_area = validate_app_text(json.dumps(payload))
    assert invalid_area["ok"] is False
    assert "APP_PRESENTATION_AREA_TARGET_MISSING" in {
        issue["code"] for issue in invalid_area["issues"]
    }

    payload = _app_with_declared_presentation()
    payload["screens"][0]["presentation"]["anchors"][0]["target_ref"] = "binding:missing"
    invalid_anchor = validate_app_text(json.dumps(payload))
    assert invalid_anchor["ok"] is False
    assert "APP_PRESENTATION_ANCHOR_TARGET_MISSING" in {
        issue["code"] for issue in invalid_anchor["issues"]
    }

    payload = _app_with_declared_presentation()
    binding_id = payload["screens"][0]["intent_bundle"]["view_spec"]["bindings"][0]["id"]
    payload["screens"][0]["presentation"]["anchors"][0].update(
        {
            "target_ref": f"binding:{binding_id}",
            "anchor_ref": "motif:incidents",
            "relation": "before",
        }
    )
    contradictory = validate_app_text(json.dumps(payload))
    assert contradictory["ok"] is False
    issue = next(
        item
        for item in contradictory["issues"]
        if item["code"] == "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID"
    )
    assert f"binding:{binding_id}" in issue["message"]
    assert "semantically contained by motif:incidents" in issue["message"]

    payload = _app_with_declared_presentation()
    payload["screens"][0]["presentation"]["rules"][0]["items"] = {
        "base": {"display": "grid", "columns": 2}
    }
    invalid_items = validate_app_text(json.dumps(payload))
    assert invalid_items["ok"] is False
    assert "APP_PRESENTATION_ITEMS_TARGET_INVALID" in {
        issue["code"] for issue in invalid_items["issues"]
    }


def test_inferred_workspace_grid_requires_sidebar_and_main_to_be_siblings():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "sidebar",
            "role": "navigation",
            "parent_region": "main",
        }
    )

    nested = build_presentation_plan(payload)
    nested_screen = nested["screens"][0]
    nested_rules = {rule["target_ref"]: rule for rule in nested_screen["rules"]}
    assert nested_rules["region:main"]["base"]["display"] == "flex"
    assert "areas" not in nested_rules["region:main"]["base"]
    assert nested_rules["region:sidebar"]["base"] == {
        "display": "flex",
        "direction": "column",
        "gap": "md",
        "order": -1,
        "padding": "md",
        "sticky": False,
    }

    sidebar = next(item for item in view["regions"] if item["id"] == "sidebar")
    sidebar["parent_region"] = view["root_region"]
    siblings = build_presentation_plan(payload)
    sibling_rules = {rule["target_ref"]: rule for rule in siblings["screens"][0]["rules"]}
    assert sibling_rules[f'region:{view["root_region"]}']["base"]["areas"] == [["sidebar", "main"]]
    assert sibling_rules[f'region:{view["root_region"]}']["variants"]["medium"] == {
        "columns": 1,
        "areas": [["sidebar"], ["main"]],
    }
    css = presentation_plan_css(siblings)
    assert (
        '[data-ir-id="region_root"] > [data-ir-id="region_sidebar"] { grid-area: sidebar; }'
        in css
    )
    assert '[data-ir-id="region_root"] > [data-ir-id="region_main"] { grid-area: main; }' in css


def test_same_row_anchor_must_match_effective_breakpoint_layout():
    payload = _app_with_declared_presentation()
    screen = payload["screens"][0]
    view = screen["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "sidebar",
            "role": "navigation",
            "layout": "stack",
            "min_children": 0,
            "max_children": None,
            "parent_region": view["root_region"],
        }
    )
    presentation = screen["presentation"]
    presentation["profile"] = "neutral"
    presentation["rules"][0] = {
        "id": "queue_shell",
        "target_ref": "region:root",
        "base": {
            "display": "grid",
            "columns": ["rail_md", "fluid"],
            "areas": [["sidebar", "main"]],
        },
        "variants": {
            "compact": {"columns": 1, "areas": [["sidebar"], ["main"]]},
            "medium": {"columns": 1, "areas": [["sidebar"], ["main"]]},
            "wide": {"columns": ["rail_md", "fluid"], "areas": [["sidebar", "main"]]},
        },
    }
    presentation["anchors"] = [
        {
            "id": "sidebar_main_row",
            "target_ref": "region:main",
            "relation": "same_row",
            "anchor_ref": "region:sidebar",
            "viewports": ["compact", "medium", "wide"],
        }
    ]

    invalid = validate_app_text(json.dumps(payload))
    assert invalid["ok"] is False
    issue = next(
        item
        for item in invalid["issues"]
        if item["code"] == "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID"
    )
    assert "compact, medium" in issue["message"]
    assert "region:root stacks" in issue["message"]

    presentation["anchors"][0]["viewports"] = ["wide"]
    valid = validate_app_text(json.dumps(payload))
    assert valid["ok"] is True, valid["issues"]


def test_operations_workspace_requires_visible_wide_rail_and_content_row():
    payload = _app_with_declared_presentation()
    screen = payload["screens"][0]
    view = screen["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "sidebar",
            "role": "navigation",
            "layout": "stack",
            "min_children": 0,
            "max_children": None,
            "parent_region": view["root_region"],
        }
    )
    presentation = screen["presentation"]
    presentation["rules"][0] = {
        "id": "queue_shell",
        "target_ref": "region:root",
        "base": {"display": "grid", "columns": 1, "areas": [["sidebar"], ["main"]]},
        "variants": {"wide": {"columns": 1, "areas": [["sidebar"], ["main"]]}},
    }
    presentation["rules"].append(
        {
            "id": "hidden_sidebar",
            "target_ref": "region:sidebar",
            "base": {"display": "none"},
        }
    )

    invalid = validate_app_text(json.dumps(payload))
    matching = [
        item
        for item in invalid["issues"]
        if item["code"] == "APP_PRESENTATION_PROFILE_INVARIANT_INVALID"
    ]
    assert len(matching) == 2
    assert any("wide rail/content row" in item["message"] for item in matching)
    assert any("cannot hide" in item["message"] for item in matching)


def test_declared_stacked_shell_disables_inherited_sticky_sidebar_per_breakpoint():
    payload = _app_with_declared_presentation()
    screen = payload["screens"][0]
    view = screen["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "sidebar",
            "role": "navigation",
            "parent_region": view["root_region"],
        }
    )
    presentation = screen["presentation"]
    presentation["rules"][0] = {
        "id": "queue_shell",
        "target_ref": "region:root",
        "base": {"display": "flex", "direction": "column"},
        "variants": {
            "compact": {"display": "flex", "direction": "column"},
            "medium": {"display": "flex", "direction": "column"},
            "wide": {"display": "grid", "columns": ["rail_md", "fluid"]},
        },
    }
    presentation["rules"].append(
        {
            "id": "queue_sidebar",
            "target_ref": "region:sidebar",
            "base": {"display": "flex", "direction": "column", "width": "full"},
            "variants": {"wide": {"width": "rail_md"}},
        }
    )

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    sidebar = rules["region:sidebar"]

    assert sidebar["base"]["sticky"] is False
    assert sidebar["variants"]["compact"]["sticky"] is False
    assert sidebar["variants"]["medium"]["sticky"] is False
    assert sidebar["variants"]["wide"]["sticky"] is True


def test_inferred_metric_motif_owns_the_grid_instead_of_its_region_and_items():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "metrics",
            "role": "metrics",
            "parent_region": "main",
        }
    )
    view["motifs"].append(
        {
            "id": "dispatch_metrics",
            "kind": "dashboard",
            "region": "metrics",
            "members": [],
        }
    )

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    assert rules["region:metrics"]["role"] == "section"
    assert rules["region:metrics"]["base"] == {"min_inline_size": "zero"}
    assert rules["motif:dispatch_metrics"]["base"]["display"] == "grid"
    assert rules["motif:dispatch_metrics"]["base"]["columns"] == 3
    assert "items" not in rules["motif:dispatch_metrics"]

    css = presentation_plan_css(plan)
    motif_selector = '[data-ir-id="motif_dispatch_metrics"]'
    assert f"{motif_selector} {{ display: grid !important" in css
    assert f"{motif_selector} > .vs-value {{ border-top: 2px solid #26342e" in css
    assert f"{motif_selector} > [data-ir-id] {{ display: grid !important" not in css
    assert "; }}" not in css


def test_declared_item_grid_drops_inferred_child_areas_it_no_longer_defines():
    payload = _app_with_declared_presentation()
    screen = payload["screens"][0]
    view = screen["intent_bundle"]["view_spec"]
    motif = view["motifs"][0]
    motif.update(
        {
            "id": "technician_workload_motif",
            "kind": "detail",
            "members": ["workload_heading", "maya_workload", "owen_workload"],
        }
    )
    prototype = view["bindings"][0]
    view["bindings"] = [
        {
            **prototype,
            "id": binding_id,
            "address": f"node:incident_queue#attr:{field}",
        }
        for binding_id, field in (
            ("workload_heading", "title"),
            ("maya_workload", "severity"),
            ("owen_workload", "status"),
        )
    ]
    screen["presentation"] = {
        "profile": "neutral",
        "rules": [
            {
                "id": "workload_items",
                "target_ref": "motif:technician_workload_motif",
                "items": {"base": {"display": "grid", "columns": 1}},
            }
        ],
        "anchors": [],
    }

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    for target in (
        "binding:workload_heading",
        "binding:maya_workload",
        "binding:owen_workload",
    ):
        assert "area" not in rules[target]["base"]
        assert all(
            "area" not in variant for variant in rules[target]["variants"].values()
        )

    css = presentation_plan_css(plan)
    assert ":is(tbody, thead, tfoot) > [data-ir-id] > .vs-value" in css
    assert ":is(tbody > .vs-value" not in css


def test_inferred_job_rows_use_readable_semantic_tracks_at_each_width():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    motif = view["motifs"][0]
    motif["id"] = "job_queue"
    title_binding = view["bindings"][0]
    title_binding["id"] = "incident_title"
    motif["members"] = ["incident_title"]

    plan = build_presentation_plan(payload)
    rule = next(
        item for item in plan["screens"][0]["rules"] if item["target_ref"] == "motif:job_queue"
    )
    assert rule["items"]["base"]["columns"] == [
        "identity_lg",
        "fluid_wide",
        "fluid",
        "auto",
    ]
    assert rule["items"]["variants"] == {
        "compact": {
            "columns": ["identity_sm", "fluid", "auto"],
            "areas": [
                ["record_id", "record_status", "record_status"],
                ["record_title", "record_title", "record_title"],
                ["record_location", "record_location", "record_time"],
                ["record_technician", "record_technician", "record_technician_role"],
            ],
            "gap": "xs",
            "padding": "md",
        },
        "medium": {
            "columns": ["identity_md", "fluid_wide", "auto"],
            "areas": [
                ["record_id", "record_title", "record_status"],
                ["record_id", "record_location", "record_time"],
                ["record_id", "record_technician", "record_technician_role"],
            ],
            "gap": "md",
            "padding": "md",
        },
        "wide": {
            "columns": ["identity_lg", "fluid_wide", "fluid", "auto"],
            "areas": [
                ["record_id", "record_title", "record_technician", "record_status"],
                ["record_id", "record_location", "record_technician_role", "record_status"],
                ["record_id", "record_time", "record_technician_role", "record_status"],
            ],
        },
    }
    title_rule = next(
        item
        for item in plan["screens"][0]["rules"]
        if item["target_ref"] == "binding:incident_title"
    )
    assert title_rule["base"] == {
        "area": "record_title",
        "font_size": "body",
        "font_weight": "semibold",
        "line_height": "snug",
        "min_inline_size": "zero",
        "text_wrap": "normal",
        "width": "full",
    }
    assert title_rule["variants"] == {}

    css = presentation_plan_css(plan)
    title_css = next(
        line for line in css.splitlines() if '[data-ir-id="binding_incident_title"] {' in line
    )
    assert "font-size: 0.95rem !important" in title_css
    assert "grid-area: record_title !important" in title_css
    assert 'grid-column: span 2 / span 2' not in css
    assert "; }}" not in css
    assert (
        ":is(tbody, thead, tfoot) > [data-ir-id]:not(:has([data-resource-id]))"
        in css
    )
    assert ":has([data-resource-id]) { min-height: 11.75rem; align-content: center; }" in css
    assert ":is(tbody > [data-ir-id" not in css


def test_resource_repeat_job_aliases_receive_semantic_areas_and_typography():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    prototype = view["bindings"][0]
    member_ids = [
        f"rvb_dispatch_jobs__J-204__{field}"
        for field in ("id", "title", "location", "time", "assignee", "role", "status")
    ]
    view["bindings"] = [
        {
            **prototype,
            "id": binding_id,
            "address": f"node:job#attr:{binding_id.rsplit('__', 1)[-1]}",
            "target_region": "main",
        }
        for binding_id in member_ids
    ]
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "job_cards",
            "kind": "table",
            "region": "main",
            "members": member_ids,
        }
    ]

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    expected_areas = {
        "id": "record_id",
        "title": "record_title",
        "location": "record_location",
        "time": "record_time",
        "assignee": "record_technician",
        "role": "record_technician_role",
        "status": "record_status",
    }
    for field, area in expected_areas.items():
        assert rules[f"binding:rvb_dispatch_jobs__J-204__{field}"]["base"]["area"] == area
    assert rules["binding:rvb_dispatch_jobs__J-204__location"]["role"] == "record_metadata"
    assert rules["binding:rvb_dispatch_jobs__J-204__assignee"]["role"] == "record_technician"
    assert rules["binding:rvb_dispatch_jobs__J-204__role"]["role"] == "record_technician_role"
    assert rules["binding:rvb_dispatch_jobs__J-204__status"]["role"] == "record_status"

    css = presentation_plan_css(plan)
    assert '[data-ir-id="binding_rvb_dispatch_jobs__J-204__time"] { grid-area: record_time !important' in css
    assert 'font-size: 0.82rem !important' in css


def test_brand_section_headings_and_empty_workload_tables_avoid_generic_surfaces():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    prototype = view["bindings"][0]
    view["bindings"] = [
        {
            **prototype,
            "id": binding_id,
            "address": f"node:fixture#attr:{binding_id}",
            "target_region": "main",
        }
        for binding_id in ("brand_mark", "brand_name", "attention_title", "attention_label")
    ]
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "brand_motif",
            "kind": "hero",
            "region": "main",
            "members": ["brand_mark", "brand_name"],
        },
        {
            "id": "attention_heading_motif",
            "kind": "hero",
            "region": "main",
            "members": ["attention_title", "attention_label"],
        },
        {
            "id": "technician_workload",
            "kind": "table",
            "region": "main",
            "members": [],
        },
    ]

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    assert rules["motif:brand_motif"]["role"] == "brand"
    assert rules["motif:brand_motif"]["base"]["display"] == "flex"
    assert rules["motif:attention_heading_motif"]["role"] == "section_heading"
    assert rules["motif:attention_heading_motif"]["base"]["justify"] == "between"
    assert rules["motif:technician_workload"]["role"] == "workload_row"
    assert rules["motif:technician_workload"]["items"]["base"]["columns"] == [
        "fluid",
        "auto",
    ]

    css = presentation_plan_css(plan)
    assert '[data-ir-id="motif_brand_motif"] > [data-binding-id="brand_mark"]' in css
    assert '[data-ir-id="motif_attention_heading_motif"] { border: 0 !important' in css
    assert (
        '[data-ir-id="motif_technician_workload"] > [data-ir-id] > .vs-value '
        "{ border-top:"
    ) not in css
    assert "; }}" not in css


def test_attention_job_rows_preserve_compact_emphasis_without_sacrificing_title_width():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    bindings = view["bindings"][:3]
    member_ids = (
        "rvb_attention_jobs__J-205__id",
        "rvb_attention_jobs__J-205__title",
        "rvb_attention_jobs__J-205__status",
    )
    for binding, binding_id in zip(bindings, member_ids, strict=True):
        binding["id"] = binding_id
        binding["target_region"] = "main"
    view["bindings"] = bindings
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "needs_attention",
            "kind": "list",
            "region": "main",
            "members": list(member_ids),
        }
    ]

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    assert rules["motif:needs_attention"]["role"] == "attention_job_row"
    assert rules["binding:rvb_attention_jobs__J-205__title"]["base"]["width"] == "full"

    css = presentation_plan_css(plan)
    assert "@media (max-width: 599px)" in css
    assert ":has([data-resource-id]) { min-height: 28.75rem; align-content: start; }" in css


def test_lane_headers_are_flat_shared_rows_instead_of_nested_cards():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    bindings = view["bindings"][:2]
    for binding, binding_id in zip(
        bindings,
        ("attention_title", "attention_label"),
        strict=True,
    ):
        binding["id"] = binding_id
        binding["target_region"] = "main"
    view["bindings"] = bindings
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "attention_lane",
            "kind": "detail",
            "region": "main",
            "members": ["attention_title", "attention_label"],
        }
    ]

    plan = build_presentation_plan(payload)
    rule = next(
        item
        for item in plan["screens"][0]["rules"]
        if item["target_ref"] == "motif:attention_lane"
    )
    assert rule["role"] == "lane_header"
    assert rule["base"]["justify"] == "between"

    css = presentation_plan_css(plan)
    assert '[data-ir-id="motif_attention_lane"] { display: flex !important' in css
    assert "border: 0 !important" in css


def test_inferred_hero_header_places_grid_on_semantic_surface():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    view["regions"].append(
        {
            "id": "page_header",
            "role": "header",
            "parent_region": "main",
        }
    )
    bindings = view["bindings"][:3]
    for binding, binding_id in zip(
        bindings,
        ("dispatch_eyebrow", "dispatch_title", "operator_initials"),
        strict=True,
    ):
        binding["id"] = binding_id
        binding["target_region"] = "page_header"
    view["bindings"] = bindings
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "header_motif",
            "kind": "hero",
            "region": "page_header",
            "members": ["dispatch_eyebrow", "dispatch_title", "operator_initials"],
        }
    ]

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}
    header = rules["motif:header_motif"]
    assert "items" not in header
    assert header["base"]["display"] == "grid"
    assert header["base"]["areas"] == [
        ["header_eyebrow", "header_avatar"],
        ["header_title", "header_avatar"],
    ]
    assert rules["binding:dispatch_title"]["base"]["area"] == "header_title"
    assert rules["binding:dispatch_title"]["base"]["width"] == "intrinsic"
    assert rules["binding:dispatch_eyebrow"]["base"]["area"] == "header_eyebrow"
    assert rules["binding:operator_initials"]["base"]["area"] == "header_avatar"

    css = presentation_plan_css(plan)
    assert '[data-ir-id="motif_header_motif"] { display: grid !important' in css
    assert "align-items: end;" in css
    assert '[data-ir-id="motif_header_motif"] > [data-ir-id] { display: grid !important' not in css


def test_page_hero_in_main_region_still_gets_page_header_typography():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    bindings = view["bindings"][:3]
    for binding, binding_id in zip(
        bindings,
        ("hero_eyebrow", "hero_title", "avatar_initials"),
        strict=True,
    ):
        binding["id"] = binding_id
        binding["target_region"] = "main"
    view["bindings"] = bindings
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "page_hero",
            "kind": "hero",
            "region": "main",
            "members": ["hero_eyebrow", "hero_title", "avatar_initials"],
        }
    ]

    plan = build_presentation_plan(payload)
    rules = {rule["target_ref"]: rule for rule in plan["screens"][0]["rules"]}

    assert rules["motif:page_hero"]["role"] == "page_header"
    assert rules["motif:page_hero"]["base"]["display"] == "grid"
    assert rules["binding:hero_title"]["base"] == {
        "area": "header_title",
        "font_family": "serif",
        "font_size": "display_lg",
        "font_weight": "medium",
        "foreground": "ink",
        "letter_spacing": "tight",
        "line_height": "tight",
        "max_width": "full",
        "min_inline_size": "zero",
        "text_wrap": "normal",
        "width": "intrinsic",
    }
    assert rules["binding:hero_title"]["variants"]["compact"] == {
        "font_size": "display_sm"
    }
    css = presentation_plan_css(plan)
    assert '[data-ir-id="binding_hero_title"] { margin: 0 !important; }' in css


def test_retained_v5_plans_normalize_direct_semantic_surfaces_and_action_slots():
    retained = ROOT / "conformance" / "agent-ui-v2" / "fixtures" / "shakedown-104729-2026-08-06-v5"
    core = json.loads(
        (retained / "viewspec-core.app.json").read_text(encoding="utf-8")
    )
    deep = json.loads(
        (retained / "viewspec-deep.app.json").read_text(encoding="utf-8")
    )

    core_plan = build_presentation_plan(core)
    core_screen = core_plan["screens"][0]
    core_rules = {rule["target_ref"]: rule for rule in core_screen["rules"]}
    core_hero = core_rules["motif:page_hero"]
    core_metrics = core_rules["motif:summary_metrics"]
    assert core_hero["role"] == "page_header"
    assert "columns" not in core_hero["variants"]["compact"]
    assert core_rules["binding:page_title"]["base"]["width"] == "intrinsic"
    assert core_metrics["items"]["base"]["areas"] == [
        ["metric_0_value", "metric_1_value", "metric_2_value"],
        ["metric_0_label", "metric_1_label", "metric_2_label"],
    ]
    assert "order" not in core_rules["region:controls"]["base"]

    deep_plan = build_presentation_plan(deep)
    deep_screen = deep_plan["screens"][0]
    deep_rules = {rule["target_ref"]: rule for rule in deep_screen["rules"]}
    assert deep_rules["motif:hero_motif"]["role"] == "page_header"
    assert deep_rules["motif:hero_motif"]["base"]["areas"] == [
        ["header_eyebrow", "header_avatar"],
        ["header_title", "header_avatar"],
    ]
    assert deep_rules["motif:sidebar_motif"]["base"]["display"] == "block"
    assert deep_rules["motif:sidebar_motif"]["items"]["base"]["display"] == "flex"
    assert (
        deep_rules["motif:sidebar_motif"]["items"]["variants"]["compact"][
            "direction"
        ]
        == "row"
    )
    assert deep_rules["motif:metrics_motif"]["base"]["display"] == "grid"
    assert deep_rules["motif:metrics_motif"]["base"]["columns"] == 3
    assert deep_rules["motif:metrics_motif"]["items"]["base"] == {
        "display": "flex",
        "direction": "column",
        "gap": "xs",
        "padding": "none",
        "width": "full",
    }
    assert deep_rules["motif:workload_motif"]["role"] == "workload_summary"
    assert "order" not in deep_rules["motif:workload_motif"]["base"]
    assert deep_rules["motif:workload_motif"]["items"]["base"]["areas"] == [
        ["metric_heading", "metric_heading", "metric_heading"],
        ["metric_0", "metric_1", "metric_2"],
    ]
    assert deep_rules["binding:page_title"]["base"]["width"] == "intrinsic"
    assert deep_rules["binding:page_title"]["base"]["padding"] == "none"
    assert {
        item["code"] for item in deep_screen["diagnostics"]
    } >= {
        "APP_PRESENTATION_DASHBOARD_GRID_NORMALIZED",
        "APP_PRESENTATION_SEMANTIC_OVERRIDE_BOUNDED",
    }
    deep_css = presentation_plan_css(deep_plan)
    assert '[data-ir-id^="planner_region_"][data-ir-id$="_actions"]' in deep_css
    assert "; }}" not in deep_css


def test_workload_resource_rows_are_not_styled_as_metric_strips():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    view = payload["screens"][0]["intent_bundle"]["view_spec"]
    bindings = view["bindings"][:2]
    member_ids = (
        "rvb_technician_workload__J-204__technician",
        "rvb_technician_workload__J-204__workload",
    )
    for binding, binding_id in zip(bindings, member_ids, strict=True):
        binding["id"] = binding_id
        binding["target_region"] = "main"
    view["bindings"] = bindings
    view["groups"] = []
    view["motifs"] = [
        {
            "id": "technician_workload_rows",
            "kind": "table",
            "region": "main",
            "members": list(member_ids),
        }
    ]

    plan = build_presentation_plan(payload)
    rule = next(
        item
        for item in plan["screens"][0]["rules"]
        if item["target_ref"] == "motif:technician_workload_rows"
    )
    assert rule["role"] == "workload_row"
    assert rule["items"]["base"]["columns"] == ["fluid", "auto"]

    css = presentation_plan_css(plan)
    assert (
        '[data-ir-id="motif_technician_workload_rows"] > [data-ir-id], '
    ) in css
    assert "{ align-items: center; border: 1px solid #d8ddd9" in css
    assert (
        '[data-ir-id="motif_technician_workload_rows"] > [data-ir-id] > .vs-value '
        "{ border-top:"
    ) not in css


def test_inferred_navigation_rail_uses_a_vertical_item_flow():
    payload = _app_with_declared_presentation()
    payload["screens"][0].pop("presentation")
    motif = payload["screens"][0]["intent_bundle"]["view_spec"]["motifs"][0]
    motif["id"] = "navigation_rail"

    plan = build_presentation_plan(payload)
    rule = next(
        item
        for item in plan["screens"][0]["rules"]
        if item["target_ref"] == "motif:navigation_rail"
    )

    assert rule["role"] == "navigation"
    assert rule["base"]["display"] == "block"
    assert rule["items"]["base"] == {
        "display": "flex",
        "direction": "column",
        "gap": "sm",
        "padding": "none",
    }
    css = presentation_plan_css(plan)
    assert (
        '[data-ir-id="motif_navigation_rail"] > :is(tbody, thead, tfoot) > [data-ir-id] '
        "{ display: flex !important; flex-direction: column"
        in css
    )
    assert "flex-wrap: wrap" in css
    assert "flex: 0 0 auto" in css
    assert "white-space: nowrap !important" in css


def test_declared_anchor_generates_canonical_viewport_proof_and_typechecks(tmp_path):
    template_modules = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules"
    tsc = template_modules / ".bin" / "tsc"
    if not tsc.is_file() or not (template_modules / "@types" / "node").exists():
        pytest.skip("host verifier dependencies are not installed")

    payload = _app_with_declared_presentation()
    app_path = tmp_path / "viewspec.app.json"
    output_dir = tmp_path / "react"
    _write_app(app_path, payload)
    result = compile_app(app_path, out_dir=output_dir, target=REACT_APP_TARGET, cwd=tmp_path)
    assert result["ok"] is True, result.get("errors")

    runtime_test = (output_dir / "tests" / "viewspec-app.spec.ts").read_text(encoding="utf-8")
    assert 'test("PresentationPlan responsive anchors"' in runtime_test
    assert '{ id: "compact", width: 390, height: 844 }' in runtime_test
    assert '{ id: "medium", width: 768, height: 1024 }' in runtime_test
    assert '{ id: "wide", width: 1440, height: 1000 }' in runtime_test
    assert "APP_PRESENTATION_ANCHOR_DIVERGED" in runtime_test
    assert "divergence.property" in runtime_test

    output_dir.joinpath("node_modules").symlink_to(template_modules, target_is_directory=True)
    completed = subprocess.run(
        [str(tsc), "--noEmit", "-p", str(output_dir / "tsconfig.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get(PRESENTATION_E2E_OPT_IN) != "1",
    reason=f"set {PRESENTATION_E2E_OPT_IN}=1 to run real PresentationPlan Chromium proof",
)
def test_real_chromium_proves_anchors_and_reports_first_divergence(tmp_path, monkeypatch):
    template_modules = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules"
    if not template_modules.is_dir():
        pytest.skip("host verifier dependencies are not installed")
    monkeypatch.setenv("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR", str(template_modules))

    payload = _app_with_declared_presentation()
    app_path = tmp_path / "passing.app.json"
    output_dir = tmp_path / "passing"
    _write_app(app_path, payload)
    assert compile_app(app_path, out_dir=output_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    passed = verify_react_app_artifact_dir(output_dir, install=True)
    assert passed["ok"] is True, passed["errors"]
    assert passed["assertions"]["presentation_anchor_assertion_count"] == 1
    assert passed["assertions"]["presentation_viewport_count"] == 3

    static_dir = tmp_path / "static"
    assert compile_app(app_path, out_dir=static_dir, cwd=tmp_path)["ok"]
    output_dir.joinpath("node_modules").symlink_to(template_modules, target_is_directory=True)
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=output_dir,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    score_spec = tmp_path / "parity-spec.json"
    score_report = tmp_path / "parity-report.json"
    score_spec.write_text(
        json.dumps(
            {
                "required_text": ["Incident Queue", "inc_1042", "inc_1043", "Triage"],
                "visual_anchors": ["Incident Queue", "inc_1042", "high", "inc_1043", "medium", "Triage"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    parity = subprocess.run(
        [
            "node",
            str(ROOT / "scripts" / "agent_ui_browser_score.mjs"),
            "--candidate",
            str(output_dir / "dist"),
            "--candidate-entry",
            "index.html",
            "--reference",
            str(static_dir / "index.html"),
            "--reference-step",
            "0",
            "--spec",
            str(score_spec),
            "--out",
            str(score_report),
            "--evidence",
            str(tmp_path / "parity-evidence"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert parity.returncode == 0, parity.stdout + parity.stderr
    parity_report = json.loads(score_report.read_text(encoding="utf-8"))
    assert {item["viewport"]["width"] for item in parity_report["viewports"]} == {390, 768, 1440}
    assert min(item["layout_fidelity"] for item in parity_report["viewports"]) >= 0.95
    anchor_criteria = [
        criterion
        for viewport in parity_report["viewports"]
        for criterion in viewport["criteria"]
        if criterion["dimension"] == "layout_fidelity"
    ]
    assert anchor_criteria
    assert all(criterion["candidate_anchor"] for criterion in anchor_criteria)
    assert all(criterion["reference_anchor"] for criterion in anchor_criteria)

    broken = _app_with_declared_presentation()
    broken_anchor = broken["screens"][0]["presentation"]["anchors"][0]
    broken_anchor.update(
        {
            "target_ref": "binding:inc_1042_id",
            "relation": "after",
            "anchor_ref": "binding:inc_1042_severity",
        }
    )
    broken_path = tmp_path / "broken.app.json"
    broken_dir = tmp_path / "broken"
    _write_app(broken_path, broken)
    assert compile_app(broken_path, out_dir=broken_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    failed = verify_react_app_artifact_dir(broken_dir, install=True)
    assert failed["ok"] is False
    assert failed["errors"][0]["code"] == "APP_REACT_VERIFY_BROWSER_FAILED"
    message = failed["errors"][0]["message"]
    assert "APP_PRESENTATION_ANCHOR_DIVERGED:compact:queue:queue_inside_content" in message
    assert ":binding:inc_1042_id:after:top:" in message
