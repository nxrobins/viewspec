"""React Tailwind emitter contrast proof (a11y slice 2).

Extends the scoped-WCAG contrast handle to the `react_tailwind_tsx` emitter. React colors are
Tailwind utility classes, not the profile hex. `tsx_contrast_report` measures a compiled
`ViewSpecView.tsx`: it reads the classes each node is emitted with, resolves their colours to the
**browser-grounded** sRGB values below, and checks every text node's ink against the background of
its nearest ancestor that paints one, with the size/role-scoped thresholds of `viewspec.a11y` (body
4.5 / large 3.0 / UI 3.0). A colour class the palette does not hold is unresolved, and the proof
fails closed on it.

`TAILWIND_PALETTE` is the Tailwind v4.3 default palette RASTERIZED by Blink (canvas getImageData) —
i.e. the exact sRGB the `react_tailwind_host` (Playwright/Chromium) actually paints. It is pinned
(RC-A); a host Tailwind bump must re-ground it.

`react_contrast_report` enumerates the pairs the recipe registry names for a profile. It describes
the registry, not an artifact, and `prove` does not use it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from viewspec.a11y import (
    BOLD_WEIGHT,
    CANVAS_TEXT,
    TRANSPARENT,
    Issue,
    PaintedNode,
    Rgba,
    UnresolvedColour,
    contrast_report_from_measurements,
    measure_text_nodes,
    parse_css_color,
    threshold_for,
    wcag_contrast_ratio,
)
from viewspec.emitters.react_tailwind_tsx.recipes import (
    RECIPE_BY_KEY,  # noqa: F401 - still importable from here; the report reads its registry
    TAILWIND_AESTHETIC_RECIPE_OVERLAYS,  # noqa: F401
    TailwindRecipeRegistry,
    _registry_or_default,
)

# Browser-grounded (Blink-rasterized) Tailwind v4.3 sRGB hex for every colour class the recipes and
# overlays paint text or a background with. red-50 is converted from Tailwind 4.3.0's
# oklch(97.1% 0.013 17.38); it is inside the sRGB gamut, where that conversion gives the
# Blink-rasterized value of every entry above it.
TAILWIND_PALETTE: dict[str, str] = {
    "white": "#ffffff",
    "slate-50": "#f8fafc",
    "slate-100": "#f1f5f9",
    "slate-200": "#e2e8f0",
    "slate-300": "#cad5e2",
    "slate-400": "#90a1b9",
    "slate-500": "#62748e",
    "slate-600": "#45556c",
    "slate-700": "#314158",
    "slate-900": "#0f172b",
    "slate-950": "#020618",
    "teal-50": "#f0fdfa",
    "teal-100": "#cbfbf1",
    "teal-700": "#00786f",
    "teal-800": "#005f5a",
    "violet-50": "#f5f3ff",
    "violet-200": "#ddd6ff",
    "indigo-50": "#eef2ff",
    "indigo-100": "#e0e7ff",
    "indigo-200": "#c6d2ff",
    "indigo-600": "#4f39f6",
    "indigo-700": "#432dd7",
    "indigo-800": "#372aac",
    "blue-50": "#eff6ff",
    "blue-100": "#dbeafe",
    "blue-700": "#1447e6",
    "blue-800": "#193cb8",
    "rose-50": "#fff1f2",
    "rose-100": "#ffe4e6",
    "rose-200": "#ffccd3",
    "rose-300": "#ffa1ad",
    "rose-700": "#c70036",
    "rose-800": "#a50036",
    "neutral-200": "#e5e5e5",
    "neutral-300": "#d4d4d4",
    "neutral-700": "#404040",
    "neutral-800": "#262626",
    "neutral-950": "#0a0a0a",
    "cyan-50": "#ecfeff",
    "cyan-100": "#cefafe",
    "cyan-200": "#a2f4fd",
    "cyan-300": "#53eafd",
    "cyan-400": "#00d3f3",
    "cyan-900": "#104e64",
    "stone-50": "#fafaf9",
    "stone-100": "#f5f5f4",
    "stone-500": "#79716b",
    "stone-700": "#44403b",
    "stone-900": "#1c1917",
    "amber-50": "#fffbeb",
    "amber-100": "#fef3c6",
    "amber-200": "#fee685",
    "amber-300": "#ffd230",
    "amber-700": "#bb4d00",
    "amber-800": "#973c00",
    "amber-900": "#7b3306",
    "red-50": "#fef2f2",
    "red-100": "#ffe2e2",
    "red-300": "#ffa2a2",
    "red-600": "#e7000b",
    "red-800": "#9f0712",
    "fuchsia-200": "#f6cfff",
    "fuchsia-400": "#ed6aff",
    "fuchsia-500": "#e12afb",
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
    return "#" + "".join(f"{round(alpha * t[i] + (1 - alpha) * b[i]):02x}" for i in range(3))


def _bg_hex(bg: tuple[str, float] | None, *, over: str, fallback: str) -> str:
    """Resolve a (color, alpha) background to opaque hex, compositing alpha over `over`."""
    if bg is None:
        return fallback
    color, alpha = bg
    if color == "transparent" or alpha <= 0:
        return over
    base = _palette(color)
    return _composite(base, alpha, over) if alpha < 1 else base


def _effective_classes(key: str, overlay: Mapping[str, str] | None, registry: TailwindRecipeRegistry) -> str:
    # Base recipe classes merged with the profile overlay (later class wins in _last_*). overlay is
    # None for the no-profile base recipe.
    base = registry.recipes.get(key, "")
    over = overlay.get(key, "") if overlay else ""
    return f"{base} {over}".strip()


def react_contrast_report(
    profile: str | None = None, *, recipe_registry: TailwindRecipeRegistry | None = None
) -> dict[str, Any]:
    """Scoped-WCAG contrast of the pairs the React Tailwind recipe registry names for a profile.

    Resolves a fixed set of elements' effective fg/bg classes (base recipe merged with the profile
    overlay; `profile=None` = the no-profile base recipe) to grounded hex, composites alpha
    backgrounds, and checks the shared scoped thresholds. A registry fact: an artifact can paint
    pairs this set does not name, so `prove` measures the artifact with `tsx_contrast_report`.
    `recipe_registry` selects the recipe tables; when omitted it is the default registry.
    """
    registry = _registry_or_default(recipe_registry)
    overlay = registry.aesthetic_overlays.get(profile) if profile else None

    def cls(key: str) -> str:
        return _effective_classes(key, overlay, registry)

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
            "label": label,
            "fg": fg_hex,
            "bg": bg_hex,
            "kind": kind,
            "ratio": ratio,
            "threshold": threshold,
            "passes": ratio >= threshold,
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


# --- Measuring a compiled ViewSpecView.tsx ----------------------------------------------------------

# Tailwind v4 text sizes in px (rem at the default 16px root) and font weights.
_TEXT_SIZE_PX = dict(
    zip(
        ["xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl", "5xl", "6xl", "7xl", "8xl", "9xl"],
        (12.0, 14.0, 16.0, 18.0, 20.0, 24.0, 30.0, 36.0, 48.0, 60.0, 72.0, 96.0, 128.0),
        strict=True,
    )
)
_FONT_WEIGHTS = dict(
    zip(
        ["thin", "extralight", "light", "normal", "medium", "semibold", "bold", "extrabold", "black"],
        (100.0, 200.0, 300.0, 400.0, 500.0, 600.0, BOLD_WEIGHT, 800.0, 900.0),
        strict=True,
    )
)
_TEXT_NOT_COLOUR = frozenset(
    ["left", "center", "right", "justify", "start", "end", "ellipsis", "clip", "wrap", "nowrap", "balance", "pretty"]
)
_BG_NOT_COLOUR_PREFIXES = tuple(
    [
        "bg-fixed",
        "bg-local",
        "bg-scroll",
        "bg-clip-",
        "bg-origin-",
        "bg-repeat",
        "bg-no-repeat",
        "bg-auto",
        "bg-cover",
        "bg-contain",
        "bg-center",
        "bg-top",
        "bg-bottom",
        "bg-left",
        "bg-right",
        "bg-blend-",
        "bg-none",
        "bg-position-",
        "bg-size-",
    ]
)
_BG_IMAGE_PREFIXES = ("bg-linear-", "bg-radial", "bg-conic", "bg-gradient-", "bg-[", "bg-(")
# Variants of an interaction state: never the default render.
_INTERACTION_VARIANTS = frozenset(
    [
        "hover",
        "focus",
        "focus-visible",
        "focus-within",
        "active",
        "visited",
        "disabled",
        "group-hover",
        "group-focus",
        "peer-hover",
        "peer-focus",
    ]
)
_COLOUR_NAME_RE = re.compile(r"(?P<name>[a-z]+(?:-\d+)?)(?:/(?P<alpha>\d{1,3}))?")
_TSX_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*)\b")
_TSX_JSON_ATTR_RE = re.compile(r'\s([A-Za-z][-A-Za-z0-9]*)=\{("(?:\\.|[^"\\])*")\}')
_TSX_LITERAL_ATTR_RE = re.compile(r'\s([A-Za-z][-A-Za-z0-9]*)="([^"]*)"')
_TEXT_INPUT_TAGS = frozenset({"input", "textarea", "select"})


@dataclass
class _ClassStyle:
    color: Rgba | str | None = None  # an Rgba, "inherit", or None when the classes set none
    background: Rgba | str | None = None  # an Rgba, "current", or None when the classes set none
    size: float | None = None
    weight: float | None = None
    size_unknown: bool = False
    color_issue: Issue | None = None
    background_issue: Issue | None = None
    subtree_issues: list[Issue] = field(default_factory=list)


def _colour(name: str) -> Rgba | str | None:
    """A Tailwind colour name (with an optional /alpha) as sRGB, "inherit"/"current", or None."""
    match = _COLOUR_NAME_RE.fullmatch(name)
    if match is None:
        return None
    base = match.group("name")
    if base in {"inherit", "current"}:
        return base if match.group("alpha") is None else None
    if base == "transparent":
        return TRANSPARENT
    if base in {"white", "black"}:
        hex_value = "#ffffff" if base == "white" else "#000000"
    else:
        hex_value = TAILWIND_PALETTE.get(base)
        if hex_value is None:
            return None
    parsed = parse_css_color(hex_value)
    if parsed is None:
        return None
    alpha = match.group("alpha")
    if alpha is None:
        return parsed
    opacity = int(alpha) / 100
    return (parsed[0], parsed[1], parsed[2], min(1.0, opacity)) if opacity <= 1 else None


def _class_style(tokens: list[str]) -> _ClassStyle:
    """What the default render of an element's classes paints. Two classes for one property make it
    unresolved: Tailwind's stylesheet order, which the artifact does not record, would decide."""
    style = _ClassStyle()
    colours: dict[str, str] = {}
    backgrounds: dict[str, str] = {}
    for token in tokens:
        variant, _, utility = token.rpartition(":")
        is_text_colour = utility.startswith("text-") and not (
            utility[5:].split("/", 1)[0] in _TEXT_SIZE_PX or utility[5:] in _TEXT_NOT_COLOUR
        )
        is_background = utility.startswith("bg-") and not utility.startswith(_BG_NOT_COLOUR_PREFIXES)
        if variant:
            if all(part in _INTERACTION_VARIANTS for part in variant.split(":")):
                continue
            # A responsive or structural variant applies at some viewports or positions only.
            if is_text_colour:
                style.color_issue = ("color", token)
            elif is_background:
                style.background_issue = ("background-color", token)
            elif utility.startswith("opacity-"):
                style.subtree_issues.append(("opacity", token))
            elif (utility.startswith("text-") and utility[5:].split("/", 1)[0] in _TEXT_SIZE_PX) or (
                utility.startswith("font-") and utility[5:] in _FONT_WEIGHTS
            ):
                style.size_unknown = True
            continue
        if utility.startswith("text-"):
            rest = utility[5:]
            size_name = rest.split("/", 1)[0]
            if size_name in _TEXT_SIZE_PX:
                style.size = _TEXT_SIZE_PX[size_name]
            elif rest not in _TEXT_NOT_COLOUR:
                colours[token] = rest
        elif is_background:
            if utility.startswith(_BG_IMAGE_PREFIXES):
                style.background_issue = ("background-image", token)
            else:
                backgrounds[token] = utility[3:]
        elif utility.startswith("font-") and utility[5:] in _FONT_WEIGHTS:
            style.weight = _FONT_WEIGHTS[utility[5:]]
        elif utility.startswith("opacity-") and utility != "opacity-100":
            style.subtree_issues.append(("opacity", token))
    if len(colours) > 1:
        style.color_issue = ("color", " ".join(colours))
    elif colours and style.color_issue is None:
        token, name = next(iter(colours.items()))
        resolved = _colour(name)
        if resolved is None:
            style.color_issue = ("color", token)
        else:
            style.color = resolved
    if len(backgrounds) > 1:
        style.background_issue = ("background-color", " ".join(backgrounds))
    elif backgrounds and style.background_issue is None:
        token, name = next(iter(backgrounds.items()))
        resolved = _colour(name)
        if resolved is None or resolved == "inherit":
            style.background_issue = ("background-color", token)
        else:
            style.background = resolved
    return style


