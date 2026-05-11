"""Local DESIGN.md parsing and style-token mapping.

The SDK owns the local/offline subset of DESIGN.md behavior. The hosted API may
support more derivation intelligence, but shared token vocabulary should behave
the same here: parse errors, broken references, and cycles are fatal; malformed
tokens that can be ignored produce diagnostics and fall back to defaults.
"""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


MAX_REFERENCE_DEPTH = 32
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DIMENSION_RE = re.compile(r"^(?:0|-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em))$")
TOKEN_REF_RE = re.compile(r"^\{([A-Za-z0-9_.-]+)\}$")
EMBEDDED_TOKEN_REF_RE = re.compile(r"\{([A-Za-z0-9_.-]+)\}")


class DesignSystemError(ValueError):
    """Raised when DESIGN.md contains blocking validation errors."""

    def __init__(self, message: str, report: DesignLintReport | None = None) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class DesignLintFinding:
    severity: str
    code: str
    path: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass
class DesignLintReport:
    source: str = "internal"
    findings: list[DesignLintFinding] = field(default_factory=list)

    def add(self, severity: str, code: str, path: str, message: str) -> None:
        self.findings.append(DesignLintFinding(severity, code, path, message))

    @property
    def has_errors(self) -> bool:
        return any(finding.severity == "error" for finding in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(finding.severity == "warning" for finding in self.findings)

    def summary(self) -> dict[str, int]:
        return {
            "errors": sum(1 for finding in self.findings if finding.severity == "error"),
            "warnings": sum(1 for finding in self.findings if finding.severity == "warning"),
            "info": sum(1 for finding in self.findings if finding.severity == "info"),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "summary": self.summary(),
            "findings": [finding.to_json() for finding in self.findings],
        }


@dataclass(frozen=True)
class DesignSystemContext:
    name: str
    description: str
    tokens: dict[str, Any]
    style_values: dict[str, str]
    lint_report: DesignLintReport
    design_hash: str
    applied_tokens: dict[str, list[str]] = field(default_factory=dict)
    ignored_tokens: list[str] = field(default_factory=list)
    dropped_tokens: list[str] = field(default_factory=list)
    mode_defaults: list[str] = field(default_factory=list)

    def to_meta(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "design_hash": self.design_hash,
            "lint_summary": self.lint_report.summary(),
            "findings": [finding.to_json() for finding in self.lint_report.findings],
            "applied_tokens": {key: list(value) for key, value in sorted(self.applied_tokens.items())},
            "ignored_tokens": list(self.ignored_tokens),
            "dropped_tokens": list(self.dropped_tokens),
            "mode_defaults": list(self.mode_defaults),
        }


class _DroppedToken:
    pass


DROPPED = _DroppedToken()

_MODE_DARK_DEFAULTS = {
    "background": "#0F172A",
    "surface": "#1E293B",
    "text": "#F1F5F9",
    "muted": "#94A3B8",
    "neutral": "#0F172A",
}


def load_design_system(
    path: str | Path | None = None,
    *,
    content: str | None = None,
    lint: bool = True,
    strict: bool = False,
) -> DesignSystemContext:
    """Load, validate, and map a DESIGN.md file or string.

    `lint=False` preserves the hosted request shape, but fatal parse/reference
    errors still fail locally because the SDK cannot safely theme around them.
    `strict=True` escalates warnings to failure for CLI `--strict-design`.
    """

    if path is None and content is None:
        raise ValueError("Either path or content is required")
    if path is not None and content is not None:
        raise ValueError("Provide path or content, not both")

    source = str(path) if path is not None else "<content>"
    text = Path(path).read_text(encoding="utf-8") if path is not None else str(content)
    report = DesignLintReport(source=source)

    raw_tokens = _parse_design_markdown(text, report)
    tokens, dropped_tokens, reference_paths = _resolve_token_tree(raw_tokens, report)
    ignored_tokens: list[str] = []
    _validate_tokens(tokens, report, ignored_tokens, reference_paths)
    mode_defaults = _apply_mode_defaults(tokens)
    style_values, applied_tokens = _map_style_values(tokens, report, ignored_tokens)

    context = DesignSystemContext(
        name=str(tokens.get("name") or raw_tokens.get("name") or "Unnamed Design"),
        description=str(tokens.get("description") or raw_tokens.get("description") or ""),
        tokens=tokens,
        style_values=style_values,
        lint_report=report,
        design_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        applied_tokens=applied_tokens,
        ignored_tokens=sorted(set(ignored_tokens)),
        dropped_tokens=sorted(dropped_tokens),
        mode_defaults=sorted(mode_defaults),
    )
    if report.has_errors:
        raise DesignSystemError(f"{source} failed DESIGN.md validation with {report.summary()['errors']} error(s)", report)
    if strict and report.has_warnings:
        raise DesignSystemError(
            f"{source} failed strict DESIGN.md validation with {report.summary()['warnings']} warning(s)",
            report,
        )
    _ = lint
    return context


def merge_style_values(base: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    """Return style values with override declarations appended per token.

    Browsers apply later declarations last, so appending preserves fallback CSS
    while letting DESIGN.md tokens win without a heavyweight CSS parser.
    """

    merged = dict(base)
    for token, css in overrides.items():
        if not css:
            continue
        if token in merged and merged[token].strip():
            merged[token] = f"{merged[token].rstrip()} {css.strip()}"
        else:
            merged[token] = css.strip()
    return merged


def _parse_design_markdown(text: str, report: DesignLintReport) -> dict[str, Any]:
    text = text.removeprefix("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        report.add("error", "DESIGN_FRONTMATTER_ERROR", "$", "DESIGN.md must begin with YAML front matter")
        raise DesignSystemError("DESIGN.md must begin with YAML front matter", report)

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        report.add("error", "DESIGN_FRONTMATTER_ERROR", "$", "YAML front matter is missing a closing delimiter")
        raise DesignSystemError("YAML front matter is missing a closing delimiter", report)

    try:
        payload = yaml.safe_load("\n".join(lines[1:end_index])) or {}
    except yaml.YAMLError as exc:
        report.add("error", "DESIGN_YAML_ERROR", "$", f"Invalid YAML front matter: {exc}")
        raise DesignSystemError("Invalid YAML front matter", report) from exc
    if not isinstance(payload, dict):
        report.add("error", "DESIGN_YAML_ERROR", "$", "YAML front matter must be a mapping")
        raise DesignSystemError("YAML front matter must be a mapping", report)
    return payload


def _dot_path(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "$"


def _path_tuple(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in path.split(".") if segment)


def _lookup(raw: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    cursor = raw
    for segment in path:
        if isinstance(cursor, dict) and segment in cursor:
            cursor = cursor[segment]
        elif isinstance(cursor, list) and segment.isdigit() and int(segment) < len(cursor):
            cursor = cursor[int(segment)]
        else:
            return False, None
    return True, cursor


def _resolve_token_tree(raw_tokens: dict[str, Any], report: DesignLintReport) -> tuple[dict[str, Any], set[str], set[str]]:
    cache: dict[tuple[str, ...], Any] = {}
    dropped: set[tuple[str, ...]] = set()
    references: set[str] = set()

    def resolve_path(
        path: tuple[str, ...],
        active: tuple[tuple[str, ...], ...],
        depth: int,
        source_path: tuple[str, ...] | None = None,
    ) -> Any:
        if path in cache:
            return cache[path]
        if depth > MAX_REFERENCE_DEPTH:
            dropped.add(source_path or path)
            report.add("error", "DESIGN_TOKEN_DEPTH_ERROR", _dot_path(source_path or path), "Token reference depth exceeded")
            return DROPPED
        if path in active:
            cycle = (*active[active.index(path) :], path)
            for cycle_path in cycle:
                dropped.add(cycle_path)
            report.add(
                "error",
                "DESIGN_TOKEN_CYCLE_ERROR",
                _dot_path(source_path or path),
                "Cyclic token reference detected: " + " -> ".join(_dot_path(item) for item in cycle),
            )
            return DROPPED

        found, value = _lookup(raw_tokens, path)
        if not found:
            dropped.add(source_path or path)
            report.add("error", "DESIGN_TOKEN_REF_ERROR", _dot_path(source_path or path), f"Token reference not found: {_dot_path(path)}")
            return DROPPED
        resolved = resolve_value(value, path, (*active, path), depth + 1)
        cache[path] = resolved
        return resolved

    def resolve_value(value: Any, current_path: tuple[str, ...], active: tuple[tuple[str, ...], ...], depth: int) -> Any:
        if isinstance(value, dict):
            resolved_map: dict[str, Any] = {}
            for key in value:
                child_path = (*current_path, str(key))
                resolved = resolve_path(child_path, active, depth + 1, child_path)
                if resolved is DROPPED:
                    dropped.add(child_path)
                    continue
                resolved_map[str(key)] = resolved
            return resolved_map
        if isinstance(value, list):
            out = []
            for index, item in enumerate(value):
                resolved = resolve_value(item, (*current_path, str(index)), active, depth + 1)
                if resolved is not DROPPED:
                    out.append(resolved)
            return out
        if isinstance(value, str):
            whole_match = TOKEN_REF_RE.match(value.strip())
            if whole_match:
                ref_path = _path_tuple(whole_match.group(1))
                references.add(_dot_path(ref_path))
                return resolve_path(ref_path, active, depth + 1, current_path)

            def replace_ref(match: re.Match[str]) -> str:
                ref_path = _path_tuple(match.group(1))
                references.add(_dot_path(ref_path))
                resolved = resolve_path(ref_path, active, depth + 1, current_path)
                if resolved is DROPPED:
                    dropped.add(current_path)
                    return ""
                if isinstance(resolved, (dict, list)):
                    return json.dumps(resolved, sort_keys=True)
                return str(resolved)

            if "{" in value and "}" in value:
                return EMBEDDED_TOKEN_REF_RE.sub(replace_ref, value)
        return value

    resolved_root: dict[str, Any] = {}
    for key in raw_tokens:
        path = (str(key),)
        resolved = resolve_path(path, tuple(), 0, path)
        if resolved is DROPPED:
            dropped.add(path)
            continue
        resolved_root[str(key)] = resolved
    return resolved_root, {_dot_path(path) for path in dropped}, references


def _validate_tokens(
    tokens: dict[str, Any],
    report: DesignLintReport,
    ignored_tokens: list[str],
    reference_paths: set[str],
) -> None:
    colors = tokens.get("colors")
    if isinstance(colors, dict):
        invalid = []
        for key, value in colors.items():
            path = f"colors.{key}"
            if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
                invalid.append(str(key))
                ignored_tokens.append(path)
                report.add("warning", "DESIGN_COLOR_FORMAT_WARNING", path, "Color tokens must be sRGB #RRGGBB hex strings; token ignored")
        for key in invalid:
            colors.pop(key, None)
        if colors and "primary" not in colors:
            report.add("warning", "DESIGN_MISSING_PRIMARY", "colors.primary", "Colors are defined but no valid primary color exists")
    elif colors is not None:
        ignored_tokens.append("colors")
        tokens.pop("colors", None)
        report.add("warning", "DESIGN_COLOR_FORMAT_WARNING", "colors", "colors must be a mapping; token ignored")

    if tokens.get("typography") is not None and not isinstance(tokens.get("typography"), dict):
        ignored_tokens.append("typography")
        tokens.pop("typography", None)
        report.add("warning", "DESIGN_TYPOGRAPHY_WARNING", "typography", "typography must be a mapping; token ignored")

    mapped_color_paths = {
        "colors.primary",
        "colors.secondary",
        "colors.tertiary",
        "colors.accent",
        "colors.neutral",
        "colors.background",
        "colors.surface",
        "colors.text",
        "colors.warning",
        "colors.positive",
        "colors.muted",
        "colors.error",
        "colors.info",
    }
    color_paths = {f"colors.{key}" for key in colors} if isinstance(colors, dict) else set()
    for path in sorted(color_paths - mapped_color_paths - reference_paths):
        ignored_tokens.append(path)
        report.add("warning", "DESIGN_ORPHANED_TOKEN", path, f"{path} is not used by the local SDK renderer; token ignored")

    if isinstance(colors, dict) and colors.get("error") and colors.get("warning") and colors["error"] != colors["warning"]:
        report.add(
            "warning",
            "DESIGN_COLOR_COLLISION",
            "colors.error",
            "colors.error and colors.warning both target tone.warning; colors.error wins",
        )


def _apply_mode_defaults(tokens: dict[str, Any]) -> set[str]:
    if tokens.get("mode") != "dark":
        return set()
    colors = tokens.get("colors")
    if not isinstance(colors, dict):
        colors = {}
        tokens["colors"] = colors
    filled: set[str] = set()
    for slot, value in _MODE_DARK_DEFAULTS.items():
        if slot not in colors:
            colors[slot] = value
            filled.add(slot)
    return filled


def _map_style_values(
    tokens: dict[str, Any],
    report: DesignLintReport,
    ignored_tokens: list[str],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    style_values: dict[str, str] = {}
    applied_tokens: dict[str, list[str]] = {}

    def append(style_token: str, css: str, source_path: str) -> None:
        style_values[style_token] = f"{style_values.get(style_token, '')} {css}".strip()
        applied_tokens.setdefault(style_token, []).append(source_path)

    colors = tokens.get("colors") if isinstance(tokens.get("colors"), dict) else {}
    if colors.get("primary"):
        append("tone.neutral", f"color: {colors['primary']};", "colors.primary")
        append("emphasis.high", f"color: {colors['primary']};", "colors.primary")
        append("tone.accent", f"color: {colors['primary']};", "colors.primary")
    if colors.get("secondary"):
        append("tone.muted", f"color: {colors['secondary']};", "colors.secondary")
        append("surface.subtle", f"border-color: {colors['secondary']};", "colors.secondary")
    if colors.get("tertiary"):
        append("tone.accent", f"color: {colors['tertiary']};", "colors.tertiary")
    if colors.get("accent"):
        append("tone.accent", f"color: {colors['accent']};", "colors.accent")
    if colors.get("background"):
        append("palette.temperature", f"background-color: {colors['background']};", "colors.background")
    elif colors.get("neutral"):
        append("palette.temperature", f"background-color: {colors['neutral']};", "colors.neutral")
    if colors.get("surface"):
        append("surface.subtle", f"background-color: {colors['surface']};", "colors.surface")
        append("surface.strong", f"background-color: {colors['surface']};", "colors.surface")
    elif colors.get("neutral"):
        append("surface.subtle", f"background-color: {colors['neutral']};", "colors.neutral")
        append("surface.strong", f"background-color: {colors['neutral']};", "colors.neutral")
    if colors.get("text"):
        append("tone.neutral", f"color: {colors['text']};", "colors.text")
        append("narrative.flow", f"color: {colors['text']};", "colors.text")
    if colors.get("muted"):
        append("tone.muted", f"color: {colors['muted']};", "colors.muted")
    warning = colors.get("error") or colors.get("warning")
    if warning:
        source = "colors.error" if colors.get("error") else "colors.warning"
        append("tone.warning", f"color: {warning}; border-color: {warning};", source)
    if colors.get("positive"):
        append("tone.positive", f"color: {colors['positive']};", "colors.positive")
    if colors.get("info"):
        append("tone.accent", f"color: {colors['info']};", "colors.info")

    typography = tokens.get("typography") if isinstance(tokens.get("typography"), dict) else {}
    heading = _typography_entry(typography, ("heading", "h1"))
    body = _typography_entry(typography, ("body", "body-md"))
    _append_typography(heading, "rhythm.hierarchy", "heading", append, report, ignored_tokens)
    _append_typography(body, "tone.neutral", "body", append, report, ignored_tokens)

    spacing = tokens.get("spacing") if isinstance(tokens.get("spacing"), dict) else {}
    for key, token in (("sm", "density.compact"), ("md", "density.regular"), ("card", "density.regular"), ("lg", "density.airy")):
        if key in spacing:
            dim = _valid_dimension(spacing[key])
            if dim:
                append(token, f"gap: {dim}; padding: {dim};", f"spacing.{key}")
            else:
                ignored_tokens.append(f"spacing.{key}")
                report.add("warning", "DESIGN_DIMENSION_WARNING", f"spacing.{key}", "Spacing token must be a px/rem/em dimension; token ignored")

    rounded = tokens.get("rounded") if isinstance(tokens.get("rounded"), dict) else {}
    for key, token in (("sm", "surface.subtle"), ("md", "surface.strong"), ("lg", "surface.strong")):
        if key in rounded:
            dim = _valid_dimension(rounded[key])
            if dim:
                append(token, f"border-radius: {dim};", f"rounded.{key}")
            else:
                ignored_tokens.append(f"rounded.{key}")
                report.add("warning", "DESIGN_DIMENSION_WARNING", f"rounded.{key}", "Radius token must be a px/rem/em dimension; token ignored")

    return style_values, applied_tokens


def _typography_entry(typography: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        value = typography.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _append_typography(
    entry: dict[str, Any],
    token: str,
    source_prefix: str,
    append: Any,
    report: DesignLintReport,
    ignored_tokens: list[str],
) -> None:
    if not entry:
        return
    if entry.get("fontFamily"):
        family = _font_family_css(str(entry["fontFamily"]))
        if family:
            append(token, f"font-family: {family};", f"typography.{source_prefix}.fontFamily")
    if entry.get("fontWeight") is not None:
        try:
            weight = int(entry["fontWeight"])
        except (TypeError, ValueError):
            ignored_tokens.append(f"typography.{source_prefix}.fontWeight")
            report.add("warning", "DESIGN_TYPOGRAPHY_WARNING", f"typography.{source_prefix}.fontWeight", "fontWeight must be numeric; token ignored")
        else:
            append(token, f"font-weight: {weight};", f"typography.{source_prefix}.fontWeight")
            if token == "rhythm.hierarchy":
                append("emphasis.high", f"font-weight: {weight};", f"typography.{source_prefix}.fontWeight")
    for key, prop in (("fontSize", "font-size"), ("letterSpacing", "letter-spacing"), ("lineHeight", "line-height")):
        if key not in entry:
            continue
        dim = _valid_dimension(entry[key]) if key != "lineHeight" else _valid_line_height(entry[key])
        if dim:
            append(token, f"{prop}: {dim};", f"typography.{source_prefix}.{key}")
        else:
            ignored_tokens.append(f"typography.{source_prefix}.{key}")
            report.add("warning", "DESIGN_DIMENSION_WARNING", f"typography.{source_prefix}.{key}", f"{key} must be a dimension; token ignored")


def _font_family_css(value: str) -> str | None:
    value = value.strip()
    if not value or any(char in value for char in "{};"):
        return None
    family = value if "," in value else json.dumps(value)
    return f'{family}, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'


def _valid_dimension(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return f"{value}px"
    if isinstance(value, str) and DIMENSION_RE.match(value.strip()):
        return value.strip()
    return None


def _valid_line_height(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if DIMENSION_RE.match(stripped):
            return stripped
        try:
            float(stripped)
        except ValueError:
            return None
        return stripped
    return None


__all__ = [
    "DesignLintFinding",
    "DesignLintReport",
    "DesignSystemContext",
    "DesignSystemError",
    "load_design_system",
    "merge_style_values",
]
