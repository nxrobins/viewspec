"""The limits a browser session runs inside, stated before it starts and never changed by the agent.

A session is either open or sealed.

    open    No ``origins``. The agent may read the public web: http and https pages on public
            addresses. No request that is not GET, HEAD, or OPTIONS is sent, and the session holds
            no credentials. A page on this computer or its network is refused.
    sealed  ``origins`` are listed. The browser can reach those origins and the extra ``hosts``
            listed for them (a CDN), and nothing else, by any kind of request. Only a sealed
            session may hold credentials (``secrets``, ``storage_state``) or send writes, and it
            sends only the writes its ``write_rules`` name.

``Boundary.build`` refuses a combination that would break this: credentials or writes without a list
of origins, a write rule for an origin that is not listed, a secret too short to protect.

Three limits, and where each is kept:

    where the browser may go   the gate in bridge.js: a proxy that every connection passes, so a
                               redirect, a WebSocket, a service worker, or a new tab is checked
                               like any other request
    what it may send           the request handler in bridge.js: the method and address of each
                               request against ``write_rules`` and ``deny``
    what a result may hold     ``scrub`` here: the value of a declared secret is taken out of
                               every result, wherever the page put it

Only the standard library is imported, so these rules can be tested without a browser.
"""

from __future__ import annotations

import base64
import html
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, unquote_plus, urlsplit

WRITE_METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "WEBSOCKET", "EVENTSOURCE", "*")
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}
# A shorter secret would be found by chance inside ordinary words and numbers.
MIN_SECRET_LENGTH = 6
SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
CGNAT = ipaddress.ip_network("100.64.0.0/10")

# This is an explicit output contract, not an arbitrary-secret detector. A field value learned
# from the browser or supplied by the operator remains private for the lifetime of the session.
PRIVATE_MODES = ("strict", "heuristic")
PRIVATE = "[redacted]"
MAX_PRIVATE_VALUES = 2048
MAX_PRIVATE_CHARS = 1_000_000
_PARAMETER = re.compile(r"([?&#;])([^?&#;=\s\"'<>]+)=(\[secret [A-Z][A-Z0-9_]*\]|\[redacted\]|[^&#;\s\"'<>]*)")
# A query or fragment can be an opaque credential without a parameter name. Scope bare
# segments to URL-like text so ordinary headings, references and CSS can still be displayed.
_OPAQUE_URL_PART = re.compile(r"""((?:https?://|/)[^\s?#"'<>]*[?#])([^\s#&;="'<>]+)(?=[\s#&;"'<>]|$)""")
_FRAGMENT = re.compile(r"""((?:https?://|/)[^\s#"'<>]*#)([^\s&#;="'<>]+)(?=[\s&;"'<>]|$)""")
_HTML_ATTRIBUTE = re.compile(r"""\s+([^\s/>=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""")


def _svg_reference(value: str) -> bool:
    """A symbol ID in an SVG use reference, not a navigation or named credential."""
    try:
        parts = urlsplit(html.unescape(value))
    except ValueError:
        return False
    return (
        parts.scheme in {"", "http", "https"}
        and not parts.username
        and (not parts.path or parts.path.lower().endswith(".svg"))
        and bool(re.fullmatch(r"[A-Za-z_][\w.:-]*", unquote_plus(parts.fragment)))
    )


def _svg_learning_text(source: str) -> str:
    """Mask only SVG use fragments before learning URL values from raw markup.

    This is not a public-value allowlist: URLs elsewhere and all known credentials still win.
    Keep source spans intact so queries, other attributes, text and malformed markup retain the
    existing conservative scan. Never exempt a whole URL merely because its path ends in .svg.
    """
    if "<use" not in source.lower():
        return source
    starts = [0, *(match.end() for match in re.finditer("\n", source))]
    spans: list[tuple[int, int]] = []

    class Uses(HTMLParser):
        svg_depth = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "svg":
                self.svg_depth += 1
            if tag != "use" or not self.svg_depth:
                return
            raw = self.get_starttag_text()
            line, column = self.getpos()
            start = starts[line - 1] + column
            for attr in _HTML_ATTRIBUTE.finditer(raw):
                if attr[1].lower() not in {"href", "xlink:href"}:
                    continue
                group = next((i for i in (2, 3, 4) if attr[i] is not None), None)
                if group is not None and "#" in attr[group] and _svg_reference(attr[group]):
                    spans.append((start + attr.start(group) + attr[group].rfind("#") + 1, start + attr.end(group)))

        def handle_endtag(self, tag: str) -> None:
            if tag == "svg":
                self.svg_depth = max(0, self.svg_depth - 1)

    parser = Uses()
    try:
        parser.feed(source)
        parser.close()
    except AssertionError:
        # HTMLParser rejects malformed marked declarations. Without reliable element
        # context, retain the conservative URL scan rather than exempting anything.
        return source
    for start, end in reversed(spans):
        source = source[:start] + PRIVATE + source[end:]
    return source