def _tsx_attributes(line: str) -> tuple[dict[str, str], bool]:
    """The attributes of one JSX element line, and whether its className is an expression.

    String attributes are JSON literals (``id={"dom-x"}``) or plain literals (``className="..."``).
    A JSON literal escapes every quote it holds, so neither pattern matches inside one, and the
    attributes come before the element's text, so the first match of a name is the attribute.
    """
    attributes: dict[str, str] = {}
    for name, encoded in _TSX_JSON_ATTR_RE.findall(line):
        try:
            attributes.setdefault(name, str(json.loads(encoded)))
        except json.JSONDecodeError:
            continue
    for name, value in _TSX_LITERAL_ATTR_RE.findall(line):
        attributes.setdefault(name, value)
    return attributes, re.search(r"\sclassName=\{", line) is not None


def _render_block(tsx: str) -> list[str] | None:
    lines = tsx.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "return (":
            for end in range(index + 1, len(lines)):
                if lines[end].strip() == ");":
                    return lines[index + 1 : end]
            return None
    return None


def tsx_contrast_report(tsx: str) -> dict[str, Any]:
    """Measure every text node of a compiled ``ViewSpecView.tsx`` from its Tailwind classes.

    Each JSX element of the render block is one line. A line that closes its own element with a
    text expression is a text node, and so is an ``<input>``. The canvas is white and the initial
    ink black, as in the reference host page, which sets neither.
    """
    emitter = "react_tailwind_tsx"
    render_lines = _render_block(tsx)
    if render_lines is None:
        return contrast_report_from_measurements(
            emitter, [], [UnresolvedColour("ViewSpecView.tsx", "render", "unsupported render shape")]
        )
    painted: list[PaintedNode] = []
    stack: list[PaintedNode] = []
    names: list[str] = []
    for raw_line in render_lines:
        line = raw_line.strip()
        if not line or not line.startswith("<"):
            continue
        if line.startswith("</"):
            if stack:
                stack.pop()
                names.pop()
            continue
        match = _TSX_TAG_RE.match(line)
        if match is None:
            continue
        jsx_tag = match.group(1)
        attributes, class_expression = _tsx_attributes(line)
        tag = (attributes.get("as") or jsx_tag).lower() if jsx_tag[:1].isupper() else jsx_tag.lower()
        parent = stack[-1] if stack else None
        identifier = attributes.get("id")
        name = identifier or (f"{names[-1]} {tag}" if names and names[-1] else tag)
        node = _paint_tsx_node(name, parent, attributes.get("className", ""), class_expression)
        node.button = tag == "button"
        closes_inline = line.endswith(f"</{jsx_tag}>")
        node.text_bearing = closes_inline or tag in _TEXT_INPUT_TAGS
        painted.append(node)
        if not line.endswith("/>") and not closes_inline:
            stack.append(node)
            names.append(identifier or (names[-1] if names else ""))
    measured, unresolved = measure_text_nodes(painted)
    return contrast_report_from_measurements(emitter, measured, unresolved)


