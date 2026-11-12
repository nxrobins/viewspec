"""Raw HTML lift, compile, and semantic diff helpers for the local SDK."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from viewspec._version import __version__
from viewspec.design_md import DesignSystemContext, load_design_system
from viewspec.emitters._write import write_artifact_set
from viewspec.raw_html_agent import (
    AGENT_HINT_ATTR_PREFIX,
    AGENT_VIEWSPEC_DATA_ATTRS,
    HINT_SECRET,
    HtmlControl,
    HtmlForm,
    HtmlMessage,
    extract_controls,
    form_tool_names,
    form_tool_schema,
    is_sensitive_control,
    label_secret_ids,
    names_a_secret,
)

MAX_HTML_INPUT_BYTES = 5_000_000
# Maximum kept-element nesting depth. The parser is stack-based, but the
# downstream freeze/render passes recurse per DOM level, so a deeply nested (yet
# small) document would raise a Python RecursionError. Reject during parse
# instead, mirroring the MAX_HTML_INPUT_BYTES rejection.
MAX_DOM_DEPTH = 256
MANIFEST_SCHEMA_VERSION = 1
RAW_HTML_POLICY_VERSION = "viewspec-raw-html-allowlist@1"
DIFF_BASIS = "lift_v1"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
TRANSPARENT_TAGS = {"html", "body"}
ALLOWED_TAGS = {
    "a",
    "abbr",
    "article",
    "aside",
    "b",
    "blockquote",
    "br",
    "button",
    "caption",
    "code",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "img",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
STRIP_WITH_CONTENT_TAGS = {
    "audio",
    "base",
    "canvas",
    "embed",
    "head",
    "iframe",
    "link",
    "math",
    "meta",
    "noscript",
    "object",
    "picture",
    "script",
    "source",
    "style",
    "svg",
    "template",
    "track",
    "video",
}
UNWRAP_TAGS = {"form", "fieldset", "label", "legend"}
REGION_TAGS = {"main", "section", "article", "aside", "nav", "header", "footer", "div", "ul", "ol", "table"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
ACTION_TAGS = {"a", "button"}
TEXT_TAGS = {"p", "span", "strong", "em", "b", "i", "small", "code", "li", "td", "th"}
SAFE_GLOBAL_ATTRS = {"aria-label", "alt", "title"}
SAFE_ATTRS_BY_TAG = {
    "a": {"href", "rel"},
    "button": {"type"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start"},
}
URL_ATTRS = {"href", "src"}
SAFE_SCHEMES_BY_ATTR = {
    "href": {"http", "https", "mailto"},
    "src": {"data", "http", "https"},
    "action": {"http", "https"},
}

# The "agent" lift profile keeps what a browser agent needs to read and operate a page: form
# controls, labels, ARIA roles, tab stops, and ViewSpec action ids. It is a lift-only profile;
# compile_html() always uses the "compile" profile, so compiled artifacts and their hashes do not
# change. Script, style, event handlers, and unsafe URLs are still removed.
LIFT_PROFILES = ("compile", "agent")
AGENT_FORM_TAGS = {"fieldset", "form", "input", "label", "legend", "optgroup", "option", "output", "select", "textarea"}
AGENT_ALLOWED_TAGS = ALLOWED_TAGS | AGENT_FORM_TAGS
AGENT_UNWRAP_TAGS = UNWRAP_TAGS - AGENT_FORM_TAGS
AGENT_GLOBAL_ATTRS = SAFE_GLOBAL_ATTRS | {"contenteditable", "hidden", "id", "role", "tabindex"}
AGENT_ATTRS_BY_TAG = {
    **SAFE_ATTRS_BY_TAG,
    "a": SAFE_ATTRS_BY_TAG["a"] | {"download"},
    "button": {"disabled", "form", "name", "type", "value"},
    "fieldset": {"disabled", "name"},
    "form": {"action", "method", "name"},
    "input": {
        "accept",
        "autocomplete",
        "checked",
        "disabled",
        "form",
        "inputmode",
        "max",
        "maxlength",
        "min",
        "minlength",
        "multiple",
        "name",
        "pattern",
        "placeholder",
        "readonly",
        "required",
        "step",
        "type",
        "value",
    },
    "label": {"for"},
    "optgroup": {"disabled", "label"},
    "option": {"disabled", "label", "selected", "value"},
    "output": {"for", "form", "name"},
    "select": {"disabled", "form", "multiple", "name", "required"},
    "textarea": {
        "autocomplete",
        "disabled",
        "form",
        "maxlength",
        "minlength",
        "name",
        "placeholder",
        "readonly",
        "required",
    },
}
AGENT_URL_ATTRS = URL_ATTRS | {"action"}
REDACTED_VALUE = "[redacted]"
# The only data: URLs the sanitizer keeps (inline raster images on <img src>). Shared with the
# `check` URL-scheme gate so the two cannot drift on which data: payloads count as inert.
DATA_IMAGE_SRC_PREFIXES = ("data:image/png", "data:image/jpeg", "data:image/gif", "data:image/webp", "data:image/avif")
NUMERIC_RE = re.compile(r"^[+-]?(?:\$)?\d[\d,]*(?:\.\d+)?(?:%|[KMB])?$", re.IGNORECASE)
LABEL_RE = re.compile(r"^[A-Z][A-Za-z0-9][A-Za-z0-9 .,&:/()%'_-]{1,58}$")
CONTROL_WHITESPACE_RE = re.compile(r"[\x00-\x20\x7f]+")
WS_RE = re.compile(r"\s+")
# Browsers treat backslashes and percent-encoded backslashes as forward slashes
# when resolving URLs, so values like "/\evil.com" or "\\evil.com" resolve to a
# protocol-relative (cross-origin) URL. Collapse them before authority detection.
BACKSLASH_URL_RE = re.compile(r"%5c|\\", re.IGNORECASE)


class HtmlInputError(ValueError):
    """Raised when raw HTML cannot enter the local pipeline."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _HTMLNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_HTMLNode] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)
    # For each text chunk, how many children came before it: the place of the text among them.
    text_slots: list[int] = field(default_factory=list)
    redact_text: bool = False

    def add_text(self, text: str) -> None:
        self.text_chunks.append(text)
        self.text_slots.append(len(self.children))