_IDENTIFIERS = frozenset(
    {
        "id",
        "node_id",
        "ref",
        "doc_id",
        "child_ids",
        "parent_id",
        "form_id",
        "heading_id",
        "end_node_id",
        "end_ref",
        "region_node_ids",
        "group_candidates",
    }
)
_PROTOCOL_KEYS = _IDENTIFIERS | frozenset(
    [
        "schema_version",
        "ok",
        "summary",
        "diagnostics",
        "external_refs",
        "paths",
        "next_actions",
        "errors",
        "metadata",
        "code",
        "message",
        "fix",
        "url",
        "title",
        "observation_id",
        "view",
        "view_page",
        "form_tools",
        "limits",
        "changes",
        "blocked_writes",
        "passed_writes",
        "blocked_navigations",
        "blocked_requests",
        "dialogs",
        "opened",
        "steps",
        "navigated",
        "outcome",
        "wait",
        "scroll",
        "state",
        "method",
        "content_type",
        "body_bytes",
        "new_tab",
        "form_fields",
        "step",
        "op",
        "name",
        "value",
        "values",
        "secret",
        "files",
        "resolved_by",
        "verified",
        "chosen",
        "download",
        "unsaved_downloads",
        "invalid",
        "reaction",
        "mutations",
        "requests",
        "pending",
        "type",
        "answer",
        "status",
        "reason",
        "retry",
        "page",
        "effect",
        "repeat",
        "count",
        "host",
        "first",
        "offset",
        "returned",
        "total",
        "next_offset",
        "context",
        "find",
        "matches",
        "description",
        "input_schema",
        "properties",
        "required",
        "default",
        "enum",
        "items",
        "additionalProperties",
        "format",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "nullable",
        "anyOf",
        "oneOf",
        "allOf",
        "definitions",
        "bytes",
        "file",
        "path",
        "sha256",
        "suggested",
        "for",
        "condition",
        "met",
        "waited_ms",
        "timeout_ms",
        "scrolls",
        "moved",
        "loaded",
        "at_end",
        "end_reached",
        "area",
        "direction",
        "new_document",
        "empty",
        "lines",
        "handle",
        "kind",
        "tag",
        "attrs",
        "text",
        "text_runs",
        "controls",
        "forms",
        "messages",
        "profile",
        "source_hash",
        "source_name",
        "topology_fingerprint",
        "hex",
        "path_ngrams",
        "token_count",
        "ngram_length",
        "field_name",
        "control",
        "disabled",
        "readonly",
        "checked",
        "options",
        "selected",
        "expanded",
        "pressed",
        "sort",
        "href",
        "submits",
        "autocomplete",
        "action_id",
        "multiple",
        "accept",
        "date",
        "date_range",
        "busy",
        "action",
        "retry_safe",
        "choice",
        "choice_owner",
        "adapter",
        "mode",
        "coverage",
        "selection_known",
        "commit",
        "operation",
        "query",
        "selection",
        "changed",
        "completion",
        "persistence",
        "rollback",
        "progress",
        "fields",
        "submission",
        "submitters",
        "preparation",
        "reason_code",
        "phase",
        "activation",
        "native_submissions",
        "prevented_submissions",
        "writes",
        "contract",
        "index",
        "uniqueItems",
    ]
)
_ENUMS = {
    "status": {
        "succeeded",
        "blocked",
        "failed",
        "uncertain",
        "not_run",
        "attempted",
        "applied",
        "verified",
        "matched",
        "mismatch",
        "unknown",
    },
    "state": {"passed", "blocked", "blocked-and-simulated"},
    "phase": {"preparing", "finalizing", "activating", "finished"},
    "reason_code": {
        "native_submit_seen",
        "event_refused",
        "activation_only",
        "scope_changed",
        "field_mismatch",
        "field_unknown",
        "readiness_pending",
        "prepared",
        "document_unavailable",
    },
    "activation": {"not_dispatched", "dispatched", "unknown"},
    "submission": {"not_requested", "not_run", "attempted", "withheld"},
    "operation": {"add", "replace", "replace-set"},
    "completion": {"ui", "closed", "form_pending", "not_run"},
    "persistence": {"unverified"},
    "rollback": {"not_attempted"},
    "adapter": {"aria", "gitea-sidebar", "vikunja-labels"},
    "mode": {"single", "multiple", "unknown"},
    "coverage": {"complete", "partial", "unknown"},
    "commit": {"form", "close", "immediate", "unknown"},
    "retry": {"safe", "not_needed", "may_repeat"},
    "page": {"busy", "quiet", "reacted"},
    "answer": {"accepted", "dismissed"},
    "method": set(WRITE_METHODS),
}
_PRIVATE_KEY = re.compile(
    r"password|passwd|passphrase|secret|token|credential|authorization|apikey|accesskey|privatekey|"
    r"session(id)?$|^(code|auth|key|pin|otp|cvv|cvc|pan|ssn)$",
    re.I,
)


