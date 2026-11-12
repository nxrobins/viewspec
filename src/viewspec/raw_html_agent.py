"""Controls, accessible names, and forms for the "agent" profile of ``lift_html``.

The agent profile keeps the parts of a page that a browser agent reads and operates: links,
buttons, form fields, ARIA widgets, and the forms that group them. This module turns the lifted
nodes into ``HtmlControl`` and ``HtmlForm`` records and into a tool schema for each form.

It works only on the sanitized, lifted nodes. It makes no network calls and runs no script.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from viewspec.raw_html_patterns import pattern_is_bounded

if TYPE_CHECKING:
    from viewspec.raw_html import LiftNode

# Attributes a browser-side snapshot may add before the lift. ``data-lift-hidden`` marks the top of
# a subtree the user cannot see; ``data-lift-clickable`` marks an element that looks clickable (for
# example, it has a pointer cursor) but has no semantic role.
AGENT_HINT_ATTR_PREFIX = "data-lift-"
HINT_HIDDEN = "data-lift-hidden"
HINT_CLICKABLE = "data-lift-clickable"
# ``data-lift-display`` says how the page lays an element out when that differs from its tag's
# default: "block" on a span shown as a line of its own, "inline" on a div that runs into its line.
HINT_DISPLAY = "data-lift-display"
# A browser-side snapshot may also give each element a ``data-lift-ref``: a handle the browser can
# turn back into that exact element, so an agent can act on what it read.
HINT_REF = "data-lift-ref"
# Each tree scope (the page, a same-origin frame, an open shadow root) has ids of its own, and a
# snapshot flattens them into one document. ``data-lift-scope`` names the scope of an element that has
# or names an id (id, for, aria-labelledby, ...), when that is not the page: a frame's document is
# "2", and a shadow root in it is "2.5". An element without it is in the page, scope "0".
HINT_SCOPE = "data-lift-scope"
TOP_SCOPE = "0"
# Provenance attributes that ViewSpec writes into compiled artifacts. On a ViewSpec page they name
# each action exactly, so an agent does not have to guess.
AGENT_VIEWSPEC_DATA_ATTRS = frozenset(
    {"data-action-id", "data-action-kind", "data-action-target-ref", "data-binding-id", "data-ir-id"}
)

ACTION_ROLES = frozenset({"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "treeitem"})
FIELD_ROLES = frozenset(
    {"checkbox", "combobox", "listbox", "radio", "searchbox", "slider", "spinbutton", "switch", "textbox"}
)
INPUT_ACTION_TYPES = frozenset({"button", "image", "reset", "submit"})
# Field roles whose accessible name comes only from the author (a label, aria-label), never from the
# field's own content: a listbox is not named after its options.
NAME_FROM_AUTHOR = frozenset({"combobox", "listbox", "searchbox", "slider", "spinbutton", "textbox"})
# Controls that hold a list of ARIA options to choose from.
ARIA_CHOICE = frozenset({"combobox", "listbox"})
# Elements that hold more than one widget: the text beside an empty combobox input is not looked
# for past them.
WIDGET_BOUNDARY_TAGS = frozenset(
    {
        "article",
        "aside",
        "body",
        "dialog",
        "document",
        "fieldset",
        "footer",
        "form",
        "header",
        "li",
        "main",
        "nav",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }
)
# What never counts as the text a widget shows beside its input: labels, buttons, and other fields.
BESIDE_SKIP_TAGS = frozenset({"button", "input", "label", "select", "textarea"})
# Controls the lift guesses from a tab stop or a click listener, with no role that says what they are.
GUESSED_CONTROLS = frozenset({"clickable", "focusable"})
# ARIA controls that own what they hold, so nothing inside them is a control of its own.
OWNS_CONTENT_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "listbox",
        "menuitemcheckbox",
        "menuitemradio",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
    }
)
# Regions where a page tells the user what happened: an error, a confirmation, a log line.
MESSAGE_ROLES = frozenset({"alert", "alertdialog", "log", "status"})
MESSAGE_LIVE = frozenset({"assertive", "polite"})
DEFAULT_INPUT_ACTION_NAMES = {"submit": "Submit", "reset": "Reset", "image": "Submit"}
CONTROL_CONTENT_TAGS = frozenset({"input", "option", "optgroup", "select", "textarea"})
# Elements that flow inside a line of text. The browser shows their text right next to the text
# around them, so "<i>Doctor Who</i>, series 12" reads "Doctor Who, series 12". Every other element
# starts its own line, box, or cell, which reads as a space between words.
INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "button",
        "code",
        "em",
        "i",
        "img",
        "input",
        "label",
        "output",
        "select",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "textarea",
    }
)
SENSITIVE_AUTOCOMPLETE = frozenset(
    {
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
        "cc-number",
        "current-password",
        "new-password",
        "one-time-code",
    }
)
# A field is named by words: "apiKey", "api_key", and "API key" are all ("api", "key"). The rule reads
# whole words, so "businessName" does not hold "ssn" and "passenger_type" does not hold "pass".
NAME_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
# One word that marks a secret on its own, however the rest of the name reads.
SECRET_WORDS = frozenset(
    {
        *("pass", "password", "passwd", "passcode", "passphrase", "pwd", "pin"),
        *("secret", "token", "bearer", "credential", "credentials", "auth", "apikey"),
        *("otp", "totp", "mfa", "cvv", "cvc", "ssn", "iban", "cardnumber", "ccnum"),
    }
)
# Two words in a row that mark a secret together, where neither does alone: "key" is also a search
# key and a keyboard key, and "code" a postal code, so each counts only after a word that says which.
SECRET_PHRASES = frozenset(
    {
        *((first, "key") for first in ("api", "access", "private", "signing", "encryption", "license")),
        *((first, "code") for first in ("security", "verification", "recovery", "backup", "sort")),
        *((first, second) for first in ("card", "cc", "account", "routing") for second in ("num", "number")),
        ("2", "fa"),
    }
)
# An input whose value is one the page's author set for a choice or a button, not text a person typed.
# Its name is not read: a radio group named "token_type" has options, not a secret.
AUTHOR_VALUE_INPUT_TYPES = frozenset(
    {"button", "checkbox", "color", "file", "image", "radio", "range", "reset", "submit"}
)
# Attributes of a field that name it. A label element is read by ``label_secret_ids``.
NAMING_ATTRS = ("name", "id", "aria-label", "placeholder", "title")
# A label longer than this is a sentence ("We never ask for your password by email"), not a name.
MAX_SECRET_LABEL_LENGTH = 60
# Set by the lift on a field that only its label marks as secret, so that every later reader of the
# node agrees. A browser-side snapshot may set it too.
HINT_SECRET = "data-lift-secret"
NUMBER_INPUT_TYPES = frozenset({"number", "range"})
STRING_FORMATS = {
    "date": "date",
    "datetime-local": "date-time",
    "email": "email",
    "time": "time",
    "url": "uri",
}
SLUG_RE = re.compile(r"[^a-z0-9]+")
# Native inputs that hold a date or a time, and the shape their value has.
DATE_INPUT_TYPES = {
    "date": "YYYY-MM-DD",
    "datetime-local": "YYYY-MM-DDTHH:MM",
    "month": "YYYY-MM",
    "time": "HH:MM",
    "week": "YYYY-Www",
}
# A text field that holds a date, by its name, id, or label ("Check-in", "date_of_birth"), or by a
# placeholder that shows a date's shape ("DD/MM/YYYY"). Such a field often opens a calendar.
DATE_HINT_RE = re.compile(
    r"(?<![a-z])(?:dates?|date.?picker|dob|birth\w*|check.?in|check.?out|arrival|departure|deadline|due)(?![a-z])",
    re.IGNORECASE,
)
DATE_PLACEHOLDER_RE = re.compile(r"(?<![a-z])(?:dd|mm|yy|yyyy)\s*[/.\-]\s*(?:dd|mm|yy|yyyy)(?![a-z])", re.IGNORECASE)
# A text field that holds a whole date range: its name says so ("Travel dates", "date_range"), or its
# placeholder shows two dates ("DD/MM/YYYY - DD/MM/YYYY").
DATE_RANGE_RE = re.compile(r"(?<![a-z])(?:date.?range|dates)(?![a-z])", re.IGNORECASE)
# Two date fields that hold the start and the end of one range, by the words in their names. The
# end field must be the next date field after the start, in the same form.
DATE_RANGE_PAIRS = tuple(
    (
        re.compile(rf"(?<![a-z])(?:{start})(?![a-z])", re.IGNORECASE),
        re.compile(rf"(?<![a-z])(?:{end})(?![a-z])", re.IGNORECASE),
    )
    for start, end in (
        (r"check.?in", r"check.?out"),
        (r"arriv\w*", r"depart\w*"),
        (r"depart\w*|outbound|leav\w*", r"return\w*|inbound"),
        (r"start\w*|begin\w*", r"end\w*|finish\w*"),
        (r"from", r"to|until|till"),
        (r"pick.?up", r"drop.?off"),
    )
)


def names_a_secret(text: str) -> bool:
    """True when ``text``, a field's name, id, or label, or a URL parameter's name, names a secret."""

    words = [word.lower() for word in NAME_WORD_RE.findall(text)]
    return any(word in SECRET_WORDS for word in words) or any(
        pair in SECRET_PHRASES for pair in zip(words, words[1:], strict=False)
    )


def is_sensitive_control(tag: str, attrs: Mapping[str, str]) -> bool:
    """True when a field may hold a secret, so the lift must not keep its value."""

    if tag not in {"input", "textarea"}:
        return False
    kind = attrs.get("type", "").lower()
    if kind == "password" or attrs.get(HINT_SECRET, "").lower() == "true":
        return True
    if tag == "input" and kind in AUTHOR_VALUE_INPUT_TYPES:
        return False
    tokens = set(attrs.get("autocomplete", "").lower().split())
    if tokens & SENSITIVE_AUTOCOMPLETE:
        return True
    return any(names_a_secret(attrs.get(name, "")) for name in NAMING_ATTRS)


def label_secret_ids(nodes: Sequence[LiftNode]) -> frozenset[str]:
    """Ids of the fields that only a label marks as secret: a ``<label for>``, a label around the
    field, or the elements its ``aria-labelledby`` names. ``is_sensitive_control`` reads the field's
    own attributes; this reads the words a person sees beside it ("API key").
    """

    page = _Page(nodes)
    found: set[str] = set()
    for node in nodes:
        if node.tag not in {"input", "textarea"} or is_sensitive_control(node.tag, node.attrs):
            continue
        if node.tag == "input" and node.attrs.get("type", "").lower() in AUTHOR_VALUE_INPUT_TYPES | {"hidden"}:
            continue
        labels = [page.labelled_by(node), *(page.text(label, skip_controls=True) for label in page.labels(node))]
        wrapper = next((ancestor for ancestor in page.ancestors(node) if ancestor.tag == "label"), None)
        if wrapper is not None:
            labels.append(page.text(wrapper, skip_controls=True))
        if any(len(label) <= MAX_SECRET_LABEL_LENGTH and names_a_secret(label) for label in labels):
            found.add(node.id)
    return frozenset(found)


@dataclass(frozen=True)
class HtmlOption:
    """An option of a select, or a ``role="option"`` item of an ARIA listbox or combobox list.

    ``node_id`` and ``ref`` name the option's element, so a driver can click an ARIA option.
    """

    value: str
    label: str
    selected: bool
    disabled: bool
    node_id: str | None = None
    ref: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "value": self.value,
            "label": self.label,
            "selected": self.selected,
            "disabled": self.disabled,
        }
        if self.ref is not None:
            payload["ref"] = self.ref
        return payload


@dataclass(frozen=True)
class HtmlControl:
    """One thing on the page that an agent can activate (``kind="action"``) or fill in (``"field"``)."""

    node_id: str
    kind: str
    control: str
    name: str
    field_name: str | None
    value: str | None
    checked: bool | None
    required: bool
    disabled: bool
    form_id: str | None
    submits: bool
    href: str | None
    action_id: str | None
    options: tuple[HtmlOption, ...] = ()
    group: str | None = None
    readonly: bool = False
    secret: bool = False
    ref: str | None = None
    tag: str = ""
    expanded: bool | None = None
    minimum: str | None = None
    maximum: str | None = None
    popup: str | None = None
    date: bool = False
    accept: str | None = None
    multiple: bool = False
    date_range: str | None = None  # "start" or "end" of a pair of date fields, or "range" for one field.
    range_with: str | None = None  # The node id of the other field of a pair.
    download: str | None = None  # A link's download attribute: the file name it suggests, or "".
    # What the browser checks before it sends the form: a text field's pattern and length limits, and
    # a number field's step. ``minimum`` and ``maximum`` hold min and max.
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    step: str | None = None
    # ARIA state an action may toggle: a toolbar button's aria-pressed, a tab's or an option's
    # aria-selected. ``expanded`` holds aria-expanded for any control (a disclosure, a menu button).
    pressed: bool | None = None
    selected: bool | None = None
    # The aria-sort of the column header a sort button sits in: "ascending", "descending", "none",
    # or "other". A click that reorders the rows changes this and nothing else the diff can see.
    sort: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_id": self.node_id,
            "ref": self.ref,
            "tag": self.tag,
            "kind": self.kind,
            "control": self.control,
            "name": self.name,
            "field_name": self.field_name,
            "value": self.value,
            "checked": self.checked,
            "required": self.required,
            "disabled": self.disabled,
            "form_id": self.form_id,
            "submits": self.submits,
            "href": self.href,
            "action_id": self.action_id,
            "group": self.group,
            "readonly": self.readonly,
            "secret": self.secret,
            "expanded": self.expanded,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "popup": self.popup,
            "date": self.date,
            "accept": self.accept,
            "multiple": self.multiple,
            "date_range": self.date_range,
            "range_with": self.range_with,
            "download": self.download,
            "pattern": self.pattern,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "step": self.step,
            "pressed": self.pressed,
            "selected": self.selected,
            "sort": self.sort,
        }
        if self.options:
            payload["options"] = [option.to_json() for option in self.options]
        return payload


@dataclass(frozen=True)
class HtmlForm:
    node_id: str
    name: str
    action: str | None
    method: str
    control_ids: tuple[str, ...]
    submit_ids: tuple[str, ...]
    ref: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "ref": self.ref,
            "name": self.name,
            "action": self.action,
            "method": self.method,
            "control_ids": list(self.control_ids),
            "submit_ids": list(self.submit_ids),
        }


@dataclass(frozen=True)
class HtmlMessage:
    """Text in a region where the page reports what happened (``role="alert"``, ``aria-live``, ...).

    ``kind`` is the region's role (``alert``, ``alertdialog``, ``log``, ``status``), or ``live`` for an
    ``aria-live`` region without one of those roles.
    """

    node_id: str
    kind: str
    text: str

    def to_json(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class ControlExtraction:
    controls: tuple[HtmlControl, ...]
    forms: tuple[HtmlForm, ...]
    messages: tuple[HtmlMessage, ...]
    # (node_id, role, text, confidence) for each control, in document order.
    roles: tuple[tuple[str, str, str, float], ...]
    # Nodes that must not also get content roles: hidden nodes, and nodes inside controls.
    excluded_ids: frozenset[str]


def _scope(node: LiftNode) -> str:
    return node.attrs.get(HINT_SCOPE) or TOP_SCOPE


class _Page:
    """Indexes over the lifted nodes. Nodes arrive in document (pre-)order."""

    def __init__(self, nodes: Sequence[LiftNode]) -> None:
        self.nodes = nodes
        self.by_id = {node.id: node for node in nodes}
        # Ids are looked up in the scope of the element that names them (see HINT_SCOPE): two frames
        # that both have an "email" field each label their own.
        self.by_html_id: dict[tuple[str, str], LiftNode] = {}
        self.labels_for: dict[tuple[str, str], list[LiftNode]] = {}
        for node in nodes:
            scope = _scope(node)
            html_id = node.attrs.get("id")
            if html_id and (scope, html_id) not in self.by_html_id:
                self.by_html_id[(scope, html_id)] = node
            if node.tag == "label" and node.attrs.get("for"):
                self.labels_for.setdefault((scope, node.attrs["for"]), []).append(node)
        # The list a combobox controls (aria-controls, or aria-owns) belongs to that combobox: it is
        # not a field of its own, and its options are the combobox's options.
        self.popup_owner: dict[str, str] = {}
        self.popup_of: dict[str, str] = {}
        for node in nodes:
            if _role(node) != "combobox":
                continue
            for attr in ("aria-controls", "aria-owns"):
                for html_id in node.attrs.get(attr, "").split():
                    target = self.referenced(node, html_id)
                    if target is not None and target.id != node.id and target.id not in self.popup_owner:
                        self.popup_owner[target.id] = node.id
                        self.popup_of.setdefault(node.id, target.id)
        self.hidden = self._hidden_ids()
        self._text: dict[tuple[str, bool], str] = {}
        self._child_index: dict[str, int] | None = None
        self._form_headings: dict[str, str] | None = None

    def child_index(self, node: LiftNode) -> int | None:
        """Where ``node`` is among its parent's children; None for a node its parent does not list."""

        if self._child_index is None:
            self._child_index = {
                child_id: index for parent in self.nodes for index, child_id in enumerate(parent.child_ids)
            }
        return self._child_index.get(node.id)

    def form_heading(self, form: LiftNode) -> str:
        """The text of the first legend or heading inside ``form`` that has any, or ""."""

        if self._form_headings is None:
            self._form_headings = {}
            for node in self.nodes:
                if node.tag not in {"legend", "h1", "h2", "h3", "h4", "h5", "h6"}:
                    continue
                inside = [ancestor.id for ancestor in self.ancestors(node) if ancestor.tag == "form"]
                if inside and (text := self.text(node)):
                    for form_id in inside:
                        self._form_headings.setdefault(form_id, text)
        return self._form_headings.get(form.id, "")

    def owned(self, node: LiftNode, html_id: str) -> LiftNode | None:
        """The element with ``html_id`` in ``node``'s own scope, as a browser finds a label's ``for``
        and a field's ``form``."""

        return self.by_html_id.get((_scope(node), html_id))

    def referenced(self, node: LiftNode, html_id: str) -> LiftNode | None:
        """The element an ARIA reference on ``node`` names: in its own scope, or else in its
        document, as the in-page library's byId finds it for a shadow-DOM element."""

        scope = _scope(node)
        found = self.by_html_id.get((scope, html_id))
        if found is None and "." in scope:
            found = self.by_html_id.get((scope.split(".", 1)[0], html_id))
        return found

    def labels(self, node: LiftNode) -> list[LiftNode]:
        """The ``<label for>`` elements of ``node``, in its own scope."""

        html_id = node.attrs.get("id")
        return self.labels_for.get((_scope(node), html_id), []) if html_id else []

    def labelled_by(self, node: LiftNode) -> str:
        """The name the elements ``node``'s ``aria-labelledby`` names give it, in order. Each gives its
        own ``aria-label``, or else its text; an element may name itself this way ("Remove" +
        "Recipient Name 1")."""

        targets = (self.referenced(node, ref) for ref in node.attrs.get("aria-labelledby", "").split())
        parts = [target.attrs.get("aria-label") or self.text(target) for target in targets if target is not None]
        return " ".join(part for part in parts if part).strip()

    def _hidden_ids(self) -> set[str]:
        hidden: set[str] = set()
        for node in self.nodes:
            own = (
                "hidden" in node.attrs
                or node.attrs.get("aria-hidden", "").lower() == "true"
                or node.attrs.get(HINT_HIDDEN, "false").lower() != "false"
            )
            if own or (node.parent_id is not None and node.parent_id in hidden):
                hidden.add(node.id)
        return hidden

    def ancestors(self, node: LiftNode) -> Iterable[LiftNode]:
        current = self.by_id.get(node.parent_id) if node.parent_id else None
        while current is not None:
            yield current
            current = self.by_id.get(current.parent_id) if current.parent_id else None

    def text(self, node: LiftNode, *, skip_controls: bool = False) -> str:
        """Visible text of a subtree, in page order. ``skip_controls`` leaves out what fields contain
        (option text)."""

        return " ".join(self._raw_text(node, skip_controls).split())

    def _raw_text(self, node: LiftNode, skip_controls: bool) -> str:
        key = (node.id, skip_controls)
        if key not in self._text:
            children: list[tuple[bool, str | None]] = []
            for child_id in node.child_ids:
                child = self.by_id[child_id]
                skipped = child.id in self.hidden or (skip_controls and child.tag in CONTROL_CONTENT_TAGS)
                children.append((is_inline(child), None if skipped else self._raw_text(child, skip_controls)))
            self._text[key] = join_text(node, children)
        return self._text[key]

    def first_line(self, node: LiftNode) -> str:
        """The first line of a subtree's text that holds a letter or a digit: an option's title, when
        a description sits under it, and not the icon that may sit above it. Else its first line."""

        lines = [" ".join(line.split()) for line in self._raw_text(node, False).split("\n")]
        lines = [line for line in lines if line]
        for line in lines:
            if WORD_RE.search(line):
                return line
        return lines[0] if lines else ""

    def image_alt(self, node: LiftNode) -> str:
        for child_id in node.child_ids:
            child = self.by_id[child_id]
            if child.tag == "img" and child.attrs.get("alt"):
                return child.attrs["alt"]
            found = self.image_alt(child)
            if found:
                return found
        return ""


