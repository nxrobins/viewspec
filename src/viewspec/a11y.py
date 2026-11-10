"""Accessibility as a checked compiler handle: contrast and accessible-name presence.

Deterministic, statically decidable a11y facts, computed without a browser.

Contrast is measured from the compiled artifact (``artifact_contrast_report``): every text-bearing
node's ink against the background of its nearest ancestor that paints one, the node itself
included. For the ``html_tailwind`` emitter (the ``html-tailwind`` and ``mcp-app`` targets) the
colours come from the document: its inline ``style`` values, which hold the profile and DESIGN.md
values the compiler merged in, and the rules of its ``<style>`` block, cascaded by origin,
importance, specificity and order. For the React Tailwind emitter they are the classes each node is
emitted with, resolved through the browser-grounded Tailwind palette
(``viewspec.emitters.react_tailwind_tsx.a11y``). A colour the measurement cannot resolve to an sRGB
value is reported as unresolved, and ``prove`` fails closed on it.

The measurement covers the default, non-interacted render: rules for ``:hover``, ``:focus`` and the
other interaction states, and Tailwind's interaction variants, are not applied. Hidden nodes are
measured like visible ones, because they can become visible at runtime.

Names are derived tree-free from the flat manifest node props (author-provided vs the emitter's
generic fallback ladder, SC-A).

Thresholds are size/role-scoped per WCAG 2.x: body text 4.5:1; large text (18pt, or 14pt bold) and
UI components (a button's label) 3.0:1. A size the measurement cannot read counts as body text.

``a11y_contrast_report`` and ``profile_governed_pairs`` enumerate the colour pairs a profile's tokens
name. They describe the registry, not an artifact, and ``prove`` does not use them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
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


# --- The registry enumeration: the pairs a profile's tokens name, plus fixed base-CSS pairs. It does
#     not read an artifact, so it can miss what one paints; the proof measures the artifact instead. -

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
    """The text/background pairs a profile's tokens name. (label, fg, bg, kind).

    The neutral / muted / accent inks on the ground and on the primary surface, and the action
    button's label on its fill (a UI component, 3.0). A registry fact: an artifact can paint pairs
    this list does not name, which is why ``prove`` measures the artifact instead.
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
    """Contrast of the registry enumeration (base + governed pairs) for a profile, or base-only.

    Returns compact, deterministic facts about the registry. ``prove`` does not use it: it measures
    the artifact with ``artifact_contrast_report``.
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


# --- Measuring the artifact: colours ---------------------------------------------------------------

Rgba = tuple[float, float, float, float]  # sRGB channels 0-255, alpha 0-1

TRANSPARENT: Rgba = (0.0, 0.0, 0.0, 0.0)
CANVAS: Rgba = (255.0, 255.0, 255.0, 1.0)  # the light colour scheme's canvas, under every painted layer
CANVAS_TEXT: Rgba = (0.0, 0.0, 0.0, 1.0)  # the initial value of `color`
ROOT_FONT_SIZE_PX = 16.0
NORMAL_WEIGHT = 400.0
BOLD_WEIGHT = 700.0
LARGE_TEXT_PX = 24.0  # 18pt
LARGE_BOLD_TEXT_PX = 14 * 96 / 72  # 14pt, about 18.67px
_SIZE_EPSILON = 1e-6

_NAMED_COLORS: dict[str, Rgba] = {
    "transparent": TRANSPARENT,
    "white": (255.0, 255.0, 255.0, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
}
_HEX_COLOR_RE = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})")
_RGB_FUNCTION_RE = re.compile(r"rgba?\((.*)\)", re.DOTALL)
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def _number(text: str) -> float | None:
    text = text.strip()
    return float(text) if _NUMBER_RE.fullmatch(text) else None


def _rgb_arguments(arguments: str) -> Rgba | None:
    if "," in arguments:
        parts = [part.strip() for part in arguments.split(",")]
        if len(parts) not in (3, 4):
            return None
        channels, alpha = parts[:3], parts[3] if len(parts) == 4 else None
    else:
        main, slash, alpha_text = arguments.partition("/")
        channels = main.split()
        if len(channels) != 3:
            return None
        alpha = alpha_text.strip() if slash else None
    values: list[float] = []
    for channel in channels:
        percent = channel.endswith("%")
        number = _number(channel[:-1] if percent else channel)
        if number is None:
            return None
        values.append(min(255.0, max(0.0, number * 255 / 100 if percent else number)))
    opacity = 1.0
    if alpha is not None:
        percent = alpha.endswith("%")
        number = _number(alpha[:-1] if percent else alpha)
        if number is None:
            return None
        opacity = min(1.0, max(0.0, number / 100 if percent else number))
    return (values[0], values[1], values[2], opacity)


def parse_css_color(value: str) -> Rgba | None:
    """An sRGB colour from CSS text: hex (3, 4, 6 or 8 digits), ``rgb()``/``rgba()``, or
    ``transparent``, ``white`` or ``black``. Anything else is None: the measurement does not guess."""
    text = value.strip().lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    match = _HEX_COLOR_RE.fullmatch(text)
    if match is not None:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
        alpha = channels[3] / 255 if len(channels) == 4 else 1.0
        return (float(channels[0]), float(channels[1]), float(channels[2]), alpha)
    match = _RGB_FUNCTION_RE.fullmatch(text)
    if match is not None:
        return _rgb_arguments(match.group(1))
    return None


def composite_over(top: Rgba, bottom: Rgba) -> Rgba:
    """``top`` painted over the opaque ``bottom`` (source-over)."""
    alpha = top[3]
    return (
        top[0] * alpha + bottom[0] * (1 - alpha),
        top[1] * alpha + bottom[1] * (1 - alpha),
        top[2] * alpha + bottom[2] * (1 - alpha),
        1.0,
    )


def rgba_hex(color: Rgba) -> str:
    """The ``#rrggbb`` an opaque colour rasterizes to (each channel rounded half up)."""
    return "#" + "".join(f"{int(min(255.0, max(0.0, channel)) + 0.5):02x}" for channel in color[:3])


