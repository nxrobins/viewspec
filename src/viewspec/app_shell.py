"""Static Shell V0 AppBundle renderer helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from html import escape
from itertools import chain
from pathlib import Path
from typing import Any, NamedTuple

from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_resource_repeat import resource_repeat_summary
from viewspec.app_validation import (
    APP_BUNDLE_MAX_ROUTES,
    APP_BUNDLE_MAX_SCREENS,
    URL_SCHEME_RE,
    _app_schema_version,
    _app_summary,
    _resource_binding_limits,
    _resource_binding_report_fields,
    _state_ir_limits,
)
from viewspec.app_versions import app_has_visibility
from viewspec.local_tools import source_hash
from viewspec.local_tools_constants import MAX_SCANNED_TAG_BLOCKS
from viewspec.local_tools_validators import TagBlockScan, find_tag_blocks, iter_tag_regions, normalize_css_text
from viewspec.presentation_plan import (
    build_presentation_plan,
    presentation_plan_css,
    presentation_plan_diagnostics,
    presentation_plan_hash,
)
from viewspec.state_ir import generate_browser_reducer_script

APP_SHELL_TARGET = "html-tailwind-app"
APP_SHELL_ROUTE_NAVIGATION = "static_shell_v0"
APP_SHELL_DEFAULT_OUT = "app-dist"
APP_SHELL_DIR_NAME = "app-shell"
APP_SHELL_MANIFEST = "shell_manifest.json"
APP_SHELL_DIAGNOSTICS = "diagnostics.json"
APP_SHELL_INDEX = "index.html"
APP_SHELL_MAX_HTML_BYTES = 2 * 1024 * 1024
APP_SHELL_MAX_JS_BYTES = 64 * 1024
APP_SHELL_MAX_ROUTE_JSON_BYTES = 64 * 1024
APP_SHELL_MAX_STATE_JS_BYTES = 96 * 1024
APP_SHELL_STATE_DATA_ID = "viewspec-app-state-data"
APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES = 8 * 1024 * 1024
APP_SHELL_MAX_MANIFEST_BYTES = 256 * 1024
# <body>, <style> and <script> blocks are located with the linear block scanner shared with
# `viewspec check` (find_tag_blocks / iter_tag_regions), never with `<tag\b[^>]*>[\s\S]*?</tag>`
# regexes that re-scan to EOF from every unclosed opener.
#
# The surface patterns are applied by context (`_split_by_context`): tag patterns to tags, the
# handler rule to attribute names, the worker pattern to script blocks, and URL_SCHEME_RE to every
# context, so visible text such as "Nodes online = 12" is never read as markup. Markup that cannot
# be split by context is scanned whole, with every pattern.
HTML_FORBIDDEN_EMBED_RE = re.compile(r"<\s*(?:iframe|object|embed)\b", re.IGNORECASE)
HTML_FORBIDDEN_LINK_RE = re.compile(r"<\s*link\b", re.IGNORECASE)
HTML_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
HTML_WORKER_IMPORT_RE = re.compile(r"\b(?:Worker|SharedWorker|importScripts)\s*\(", re.IGNORECASE)
HTML_IMPORT_MAP_RE = re.compile(r"<script\b[^>]*type\s*=\s*['\"]importmap['\"]", re.IGNORECASE)
HTML_PROTOCOL_RELATIVE_RE = re.compile(r"(?i)(?:src|href|action|formaction|poster|srcset)\s*=\s*['\"]//")
# Matched against CSS normalised by `normalize_css_text`: escapes decoded, comments removed,
# whitespace stripped, lower-cased. Lists every URL-loading function plus remote schemes.
CSS_FORBIDDEN_RE = re.compile(
    r"@import|url\(|image-set\(|-webkit-image-set\(|image\(|src\(|expression\(|javascript:|vbscript:|data:|https?:|//"
)

_HTML_SPACE = r"[ \t\n\f\r]"
# One tag as the compiler writes it: the doctype, an end tag, or a start tag whose ASCII attribute
# names carry double-quoted values with no quote or angle bracket inside. In markup made only of
# these, the first ">" after a "<" ends the tag for a browser too, so cutting at "<" and ">" (the
# rule `_tag_source` applies for `viewspec check`) separates tags, attributes and text as a browser
# does. Possessive quantifiers keep a long malformed tag linear.
_COMPILER_TAG_RE = re.compile(
    rf"!doctype{_HTML_SPACE}++html{_HTML_SPACE}*+"
    rf"|/[a-z][a-z0-9-]*+{_HTML_SPACE}*+"
    rf"|(?P<name>[a-z][a-z0-9-]*+)"
    rf'(?P<attributes>(?:{_HTML_SPACE}++[a-z_:][a-z0-9_:.-]*+(?:="[^"<>]*+")?)*+){_HTML_SPACE}*+/?',
    re.ASCII | re.IGNORECASE,
)
_COMPILER_ATTRIBUTE_RE = re.compile(
    r'(?P<name>[a-z_:][a-z0-9_:.-]*)(?:="(?P<value>[^"<>]*)")?', re.ASCII | re.IGNORECASE
)
# The shell's route and state data blocks; the only script opener with attributes the shell writes.
_JSON_DATA_OPENER_RE = re.compile(r'<script type="application/json" id="[a-z0-9_-]+">')
# In SVG and MathML a script or style element holds markup, not raw text.
_FOREIGN_CONTENT_TAGS = frozenset({"svg", "math"})
_EVENT_HANDLER_NAME_RE = re.compile(r"on[a-z]+", re.ASCII | re.IGNORECASE)
_PROTOCOL_RELATIVE_ATTRIBUTES = ("src", "href", "action", "formaction", "poster", "srcset")
_REMOTE_SCHEME_RE = re.compile(r"https?:", re.ASCII | re.IGNORECASE)
# Attributes a browser shows or reads out as text; a bare "http:" in them is prose, not a fetch.
_TEXT_ATTRIBUTES = frozenset({"alt", "aria-label", "placeholder", "title", "value"})
_EXCERPT_LIMIT = 40
_EXCERPT_TOKEN_RE = re.compile(r"[^\s\"'<>;{}]*")
_UNSPLIT_MARKUP = "markup the scan cannot split by context"


class _Tag(NamedTuple):
    source: str
    attributes: tuple[tuple[str, str | None], ...]  # (lower-cased name, value as written or None)


class _ShellMarkup(NamedTuple):
    """A document split by context: its tags, its text, and its script, JSON data, and style blocks."""

    tags: tuple[_Tag, ...]
    text: tuple[str, ...]
    scripts: tuple[str, ...]
    data: tuple[str, ...]
    styles: tuple[str, ...]


def _split_by_context(html: str) -> _ShellMarkup | None:
    """Split markup the compiler wrote into tags, text, and raw-text blocks; None for other markup.

    Script and style blocks come from find_tag_blocks. A block counts only with the compiler's own
    opener and closer and with content a browser cannot end early or read as markup: no "</" or
    "<!--" in a script, no "<" in a style. Every other "<" must open a tag in the compiler's form
    (_COMPILER_TAG_RE), outside SVG and MathML.
    """
    blocks: list[tuple[int, int, int, int, str]] = []
    for kind in ("script", "style"):
        scan = find_tag_blocks(html, kind)
        if scan.unclosed_content_start is not None or scan.truncated:
            return None
        blocks.extend((*block, kind) for block in scan.blocks)
    scripts: list[str] = []
    data: list[str] = []
    styles: list[str] = []
    markup: list[str] = []
    position = previous_end = 0
    for start, content_start, content_end, end, kind in sorted(blocks):
        opener, content = html[start:content_start], html[content_start:content_end]
        if start < previous_end or html[content_end:end] != f"</{kind}>":
            return None
        if kind == "style":
            if opener != "<style>" or "<" in content:
                return None
            styles.append(content)
        elif "</" in content or "<!--" in content:
            return None
        elif opener == "<script>":
            scripts.append(content)
        elif _JSON_DATA_OPENER_RE.fullmatch(opener):
            data.append(content)
        else:
            return None
        markup.append(html[position:content_start])
        position, previous_end = content_end, end
    markup.append(html[position:])
    tags: list[_Tag] = []
    text: list[str] = []
    for piece in markup:
        leading, *spans = piece.split("<")
        text.append(leading)
        for span in spans:
            body, closed, trailing = span.partition(">")
            tag = _COMPILER_TAG_RE.fullmatch(body)
            if not closed or tag is None or (tag["name"] or "").lower() in _FOREIGN_CONTENT_TAGS:
                return None
            attributes = tuple(
                (attribute["name"].lower(), attribute["value"])
                for attribute in _COMPILER_ATTRIBUTE_RE.finditer(tag["attributes"] or "")
            )
            tags.append(_Tag(f"<{body}>", attributes))
            text.append(trailing)
    return _ShellMarkup(tuple(tags), tuple(text), tuple(scripts), tuple(data), tuple(styles))


def _excerpt(text: str, match: re.Match[str]) -> str:
    """The matched text run on to the end of its token, in at most 40 characters."""
    token = _EXCERPT_TOKEN_RE.match(text, match.end())
    end = token.end() if token is not None else match.end()
    return text[match.start() : end].strip()[:_EXCERPT_LIMIT]


def _first_match(pattern: re.Pattern[str], segments: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
    """(where, excerpt) for the first segment the pattern matches."""
    for where, text in segments:
        match = pattern.search(text)
        if match is not None:
            return where, _excerpt(text, match)
    return None


def _tag_segments(markup: _ShellMarkup) -> Iterator[tuple[str, str]]:
    return (("a tag", tag.source) for tag in markup.tags)


def _value_segments(markup: _ShellMarkup, *, skip: frozenset[str] = frozenset()) -> Iterator[tuple[str, str]]:
    return (
        ("an attribute", value) for tag in markup.tags for name, value in tag.attributes if value and name not in skip
    )


def _style_segments(markup: _ShellMarkup) -> Iterator[tuple[str, str]]:
    return (("a style block", style) for style in markup.styles)


def _first_attribute(markup: _ShellMarkup, matches: Callable[[str, str], bool]) -> tuple[str, str] | None:
    for tag in markup.tags:
        for name, value in tag.attributes:
            if value is not None and matches(name, value):
                return "an attribute", f'{name}="{value}"'[:_EXCERPT_LIMIT]
    return None


def _embed_tag(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(HTML_FORBIDDEN_EMBED_RE, _tag_segments(markup))


def _import_map_tag(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(HTML_IMPORT_MAP_RE, _tag_segments(markup))


def _link_tag(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(HTML_FORBIDDEN_LINK_RE, _tag_segments(markup))


def _worker_in_script(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(HTML_WORKER_IMPORT_RE, (("a script block", script) for script in markup.scripts))


def _url_anywhere(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(
        URL_SCHEME_RE,
        chain(
            _value_segments(markup),
            (("text", text) for text in markup.text),
            (("a script block", script) for script in markup.scripts),
            (("a JSON data block", data) for data in markup.data),
            _style_segments(markup),
        ),
    )


def _remote_scheme_outside_text(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_match(
        _REMOTE_SCHEME_RE, chain(_value_segments(markup, skip=_TEXT_ATTRIBUTES), _style_segments(markup))
    )


def _protocol_relative_attribute(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_attribute(
        markup, lambda name, value: value.startswith("//") and name.endswith(_PROTOCOL_RELATIVE_ATTRIBUTES)
    )


def _event_handler_attribute(markup: _ShellMarkup) -> tuple[str, str] | None:
    return _first_attribute(markup, lambda name, _value: _EVENT_HANDLER_NAME_RE.fullmatch(name) is not None)


class _SurfaceRule(NamedTuple):
    what: str
    whole_document: re.Pattern[str]  # applied to markup that cannot be split by context
    by_context: Callable[[_ShellMarkup], tuple[str, str] | None]


_EMBED_RULE = _SurfaceRule("an embed, object, or frame tag", HTML_FORBIDDEN_EMBED_RE, _embed_tag)
_IMPORT_MAP_RULE = _SurfaceRule("an import map", HTML_IMPORT_MAP_RE, _import_map_tag)
_LINK_RULE = _SurfaceRule("a link tag", HTML_FORBIDDEN_LINK_RE, _link_tag)
_WORKER_RULE = _SurfaceRule("worker construction", HTML_WORKER_IMPORT_RE, _worker_in_script)
_URL_RULE = _SurfaceRule("a URL", URL_SCHEME_RE, _url_anywhere)
_REMOTE_URL_RULE = _SurfaceRule("a remote URL", _REMOTE_SCHEME_RE, _remote_scheme_outside_text)
_PROTOCOL_RELATIVE_RULE = _SurfaceRule(
    "a protocol-relative URL", HTML_PROTOCOL_RELATIVE_RE, _protocol_relative_attribute
)
_EVENT_HANDLER_RULE = _SurfaceRule("an inline event handler", HTML_INLINE_HANDLER_RE, _event_handler_attribute)
# (code, rules, fix) in the order each scan reports them.
_SCREEN_SURFACE_CHECKS: tuple[tuple[str, tuple[_SurfaceRule, ...], str], ...] = (
    (
        "APP_SHELL_EMBEDDING_UNSUPPORTED",
        (_EMBED_RULE, _WORKER_RULE),
        "Remove embed/runtime surfaces before compiling the static shell.",
    ),
    (
        "APP_SHELL_NETWORK_SURFACE_REJECTED",
        (_LINK_RULE, _URL_RULE, _PROTOCOL_RELATIVE_RULE),
        "Remove external resources before compiling the static shell.",
    ),
    (
        "APP_SHELL_EMBEDDING_UNSUPPORTED",
        (_EVENT_HANDLER_RULE,),
        "Remove inline event handlers before compiling the static shell.",
    ),
)
_SHELL_SURFACE_CHECKS: tuple[tuple[str, tuple[_SurfaceRule, ...], str], ...] = (
    (
        "APP_SHELL_NETWORK_SURFACE_REJECTED",
        (_REMOTE_URL_RULE, _URL_RULE, _PROTOCOL_RELATIVE_RULE),
        "Remove external resources before compiling the static shell.",
    ),
    (
        "APP_SHELL_EMBEDDING_UNSUPPORTED",
        (_EMBED_RULE, _IMPORT_MAP_RULE, _WORKER_RULE),
        "Remove unsupported surfaces before compiling the static shell.",
    ),
    (
        "APP_SHELL_EMBEDDING_UNSUPPORTED",
        (_EVENT_HANDLER_RULE,),
        "Use the compiler-owned static route script only.",
    ),
)


def _reject_surfaces(html: str, subject: str, checks: tuple[tuple[str, tuple[_SurfaceRule, ...], str], ...]) -> None:
    """Raise for the first surface any check finds, naming what matched and in which context."""
    markup = _split_by_context(html)
    for code, rules, fix in checks:
        for rule in rules:
            found = (
                rule.by_context(markup)
                if markup is not None
                else _first_match(rule.whole_document, ((_UNSPLIT_MARKUP, html),))
            )
            if found is not None:
                where, excerpt = found
                raise AppBundleProofFailure(code, f"{subject} contains {rule.what} in {where}: {excerpt!r}.", fix)


def _unsafe_css_excerpt(css: str) -> str | None:
    """What CSS_FORBIDDEN_RE matched in either normalised view of the CSS, or None."""
    for variant in normalize_css_text(css):
        match = CSS_FORBIDDEN_RE.search(variant)
        if match is not None:
            return _excerpt(variant, match)
    return None


def _build_static_app_shell(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    *,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_assertions = _shell_route_assertions(payload)
    if not all(route_assertions.values()):
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Static shell route assertions failed before shell write.",
            "Fix the AppBundle static route graph and rerun viewspec compile.",
        )
    screen_payloads = _collect_shell_screens(screen_reports)
    aggregate_screen_html = sum(len(screen["fragment"].encode("utf-8")) for screen in screen_payloads)
    if aggregate_screen_html > APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Embedded checked screen HTML totals {aggregate_screen_html} bytes; limit is {APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    route_table = _shell_route_table(payload)
    route_json = _safe_json_for_script(
        {
            "app": {
                "id": payload["app"]["id"],
                "title": payload["app"]["title"],
                "kind": payload["app"]["kind"],
                "rootRoute": payload["app"]["root_route"],
            },
            "routes": route_table,
        }
    )
    route_json_bytes = len(route_json.encode("utf-8"))
    if route_json_bytes > APP_SHELL_MAX_ROUTE_JSON_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Serialized shell route table is {route_json_bytes} bytes; limit is {APP_SHELL_MAX_ROUTE_JSON_BYTES}.",
            "Reduce route labels or split the app into smaller AppBundles.",
        )
    presentation_plan = build_presentation_plan(payload)
    styles = _dedupe_styles(screen_payloads)
    styles.append(presentation_plan_css(presentation_plan))
    route_script = _app_shell_route_script()
    script_bytes = len(route_script.encode("utf-8"))
    if script_bytes > APP_SHELL_MAX_JS_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell JS is {script_bytes} bytes; limit is {APP_SHELL_MAX_JS_BYTES}.",
            "Reduce the shell runtime before compiling.",
        )
    state_json = ""
    state_scripts: tuple[str, ...] = ()
    state_runtime: dict[str, Any] | None = None
    if app_has_visibility(payload):
        # V4+ bounded state runtime: the generated reducer (IIFE) + ONE delegated click listener
        # dispatches declared mutations, toggles visibility, and applies exact scalar text projections.
        state_json = _safe_json_for_script({"triggers": _shell_state_trigger_table(payload)})
        reducer_script = generate_browser_reducer_script(payload)
        runtime_script = _app_shell_state_runtime_script()
        state_js_bytes = len(reducer_script.encode("utf-8")) + len(runtime_script.encode("utf-8"))
        if state_js_bytes > APP_SHELL_MAX_STATE_JS_BYTES:
            raise AppBundleProofFailure(
                "APP_SHELL_SIZE_LIMIT_EXCEEDED",
                f"Static shell state runtime is {state_js_bytes} bytes; limit is {APP_SHELL_MAX_STATE_JS_BYTES}.",
                "Reduce AppBundle V4/V5 state, mutation, selector, visibility, or state text declarations.",
            )
        state_scripts = (reducer_script, runtime_script)
        state_runtime = {
            "enabled": True,
            "listener_count": 1,
            "reducer_js_hash": source_hash(reducer_script),
            "runtime_js_hash": source_hash(runtime_script),
            "state_data_json_bytes": len(state_json.encode("utf-8")),
            "state_js_bytes": state_js_bytes,
            "state_text_rule_count": (
                len(payload.get("state_text", [])) if isinstance(payload.get("state_text"), list) else 0
            ),
        }
    html = _render_static_app_shell_html(
        payload, screen_payloads, styles, route_json, route_script, state_json=state_json, state_scripts=state_scripts
    )
    html_bytes = len(html.encode("utf-8"))
    if html_bytes > APP_SHELL_MAX_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell HTML is {html_bytes} bytes; limit is {APP_SHELL_MAX_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    _assert_rendered_shell_static_contract(html)
    manifest = _static_shell_manifest(
        payload,
        screen_reports,
        route_assertions,
        html_bytes,
        script_bytes,
        route_json_bytes,
        aggregate_screen_html,
        resource_binding_report=resource_binding_report,
        state_runtime=state_runtime,
        presentation_plan=presentation_plan,
    )
    return {
        "html": html,
        "manifest": manifest,
        "presentation_plan": presentation_plan,
        "presentation_plan_hash": presentation_plan_hash(presentation_plan),
        "route_assertions": route_assertions,
    }


def _collect_shell_screens(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    screens: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict) or screen.get("errors"):
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_FAILED",
                f"Screen {screen.get('id') if isinstance(screen, dict) else 'unknown'} is not a passed checked artifact.",
                "Fix screen validation, compile, and check errors before compiling the shell.",
            )
        paths = screen.get("paths") if isinstance(screen.get("paths"), dict) else {}
        artifact = Path(str(paths.get("artifact") or ""))
        if not artifact.exists():
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_ARTIFACT_MISSING",
                f"Screen {screen.get('id')} artifact is missing.",
                "Regenerate checked screen artifacts before compiling the shell.",
            )
        html = artifact.read_text(encoding="utf-8")
        screen_id = str(screen.get("id"))
        _assert_screen_artifact_shell_safe(html, screen_id)
        fragment = _extract_screen_body_fragment(html, screen_id)
        styles = _extract_screen_styles(html, screen_id)
        screens.append(
            {
                "id": screen_id,
                "title": str(screen.get("title") or screen_id),
                "fragment": fragment,
                "styles": styles,
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
            }
        )
    return screens


def _assert_screen_artifact_shell_safe(html: str, screen_id: str) -> None:
    # Any attributed <script> (including an import map, which needs type="importmap") is rejected here;
    # the regions come from the linear scan, not from `<script\b[^>]*>` over the whole document.
    for _start, attrs in iter_tag_regions(html, "script"):
        if attrs.strip():
            raise AppBundleProofFailure(
                "APP_SHELL_EMBEDDING_UNSUPPORTED",
                (
                    f"Screen {screen_id} contains an attributed script tag in a tag: "
                    f"{('<script' + attrs)[:_EXCERPT_LIMIT]!r}; Static Shell V0 embeds inert fragments only."
                ),
                "Remove import maps, external script references, or typed script surfaces before compiling the shell.",
            )
    _reject_surfaces(html, f"Screen {screen_id}", _SCREEN_SURFACE_CHECKS)
    for style in _extract_screen_styles(html, screen_id):
        excerpt = _unsafe_css_excerpt(style)
        if excerpt is not None:
            raise AppBundleProofFailure(
                "APP_SHELL_NETWORK_SURFACE_REJECTED",
                (
                    f"Screen {screen_id} contains an import, URL, expression, or script-like CSS value "
                    f"in a style block: {excerpt!r}."
                ),
                "Remove external or executable CSS surfaces before compiling the static shell.",
            )


def _scan_screen_blocks(html: str, tag: str, screen_id: str) -> TagBlockScan:
    """Linear `<tag>` block scan over a screen artifact that fails closed on unclosed or excess blocks."""
    scan = find_tag_blocks(html, tag)
    if scan.unclosed_content_start is not None:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains an unclosed <{tag}> block; Static Shell V0 embeds inert fragments only.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    if scan.truncated:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Screen {screen_id} contains more than {MAX_SCANNED_TAG_BLOCKS} <{tag}> blocks.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    return scan


def _extract_screen_body_fragment(html: str, screen_id: str) -> str:
    body_scan = find_tag_blocks(html, "body", limit=1)
    if not body_scan.blocks:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} artifact does not contain a body element.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    _start, content_start, content_end, _end = body_scan.blocks[0]
    body = html[content_start:content_end]
    script_scan = _scan_screen_blocks(body, "script", screen_id)
    pieces: list[str] = []
    position = 0
    for start, _content_start, _content_end, end in script_scan.blocks:
        pieces.append(body[position:start])
        position = end
    pieces.append(body[position:])
    fragment = "".join(pieces).strip()
    if not fragment:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} body is empty.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    return fragment


def _extract_screen_styles(html: str, screen_id: str) -> list[str]:
    scan = _scan_screen_blocks(html, "style", screen_id)
    styles = [html[content_start:content_end].strip() for _start, content_start, content_end, _end in scan.blocks]
    return [style for style in styles if style]


def _dedupe_styles(screen_payloads: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    styles: list[str] = []
    for screen in screen_payloads:
        for style in screen["styles"]:
            if style in seen:
                continue
            seen.add(style)
            styles.append(style)
    return styles


def _shell_route_table(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": str(route["id"]),
            "path": str(route["path"]),
            "label": str(route["label"]),
            "screenId": str(route["screen_id"]),
        }
        for route in payload["routes"]
    ]


def _shell_route_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    screen_ids = {screen.get("id") for screen in screens if isinstance(screen, dict)}
    route_paths = [route.get("path") for route in routes if isinstance(route, dict)]
    route_screen_ids = [route.get("screen_id") for route in routes if isinstance(route, dict)]
    root_route = payload.get("app", {}).get("root_route") if isinstance(payload.get("app"), dict) else None
    return {
        "every_route_maps_exactly_one_screen": all(
            route_screen_ids.count(screen_id) >= 1 and screen_id in screen_ids for screen_id in route_screen_ids
        ),
        "every_screen_has_route": all(screen_id in route_screen_ids for screen_id in screen_ids),
        "root_route_selects_exactly_one_screen": route_paths.count(root_route) == 1,
        "unknown_route_selects_no_screen_and_one_404": True,
    }


def _render_static_app_shell_html(
    payload: dict[str, Any],
    screen_payloads: list[dict[str, Any]],
    styles: list[str],
    route_json: str,
    route_script: str,
    *,
    state_json: str = "",
    state_scripts: tuple[str, ...] = (),
) -> str:
    root_route = payload["app"]["root_route"]
    route_by_screen = {route["screen_id"]: route["path"] for route in payload["routes"]}
    root_screen_id = next(route["screen_id"] for route in payload["routes"] if route["path"] == root_route)
    screen_sections: list[str] = []
    for screen in screen_payloads:
        screen_id = screen["id"]
        selected = screen_id == root_screen_id
        screen_sections.extend(
            [
                (
                    f'<section class="vs-app-screen" data-viewspec-app-screen="{escape(screen_id, quote=True)}" '
                    f'data-route-path="{escape(str(route_by_screen.get(screen_id, "")), quote=True)}" '
                    f'data-selected="{"true" if selected else "false"}"'
                    f"{' hidden' if not selected else ''}>"
                ),
                screen["fragment"],
                "</section>",
            ]
        )
    style_text = "\n\n".join([_app_shell_css(), *styles])
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>ViewSpec Static App</title>",
            "<style>",
            style_text,
            "</style>",
            "</head>",
            '<body data-viewspec-app-shell="static_shell_v0">',
            '<div class="vs-app-shell">',
            '<header class="vs-app-chrome">',
            '<div class="vs-app-title-block">',
            '<p class="vs-app-kicker">ViewSpec App Shell</p>',
            '<h1 id="vs-app-title" class="vs-app-title"></h1>',
            "</div>",
            '<nav id="vs-app-nav" class="vs-app-nav" aria-label="App routes"></nav>',
            "</header>",
            '<main id="vs-app-main" class="vs-app-main">',
            *screen_sections,
            '<section class="vs-app-404" data-viewspec-app-404 hidden>',
            '<p class="vs-app-kicker">Route unavailable</p>',
            "<h2>Unknown route</h2>",
            "<p>The selected local route is not declared in this AppBundle.</p>",
            "</section>",
            "</main>",
            "</div>",
            f'<script type="application/json" id="viewspec-app-route-data">{route_json}</script>',
            "<script>",
            route_script,
            "</script>",
            *(
                [f'<script type="application/json" id="{APP_SHELL_STATE_DATA_ID}">{state_json}</script>']
                if state_json
                else []
            ),
            *(item for script in state_scripts for item in ("<script>", script, "</script>")),
            "</body>",
            "</html>",
            "",
        ]
    )


def _app_shell_css() -> str:
    return """