def _is_date_field(node: LiftNode, control: str, name: str) -> bool:
    if node.tag == "input" and control in DATE_INPUT_TYPES:
        return True
    # A date combobox (the W3C APG "Date Picker Combobox": an input with role="combobox" and a
    # calendar button beside it) is a date field too.
    if node.tag != "input" or control not in {"text", "search", "textbox", "combobox"}:
        return False
    hints = (node.attrs.get("name", ""), node.attrs.get("id", ""), name)
    return any(DATE_HINT_RE.search(hint) for hint in hints) or bool(
        DATE_PLACEHOLDER_RE.search(node.attrs.get("placeholder", ""))
    )


def _date_range_hints(page: _Page, control: HtmlControl) -> tuple[str, ...]:
    attrs = page.by_id[control.node_id].attrs
    hints = (attrs.get("name", ""), attrs.get("id", ""), control.name)
    return tuple(re.sub(r"([a-z])([A-Z])", r"\1 \2", hint) for hint in hints)  # "dateFrom" -> "date From"


def _single_day_field(control: HtmlControl) -> bool:
    """A date field that holds one day: a native date input, or a custom text date field."""

    return control.date and (control.control == "date" or control.control not in DATE_INPUT_TYPES)


def _date_ranges(page: _Page, controls: list[HtmlControl]) -> list[HtmlControl]:
    """Mark fields that hold a date range, and pairs of fields that hold its start and its end."""

    marked = list(controls)
    days = [index for index, control in enumerate(marked) if _single_day_field(control)]
    for index in days:
        control = marked[index]
        if control.control in DATE_INPUT_TYPES:
            continue
        placeholder = page.by_id[control.node_id].attrs.get("placeholder", "")
        hints = _date_range_hints(page, control)
        if len(DATE_PLACEHOLDER_RE.findall(placeholder)) >= 2 or any(DATE_RANGE_RE.search(hint) for hint in hints):
            marked[index] = replace(control, date_range="range")
    for first, second in zip(days, days[1:], strict=False):
        start, end = marked[first], marked[second]
        if start.date_range or end.date_range or start.form_id != end.form_id:
            continue
        own = page.by_id[start.node_id].attrs.get("aria-controls", "").split()
        shared = bool(own) and own == page.by_id[end.node_id].attrs.get("aria-controls", "").split()
        start_hints, end_hints = _date_range_hints(page, start), _date_range_hints(page, end)
        named = any(
            any(begins.search(hint) for hint in start_hints) and any(ends.search(hint) for hint in end_hints)
            for begins, ends in DATE_RANGE_PAIRS
        )
        if shared or named:
            marked[first] = replace(start, date_range="start", range_with=end.node_id)
            marked[second] = replace(end, date_range="end", range_with=start.node_id)
    return marked