class PrivacyRedactor:
    """Project data for a reader without changing the browser's planning data.

    Strict mode learns all text-entry field values; heuristic mode learns only fields marked
    sensitive. Both remove learned/declared credentials, common reversible encodings and named
    sensitive URL parameters from every projected string, including dictionary keys. Arbitrary
    secrets in prose and arbitrary transformations of a secret are outside this contract.
    """

    def __init__(self, mode: str = "strict") -> None:
        if mode not in PRIVATE_MODES:
            raise BoundaryError("--private must be strict or heuristic.")
        self.mode = mode
        self._values: dict[str, str] = {}
        self._characters = 0
        self._exhausted = False
        self._forms: list[tuple[str, str]] = []
        self._pattern: re.Pattern[str] | None = None

    def require_capacity(self) -> None:
        if self._exhausted:
            raise PrivacyLimit("Privacy value limit reached; close this session and start a new one.")

    def remember(self, values: Sequence[str], label: str = PRIVATE) -> None:
        self.require_capacity()
        changed = False
        for value in values:
            if (
                isinstance(value, str)
                and value
                and value not in {PRIVATE, "[not shown]"}
                and not re.fullmatch(r"\[secret [A-Z][A-Z0-9_]*\]", value)
            ):
                if value not in self._values:
                    if len(self._values) >= MAX_PRIVATE_VALUES or self._characters + len(value) > MAX_PRIVATE_CHARS:
                        self._exhausted = True
                        self.require_capacity()
                    self._values[value] = label
                    self._characters += len(value)
                    changed = True
        if changed:
            forms: dict[str, str] = {}
            for value, replacement in self._values.items():
                # The HTML lift strips attributes and collapses text whitespace before rendering.
                for text in {value, value.strip(), " ".join(value.split())} - {""}:
                    raw = text.encode("utf-8")
                    encoded = {
                        text,
                        quote(text, safe=""),
                        quote_plus(text),
                        html.escape(text),
                        json.dumps(text, ensure_ascii=False)[1:-1],
                        json.dumps(text)[1:-1],
                        repr(text)[1:-1],
                        base64.b64encode(raw).decode(),
                        base64.urlsafe_b64encode(raw).decode().rstrip("="),
                    }
                    encoded |= {re.sub(r"%[A-F0-9]{2}", lambda match: match[0].lower(), form) for form in encoded}
                    forms.update((form, replacement) for form in encoded if form)
            self._forms = sorted(forms.items(), key=lambda item: -len(item[0]))
            long_forms = [re.escape(form) for form, _ in self._forms if len(form) >= 4]
            self._pattern = re.compile("|".join(long_forms)) if long_forms else None

    def _parameters(self, text: str) -> str:
        def hide(match: re.Match[str]) -> str:
            if match[3] == PRIVATE or re.fullmatch(r"\[secret [A-Z][A-Z0-9_]*\]", match[3]):
                return match[0]
            key = re.sub(r"[^a-z0-9]", "", unquote_plus(match[2]).lower())
            label = self._values.get(unquote_plus(match[3]), PRIVATE)
            return f"{match[1]}{match[2]}={label}" if self.mode == "strict" or _PRIVATE_KEY.search(key) else match[0]

        if self.mode == "strict":
            for pattern in (_OPAQUE_URL_PART, _FRAGMENT):
                text = pattern.sub(lambda match: match[1] + PRIVATE, text)
        return _PARAMETER.sub(hide, text)

    def learn_html(self, source: str) -> None:
        """Capture initial native values before the HTML parser discards sensitive fields."""
        owner = self

        class Values(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.textarea = False
                self.text: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                attr = dict(attrs)
                kind = (attr.get("type") or "text").lower()
                sensitive = (
                    attr.get("data-lift-secret") == "true"
                    or kind == "password"
                    or bool(
                        _PRIVATE_KEY.search(" ".join(attr.get(key) or "" for key in ("name", "id", "autocomplete")))
                    )
                )
                private = sensitive or owner.mode == "strict"
                if (
                    tag == "input"
                    and private
                    and kind
                    not in {"button", "submit", "reset", "checkbox", "radio", "range", "color", "file", "image"}
                ):
                    owner.remember([attr.get("value") or ""])
                if tag == "textarea":
                    self.textarea = private
                    self.text = []

            def handle_data(self, data: str) -> None:
                if self.textarea:
                    self.text.append(data)

            def handle_endtag(self, tag: str) -> None:
                if tag == "textarea" and self.textarea:
                    owner.remember(["".join(self.text)])
                    self.textarea = False

        Values().feed(source)

    def learn(self, value: Any) -> None:
        """Learn URL credentials in the whole object before projecting its first string."""
        if isinstance(value, str):
            value = _svg_learning_text(value)
            if self.mode == "strict":
                for pattern in (_OPAQUE_URL_PART, _FRAGMENT):
                    self.remember([unquote_plus(match[2]) for match in pattern.finditer(value)])
            for match in _PARAMETER.finditer(value):
                key = re.sub(r"[^a-z0-9]", "", unquote_plus(match[2]).lower())
                if self.mode == "strict" or _PRIVATE_KEY.search(key):
                    self.remember([unquote_plus(match[3])])
        elif isinstance(value, Mapping):
            for key, inner in value.items():
                self.learn(key)
                self.learn(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                self.learn(inner)
        elif is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                self.learn(getattr(value, item.name))

    def project_html(self, source: str) -> str:
        """Project attribute and text values before HTML quoting, including short native values."""
        self.learn_html(source)
        owner = self
        output: list[str] = []

        class Project(HTMLParser):
            svg_depth = 0

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "svg":
                    self.svg_depth += 1
                # Attribute projection loses the surrounding element. Mask a use's fragment
                # before clean() can relearn it as an opaque navigation credential. Queries
                # and previously learned values still pass through the same redactor.
                if tag == "use" and self.svg_depth:
                    attrs = [
                        (name, value.rsplit("#", 1)[0] + "#" + PRIVATE)
                        if name in {"href", "xlink:href"} and value and _svg_reference(value)
                        else (name, value)
                        for name, value in attrs
                    ]
                rendered = "".join(
                    f" {name}" if value is None else f' {name}="{html.escape(owner.clean(value), quote=True)}"'
                    for name, value in attrs
                )
                output.append(f"<{tag}{rendered}>")

            def handle_endtag(self, tag: str) -> None:
                output.append(f"</{tag}>")
                if tag == "svg":
                    self.svg_depth = max(0, self.svg_depth - 1)

            def handle_data(self, data: str) -> None:
                output.append(html.escape(owner.clean(data), quote=False))

            def handle_decl(self, decl: str) -> None:
                output.append(f"<!{owner.clean(decl)}>")

            def handle_comment(self, data: str) -> None:
                output.append(f"<!--{owner.clean(data)}-->")

        parser = Project(convert_charrefs=True)
        parser.feed(source)
        parser.close()
        return "".join(output)

    def clean(self, value: Any) -> Any:
        self.require_capacity()
        self.learn(value)

        def project(item: Any, context: str = "") -> Any:
            if isinstance(item, str):
                if context in _IDENTIFIERS or item in _ENUMS.get(context, ()):
                    return item
                item = self._parameters(item)
                replacements = dict(self._forms)
                if item in replacements:
                    return replacements[item]
                # One substitution pass avoids processing the replacement as if it were input.
                if self._pattern is not None:
                    item = self._pattern.sub(lambda match: replacements[match[0]], item)
                return item
            if isinstance(item, Mapping):
                return {
                    (
                        key if key in _PROTOCOL_KEYS and context not in {"properties", "form_fields"} else project(key)
                    ): project(
                        inner,
                        key if isinstance(key, str) and context not in {"properties", "form_fields", "attrs"} else "",
                    )
                    for key, inner in item.items()
                }
            if isinstance(item, tuple):
                return tuple(project(inner, context) for inner in item)
            if isinstance(item, list):
                return [project(inner) for inner in item]
            if is_dataclass(item) and not isinstance(item, type):
                return replace(
                    item, **{part.name: project(getattr(item, part.name), part.name) for part in fields(item)}
                )
            return item

        return project(value)

    def audit(self, outputs: Mapping[str, str]) -> dict[str, Any]:
        """Counts for a diagnostic report, without giving the diagnostic caller private values."""
        return {
            "mode": self.mode,
            "known_values_checked": len(self._values),
            "checked": bool(self._values),
            "unredacted_matches": {
                name: sum(1 for value in self._values if value and value in output) for name, output in outputs.items()
            },
            "redaction_markers": {name: output.count(PRIVATE) for name, output in outputs.items()},
            "scope": "Known values only; arbitrary secrets in prose are not detected.",
        }


class BoundaryError(ValueError):
    """The limits asked for cannot be kept, or contradict each other."""


class PrivacyLimit(BoundaryError):
    """The session can no longer retain all private values."""


def _url(text: str, *, what: str, socket: bool = False) -> tuple[str, str]:
    """Parse the unambiguous URL subset shared with the browser's WHATWG parser.

    Reject credentials, control characters, backslashes and numeric host aliases instead of
    allowing Python and Chromium to disagree. Normalize ports and IPv6 before comparing policy.
    """
    if not isinstance(text, str) or re.search(r"[\x00-\x20\x7f\\]", text):
        raise BoundaryError(f"{what} must be a web address without spaces, controls, or backslashes.")
    try:
        parts = urlsplit(text)
        schemes = {"http", "https", "ws", "wss"} if socket else {"http", "https"}
        if parts.scheme not in schemes or not parts.hostname or "@" in parts.netloc:
            raise ValueError("scheme, host or credentials")
        host = parts.hostname
        if "%" in host:
            raise ValueError("encoded host or IPv6 zone")
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or host.endswith("."):
                raise ValueError("host spelling")
            # WHATWG treats these as IPv4 aliases; Python otherwise treats them as DNS names.
            if re.fullmatch(r"(?:[0-9.]+|0[xX][0-9a-fA-F.]+)", host) or re.search(r"(?:^|\.)[0-9]+$", host):
                host = str(ipaddress.IPv4Address(host))
            elif any(not label or label.startswith("-") or label.endswith("-") for label in host.split(".")):
                raise ValueError("host spelling")
        port = parts.port
        if port == 0 or parts.netloc.endswith(":"):
            raise ValueError("port")
        origin = f"{parts.scheme}://{host}" + ("" if port in {None, DEFAULT_PORTS[parts.scheme]} else f":{port}")
        return origin, origin + (parts.path or "/") + ("?" + parts.query if parts.query else "")
    except ValueError as error:
        raise BoundaryError(f"{what} must be an unambiguous http or https address without credentials.") from error


def _pattern(text: str, *, what: str, socket: bool = False) -> str:
    value = text.strip()
    if not value or not value.isascii() or "#" in value or re.search(r"[\x00-\x20\x7f\\]", value):
        raise BoundaryError(
            f"{what} needs an ASCII address pattern without fragments, spaces, controls, or backslashes."
        )
    # The browser removes literal and encoded dot segments before matching a request. Refuse a
    # rule whose spelling would silently stop matching; use the normalized destination instead.
    try:
        path = urlsplit(value).path if "://" in value else value.partition("?")[0]
    except ValueError as error:
        raise BoundaryError(f"{what} needs a valid address pattern.") from error
    if any(re.sub("%2e", ".", segment, flags=re.I) in {".", ".."} for segment in path.split("/")):
        raise BoundaryError(f"{what} needs a normalized URL pattern without dot segments.")
    if "://" not in value:
        return value
    return _url(value, what=what, socket=socket)[1]


def _origin(text: str, *, what: str) -> str:
    """``scheme://host[:port]``, lower case, without a default port. Anything more is an error."""

    value = text.strip()
    try:
        parts = urlsplit(value)
    except ValueError as error:
        raise BoundaryError(f"{what} is not an origin such as https://shop.example.") from error
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise BoundaryError(f"{what} is not an origin such as https://shop.example.")
    if "@" in parts.netloc or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise BoundaryError(f"{what} {text!r} must be an origin only: a scheme, a host, and a port if it needs one.")
    return _url(value, what=what)[0]


def _endpoint(origin: str) -> str:
    """The ``host:port`` that an origin's connections go to."""

    parts = urlsplit(origin)
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    return f"{host}:{parts.port or DEFAULT_PORTS[parts.scheme]}"


def glob_matches(pattern: str, text: str) -> bool:
    """Whether ``text`` is ``pattern``, where ``*`` stands for any run of characters. Nothing else in
    a pattern is special: a ``?`` is the question mark of a query. bridge.js matches the same way."""

    return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), text) is not None