@dataclass(frozen=True)
class LiftNode:
    id: str
    tag: str
    attrs: dict[str, str]
    text: str
    path: tuple[int, ...]
    child_ids: tuple[str, ...]
    parent_id: str | None
    # The node's own text split at its children: run i comes just before child i, and the last run
    # after the last child. Runs of whitespace are one space, kept at the edges, so words on either
    # side of a child stay apart. Empty when the node has no text. ``text`` holds the same words,
    # so ``to_json`` leaves this out.
    text_runs: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tag": self.tag,
            "attrs": dict(self.attrs),
            "text": self.text,
            "path": list(self.path),
            "child_ids": list(self.child_ids),
            "parent_id": self.parent_id,
        }


@dataclass(frozen=True)
class HtmlRole:
    node_id: str
    role: str
    text: str
    confidence: float

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class HtmlTopologyFingerprint:
    hex: str
    path_ngrams: tuple[str, ...]
    token_count: int
    ngram_length: int

    def to_json(self) -> dict[str, Any]:
        return {
            "hex": self.hex,
            "path_ngrams": list(self.path_ngrams),
            "token_count": self.token_count,
            "ngram_length": self.ngram_length,
        }


@dataclass(frozen=True)
class HtmlLiftResult:
    source_name: str | None
    source_hash: str
    nodes: tuple[LiftNode, ...]
    roles: tuple[HtmlRole, ...]
    region_node_ids: tuple[str, ...]
    group_candidates: tuple[tuple[str, ...], ...]
    topology_fingerprint: HtmlTopologyFingerprint
    diagnostics: tuple[dict[str, str], ...]
    profile: str = "compile"
    controls: tuple[HtmlControl, ...] = ()
    forms: tuple[HtmlForm, ...] = ()
    messages: tuple[HtmlMessage, ...] = ()

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_name": self.source_name,
            "source_hash": self.source_hash,
            "nodes": [node.to_json() for node in self.nodes],
            "roles": [role.to_json() for role in self.roles],
            "region_node_ids": list(self.region_node_ids),
            "group_candidates": [list(group) for group in self.group_candidates],
            "topology_fingerprint": self.topology_fingerprint.to_json(),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }
        if self.profile != "compile":
            payload["profile"] = self.profile
            payload["controls"] = [control.to_json() for control in self.controls]
            payload["forms"] = [form.to_json() for form in self.forms]
            payload["messages"] = [message.to_json() for message in self.messages]
        return payload

    def form_tool_schemas(self) -> list[dict[str, Any]]:
        """Describe each form as a tool an agent can call: typed inputs plus the submit controls.

        Tool names are unique within the page. Only the "agent" profile finds forms; for the
        "compile" profile this is empty.
        """

        controls = {control.node_id: control for control in self.controls}
        return [
            form_tool_schema(form, controls, name=name)
            for form, name in zip(self.forms, form_tool_names(self.forms), strict=True)
        ]

    def control(self, node_id: str) -> HtmlControl:
        """The control with this node id. Raises ``KeyError`` when there is none."""

        for control in self.controls:
            if control.node_id == node_id:
                return control
        raise KeyError(node_id)


@dataclass(frozen=True)
class HtmlCompileResult:
    html: str
    manifest: dict[str, Any]
    diagnostics: tuple[dict[str, str], ...]
    lift: HtmlLiftResult


