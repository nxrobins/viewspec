"""React Tailwind emitter contrast proof (a11y slice 2).

Extends the scoped-WCAG contrast handle to the `react_tailwind_tsx` emitter. React colors are
Tailwind utility classes, not the profile hex — so this resolves each profile's recipe/overlay
classes to their **browser-grounded** sRGB values and checks the same size/role-scoped thresholds
(body 4.5 / large 3.0 / UI 3.0) as the HTML slice, reusing `viewspec.a11y`'s WCAG core.

`TAILWIND_PALETTE` is the Tailwind v4.3 default palette RASTERIZED by Blink (canvas getImageData) —
i.e. the exact sRGB the `react_tailwind_host` (Playwright/Chromium) actually paints. It is pinned
(RC-A); a host Tailwind bump must re-ground it. See `specs/a11y-react-contrast-spec.md`.
"""

from __future__ import annotations

import re
from typing import Any

from viewspec.a11y import threshold_for, wcag_contrast_ratio
from viewspec.emitters.react_tailwind_tsx.recipes import RECIPE_BY_KEY, TAILWIND_AESTHETIC_RECIPE_OVERLAYS

# Browser-grounded (Blink-rasterized) Tailwind v4.3 sRGB hex for every class the recipes/overlays use.
TAILWIND_PALETTE: dict[str, str] = {
    "white": "#ffffff",
    "slate-50": "#f8fafc", "slate-100": "#f1f5f9", "slate-200": "#e2e8f0", "slate-300": "#cad5e2",
    "slate-400": "#90a1b9", "slate-500": "#62748e", "slate-600": "#45556c", "slate-700": "#314158",
    "slate-900": "#0f172b", "slate-950": "#020618",
    "teal-50": "#f0fdfa", "teal-100": "#cbfbf1", "teal-700": "#00786f", "teal-800": "#005f5a",
    "violet-50": "#f5f3ff", "violet-200": "#ddd6ff",
    "indigo-50": "#eef2ff", "indigo-100": "#e0e7ff", "indigo-200": "#c6d2ff", "indigo-600": "#4f39f6",
    "indigo-700": "#432dd7", "indigo-800": "#372aac",
    "blue-50": "#eff6ff", "blue-100": "#dbeafe", "blue-700": "#1447e6", "blue-800": "#193cb8",
    "rose-50": "#fff1f2", "rose-100": "#ffe4e6", "rose-200": "#ffccd3", "rose-300": "#ffa1ad",
    "rose-700": "#c70036", "rose-800": "#a50036",
    "neutral-200": "#e5e5e5", "neutral-300": "#d4d4d4", "neutral-700": "#404040",
    "neutral-800": "#262626", "neutral-950": "#0a0a0a",
    "cyan-50": "#ecfeff", "cyan-100": "#cefafe", "cyan-200": "#a2f4fd", "cyan-300": "#53eafd",
    "cyan-400": "#00d3f3", "cyan-900": "#104e64",
    "stone-50": "#fafaf9", "stone-100": "#f5f5f4", "stone-500": "#79716b", "stone-700": "#44403b",
    "stone-900": "#1c1917",
    "amber-50": "#fffbeb", "amber-100": "#fef3c6", "amber-200": "#fee685", "amber-300": "#ffd230",
    "amber-700": "#bb4d00", "amber-800": "#973c00", "amber-900": "#7b3306",
    "red-100": "#ffe2e2", "red-300": "#ffa2a2", "red-600": "#e7000b", "red-800": "#9f0712",
    "fuchsia-200": "#f6cfff", "fuchsia-400": "#ed6aff", "fuchsia-500": "#e12afb",
}

_BG_RE = re.compile(r"bg-(white|black|transparent|[a-z]+-\d+)(?:/(\d+))?")
_TEXT_RE = re.compile(r"text-(white|black|[a-z]+-\d+)")  # \d+ excludes size classes (text-xs/2xl)


class ReactA11yError(ValueError):
    """A recipe class has no grounded palette entry — the palette is incomplete (fail-closed)."""


def _palette(color: str) -> str:
    if color == "transparent":
        return "transparent"
    hex_value = TAILWIND_PALETTE.get(color)
    if hex_value is None:
        raise ReactA11yError(f"react a11y palette missing Tailwind class color {color!r}")
    return hex_value


def _base_tokens(classes: str) -> list[str]:
    # Only unprefixed utilities apply to the default render; skip hover:/focus:/sm: variants.
    return [token for token in classes.split() if ":" not in token]