def is_private_host(host: str) -> bool:
    """Whether a host name written in an address is this computer or a private network: ``localhost``,
    or an IP address that is loopback, private, link-local, or otherwise not public. A name that
    resolves to such an address is caught by the gate, which sees the address."""

    name = host.strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global or address in CGNAT if address.version == 4 else not address.is_global


@dataclass(frozen=True)
class WriteRule:
    """A request that may be sent though it is not GET, HEAD, or OPTIONS: its method (``*`` for any,
    ``WEBSOCKET`` for a socket) and the addresses it may go to."""

    method: str
    pattern: str

    @classmethod
    def parse(cls, text: str, origins: Sequence[str]) -> WriteRule:
        method, _, pattern = text.strip().partition(" ")
        method, pattern = method.upper(), pattern.strip()
        if method not in WRITE_METHODS or not pattern:
            raise BoundaryError(
                f"Write rule {text!r} must be a method and an address, such as "
                f"'POST https://shop.example/cart/*'. Methods: {', '.join(WRITE_METHODS)}."
            )
        # A socket's address starts ws: or wss:, and belongs to the http: or https: origin beside it.
        pattern = _pattern(pattern, what="Write rule", socket=method in {"WEBSOCKET", "*"})
        as_http = re.sub(r"^ws(s?)://", r"http\1://", pattern)
        if not any(as_http == origin or as_http.startswith(origin + "/") for origin in origins):
            raise BoundaryError(f"Write rule {text!r} is for an address that is not on a listed origin.")
        return cls(method, pattern)

    def to_json(self) -> dict[str, str]:
        return {"method": self.method, "pattern": self.pattern}