.vs-app-shell {
  min-height: 100vh;
  background: #eef2f7;
  color: #0f172a;
}
.vs-app-chrome {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid #cbd5e1;
  background: #ffffff;
}
.vs-app-title-block { min-width: 0; }
.vs-app-kicker {
  margin: 0 0 3px;
  color: #64748b;
  font-size: 0.72rem;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.vs-app-title {
  margin: 0;
  color: #0f172a;
  font-size: 1.05rem;
  line-height: 1.2;
}
.vs-app-nav {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.vs-app-route-button {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #334155;
  padding: 7px 10px;
  font: inherit;
  font-size: 0.86rem;
  font-weight: 800;
  line-height: 1.25;
  cursor: pointer;
}
.vs-app-route-button[aria-current="page"] {
  border-color: #0f766e;
  background: #0f766e;
  color: #ffffff;
}
.vs-app-main { min-height: calc(100vh - 66px); }
.vs-app-screen[hidden], .vs-app-404[hidden] { display: none !important; }
.vs-app-404 {
  width: min(100%, 760px);
  margin: 42px auto;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff1f2;
  color: #7f1d1d;
  padding: 24px;
}
@media (max-width: 760px) {
  .vs-app-chrome { align-items: stretch; flex-direction: column; }
  .vs-app-nav { justify-content: flex-start; }
}
""".strip()


def _app_shell_route_script() -> str:
    return """
(() => {
  const dataEl = document.getElementById('viewspec-app-route-data');
  const payload = JSON.parse(dataEl.textContent || '{}');
  const routes = Array.isArray(payload.routes) ? payload.routes : [];
  const app = payload.app || {};
  const rootRoute = typeof app.rootRoute === 'string' ? app.rootRoute : '/';
  const titleEl = document.getElementById('vs-app-title');
  const navEl = document.getElementById('vs-app-nav');
  const notFoundEl = document.querySelector('[data-viewspec-app-404]');
  const screens = Array.from(document.querySelectorAll('[data-viewspec-app-screen]'));
  const routeByPath = new Map(routes.map((route) => [route.path, route]));

  function hashPath() {
    const raw = window.location.hash.slice(1);
    if (!raw) return rootRoute;
    return raw.startsWith('/') ? raw : `/${raw}`;
  }

  function setRoute(path) {
    const route = routeByPath.get(path);
    const known = Boolean(route);
    let selectedCount = 0;
    screens.forEach((screen) => {
      const selected = known && screen.dataset.viewspecAppScreen === route.screenId;
      screen.hidden = !selected;
      screen.dataset.selected = selected ? 'true' : 'false';
      if (selected) selectedCount += 1;
    });
    if (notFoundEl) notFoundEl.hidden = known;
    Array.from(navEl.querySelectorAll('[data-route-path]')).forEach((button) => {
      button.setAttribute('aria-current', button.dataset.routePath === path && known ? 'page' : 'false');
    });
    const appTitle = typeof app.title === 'string' ? app.title : 'ViewSpec App';
    titleEl.textContent = appTitle;
    document.title = known ? `${appTitle} - ${route.label}` : `${appTitle} - Unknown route`;
    document.body.dataset.routeKnown = known ? 'true' : 'false';
    document.body.dataset.selectedScreenCount = String(selectedCount);
  }

  routes.forEach((route) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'vs-app-route-button';
    button.dataset.routePath = route.path;
    button.textContent = route.label;
    button.addEventListener('click', () => {
      window.location.hash = route.path;
      setRoute(route.path);
    });
    navEl.appendChild(button);
  });

  window.addEventListener('hashchange', () => setRoute(hashPath()));
  setRoute(hashPath());
})();
""".strip()


def _shell_state_trigger_table(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """(screenId, actionId) -> declared-order mutation ids, mirroring the replay's event order."""
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for mutation in payload.get("mutations", []) if isinstance(payload.get("mutations"), list) else []:
        if not isinstance(mutation, dict):
            continue
        trigger = mutation.get("trigger") if isinstance(mutation.get("trigger"), dict) else {}
        key = (str(trigger.get("screen_id")), str(trigger.get("action_id")))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(str(mutation.get("id")))
    return [
        {"screenId": screen_id, "actionId": action_id, "mutationIds": grouped[(screen_id, action_id)]}
        for screen_id, action_id in order
    ]


def _app_shell_state_runtime_script() -> str:
    # ONE delegated click listener. Payload harvesting mirrors the html emitter's
    # dispatchViewSpecAction (declared data-payload-bindings only, [data-binding-id] values,
    # scoped to the screen section). A failed mutation halts the click's remaining mutations
    # (mirroring Python replay) and marks the section data-viewspec-state-halted (SC-V5).
    # NAMING RULE: no identifier may start with "on", and no http/url(/Worker( tokens: the shell
    # scan reads script blocks for worker construction and URLs, and reads markup it cannot split
    # by context whole, with every pattern (\son[a-z]+\s*= included).
    return """(() => {
  const dataEl = document.getElementById('viewspec-app-state-data');
  if (!dataEl || typeof ViewSpecStateRuntime === 'undefined') return;
  let table = {};
  try { table = JSON.parse(dataEl.textContent || '{}'); } catch { table = {}; }
  const triggers = new Map(
    (Array.isArray(table.triggers) ? table.triggers : [])
      .map((t) => [`${t.screenId}::${t.actionId}`, Array.isArray(t.mutationIds) ? t.mutationIds : []])
  );
  let state = ViewSpecStateRuntime.initialState;
  const applyVisibility = () => {
    const verdicts = ViewSpecStateRuntime.evaluateViewSpecVisibility(state);
    document.querySelectorAll('[data-visibility-rule]').forEach((el) => {
      const ruleId = el.dataset.visibilityRule;
      if (!Object.prototype.hasOwnProperty.call(verdicts, ruleId)) return;
      const visible = verdicts[ruleId] === true;
      el.hidden = !visible;
      el.dataset.visibilityState = visible ? 'visible' : 'hidden';
    });
  };
  const applyStateText = () => {
    if (typeof ViewSpecStateRuntime.evaluateViewSpecText !== 'function') return;
    const values = ViewSpecStateRuntime.evaluateViewSpecText(state);
    document.querySelectorAll('[data-state-text-id]').forEach((el) => {
      const ruleId = el.dataset.stateTextId;
      if (!Object.prototype.hasOwnProperty.call(values, ruleId)) return;
      el.textContent = values[ruleId];
    });
  };
  document.addEventListener('click', (e) => {
    const target = e.target instanceof Element ? e.target : null;
    const btn = target ? target.closest('[data-action-id]') : null;
    if (!btn) return;
    const section = btn.closest('[data-viewspec-app-screen]');
    if (!section) return;
    const mutationIds = triggers.get(`${section.dataset.viewspecAppScreen}::${btn.dataset.actionId}`) || [];
    if (!mutationIds.length) return;
    let bindings = [];
    try {
      const parsed = JSON.parse(btn.dataset.payloadBindings || '[]');
      if (Array.isArray(parsed)) bindings = parsed.filter((id) => typeof id === 'string');
    } catch { bindings = []; }
    const requested = new Set(bindings);
    const values = {};
    section.querySelectorAll('[data-binding-id]').forEach((el) => {
      const id = el.dataset.bindingId;
      if (!requested.has(id)) return;
      values[id] = 'value' in el ? el.value : el.textContent || '';
    });
    delete section.dataset.viewspecStateHalted;
    for (const mutationId of mutationIds) {
      try {
        state = ViewSpecStateRuntime.reduceViewSpecState(state, { mutation_id: mutationId, payload_values: values });
      } catch {
        section.dataset.viewspecStateHalted = mutationId;
        break;
      }
    }
    applyVisibility();
    applyStateText();
  });
  applyVisibility();
  applyStateText();
})();"""


def _safe_json_for_script(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _assert_rendered_shell_static_contract(html: str) -> None:
    main_count = len(re.findall(r"<main\b", html, flags=re.IGNORECASE))
    embedded_root_count = len(re.findall(r"\bdata-viewspec-screen-root=[\"']embedded[\"']", html, flags=re.IGNORECASE))
    if main_count != 1 or embedded_root_count < 1:
        raise AppBundleProofFailure(
            "APP_SEMANTIC_LANDMARK_INVALID",
            (
                "Static AppBundle shell must contain exactly one main landmark and at least one neutral embedded "
                f"screen root; found {main_count} main landmarks and {embedded_root_count} embedded roots."
            ),
            "Regenerate the app with one shell-owned main and neutral embedded screen roots.",
        )
    if len(re.findall(r"<section\b[^>]*\bdata-viewspec-app-404\b", html, flags=re.IGNORECASE)) != 1:
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Rendered shell must contain exactly one local 404 panel.",
            "Regenerate the shell from the validated AppBundle route graph.",
        )
    _reject_surfaces(html, "Rendered shell", _SHELL_SURFACE_CHECKS)


def _static_shell_manifest(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    route_assertions: dict[str, bool],
    shell_html_bytes: int,
    shell_js_bytes: int,
    route_json_bytes: int,
    aggregate_screen_html_bytes: int,
    *,
    resource_binding_report: dict[str, Any] | None = None,
    state_runtime: dict[str, Any] | None = None,
    presentation_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "kind": "app_static_shell_compile",
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
        **_resource_binding_report_fields(payload, resource_binding_report),
        "policy": {"network_calls": "none"},
        "app": _app_summary(payload),
        "routes": _shell_route_table(payload),
        "route_assertions": route_assertions,
        "presentation_plan": {
            "path": "presentation_plan.json",
            "plan_hash": presentation_plan_hash(presentation_plan),
            "screen_count": len(presentation_plan.get("screens", [])),
            "diagnostics": presentation_plan_diagnostics(presentation_plan),
            "sources": {
                str(screen.get("id")): str(screen.get("source"))
                for screen in presentation_plan.get("screens", [])
                if isinstance(screen, dict)
            },
        },
        "resource_repeat": resource_repeat_summary(payload),
        "screens": _screen_shell_summaries(screen_reports),
        "limits": _app_shell_limits(),
        "sizes": {
            "shell_html_bytes": shell_html_bytes,
            "shell_js_bytes": shell_js_bytes,
            "route_json_bytes": route_json_bytes,
            "aggregate_screen_html_bytes": aggregate_screen_html_bytes,
        },
        # v4+ block: v3 shell manifests stay byte-stable.
        **({"state_runtime": state_runtime} if state_runtime is not None else {}),
    }


def _screen_shell_summaries(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict):
            continue
        summaries.append(
            {
                "id": screen.get("id"),
                "title": screen.get("title"),
                "validation_status": screen.get("validation_status"),
                "compile_status": screen.get("compile_status"),
                "check_status": screen.get("check_status"),
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
                "manifest_summary": screen.get("manifest_summary"),
            }
        )
    return summaries


def _app_shell_limits() -> dict[str, int]:
    return {
        "max_screens": APP_BUNDLE_MAX_SCREENS,
        "max_routes": APP_BUNDLE_MAX_ROUTES,
        "max_shell_html_bytes": APP_SHELL_MAX_HTML_BYTES,
        "max_shell_js_bytes": APP_SHELL_MAX_JS_BYTES,
        "max_route_json_bytes": APP_SHELL_MAX_ROUTE_JSON_BYTES,
        "max_aggregate_screen_html_bytes": APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES,
        "max_external_network_surfaces": 0,
        "max_dynamic_route_features": 0,
        "max_third_party_executable_surfaces": 0,
        "max_generated_framework_files": 0,
        **_state_ir_limits(),
        **_resource_binding_limits(),
    }


__all__ = [
    "APP_SHELL_DEFAULT_OUT",
    "APP_SHELL_DIAGNOSTICS",
    "APP_SHELL_DIR_NAME",
    "APP_SHELL_INDEX",
    "APP_SHELL_MANIFEST",
    "APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES",
    "APP_SHELL_MAX_HTML_BYTES",
    "APP_SHELL_MAX_JS_BYTES",
    "APP_SHELL_MAX_MANIFEST_BYTES",
    "APP_SHELL_MAX_ROUTE_JSON_BYTES",
    "APP_SHELL_ROUTE_NAVIGATION",
    "APP_SHELL_TARGET",
    "CSS_FORBIDDEN_RE",
    "HTML_FORBIDDEN_EMBED_RE",
    "HTML_FORBIDDEN_LINK_RE",
    "HTML_IMPORT_MAP_RE",
    "HTML_INLINE_HANDLER_RE",
    "HTML_PROTOCOL_RELATIVE_RE",
    "HTML_WORKER_IMPORT_RE",
    "_app_shell_css",
    "_app_shell_limits",
    "_app_shell_route_script",
    "_assert_rendered_shell_static_contract",
    "_assert_screen_artifact_shell_safe",
    "_build_static_app_shell",
    "_collect_shell_screens",
    "_dedupe_styles",
    "_extract_screen_body_fragment",
    "_extract_screen_styles",
    "_render_static_app_shell_html",
    "_safe_json_for_script",
    "_screen_shell_summaries",
    "_shell_route_assertions",
    "_shell_route_table",
    "_static_shell_manifest",
]