class _SanitizingHTMLParser(HTMLParser):
    def __init__(self, profile: str = "compile") -> None:
        super().__init__(convert_charrefs=True)
        self.agent = profile == "agent"
        self.allowed_tags = AGENT_ALLOWED_TAGS if self.agent else ALLOWED_TAGS
        self.unwrap_tags = AGENT_UNWRAP_TAGS if self.agent else UNWRAP_TAGS
        self.root = _HTMLNode("document")
        self.stack: list[_HTMLNode] = [self.root]
        self.skipped_tags: list[str] = []
        self.diagnostics: list[dict[str, str]] = []
        self.external_refs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skipped_tags:
            # Pop back to the matching open tag so one unclosed descendant inside
            # stripped content cannot swallow the rest of the document.
            for index in range(len(self.skipped_tags) - 1, -1, -1):
                if self.skipped_tags[index] == tag:
                    del self.skipped_tags[index:]
                    break
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self.skipped_tags and data:
            node = self.stack[-1]
            if node.redact_text:
                if data.strip() and not node.text_chunks:
                    node.add_text(REDACTED_VALUE)
                return
            node.add_text(data)

    def handle_comment(self, data: str) -> None:
        return

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        tag = tag.lower()
        if self.skipped_tags:
            if not self_closing and tag not in VOID_TAGS:
                self.skipped_tags.append(tag)
            return
        if tag in STRIP_WITH_CONTENT_TAGS:
            if tag in VOID_TAGS or self_closing:
                self._diagnostic("warning", "HTML_TAG_STRIPPED", f"Removed <{tag}>.")
                return
            self.skipped_tags.append(tag)
            self._diagnostic("warning", "HTML_TAG_STRIPPED", f"Removed <{tag}> and its contents.")
            return
        if self.agent and tag not in TRANSPARENT_TAGS and tag not in self.allowed_tags and _marks_hidden(attrs):
            # An unwrapped element keeps no attributes, so its hidden mark would be lost and its
            # content would look visible. The user cannot see it, so drop it with its content.
            if not self_closing and tag not in VOID_TAGS:
                self.skipped_tags.append(tag)
            return
        if tag in TRANSPARENT_TAGS or tag in self.unwrap_tags:
            return
        if tag not in self.allowed_tags:
            self._diagnostic(
                "warning", "HTML_TAG_UNWRAPPED", f"Removed unsupported <{tag}> wrapper but kept text children."
            )
            return
        if self.agent:
            if tag == "input" and any(
                name.lower() == "type" and (value or "").strip().lower() == "hidden" for name, value in attrs
            ):
                self._diagnostic("info", "HTML_HIDDEN_INPUT_DROPPED", "Removed a hidden <input> and its value.")
                return
            self._close_implied_option(tag)

        if len(self.stack) >= MAX_DOM_DEPTH:
            raise HtmlInputError(
                "HTML_DOM_DEPTH_EXCEEDED",
                f"HTML nesting depth exceeds the {MAX_DOM_DEPTH} level local limit.",
            )
        if self.agent:
            node = _HTMLNode(tag, _sanitize_agent_attrs(tag, attrs, self.diagnostics, self.external_refs))
            node.redact_text = tag == "textarea" and is_sensitive_control(tag, node.attrs)
            if node.redact_text:
                self._diagnostic("info", "HTML_VALUE_REDACTED", "Redacted the text of a secret <textarea> field.")
        else:
            node = _HTMLNode(tag, _sanitize_attrs(tag, attrs, self.diagnostics, self.external_refs))
        self.stack[-1].children.append(node)
        if not self_closing and tag not in VOID_TAGS:
            self.stack.append(node)

    def _close_implied_option(self, tag: str) -> None:
        # HTML closes an open <option> when the next <option> or <optgroup> starts, and an open
        # <optgroup> when the next <optgroup> starts. The stack parser has no implied end tags,
        # so close them here; otherwise each option would nest inside the one before it.
        if tag not in {"option", "optgroup"}:
            return
        closes = {"option"} if tag == "option" else {"option", "optgroup"}
        while len(self.stack) > 1 and self.stack[-1].tag in closes:
            self.stack.pop()

    def _diagnostic(self, severity: str, code: str, message: str) -> None:
        self.diagnostics.append({"severity": severity, "code": code, "message": message})


def lift_html(
    html: str, source_name: str | None = None, *, ngram_length: int = 4, profile: str = "compile"
) -> HtmlLiftResult:
    """Lift raw HTML into deterministic local signals.

    This is not ViewSpec decompilation. It returns canonical DOM, roles,
    repeated groups, and topology signals that the local compiler and diff
    command can use without any network or model calls.

    ``profile="compile"`` (the default) is the sanitizer policy of ``compile_html()``.
    ``profile="agent"`` is for agents that read and operate a rendered page. It also keeps form
    controls, labels, ARIA roles, tab stops, and ViewSpec ``data-action-*`` attributes; it adds
    ``action`` and ``field`` roles, ``controls``, and ``forms``; and it does not keep a hidden input's
    value or the value of a field whose type, ``autocomplete``, name, or label marks it as a secret. A browser-side snapshot can add
    ``data-lift-hidden`` and ``data-lift-clickable`` hints, which this profile reads.
    """

    if profile not in LIFT_PROFILES:
        raise ValueError(f"profile must be one of {', '.join(LIFT_PROFILES)}")
    _check_html_size(html)
    parser = _SanitizingHTMLParser(profile)
    parser.feed(html)
    parser.close()
    nodes = _freeze_nodes(parser.root)
    if profile == "agent":
        nodes = _redact_labelled_secrets(nodes, parser.diagnostics)
    source_hash = hashlib.sha256("\n".join(_source_token(node) for node in nodes).encode("utf-8")).hexdigest()
    controls: tuple[HtmlControl, ...] = ()
    forms: tuple[HtmlForm, ...] = ()
    messages: tuple[HtmlMessage, ...] = ()
    if profile == "agent":
        extraction = extract_controls(nodes)
        controls, forms, messages = extraction.controls, extraction.forms, extraction.messages
        content_roles = [
            role
            for role in _infer_roles([node for node in nodes if node.id not in extraction.excluded_ids])
            if role.role != "action"
        ]
        control_roles = [HtmlRole(*role) for role in extraction.roles]
        order = {node.id: index for index, node in enumerate(nodes)}
        roles = tuple(sorted([*content_roles, *control_roles], key=lambda role: order[role.node_id]))
    else:
        roles = _infer_roles(nodes)
    groups = _detect_group_candidates(nodes)
    regions = tuple(node.id for node in nodes if _is_region(node))
    fingerprint = _fingerprint(nodes, ngram_length=ngram_length)
    return HtmlLiftResult(
        source_name=source_name,
        source_hash=source_hash,
        nodes=tuple(nodes),
        roles=roles,
        region_node_ids=regions or (nodes[0].id if nodes else "dom_empty",),
        group_candidates=groups,
        topology_fingerprint=fingerprint,
        diagnostics=_dedupe_diagnostics(parser.diagnostics),
        profile=profile,
        controls=controls,
        forms=forms,
        messages=messages,
    )


