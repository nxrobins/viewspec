"""Raw HTML lift, compile, and semantic diff helpers for the local SDK."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import tempfile
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from viewspec.design_md import DesignSystemContext


MAX_HTML_INPUT_BYTES = 5_000_000
VOID_TAGS = {"area", "br", "col", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
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
}
SANITIZER_POLICY = {
    "allowed_tags": sorted(ALLOWED_TAGS),
    "allowed_attrs": {tag: sorted(set(SAFE_GLOBAL_ATTRS) | attrs) for tag, attrs in sorted(SAFE_ATTRS_BY_TAG.items())},
    "global_attrs": sorted(SAFE_GLOBAL_ATTRS),
    "url_attrs": sorted(URL_ATTRS),
    "safe_schemes_by_attr": {key: sorted(value) for key, value in sorted(SAFE_SCHEMES_BY_ATTR.items())},
    "strip_with_content_tags": sorted(STRIP_WITH_CONTENT_TAGS),
    "unwrap_tags": sorted(UNWRAP_TAGS),
}
NUMERIC_RE = re.compile(r"^[+-]?(?:\$)?\d[\d,]*(?:\.\d+)?(?:%|[KMB])?$", re.IGNORECASE)
LABEL_RE = re.compile(r"^[A-Z][A-Za-z0-9][A-Za-z0-9 .,&:/()%'_-]{1,58}$")
CONTROL_WHITESPACE_RE = re.compile(r"[\x00-\x20\x7f]+")


class HtmlInputError(ValueError):
    """Raised when raw HTML cannot enter the local pipeline."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _HTMLNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_HTMLNode"] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LiftNode:
    id: str
    tag: str
    attrs: dict[str, str]
    text: str
    path: tuple[int, ...]
    child_ids: tuple[str, ...]
    parent_id: str | None

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

    def to_json(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_hash": self.source_hash,
            "nodes": [node.to_json() for node in self.nodes],
            "roles": [role.to_json() for role in self.roles],
            "region_node_ids": list(self.region_node_ids),
            "group_candidates": [list(group) for group in self.group_candidates],
            "topology_fingerprint": self.topology_fingerprint.to_json(),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


@dataclass(frozen=True)
class HtmlCompileResult:
    html: str
    manifest: dict[str, Any]
    diagnostics: tuple[dict[str, str], ...]
    lift: HtmlLiftResult


@dataclass(frozen=True)
class HtmlSemanticDiff:
    left_source_hash: str
    right_source_hash: str
    topology_similarity: float
    changed_headings: dict[str, list[str]]
    changed_values: dict[str, list[str]]
    changed_actions: dict[str, list[str]]
    changed_lists: dict[str, list[Any]]
    changed_tables: dict[str, list[Any]]
    role_count_delta: dict[str, int]
    group_count_delta: int
    diagnostics: tuple[dict[str, str], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "left_source_hash": self.left_source_hash,
            "right_source_hash": self.right_source_hash,
            "topology_similarity": self.topology_similarity,
            "changed_headings": self.changed_headings,
            "changed_values": self.changed_values,
            "changed_actions": self.changed_actions,
            "changed_lists": self.changed_lists,
            "changed_tables": self.changed_tables,
            "role_count_delta": self.role_count_delta,
            "group_count_delta": self.group_count_delta,
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


class _SanitizingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
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
            if tag == self.skipped_tags[-1]:
                self.skipped_tags.pop()
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not self.skipped_tags and data:
            self.stack[-1].text_chunks.append(data)

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
        if tag in TRANSPARENT_TAGS or tag in UNWRAP_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            self._diagnostic("warning", "HTML_TAG_UNWRAPPED", f"Removed unsupported <{tag}> wrapper but kept text children.")
            return

        node = _HTMLNode(tag, _sanitize_attrs(tag, attrs, self.diagnostics, self.external_refs))
        self.stack[-1].children.append(node)
        if not self_closing and tag not in VOID_TAGS:
            self.stack.append(node)

    def _diagnostic(self, severity: str, code: str, message: str) -> None:
        self.diagnostics.append({"severity": severity, "code": code, "message": message})


def lift_html(html: str, source_name: str | None = None, *, ngram_length: int = 4) -> HtmlLiftResult:
    """Lift raw HTML into deterministic local signals.

    This is not ViewSpec decompilation. It returns canonical DOM, roles,
    repeated groups, and topology signals that the local compiler and diff
    command can use without any network or model calls.
    """

    _check_html_size(html)
    parser = _SanitizingHTMLParser()
    parser.feed(html)
    parser.close()
    nodes = _freeze_nodes(parser.root)
    source_hash = hashlib.sha256("\n".join(_source_token(node) for node in nodes).encode("utf-8")).hexdigest()
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
    )


def compile_html(
    html: str,
    design: DesignSystemContext | None = None,
    title: str | None = None,
    *,
    source_name: str | None = None,
) -> HtmlCompileResult:
    """Compile raw HTML into a governed standalone HTML artifact."""

    _check_html_size(html)
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
        "kind": "raw_html_compile",
        "source_name": source_name,
        "source_hash": lift.source_hash,
        "command": "compile_html",
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
        },
        "lift": {
            "role_count": len(lift.roles),
            "region_count": len(lift.region_node_ids),
            "group_count": len(lift.group_candidates),
            "topology_fingerprint": lift.topology_fingerprint.to_json(),
        },
        "diagnostics": [dict(item) for item in diagnostics],
    }
    return HtmlCompileResult(html=output_html, manifest=manifest, diagnostics=diagnostics, lift=lift)