def _role(node: LiftNode) -> str:
    return node.attrs.get("role", "").strip().lower().split(" ")[0]


def _classify(node: LiftNode) -> tuple[str, str, float] | None:
    """Return (kind, control, confidence) when the node is a control."""

    tag = node.tag
    attrs = node.attrs
    role = attrs.get("role", "").strip().lower().split(" ")[0]
    result: tuple[str, str, float] | None = None
    if tag == "a" and "href" in attrs:
        result = ("action", "link", 0.96)
    elif tag == "button":
        result = ("action", "button", 0.96)
    elif tag == "summary":
        result = ("action", "disclosure", 0.9)
    elif tag == "input":
        input_type = attrs.get("type", "text").lower() or "text"
        result = ("action", input_type, 0.96) if input_type in INPUT_ACTION_TYPES else ("field", input_type, 0.96)
    elif tag == "select":
        result = ("field", "select-multiple" if "multiple" in attrs else "select", 0.96)
    elif tag == "textarea":
        result = ("field", "textarea", 0.96)
    if role in ACTION_ROLES:
        result = ("action", role, max(result[2], 0.8) if result else 0.8)
    elif role in FIELD_ROLES:
        result = ("field", role, max(result[2], 0.8) if result else 0.8)
    if result is None and attrs.get("contenteditable", "missing").lower() in {"", "true", "plaintext-only"}:
        result = ("field", "textbox", 0.8)
    if result is None and _tab_stop(attrs):
        result = ("action", "focusable", 0.6)
    if result is None and attrs.get(HINT_CLICKABLE, "missing").lower() in {"", "true"}:
        result = ("action", "clickable", 0.55)
    return result