def _paint_tsx_node(name: str, parent: PaintedNode | None, classes: str, class_expression: bool) -> PaintedNode:
    node = PaintedNode(name=name, parent=parent)
    node.color = parent.color if parent is not None else CANVAS_TEXT
    node.color_issue = parent.color_issue if parent is not None else None
    node.font_size = parent.font_size if parent is not None else node.font_size
    node.font_weight = parent.font_weight if parent is not None else node.font_weight
    if class_expression:
        # Classes computed at runtime cannot be read from the artifact.
        node.color, node.color_issue = None, ("className", "expression")
        node.background, node.background_issue = None, ("className", "expression")
        return node
    style = _class_style(classes.split())
    if style.color_issue is not None:
        node.color, node.color_issue = None, style.color_issue
    elif isinstance(style.color, tuple):
        node.color, node.color_issue = style.color, None
    if style.background_issue is not None:
        node.background, node.background_issue = None, style.background_issue
    elif style.background == "current":
        node.background, node.background_issue = node.color, node.color_issue
    elif isinstance(style.background, tuple):
        node.background = style.background
    if style.size_unknown:  # a variant may change the size: count the text as body, the strictest
        node.font_size, node.font_weight = None, None
    else:
        node.font_size = style.size if style.size is not None else node.font_size
        node.font_weight = style.weight if style.weight is not None else node.font_weight
    node.subtree_issues.extend(style.subtree_issues)
    return node


__all__ = ["TAILWIND_PALETTE", "ReactA11yError", "react_contrast_report", "tsx_contrast_report"]