def compile_html(
    html: str,
    design: DesignSystemContext | os.PathLike[str] | None = None,
    title: str | None = None,
    *,
    source_name: str | None = None,
    command_args: list[str] | tuple[str, ...] | None = None,
) -> HtmlCompileResult:
    """Compile raw HTML into a governed standalone HTML artifact.

    ``design`` is a ``DesignSystemContext`` from ``load_design_system()`` or, as for ``compile()``,
    an ``os.PathLike`` such as ``pathlib.Path("DESIGN.md")``, read as a DESIGN.md file.
    """

    _check_html_size(html)
    if isinstance(design, os.PathLike):
        design = load_design_system(path=Path(design))
    raw_source_hash = _sha256_text(html)
    parser = _SanitizingHTMLParser()
    parser.feed(html)
    parser.close()
    lift = lift_html(html, source_name=source_name)
    rendered_body, node_manifest = _render_sanitized_children(parser.root, ())
    document_title = title or _first_heading(lift) or "ViewSpec Artifact"
    css = _governed_stylesheet(design)
    output_html = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(document_title)}</title>",
            f"<style>{css}</style>",
            "</head>",
            "<body>",
            f'<div class="vs-artifact" data-viewspec-root="true" data-source-hash="{lift.source_hash}">',
            rendered_body,
            "</div>",
            "</body>",
            "</html>",
        ]
    )
    diagnostics = _dedupe_diagnostics([*parser.diagnostics, *lift.diagnostics])
    manifest = {
        "version": 1,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "raw_html_compile",
        "sdk_version": __version__,
        "source_name": source_name,
        "raw_source_hash": raw_source_hash,
        "source_hash": lift.source_hash,
        "design_hash": design.design_hash if design else None,
        "artifact_hash": _sha256_text(output_html),
        "command": "compile_html",
        "command_args": list(command_args or ("compile_html",)),
        "policy_version": RAW_HTML_POLICY_VERSION,
        "guarantees": {
            "sdk_network_calls": "none",
            "artifact_autofetch_network": "none",
            "network_calls": "none",
            "decompilation": "not_claimed",
        },
        "nodes": node_manifest,
        "external_refs": _dedupe_external_refs(parser.external_refs),
        "sanitizer_policy": {
            "version": 1,
            "name": "viewspec-raw-html-allowlist",
            "policy_version": RAW_HTML_POLICY_VERSION,
        },
        "lift": {
            "basis": DIFF_BASIS,
            "role_count": len(lift.roles),
            "region_count": len(lift.region_node_ids),
            "group_count": len(lift.group_candidates),
            "topology_fingerprint": lift.topology_fingerprint.to_json(),
        },
        "diagnostics": [dict(item) for item in diagnostics],
    }
    if design is not None:
        manifest["design"] = design.to_meta()
    return HtmlCompileResult(html=output_html, manifest=manifest, diagnostics=diagnostics, lift=lift)