def _owns_content(node: LiftNode, kind: str, control: str) -> bool:
    """Whether everything inside this control is part of it, so nothing inside is a control of its own.

    ``node`` is a native or ARIA control (``_classify`` found it); ``kind`` is "action" or "field" and
    ``control`` is what ``_classify`` called it: "link", "button", "text", "combobox", "treeitem", ...
    A control that owns its content is named from it, and a link or button inside it is not counted.
    A control that does not own it is still named from it, and the controls inside it are counted too.
    Guessed controls ("clickable", "focusable") never own their content and are not passed here.
    """

    if node.tag in {"a", "button", "summary", "input", "select", "textarea"}:
        return True  # Native controls render what they hold as part of themselves.
    # ARIA roles whose children are presentational, and the choice widgets, whose options and
    # inner text field (the ARIA 1.1 combobox) are the control. A tree item or a menu item often
    # wraps the link the user follows, so it does not own its content.
    return control in OWNS_CONTENT_ROLES


def _guessed_wrappers(page: _Page, controls: Sequence[HtmlControl]) -> set[str]:
    """Guessed controls that hold real controls: drop them, the controls inside are what to act on.

    A page that listens for clicks on a ``div`` around an input and its button (event delegation)
    makes the ``div`` look clickable, but a click there does not do what the input or the button does.
    """

    guessed = {control.node_id for control in controls if control.control in GUESSED_CONTROLS}
    wrappers: set[str] = set()
    for control in controls:
        for ancestor in page.ancestors(page.by_id[control.node_id]):
            if ancestor.id in guessed:
                wrappers.add(ancestor.id)
    return wrappers


def _tab_stop(attrs: Mapping[str, str]) -> bool:
    try:
        return int(attrs.get("tabindex", "-1")) >= 0
    except ValueError:
        return False


def _accessible_name(page: _Page, node: LiftNode, kind: str, control: str) -> str:
    attrs = node.attrs
    text = page.labelled_by(node)
    if text:
        return text
    if attrs.get("aria-label"):
        return attrs["aria-label"]
    if node.tag in {"input", "select", "textarea"} and control not in INPUT_ACTION_TYPES:
        for label in page.labels(node):
            text = page.text(label, skip_controls=True)
            if text:
                return text
        for ancestor in page.ancestors(node):
            if ancestor.tag == "label":
                text = page.text(ancestor, skip_controls=True)
                if text:
                    return text
                break
    if node.tag == "input" and control in INPUT_ACTION_TYPES:
        if control == "image" and attrs.get("alt"):
            return attrs["alt"]
        return attrs.get("value") or DEFAULT_INPUT_ACTION_NAMES.get(control, "")
    if node.tag not in {"input", "select", "textarea"} and control not in NAME_FROM_AUTHOR:
        text = page.text(node) if kind == "action" else page.text(node, skip_controls=True)
        if text:
            return text
        alt = page.image_alt(node)
        if alt:
            return alt
    fallback = attrs.get("title") or attrs.get("placeholder") or ""
    if not fallback and kind == "field" and node.tag in {"input", "select", "textarea"}:
        fallback = _text_beside(page, node)
    return fallback