def text_kind(font_size_px: float | None, font_weight: float | None, *, ui: bool) -> str:
    """The WCAG scope of a text node: ``ui`` for a button's label, ``large`` for 18pt (or 14pt bold)
    text, ``body`` otherwise. An unknown size is body text, the strictest."""
    if ui:
        return "ui"
    if font_size_px is None:
        return "body"
    weight = font_weight if font_weight is not None else NORMAL_WEIGHT
    if font_size_px >= LARGE_TEXT_PX - _SIZE_EPSILON:
        return "large"
    if font_size_px >= LARGE_BOLD_TEXT_PX - _SIZE_EPSILON and weight >= BOLD_WEIGHT:
        return "large"
    return "body"


# --- Measuring the artifact: the painted tree both readers build ------------------------------------

Issue = tuple[str, str]  # (property, the value the measurement could not resolve)


@dataclass(eq=False)
class PaintedNode:
    """One element as the measurement sees it: its computed ink, the background it paints itself,
    its font size and weight, and what could not be resolved.

    ``color`` is None when ``color_issue`` holds why; ``background`` is None when
    ``background_issue`` does (a colour or an image the measurement cannot read). ``subtree_issues``
    affect this node and everything inside it (opacity, a conditional rule).
    """

    name: str
    parent: PaintedNode | None
    text_bearing: bool = False
    button: bool = False
    color: Rgba | None = CANVAS_TEXT
    color_issue: Issue | None = None
    background: Rgba | None = TRANSPARENT
    background_issue: Issue | None = None
    font_size: float | None = ROOT_FONT_SIZE_PX
    font_weight: float | None = NORMAL_WEIGHT
    subtree_issues: list[Issue] = field(default_factory=list)

    def chain(self) -> Iterator[PaintedNode]:
        node: PaintedNode | None = self
        while node is not None:
            yield node
            node = node.parent


@dataclass(frozen=True)
class TextMeasurement:
    node: str
    ink: Rgba
    background: Rgba  # opaque
    kind: str


@dataclass(frozen=True)
class UnresolvedColour:
    node: str
    property: str
    value: str


def measure_text_nodes(nodes: Iterable[PaintedNode]) -> tuple[list[TextMeasurement], list[UnresolvedColour]]:
    """Measure every text-bearing node: its ink over the layers painted behind it, down to the
    first opaque one (or the canvas). Alpha layers composite in paint order."""
    measured: list[TextMeasurement] = []
    unresolved: list[UnresolvedColour] = []
    for node in nodes:
        if not node.text_bearing:
            continue
        issues: list[Issue] = []
        chain = list(node.chain())
        for ancestor in chain:
            issues.extend(ancestor.subtree_issues)
        if node.color is None:
            issues.append(node.color_issue or ("color", "unknown"))
        layers: list[Rgba] = []
        for ancestor in chain:
            if ancestor.background is None:
                issues.append(ancestor.background_issue or ("background-color", "unknown"))
                break
            if ancestor.background[3] > 0:
                layers.append(ancestor.background)
            if ancestor.background[3] >= 1:
                break
        if issues:
            for prop, value in dict.fromkeys(issues):
                unresolved.append(UnresolvedColour(node.name, prop, value))
            continue
        backdrop = CANVAS
        for layer in reversed(layers):
            backdrop = composite_over(layer, backdrop)
        ink = node.color if node.color is not None else CANVAS_TEXT
        ui = any(ancestor.button for ancestor in chain)
        measured.append(TextMeasurement(node.name, ink, backdrop, text_kind(node.font_size, node.font_weight, ui=ui)))
    return measured, unresolved


def contrast_report_from_measurements(
    emitter: str, measured: Iterable[TextMeasurement], unresolved: Iterable[UnresolvedColour]
) -> dict[str, Any]:
    """The deterministic report of a measurement: every pair with its ratio and scoped threshold,
    the pairs below it (``failures``) and the colours that could not be resolved (``unresolved``)."""
    pairs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in measured:
        background = rgba_hex(item.background)
        ink = rgba_hex(composite_over(item.ink, item.background))
        ratio = wcag_contrast_ratio(ink, background)
        threshold = threshold_for(item.kind)
        entry = {
            "node": item.node,
            "fg": ink,
            "bg": background,
            "kind": item.kind,
            "ratio": ratio,
            "threshold": threshold,
            "passes": ratio >= threshold,
        }
        pairs.append(entry)
        if not entry["passes"]:
            failures.append(entry)
    unresolved_entries = [
        {"node": item.node, "property": item.property, "value": item.value} for item in dict.fromkeys(unresolved)
    ]
    return {
        "source": "artifact",
        "emitter": emitter,
        "pairs_checked": len(pairs),
        "min_contrast_ratio": min((entry["ratio"] for entry in pairs), default=None),
        "contrast_failures": len(failures),
        "failures": failures,
        "unresolved": unresolved_entries,
        "pairs": pairs,
    }


