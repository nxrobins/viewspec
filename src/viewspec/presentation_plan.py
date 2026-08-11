"""Deterministic, target-neutral presentation planning for AppBundle screens."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from viewspec.compiler import _derive_style_tokens
from viewspec.types import IntentBundle

PRESENTATION_PLAN_SCHEMA_VERSION = 1
PRESENTATION_PLAN_FILE = "presentation_plan.json"
PRESENTATION_PLAN_MAX_BYTES = 256 * 1024
PRESENTATION_PROFILES = ("neutral", "operations_workspace", "editorial_dashboard")
PRESENTATION_BREAKPOINTS = ("compact", "medium", "wide")
PRESENTATION_RELATIONS = ("inside", "before", "after", "aligned_start", "aligned_center", "same_row")
PRESENTATION_TARGET_RE = re.compile(r"^(region|motif|binding):[A-Za-z0-9_.-]+$")
PRESENTATION_MAX_RULES = 96
PRESENTATION_MAX_ANCHORS = 64
PRESENTATION_MAX_GRID_TRACKS = 4
PRESENTATION_MAX_GRID_ROWS = 4
PRESENTATION_ITEM_LAYOUT_KEYS = ("base", "variants")

DISPLAY_VALUES = ("block", "flex", "grid", "none")
DIRECTION_VALUES = ("row", "column")
SPACING_VALUES = ("none", "xs", "sm", "md", "lg", "xl", "2xl", "3xl")
SIZE_VALUES = ("auto", "full", "intrinsic", "content_sm", "content_md", "content_lg", "rail_sm", "rail_md", "rail_lg")
TRACK_VALUES = (
    "auto",
    "fluid",
    "fluid_wide",
    "content_sm",
    "content_md",
    "identity_sm",
    "identity_md",
    "identity_lg",
    "rail_sm",
    "rail_md",
    "rail_lg",
)
VISIBILITY_VALUES = ("visible", "hidden")
ALIGN_VALUES = ("start", "center", "end", "stretch")
JUSTIFY_VALUES = ("start", "center", "end", "between", "stretch")
TEXT_WRAP_VALUES = ("normal", "anywhere", "no_wrap")
MIN_INLINE_SIZE_VALUES = ("zero", "content")
FONT_FAMILY_VALUES = ("sans", "serif", "mono")
FONT_SIZE_VALUES = ("caption", "small", "body", "title", "display_sm", "display_lg")
FONT_WEIGHT_VALUES = ("regular", "medium", "semibold", "bold", "black")
LINE_HEIGHT_VALUES = ("tight", "snug", "normal", "relaxed")
LETTER_SPACING_VALUES = ("tight", "normal", "wide")
TEXT_TRANSFORM_VALUES = ("none", "uppercase")
FOREGROUND_VALUES = ("ink", "muted", "inverse", "accent", "danger")
BACKGROUND_VALUES = ("transparent", "canvas", "surface", "sidebar", "subtle", "accent", "danger_subtle")
BORDER_VALUES = ("none", "subtle", "strong")
RADIUS_VALUES = ("none", "sm", "md", "pill")

LAYOUT_PROPERTY_KEYS = frozenset(
    {
        "align",
        "area",
        "areas",
        "background",
        "border",
        "columns",
        "direction",
        "display",
        "font_family",
        "font_size",
        "font_weight",
        "foreground",
        "gap",
        "justify",
        "letter_spacing",
        "line_height",
        "max_lines",
        "max_width",
        "min_inline_size",
        "order",
        "padding",
        "radius",
        "span",
        "sticky",
        "text_transform",
        "text_wrap",
        "visibility",
        "width",
    }
)

_SPACING_CSS = {
    "none": "0",
    "xs": "0.25rem",
    "sm": "0.5rem",
    "md": "0.75rem",
    "lg": "1rem",
    "xl": "1.5rem",
    "2xl": "2.5rem",
    "3xl": "4.5rem",
}
_SIZE_CSS = {
    "auto": "auto",
    "full": "100%",
    "intrinsic": "max-content",
    "content_sm": "40rem",
    "content_md": "56rem",
    "content_lg": "76rem",
    "rail_sm": "11.25rem",
    "rail_md": "14.5rem",
    "rail_lg": "17.5rem",
}
_TRACK_CSS = {
    "auto": "auto",
    "fluid": "minmax(0, 1fr)",
    "fluid_wide": "minmax(0, 1.4fr)",
    "content_sm": "minmax(0, 40rem)",
    "content_md": "minmax(0, 56rem)",
    "identity_sm": "3.625rem",
    "identity_md": "4.5rem",
    "identity_lg": "5.625rem",
    "rail_sm": "11.25rem",
    "rail_md": "14.5rem",
    "rail_lg": "17.5rem",
}
_FONT_FAMILY_CSS = {
    "sans": "Inter, ui-sans-serif, system-ui, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
}
_FONT_SIZE_CSS = {
    "caption": "0.72rem",
    "small": "0.82rem",
    "body": "0.95rem",
    "title": "1.6rem",
    "display_sm": "2.4rem",
    "display_lg": "4.4rem",
}
_FONT_WEIGHT_CSS = {"regular": "400", "medium": "500", "semibold": "600", "bold": "700", "black": "800"}
_LINE_HEIGHT_CSS = {"tight": "1.05", "snug": "1.2", "normal": "1.5", "relaxed": "1.7"}
_LETTER_SPACING_CSS = {"tight": "-0.02em", "normal": "0", "wide": "0.1em"}
_FOREGROUND_CSS = {
    "ink": "#19241f",
    "muted": "#66716d",
    "inverse": "#f5f7f2",
    "accent": "#8fd11f",
    "danger": "#8f2525",
}
_BACKGROUND_CSS = {
    "transparent": "transparent",
    "canvas": "#ecece8",
    "surface": "#f7f7f2",
    "sidebar": "#1b2a25",
    "subtle": "#e0e5de",
    "accent": "#bdff42",
    "danger_subtle": "#f7e6e2",
}
_BORDER_CSS = {"none": "none", "subtle": "1px solid #cdd2cb", "strong": "1px solid #26342e"}
_RADIUS_CSS = {"none": "0", "sm": "0.35rem", "md": "0.75rem", "pill": "999px"}


def build_presentation_plan(payload: dict[str, Any]) -> dict[str, Any]:
    screens: list[dict[str, Any]] = []
    for screen in payload.get("screens", []):
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        declared = screen.get("presentation")
        screen_plan = _declared_screen_plan(screen, declared) if isinstance(declared, dict) else _inferred_screen_plan(screen)
        screens.append(screen_plan)
    profiles = {str(screen.get("profile") or "neutral") for screen in screens}
    single_route_workspace = len(payload.get("routes", [])) == 1 and profiles == {"operations_workspace"}
    return {
        "schema_version": PRESENTATION_PLAN_SCHEMA_VERSION,
        "shell": {
            "chrome": "hidden_single_route" if single_route_workspace else "header",
            "main_width": "full",
        },
        "breakpoints": {
            "compact": {"max_width": 599},
            "medium": {"min_width": 600, "max_width": 1023},
            "wide": {"min_width": 1024},
        },
        "screens": screens,
    }


def presentation_plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(plan).encode("utf-8")).hexdigest()


def presentation_plan_text(plan: dict[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def presentation_plan_diagnostics(plan: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for screen in plan.get("screens", []):
        if not isinstance(screen, dict):
            continue
        screen_id = str(screen.get("id") or "")
        for diagnostic in screen.get("diagnostics", []):
            if not isinstance(diagnostic, dict):
                continue
            diagnostics.append({"screen_id": screen_id, **copy.deepcopy(diagnostic)})
    return diagnostics


def presentation_plan_css(plan: dict[str, Any]) -> str:
    lines = [
        "/* ViewSpec PresentationPlan v1: shared by static and React AppBundle targets. */",
        ".vs-app-shell > .vs-app-chrome, .vs-app-shell > .vs-app-header {",
        "  position: relative; top: auto; z-index: 10; display: flex; align-items: center;",
        "  justify-content: space-between; gap: 1rem; min-height: 3.75rem; padding: 0.75rem 1rem;",
        "  border: 0; border-bottom: 1px solid #d8ddd9; background: #ffffff; color: #17201c;",
        "}",
        ".vs-app-shell > .vs-app-chrome nav, .vs-app-shell > .vs-app-header nav { display: flex; flex-wrap: wrap; gap: 0.5rem; }",
        ".vs-app-main { width: 100%; max-width: none; margin: 0; padding: 0; }",
        '[data-viewspec-app-screen] [data-ir-id^="binding_"] { grid-column: auto !important; }',
    ]
    shell = plan.get("shell") if isinstance(plan.get("shell"), dict) else {}
    if shell.get("chrome") == "hidden_single_route":
        lines.extend(
            [
                ".vs-app-shell > .vs-app-chrome, .vs-app-shell > .vs-app-header { display: none !important; }",
                ".vs-app-main { min-height: 100vh; }",
            ]
        )
    for screen in plan.get("screens", []):
        if not isinstance(screen, dict):
            continue
        lines.extend(_screen_plan_css(screen))
    return "\n".join(lines).strip()


def validate_screen_presentation(
    presentation: Any,
    *,
    screen: dict[str, Any],
    path: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(presentation, dict):
        return [_issue("APP_PRESENTATION_NOT_OBJECT", path, "presentation must be an object.")]
    extra = sorted(set(presentation) - {"anchors", "profile", "rules"})
    if extra:
        issues.append(_issue("APP_PRESENTATION_UNKNOWN_FIELD", path, f"Unsupported presentation field(s): {', '.join(extra)}."))
    profile = presentation.get("profile", "neutral")
    if profile not in PRESENTATION_PROFILES:
        issues.append(
            _issue(
                "APP_PRESENTATION_PROFILE_UNSUPPORTED",
                f"{path}.profile",
                f"profile must be one of {', '.join(PRESENTATION_PROFILES)}.",
            )
        )
    target_refs = _screen_target_refs(screen)
    target_parents = _screen_target_parents(screen)
    rules = presentation.get("rules", [])
    if not isinstance(rules, list):
        issues.append(_issue("APP_PRESENTATION_RULES_NOT_ARRAY", f"{path}.rules", "rules must be an array."))
        rules = []
    if len(rules) > PRESENTATION_MAX_RULES:
        issues.append(_issue("APP_PRESENTATION_LIMIT_EXCEEDED", f"{path}.rules", f"rules exceeds {PRESENTATION_MAX_RULES} entries."))
    seen_rule_ids: set[str] = set()
    seen_targets: set[str] = set()
    declared_areas = _declared_area_names(rules)
    for index, rule in enumerate(rules):
        rule_path = f"{path}.rules[{index}]"
        if not isinstance(rule, dict):
            issues.append(_issue("APP_PRESENTATION_RULE_NOT_OBJECT", rule_path, "Each presentation rule must be an object."))
            continue
        extra = sorted(set(rule) - {"base", "id", "items", "target_ref", "variants"})
        if extra:
            issues.append(_issue("APP_PRESENTATION_UNKNOWN_FIELD", rule_path, f"Unsupported rule field(s): {', '.join(extra)}."))
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            issues.append(_issue("APP_PRESENTATION_FIELD_REQUIRED", f"{rule_path}.id", "Rule id is required."))
        elif rule_id in seen_rule_ids:
            issues.append(_issue("APP_PRESENTATION_DUPLICATE_RULE", f"{rule_path}.id", f"Duplicate presentation rule id {rule_id}."))
        else:
            seen_rule_ids.add(rule_id)
        target_ref = rule.get("target_ref")
        if not isinstance(target_ref, str) or not PRESENTATION_TARGET_RE.fullmatch(target_ref):
            issues.append(_issue("APP_PRESENTATION_TARGET_INVALID", f"{rule_path}.target_ref", "target_ref must identify a region, motif, or binding."))
        elif target_ref not in target_refs:
            issues.append(_issue("APP_PRESENTATION_TARGET_MISSING", f"{rule_path}.target_ref", f"Presentation target {target_ref} is not declared by this screen."))
        elif target_ref in seen_targets:
            issues.append(_issue("APP_PRESENTATION_DUPLICATE_TARGET", f"{rule_path}.target_ref", f"Presentation target {target_ref} has more than one rule."))
        else:
            seen_targets.add(target_ref)
        issues.extend(
            _validate_layout_properties(
                rule.get("base", {}),
                f"{rule_path}.base",
                target_refs,
                declared_areas=declared_areas,
            )
        )
        variants = rule.get("variants", {})
        if not isinstance(variants, dict):
            issues.append(_issue("APP_PRESENTATION_VARIANTS_INVALID", f"{rule_path}.variants", "variants must be an object."))
        else:
            unknown = sorted(set(variants) - set(PRESENTATION_BREAKPOINTS))
            if unknown:
                issues.append(_issue("APP_PRESENTATION_BREAKPOINT_INVALID", f"{rule_path}.variants", f"Unknown breakpoint(s): {', '.join(unknown)}."))
            for breakpoint, props in variants.items():
                issues.extend(
                    _validate_layout_properties(
                        props,
                        f"{rule_path}.variants.{breakpoint}",
                        target_refs,
                        declared_areas=declared_areas,
                    )
                )
        items = rule.get("items")
        if items is not None:
            if not isinstance(target_ref, str) or not target_ref.startswith("motif:"):
                issues.append(
                    _issue(
                        "APP_PRESENTATION_ITEMS_TARGET_INVALID",
                        f"{rule_path}.items",
                        "items can only style the stable direct items generated inside a motif target.",
                    )
                )
            issues.extend(
                _validate_item_layout(
                    items,
                    f"{rule_path}.items",
                    target_refs,
                    declared_areas=declared_areas,
                )
            )
    anchors = presentation.get("anchors", [])
    if not isinstance(anchors, list):
        issues.append(_issue("APP_PRESENTATION_ANCHORS_NOT_ARRAY", f"{path}.anchors", "anchors must be an array."))
        anchors = []
    if len(anchors) > PRESENTATION_MAX_ANCHORS:
        issues.append(_issue("APP_PRESENTATION_LIMIT_EXCEEDED", f"{path}.anchors", f"anchors exceeds {PRESENTATION_MAX_ANCHORS} entries."))
    seen_anchor_ids: set[str] = set()
    effective_rules = _effective_declared_rules(screen, presentation)
    for index, anchor in enumerate(anchors):
        anchor_path = f"{path}.anchors[{index}]"
        if not isinstance(anchor, dict):
            issues.append(_issue("APP_PRESENTATION_ANCHOR_NOT_OBJECT", anchor_path, "Each anchor must be an object."))
            continue
        extra = sorted(set(anchor) - {"anchor_ref", "id", "relation", "target_ref", "viewports"})
        if extra:
            issues.append(_issue("APP_PRESENTATION_UNKNOWN_FIELD", anchor_path, f"Unsupported anchor field(s): {', '.join(extra)}."))
        anchor_id = anchor.get("id")
        if not isinstance(anchor_id, str) or not anchor_id:
            issues.append(_issue("APP_PRESENTATION_FIELD_REQUIRED", f"{anchor_path}.id", "Anchor id is required."))
        elif anchor_id in seen_anchor_ids:
            issues.append(_issue("APP_PRESENTATION_DUPLICATE_ANCHOR", f"{anchor_path}.id", f"Duplicate anchor id {anchor_id}."))
        else:
            seen_anchor_ids.add(anchor_id)
        for key in ("target_ref", "anchor_ref"):
            value = anchor.get(key)
            if not isinstance(value, str) or value not in target_refs:
                issues.append(_issue("APP_PRESENTATION_ANCHOR_TARGET_MISSING", f"{anchor_path}.{key}", f"{key} must reference a declared screen target."))
        if anchor.get("relation") not in PRESENTATION_RELATIONS:
            issues.append(_issue("APP_PRESENTATION_ANCHOR_RELATION_INVALID", f"{anchor_path}.relation", f"relation must be one of {', '.join(PRESENTATION_RELATIONS)}."))
        target_ref = anchor.get("target_ref")
        anchor_ref = anchor.get("anchor_ref")
        relation = anchor.get("relation")
        if (
            isinstance(target_ref, str)
            and isinstance(anchor_ref, str)
            and target_ref in target_refs
            and anchor_ref in target_refs
        ):
            target_inside_anchor = _target_descends_from(
                target_ref,
                anchor_ref,
                target_parents,
            )
            anchor_inside_target = _target_descends_from(
                anchor_ref,
                target_ref,
                target_parents,
            )
            if relation == "inside" and not target_inside_anchor:
                issues.append(
                    _issue(
                        "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID",
                        anchor_path,
                        f"Anchor {anchor_id or index} declares {target_ref} inside {anchor_ref}, "
                        "but the semantic target hierarchy does not contain it there.",
                    )
                )
            elif relation in {"before", "after"} and (
                target_inside_anchor or anchor_inside_target
            ):
                container, descendant = (
                    (anchor_ref, target_ref)
                    if target_inside_anchor
                    else (target_ref, anchor_ref)
                )
                issues.append(
                    _issue(
                        "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID",
                        anchor_path,
                        f"Anchor {anchor_id or index} cannot place {target_ref} {relation} "
                        f"{anchor_ref}: {descendant} is semantically contained by {container}. "
                        "Use inside, or compare targets that are not ancestors of one another.",
                    )
                )
        viewports = anchor.get("viewports", list(PRESENTATION_BREAKPOINTS))
        if not isinstance(viewports, list) or not viewports or any(item not in PRESENTATION_BREAKPOINTS for item in viewports):
            issues.append(_issue("APP_PRESENTATION_ANCHOR_VIEWPORT_INVALID", f"{anchor_path}.viewports", "viewports must contain compact, medium, or wide."))
        elif (
            relation == "same_row"
            and isinstance(target_ref, str)
            and isinstance(anchor_ref, str)
            and target_ref in target_refs
            and anchor_ref in target_refs
        ):
            issues.extend(
                _validate_same_row_anchor_layout(
                    anchor_id=str(anchor_id or index),
                    target_ref=target_ref,
                    anchor_ref=anchor_ref,
                    viewports=viewports,
                    path=anchor_path,
                    parents=target_parents,
                    rules=effective_rules,
                )
            )
    if profile == "operations_workspace":
        issues.extend(
            _validate_operations_workspace_profile(
                screen,
                rules=effective_rules,
                path=path,
            )
        )
    return issues


def _effective_declared_rules(
    screen: dict[str, Any],
    presentation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return normalized inferred+declared rules for validation-only geometry checks."""
    plan = _declared_screen_plan(screen, presentation)
    return {
        str(rule.get("target_ref")): rule
        for rule in plan.get("rules", [])
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    }