# The longest name a control or a group keeps. One long label can name every field inside it, so
# without a limit a small page gives a lift thousands of times its size.
MAX_NAME_LENGTH = 200


def _clip(name: str) -> str:
    return name if len(name) <= MAX_NAME_LENGTH else name[:MAX_NAME_LENGTH].rstrip()


# The longest phrase that is taken as the label written beside an unlabelled field.
LABEL_BESIDE_MAX = 40
# A letter or a digit in any script: what tells a line of words from a line that is only an icon.
WORD_RE = re.compile(r"[^\W_]")


def _text_beside(page: _Page, node: LiftNode) -> str:
    """The text written beside a field that nothing labels, as a person reads it: the text just before
    it in its block (``Date: <input>``), or just after a bare checkbox or radio (``<input> Remember
    me``); when that is blank, the inline element next to it (``<span>Amount:</span>``). Only a short,
    label-like phrase counts, without its colon; a sentence or a heading is not a label. The in-page
    library names such a field the same way.
    """

    parent = page.by_id.get(node.parent_id) if node.parent_id else None
    index = page.child_index(node) if parent is not None else None
    if parent is None or index is None:
        return ""
    after = node.tag == "input" and node.attrs.get("type", "").lower() in {"checkbox", "radio"}
    runs = parent.text_runs
    text = (runs[index + 1] if index + 1 < len(runs) else "") if after else (runs[index] if index < len(runs) else "")
    if not text.strip():
        # Walked in place: a copy of the siblings for every field of one parent is quadratic.
        positions = range(index + 1, len(parent.child_ids)) if after else range(index - 1, -1, -1)
        for position in positions:
            sibling = page.by_id[parent.child_ids[position]]
            if sibling.id in page.hidden:
                continue
            if is_inline(sibling) and _classify(sibling) is None:
                text = page.text(sibling)
            break
    text = " ".join(text.split()).rstrip(":").strip()
    return text if 0 < len(text) <= LABEL_BESIDE_MAX and not text.endswith(".") else ""


def _form_of(page: _Page, node: LiftNode) -> LiftNode | None:
    owner = page.owned(node, node.attrs["form"]) if node.attrs.get("form") else None
    if owner is not None and owner.tag == "form":
        return owner
    for ancestor in page.ancestors(node):
        if ancestor.tag == "form":
            return ancestor
    return None


def _disabled(page: _Page, node: LiftNode) -> bool:
    if "disabled" in node.attrs or node.attrs.get("aria-disabled", "").lower() == "true":
        return True
    return any(ancestor.tag == "fieldset" and "disabled" in ancestor.attrs for ancestor in page.ancestors(node))


def _group_name(page: _Page, node: LiftNode) -> str | None:
    """Name of the nearest group around a field: a fieldset's legend, or an ARIA group's label."""

    for ancestor in page.ancestors(node):
        if ancestor.tag == "form":
            return None
        role = ancestor.attrs.get("role", "").strip().lower()
        if ancestor.tag == "fieldset":
            for child_id in ancestor.child_ids:
                child = page.by_id[child_id]
                if child.tag == "legend" and page.text(child):
                    return page.text(child)
        if ancestor.tag == "fieldset" or role in {"group", "radiogroup"}:
            return page.labelled_by(ancestor) or ancestor.attrs.get("aria-label") or None
    return None


def _options(page: _Page, node: LiftNode) -> tuple[HtmlOption, ...]:
    options: list[HtmlOption] = []

    def visit(current: LiftNode, group_disabled: bool) -> None:
        for child_id in current.child_ids:
            child = page.by_id[child_id]
            if child.tag == "optgroup":
                visit(child, group_disabled or "disabled" in child.attrs)
            elif child.tag == "option":
                label = child.attrs.get("label") or page.text(child)
                options.append(
                    HtmlOption(
                        value=child.attrs.get("value", page.text(child)),
                        label=label,
                        selected="selected" in child.attrs,
                        disabled=group_disabled or "disabled" in child.attrs,
                        node_id=child.id,
                        ref=child.attrs.get(HINT_REF) or None,
                    )
                )

    visit(node, False)
    return tuple(options)


def _aria_options(page: _Page, root: LiftNode) -> tuple[HtmlOption, ...]:
    """The visible ``role="option"`` items under ``root``, in document order."""

    if root.id in page.hidden:
        return ()
    options: list[HtmlOption] = []

    def visit(current: LiftNode) -> None:
        for child_id in current.child_ids:
            child = page.by_id[child_id]
            if child.id in page.hidden:
                continue
            if _role(child) == "option":
                # An option that shows a description under its title is named by the title.
                label = child.attrs.get("aria-label") or page.first_line(child)
                options.append(
                    HtmlOption(
                        value=label,
                        label=label,
                        selected=child.attrs.get("aria-selected", "").lower() == "true",
                        disabled=child.attrs.get("aria-disabled", "").lower() == "true",
                        node_id=child.id,
                        ref=child.attrs.get(HINT_REF) or None,
                    )
                )
            else:
                visit(child)

    visit(root)
    return tuple(options)


def _choice_state(page: _Page, node: LiftNode, control: str) -> tuple[tuple[HtmlOption, ...], bool | None]:
    """Options and expanded state of an ARIA combobox or listbox."""

    if control == "listbox":
        return _aria_options(page, node), None
    popup_id = page.popup_of.get(node.id)
    popup = page.by_id[popup_id] if popup_id else node
    options = _aria_options(page, popup)
    expanded_attr = node.attrs.get("aria-expanded", "").lower()
    if expanded_attr in {"true", "false"}:
        expanded = expanded_attr == "true"
    else:
        expanded = popup_id is not None and popup_id not in page.hidden
    return options, expanded


def _field_value(
    page: _Page, node: LiftNode, control: str, options: tuple[HtmlOption, ...]
) -> tuple[str | None, bool | None]:
    """Return (value, checked) as the page shows it now."""

    attrs = node.attrs
    if control in {"checkbox", "radio", "switch"} and node.tag == "input":
        return attrs.get("value", "on"), "checked" in attrs
    if control in {"checkbox", "radio", "switch"}:
        return None, attrs.get("aria-checked", "false").lower() == "true"
    if node.tag == "select":
        chosen = [option.value for option in options if option.selected]
        if not chosen and options and control == "select":
            chosen = [options[0].value]  # A single select shows its first option when none is marked.
        return ", ".join(chosen), None
    if node.tag == "textarea":
        return node.text, None
    if node.tag == "input" and control == "file":
        # A browser snapshot names the chosen files in data-lift-files; the files stay in the page.
        return attrs.get("data-lift-files", ""), None
    if node.tag == "input" and control == "combobox":
        # A combobox input shows its value in the field or, as react-select and Ant Design do, in a
        # box beside it while the input stays empty.
        return attrs.get("value") or _shown_beside(page, node), None
    if node.tag == "input":
        return attrs.get("value", ""), None
    if control == "listbox":
        return ", ".join(option.label for option in options if option.selected), None
    if control == "combobox":
        return _text_outside_lists(page, node), None
    return page.text(node, skip_controls=True) or attrs.get("aria-valuenow"), None