def contrast_pair_label(entry: Mapping[str, Any]) -> str:
    """``node: #ink on #background = ratio:1 (kind, needs threshold:1)``."""
    return (
        f"{entry['node']}: {entry['fg']} on {entry['bg']} = {entry['ratio']}:1 "
        f"({entry['kind']}, needs {entry['threshold']}:1)"
    )


def unresolved_colour_label(entry: Mapping[str, Any]) -> str:
    """``node: property value``."""
    return f"{entry['node']}: {entry['property']} {entry['value']}"


def summarize_labels(labels: list[str], *, limit: int = 8) -> str:
    """The first ``limit`` labels joined, and how many more there are."""
    shown = "; ".join(labels[:limit])
    return f"{shown}; and {len(labels) - limit} more" if len(labels) > limit else shown


# --- Measuring the artifact: HTML documents ---------------------------------------------------------

# Elements whose content is never painted as page text.
_NON_RENDERED_TAGS = frozenset({"head", "script", "style", "template", "noscript", "title", "meta", "link"})
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_TEXT_FORM_CONTROLS = frozenset({"textarea", "select"})
# <input> types that show no text of their own.
_TEXTLESS_INPUT_TYPES = frozenset({"hidden", "checkbox", "radio", "range", "color", "file", "image"})
_BUTTON_INPUT_TYPES = frozenset({"submit", "button", "reset"})

# The user-agent colours that apply when no author rule sets them (Chromium, light colour scheme).
# The emitter's own rules set every one of these on the elements it writes.
_USER_AGENT_CSS = """
a[href] { color: #0000ee; }
button { color: #000000; background-color: #efefef; }
input, textarea, select { color: #000000; background-color: #ffffff; }
mark { color: #000000; background-color: #ffff00; }
"""

_TRACKED_LONGHANDS = frozenset({"color", "background-color", "background-image", "font-size", "font-weight", "opacity"})
_CSS_WIDE_KEYWORDS = frozenset({"inherit", "initial", "unset", "revert", "revert-layer"})
# Interaction states: never the default render.
_DYNAMIC_PSEUDO_CLASSES = frozenset(
    {"hover", "focus", "focus-visible", "focus-within", "active", "visited", "target", "target-within"}
)
_STRUCTURAL_PSEUDO_CLASSES = frozenset(
    {"root", "first-child", "last-child", "only-child", "empty", "disabled", "enabled", "checked", "link", "any-link"}
)
_FORM_CONTROL_TAGS = frozenset({"button", "input", "select", "textarea", "optgroup", "option", "fieldset"})