def _last_bg(classes: str) -> tuple[str, float] | None:
    result: tuple[str, float] | None = None
    for token in _base_tokens(classes):
        match = _BG_RE.fullmatch(token)
        if match:
            result = (match.group(1), int(match.group(2)) / 100 if match.group(2) else 1.0)
    return result  # Tailwind merge order: later class wins


def _last_text(classes: str) -> str | None:
    result: str | None = None
    for token in _base_tokens(classes):
        match = _TEXT_RE.fullmatch(token)
        if match:
            result = match.group(1)
    return result


def _composite(top_hex: str, alpha: float, bottom_hex: str) -> str:
    t = [int(top_hex[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(bottom_hex[i : i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(alpha * t[i] + (1 - alpha) * b[i]) for i in range(3))


def _bg_hex(bg: tuple[str, float] | None, *, over: str, fallback: str) -> str:
    """Resolve a (color, alpha) background to opaque hex, compositing alpha over `over`."""
    if bg is None:
        return fallback
    color, alpha = bg
    if color == "transparent" or alpha <= 0:
        return over
    base = _palette(color)
    return _composite(base, alpha, over) if alpha < 1 else base


def _effective_classes(key: str, overlay: dict[str, str] | None) -> str:
    # Base recipe classes merged with the profile overlay (later class wins in _last_*). overlay is
    # None for the no-profile base recipe.
    base = RECIPE_BY_KEY.get(key, "")
    over = overlay.get(key, "") if overlay else ""
    return f"{base} {over}".strip()


def react_contrast_report(profile: str | None = None) -> dict[str, Any]:
    """Scoped-WCAG contrast enumeration for the React Tailwind recipes.

    Resolves each color-bearing element's effective fg/bg classes (base recipe merged with the
    profile overlay; `profile=None` = the no-profile base recipe) to grounded hex, composites alpha
    backgrounds, and checks the shared scoped thresholds. Returns compact, deterministic facts;
    `failures` is the fail-closed signal for the React proof.
    """
    overlay = TAILWIND_AESTHETIC_RECIPE_OVERLAYS.get(profile) if profile else None

    def cls(key: str) -> str:
        return _effective_classes(key, overlay)

    ground = _bg_hex(_last_bg(cls("app_role:app_shell")), over="#ffffff", fallback="#ffffff")
    surface = _bg_hex(_last_bg(cls("app_role:metric_card")), over=ground, fallback=ground)
    value_fg = _last_text(cls("primitive:value"))
    label_fg = _last_text(cls("primitive:label"))
    text_fg = _last_text(cls("primitive:text"))
    button_bg = _bg_hex(_last_bg(cls("primitive:button")), over=surface, fallback="#00786f")
    button_fg = _last_text(cls("primitive:button")) or "white"  # base recipe is text-white
    badge_bg = _bg_hex(_last_bg(cls("primitive:badge")), over=surface, fallback="#cbfbf1")
    badge_fg = _last_text(cls("primitive:badge")) or "teal-800"

    ground_text_fg = _last_text(cls("app_role:app_shell"))

    def fg(name: str | None, default: str) -> str:
        return _palette(name if name else default)

    # Value/label/text render inside cards (surface); page-level text uses the app-shell ink on the
    # ground. Button is a UI component; the badge composites over its surface.
    pairs = [
        ("value / surface", fg(value_fg, "slate-950"), surface, "large"),
        ("label / surface", fg(label_fg, "slate-500"), surface, "body"),
        ("text / surface", fg(text_fg, "slate-700"), surface, "body"),
        ("page text / ground", fg(ground_text_fg, "slate-950"), ground, "body"),
        ("button label", fg(button_fg, "white"), button_bg, "ui"),
        ("badge text", fg(badge_fg, "teal-800"), badge_bg, "body"),
    ]
    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for label, fg_hex, bg_hex, kind in pairs:
        ratio = wcag_contrast_ratio(fg_hex, bg_hex)
        threshold = threshold_for(kind)
        entry = {
            "label": label, "fg": fg_hex, "bg": bg_hex, "kind": kind,
            "ratio": ratio, "threshold": threshold, "passes": ratio >= threshold,
        }
        checked.append(entry)
        if not entry["passes"]:
            failures.append(entry)
    return {
        "emitter": "react_tailwind_tsx",
        "profile": profile,
        "pairs_checked": len(checked),
        "min_contrast_ratio": min((e["ratio"] for e in checked), default=None),
        "contrast_failures": len(failures),
        "failures": failures,
        "pairs": checked,
    }


__all__ = ["TAILWIND_PALETTE", "ReactA11yError", "react_contrast_report"]