def _shown_beside(page: _Page, node: LiftNode) -> str:
    """The text a widget shows beside an empty input: the text of the nearest ancestor that has any.

    The input itself, hidden nodes, lists and options, live regions, labels, buttons, and other fields
    do not count. The search stops at a form, a list item, a table cell, or a section, which hold more
    than one widget, so the text of the page around the input is never taken for its value.
    """

    def skip(child: LiftNode) -> bool:
        return (
            child.id == node.id
            or child.id in page.hidden
            or child.tag in BESIDE_SKIP_TAGS
            or _role(child) in {"listbox", "option"}
            or _message_kind(child) is not None
        )

    for ancestor in page.ancestors(node):
        if ancestor.tag in WIDGET_BOUNDARY_TAGS:
            return ""
        if ancestor.tag in BESIDE_SKIP_TAGS:
            continue  # A label around the input names it; its text is not the value.
        text = subtree_text(ancestor, page.by_id, skip=skip)
        if text:
            return text
    return ""


def _text_outside_lists(page: _Page, node: LiftNode) -> str:
    """What a combobox shows as its value: its text, without the list of options it may wrap."""

    return subtree_text(
        node, page.by_id, skip=lambda child: child.id in page.hidden or _role(child) in {"listbox", "option"}
    )


def is_inline(node: LiftNode) -> bool:
    """Whether the node flows inside a line of text: by its tag, unless a browser snapshot says how
    the page lays it out (``data-lift-display``)."""

    hint = node.attrs.get(HINT_DISPLAY, "").lower()
    if hint in {"inline", "block"}:
        return hint == "inline"
    return node.tag in INLINE_TAGS


def join_text(node: LiftNode, children: Iterable[tuple[bool, str | None]]) -> str:
    """A subtree's text in page order, from the node's own text runs and each child's text.

    ``children`` gives, for each child in order, whether it flows inline (see ``is_inline``) and its
    text, or ``None`` for a child left out. Like the browser's ``innerText``, the text of an inline
    child runs into the text around it, and the text of any other child is set on a line of its own.
    The result keeps its line breaks and edge spaces, so the level above joins it correctly; collapse
    it at the top with ``" ".join(text.split())``, or split it on newlines for its lines.
    """

    runs = node.text_runs or ((node.text,) if node.text else ())
    pieces = [runs[0] if runs else ""]
    for index, (inline, text) in enumerate(children, 1):
        if text is not None:
            pieces.append(text if inline else f"\n{text}\n")
        pieces.append(runs[index] if index < len(runs) else "")
    return "".join(pieces)


def subtree_text(
    node: LiftNode, by_id: Mapping[str, LiftNode], *, skip: Callable[[LiftNode], bool] | None = None
) -> str:
    """The text of a subtree in page order, with whitespace collapsed. ``skip`` leaves out a child
    and everything in it."""

    def raw(current: LiftNode) -> str:
        children: list[tuple[bool, str | None]] = []
        for child_id in current.child_ids:
            child = by_id[child_id]
            children.append((is_inline(child), None if skip is not None and skip(child) else raw(child)))
        return join_text(current, children)

    return " ".join(raw(node).split())


def _submits(node: LiftNode, kind: str, control: str, form: LiftNode | None) -> bool:
    if form is None or kind != "action":
        return False
    if node.tag == "button":
        # A missing, empty, or unknown type is a submit button, as the HTML spec has it.
        return (node.attrs.get("type") or "submit").lower() not in {"button", "reset"}
    return node.tag == "input" and control in {"submit", "image"}


def _form_name(page: _Page, form: LiftNode, members: Sequence[HtmlControl]) -> str:
    for attr in ("aria-label", "name", "id"):
        if form.attrs.get(attr):
            return form.attrs[attr]
    text = page.labelled_by(form)
    if text:
        return text
    text = page.form_heading(form)
    if text:
        return text
    for control in members:
        if control.submits and control.name:
            return control.name
    action = (form.attrs.get("action") or "").split("?")[0].strip("/").rsplit("/", 1)[-1]
    return action or "form"


def extract_controls(nodes: Sequence[LiftNode]) -> ControlExtraction:
    """Find every visible control and form in the lifted nodes."""

    page = _Page(nodes)
    controls: list[HtmlControl] = []
    roles: list[tuple[str, str, str, float]] = []
    excluded: set[str] = set(page.hidden)
    inside_control: set[str] = set()
    messages: list[HtmlMessage] = []
    inside_message: set[str] = set()
    for node in nodes:
        if node.id in page.hidden:
            continue
        if node.parent_id is not None and node.parent_id in inside_message:
            inside_message.add(node.id)
            excluded.add(node.id)  # Its text is part of the message.
        else:
            kind = _message_kind(node)
            if kind is not None:
                inside_message.add(node.id)
                excluded.add(node.id)
                text = page.text(node, skip_controls=True)
                if text:
                    messages.append(HtmlMessage(node.id, kind, text))
        if node.parent_id is not None and node.parent_id in inside_control:
            # The content of a control that owns it is part of that control: its name, its options,
            # or its value. A control nested in it is not counted again.
            inside_control.add(node.id)
            excluded.add(node.id)
            continue
        if node.id in page.popup_owner:
            # A combobox's list: its options belong to the combobox, not to the page or a field.
            inside_control.add(node.id)
            excluded.add(node.id)
            continue
        if node.tag in {"label", "legend"}:
            excluded.add(node.id)  # Its text names a control or a group; it is not page content.
        classified = _classify(node)
        if classified is None:
            continue
        kind, control, confidence = classified
        if control not in GUESSED_CONTROLS and _owns_content(node, kind, control):
            inside_control.add(node.id)
        excluded.add(node.id)
        form = _form_of(page, node)
        expanded: bool | None = None
        if node.tag == "select":
            options = _options(page, node)
        elif control in ARIA_CHOICE:
            options, expanded = _choice_state(page, node, control)
        else:
            options = ()
        if expanded is None:
            expanded = _aria_bool(node.attrs.get("aria-expanded"))  # A disclosure, a menu button, a tree item.
        value, checked = (None, None) if kind == "action" else _field_value(page, node, control, options)
        name = _clip(_accessible_name(page, node, kind, control))
        controls.append(
            HtmlControl(
                node_id=node.id,
                kind=kind,
                control=control,
                name=name,
                field_name=node.attrs.get("name") or None,
                value=value,
                checked=checked,
                required="required" in node.attrs or node.attrs.get("aria-required", "").lower() == "true",
                disabled=_disabled(page, node),
                form_id=form.id if form is not None else None,
                submits=_submits(node, kind, control, form),
                href=node.attrs.get("href") if node.tag == "a" else None,
                action_id=node.attrs.get("data-action-id") or None,
                options=options,
                group=_clip(_group_name(page, node) or "") or None if kind == "field" else None,
                readonly="readonly" in node.attrs or node.attrs.get("aria-readonly", "").lower() == "true",
                secret=is_sensitive_control(node.tag, node.attrs),
                ref=node.attrs.get(HINT_REF) or None,
                tag=node.tag,
                expanded=expanded,
                minimum=node.attrs.get("min") or None,
                maximum=node.attrs.get("max") or None,
                popup=(node.attrs.get("aria-haspopup") or "").lower() or None,
                date=_is_date_field(node, control, name),
                accept=node.attrs.get("accept") or None if node.tag == "input" else None,
                multiple="multiple" in node.attrs,
                download=node.attrs.get("download") if node.tag in {"a", "area"} and "download" in node.attrs else None,
                pattern=node.attrs.get("pattern") or None,
                min_length=_length(node.attrs.get("minlength")),
                max_length=_length(node.attrs.get("maxlength")),
                step=node.attrs.get("step") or None,
                pressed=_aria_bool(node.attrs.get("aria-pressed")),
                selected=_aria_bool(node.attrs.get("aria-selected")),
                sort=_column_sort(page, node) if kind == "action" else None,
            )
        )
        roles.append((node.id, kind, name, confidence))
    wrappers = _guessed_wrappers(page, controls)
    if wrappers:
        controls = [control for control in controls if control.node_id not in wrappers]
        roles = [role for role in roles if role[0] not in wrappers]
        excluded.difference_update(wrappers)
    controls = _date_ranges(page, controls)

    members_of: dict[str, list[HtmlControl]] = {}
    for control in controls:
        if control.form_id is not None:
            members_of.setdefault(control.form_id, []).append(control)
    forms: list[HtmlForm] = []
    for node in nodes:
        if node.tag != "form" or node.id in page.hidden:
            continue
        members = members_of.get(node.id, [])
        forms.append(
            HtmlForm(
                node_id=node.id,
                name=_form_name(page, node, members),
                action=node.attrs.get("action"),
                method=(node.attrs.get("method") or "get").upper(),
                control_ids=tuple(control.node_id for control in members),
                submit_ids=tuple(control.node_id for control in members if control.submits),
                ref=node.attrs.get(HINT_REF) or None,
            )
        )
    return ControlExtraction(
        controls=tuple(controls),
        forms=tuple(forms),
        messages=tuple(messages),
        roles=tuple(roles),
        excluded_ids=frozenset(excluded),
    )