def diff_html(left_html: str, right_html: str, *, left_name: str | None = None, right_name: str | None = None) -> HtmlSemanticDiff:
    left = lift_html(left_html, source_name=left_name)
    right = lift_html(right_html, source_name=right_name)
    left_nodes = _nodes_by_id(left.nodes)
    right_nodes = _nodes_by_id(right.nodes)
    left_role_counts = _role_counts(left.roles)
    right_role_counts = _role_counts(right.roles)
    all_roles = sorted(set(left_role_counts) | set(right_role_counts))

    return HtmlSemanticDiff(
        left_source_hash=left.source_hash,
        right_source_hash=right.source_hash,
        topology_similarity=round(_jaccard(left.topology_fingerprint.path_ngrams, right.topology_fingerprint.path_ngrams), 6),
        changed_headings=_changed_texts(_texts_by_role(left.roles, "heading"), _texts_by_role(right.roles, "heading")),
        changed_values=_changed_texts(_texts_by_role(left.roles, "value"), _texts_by_role(right.roles, "value")),
        changed_actions=_changed_texts(_texts_by_role(left.roles, "action"), _texts_by_role(right.roles, "action")),
        changed_lists=_changed_sequences(_sequences_by_tag(left.nodes, "li", left_nodes), _sequences_by_tag(right.nodes, "li", right_nodes)),
        changed_tables=_changed_sequences(_table_rows(left.nodes, left_nodes), _table_rows(right.nodes, right_nodes)),
        role_count_delta={role: right_role_counts.get(role, 0) - left_role_counts.get(role, 0) for role in all_roles},
        group_count_delta=len(right.group_candidates) - len(left.group_candidates),
        diagnostics=_dedupe_diagnostics([*left.diagnostics, *right.diagnostics]),
    )


def _check_html_size(html: str) -> None:
    size = len(html.encode("utf-8"))
    if size > MAX_HTML_INPUT_BYTES:
        raise HtmlInputError("HTML_INPUT_TOO_LARGE", f"Raw HTML exceeds {MAX_HTML_INPUT_BYTES} byte local limit")


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
                {"severity": "warning", "code": "HTML_ATTR_STRIPPED", "message": f"Removed unsafe attribute {name} from <{tag}>."}
            )
            continue
        if name not in allowed and not name.startswith("aria-"):
            continue
        if name in URL_ATTRS:
            normalized = _normalize_url_for_policy(value)
            if not _safe_url(normalized, attr=name):
                diagnostics.append(
                    {"severity": "warning", "code": "HTML_URL_STRIPPED", "message": f"Removed unsafe {name} URL from <{tag}>."}
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


def _safe_url(value: str, *, attr: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith("#") or lowered.startswith(("/", "./", "../")):
        return True
    if attr == "src" and lowered.startswith(("data:image/png", "data:image/jpeg", "data:image/gif", "data:image/webp", "data:image/avif")):
        return True
    parsed = urlparse(value)
    if not parsed.scheme:
        return True
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return bool(parsed.netloc) and scheme in SAFE_SCHEMES_BY_ATTR.get(attr, set())
    if attr == "src" and scheme == "data":
        return lowered.startswith(("data:image/png", "data:image/jpeg", "data:image/gif", "data:image/webp", "data:image/avif"))
    return scheme in SAFE_SCHEMES_BY_ATTR.get(attr, set())


def _normalize_url_for_policy(value: str) -> str:
    previous = value
    for _ in range(4):
        decoded = html_lib.unescape(previous)
        if decoded == previous:
            break
        previous = decoded
    stripped = previous.strip()
    compacted = CONTROL_WHITESPACE_RE.sub("", stripped)
    parsed = urlparse(compacted)
    if parsed.scheme:
        return f"{parsed.scheme.lower()}{compacted[len(parsed.scheme):]}"
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
            )
        )
        for index, child in enumerate(node.children):
            visit(child, (*path, index), node_id)

    visit(root, (0,), None)
    return nodes