def _credentials(specs: Sequence[str], env: Mapping[str, str], *, header: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        name, sep, variable = spec.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or not SECRET_NAME.fullmatch(variable):
            raise BoundaryError("Credential options require NAME=ENV_VARIABLE.")
        value = env.get(variable, "")
        if not value or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise BoundaryError("Credential environment variable must contain a printable HTTP value.")
        if header:
            name = name.lower()
            if name != "authorization" and not name.startswith("x-"):
                raise BoundaryError("--header accepts Authorization or an application X- header.")
        elif ";" in value or "," in value:
            raise BoundaryError("Invalid cookie value.")
        if name in result:
            raise BoundaryError("Duplicate credential name.")
        result[name] = value
    return result


def _scoped_state(state: Any, origin: str) -> dict[str, Any]:
    try:
        cookies = state.get("cookies", [])
        origins = state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise ValueError
        for cookie in cookies:
            if cookie["domain"].lower() != urlsplit(origin).hostname or not isinstance(cookie["value"], str):
                raise ValueError
        for site in origins:
            if site["origin"] != origin or set(site) - {"origin", "localStorage"}:
                raise ValueError
            if any(not isinstance(entry["value"], str) for entry in site.get("localStorage", [])):
                raise ValueError
        return {"cookies": cookies, "origins": origins}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise BoundaryError(
            "Invalid storage state. Use host-only cookies and local storage for the selected origin."
        ) from None


def _session_state(path: Path, origin: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise BoundaryError("Cannot read the session storage state.") from None
    return _scoped_state(state, origin)


@dataclass(frozen=True)
class Boundary:
    origins: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()  # ``host`` or ``host:port`` that requests, never pages, may also reach.
    write_rules: tuple[WriteRule, ...] = ()
    all_writes: bool = False
    read_policy: str = "permissive"
    read_urls: tuple[str, ...] = ()
    mutation_urls: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()  # Addresses never requested, by any method.
    secrets: Mapping[str, str] = field(default_factory=dict, repr=False)
    storage_state: Path | None = None
    save_storage_state: Path | None = None
    confirm: str = "dismiss"
    initial_state: dict[str, Any] | None = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    cookies: Mapping[str, str] = field(default_factory=dict, repr=False)
    simulate_writes: bool = False
    private: str = "strict"

    @classmethod
    def build(
        cls,
        *,
        allow_origin: Sequence[str] = (),
        allow_host: Sequence[str] = (),
        allow_write: Sequence[str] = (),
        allow_writes: bool = False,
        allow_read: Sequence[str] = (),
        read_policy: str = "auto",
        mutation_url: Sequence[str] = (),
        deny_url: Sequence[str] = (),
        secret: Sequence[str] = (),
        storage_state: Path | None = None,
        save_storage_state: Path | None = None,
        confirm: str = "dismiss",
        header: Sequence[str] = (),
        cookie: Sequence[str] = (),
        simulate_writes: bool = False,
        private: str = "strict",
        environ: Mapping[str, str] | None = None,
    ) -> Boundary:
        """Check the limits and fix them. ``secret`` names environment variables; their values are
        read from ``environ`` here, so no secret is ever written on a command line."""

        if private not in PRIVATE_MODES:
            raise BoundaryError("--private must be strict or heuristic.")
        origins = tuple(dict.fromkeys(_origin(item, what="--allow-origin") for item in allow_origin))
        hosts = []
        for item in allow_host:
            name = item.strip().lower()
            if not name or "/" in name or "@" in name or " " in name:
                raise BoundaryError(f"--allow-host {item!r} must be a host name, with a port if it needs one.")
            normalized, _ = _url(f"http://{name}", what="--allow-host")
            parts = urlsplit(f"http://{name}")
            host = urlsplit(normalized).netloc
            # Keep an explicitly supplied :80: a bare host means any port.
            if parts.port == 80:
                host += ":80"
            hosts.append(host)
        if read_policy not in {"auto", "explicit", "permissive"}:
            raise BoundaryError("--read-policy must be auto, explicit, or permissive.")
        reads = tuple(_pattern(item, what="--allow-read") for item in allow_read)
        mutations = tuple(_pattern(item, what="--mutation-url") for item in mutation_url)
        secrets: dict[str, str] = {}
        for name in secret:
            if not SECRET_NAME.match(name):
                raise BoundaryError(f"--secret {name!r} must be the name of an environment variable in capitals.")
            value = (environ or {}).get(name, "")
            if len(value) < MIN_SECRET_LENGTH:
                raise BoundaryError(
                    f"--secret {name}: the environment has no value for it of at least {MIN_SECRET_LENGTH} characters."
                )
            secrets[name] = value
        if storage_state is not None and not storage_state.is_file():
            raise BoundaryError(f"--storage-state {storage_state} is not a file.")
        if any(not pattern.strip() for pattern in deny_url):
            raise BoundaryError("--deny-url needs an address pattern.")
        needs_list = {
            "--allow-write": bool(allow_write),
            "--allow-writes": allow_writes,
            "--secret": bool(secrets),
            "--storage-state": storage_state is not None,
            "--allow-host": bool(hosts),
        }
        asked = [flag for flag, given in needs_list.items() if given]
        if asked and not origins:
            raise BoundaryError(
                f"{', '.join(asked)} needs --allow-origin. Without a list of origins the browser may talk to "
                "any site, and a session that holds credentials or sends writes must not."
            )
        if confirm not in {"accept", "dismiss"}:
            raise BoundaryError("--confirm must be accept or dismiss.")
        if confirm == "accept" and not origins:
            raise BoundaryError("--confirm accept needs --allow-origin.")
        if save_storage_state and (not save_storage_state.parent.is_dir() or os.path.lexists(save_storage_state)):
            raise BoundaryError("--save-storage-state needs a new file in an existing directory.")
        if (header or cookie or storage_state or save_storage_state) and (len(origins) != 1 or hosts):
            raise BoundaryError("Authenticated sessions require exactly one --allow-origin and no extra hosts.")
        auth_headers = _credentials(header, environ or {}, header=True)
        auth_cookies = _credentials(cookie, environ or {}, header=False)
        rules = tuple(WriteRule.parse(item, origins) for item in allow_write)
        return cls(
            origins=origins,
            hosts=tuple(dict.fromkeys(hosts)),
            write_rules=rules,
            all_writes=allow_writes,
            read_policy=("explicit" if origins else "permissive") if read_policy == "auto" else read_policy,
            read_urls=reads,
            mutation_urls=mutations,
            deny=tuple(_pattern(pattern, what="--deny-url", socket=True) for pattern in deny_url),
            secrets=secrets,
            save_storage_state=save_storage_state.absolute() if save_storage_state else None,
            confirm=confirm,
            headers=auth_headers,
            cookies=auth_cookies,
            simulate_writes=simulate_writes,
            initial_state=_session_state(storage_state, origins[0]) if storage_state else None,
            storage_state=storage_state.resolve() if storage_state is not None else None,
            private=private,
        )

    @property
    def sealed(self) -> bool:
        return bool(self.origins)

    @property
    def sends_writes(self) -> bool:
        return self.all_writes or bool(self.write_rules)

    def endpoints(self) -> tuple[str, ...]:
        """Every ``host:port`` the browser may connect to when sealed. A host listed without a port
        is ``host:*``."""

        extra = [host if re.search(r":\d+\Z", host) else f"{host}:*" for host in self.hosts]
        return tuple(dict.fromkeys([*(_endpoint(origin) for origin in self.origins), *extra]))

    def to_bridge(self) -> dict[str, Any]:
        """The limits as bridge.js takes them. No secret value is in it."""

        return {
            "sealed": self.sealed,
            "confirm": self.confirm,
            "origins": list(self.origins),
            "endpoints": list(self.endpoints()),
            "hosts": list(self.hosts),
            "writes": {"all": self.all_writes, "rules": [rule.to_json() for rule in self.write_rules]},
            "reads": {"policy": self.read_policy, "rules": list(self.read_urls), "mutations": list(self.mutation_urls)},
            "deny": list(self.deny),
            "storage_state": str(self.storage_state) if self.storage_state is not None else None,
        }

    def save_state(self, state: dict[str, Any]) -> None:
        """Publish a complete, owner-readable state file without replacing any existing entry."""
        destination = self.save_storage_state
        if destination is None:
            return
        scoped = _scoped_state(state, self.origins[0])
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, encoding="utf-8") as draft:
                json.dump(scoped, draft)
                draft.flush()
                os.link(draft.name, destination)
        except OSError:
            raise BoundaryError("Could not save session storage to a new private file.") from None

    def refusal(self, url: str) -> tuple[str, str] | None:
        """Why the agent may not open ``url``, as an error code and a sentence, or None when it may.
        The gate checks the same again for every request, so this is only the early, clear answer."""

        try:
            origin, address = _url(url, what="Address")
        except BoundaryError as error:
            return "BROWSER_ADDRESS_NOT_ALLOWED", str(error)
        parts = urlsplit(address)
        if any(glob_matches(pattern, address) for pattern in self.deny):
            return "BROWSER_ADDRESS_NOT_ALLOWED", "This server was started with a rule that denies that address."
        if self.sealed:
            if origin not in self.origins:
                return "BROWSER_ORIGIN_NOT_ALLOWED", f"{origin or url} is not an allowed origin."
        elif is_private_host(parts.hostname):
            return (
                "BROWSER_ADDRESS_PRIVATE",
                f"{parts.hostname} is on this computer or a private network, which the agent may not read "
                "unless the server lists that origin.",
            )
        mutation = any(glob_matches(pattern, address) for pattern in self.mutation_urls) or any(
            key.lower() == "_method" for key, value in parse_qsl(parts.query, keep_blank_values=True)
        )
        can_write = (mutation and self.all_writes) or any(
            (rule.method == "GET" or (mutation and rule.method == "*")) and glob_matches(rule.pattern, address)
            for rule in self.write_rules
        )
        if (mutation or self.read_policy == "explicit") and not can_write:
            if mutation or not any(glob_matches(pattern, address) for pattern in self.read_urls):
                return (
                    "BROWSER_ADDRESS_NOT_ALLOWED",
                    "This GET address has no applicable operator read or write permission.",
                )
        return None

    def scrub(self, value: Any) -> Any:
        """Project one result. Browser sessions reuse ``make_redactor`` to retain learned values."""
        return self.make_redactor().clean(value)

    @property
    def credential_values(self) -> tuple[str, ...]:
        state = self.initial_state or {}
        return (
            *self.headers.values(),
            *self.cookies.values(),
            *(c["value"] for c in state.get("cookies", [])),
            *(v["value"] for o in state.get("origins", []) for v in o.get("localStorage", [])),
        )

    def browser_config(self) -> dict[str, Any]:
        """Private process input. Operator credentials must never appear in a tool result."""
        return {
            **self.to_bridge(),
            "headers": dict(self.headers),
            "cookies": dict(self.cookies),
            "initial_state": self.initial_state,
            "simulate_writes": self.simulate_writes,
        }

    def make_redactor(self) -> PrivacyRedactor:
        redactor = PrivacyRedactor(self.private)
        for name, value in self.secrets.items():
            redactor.remember([value], f"[secret {name}]")
        redactor.remember(getattr(self, "credential_values", ()))
        return redactor

    def privacy_description(self) -> str:
        fields = "all text-entry field values" if self.private == "strict" else "recognized sensitive field values"
        return (
            f"Privacy ({self.private}): {fields} are withheld and remembered for this session. Declared and "
            "imported credentials and URL parameter values are redacted from output (heuristic mode uses sensitive names). "
            "This does not recognize arbitrary secrets in prose or arbitrary transformations of values. "
            "Values shorter than four characters are hidden as whole values, not substrings of prose. "
            "data-lift-secret=true adds protection; only the operator can choose the weaker heuristic policy."
        )

    def describe(self) -> list[str]:
        """The limits in plain sentences, for the person who starts the server and for the agent."""

        reads = (
            "Reads: only operator-classified URL patterns; unknown GET, HEAD and OPTIONS addresses are refused."
            if self.read_policy == "explicit"
            else "Reads: unclassified GET, HEAD and OPTIONS addresses are allowed; their method does not prove they are read-only."
        )
        channels = (
            "Workers: disabled. New tabs: refused before network contact. Frames: the top document's exact origin only."
        )
        if not self.sealed:
            return [
                "Pages: any public http or https page. Pages on this computer or a private network are refused.",
                "Writes: none. Every request that is not GET, HEAD, or OPTIONS is stopped and listed.",
                "Credentials: none. This session is not signed in anywhere.",
                reads,
                channels,
            ]
        reach = list(self.origins) + [f"{host} (requests only)" for host in self.hosts]
        lines = [
            f"Pages: only {', '.join(self.origins)}.",
            f"Requests: only to {', '.join(reach)}. Nothing else is reached, by a page, a script, a redirect, "
            "a socket, or a new tab.",
        ]
        if self.all_writes:
            lines.append(
                "Writes: mutation channels are permitted on the listed origins, subject to deny and channel rules."
            )
        elif self.write_rules:
            rules = "; ".join(f"{rule.method} {rule.pattern}" for rule in self.write_rules)
            lines.append(f"Writes: only {rules}. Every other one is stopped and listed.")
        else:
            lines.append("Writes: none. Every request that is not GET, HEAD, or OPTIONS is stopped and listed.")
        if self.deny:
            lines.append(f"Never requested: {', '.join(self.deny)}.")
        held = [f"secret {name}" for name in self.secrets] + (
            ["a scoped sign-in"] if self.storage_state or self.headers or self.cookies else []
        )
        if held:
            lines.append(
                f"Credentials: {', '.join(held)}. A secret is filled in by its name, into a secret field only, "
                "and its value is taken out of every result."
            )
        else:
            lines.append("Credentials: none.")
        lines.extend([reads, channels, f"Confirm dialogs: {self.confirm}."])
        if self.simulate_writes:
            lines.append("Blocked HTTP writes: simulate success to the page and report blocked to the agent.")
        if self.save_storage_state:
            lines.append("Session state: save host-only cookies and local storage to a private file on close.")
        if self.read_urls:
            lines.append(f"Read URL patterns: {', '.join(self.read_urls)}.")
        if self.mutation_urls:
            lines.append(
                f"Mutation URL patterns: {', '.join(self.mutation_urls)}. Matching GETs require write permission."
            )
        return lines