def _check_html_size(html: str) -> None:
    size = len(html.encode("utf-8"))
    if size > MAX_HTML_INPUT_BYTES:
        raise HtmlInputError("HTML_INPUT_TOO_LARGE", f"Raw HTML exceeds {MAX_HTML_INPUT_BYTES} byte local limit")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sanitize_attrs(
    tag: str,
    attrs: list[tuple[str, str | None]],
    diagnostics: list[dict[str, str]],
    external_refs: list[dict[str, str]],
) -> dict[str, str]:
    allowed = set(SAFE_GLOBAL_ATTRS) | SAFE_ATTRS_BY_TAG.get(tag, set())
    sanitized: dict[str, str] = {}
    for raw_name, raw_value in attrs:
        name = raw_name.lower()
        value = (raw_value or "").strip()
        if name.startswith("on") or name in {"style", "srcdoc", "formaction", "srcset"}:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "HTML_ATTR_STRIPPED",
                    "message": f"Removed unsafe attribute {name} from <{tag}>.",
                }
            )
            continue
        if name not in allowed and not name.startswith("aria-"):
            continue
        if name in URL_ATTRS:
            normalized = _normalize_url_for_policy(value)
            if not _safe_url(normalized, attr=name):
                diagnostics.append(
                    {
                        "severity": "warning",
                        "code": "HTML_URL_STRIPPED",
                        "message": f"Removed unsafe {name} URL from <{tag}>.",
                    }
                )
                continue
            if tag == "img" and name == "src" and _is_external_http_url(normalized):
                external_refs.append(
                    {
                        "kind": "image",
                        "attr": "src",
                        "url": normalized,
                        "behavior": "inert_placeholder",
                    }
                )
                sanitized["data-viewspec-external-src"] = normalized
                diagnostics.append(
                    {
                        "severity": "warning",
                        "code": "HTML_REMOTE_IMAGE_INERT",
                        "message": "Replaced remote image src with an inert external-image placeholder.",
                    }
                )
                continue
            value = normalized
            if tag == "a" and name == "href" and _is_external_http_url(normalized):
                external_refs.append({"kind": "link", "attr": "href", "url": normalized, "behavior": "user_click"})
                sanitized["rel"] = "noopener noreferrer"
        if name == "rel" and sanitized.get("rel") == "noopener noreferrer":
            continue
        sanitized[name] = value
    if tag == "button":
        sanitized["type"] = "button"
    return sanitized


def _marks_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    for raw_name, raw_value in attrs:
        name = raw_name.lower()
        value = (raw_value or "").strip().lower()
        if name == "hidden" or (name == "aria-hidden" and value == "true"):
            return True
        if name == "data-lift-hidden" and value != "false":
            return True
    return False


def _sanitize_agent_attrs(
    tag: str,
    attrs: list[tuple[str, str | None]],
    diagnostics: list[dict[str, str]],
    external_refs: list[dict[str, str]],
) -> dict[str, str]:
    """Attribute policy of the "agent" lift profile.

    The URL, event-handler, and style rules match the compile profile. On top of that it keeps form
    state and ARIA, keeps the real ``type`` of a button (a submit button must stay one), keeps
    ViewSpec action ids and ``data-lift-*`` snapshot hints, and redacts secret values.
    """

    allowed = AGENT_GLOBAL_ATTRS | AGENT_ATTRS_BY_TAG.get(tag, set())
    sanitized: dict[str, str] = {}
    for raw_name, raw_value in attrs:
        name = raw_name.lower()
        value = (raw_value or "").strip()
        if name.startswith("on") or name in {"style", "srcdoc", "formaction", "srcset"}:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "HTML_ATTR_STRIPPED",
                    "message": f"Removed unsafe attribute {name} from <{tag}>.",
                }
            )
            continue
        kept = (
            name in allowed
            or name.startswith("aria-")
            or name in AGENT_VIEWSPEC_DATA_ATTRS
            or name.startswith(AGENT_HINT_ATTR_PREFIX)
        )
        if not kept:
            continue
        if name in AGENT_URL_ATTRS:
            if name == "action" and not value:
                continue  # An empty action submits to the page itself; leaving it out says the same.
            normalized = _normalize_url_for_policy(value)
            if not _safe_url(normalized, attr=name):
                diagnostics.append(
                    {
                        "severity": "warning",
                        "code": "HTML_URL_STRIPPED",
                        "message": f"Removed unsafe {name} URL from <{tag}>.",
                    }
                )
                continue
            shown = _redact_url_secrets(normalized)
            if shown != normalized:
                normalized = shown
                diagnostics.append(
                    {
                        "severity": "info",
                        "code": "HTML_VALUE_REDACTED",
                        "message": f"Redacted the value of a secret parameter in the {name} URL of <{tag}>.",
                    }
                )
            if _is_external_http_url(normalized):
                kind = {"href": "link", "src": "image", "action": "form"}[name]
                external_refs.append({"kind": kind, "attr": name, "url": normalized, "behavior": "agent_read"})
            value = normalized
        sanitized[name] = value
    if "value" in sanitized and sanitized["value"] and is_sensitive_control(tag, sanitized):
        sanitized["value"] = REDACTED_VALUE
        diagnostics.append(
            {
                "severity": "info",
                "code": "HTML_VALUE_REDACTED",
                "message": f"Redacted the value of a secret <{tag}> field.",
            }
        )
    return sanitized


def _redact_labelled_secrets(nodes: list[LiftNode], diagnostics: list[dict[str, str]]) -> list[LiftNode]:
    """Redact the fields that only a label marks as secret ("API key"), and mark them with
    ``data-lift-secret``. A label is known only once the whole page is read, so this runs after the
    parser, and before anything is derived from the nodes."""

    secret_ids = label_secret_ids(nodes)
    if not secret_ids:
        return nodes
    redacted: list[LiftNode] = []
    for node in nodes:
        if node.id in secret_ids:
            attrs = {**node.attrs, HINT_SECRET: "true"}
            if attrs.get("value"):
                attrs["value"] = REDACTED_VALUE
            held = node.tag == "textarea" and bool(node.text)
            node = replace(
                node,
                attrs=dict(sorted(attrs.items())),
                text=REDACTED_VALUE if held else node.text,
                text_runs=(REDACTED_VALUE,) if held else node.text_runs,
            )
            diagnostics.append(
                {
                    "severity": "info",
                    "code": "HTML_VALUE_REDACTED",
                    "message": f"Redacted the value of a <{node.tag}> field whose label names a secret.",
                }
            )
        redacted.append(node)
    return redacted