def hidden_ids(nodes: Sequence[LiftNode]) -> frozenset[str]:
    """The nodes the user cannot see: under ``hidden``, ``aria-hidden="true"``, or ``data-lift-hidden``."""

    return frozenset(_Page(nodes).hidden)


def visible_text(nodes: Sequence[LiftNode]) -> str:
    """All text the user can see, in document order, with whitespace collapsed.

    Secret values are not in it: the lift never keeps them.
    """

    if not nodes:
        return ""
    page = _Page(nodes)
    root = next(node for node in nodes if node.parent_id is None)
    return subtree_text(root, page.by_id, skip=lambda child: child.id in page.hidden)


def _message_kind(node: LiftNode) -> str | None:
    role = node.attrs.get("role", "").strip().lower().split(" ")[0]
    if role in MESSAGE_ROLES:
        return role
    if node.attrs.get("aria-live", "").strip().lower() in MESSAGE_LIVE:
        return "live"
    return None


def _slug(text: str) -> str:
    return SLUG_RE.sub("_", text.lower()).strip("_")


BOOLEAN_CONTROLS = frozenset({"checkbox", "switch"})
NUMBER_CONTROLS = NUMBER_INPUT_TYPES | {"slider", "spinbutton"}


@dataclass(frozen=True)
class FormField:
    """One argument of a form tool, and the controls it fills.

    ``kind`` is ``text``, ``number``, ``boolean``, ``choice`` (a radio group), ``select``, or
    ``multi`` (a multiple select). A ``choice`` field maps each allowed value to one radio control.
    """

    key: str
    kind: str
    controls: tuple[HtmlControl, ...]
    choices: tuple[tuple[str, HtmlControl], ...] = ()

    @property
    def control(self) -> HtmlControl:
        return self.controls[0]

    @property
    def required(self) -> bool:
        return any(control.required for control in self.controls)


def _field_kind(control: HtmlControl) -> str:
    if control.control == "file" and control.tag == "input":
        return "file"
    if control.date:
        return "date"
    if control.control in ARIA_CHOICE and control.tag != "select":
        return "choose"
    if control.control in BOOLEAN_CONTROLS:
        return "boolean"
    if control.control in NUMBER_CONTROLS:
        return "number"
    if control.control == "select":
        return "select"
    if control.control == "select-multiple":
        return "multi"
    return "text"


def form_fields(form: HtmlForm, controls: Mapping[str, HtmlControl]) -> tuple[FormField, ...]:
    """The arguments of a form tool, in document order. Disabled fields are left out.

    Radios that share a ``name`` (or, for ARIA radios, a group label) become one ``choice`` field.
    Other fields are keyed by ``name``, then by the slug of the accessible name, then by node id;
    a key already in use gets a ``_2``, ``_3``, ... suffix.
    """

    fields: list[FormField] = []
    by_key: dict[str, int] = {}
    radio_keys: dict[str, str] = {}
    for control_id in form.control_ids:
        control = controls[control_id]
        if control.kind != "field" or control.disabled:
            continue
        if control.control == "radio":
            group_id = control.field_name or _slug(control.group or "") or None
            if group_id is not None:
                option = (control.value if control.field_name else None) or control.name or control.node_id
                if group_id in radio_keys:
                    index = by_key[radio_keys[group_id]]
                    field = fields[index]
                    fields[index] = FormField(
                        field.key, "choice", (*field.controls, control), (*field.choices, (option, control))
                    )
                    continue
                key = _unique_key(group_id, by_key)
                radio_keys[group_id] = key
                by_key[key] = len(fields)
                fields.append(FormField(key, "choice", (control,), ((option, control),)))
                continue
        key = _unique_key(control.field_name or _slug(control.name) or control.node_id, by_key)
        by_key[key] = len(fields)
        kind = "boolean" if control.control == "radio" else _field_kind(control)
        fields.append(FormField(key, kind, (control,)))
    return tuple(fields)


def _column_sort(page: _Page, node: LiftNode) -> str | None:
    """The aria-sort of the column header around a control (a sort button in a <th>), if any."""

    for ancestor in page.ancestors(node):
        if ancestor.tag in {"th", "td"} or _role(ancestor) in {"columnheader", "rowheader"}:
            value = ancestor.attrs.get("aria-sort", "").strip().lower()
            return value if value in {"ascending", "descending", "none", "other"} else None
    return None


def _aria_bool(value: str | None) -> bool | None:
    """An ARIA state attribute as a bool: "true" or "false"; None when missing, "mixed", or "undefined"."""

    lowered = (value or "").strip().lower()
    return True if lowered == "true" else False if lowered == "false" else None