def _effective_rule_layout(
    rule: dict[str, Any] | None,
    viewport: str,
    *,
    items: bool = False,
) -> dict[str, Any]:
    if not isinstance(rule, dict):
        return {}
    container = rule.get("items") if items else rule
    if not isinstance(container, dict):
        return {}
    base = container.get("base") if isinstance(container.get("base"), dict) else {}
    variants = container.get("variants") if isinstance(container.get("variants"), dict) else {}
    variant = variants.get(viewport) if isinstance(variants.get(viewport), dict) else {}
    return _merged_layout(base, variant)


def _target_ancestors(target_ref: str, parents: dict[str, str]) -> list[str]:
    ancestors = [target_ref]
    seen = {target_ref}
    current = target_ref
    while current in parents:
        current = parents[current]
        if current in seen:
            break
        ancestors.append(current)
        seen.add(current)
    return ancestors


def _common_target_ancestor(
    target_ref: str,
    anchor_ref: str,
    parents: dict[str, str],
) -> str | None:
    anchor_ancestors = set(_target_ancestors(anchor_ref, parents))
    return next(
        (candidate for candidate in _target_ancestors(target_ref, parents) if candidate in anchor_ancestors),
        None,
    )


def _branch_below(
    target_ref: str,
    ancestor_ref: str,
    parents: dict[str, str],
) -> str | None:
    if target_ref == ancestor_ref:
        return None
    current = target_ref
    seen = {current}
    while current in parents:
        parent = parents[current]
        if parent == ancestor_ref:
            return current
        if parent in seen:
            break
        seen.add(parent)
        current = parent
    return None


def _grid_area_for_branch(
    branch_ref: str,
    viewport: str,
    rules: dict[str, dict[str, Any]],
) -> str:
    child_layout = _effective_rule_layout(rules.get(branch_ref), viewport)
    area = child_layout.get("area")
    if isinstance(area, str) and area:
        return area
    kind, _, identity = branch_ref.partition(":")
    return identity if kind == "region" else ""


def _layout_places_branches_same_row(
    layout: dict[str, Any],
    *,
    left_branch: str,
    right_branch: str,
    viewport: str,
    rules: dict[str, dict[str, Any]],
) -> bool:
    display = layout.get("display")
    if display == "flex":
        return layout.get("direction", "row") == "row"
    if display != "grid":
        return False
    columns = layout.get("columns")
    column_count = columns if isinstance(columns, int) else len(columns) if isinstance(columns, list) else 0
    if column_count < 2:
        return False
    areas = layout.get("areas")
    if not isinstance(areas, list) or not areas:
        return True
    left_area = _grid_area_for_branch(left_branch, viewport, rules)
    right_area = _grid_area_for_branch(right_branch, viewport, rules)
    if not left_area or not right_area:
        return False
    left_rows = {
        row_index
        for row_index, row in enumerate(areas)
        if isinstance(row, list) and left_area in row
    }
    right_rows = {
        row_index
        for row_index, row in enumerate(areas)
        if isinstance(row, list) and right_area in row
    }
    return bool(left_rows & right_rows)