@dataclass(frozen=True)
class _Declaration:
    longhand: str  # a tracked longhand, or a custom property name
    value: str
    shorthand: str | None  # "background" or "font" when expanded from that shorthand
    important: bool


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` (or any whitespace for " ") outside parentheses, brackets and quotes."""
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and (ch.isspace() if separator == " " else ch == separator):
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


_IMPORTANT_RE = re.compile(r"!\s*important\s*$", re.IGNORECASE)


def _parse_declarations(block: str) -> list[_Declaration]:
    declarations: list[_Declaration] = []
    for item in _split_top_level(block, ";"):
        name, colon, value = item.partition(":")
        if not colon:
            continue
        name = name.strip()
        if not name.startswith("--"):
            name = name.lower()
        value = value.strip()
        match = _IMPORTANT_RE.search(value)
        important = match is not None
        if match is not None:
            value = value[: match.start()].strip()
        if name.startswith("--"):
            declarations.append(_Declaration(name, value, None, important))
        elif name == "background":
            declarations.append(_Declaration("background-color", value, "background", important))
            declarations.append(_Declaration("background-image", value, "background", important))
        elif name == "font":
            declarations.append(_Declaration("font-size", value, "font", important))
            declarations.append(_Declaration("font-weight", value, "font", important))
        elif name in _TRACKED_LONGHANDS:
            declarations.append(_Declaration(name, value, None, important))
    return declarations


@dataclass(frozen=True)
class _Compound:
    tag: str | None
    ids: tuple[str, ...]
    classes: tuple[str, ...]
    attributes: tuple[tuple[str, str | None, str | None], ...]
    pseudo_classes: tuple[str, ...]
    negations: tuple[_Compound, ...]
    never: bool  # an interaction state: not part of the default render


@dataclass(frozen=True)
class _Selector:
    parts: tuple[Any, ...]  # compound, combinator, compound, ...
    specificity: tuple[int, int, int]


_SELECTOR_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<combinator>[>+~])
  | (?P<universal>\*)
  | (?P<type>[A-Za-z][A-Za-z0-9-]*)
  | (?P<id>\#[A-Za-z0-9_-]+)
  | (?P<class>\.[A-Za-z0-9_-]+)
  | (?P<attribute>\[\s*(?P<attr_name>[A-Za-z_][-A-Za-z0-9_:.]*)\s*
        (?:(?P<attr_op>[~|^$*]?=)\s*(?P<attr_value>"[^"]*"|'[^']*'|[-A-Za-z0-9_]+)\s*)?\])
  | (?P<pseudo_element>::[A-Za-z-]+)
  | (?P<pseudo>:[A-Za-z-]+(?:\((?P<pseudo_arg>[^()]*)\))?)
    """,
    re.VERBOSE,
)


def _parse_compound_tokens(tokens: list[re.Match[str]]) -> tuple[_Compound, tuple[int, int, int]] | None:
    tag: str | None = None
    ids: list[str] = []
    classes: list[str] = []
    attributes: list[tuple[str, str | None, str | None]] = []
    pseudo_classes: list[str] = []
    negations: list[_Compound] = []
    never = False
    a = b = c = 0
    for token in tokens:
        kind = token.lastgroup
        text = token.group(0)
        if kind == "type":
            if tag is not None:
                return None
            tag = text.lower()
            c += 1
        elif kind == "universal":
            continue
        elif kind == "id":
            ids.append(text[1:])
            a += 1
        elif kind == "class":
            classes.append(text[1:])
            b += 1
        elif kind == "attribute":
            value = token.group("attr_value")
            if value is not None and value[:1] in "\"'":
                value = value[1:-1]
            attributes.append((token.group("attr_name").lower(), token.group("attr_op"), value))
            b += 1
        elif kind == "pseudo":
            name = text[1:].split("(", 1)[0].lower()
            argument = token.group("pseudo_arg")
            if name == "not" and argument is not None:
                inner = _parse_selector(argument)
                if inner is None or len(inner.parts) != 1:
                    return None
                negations.append(inner.parts[0])
                a, b, c = a + inner.specificity[0], b + inner.specificity[1], c + inner.specificity[2]
            elif argument is None and name in _DYNAMIC_PSEUDO_CLASSES:
                never = True
                b += 1
            elif argument is None and name in _STRUCTURAL_PSEUDO_CLASSES:
                pseudo_classes.append(name)
                b += 1
            else:
                return None
        else:  # a pseudo-element styles generated content, which the measurement does not read
            return None
    compound = _Compound(
        tag, tuple(ids), tuple(classes), tuple(attributes), tuple(pseudo_classes), tuple(negations), never
    )
    return compound, (a, b, c)


def _parse_selector(text: str) -> _Selector | None:
    """One complex selector, or None when it uses syntax the measurement does not support."""
    position = 0
    tokens: list[re.Match[str]] = []
    text = text.strip()
    while position < len(text):
        match = _SELECTOR_TOKEN_RE.match(text, position)
        if match is None:
            return None
        tokens.append(match)
        position = match.end()
    parts: list[Any] = []
    specificity = [0, 0, 0]
    compound_tokens: list[re.Match[str]] = []
    pending_combinator: str | None = None

    def flush() -> bool:
        nonlocal pending_combinator
        if not compound_tokens:
            return True
        parsed = _parse_compound_tokens(compound_tokens)
        if parsed is None:
            return False
        if parts:
            parts.append(pending_combinator or " ")
        parts.append(parsed[0])
        for index in range(3):
            specificity[index] += parsed[1][index]
        compound_tokens.clear()
        pending_combinator = None
        return True

    for token in tokens:
        kind = token.lastgroup
        if kind == "space":
            if not flush():
                return None
            if parts and pending_combinator is None:
                pending_combinator = " "
        elif kind == "combinator":
            if not flush() or not parts:
                return None
            pending_combinator = token.group(0)
        else:
            compound_tokens.append(token)
    if not flush() or not parts:
        return None
    return _Selector(tuple(parts), (specificity[0], specificity[1], specificity[2]))


@dataclass(frozen=True)
class _Rule:
    selectors: tuple[_Selector, ...] | None  # None: unsupported selector syntax
    selector_text: str
    declarations: tuple[_Declaration, ...]
    origin: int  # 0 user agent, 1 author
    order: int
    conditional: bool  # inside @media, @supports or another conditional group


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)


def _parse_stylesheet(css: str, *, origin: int, start: int = 0, conditional: bool = False) -> list[_Rule]:
    rules: list[_Rule] = []
    css = _strip_css_comments(css)
    position = 0
    while True:
        brace = css.find("{", position)
        if brace < 0:
            break
        prelude = css[position:brace].split(";")[-1].strip()  # statement at-rules end with ';'
        depth = 1
        index = brace + 1
        while index < len(css) and depth:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        body = css[brace + 1 : index - 1]
        position = index
        if prelude.startswith("@"):
            name = prelude.split(None, 1)[0].lower()
            if name in {"@media", "@supports", "@container", "@layer", "@document", "@scope", "@starting-style"}:
                rules.extend(_parse_stylesheet(body, origin=origin, start=start + len(rules), conditional=True))
            continue  # @font-face, @keyframes, @page, @property: no element's resting colours
        declarations = tuple(_parse_declarations(body))
        if not declarations:
            continue
        parsed = [_parse_selector(part) for part in _split_top_level(prelude, ",")]
        selectors = None if any(item is None for item in parsed) else tuple(item for item in parsed if item is not None)
        rules.append(_Rule(selectors, prelude, declarations, origin, start + len(rules), conditional))
    return rules


class _Element:
    __slots__ = ("tag", "attrs", "parent", "children", "text", "classes", "painted", "custom")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Element | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Element] = []
        self.text: list[str] = []
        self.classes = frozenset(attrs.get("class", "").split())
        self.painted: PaintedNode | None = None
        self.custom: dict[str, str] = {}  # the computed custom properties, inherited


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document = _Element("#document", {}, None)
        self.current = self.document
        self.styles: list[str] = []

    def _element(self, tag: str, attrs: list[tuple[str, str | None]]) -> _Element:
        attributes: dict[str, str] = {}
        for name, value in attrs:
            attributes.setdefault(name.lower(), value or "")  # a browser keeps the first of duplicates
        element = _Element(tag.lower(), attributes, self.current)
        self.current.children.append(element)
        return element

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = self._element(tag, attrs)
        if element.tag not in _VOID_TAGS:
            self.current = element

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        node: _Element | None = self.current
        while node is not None and node is not self.document and node.tag != tag:
            node = node.parent
        if node is not None and node is not self.document and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        if self.current.tag == "style":
            self.styles.append(data)
        else:
            self.current.text.append(data)


def _pseudo_matches(name: str, element: _Element) -> bool:
    parent = element.parent
    siblings = parent.children if parent is not None else [element]
    if name == "root":
        return parent is not None and parent.tag == "#document"
    if name == "first-child":
        return siblings[0] is element
    if name == "last-child":
        return siblings[-1] is element
    if name == "only-child":
        return len(siblings) == 1
    if name == "empty":
        return not element.children and not "".join(element.text)
    if name == "disabled":
        return element.tag in _FORM_CONTROL_TAGS and "disabled" in element.attrs
    if name == "enabled":
        return element.tag in _FORM_CONTROL_TAGS and "disabled" not in element.attrs
    if name == "checked":
        return "checked" in element.attrs
    return element.tag in {"a", "area"} and "href" in element.attrs  # link, any-link


def _attribute_matches(element: _Element, name: str, operator: str | None, expected: str | None) -> bool:
    actual = element.attrs.get(name)
    if actual is None:
        return False
    if operator is None or expected is None:
        return True
    if operator == "=":
        return actual == expected
    if operator == "~=":
        return expected in actual.split()
    if operator == "|=":
        return actual == expected or actual.startswith(expected + "-")
    if not expected:
        return False
    if operator == "^=":
        return actual.startswith(expected)
    if operator == "$=":
        return actual.endswith(expected)
    return expected in actual  # *=


def _compound_matches(compound: _Compound, element: _Element) -> bool:
    if compound.never:
        return False
    if compound.tag is not None and compound.tag != element.tag:
        return False
    if any(element.attrs.get("id") != identifier for identifier in compound.ids):
        return False
    if any(name not in element.classes for name in compound.classes):
        return False
    if not all(_attribute_matches(element, *attribute) for attribute in compound.attributes):
        return False
    if not all(_pseudo_matches(name, element) for name in compound.pseudo_classes):
        return False
    return not any(_compound_matches(negation, element) for negation in compound.negations)


def _selector_matches(selector: _Selector, element: _Element) -> bool:
    parts = selector.parts

    def match_at(index: int, candidate: _Element) -> bool:
        if not _compound_matches(parts[index], candidate):
            return False
        if index == 0:
            return True
        combinator = parts[index - 1]
        parent = candidate.parent if candidate.parent is not None and candidate.parent.tag != "#document" else None
        if combinator == ">":
            return parent is not None and match_at(index - 2, parent)
        if combinator == " ":
            while parent is not None:
                if match_at(index - 2, parent):
                    return True
                parent = parent.parent if parent.parent is not None and parent.parent.tag != "#document" else None
            return False
        siblings = candidate.parent.children if candidate.parent is not None else [candidate]
        before = siblings[: siblings.index(candidate)]
        if combinator == "+":
            return bool(before) and match_at(index - 2, before[-1])
        return any(match_at(index - 2, sibling) for sibling in before)  # ~

    return match_at(len(parts) - 1, element)


_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,(?P<fallback>[^()]*(?:\([^()]*\)[^()]*)*))?\)")


class _Unresolvable(Exception):
    pass


def _substitute_vars(value: str, custom: Mapping[str, str], depth: int = 0) -> str | None:
    """``value`` with every ``var()`` replaced from ``custom`` (or its fallback); None when one has
    neither, or the references do not settle."""
    if "var(" not in value:
        return value
    if depth > 8:
        return None

    def replace(match: re.Match[str]) -> str:
        if match.group(1) in custom:
            return custom[match.group(1)]
        fallback = match.group("fallback")
        if fallback is None:
            raise _Unresolvable
        return fallback.strip()

    try:
        substituted = _VAR_RE.sub(replace, value)
    except _Unresolvable:
        return None
    if substituted == value:
        return None
    return _substitute_vars(substituted, custom, depth + 1)


_BACKGROUND_IMAGE_RE = re.compile(
    r"\b(?:url|image|image-set|cross-fade|element|paint|(?:repeating-)?(?:linear|radial|conic)-gradient)\s*\(",
    re.IGNORECASE,
)
_BACKGROUND_KEYWORDS = frozenset(
    [
        "none",
        "repeat",
        "repeat-x",
        "repeat-y",
        "no-repeat",
        "space",
        "round",
        "scroll",
        "fixed",
        "local",
        "top",
        "bottom",
        "left",
        "right",
        "center",
        "border-box",
        "padding-box",
        "content-box",
        "text",
        "auto",
        "cover",
        "contain",
        "/",
    ]
)
_LENGTH_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[a-z]+|%)?", re.IGNORECASE)


def _background_shorthand_color(value: str) -> str | None:
    """The colour token of a ``background`` shorthand, "transparent" when it has none, or None when
    a token could be a colour the measurement does not read."""
    layers = _split_top_level(value, ",")
    if not layers:
        return "transparent"
    for token in _split_top_level(layers[-1], " "):
        lowered = token.lower()
        if parse_css_color(lowered) is not None or lowered == "currentcolor":
            return lowered
        if lowered in _BACKGROUND_KEYWORDS or _LENGTH_RE.fullmatch(lowered) or _BACKGROUND_IMAGE_RE.match(lowered):
            continue
        return None  # a named colour, a colour function or something else unread
    return "transparent"


_FONT_SIZE_RE = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))(px|rem|em|%|pt)")
_FONT_WEIGHT_KEYWORDS = {"normal": NORMAL_WEIGHT, "bold": BOLD_WEIGHT}


def _font_shorthand_parts(value: str) -> tuple[str | None, str | None]:
    """(size, weight) of a ``font`` shorthand; a CSS-wide keyword stands for both."""
    lowered = value.strip().lower()
    if lowered in _CSS_WIDE_KEYWORDS:
        return lowered, lowered
    size: str | None = None
    weight = "normal"
    for token in _split_top_level(lowered, " "):
        candidate = token.split("/", 1)[0]
        if size is None and _FONT_SIZE_RE.fullmatch(candidate):
            size = candidate
            break
        if candidate in {"bold", "bolder", "lighter", "normal"} or (
            _NUMBER_RE.fullmatch(candidate) and 1 <= float(candidate) <= 1000
        ):
            weight = candidate
    return size, weight


def _font_size_px(value: str, parent: float | None, root: float | None) -> float | None:
    lowered = value.strip().lower()
    if lowered in {"inherit", "unset", "revert", "revert-layer"}:
        return parent
    if lowered in {"initial", "medium"}:
        return ROOT_FONT_SIZE_PX
    match = _FONT_SIZE_RE.fullmatch(lowered)
    if match is None:
        return None
    number, unit = float(match.group(1)), match.group(2)
    if unit == "px":
        return number
    if unit == "pt":
        return number * 96 / 72
    if unit == "rem":
        return number * root if root is not None else None
    if parent is None:
        return None
    return number * parent if unit == "em" else number * parent / 100


def _font_weight_value(value: str, parent: float | None) -> float | None:
    lowered = value.strip().lower()
    if lowered in {"inherit", "unset", "revert", "revert-layer"}:
        return parent
    if lowered == "initial":
        return NORMAL_WEIGHT
    if lowered in _FONT_WEIGHT_KEYWORDS:
        return _FONT_WEIGHT_KEYWORDS[lowered]
    if lowered in {"bolder", "lighter"}:
        if parent is None:
            return None
        if lowered == "bolder":
            return 400.0 if parent < 350 else 700.0 if parent < 550 else 900.0 if parent < 900 else parent
        return 100.0 if parent < 550 else 400.0 if parent < 750 else 700.0
    number = _number(lowered)
    return number if number is not None and 1 <= number <= 1000 else None


def _cascade(element: _Element, rules: list[_Rule]) -> tuple[dict[str, _Declaration], list[Issue]]:
    """The winning declaration per longhand, and the conditional rules that match the element."""
    best: dict[str, tuple[tuple[Any, ...], _Declaration]] = {}
    issues: list[Issue] = []
    for rule in rules:
        if rule.selectors is None:
            continue
        specificities = [selector.specificity for selector in rule.selectors if _selector_matches(selector, element)]
        if not specificities:
            continue
        if rule.conditional:
            issues.append(("conditional rule", rule.selector_text))
            continue
        specificity = max(specificities)
        for index, declaration in enumerate(rule.declarations):
            key = (declaration.important, rule.origin, specificity, rule.order, index)
            current = best.get(declaration.longhand)
            if current is None or key > current[0]:
                best[declaration.longhand] = (key, declaration)
    for index, declaration in enumerate(_parse_declarations(element.attrs.get("style", ""))):
        # The style attribute's origin rank (2) outranks every rule of the same importance.
        key = (declaration.important, 2, (0, 0, 0), 0, index)
        current = best.get(declaration.longhand)
        if current is None or key > current[0]:
            best[declaration.longhand] = (key, declaration)
    return {longhand: entry[1] for longhand, entry in best.items()}, issues


def _custom_properties(winners: Mapping[str, _Declaration], parent: Mapping[str, str]) -> dict[str, str]:
    declared = {name: declaration.value for name, declaration in winners.items() if name.startswith("--")}
    if not declared:
        return dict(parent)
    raw = {**parent, **declared}
    computed = dict(parent)
    for name, value in declared.items():
        keyword = value.strip().lower()
        if keyword in {"inherit", "unset"}:
            continue
        if keyword in {"initial", "revert", "revert-layer"}:
            computed.pop(name, None)
            continue
        resolved = _substitute_vars(value, {key: item for key, item in raw.items() if key != name})
        if resolved is None:
            computed.pop(name, None)  # guaranteed-invalid: a var() of it takes the fallback
        else:
            computed[name] = resolved
    return computed


def _paint_element(
    element: _Element,
    winners: Mapping[str, _Declaration],
    parent: PaintedNode | None,
    root_size: float | None,
    custom: Mapping[str, str],
) -> PaintedNode:
    parent_color = parent.color if parent is not None else CANVAS_TEXT
    parent_color_issue = parent.color_issue if parent is not None else None
    painted = PaintedNode(name="", parent=parent)

    # color (inherited)
    declaration = winners.get("color")
    painted.color, painted.color_issue = parent_color, parent_color_issue
    if declaration is not None:
        raw = _substitute_vars(declaration.value, custom)
        keyword = raw.strip().lower() if raw is not None else ""
        if raw is None:
            painted.color, painted.color_issue = None, ("color", declaration.value)
        elif keyword == "initial":
            painted.color, painted.color_issue = CANVAS_TEXT, None
        elif keyword not in _CSS_WIDE_KEYWORDS and keyword != "currentcolor":
            parsed = parse_css_color(raw)
            painted.color, painted.color_issue = (
                (parsed, None) if parsed is not None else (None, ("color", declaration.value))
            )

    # background-color and background-image (not inherited)
    declaration = winners.get("background-color")
    if declaration is not None:
        prop = declaration.shorthand or "background-color"
        raw = _substitute_vars(declaration.value, custom)
        if raw is not None and declaration.shorthand == "background" and raw.strip().lower() not in _CSS_WIDE_KEYWORDS:
            raw = _background_shorthand_color(raw)
        keyword = raw.strip().lower() if raw is not None else ""
        if raw is None:
            painted.background, painted.background_issue = None, (prop, declaration.value)
        elif keyword == "inherit":
            painted.background = parent.background if parent is not None else TRANSPARENT
            painted.background_issue = parent.background_issue if parent is not None else None
        elif keyword in _CSS_WIDE_KEYWORDS:
            painted.background = TRANSPARENT
        elif keyword == "currentcolor":
            painted.background, painted.background_issue = painted.color, painted.color_issue
        else:
            parsed = parse_css_color(raw)
            painted.background, painted.background_issue = (
                (parsed, None) if parsed is not None else (None, (prop, declaration.value))
            )
    declaration = winners.get("background-image")
    if declaration is not None and painted.background is not None:
        prop = declaration.shorthand or "background-image"
        raw = _substitute_vars(declaration.value, custom)
        if raw is None:
            painted.background, painted.background_issue = None, (prop, declaration.value)
        elif declaration.shorthand == "background":
            if _BACKGROUND_IMAGE_RE.search(raw):
                painted.background, painted.background_issue = None, (prop, declaration.value)
        elif raw.strip().lower() not in _CSS_WIDE_KEYWORDS | {"none"}:
            painted.background, painted.background_issue = None, (prop, declaration.value)

    # font-size and font-weight (inherited)
    parent_size = parent.font_size if parent is not None else ROOT_FONT_SIZE_PX
    parent_weight = parent.font_weight if parent is not None else NORMAL_WEIGHT
    painted.font_size, painted.font_weight = parent_size, parent_weight
    declaration = winners.get("font-size")
    if declaration is not None:
        raw = _substitute_vars(declaration.value, custom)
        if raw is not None and declaration.shorthand == "font":
            raw = _font_shorthand_parts(raw)[0]
        painted.font_size = _font_size_px(raw, parent_size, root_size) if raw is not None else None
    declaration = winners.get("font-weight")
    if declaration is not None:
        raw = _substitute_vars(declaration.value, custom)
        if raw is not None and declaration.shorthand == "font":
            raw = _font_shorthand_parts(raw)[1]
        painted.font_weight = _font_weight_value(raw, parent_weight) if raw is not None else None

    # opacity (not inherited, but it fades everything inside the element)
    declaration = winners.get("opacity")
    if declaration is not None:
        raw = _substitute_vars(declaration.value, custom)
        lowered = raw.strip().lower() if raw is not None else ""
        if raw is None:
            painted.subtree_issues.append(("opacity", declaration.value))
        elif lowered not in _CSS_WIDE_KEYWORDS:
            percent = lowered.endswith("%")
            number = _number(lowered[:-1] if percent else lowered)
            if number is None or (number / 100 if percent else number) < 1:
                painted.subtree_issues.append(("opacity", declaration.value))
    return painted


def _element_name(element: _Element) -> str:
    identifier = element.attrs.get("id")
    if identifier:
        return identifier
    ancestor = element.parent
    while ancestor is not None:
        if ancestor.attrs.get("id"):
            return f"{ancestor.attrs['id']} {element.tag}"
        ancestor = ancestor.parent
    return element.tag


def _html_text_bearing(element: _Element) -> bool:
    if element.tag == "input":
        return element.attrs.get("type", "text").lower() not in _TEXTLESS_INPUT_TYPES
    if element.tag in _TEXT_FORM_CONTROLS:
        return True
    if "".join(element.text).strip():
        return True
    # A runtime text slot: its text arrives from data or state after the page loads.
    return not element.children and ("data-binding-id" in element.attrs or "data-state-text-id" in element.attrs)


def html_contrast_report(html: str, *, emitter: str = "html_tailwind") -> dict[str, Any]:
    """Measure every text node of an HTML artifact from its inline styles and ``<style>`` rules."""
    parser = _DocumentParser()
    parser.feed(html)
    parser.close()
    # Every parsed rule declares something the measurement reads (the parser keeps nothing else).
    rules = _parse_stylesheet(_USER_AGENT_CSS, origin=0)
    rules.extend(_parse_stylesheet("\n".join(parser.styles), origin=1, start=len(rules)))
    unresolved = [UnresolvedColour("style", "selector", rule.selector_text) for rule in rules if rule.selectors is None]
    painted: list[PaintedNode] = []
    # Only what the body holds is page text; a fragment without a body is all page text.
    has_body = _find_tag(parser.document, "body")

    def visit(element: _Element, parent: _Element | None, root_size: float | None, in_body: bool) -> None:
        if element.tag in _NON_RENDERED_TAGS:
            return
        in_body = in_body or element.tag == "body"
        winners, conditional_issues = _cascade(element, rules)
        element.custom = _custom_properties(winners, parent.custom if parent is not None else {})
        node = _paint_element(
            element, winners, parent.painted if parent is not None else None, root_size, element.custom
        )
        node.name = _element_name(element)
        node.subtree_issues.extend(conditional_issues)
        node.button = element.tag == "button" or (
            element.tag == "input" and element.attrs.get("type", "").lower() in _BUTTON_INPUT_TYPES
        )
        node.text_bearing = in_body and _html_text_bearing(element)
        element.painted = node
        painted.append(node)
        # rem is relative to the root element's font size.
        child_root_size = node.font_size if parent is None else root_size
        for child in element.children:
            visit(child, element, child_root_size, in_body)

    for top in parser.document.children:
        visit(top, None, ROOT_FONT_SIZE_PX, not has_body)
    measured, unresolved_nodes = measure_text_nodes(painted)
    return contrast_report_from_measurements(emitter, measured, [*unresolved, *unresolved_nodes])


def _find_tag(element: _Element, tag: str) -> bool:
    return any(child.tag == tag or _find_tag(child, tag) for child in element.children)


# The artifact each measured emitter writes. react_tsx is not measured: its inline style objects
# are outside what the proof reads, so its contrast check is not applicable.
MEASURED_ARTIFACT_FILES = {"html_tailwind": "index.html", "react_tailwind_tsx": "ViewSpecView.tsx"}


def artifact_contrast_report(artifact_dir: str | Path, *, emitter: str | None) -> dict[str, Any] | None:
    """Measure the compiled artifact in ``artifact_dir``; None for an emitter it does not measure.

    An artifact file that cannot be read is reported as unresolved, so the proof fails closed.
    """
    name = MEASURED_ARTIFACT_FILES.get(emitter or "")
    if emitter is None or name is None:
        return None
    try:
        text = (Path(artifact_dir) / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return contrast_report_from_measurements(emitter, [], [UnresolvedColour(name, "artifact", "unreadable")])
    if emitter == "react_tailwind_tsx":
        # Lazy: plain HTML proofs do not import the React emitter.
        from viewspec.emitters.react_tailwind_tsx.a11y import tsx_contrast_report

        return tsx_contrast_report(text)
    return html_contrast_report(text, emitter=emitter)


# --- Accessible-name presence (SC-A): tree-free scan of flat manifest node props. -------------------
#     name_source ∈ {author, fallback}. A name that would come from the emitter's generic fallback
#     ladder counts as UNNAMED — "non-empty" is never the criterion.

# manifest node PRIMITIVE -> the props that carry an author-supplied accessible name. Keyed by
# primitive (emitter-agnostic: html and react manifests share the composition-IR primitives), not
# the html-only vs-* class. Mirrors the emitter's real derivation (html_tailwind._render_node):
# input -> aria_label, or `labelled_by` (the compiler-associated visible FIELD LABEL — its text is
# author content, rendered as aria-labelledby, so it is a real accessible name, not a fallback);
# image_slot/svg -> alt|label; button -> its visible text|label (fallback "Action").
_NAMED_CONTROL_AUTHOR_PROPS: dict[str, tuple[str, ...]] = {
    "input": ("aria_label", "labelled_by"),
    "button": ("text", "label"),
    "image_slot": ("alt", "label"),
    "svg": ("alt", "label"),
}


def name_report(nodes: dict[str, Any]) -> dict[str, Any]:
    """Accessible-name provenance over the flat manifest `nodes` map.

    Returns counts plus the ir_ids of controls whose name would come from the emitter's generic
    fallback ladder (`unnamed`) — the fail signal for `prove`'s `a11y_names` check.
    """
    interactive = 0
    named = 0
    unnamed: list[str] = []
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        author_props = _NAMED_CONTROL_AUTHOR_PROPS.get(entry.get("primitive"))
        if author_props is None:
            continue
        interactive += 1
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
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
    "CANVAS",
    "CANVAS_TEXT",
    "MEASURED_ARTIFACT_FILES",
    "PaintedNode",
    "THRESHOLD_BODY",
    "THRESHOLD_LARGE",
    "THRESHOLD_UI",
    "TRANSPARENT",
    "TextMeasurement",
    "UnresolvedColour",
    "a11y_contrast_report",
    "artifact_contrast_report",
    "composite_over",
    "contrast_pair_label",
    "contrast_report_from_measurements",
    "html_contrast_report",
    "measure_text_nodes",
    "name_report",
    "parse_css_color",
    "profile_governed_pairs",
    "rgba_hex",
    "summarize_labels",
    "text_kind",
    "threshold_for",
    "unresolved_colour_label",
    "wcag_contrast_ratio",
    "wcag_relative_luminance",
]