def _length(value: str | None) -> int | None:
    """A minlength or maxlength attribute as a number; None when missing or not a whole number."""

    try:
        return int(value) if value is not None and int(value) >= 0 else None
    except ValueError:
        return None


def _number(value: str | None) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _unique_key(base: str, used: Mapping[str, int]) -> str:
    key = base
    suffix = 2
    while key in used:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _field_schema(field: FormField) -> dict[str, Any]:
    control = field.control
    if field.kind == "choice":
        prop: dict[str, Any] = {
            "description": control.group or field.key,
            "type": "string",
            "enum": [value for value, _ in field.choices],
            "x-node-ids": {value: member.node_id for value, member in field.choices},
        }
        checked = [value for value, member in field.choices if member.checked]
        if checked:
            prop["default"] = checked[0]
        return prop
    prop = {"description": control.name or control.field_name or field.key, "x-node-id": control.node_id}
    if field.kind == "boolean":
        prop["type"] = "boolean"
        if control.checked is not None:
            prop["default"] = control.checked
    elif field.kind == "number":
        prop["type"] = "number"
        for key, limit in (("minimum", control.minimum), ("maximum", control.maximum)):
            number = _number(limit)
            if number is not None:
                prop[key] = number
    elif field.kind == "file":
        prop["type"] = "array"
        prop["items"] = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "encoding": {"type": "string", "enum": ["text", "base64"]},
                "mime_type": {"type": "string"},
                "path": {"type": "string"},
            },
        }
        if not control.multiple:
            prop["maxItems"] = 1
        accepts = f"; accepts {control.accept}" if control.accept else ""
        prop["description"] = (
            f"{prop['description']} (files to upload: name and content, or a path in the upload folder{accepts})"
        )
    elif field.kind == "date" and control.date_range == "range":
        day = {"type": "string", "format": "date"}
        prop["type"] = "object"
        prop["properties"] = {"start": day, "end": day}
        prop["required"] = ["start", "end"]
        prop["description"] = (
            f"{prop['description']} (a date range: start and end as YYYY-MM-DD; picked from its calendar)"
        )
    elif field.kind == "date":
        native = control.tag == "input" and control.control in DATE_INPUT_TYPES
        kind = control.control if native else "date"
        prop["type"] = "string"
        if kind in STRING_FORMATS:
            prop["format"] = STRING_FORMATS[kind]
        detail = DATE_INPUT_TYPES[kind]
        if control.minimum:
            detail += f", from {control.minimum}"
        if control.maximum:
            detail += f", to {control.maximum}"
        if not native:
            detail += "; picked from its calendar"
        prop["description"] = f"{prop['description']} ({detail})"
    elif field.kind == "choose":
        prop["type"] = "string"
        labels = [option.label for option in control.options if not option.disabled]
        if control.control == "listbox" and labels:
            prop["enum"] = labels
        else:
            prop["description"] = f"{prop['description']}: pick an option from its list"
            if labels:
                prop["examples"] = labels[:10]
    elif field.kind in {"select", "multi"}:
        values = [option.value for option in control.options if not option.disabled]
        if field.kind == "multi":
            prop["type"] = "array"
            prop["items"] = {"type": "string", "enum": values}
        else:
            prop["type"] = "string"
            prop["enum"] = values
    else:
        prop["type"] = "string"
        if control.control in STRING_FORMATS:
            prop["format"] = STRING_FORMATS[control.control]
        # What the browser will check before it sends the form, in the schema's own words.
        if control.min_length is not None:
            prop["minLength"] = control.min_length
        if control.max_length is not None:
            prop["maxLength"] = control.max_length
        # A host that checks a call against this schema runs the pattern, so one that could stall
        # the planner is left out here too; the browser still checks it.
        if control.pattern and pattern_is_bounded(control.pattern):
            prop["pattern"] = f"^(?:{control.pattern})$"
    custom_date = field.kind == "date" and control.control not in DATE_INPUT_TYPES
    if control.value not in {None, "", "[redacted]"} and field.kind not in {"boolean", "file"} and not custom_date:
        prop["default"] = control.value  # A calendar's own display format is not the tool's format.
    if control.secret:
        prop["writeOnly"] = True
    if control.readonly:
        prop["readOnly"] = True
    return prop


def form_tool_name(form: HtmlForm) -> str:
    return _slug(form.name) or form.node_id


def form_tool_schema(form: HtmlForm, controls: Mapping[str, HtmlControl], *, name: str | None = None) -> dict[str, Any]:
    """Describe one form as a callable tool: a JSON Schema for its inputs plus its submit controls.

    Each property carries ``x-node-id`` (or ``x-node-ids`` for a radio group) so the caller can map
    an argument back to the element to fill in. Disabled fields are left out. A secret field is
    ``writeOnly``: an agent may fill it, but the lift never shows its value.
    """

    fields = form_fields(form, controls)
    properties = {field.key: _field_schema(field) for field in fields}
    keys = {field.control.node_id: field.key for field in fields}
    for field in fields:
        other = keys.get(field.control.range_with or "")
        if other and field.control.date_range == "start":
            properties[field.key]["description"] += f"; the start of a date range that ends at {other!r}"
        elif other and field.control.date_range == "end":
            properties[field.key]["description"] += f"; the end of the date range from {other!r}, not before it"
    return {
        "name": name or form_tool_name(form),
        "description": f"Fill in and submit the form {form.name!r}.",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": [field.key for field in fields if field.required],
        },
        "x-form-node-id": form.node_id,
        "x-submit-node-ids": list(form.submit_ids),
        "x-method": form.method,
        "x-action": form.action,
    }


def form_tool_names(forms: Sequence[HtmlForm]) -> tuple[str, ...]:
    """A unique tool name for each form: two forms with the same name get ``_2``, ``_3``, ..."""

    used: dict[str, int] = {}
    names: list[str] = []
    for form in forms:
        name = _unique_key(form_tool_name(form), used)
        used[name] = 0
        names.append(name)
    return tuple(names)


def control_aria_role(control: HtmlControl) -> str | None:
    """The ARIA role a browser gives the control, for a role-and-name locator. None if it has none."""

    if control.control in ACTION_ROLES | FIELD_ROLES:
        return control.control
    return {
        "link": "link",
        "button": "button",
        "submit": "button",
        "reset": "button",
        "image": "button",
        "checkbox": "checkbox",
        "radio": "radio",
        "search": "searchbox",
        "number": "spinbutton",
        "range": "slider",
        "select": "combobox",
        "select-multiple": "listbox",
        "textarea": "textbox",
        "text": "textbox",
        "email": "textbox",
        "tel": "textbox",
        "url": "textbox",
        "password": "textbox",
    }.get(control.control)


__all__ = [
    "AGENT_HINT_ATTR_PREFIX",
    "AGENT_VIEWSPEC_DATA_ATTRS",
    "ControlExtraction",
    "FormField",
    "HtmlControl",
    "HtmlForm",
    "HtmlMessage",
    "HtmlOption",
    "control_aria_role",
    "extract_controls",
    "form_fields",
    "form_tool_name",
    "form_tool_names",
    "form_tool_schema",
    "hidden_ids",
    "is_sensitive_control",
    "label_secret_ids",
    "names_a_secret",
    "visible_text",
]