def _validate_same_row_anchor_layout(
    *,
    anchor_id: str,
    target_ref: str,
    anchor_ref: str,
    viewports: list[Any],
    path: str,
    parents: dict[str, str],
    rules: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    common = _common_target_ancestor(target_ref, anchor_ref, parents)
    if common is None:
        return [
            _issue(
                "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID",
                path,
                f"Anchor {anchor_id} cannot keep {target_ref} and {anchor_ref} on the same row: "
                "the targets have no shared semantic layout ancestor.",
            )
        ]
    target_branch = _branch_below(target_ref, common, parents)
    anchor_branch = _branch_below(anchor_ref, common, parents)
    if target_branch is None or anchor_branch is None or target_branch == anchor_branch:
        return [
            _issue(
                "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID",
                path,
                f"Anchor {anchor_id} cannot compare {target_ref} and {anchor_ref} as independent rows: "
                f"both resolve through {common}. Use aligned_start/aligned_center or choose sibling semantic targets.",
            )
        ]
    common_rule = rules.get(common)
    uses_item_layout = bool(
        common.startswith("motif:")
        and isinstance(common_rule, dict)
        and isinstance(common_rule.get("items"), dict)
    )
    invalid_viewports = [
        str(viewport)
        for viewport in viewports
        if not _layout_places_branches_same_row(
            _effective_rule_layout(common_rule, str(viewport), items=uses_item_layout),
            left_branch=target_branch,
            right_branch=anchor_branch,
            viewport=str(viewport),
            rules=rules,
        )
    ]
    if not invalid_viewports:
        return []
    return [
        _issue(
            "APP_PRESENTATION_ANCHOR_TOPOLOGY_INVALID",
            path,
            f"Anchor {anchor_id} requires {target_ref} and {anchor_ref} on the same row at "
            f"{', '.join(invalid_viewports)}, but {common} stacks their semantic branches there. "
            "Limit viewports to widths where the targets share a row, or change the ancestor layout.",
        )
    ]


def _validate_operations_workspace_profile(
    screen: dict[str, Any],
    *,
    rules: dict[str, dict[str, Any]],
    path: str,
) -> list[dict[str, str]]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    regions = [item for item in view.get("regions", []) if isinstance(item, dict)]
    roles = {
        str(region.get("id")): _infer_region_role(
            str(region.get("role") or ""),
            str(region.get("id") or ""),
            str(region.get("id") or "") == view.get("root_region"),
        )
        for region in regions
        if isinstance(region.get("id"), str)
    }
    sidebar = next((region_id for region_id, role in roles.items() if role == "sidebar"), None)
    main = next((region_id for region_id, role in roles.items() if role == "main"), None)
    parents = {
        str(region.get("id")): str(region.get("parent_region") or "")
        for region in regions
        if isinstance(region.get("id"), str)
    }
    if not sidebar or not main or not parents.get(sidebar) or parents.get(sidebar) != parents.get(main):
        return []
    shell_ref = f"region:{parents[sidebar]}"
    sidebar_ref = f"region:{sidebar}"
    main_ref = f"region:{main}"
    wide_shell = _effective_rule_layout(rules.get(shell_ref), "wide")
    issues: list[dict[str, str]] = []
    if not _layout_places_branches_same_row(
        wide_shell,
        left_branch=sidebar_ref,
        right_branch=main_ref,
        viewport="wide",
        rules=rules,
    ):
        issues.append(
            _issue(
                "APP_PRESENTATION_PROFILE_INVARIANT_INVALID",
                path,
                f"operations_workspace requires sibling {sidebar_ref} and {main_ref} to form a wide rail/content row, "
                f"but {shell_ref} stacks them. Use a two-track wide grid or row flex layout.",
            )
        )
    wide_sidebar = _effective_rule_layout(rules.get(sidebar_ref), "wide")
    if wide_sidebar.get("display") == "none" or wide_sidebar.get("visibility") == "hidden":
        issues.append(
            _issue(
                "APP_PRESENTATION_PROFILE_INVARIANT_INVALID",
                path,
                f"operations_workspace cannot hide its semantic navigation rail {sidebar_ref} at wide. "
                "Keep the rail visible, or choose a profile without a persistent workspace rail.",
            )
        )
    return issues


def _declared_screen_plan(screen: dict[str, Any], presentation: dict[str, Any]) -> dict[str, Any]:
    inferred = _inferred_screen_plan(screen)
    inferred_rules = {
        str(rule.get("target_ref")): rule
        for rule in inferred.get("rules", [])
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    }
    rules: list[dict[str, Any]] = []
    defaulted_targets: list[str] = []
    for rule in presentation.get("rules", []):
        if not isinstance(rule, dict):
            continue
        target_ref = str(rule.get("target_ref") or "")
        fallback = inferred_rules.get(target_ref)
        normalized = _normalize_rule(rule, fallback=fallback)
        rules.append(normalized)
        if fallback is not None and _rule_inherits_defaults(rule, fallback):
            defaulted_targets.append(target_ref)
    declared_targets = {
        str(rule.get("target_ref"))
        for rule in rules
        if isinstance(rule.get("target_ref"), str)
    }
    for target_ref, fallback in inferred_rules.items():
        if target_ref in declared_targets:
            continue
        rules.append(copy.deepcopy(fallback))
        defaulted_targets.append(target_ref)
    _coordinate_declared_shell_sidebar_stickiness(rules, presentation.get("rules", []))
    _prune_orphan_inferred_areas(
        screen,
        rules,
        presentation.get("rules", []),
    )
    diagnostics: list[dict[str, Any]] = []
    diagnostics.extend(_coordinate_direct_motif_surfaces(screen, rules))
    diagnostics.extend(
        _coordinate_grouped_metric_surfaces(rules, presentation.get("rules", []))
    )
    diagnostics.extend(
        _coordinate_dashboard_metric_surfaces(
            screen,
            rules,
            presentation.get("rules", []),
        )
    )
    diagnostics.extend(_bound_semantic_text_overrides(rules))
    if defaulted_targets:
        ordered_targets = sorted(defaulted_targets)
        shown_targets = ordered_targets[:8]
        remainder = len(ordered_targets) - len(shown_targets)
        target_summary = ", ".join(shown_targets)
        if remainder:
            target_summary += f", and {remainder} more"
        diagnostics.append(
            {
                "severity": "info",
                "code": "APP_PRESENTATION_DEFAULTS_APPLIED",
                "message": "Declared PresentationPlan inherited deterministic semantic and responsive defaults "
                f"for {len(ordered_targets)} targets ({target_summary}); explicit base and breakpoint values override them.",
            }
        )
    if not presentation.get("anchors"):
        diagnostics.append(
            {
                "severity": "warning",
                "code": "APP_PRESENTATION_ANCHORS_UNDECLARED",
                "message": "The declared PresentationPlan has no semantic responsive anchors; add anchors for reference-sensitive delivery.",
            }
        )
    return {
        "id": screen["id"],
        "profile": presentation.get("profile", "neutral"),
        "source": "declared",
        "rules": rules,
        "anchors": copy.deepcopy(presentation.get("anchors", [])),
        "style_tokens": _screen_style_tokens(screen),
        "diagnostics": diagnostics,
    }


def _coordinate_direct_motif_surfaces(
    screen: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Put layout on motifs whose semantic bindings are direct DOM children."""
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    direct_kinds = {
        str(item.get("id")): str(item.get("kind"))
        for item in view.get("motifs", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("kind") == "hero"
    }
    normalized: list[str] = []
    for rule in rules:
        target_ref = str(rule.get("target_ref") or "")
        motif_id = target_ref.removeprefix("motif:")
        items = rule.get("items")
        if motif_id not in direct_kinds or not isinstance(items, dict):
            continue
        rule["base"] = _merged_layout(rule.get("base"), items.get("base"))
        rule["variants"] = _merged_variants(
            rule.get("variants"),
            items.get("variants"),
        )
        rule.pop("items", None)
        normalized.append(target_ref)
    if not normalized:
        return []
    return [
        {
            "severity": "info",
            "code": "APP_PRESENTATION_LAYOUT_SURFACE_NORMALIZED",
            "message": "Moved item layout onto direct-child semantic motif surface(s): "
            + ", ".join(sorted(normalized))
            + ".",
        }
    ]


def _coordinate_dashboard_metric_surfaces(
    screen: dict[str, Any],
    rules: list[dict[str, Any]],
    declared_rules: Any,
) -> list[dict[str, Any]]:
    """Move an authored dashboard card grid to its wrapper and keep cards internally stacked."""
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    dashboard_ids = {
        str(motif.get("id"))
        for motif in view.get("motifs", [])
        if isinstance(motif, dict)
        and motif.get("kind") == "dashboard"
        and _infer_effective_motif_role(motif) == "metric_grid"
    }
    declared_by_target = {
        str(rule.get("target_ref")): rule
        for rule in declared_rules
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    } if isinstance(declared_rules, list) else {}
    normalized: list[str] = []
    for rule in rules:
        target_ref = str(rule.get("target_ref") or "")
        if target_ref.removeprefix("motif:") not in dashboard_ids:
            continue
        declared = declared_by_target.get(target_ref)
        declared_items = (
            declared.get("items")
            if isinstance(declared, dict) and isinstance(declared.get("items"), dict)
            else None
        )
        items = rule.get("items")
        if not isinstance(declared_items, dict) or not isinstance(items, dict):
            continue
        declared_item_base = (
            declared_items.get("base")
            if isinstance(declared_items.get("base"), dict)
            else {}
        )
        wrapper_layout = {
            key: declared_item_base[key]
            for key in ("align", "columns", "gap", "justify")
            if key in declared_item_base
        }
        wrapper_layout["display"] = "grid"
        rule["base"] = _merged_layout(rule.get("base"), wrapper_layout)
        rule["base"].pop("areas", None)
        items["base"] = {
            "display": "flex",
            "direction": "column",
            "gap": "xs",
            "padding": "none",
            "width": "full",
        }

        declared_item_variants = (
            declared_items.get("variants")
            if isinstance(declared_items.get("variants"), dict)
            else {}
        )
        wrapper_variants = (
            rule.get("variants") if isinstance(rule.get("variants"), dict) else {}
        )
        for breakpoint, declared_variant in declared_item_variants.items():
            if not isinstance(declared_variant, dict):
                continue
            moved = {
                key: declared_variant[key]
                for key in ("align", "columns", "gap", "justify")
                if key in declared_variant
            }
            if moved:
                wrapper_variants[breakpoint] = _merged_layout(
                    wrapper_variants.get(breakpoint),
                    moved,
                )
        rule["variants"] = wrapper_variants
        items["variants"] = {}
        normalized.append(target_ref)
    if not normalized:
        return []
    return [
        {
            "severity": "info",
            "code": "APP_PRESENTATION_DASHBOARD_GRID_NORMALIZED",
            "message": "Moved dashboard card-grid layout to its semantic wrapper: "
            + ", ".join(sorted(normalized))
            + ".",
        }
    ]


def _coordinate_grouped_metric_surfaces(
    rules: list[dict[str, Any]],
    declared_rules: Any,
) -> list[dict[str, Any]]:
    """Keep a list wrapper full-width while its stable summary item owns the metric grid."""
    layout_keys = {"align", "areas", "columns", "direction", "display", "gap", "justify"}
    declared_by_target = {
        str(rule.get("target_ref")): rule
        for rule in declared_rules
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    } if isinstance(declared_rules, list) else {}
    normalized: list[str] = []
    for rule in rules:
        items = rule.get("items")
        if rule.get("role") != "metric_grid" or not isinstance(items, dict):
            continue
        base = rule.get("base") if isinstance(rule.get("base"), dict) else {}
        item_base = items.get("base") if isinstance(items.get("base"), dict) else {}
        declared = declared_by_target.get(str(rule.get("target_ref") or ""), {})
        declared_base = (
            declared.get("base") if isinstance(declared.get("base"), dict) else {}
        )
        preserves_semantic_areas = isinstance(item_base.get("areas"), list)
        moved_base = {
            key: declared_base[key]
            for key in layout_keys
            if key in declared_base
            and not (preserves_semantic_areas and key in {"areas", "columns"})
        }
        if moved_base:
            item_base = _merged_layout(item_base, moved_base)
        for key in layout_keys:
            base.pop(key, None)
        base["display"] = "block"
        rule["base"] = base
        items["base"] = item_base

        variants = rule.get("variants") if isinstance(rule.get("variants"), dict) else {}
        declared_variants = (
            declared.get("variants")
            if isinstance(declared.get("variants"), dict)
            else {}
        )
        item_variants = (
            items.get("variants") if isinstance(items.get("variants"), dict) else {}
        )
        for breakpoint in PRESENTATION_BREAKPOINTS:
            variant = variants.get(breakpoint)
            declared_variant = declared_variants.get(breakpoint)
            if not isinstance(variant, dict):
                continue
            moved = {
                key: declared_variant[key]
                for key in layout_keys
                if isinstance(declared_variant, dict) and key in declared_variant
                and not (preserves_semantic_areas and key in {"areas", "columns"})
            }
            if moved:
                item_variants[breakpoint] = _merged_layout(
                    item_variants.get(breakpoint),
                    moved,
                )
            for key in layout_keys:
                variant.pop(key, None)
            if not variant:
                variants.pop(breakpoint, None)
        rule["variants"] = variants
        items["variants"] = item_variants
        normalized.append(str(rule.get("target_ref") or ""))
    if not normalized:
        return []
    return [
        {
            "severity": "info",
            "code": "APP_PRESENTATION_GROUP_LAYOUT_NORMALIZED",
            "message": "Moved metric grid layout from list wrapper to its stable semantic item(s): "
            + ", ".join(sorted(normalized))
            + ".",
        }
    ]


def _bound_semantic_text_overrides(
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep structural rail sizing and padding off intrinsic page headings."""
    bounded: list[str] = []
    for rule in rules:
        if rule.get("role") != "page_title":
            continue
        changed: set[str] = set()
        layouts = [rule.get("base")]
        variants = rule.get("variants")
        if isinstance(variants, dict):
            layouts.extend(variants.values())
        for layout in layouts:
            if not isinstance(layout, dict):
                continue
            width = layout.get("width")
            if isinstance(width, str) and width.startswith("rail_"):
                layout["width"] = "intrinsic"
                changed.add("width")
            if layout.get("padding") not in {None, "none"}:
                layout["padding"] = "none"
                changed.add("padding")
        if changed:
            bounded.append(
                f"{rule.get('target_ref')} ({', '.join(sorted(changed))})"
            )
    if not bounded:
        return []
    return [
        {
            "severity": "warning",
            "code": "APP_PRESENTATION_SEMANTIC_OVERRIDE_BOUNDED",
            "message": "Bound destructive structural sizing on semantic page title rule(s): "
            + ", ".join(bounded)
            + ". Use max_width to constrain text without turning the heading into a layout rail.",
        }
    ]


def _prune_orphan_inferred_areas(
    screen: dict[str, Any],
    rules: list[dict[str, Any]],
    declared_rules: Any,
) -> None:
    """Do not retain inferred child areas after a declaration replaces the parent grid."""
    declared_by_target = {
        str(rule.get("target_ref")): rule
        for rule in declared_rules
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    } if isinstance(declared_rules, list) else {}
    rules_by_target = {
        str(rule.get("target_ref")): rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    }
    parents = _screen_target_parents(screen)
    for target_ref, rule in rules_by_target.items():
        base = rule.get("base") if isinstance(rule.get("base"), dict) else {}
        area = base.get("area")
        if not isinstance(area, str) or not area:
            continue
        declared = declared_by_target.get(target_ref)
        declared_base = declared.get("base") if isinstance(declared, dict) and isinstance(declared.get("base"), dict) else {}
        if "area" in declared_base:
            continue
        parent_ref = parents.get(target_ref)
        parent_rule = rules_by_target.get(str(parent_ref))
        if not parent_ref or not isinstance(parent_rule, dict):
            base.pop("area", None)
            continue
        valid_viewports: list[str] = []
        for viewport in PRESENTATION_BREAKPOINTS:
            parent_layout = _effective_rule_layout(
                parent_rule,
                viewport,
                items=bool(
                    parent_ref.startswith("motif:")
                    and isinstance(parent_rule.get("items"), dict)
                ),
            )
            areas = parent_layout.get("areas")
            if isinstance(areas, list) and any(
                isinstance(row, list) and area in row for row in areas
            ):
                valid_viewports.append(viewport)
        if len(valid_viewports) == len(PRESENTATION_BREAKPOINTS):
            continue
        base.pop("area", None)
        variants = rule.get("variants") if isinstance(rule.get("variants"), dict) else {}
        for viewport in valid_viewports:
            variants.setdefault(viewport, {})["area"] = area
        rule["variants"] = variants


def _coordinate_declared_shell_sidebar_stickiness(
    rules: list[dict[str, Any]],
    declared_rules: Any,
) -> None:
    """Keep an inherited sticky rail from covering a declared stacked shell."""
    shell = next((rule for rule in rules if rule.get("role") == "app_shell"), None)
    sidebar = next((rule for rule in rules if rule.get("role") == "sidebar"), None)
    if not isinstance(shell, dict) or not isinstance(sidebar, dict):
        return
    declared_by_target = {
        str(rule.get("target_ref")): rule
        for rule in declared_rules
        if isinstance(rule, dict) and isinstance(rule.get("target_ref"), str)
    } if isinstance(declared_rules, list) else {}
    declared_sidebar = declared_by_target.get(str(sidebar.get("target_ref")), {})
    shell_base = shell.get("base") if isinstance(shell.get("base"), dict) else {}
    sidebar_base = sidebar.get("base") if isinstance(sidebar.get("base"), dict) else {}
    declared_sidebar_base = (
        declared_sidebar.get("base")
        if isinstance(declared_sidebar.get("base"), dict)
        else {}
    )

    def desired_sticky(props: dict[str, Any]) -> bool | None:
        display = props.get("display")
        if display == "grid":
            areas = props.get("areas")
            if isinstance(areas, list) and areas and all(
                isinstance(row, list) and len(row) == 1 for row in areas
            ):
                return False
            columns = props.get("columns")
            if columns == 1 or (isinstance(columns, list) and len(columns) == 1):
                return False
            return True
        if display == "block" or (
            display == "flex" and props.get("direction", "row") == "column"
        ):
            return False
        return None

    base_sticky = desired_sticky(shell_base)
    if base_sticky is not None and "sticky" not in declared_sidebar_base:
        sidebar_base["sticky"] = base_sticky
        sidebar["base"] = sidebar_base

    shell_variants = shell.get("variants") if isinstance(shell.get("variants"), dict) else {}
    sidebar_variants = (
        sidebar.get("variants") if isinstance(sidebar.get("variants"), dict) else {}
    )
    declared_sidebar_variants = (
        declared_sidebar.get("variants")
        if isinstance(declared_sidebar.get("variants"), dict)
        else {}
    )
    for breakpoint in PRESENTATION_BREAKPOINTS:
        shell_variant = (
            shell_variants.get(breakpoint)
            if isinstance(shell_variants.get(breakpoint), dict)
            else {}
        )
        effective_shell = {**shell_base, **shell_variant}
        sticky = desired_sticky(effective_shell)
        explicit_sidebar_variant = (
            declared_sidebar_variants.get(breakpoint)
            if isinstance(declared_sidebar_variants.get(breakpoint), dict)
            else {}
        )
        if sticky is None or "sticky" in explicit_sidebar_variant:
            continue
        sidebar_variants.setdefault(breakpoint, {})["sticky"] = sticky
    sidebar["variants"] = sidebar_variants


def _inferred_screen_plan(screen: dict[str, Any]) -> dict[str, Any]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    regions = [item for item in view.get("regions", []) if isinstance(item, dict)]
    rules: list[dict[str, Any]] = []
    roles: dict[str, str] = {}
    motif_roles_by_region: dict[str, set[str]] = {}
    motif_role_by_binding: dict[str, str] = {}
    inferred_binding_areas: dict[str, str] = {}
    for motif in view.get("motifs", []):
        if not isinstance(motif, dict):
            continue
        region_id = motif.get("region")
        motif_id = motif.get("id")
        if isinstance(region_id, str) and isinstance(motif_id, str):
            motif_role = _infer_effective_motif_role(motif)
            motif_roles_by_region.setdefault(region_id, set()).add(motif_role)
            for member in motif.get("members", []):
                if isinstance(member, str):
                    motif_role_by_binding.setdefault(member, motif_role)
    for region in regions:
        region_id = str(region.get("id") or "")
        if not region_id:
            continue
        role = _infer_region_role(str(region.get("role") or ""), region_id, region_id == view.get("root_region"))
        roles[region_id] = role
    sidebar = next((str(item.get("id")) for item in regions if roles.get(str(item.get("id"))) == "sidebar"), None)
    main = next((str(item.get("id")) for item in regions if roles.get(str(item.get("id"))) == "main"), None)
    parents = {str(item.get("id")): str(item.get("parent_region") or "") for item in regions}
    shell_grid_target = (
        parents[sidebar]
        if sidebar and main and parents.get(sidebar) and parents.get(sidebar) == parents.get(main)
        else None
    )
    for region in regions:
        region_id = str(region.get("id") or "")
        if not region_id:
            continue
        role = roles[region_id]
        base, variants = _inferred_layout_for_role(role)
        if role == "metric_grid" and "metric_grid" in motif_roles_by_region.get(region_id, set()):
            role = "section"
            base, variants = _inferred_layout_for_role(role)
        if role == "sidebar" and shell_grid_target is None:
            base = {
                "display": "flex",
                "direction": "column",
                "gap": "md",
                "order": -1,
                "padding": "md",
                "sticky": False,
            }
            variants = {}
        if region_id == shell_grid_target and sidebar and main:
            base = {
                "display": "grid",
                "columns": ["rail_lg", "fluid"],
                "areas": [[sidebar, main]],
                "gap": "none",
                "padding": "none",
            }
            variants = {
                "compact": {"columns": 1, "areas": [[sidebar], [main]]},
                "medium": {"columns": 1, "areas": [[sidebar], [main]]},
                "wide": {"columns": ["rail_lg", "fluid"], "areas": [[sidebar, main]]},
            }
        rules.append(
            {
                "id": f"inferred_{region_id}",
                "target_ref": f"region:{region_id}",
                "role": role,
                "base": base,
                "variants": variants,
            }
        )
    for motif in view.get("motifs", []):
        if not isinstance(motif, dict) or not isinstance(motif.get("id"), str):
            continue
        motif_id = motif["id"]
        motif_kind = str(motif.get("kind") or "")
        role = _infer_effective_motif_role(motif)
        members = [item for item in motif.get("members", []) if isinstance(item, str)]
        resource_areas = _job_field_areas(members)
        base, variants = _inferred_layout_for_role(role)
        if role in {"attention_job_row", "job_row"}:
            inferred_binding_areas.update(resource_areas)
        rule: dict[str, Any] = {
            "id": f"inferred_{motif_id}",
            "target_ref": f"motif:{motif_id}",
            "role": role,
            "base": base,
            "variants": variants,
        }
        if role in {
            "attention_job_row",
            "job_row",
            "navigation",
            "workload_row",
        }:
            rule["base"] = {
                "background": "transparent",
                "border": "none",
                "display": "block",
                "min_inline_size": "zero",
                "padding": "none",
                "radius": "none",
            }
            rule["variants"] = {}
            rule["items"] = {"base": base, "variants": variants}
        elif role == "page_header":
            item_layout, areas = _header_item_layout(members)
            surface = {
                "background": "transparent",
                "border": "none",
                "order": -30,
                "padding": "none",
            }
            rule["variants"] = {}
            header_variants = {
                "compact": {"gap": "sm"},
                "medium": {"gap": "md"},
            }
            if motif_kind == "hero":
                # Hero bindings are direct children of the semantic header surface.
                # Put the header grid on that surface so areas and intrinsic title
                # sizing affect the actual eyebrow/title/avatar nodes.
                rule["base"] = {**surface, **item_layout}
                rule["variants"] = header_variants
            else:
                # Detail motifs wrap their bindings in stable item rows, so their
                # shared header geometry belongs on those direct items.
                rule["base"] = {**surface, "display": "block"}
                rule["items"] = {
                    "base": item_layout,
                    "variants": header_variants,
                }
            inferred_binding_areas.update(areas)
        elif role == "metric_grid" and members:
            item_layout, areas = _metric_item_layout(members)
            surface = {
                "background": "transparent",
                "border": "none",
                "order": -20,
                "padding": "none",
            }
            if motif_kind == "dashboard":
                # Dashboard motifs render one stable child surface per metric
                # resource. The motif owns the card grid; each card stacks its
                # value and label. Binding areas cannot span those child surfaces.
                dashboard_base, dashboard_variants = _inferred_layout_for_role(
                    "metric_grid"
                )
                rule["base"] = {**surface, **dashboard_base}
                rule["variants"] = dashboard_variants
                rule["items"] = {
                    "base": {
                        "display": "flex",
                        "direction": "column",
                        "gap": "xs",
                        "padding": "none",
                        "width": "full",
                    },
                    "variants": {},
                }
            else:
                rule["base"] = {**surface, "display": "block"}
                rule["variants"] = {}
                rule["items"] = {
                    "base": item_layout,
                    "variants": {"compact": {"gap": "xs"}},
                }
                inferred_binding_areas.update(areas)
        elif role == "workload_summary" and members:
            item_layout, areas = _metric_item_layout(members)
            rule["base"] = {
                "background": "transparent",
                "border": "none",
                "display": "block",
                "min_inline_size": "zero",
                "padding": "none",
                "width": "full",
            }
            rule["variants"] = {}
            rule["items"] = {
                "base": item_layout,
                "variants": {
                    "compact": {
                        "columns": 1,
                        "gap": "sm",
                        "padding": "md",
                    }
                },
            }
            inferred_binding_areas.update(areas)
        rules.append(rule)
    for binding in view.get("bindings", []):
        if not isinstance(binding, dict) or not isinstance(binding.get("id"), str):
            continue
        binding_id = binding["id"]
        parent_region = str(binding.get("target_region") or "")
        parent_role = roles.get(parent_region, "")
        motif_role = motif_role_by_binding.get(binding_id, "")
        role, base, variants = _inferred_binding_layout(
            binding_id,
            parent_role=parent_role,
            motif_role=motif_role,
        )
        area = inferred_binding_areas.get(binding_id)
        if area is not None:
            base["area"] = area
        if base or variants:
            rules.append(
                {
                    "id": f"inferred_text_{binding_id}",
                    "target_ref": f"binding:{binding_id}",
                    "role": role,
                    "base": base,
                    "variants": variants,
                }
            )
    profile = "operations_workspace" if sidebar else "editorial_dashboard"
    return {
        "id": screen["id"],
        "profile": profile,
        "source": "inferred",
        "rules": rules,
        "anchors": [],
        "style_tokens": _screen_style_tokens(screen),
        "diagnostics": [
            {
                "severity": "warning",
                "code": "APP_PRESENTATION_INFERRED",
                "message": "Complex screen uses a deterministic inferred PresentationPlan; declare presentation rules and anchors for reference-sensitive work.",
            }
        ],
    }


def _normalized_layout(layout: Any) -> dict[str, Any]:
    normalized = copy.deepcopy(layout) if isinstance(layout, dict) else {}
    if "display" not in normalized:
        if "columns" in normalized or "areas" in normalized:
            normalized["display"] = "grid"
        elif "direction" in normalized:
            normalized["display"] = "flex"
    return normalized


def _merged_layout(fallback: Any, declared: Any) -> dict[str, Any]:
    merged = _normalized_layout(fallback)
    declared_layout = _normalized_layout(declared)
    declared_display = declared_layout.get("display")
    if declared_display in {"block", "flex"}:
        merged.pop("areas", None)
        merged.pop("columns", None)
    elif declared_display == "grid":
        merged.pop("direction", None)
    if "columns" in declared_layout and "areas" not in declared_layout:
        areas = merged.get("areas")
        columns = declared_layout.get("columns")
        column_count = columns if isinstance(columns, int) else len(columns) if isinstance(columns, list) else None
        if (
            isinstance(areas, list)
            and isinstance(column_count, int)
            and any(isinstance(row, list) and len(row) != column_count for row in areas)
        ):
            merged.pop("areas", None)
    merged.update(declared_layout)
    return _normalized_layout(merged)


def _merged_variants(fallback: Any, declared: Any) -> dict[str, dict[str, Any]]:
    fallback_map = fallback if isinstance(fallback, dict) else {}
    declared_map = declared if isinstance(declared, dict) else {}
    return {
        breakpoint: _merged_layout(
            fallback_map.get(breakpoint, {}),
            declared_map.get(breakpoint, {}),
        )
        for breakpoint in PRESENTATION_BREAKPOINTS
        if fallback_map.get(breakpoint) or declared_map.get(breakpoint)
    }


def _normalize_rule(
    rule: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or {}
    normalized = {
        "id": rule.get("id"),
        "target_ref": rule.get("target_ref"),
        "role": fallback.get("role", "declared"),
        "base": _merged_layout(fallback.get("base"), rule.get("base")),
        "variants": _merged_variants(fallback.get("variants"), rule.get("variants")),
    }
    fallback_items = fallback.get("items") if isinstance(fallback.get("items"), dict) else {}
    declared_items = rule.get("items") if isinstance(rule.get("items"), dict) else {}
    if fallback_items or declared_items:
        normalized["items"] = {
            "base": _merged_layout(fallback_items.get("base"), declared_items.get("base")),
            "variants": _merged_variants(
                fallback_items.get("variants"),
                declared_items.get("variants"),
            ),
        }
    return normalized


def _rule_inherits_defaults(rule: dict[str, Any], fallback: dict[str, Any]) -> bool:
    for key in ("base", "variants", "items"):
        declared = rule.get(key)
        inherited = fallback.get(key)
        if isinstance(inherited, dict) and inherited and declared != inherited:
            return True
    return False


def _infer_region_role(role: str, region_id: str, is_root: bool) -> str:
    markers = set(re.split(r"[^a-z0-9]+", f"{role} {region_id}".lower()))
    if is_root:
        return "app_shell"
    if markers & {"sidebar", "navigation", "nav", "rail"}:
        return "sidebar"
    if markers & {"main", "workspace", "content"}:
        return "main"
    if markers & {"header", "heading", "title"}:
        return "page_header"
    if markers & {"summary", "metric", "metrics", "stats", "kpi"}:
        return "metric_grid"
    if markers & {"attention", "risk", "alert"}:
        return "attention"
    if markers & {"queue", "jobs", "workload", "list"}:
        return "queue"
    if markers & {"actions", "action", "controls"}:
        return "action_row"
    return "section"


def _infer_motif_role(kind: str, motif_id: str) -> str:
    markers = set(re.split(r"[^a-z0-9]+", f"{kind} {motif_id}".lower()))
    if markers & {"nav", "navigation", "rail", "sidebar"}:
        return "navigation"
    if "brand" in markers:
        return "brand"
    if markers & {"heading"}:
        return "section_heading"
    if (
        kind == "hero"
        or markers & {"header", "topline"}
        or {"page", "hero"} <= markers
    ):
        return "page_header"
    if "workload" in markers and kind == "dashboard":
        return "workload_summary"
    if "workload" in markers and (
        markers & {"row", "rows"} or kind in {"list", "table"}
    ):
        return "workload_row"
    if "dashboard" in markers or markers & {"metric", "metrics", "summary", "workload"}:
        return "metric_grid"
    if any(marker.startswith("job") for marker in markers):
        return "job_row"
    if kind == "list":
        return "collection"
    if kind == "detail":
        return "detail"
    return "surface"


def _infer_effective_motif_role(motif: dict[str, Any]) -> str:
    motif_id = str(motif.get("id") or "")
    role = _infer_motif_role(str(motif.get("kind") or ""), motif_id)
    members = [item for item in motif.get("members", []) if isinstance(item, str)]
    resource_areas = _job_field_areas(members)
    markers = set(re.split(r"[^a-z0-9]+", motif_id.lower()))
    if role == "page_header" and markers & {"attention", "queue", "workload"}:
        return "lane_header"
    if any(item.startswith("rvb_") for item in members) and {
        "record_title",
        "record_status",
    } & set(resource_areas.values()):
        return (
            "attention_job_row"
            if markers & {"attention", "risk", "urgent"}
            else "job_row"
        )
    if role != "job_row" and members and markers & {"lane", "queue"}:
        return "lane_header"
    return role


def _inferred_layout_for_role(role: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if role == "app_shell":
        return ({"display": "block", "min_inline_size": "zero", "padding": "none"}, {})
    if role == "sidebar":
        return (
            {"display": "flex", "direction": "column", "gap": "md", "padding": "xl", "sticky": True},
            {
                "compact": {
                    "align": "center",
                    "direction": "row",
                    "gap": "sm",
                    "sticky": False,
                    "padding": "lg",
                },
                "medium": {
                    "align": "center",
                    "direction": "row",
                    "gap": "sm",
                    "sticky": False,
                    "padding": "lg",
                },
            },
        )
    if role == "main":
        return (
            {
                "display": "flex",
                "direction": "column",
                "gap": "xl",
                "padding": "xl",
                "min_inline_size": "zero",
            },
            {
                "compact": {"padding": "lg"},
                "medium": {"padding": "2xl"},
            },
        )
    if role == "metric_grid":
        return ({"display": "grid", "columns": 3, "gap": "md", "order": -20}, {"compact": {"columns": 3, "gap": "sm"}})
    if role == "navigation":
        return (
            {"display": "flex", "direction": "column", "gap": "sm", "padding": "none"},
            {
                "compact": {"direction": "row", "gap": "sm"},
                "medium": {"direction": "row", "gap": "sm"},
            },
        )
    if role == "brand":
        return (
            {
                "align": "center",
                "display": "flex",
                "direction": "row",
                "gap": "sm",
                "padding": "none",
                "width": "full",
            },
            {},
        )
    if role in {"attention_job_row", "job_row"}:
        return (
            {
                "display": "grid",
                "columns": ["identity_lg", "fluid_wide", "fluid", "auto"],
                "areas": [
                    ["record_id", "record_title", "record_technician", "record_status"],
                    ["record_id", "record_location", "record_technician_role", "record_status"],
                    ["record_id", "record_time", "record_technician_role", "record_status"],
                ],
                "gap": "sm",
                "padding": "lg",
                "min_inline_size": "zero",
            },
            {
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
            },
        )
    if role == "workload_row":
        return (
            {
                "display": "grid",
                "columns": ["fluid", "auto"],
                "gap": "sm",
                "padding": "md",
                "min_inline_size": "zero",
                "width": "full",
            },
            {
                "compact": {
                    "columns": ["fluid", "auto"],
                    "gap": "sm",
                    "padding": "sm",
                    "min_inline_size": "zero",
                    "width": "full",
                }
            },
        )
    if role == "workload_summary":
        return ({"display": "block", "min_inline_size": "zero", "width": "full"}, {})
    if role == "lane_header":
        return (
            {
                "display": "flex",
                "direction": "row",
                "gap": "sm",
                "justify": "between",
                "padding": "none",
                "width": "full",
            },
            {},
        )
    if role == "section_heading":
        return (
            {
                "align": "center",
                "display": "flex",
                "direction": "row",
                "gap": "sm",
                "justify": "between",
                "padding": "none",
                "width": "full",
            },
            {},
        )
    if role == "action_row":
        return (
            {
                "display": "flex",
                "direction": "row",
                "gap": "sm",
                "justify": "start",
                "width": "full",
            },
            {"compact": {"justify": "stretch"}},
        )
    return ({"min_inline_size": "zero"}, {})


def _header_item_layout(members: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    areas: dict[str, str] = {}
    eyebrow = next(
        (item for item in members if re.search(r"(?:^|_)(?:eyebrow|date)(?:_|$)", item.lower())),
        None,
    )
    title = next(
        (item for item in members if re.search(r"(?:^|_)(?:title|heading)(?:_|$)", item.lower())),
        None,
    )
    avatar = next(
        (
            item
            for item in members
            if re.search(r"(?:^|_)(?:avatar|initials|operator)(?:_|$)", item.lower())
        ),
        None,
    )
    if eyebrow and title and avatar:
        areas.update(
            {
                eyebrow: "header_eyebrow",
                title: "header_title",
                avatar: "header_avatar",
            }
        )
        return (
            {
                "align": "end",
                "areas": [
                    ["header_eyebrow", "header_avatar"],
                    ["header_title", "header_avatar"],
                ],
                "columns": ["fluid", "auto"],
                "display": "grid",
                "gap": "sm",
                "padding": "none",
                "width": "full",
            },
            areas,
        )
    if eyebrow and title:
        areas.update(
            {
                eyebrow: "header_eyebrow",
                title: "header_title",
            }
        )
        return (
            {
                "align": "start",
                "areas": [["header_eyebrow"], ["header_title"]],
                "columns": 1,
                "display": "grid",
                "gap": "xs",
                "padding": "none",
                "width": "full",
            },
            areas,
        )
    return (
        {
            "align": "end",
            "columns": ["fluid", "auto"],
            "display": "grid",
            "gap": "sm",
            "padding": "none",
            "width": "full",
        },
        areas,
    )


def _job_field_areas(members: list[str]) -> dict[str, str]:
    fields = (
        ("technician_role", "record_technician_role"),
        ("scheduled_time", "record_time"),
        ("location_time", "record_location"),
        ("assignee", "record_technician"),
        ("technician", "record_technician"),
        ("role", "record_technician_role"),
        ("time", "record_time"),
        ("location", "record_location"),
        ("status", "record_status"),
        ("title", "record_title"),
        ("id", "record_id"),
    )
    areas: dict[str, str] = {}
    for member in members:
        lowered = member.lower()
        for field, area in fields:
            if lowered.endswith((f"__{field}", f"_{field}")):
                areas[member] = area
                break
    return areas


def _metric_item_layout(members: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    clean = members[:4]
    areas: dict[str, str] = {}
    layout: list[list[str]] = []
    if len(members) >= 4 and len(members) % 2 == 0:
        pairs = [members[index : index + 2] for index in range(0, len(members), 2)]
        if len(pairs) <= PRESENTATION_MAX_GRID_TRACKS and all(
            re.search(r"(?:^|_)(?:value|count|number|percent|rate)(?:_|$)", pair[0].lower())
            and "label" in pair[1].lower()
            for pair in pairs
        ):
            value_areas = [f"metric_{index}_value" for index in range(len(pairs))]
            label_areas = [f"metric_{index}_label" for index in range(len(pairs))]
            layout = [value_areas, label_areas]
            for index, pair in enumerate(pairs):
                areas[pair[0]] = value_areas[index]
                areas[pair[1]] = label_areas[index]
            clean = [pair[0] for pair in pairs]
    if not layout and len(members) >= 2 and re.search(
        r"(?:^|_)(?:heading|title|label)(?:_|$)",
        members[0].lower(),
    ):
        values = members[1 : 1 + PRESENTATION_MAX_GRID_TRACKS]
        if values:
            heading_area = "metric_heading"
            value_areas = [f"metric_{index}" for index in range(len(values))]
            layout = [[heading_area] * len(values), value_areas]
            areas[members[0]] = heading_area
            areas.update(dict(zip(values, value_areas, strict=True)))
            clean = values
    columns = max(1, min(PRESENTATION_MAX_GRID_TRACKS, len(clean)))
    base: dict[str, Any] = {
        "align": "stretch",
        "columns": columns,
        "display": "grid",
        "gap": "sm",
        "padding": "none",
        "width": "full",
    }
    if layout:
        base["areas"] = layout
    return base, areas


def _inferred_binding_layout(
    binding_id: str,
    *,
    parent_role: str,
    motif_role: str,
) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    lowered = binding_id.lower()
    field = (
        lowered.rsplit("__", 1)[-1]
        if "__" in lowered
        else lowered.rsplit("_", 1)[-1]
    )
    if (parent_role == "page_header" or motif_role == "page_header") and re.search(
        r"(?:^|_)(?:title|heading)(?:_|$)", lowered
    ):
        return (
            "page_title",
            {
                "font_family": "serif",
                "font_size": "display_lg",
                "font_weight": "medium",
                "foreground": "ink",
                "letter_spacing": "tight",
                "line_height": "tight",
                "min_inline_size": "zero",
                "text_wrap": "normal",
                "width": "intrinsic",
                "max_width": "full",
            },
            {
                "compact": {"font_size": "display_sm"},
                "medium": {"font_size": "display_sm"},
            },
        )
    if (parent_role == "page_header" or motif_role == "page_header") and re.search(
        r"(?:^|_)(?:eyebrow|date)(?:_|$)", lowered
    ):
        return (
            "eyebrow",
            {
                "font_size": "caption",
                "font_weight": "bold",
                "foreground": "muted",
                "letter_spacing": "wide",
                "text_transform": "uppercase",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field == "id":
        return (
            "record_id",
            {
                "font_family": "mono",
                "font_size": "small",
                "font_weight": "bold",
                "foreground": "ink",
                "min_inline_size": "content",
                "text_wrap": "no_wrap",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field == "title":
        return (
            "natural_wrap_text",
            {
                "font_size": "body",
                "font_weight": "semibold",
                "line_height": "snug",
                "min_inline_size": "zero",
                "text_wrap": "normal",
                "width": "full",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field in {
        "location",
        "location_time",
        "scheduled_time",
        "time",
    }:
        return (
            "record_metadata",
            {
                "font_size": "small",
                "font_weight": "regular",
                "foreground": "muted",
                "line_height": "snug",
                "min_inline_size": "zero",
                "text_wrap": "normal",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field in {
        "assignee",
        "technician",
    }:
        return (
            "record_technician",
            {
                "font_size": "small",
                "font_weight": "semibold",
                "foreground": "ink",
                "line_height": "snug",
                "min_inline_size": "zero",
                "text_wrap": "normal",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field in {
        "role",
        "technician_role",
    }:
        return (
            "record_technician_role",
            {
                "font_size": "small",
                "font_weight": "regular",
                "foreground": "muted",
                "line_height": "snug",
                "min_inline_size": "zero",
                "text_wrap": "normal",
            },
            {},
        )
    if motif_role in {"attention_job_row", "job_row"} and field == "status":
        return (
            "record_status",
            {
                "font_size": "caption",
                "font_weight": "bold",
                "line_height": "snug",
                "min_inline_size": "content",
                "text_wrap": "no_wrap",
            },
            {},
        )
    if re.search(r"(?:^|_)(?:attention|queue|workload).*(?:heading|title)(?:_|$)", lowered):
        return (
            "section_heading",
            {
                "font_size": "small",
                "font_weight": "bold",
                "letter_spacing": "wide",
                "line_height": "normal",
                "text_transform": "uppercase",
            },
            {},
        )
    if lowered.endswith(("_title", "title")):
        return (
            "natural_wrap_text",
            {"min_inline_size": "zero", "text_wrap": "normal"},
            {},
        )
    return "binding", {}, {}


def _screen_plan_css(screen: dict[str, Any]) -> list[str]:
    screen_id = str(screen.get("id") or "")
    scope = f'[data-viewspec-app-screen="{screen_id}"]'
    profile = str(screen.get("profile") or "neutral")
    lines = [
        f"{scope} {{ min-width: 0; background: #f4f6f4; color: #17201c; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}",
        f"{scope} [data-ir-id] {{ box-sizing: border-box; min-width: 0; }}",
        f'{scope} [data-ir-id^="planner_region_"][data-ir-id$="_actions"] '
        "{ display: flex; flex-flow: row wrap; justify-content: flex-start; "
        "gap: 0.5rem; width: 100%; padding: 0; }",
    ]
    if profile == "operations_workspace":
        lines.extend(
            [
                f'{scope} [data-ir-id][data-intent-refs*="region:sidebar"], {scope} [data-ir-id][data-intent-refs*="region:navigation"] {{ background: #18231f; color: #f4f7f5; }}',
                f'{scope} [data-ir-id][data-intent-refs*="region:sidebar"] *, {scope} [data-ir-id][data-intent-refs*="region:navigation"] * {{ color: inherit; }}',
            ]
        )
    style_tokens = screen.get("style_tokens")
    if isinstance(style_tokens, dict):
        for token, css in sorted(style_tokens.items()):
            if not isinstance(token, str) or not isinstance(css, str):
                continue
            declarations = _important_declarations(css)
            if declarations:
                lines.append(
                    f'{scope} [data-style-tokens*="{_css_attr_token(token)}"] '
                    f"{{ {'; '.join(declarations)}; }}"
                )
    for rule in screen.get("rules", []):
        if not isinstance(rule, dict):
            continue
        selector = f'{scope} [data-ir-id="{_target_ir_id(str(rule.get("target_ref") or ""))}"]'
        lines.extend(_rule_css(selector, rule.get("base", {}), scope=scope))
        variants = rule.get("variants") if isinstance(rule.get("variants"), dict) else {}
        for breakpoint in PRESENTATION_BREAKPOINTS:
            props = variants.get(breakpoint)
            if not isinstance(props, dict) or not props:
                continue
            body = _rule_css(selector, props, scope=scope)
            if body:
                lines.append(_media_wrap(breakpoint, body))
        items = rule.get("items") if isinstance(rule.get("items"), dict) else {}
        if items:
            lines.extend(
                [
                    f"{selector} {{ width: 100%; min-width: 0; }}",
                    f"{selector} > :is(tbody, thead, tfoot) "
                    "{ display: grid; gap: 0.625rem; width: 100%; min-width: 0; }",
                ]
            )
        item_selector = (
            f"{selector} > [data-ir-id], "
            f"{selector} > :is(tbody, thead, tfoot) > [data-ir-id]"
        )
        lines.extend(_rule_css(item_selector, items.get("base", {}), scope=scope))
        lines.extend(_role_css(item_selector if items else selector, str(rule.get("role") or ""), profile))
        item_variants = items.get("variants") if isinstance(items.get("variants"), dict) else {}
        for breakpoint in PRESENTATION_BREAKPOINTS:
            props = item_variants.get(breakpoint)
            if not isinstance(props, dict) or not props:
                continue
            body = _rule_css(item_selector, props, scope=scope)
            if body:
                lines.append(_media_wrap(breakpoint, body))
    return lines


def _rule_css(selector: str, props: Any, *, scope: str) -> list[str]:
    if not isinstance(props, dict) or not props:
        return []
    declarations: list[str] = []
    display = props.get("display")
    if display in DISPLAY_VALUES:
        declarations.append(f"display: {display} !important")
    direction = props.get("direction")
    if direction in DIRECTION_VALUES:
        declarations.append(f"flex-direction: {direction}")
    columns = props.get("columns")
    if isinstance(columns, int) and not isinstance(columns, bool):
        declarations.append(f"grid-template-columns: repeat({columns}, minmax(0, 1fr)) !important")
    elif isinstance(columns, list):
        declarations.append(f"grid-template-columns: {' '.join(_TRACK_CSS[item] for item in columns)} !important")
    areas = props.get("areas")
    child_lines: list[str] = []
    if isinstance(areas, list) and areas:
        area_rows = " ".join('"' + " ".join(row) + '"' for row in areas)
        declarations.append(f"grid-template-areas: {area_rows}")
        for name in dict.fromkeys(item for row in areas for item in row if item != "."):
            child_selector = ", ".join(
                f'{part.strip()} > [data-ir-id="region_{name}"]'
                for part in _split_css_selector_list(selector)
            )
            child_lines.append(f"{child_selector} {{ grid-area: {name}; }}")
    area = props.get("area")
    if isinstance(area, str) and area:
        declarations.append(f"grid-area: {area} !important")
    if props.get("gap") in _SPACING_CSS:
        declarations.append(f"gap: {_SPACING_CSS[props['gap']]} !important")
    if props.get("padding") in _SPACING_CSS:
        declarations.append(f"padding: {_SPACING_CSS[props['padding']]} !important")
    if props.get("font_family") in _FONT_FAMILY_CSS:
        declarations.append(f"font-family: {_FONT_FAMILY_CSS[props['font_family']]} !important")
    if props.get("font_size") in _FONT_SIZE_CSS:
        declarations.append(f"font-size: {_FONT_SIZE_CSS[props['font_size']]} !important")
    if props.get("font_weight") in _FONT_WEIGHT_CSS:
        declarations.append(f"font-weight: {_FONT_WEIGHT_CSS[props['font_weight']]} !important")
    if props.get("line_height") in _LINE_HEIGHT_CSS:
        declarations.append(f"line-height: {_LINE_HEIGHT_CSS[props['line_height']]} !important")
    if props.get("letter_spacing") in _LETTER_SPACING_CSS:
        declarations.append(f"letter-spacing: {_LETTER_SPACING_CSS[props['letter_spacing']]} !important")
    if props.get("text_transform") in TEXT_TRANSFORM_VALUES:
        declarations.append(f"text-transform: {props['text_transform']} !important")
    if props.get("foreground") in _FOREGROUND_CSS:
        declarations.append(f"color: {_FOREGROUND_CSS[props['foreground']]} !important")
    if props.get("background") in _BACKGROUND_CSS:
        declarations.append(f"background: {_BACKGROUND_CSS[props['background']]} !important")
    if props.get("border") in _BORDER_CSS:
        declarations.append(f"border: {_BORDER_CSS[props['border']]} !important")
    if props.get("radius") in _RADIUS_CSS:
        declarations.append(f"border-radius: {_RADIUS_CSS[props['radius']]} !important")
    if props.get("width") in _SIZE_CSS:
        declarations.append(f"width: {_SIZE_CSS[props['width']]} !important")
    if props.get("max_width") in _SIZE_CSS:
        declarations.append(f"max-width: {_SIZE_CSS[props['max_width']]} !important")
    if isinstance(props.get("order"), int):
        declarations.append(f"order: {props['order']}")
    if isinstance(props.get("span"), int):
        declarations.append(f"grid-column: span {props['span']} / span {props['span']} !important")
    if props.get("visibility") == "hidden":
        declarations.append("display: none !important")
    if props.get("align") in ALIGN_VALUES:
        declarations.append(f"align-items: {props['align']}")
    justify = props.get("justify")
    if justify in JUSTIFY_VALUES:
        declarations.append(f"justify-content: {'space-between' if justify == 'between' else justify}")
    if props.get("sticky") is True:
        declarations.extend(("position: sticky", "top: 0", "align-self: start", "min-height: 100vh"))
    elif props.get("sticky") is False:
        declarations.extend(("position: relative", "top: auto", "min-height: auto"))
    if props.get("text_wrap") == "normal":
        declarations.extend(("white-space: normal", "overflow-wrap: normal", "word-break: normal"))
    elif props.get("text_wrap") == "anywhere":
        declarations.append("overflow-wrap: anywhere")
    elif props.get("text_wrap") == "no_wrap":
        declarations.extend(("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"))
    if props.get("min_inline_size") == "zero":
        declarations.append("min-inline-size: 0")
    elif props.get("min_inline_size") == "content":
        declarations.append("min-inline-size: min-content")
    if isinstance(props.get("max_lines"), int):
        declarations.extend(("display: -webkit-box", "-webkit-box-orient: vertical", f"-webkit-line-clamp: {props['max_lines']}", "overflow: hidden"))
    own = [f"{selector} {{ {'; '.join(declarations)}; }}"] if declarations else []
    return [*own, *child_lines]


def _role_css(selector: str, role: str, profile: str) -> list[str]:
    if role == "sidebar":
        mark = _child_selector(selector, '[data-binding-id="brand_mark"]')
        name = _child_selector(
            selector,
            ':is([data-binding-id="brand_name"], [data-binding-id="brand_title"])',
        )
        navigation = _child_selector(selector, '[data-binding-id^="nav_"]')
        active = _child_selector(selector, '[data-binding-id="nav_dispatch"]')
        return [
            f"{selector} {{ flex-wrap: wrap; border-radius: 0 !important; box-shadow: none !important; background: #18231f !important; color: #f4f7f5 !important; }}",
            f"{mark} {{ display: inline-grid; flex: 0 0 auto; width: 1.75rem !important; height: 1.75rem; "
            "place-items: center; border-radius: 0.4rem; background: #bdff42 !important; color: #18231f !important; "
            "font-size: 0.85rem !important; font-weight: 800 !important; line-height: 1 !important; }",
            f"{name} {{ margin: 0 !important; color: inherit !important; font-size: 0.95rem !important; "
            "font-weight: 700 !important; letter-spacing: 0 !important; }",
            f"{navigation} {{ margin: 0 !important; color: inherit !important; font-size: 0.85rem !important; "
            "font-weight: 500 !important; letter-spacing: 0 !important; line-height: 1.2 !important; }",
            f"{active} {{ border-radius: 0.55rem; background: rgb(255 255 255 / 0.12) !important; "
            "padding: 0.65rem 0.75rem !important; font-weight: 700 !important; }",
        ]
    if role == "main":
        return [f"{selector} {{ width: 100%; max-width: 76rem; margin-inline: auto; background: #f4f6f4 !important; }}"]
    if role == "page_header":
        return [f"{selector} {{ border: 0 !important; box-shadow: none !important; background: transparent !important; }}"]
    if role == "page_title":
        return [f"{selector} {{ margin: 0 !important; }}"]
    if role == "brand":
        mark = _child_selector(selector, '[data-binding-id="brand_mark"]')
        name = _child_selector(selector, '[data-binding-id="brand_name"]')
        return [
            f"{selector} {{ flex-wrap: wrap; border: 0 !important; border-radius: 0 !important; "
            "background: transparent !important; box-shadow: none !important; color: inherit !important; }",
            f"{mark} {{ display: inline-grid; width: 1.75rem; height: 1.75rem; place-items: center; "
            "border-radius: 0.4rem; background: #bdff42 !important; color: #18231f !important; "
            "font-size: 0.85rem !important; font-weight: 800 !important; line-height: 1 !important; }",
            f"{name} {{ margin: 0 !important; color: inherit !important; font-size: 0.95rem !important; "
            "font-weight: 700 !important; letter-spacing: 0 !important; }",
        ]
    if role == "navigation":
        mark = _child_selector(selector, '[data-binding-id="brand_mark"]')
        name = _child_selector(
            selector,
            ':is([data-binding-id="brand_name"], [data-binding-id="brand_title"])',
        )
        bindings = _child_selector(selector, '[data-binding-id^="nav_"]')
        active = _child_selector(selector, '[data-binding-id="nav_dispatch"]')
        return [
            f"{selector} {{ border: 0 !important; border-radius: 0 !important; "
            "background: transparent !important; box-shadow: none !important; color: inherit !important; }",
            f"{mark} {{ display: inline-grid; flex: 0 0 auto; width: 1.75rem !important; height: 1.75rem; "
            "place-items: center; border-radius: 0.4rem; background: #bdff42 !important; color: #18231f !important; "
            "font-size: 0.85rem !important; font-weight: 800 !important; line-height: 1 !important; }",
            f"{name} {{ margin: 0 !important; color: inherit !important; font-size: 0.95rem !important; "
            "font-weight: 700 !important; letter-spacing: 0 !important; }",
            f"{bindings} {{ margin: 0 !important; color: inherit !important; font-size: 0.85rem !important; "
            "font-weight: 500 !important; letter-spacing: 0 !important; line-height: 1.2 !important; }",
            f"{active} {{ border-radius: 0.55rem; background: rgb(255 255 255 / 0.12) !important; "
            "padding: 0.65rem 0.75rem !important; font-weight: 700 !important; }",
        ]
    if role == "metric_grid":
        values = _child_selector(selector, ".vs-value")
        labels = _child_selector(selector, ".vs-label")
        return [
            f"{selector} {{ border: 0 !important; box-shadow: none !important; background: transparent !important; }}",
            f"{values} {{ border-top: 2px solid #26342e; padding-top: 0.75rem !important; "
            "font-family: Georgia, 'Times New Roman', serif !important; font-size: 2.1rem !important; "
            "font-weight: 500 !important; line-height: 1 !important; }",
            f"{labels} {{ font-size: 0.72rem !important; font-weight: 800 !important; "
            "letter-spacing: 0.08em !important; text-transform: uppercase !important; "
            "white-space: nowrap !important; }",
        ]
    if role == "workload_summary":
        heading = _child_selector(
            selector,
            ':is([data-binding-id$="_label"], [data-binding-id$="_heading"], [data-binding-id$="_title"])',
        )
        values = _child_selector(selector, '[data-binding-id*="workload"]:not([data-binding-id$="_label"])')
        return [
            f"{selector} {{ border: 0 !important; box-shadow: none !important; background: transparent !important; }}",
            f"{heading} {{ font-size: 0.72rem !important; font-weight: 800 !important; "
            "letter-spacing: 0.08em !important; text-transform: uppercase !important; }",
            f"{values} {{ border-top: 2px solid #26342e; padding-top: 0.75rem !important; "
            "font-size: 1rem !important; font-weight: 650 !important; line-height: 1.25 !important; }",
        ]
    if role == "action_row":
        return [
            f"{selector} {{ display: flex !important; flex-flow: row wrap; "
            "justify-content: flex-start; align-items: center; gap: 0.5rem !important; "
            "width: 100%; padding: 0 !important; border: 0 !important; "
            "background: transparent !important; box-shadow: none !important; }"
        ]
    if role in {"attention_job_row", "job_row"}:
        cells = _child_selector(selector, ":is(th, td)")
        heading_rows = ", ".join(
            f"{part.strip()}:not(:has([data-resource-id]))"
            for part in _split_css_selector_list(selector)
        )
        heading_bindings = _child_selector(heading_rows, "[data-binding-id]")
        resource_rows = ", ".join(
            f"{part.strip()}:has([data-resource-id])"
            for part in _split_css_selector_list(selector)
        )
        lines = [
            f"{selector} {{ align-items: center; border: 1px solid #d8ddd9 !important; border-radius: 0.75rem !important; background: #ffffff !important; box-shadow: none !important; }}",
            f"{cells} {{ display: block !important; width: auto !important; padding: 0 !important; "
            "border: 0 !important; background: transparent !important; vertical-align: middle; }",
            f"{heading_rows} {{ display: flex !important; flex-flow: row wrap; justify-content: space-between; "
            "align-items: center; gap: 0.5rem !important; "
            "border: 0 !important; border-radius: 0 !important; background: transparent !important; "
            "padding: 0 !important; }",
            f"{heading_bindings} {{ font-size: 0.875rem !important; font-weight: 700 !important; "
            "letter-spacing: 0.09em !important; line-height: 1.2 !important; }",
        ]
        if role == "attention_job_row":
            lines.append(
                "@media (max-width: 599px) {\n"
                f"  {resource_rows} {{ min-height: 28.75rem; align-content: start; }}\n"
                "}"
            )
        else:
            lines.append(
                "@media (max-width: 599px) {\n"
                f"  {resource_rows} {{ min-height: 11.75rem; align-content: center; }}\n"
                "}"
            )
        return lines
    if role == "lane_header":
        rows = _child_selector(selector, "[data-ir-id]")
        bindings = _child_selector(rows, "[data-binding-id]")
        return [
            f"{selector} {{ display: flex !important; flex-flow: row wrap; justify-content: space-between; "
            "align-items: center; gap: 0.5rem !important; padding: 0 !important; border: 0 !important; "
            "border-radius: 0 !important; background: transparent !important; box-shadow: none !important; }",
            f"{rows} {{ display: flex !important; flex: 1 1 auto; flex-flow: row wrap; "
            "justify-content: space-between; align-items: center; gap: 0.5rem !important; "
            "padding: 0 !important; border: 0 !important; border-radius: 0 !important; "
            "background: transparent !important; box-shadow: none !important; }",
            f"{bindings} {{ font-size: 0.875rem !important; font-weight: 700 !important; "
            "letter-spacing: 0.09em !important; line-height: 1.2 !important; }",
        ]
    if role == "section_heading":
        bindings = _child_selector(selector, "[data-binding-id]")
        return [
            f"{selector} {{ border: 0 !important; border-radius: 0 !important; "
            "background: transparent !important; box-shadow: none !important; }",
            f"{bindings} {{ margin: 0 !important; font-size: 0.875rem !important; "
            "font-weight: 700 !important; letter-spacing: 0.09em !important; "
            "line-height: 1.2 !important; text-transform: uppercase !important; }",
        ]
    if role == "workload_row":
        cells = _child_selector(selector, ":is(th, td)")
        return [
            f"{selector} {{ align-items: center; border: 1px solid #d8ddd9 !important; "
            "border-radius: 0.75rem !important; background: #ffffff !important; "
            "box-shadow: none !important; }",
            f"{cells} {{ display: block !important; width: auto !important; padding: 0 !important; "
            "border: 0 !important; background: transparent !important; vertical-align: middle; }",
        ]
    if role in {"attention", "queue", "section", "collection", "detail", "surface"}:
        return [f"{selector} {{ box-shadow: none !important; }}"]
    return []


def _child_selector(selector: str, child: str) -> str:
    return ", ".join(f"{part.strip()} > {child}" for part in _split_css_selector_list(selector))


def _split_css_selector_list(selector: str) -> list[str]:
    parts: list[str] = []
    start = 0
    round_depth = 0
    square_depth = 0
    quote = ""
    escaped = False
    for index, character in enumerate(selector):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "(":
            round_depth += 1
        elif character == ")":
            round_depth = max(0, round_depth - 1)
        elif character == "[":
            square_depth += 1
        elif character == "]":
            square_depth = max(0, square_depth - 1)
        elif character == "," and round_depth == 0 and square_depth == 0:
            parts.append(selector[start:index])
            start = index + 1
    parts.append(selector[start:])
    return [part for part in parts if part.strip()]


def _media_wrap(breakpoint: str, body: list[str]) -> str:
    condition = {
        "compact": "(max-width: 599px)",
        "medium": "(min-width: 600px) and (max-width: 1023px)",
        "wide": "(min-width: 1024px)",
    }[breakpoint]
    return f"@media {condition} {{\n  " + "\n  ".join(body) + "\n}"


def _validate_layout_properties(
    props: Any,
    path: str,
    target_refs: set[str],
    *,
    declared_areas: set[str] | None = None,
) -> list[dict[str, str]]:
    if not isinstance(props, dict):
        return [_issue("APP_PRESENTATION_LAYOUT_INVALID", path, "Layout properties must be an object.")]
    issues: list[dict[str, str]] = []
    extra = sorted(set(props) - LAYOUT_PROPERTY_KEYS)
    if extra:
        issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", path, f"Unsupported layout property(s): {', '.join(extra)}."))
    enum_fields = {
        "align": ALIGN_VALUES,
        "background": BACKGROUND_VALUES,
        "border": BORDER_VALUES,
        "direction": DIRECTION_VALUES,
        "display": DISPLAY_VALUES,
        "font_family": FONT_FAMILY_VALUES,
        "font_size": FONT_SIZE_VALUES,
        "font_weight": FONT_WEIGHT_VALUES,
        "foreground": FOREGROUND_VALUES,
        "gap": SPACING_VALUES,
        "justify": JUSTIFY_VALUES,
        "letter_spacing": LETTER_SPACING_VALUES,
        "line_height": LINE_HEIGHT_VALUES,
        "max_width": SIZE_VALUES,
        "min_inline_size": MIN_INLINE_SIZE_VALUES,
        "padding": SPACING_VALUES,
        "radius": RADIUS_VALUES,
        "text_transform": TEXT_TRANSFORM_VALUES,
        "text_wrap": TEXT_WRAP_VALUES,
        "visibility": VISIBILITY_VALUES,
        "width": SIZE_VALUES,
    }
    for key, allowed in enum_fields.items():
        if key in props and props[key] not in allowed:
            issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", f"{path}.{key}", f"{key} must be one of {', '.join(allowed)}."))
    columns = props.get("columns")
    if columns is not None:
        valid_columns = (
            isinstance(columns, int)
            and not isinstance(columns, bool)
            and 1 <= columns <= PRESENTATION_MAX_GRID_TRACKS
        ) or (
            isinstance(columns, list)
            and 1 <= len(columns) <= PRESENTATION_MAX_GRID_TRACKS
            and all(item in TRACK_VALUES for item in columns)
        )
        if not valid_columns:
            issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", f"{path}.columns", "columns must be 1-4 or a list of 1-4 bounded track tokens."))
    areas = props.get("areas")
    if areas is not None:
        valid_areas = (
            isinstance(areas, list)
            and 1 <= len(areas) <= PRESENTATION_MAX_GRID_ROWS
            and all(isinstance(row, list) and 1 <= len(row) <= PRESENTATION_MAX_GRID_TRACKS for row in areas)
            and len({len(row) for row in areas}) == 1
            and all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", item) for row in areas for item in row)
        )
        if not valid_areas:
            issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", f"{path}.areas", "areas must be a rectangular 1-4 by 1-4 matrix of region ids."))
        else:
            missing_regions = sorted(
                {
                    str(item)
                    for row in areas
                    for item in row
                    if f"region:{item}" not in target_refs
                    and item != "."
                    and item not in (declared_areas or set())
                }
            )
            if missing_regions:
                issues.append(
                    _issue(
                        "APP_PRESENTATION_AREA_TARGET_MISSING",
                        f"{path}.areas",
                        f"Grid area(s) have no matching region or rule area: {', '.join(missing_regions)}.",
                    )
                )
    area = props.get("area")
    if area is not None and (not isinstance(area, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", area)):
        issues.append(
            _issue(
                "APP_PRESENTATION_LAYOUT_INVALID",
                f"{path}.area",
                "area must be a safe grid-area identity.",
            )
        )
    for key, minimum, maximum in (("order", -16, 16), ("span", 1, 4), ("max_lines", 1, 8)):
        if key in props and (not isinstance(props[key], int) or isinstance(props[key], bool) or not minimum <= props[key] <= maximum):
            issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", f"{path}.{key}", f"{key} must be an integer from {minimum} through {maximum}."))
    if "sticky" in props and not isinstance(props["sticky"], bool):
        issues.append(_issue("APP_PRESENTATION_LAYOUT_INVALID", f"{path}.sticky", "sticky must be boolean."))
    return issues


def _validate_item_layout(
    items: Any,
    path: str,
    target_refs: set[str],
    *,
    declared_areas: set[str],
) -> list[dict[str, str]]:
    if not isinstance(items, dict):
        return [_issue("APP_PRESENTATION_ITEMS_INVALID", path, "items must be an object.")]
    issues: list[dict[str, str]] = []
    extra = sorted(set(items) - set(PRESENTATION_ITEM_LAYOUT_KEYS))
    if extra:
        issues.append(
            _issue(
                "APP_PRESENTATION_UNKNOWN_FIELD",
                path,
                f"Unsupported item layout field(s): {', '.join(extra)}.",
            )
        )
    issues.extend(
        _validate_layout_properties(
            items.get("base", {}),
            f"{path}.base",
            target_refs,
            declared_areas=declared_areas,
        )
    )
    variants = items.get("variants", {})
    if not isinstance(variants, dict):
        return [
            *issues,
            _issue("APP_PRESENTATION_VARIANTS_INVALID", f"{path}.variants", "variants must be an object."),
        ]
    unknown = sorted(set(variants) - set(PRESENTATION_BREAKPOINTS))
    if unknown:
        issues.append(
            _issue(
                "APP_PRESENTATION_BREAKPOINT_INVALID",
                f"{path}.variants",
                f"Unknown breakpoint(s): {', '.join(unknown)}.",
            )
        )
    for breakpoint, props in variants.items():
        issues.extend(
            _validate_layout_properties(
                props,
                f"{path}.variants.{breakpoint}",
                target_refs,
                declared_areas=declared_areas,
            )
        )
    return issues


def _declared_area_names(rules: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(rules, list):
        return names
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        layouts: list[Any] = [rule.get("base")]
        variants = rule.get("variants")
        if isinstance(variants, dict):
            layouts.extend(variants.values())
        items = rule.get("items")
        if isinstance(items, dict):
            layouts.append(items.get("base"))
            item_variants = items.get("variants")
            if isinstance(item_variants, dict):
                layouts.extend(item_variants.values())
        for layout in layouts:
            if isinstance(layout, dict) and isinstance(layout.get("area"), str):
                names.add(layout["area"])
    return names


def _screen_target_refs(screen: dict[str, Any]) -> set[str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    refs: set[str] = set()
    for section, prefix in (("regions", "region"), ("motifs", "motif"), ("bindings", "binding")):
        for item in view.get(section, []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                refs.add(f"{prefix}:{item['id']}")
    return refs


def _screen_target_parents(screen: dict[str, Any]) -> dict[str, str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    parents: dict[str, str] = {}
    for region in view.get("regions", []):
        if not isinstance(region, dict) or not isinstance(region.get("id"), str):
            continue
        parent = region.get("parent_region")
        if isinstance(parent, str) and parent:
            parents[f"region:{region['id']}"] = f"region:{parent}"
    member_motifs: dict[str, str] = {}
    for motif in view.get("motifs", []):
        if not isinstance(motif, dict) or not isinstance(motif.get("id"), str):
            continue
        motif_ref = f"motif:{motif['id']}"
        region = motif.get("region")
        if isinstance(region, str) and region:
            parents[motif_ref] = f"region:{region}"
        for member in motif.get("members", []):
            if isinstance(member, str):
                member_motifs.setdefault(member, motif_ref)
    for binding in view.get("bindings", []):
        if not isinstance(binding, dict) or not isinstance(binding.get("id"), str):
            continue
        binding_id = binding["id"]
        parent = member_motifs.get(binding_id)
        if parent is None:
            region = binding.get("target_region")
            if isinstance(region, str) and region:
                parent = f"region:{region}"
        if parent is not None:
            parents[f"binding:{binding_id}"] = parent
    return parents


def _target_descends_from(
    target_ref: str,
    ancestor_ref: str,
    parents: dict[str, str],
) -> bool:
    seen: set[str] = set()
    current = target_ref
    while current in parents and current not in seen:
        seen.add(current)
        current = parents[current]
        if current == ancestor_ref:
            return True
    return False


def _target_ir_id(target_ref: str) -> str:
    kind, _, identity = target_ref.partition(":")
    return f"{kind}_{identity}"


def _screen_style_tokens(screen: dict[str, Any]) -> dict[str, str]:
    intent = screen.get("intent_bundle")
    if not isinstance(intent, dict):
        return {}
    try:
        bundle = IntentBundle.from_json(intent)
    except (TypeError, ValueError, KeyError):
        return {}
    return dict(sorted(_derive_style_tokens(bundle.substrate, bundle.view_spec).items()))


def _important_declarations(css: str) -> list[str]:
    declarations: list[str] = []
    for item in css.split(";"):
        declaration = item.strip()
        if not declaration or ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip()
        value = value.strip()
        if prop and value:
            declarations.append(f"{prop}: {value} !important")
    return declarations


def _css_attr_token(token: str) -> str:
    return token.replace("\\", "\\\\").replace('"', '\\"')


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": message,
        "suggestion": "Use bounded PresentationPlan profile, rule, layout, breakpoint, and anchor values.",
    }


__all__ = [
    "ALIGN_VALUES",
    "BACKGROUND_VALUES",
    "BORDER_VALUES",
    "DIRECTION_VALUES",
    "DISPLAY_VALUES",
    "FONT_FAMILY_VALUES",
    "FONT_SIZE_VALUES",
    "FONT_WEIGHT_VALUES",
    "FOREGROUND_VALUES",
    "JUSTIFY_VALUES",
    "LETTER_SPACING_VALUES",
    "LINE_HEIGHT_VALUES",
    "LAYOUT_PROPERTY_KEYS",
    "MIN_INLINE_SIZE_VALUES",
    "PRESENTATION_BREAKPOINTS",
    "PRESENTATION_MAX_ANCHORS",
    "PRESENTATION_MAX_GRID_ROWS",
    "PRESENTATION_MAX_GRID_TRACKS",
    "PRESENTATION_MAX_RULES",
    "PRESENTATION_PLAN_FILE",
    "PRESENTATION_PLAN_MAX_BYTES",
    "PRESENTATION_PLAN_SCHEMA_VERSION",
    "PRESENTATION_PROFILES",
    "PRESENTATION_RELATIONS",
    "RADIUS_VALUES",
    "SIZE_VALUES",
    "SPACING_VALUES",
    "TEXT_WRAP_VALUES",
    "TEXT_TRANSFORM_VALUES",
    "TRACK_VALUES",
    "VISIBILITY_VALUES",
    "build_presentation_plan",
    "presentation_plan_css",
    "presentation_plan_diagnostics",
    "presentation_plan_hash",
    "presentation_plan_text",
    "validate_screen_presentation",
]
