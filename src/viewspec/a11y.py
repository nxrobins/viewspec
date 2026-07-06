"""Accessibility as a checked compiler handle (slice 1: contrast + accessible-name presence).

Deterministic, statically-decidable a11y facts derived from the closed style vocabulary — no
browser, no per-node tree walk, no manifest mutation. Because agents may only use governed style
tokens, the set of possible (foreground, background) pairs is finite and enumerable from the
aesthetic registry plus a fixed table of base-CSS pairs the tokens do not govern; checking that
enumeration provably covers every rendered node (SC-B). Names are derived tree-free from the flat
manifest node props (author-provided vs the emitter's generic fallback ladder, SC-A).

Thresholds are size/role-scoped per WCAG 2.x (body 4.5:1, large text & UI components 3.0:1). See
`specs/a11y-proof-spec.md`.
"""

from __future__ import annotations

import re
from typing import Any

from viewspec.aesthetics import AESTHETIC_PROFILE_STYLE_VALUES

# --- WCAG contrast primitives (proper relative-luminance, not the 0-255 perceptual value the
#     projection instrument uses). Pure, no deps beyond the stdlib. ---------------------------------

_HEX_RE = re.compile(r"#([0-9a-fA-F]{6})")

# Size/role thresholds (WCAG 1.4.3 / 1.4.11). A closed 3-entry table; unknown size -> body (strictest).
THRESHOLD_BODY = 4.5
THRESHOLD_LARGE = 3.0
THRESHOLD_UI = 3.0
CONTRAST_PRECISION = 2  # ratios rounded to 2 decimals so hashes/goldens stay deterministic (C5).


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def wcag_relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance in [0, 1] for a #rrggbb color."""
    match = _HEX_RE.fullmatch(hex_color.strip())
    if match is None:
        raise ValueError(f"a11y contrast needs a #rrggbb color, got {hex_color!r}")
    digits = match.group(1)
    r, g, b = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG contrast ratio (1.0 – 21.0), rounded to CONTRAST_PRECISION for determinism."""
    l1 = wcag_relative_luminance(fg_hex)
    l2 = wcag_relative_luminance(bg_hex)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return round((hi + 0.05) / (lo + 0.05), CONTRAST_PRECISION)


def threshold_for(kind: str) -> float:
    if kind == "large":
        return THRESHOLD_LARGE
    if kind == "ui":
        return THRESHOLD_UI
    return THRESHOLD_BODY  # body + any unknown kind -> strictest


# --- The fixed base-CSS pairs the profile tokens do NOT govern (enumerated from OFFLINE_EMITTER_CSS).
#     Each is (label, fg, bg, kind). Profile-invariant, so checked once. ------------------------------

BASE_CONTRAST_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("badge text", "#115e59", "#ccfbf1", "body"),
    ("input text", "#020617", "#ffffff", "body"),
    ("image-slot label", "#475569", "#e2e8f0", "body"),
    ("svg-slot label", "#475569", "#f8fafc", "body"),
    ("error text", "#991b1b", "#fef2f2", "body"),
    ("loading-state text", "#475569", "#ffffff", "body"),
    ("table header label", "#64748b", "#f8fafc", "body"),
)


def _first_hex(css: str | None) -> str | None:
    if not css:
        return None
    match = _HEX_RE.search(css)
    return "#" + match.group(1).lower() if match else None


def profile_governed_pairs(profile: str) -> list[tuple[str, str, str, str]]:
    """The profile-governed text/background pairs (registry-derived). (label, fg, bg, kind).

    Enumerates the pairs an agent can actually produce with the closed token vocabulary: the neutral
    / muted / accent inks on the ground and on the primary surface, and the action button's label on
    its fill. Button label is treated as a UI component (3.0).
    """
    values = AESTHETIC_PROFILE_STYLE_VALUES[profile]
    ground = _first_hex(values.get("palette.temperature")) or "#f8fafc"
    surface = _first_hex(values.get("surface.subtle")) or ground
    neutral = _first_hex(values.get("tone.neutral")) or "#1f2937"
    muted = _first_hex(values.get("tone.muted")) or "#64748b"
    accent = _first_hex(values.get("tone.accent")) or "#0f766e"
    action = _HEX_RE.findall(values.get("action.accent") or "")  # [bg, fg]
    pairs = [
        ("neutral ink / ground", neutral, ground, "body"),
        ("neutral ink / surface", neutral, surface, "body"),
        ("muted ink / surface", muted, surface, "body"),
        ("accent ink / surface", accent, surface, "body"),
    ]
    if len(action) >= 2:
        pairs.append(("button label", "#" + action[1].lower(), "#" + action[0].lower(), "ui"))
    return pairs


def a11y_contrast_report(profile: str | None = None) -> dict[str, Any]:
    """Full contrast enumeration (base + governed) for a profile (or base-only when profile is None).

    Returns compact, deterministic facts. `failures` lists pairs below their scoped threshold — the
    fail-closed signal for `prove`.
    """
    pairs: list[tuple[str, str, str, str]] = list(BASE_CONTRAST_PAIRS)
    if profile is not None:
        pairs = profile_governed_pairs(profile) + pairs
    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for label, fg, bg, kind in pairs:
        ratio = wcag_contrast_ratio(fg, bg)
        threshold = threshold_for(kind)
        entry = {
            "label": label,
            "fg": fg,
            "bg": bg,
            "kind": kind,
            "ratio": ratio,
            "threshold": threshold,
            "passes": ratio >= threshold,
        }
        checked.append(entry)
        if not entry["passes"]:
            failures.append(entry)
    min_ratio = min((e["ratio"] for e in checked), default=None)
    return {
        "profile": profile,
        "pairs_checked": len(checked),
        "min_contrast_ratio": min_ratio,
        "contrast_failures": len(failures),
        "failures": failures,
        "pairs": checked,
    }


# --- Accessible-name presence (SC-A): tree-free scan of flat manifest node props. -------------------
#     name_source ∈ {author, fallback}. A name that would come from the emitter's generic fallback
#     ladder counts as UNNAMED — "non-empty" is never the criterion.

# class -> the props that carry an author-supplied accessible name for that control type.
_NAMED_CONTROL_AUTHOR_PROPS: dict[str, tuple[str, ...]] = {
    "vs-input": ("aria_label",),
    "vs-button": ("text", "label"),
    "vs-image-slot": ("alt", "label"),
    "vs-svg": ("label",),
}


def _node_classes(entry: dict[str, Any]) -> list[str]:
    classes = entry.get("classes")
    return [c for c in classes if isinstance(c, str)] if isinstance(classes, list) else []


def name_report(nodes: dict[str, Any]) -> dict[str, Any]:
    """Accessible-name provenance over the flat manifest `nodes` map.

    Returns counts plus the ir_ids of controls whose name would come from the fallback ladder
    (`unnamed`) — the warn/fail signal for `prove`'s `a11y_names` check.
    """
    interactive = 0
    named = 0
    unnamed: list[str] = []
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        control_classes = [c for c in _node_classes(entry) if c in _NAMED_CONTROL_AUTHOR_PROPS]
        if not control_classes:
            continue
        interactive += 1
        author_props = _NAMED_CONTROL_AUTHOR_PROPS[control_classes[0]]
        has_author_name = any(isinstance(props.get(key), str) and props.get(key, "").strip() for key in author_props)
        if has_author_name:
            named += 1
        else:
            ir_id = entry.get("ir_id")
            unnamed.append(ir_id if isinstance(ir_id, str) else "<unknown>")
    return {
        "interactive_controls": interactive,
        "named": named,
        "unnamed_interactive": len(unnamed),
        "unnamed": sorted(unnamed),
    }


__all__ = [
    "BASE_CONTRAST_PAIRS",
    "THRESHOLD_BODY",
    "THRESHOLD_LARGE",
    "THRESHOLD_UI",
    "a11y_contrast_report",
    "name_report",
    "profile_governed_pairs",
    "threshold_for",
    "wcag_contrast_ratio",
    "wcag_relative_luminance",
]