def _render_sanitized_children(root: _HTMLNode, path: tuple[int, ...]) -> tuple[str, dict[str, Any]]:
    manifest: dict[str, Any] = {}

    def render(node: _HTMLNode, item_path: tuple[int, ...]) -> str:
        if node.tag == "document":
            pieces = [escape(text) for text in node.text_chunks if text]
            for index, child in enumerate(node.children):
                pieces.append(render(child, (*item_path, index)))
            return "".join(pieces)
        node_id = _node_id(item_path, node.tag)
        attrs = {**node.attrs, "data-viewspec-node-id": node_id}
        attr_html = " ".join(f'{escape(key, quote=True)}="{escape(value, quote=True)}"' for key, value in sorted(attrs.items()))
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
            return f'<a {link_html}>External image: {escape(label)}</a>'
        if node.tag in {"br", "hr", "img"}:
            return f"<{node.tag} {attr_html}>"
        pieces = [escape(text) for text in node.text_chunks if text]
        for index, child in enumerate(node.children):
            pieces.append(render(child, (*item_path, index)))
        return f"<{node.tag} {attr_html}>{''.join(pieces)}</{node.tag}>"

    return render(root, path or (0,)), manifest


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
    return HtmlTopologyFingerprint(hex=digest, path_ngrams=sorted_ngrams, token_count=len(sorted_ngrams), ngram_length=ngram_length)


def _governed_stylesheet(design: DesignSystemContext | None) -> str:
    colors = design.tokens.get("colors", {}) if design and isinstance(design.tokens.get("colors"), dict) else {}
    typography = design.tokens.get("typography", {}) if design and isinstance(design.tokens.get("typography"), dict) else {}
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


def _nodes_by_id(nodes: tuple[LiftNode, ...]) -> dict[str, LiftNode]:
    return {node.id: node for node in nodes}


def _texts_by_role(roles: tuple[HtmlRole, ...], role: str) -> list[str]:
    return [item.text for item in roles if item.role == role]


def _changed_texts(left: list[str], right: list[str]) -> dict[str, list[str]]:
    left_set = set(left)
    right_set = set(right)
    return {"removed": sorted(left_set - right_set), "added": sorted(right_set - left_set)}


def _role_counts(roles: tuple[HtmlRole, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for role in roles:
        counts[role.role] = counts.get(role.role, 0) + 1
    return counts


def _jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _sequences_by_tag(nodes: tuple[LiftNode, ...], tag: str, nodes_by_id: dict[str, LiftNode]) -> list[list[str]]:
    sequences = []
    for node in nodes:
        children = [nodes_by_id[child_id] for child_id in node.child_ids if child_id in nodes_by_id]
        values = [child.text for child in children if child.tag == tag and child.text]
        if values:
            sequences.append(values)
    return sequences


def _table_rows(nodes: tuple[LiftNode, ...], nodes_by_id: dict[str, LiftNode]) -> list[list[str]]:
    rows = []
    for node in nodes:
        if node.tag != "tr":
            continue
        cells = [nodes_by_id[child_id].text for child_id in node.child_ids if child_id in nodes_by_id and nodes_by_id[child_id].tag in {"td", "th"}]
        if cells:
            rows.append(cells)
    return rows


def _changed_sequences(left: list[list[str]], right: list[list[str]]) -> dict[str, list[Any]]:
    left_items = {tuple(item) for item in left}
    right_items = {tuple(item) for item in right}
    return {
        "removed": [list(item) for item in sorted(left_items - right_items)],
        "added": [list(item) for item in sorted(right_items - left_items)],
    }


def write_html_compile_result(result: HtmlCompileResult, output_dir: str | Path, *, include_lift: bool = False) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "html": output / "index.html",
        "manifest": output / "provenance_manifest.json",
        "diagnostics": output / "diagnostics.json",
    }
    try:
        _atomic_write(paths["html"], result.html)
        _atomic_write(paths["manifest"], json.dumps(result.manifest, indent=2, sort_keys=True))
        _atomic_write(paths["diagnostics"], json.dumps([dict(item) for item in result.diagnostics], indent=2, sort_keys=True))
        if include_lift:
            lift_path = output / "lift.json"
            _atomic_write(lift_path, json.dumps(result.lift.to_json(), indent=2, sort_keys=True))
            paths["lift"] = lift_path
    except Exception as exc:
        _write_failure_marker(output, exc)
        raise
    return {key: str(value) for key, value in paths.items()}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        Path(temp_name).replace(path)
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise


def _write_failure_marker(output: Path, exc: Exception) -> None:
    marker = output / ".viewspec_write_failed.json"
    try:
        _atomic_write(
            marker,
            json.dumps(
                {
                    "version": 1,
                    "severity": "error",
                    "code": "ARTIFACT_WRITE_FAILED",
                    "message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
        )
    except Exception:
        return


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
    "HtmlSemanticDiff",
    "HtmlTopologyFingerprint",
    "SANITIZER_POLICY",
    "compile_html",
    "diff_html",
    "lift_html",
    "write_html_compile_result",
]