# What stands in a URL for the value of a parameter whose name names a secret: "[redacted]", encoded.
REDACTED_URL_VALUE = "%5Bredacted%5D"


def _redact_url_secrets(url: str) -> str:
    """``url`` without the values of the query and fragment parameters whose names name a secret
    ("?token=...", "#access_token=..."). The names and every other parameter stay."""

    head, mark, fragment = url.partition("#")
    path, question, query = head.partition("?")

    def redact(pairs: str) -> str:
        kept: list[str] = []
        for pair in pairs.split("&"):
            name, equals, value = pair.partition("=")
            kept.append(f"{name}={REDACTED_URL_VALUE}" if equals and value and names_a_secret(unquote(name)) else pair)
        return "&".join(kept)

    return f"{path}{question}{redact(query)}{mark}{redact(fragment) if '=' in fragment else fragment}"


def _safe_url(value: str, *, attr: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith("#") or lowered.startswith(("./", "../")):
        return True
    if lowered.startswith("/"):
        return not lowered.startswith("//")
    if lowered.startswith("\\"):
        return False
    if attr == "src" and lowered.startswith(DATA_IMAGE_SRC_PREFIXES):
        return True
    parsed = urlparse(value)
    if not parsed.scheme:
        return True
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return bool(parsed.netloc) and scheme in SAFE_SCHEMES_BY_ATTR.get(attr, set())
    if attr == "src" and scheme == "data":
        return lowered.startswith(DATA_IMAGE_SRC_PREFIXES)
    return scheme in SAFE_SCHEMES_BY_ATTR.get(attr, set())


def collapse_url_obfuscation(value: str) -> str:
    """Collapse the obfuscations a browser resolves back to '/': ASCII control
    whitespace (tab/LF/CR/etc.) removed, and backslashes incl. percent-encoded
    (%5c) -> '/'. Shared by the compiler URL policy (_normalize_url_for_policy)
    and the `check` autofetch gate (_contains_remote_http_reference) so the two
    cannot drift on obfuscation handling."""
    return BACKSLASH_URL_RE.sub("/", CONTROL_WHITESPACE_RE.sub("", value))


def _normalize_url_for_policy(value: str) -> str:
    previous = value
    for _ in range(4):
        decoded = html_lib.unescape(previous)
        if decoded == previous:
            break
        previous = decoded
    compacted = collapse_url_obfuscation(previous.strip())
    if compacted.startswith("//"):
        authority_path = compacted.lstrip("/")
        return f"https://{authority_path}" if authority_path else compacted
    parsed = urlparse(compacted)
    if parsed.scheme:
        return f"{parsed.scheme.lower()}{compacted[len(parsed.scheme) :]}"
    return compacted


def _is_external_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _freeze_nodes(root: _HTMLNode) -> list[LiftNode]:
    nodes: list[LiftNode] = []

    def visit(node: _HTMLNode, path: tuple[int, ...], parent_id: str | None) -> None:
        node_id = _node_id(path, node.tag)
        child_ids = tuple(_node_id((*path, index), child.tag) for index, child in enumerate(node.children))
        nodes.append(
            LiftNode(
                id=node_id,
                tag=node.tag,
                attrs=dict(sorted(node.attrs.items())),
                text=_collapse_ws(" ".join(node.text_chunks)),
                path=path,
                child_ids=child_ids,
                parent_id=parent_id,
                text_runs=tuple(WS_RE.sub(" ", run) for run in _text_runs(node)) if node.text_chunks else (),
            )
        )
        for index, child in enumerate(node.children):
            visit(child, (*path, index), node_id)

    visit(root, (0,), None)
    return nodes


def _render_sanitized_children(root: _HTMLNode, path: tuple[int, ...]) -> tuple[str, dict[str, Any]]:
    manifest: dict[str, Any] = {}

    def content(node: _HTMLNode, item_path: tuple[int, ...]) -> str:
        # Text and children in the order the source had them.
        runs = _text_runs(node)
        pieces = [escape(runs[0])]
        for index, child in enumerate(node.children):
            pieces.append(render(child, (*item_path, index)))
            pieces.append(escape(runs[index + 1]))
        return "".join(pieces)

    def render(node: _HTMLNode, item_path: tuple[int, ...]) -> str:
        if node.tag == "document":
            return content(node, item_path)
        node_id = _node_id(item_path, node.tag)
        attrs = {**node.attrs, "data-viewspec-node-id": node_id}
        attr_html = " ".join(
            f'{escape(key, quote=True)}="{escape(value, quote=True)}"' for key, value in sorted(attrs.items())
        )
        manifest[node_id] = {
            "tag": node.tag,
            "attrs": dict(sorted(node.attrs.items())),
            "text": _collapse_ws(" ".join(node.text_chunks))[:160],
        }
        if node.tag == "img" and "data-viewspec-external-src" in node.attrs:
            url = node.attrs["data-viewspec-external-src"]
            label = node.attrs.get("alt") or node.attrs.get("title") or "external image"
            link_attrs = {
                "class": "vs-external-image",
                "data-viewspec-node-id": node_id,
                "href": url,
                "rel": "noopener noreferrer",
            }
            link_html = " ".join(
                f'{escape(key, quote=True)}="{escape(value, quote=True)}"' for key, value in sorted(link_attrs.items())
            )
            return f"<a {link_html}>External image: {escape(label)}</a>"
        if node.tag in {"br", "hr", "img"}:
            return f"<{node.tag} {attr_html}>"
        return f"<{node.tag} {attr_html}>{content(node, item_path)}</{node.tag}>"

    return render(root, path or (0,)), manifest


def _text_runs(node: _HTMLNode) -> list[str]:
    """The node's text split at its children: run i comes just before child i, the last after them."""

    runs = [""] * (len(node.children) + 1)
    for text, slot in zip(node.text_chunks, node.text_slots, strict=True):
        runs[slot] += text
    return runs


def _node_id(path: tuple[int, ...], tag: str) -> str:
    digest = hashlib.sha1(("/".join(str(part) for part in path) + ":" + tag).encode("utf-8")).hexdigest()
    return f"dom_{digest[:12]}"


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


def _source_token(node: LiftNode) -> str:
    return f"{node.path}:{node.tag}:{sorted(node.attrs.items())}:{node.text}:{node.child_ids}"


def _infer_roles(nodes: list[LiftNode]) -> tuple[HtmlRole, ...]:
    roles: list[HtmlRole] = []
    for node in nodes:
        text = node.text.strip()
        if not text:
            continue
        if node.tag in HEADING_TAGS:
            roles.append(HtmlRole(node.id, "heading", text, 0.96))
        elif node.tag in ACTION_TAGS:
            roles.append(HtmlRole(node.id, "action", text, 0.94))
        elif NUMERIC_RE.match(text):
            roles.append(HtmlRole(node.id, "value", text, 0.9))
        elif len(text) > 100:
            roles.append(HtmlRole(node.id, "prose", text, 0.82))
        elif node.tag in TEXT_TAGS and LABEL_RE.match(text):
            roles.append(HtmlRole(node.id, "label", text, 0.78))
    return tuple(roles)


def _detect_group_candidates(nodes: list[LiftNode]) -> tuple[tuple[str, ...], ...]:
    children_by_parent: dict[str | None, list[LiftNode]] = {}
    for node in nodes:
        children_by_parent.setdefault(node.parent_id, []).append(node)
    groups: list[tuple[str, ...]] = []
    for children in children_by_parent.values():
        run: list[LiftNode] = []
        previous: str | None = None
        for child in children:
            signature = f"{child.tag}:{len(child.child_ids)}:{bool(child.text)}"
            if signature == previous:
                run.append(child)
            else:
                if len(run) >= 2:
                    groups.append(tuple(item.id for item in run))
                run = [child]
                previous = signature
        if len(run) >= 2:
            groups.append(tuple(item.id for item in run))
    return tuple(groups)


def _is_region(node: LiftNode) -> bool:
    return node.tag in REGION_TAGS and bool(node.child_ids)


def _fingerprint(nodes: list[LiftNode], *, ngram_length: int) -> HtmlTopologyFingerprint:
    if ngram_length < 1:
        raise ValueError("ngram_length must be >= 1")
    node_by_id = {node.id: node for node in nodes}
    tokens = {node.id: f"{node.tag}:{len(node.child_ids)}:{bool(node.text)}" for node in nodes}
    ngrams: list[str] = []
    for node in nodes:
        path_tokens: list[str] = []
        current: LiftNode | None = node
        while current is not None:
            path_tokens.append(tokens[current.id])
            current = node_by_id.get(current.parent_id) if current.parent_id else None
        path_tokens.reverse()
        if len(path_tokens) < ngram_length:
            ngrams.append(" > ".join(path_tokens))
        else:
            for index in range(0, len(path_tokens) - ngram_length + 1):
                ngrams.append(" > ".join(path_tokens[index : index + ngram_length]))
    sorted_ngrams = tuple(sorted(ngrams))
    digest = hashlib.sha256("\n".join(sorted_ngrams).encode("utf-8")).hexdigest()
    return HtmlTopologyFingerprint(
        hex=digest, path_ngrams=sorted_ngrams, token_count=len(sorted_ngrams), ngram_length=ngram_length
    )


def _governed_stylesheet(design: DesignSystemContext | None) -> str:
    colors = design.tokens.get("colors", {}) if design and isinstance(design.tokens.get("colors"), dict) else {}
    typography = (
        design.tokens.get("typography", {}) if design and isinstance(design.tokens.get("typography"), dict) else {}
    )
    heading = typography.get("heading") or typography.get("h1") or {}
    body = typography.get("body") or typography.get("body-md") or {}
    spacing = design.tokens.get("spacing", {}) if design and isinstance(design.tokens.get("spacing"), dict) else {}
    rounded = design.tokens.get("rounded", {}) if design and isinstance(design.tokens.get("rounded"), dict) else {}
    bg = colors.get("background") or colors.get("neutral") or "#F8FAFC"
    surface = colors.get("surface") or "#FFFFFF"
    text = colors.get("text") or colors.get("primary") or "#111827"
    muted = colors.get("muted") or colors.get("secondary") or "#5B6472"
    accent = colors.get("accent") or colors.get("tertiary") or colors.get("primary") or "#0F766E"
    font = body.get("fontFamily") or "ui-sans-serif, system-ui, sans-serif"
    heading_font = heading.get("fontFamily") or font
    gap = spacing.get("md") or spacing.get("card") or "1rem"
    radius = rounded.get("md") or "10px"
    return (
        ":root{"
        f"--vs-bg:{bg};--vs-surface:{surface};--vs-text:{text};--vs-muted:{muted};"
        f"--vs-accent:{accent};--vs-gap:{gap};--vs-radius:{radius};"
        "}"
        "body{margin:0;background:var(--vs-bg);color:var(--vs-text);font-family:"
        f"{escape(str(font))};line-height:1.6;padding:24px;}}"
        ".vs-artifact{max-width:960px;margin:0 auto;background:var(--vs-surface);"
        "border:1px solid color-mix(in srgb,var(--vs-muted) 24%,transparent);"
        "border-radius:var(--vs-radius);padding:clamp(18px,3vw,36px);}"
        ".vs-artifact>*+*{margin-top:var(--vs-gap);}"
        "h1,h2,h3,h4,h5,h6{font-family:"
        f"{escape(str(heading_font))};line-height:1.18;margin:0 0 .45em;color:var(--vs-text);}}"
        "p{margin:.5rem 0;}a{color:var(--vs-accent);font-weight:650;}button{border:0;"
        "border-radius:calc(var(--vs-radius) * .75);background:var(--vs-accent);color:white;"
        "padding:.55rem .85rem;font-weight:700;}table{width:100%;border-collapse:collapse;}"
        "th,td{border-bottom:1px solid color-mix(in srgb,var(--vs-muted) 24%,transparent);"
        "padding:.55rem;text-align:left;}th,caption{color:var(--vs-muted);font-weight:750;}"
        "section,article,aside,figure,blockquote{border-radius:var(--vs-radius);}"
        "img{max-width:100%;height:auto;}pre,code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;}"
        ".vs-external-image{display:inline-block;border:1px dashed color-mix(in srgb,var(--vs-muted) 45%,transparent);"
        "border-radius:var(--vs-radius);padding:.6rem .75rem;text-decoration:none;}"
    )


def _first_heading(lift: HtmlLiftResult) -> str | None:
    for role in lift.roles:
        if role.role == "heading":
            return role.text
    return None


def write_html_compile_result(
    result: HtmlCompileResult, output_dir: str | Path, *, include_lift: bool = False
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "html": output / "index.html",
        "manifest": output / "provenance_manifest.json",
        "diagnostics": output / "diagnostics.json",
    }
    if include_lift:
        paths["lift"] = output / "lift.json"
    write_artifact_set(output, _html_compile_files(result, paths))
    return {key: str(value) for key, value in paths.items()}


def _html_compile_files(result: HtmlCompileResult, paths: dict[str, Path]) -> Iterator[tuple[Path, str]]:
    """Yield the artifact files in write order; the manifest hashes index.html once it is on disk."""
    yield paths["html"], result.html
    manifest = dict(result.manifest)
    manifest["artifact_hash"] = _sha256_bytes(paths["html"].read_bytes())
    yield paths["manifest"], json.dumps(manifest, indent=2, sort_keys=True)
    yield paths["diagnostics"], json.dumps([dict(item) for item in result.diagnostics], indent=2, sort_keys=True)
    if "lift" in paths:
        yield paths["lift"], json.dumps(result.lift.to_json(), indent=2, sort_keys=True)


def _dedupe_diagnostics(items: list[dict[str, str]] | tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for item in items:
        normalized = {
            "severity": item.get("severity", "warning"),
            "code": item.get("code", "HTML_DIAGNOSTIC"),
            "message": item.get("message", ""),
        }
        if item.get("node_id"):
            normalized["node_id"] = item["node_id"]
        if item.get("path"):
            normalized["path"] = item["path"]
        key = (
            normalized["code"],
            normalized.get("node_id", ""),
            normalized.get("path", ""),
            normalized["message"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return tuple(deduped)


def _dedupe_external_refs(items: list[dict[str, str]] | tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for item in items:
        normalized = {
            "kind": item.get("kind", "external"),
            "attr": item.get("attr", ""),
            "url": item.get("url", ""),
            "behavior": item.get("behavior", ""),
        }
        key = (normalized["kind"], normalized["attr"], normalized["url"], normalized["behavior"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


__all__ = [
    "HtmlCompileResult",
    "HtmlInputError",
    "HtmlLiftResult",
    "HtmlRole",
    "HtmlTopologyFingerprint",
    "compile_html",
    "lift_html",
    "write_html_compile_result",
]
