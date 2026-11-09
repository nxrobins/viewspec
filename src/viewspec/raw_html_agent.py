"""Controls, accessible names, and forms for the "agent" profile of ``lift_html``.

The agent profile keeps the parts of a page that a browser agent reads and operates: links,
buttons, form fields, ARIA widgets, and the forms that group them. This module turns the lifted
nodes into ``HtmlControl`` and ``HtmlForm`` records and into a tool schema for each form.

It works only on the sanitized, lifted nodes. It makes no network calls and runs no script.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from viewspec.raw_html import LiftNode

# Attributes a browser-side snapshot may add before the lift. ``data-lift-hidden`` marks the top of
# a subtree the user cannot see; ``data-lift-clickable`` marks an element that looks clickable (for
# example, it has a pointer cursor) but has no semantic role.
AGENT_HINT_ATTR_PREFIX = "data-lift-"
HINT_HIDDEN = "data-lift-hidden"
HINT_CLICKABLE = "data-lift-clickable"
# A browser-side snapshot may also give each element a ``data-lift-ref``: a handle the browser can
# turn back into that exact element, so an agent can act on what it read.
HINT_REF = "data-lift-ref"
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
# Regions where a page tells the user what happened: an error, a confirmation, a log line.
MESSAGE_ROLES = frozenset({"alert", "alertdialog", "log", "status"})
MESSAGE_LIVE = frozenset({"assertive", "polite"})
DEFAULT_INPUT_ACTION_NAMES = {"submit": "Submit", "reset": "Reset", "image": "Submit"}
CONTROL_CONTENT_TAGS = frozenset({"input", "option", "optgroup", "select", "textarea"})
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
SENSITIVE_NAME_RE = re.compile(
    r"pass(?:word|wd|code)?|secret|token|otp|cvv|cvc|ssn|card.?num|cc.?num|security.?code", re.IGNORECASE
)
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
    r"(?<![a-z])(?:dates?|dob|birth\w*|check.?in|check.?out|arrival|departure|deadline|due)(?![a-z])", re.IGNORECASE
)
DATE_PLACEHOLDER_RE = re.compile(r"(?<![a-z])(?:dd|mm|yy|yyyy)\s*[/.\-]\s*(?:dd|mm|yy|yyyy)(?![a-z])", re.IGNORECASE)


def is_sensitive_control(tag: str, attrs: Mapping[str, str]) -> bool:
    """True when a field may hold a secret, so the lift must not keep its value."""

    if tag not in {"input", "textarea"}:
        return False
    if attrs.get("type", "").lower() == "password":
        return True
    tokens = set(attrs.get("autocomplete", "").lower().split())
    if tokens & SENSITIVE_AUTOCOMPLETE:
        return True
    return any(SENSITIVE_NAME_RE.search(attrs.get(name, "")) for name in ("name", "id"))


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


class _Page:
    """Indexes over the lifted nodes. Nodes arrive in document (pre-)order."""

    def __init__(self, nodes: Sequence[LiftNode]) -> None:
        self.nodes = nodes
        self.by_id = {node.id: node for node in nodes}
        self.by_html_id: dict[str, LiftNode] = {}
        self.labels_for: dict[str, list[LiftNode]] = {}
        for node in nodes:
            html_id = node.attrs.get("id")
            if html_id and html_id not in self.by_html_id:
                self.by_html_id[html_id] = node
            if node.tag == "label" and node.attrs.get("for"):
                self.labels_for.setdefault(node.attrs["for"], []).append(node)
        # The list a combobox controls (aria-controls, or aria-owns) belongs to that combobox: it is
        # not a field of its own, and its options are the combobox's options.
        self.popup_owner: dict[str, str] = {}
        self.popup_of: dict[str, str] = {}
        for node in nodes:
            if _role(node) != "combobox":
                continue
            for attr in ("aria-controls", "aria-owns"):
                for html_id in node.attrs.get(attr, "").split():
                    target = self.by_html_id.get(html_id)
                    if target is not None and target.id != node.id and target.id not in self.popup_owner:
                        self.popup_owner[target.id] = node.id
                        self.popup_of.setdefault(node.id, target.id)
        self.hidden = self._hidden_ids()
        self._text: dict[tuple[str, bool], str] = {}

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
        """Visible text of a subtree. ``skip_controls`` leaves out what fields contain (option text)."""

        key = (node.id, skip_controls)
        if key not in self._text:
            parts = [node.text]
            for child_id in node.child_ids:
                child = self.by_id[child_id]
                if child.id in self.hidden or (skip_controls and child.tag in CONTROL_CONTENT_TAGS):
                    continue
                parts.append(self.text(child, skip_controls=skip_controls))
            self._text[key] = " ".join(part for part in parts if part).strip()
        return self._text[key]

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
    if node.tag != "input" or control not in {"text", "search", "textbox"}:
        return False
    hints = (node.attrs.get("name", ""), node.attrs.get("id", ""), name)
    return any(DATE_HINT_RE.search(hint) for hint in hints) or bool(
        DATE_PLACEHOLDER_RE.search(node.attrs.get("placeholder", ""))
    )


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


def _tab_stop(attrs: Mapping[str, str]) -> bool:
    try:
        return int(attrs.get("tabindex", "-1")) >= 0
    except ValueError:
        return False


def _accessible_name(page: _Page, node: LiftNode, kind: str, control: str) -> str:
    attrs = node.attrs
    labelled_by = attrs.get("aria-labelledby", "").split()
    if labelled_by:
        text = " ".join(page.text(page.by_html_id[ref]) for ref in labelled_by if ref in page.by_html_id).strip()
        if text:
            return text
    if attrs.get("aria-label"):
        return attrs["aria-label"]
    if node.tag in {"input", "select", "textarea"} and control not in INPUT_ACTION_TYPES:
        html_id = attrs.get("id")
        for label in page.labels_for.get(html_id, []) if html_id else []:
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
    return attrs.get("title") or attrs.get("placeholder") or ""


def _form_of(page: _Page, node: LiftNode) -> LiftNode | None:
    owner = node.attrs.get("form")
    if owner and owner in page.by_html_id and page.by_html_id[owner].tag == "form":
        return page.by_html_id[owner]
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
            labelled_by = ancestor.attrs.get("aria-labelledby", "").split()
            text = " ".join(page.text(page.by_html_id[ref]) for ref in labelled_by if ref in page.by_html_id).strip()
            return text or ancestor.attrs.get("aria-label") or None
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
                label = child.attrs.get("aria-label") or page.text(child)
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
    if node.tag == "input":
        return attrs.get("value", ""), None
    if control == "listbox":
        return ", ".join(option.label for option in options if option.selected), None
    if control == "combobox":
        return _text_outside_lists(page, node), None
    return page.text(node, skip_controls=True) or attrs.get("aria-valuenow"), None


def _text_outside_lists(page: _Page, node: LiftNode) -> str:
    """What a combobox shows as its value: its text, without the list of options it may wrap."""

    parts = [node.text]
    for child_id in node.child_ids:
        child = page.by_id[child_id]
        if child.id in page.hidden or _role(child) in {"listbox", "option"}:
            continue
        parts.append(_text_outside_lists(page, child))
    return " ".join(part for part in parts if part).strip()


def _submits(node: LiftNode, kind: str, control: str, form: LiftNode | None) -> bool:
    if form is None or kind != "action":
        return False
    if node.tag == "button":
        return node.attrs.get("type", "submit").lower() == "submit"
    return node.tag == "input" and control in {"submit", "image"}


def _form_name(page: _Page, form: LiftNode, members: Sequence[HtmlControl]) -> str:
    for attr in ("aria-label", "name", "id"):
        if form.attrs.get(attr):
            return form.attrs[attr]
    labelled_by = form.attrs.get("aria-labelledby", "").split()
    if labelled_by:
        text = " ".join(page.text(page.by_html_id[ref]) for ref in labelled_by if ref in page.by_html_id).strip()
        if text:
            return text
    for node in page.nodes:
        if node.tag in {"legend", "h1", "h2", "h3", "h4", "h5", "h6"} and any(
            ancestor.id == form.id for ancestor in page.ancestors(node)
        ):
            text = page.text(node)
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
            # A control's content is part of that control: its name, its options, or its value.
            # Like the browser's tab order, a control nested in another control is not counted twice.
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
        value, checked = (None, None) if kind == "action" else _field_value(page, node, control, options)
        name = _accessible_name(page, node, kind, control)
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
                group=_group_name(page, node) if kind == "field" else None,
                readonly="readonly" in node.attrs or node.attrs.get("aria-readonly", "").lower() == "true",
                secret=is_sensitive_control(node.tag, node.attrs),
                ref=node.attrs.get(HINT_REF) or None,
                tag=node.tag,
                expanded=expanded,
                minimum=node.attrs.get("min") or None,
                maximum=node.attrs.get("max") or None,
                popup=(node.attrs.get("aria-haspopup") or "").lower() or None,
                date=_is_date_field(node, control, name),
            )
        )
        roles.append((node.id, kind, name, confidence))

    forms: list[HtmlForm] = []
    for node in nodes:
        if node.tag != "form" or node.id in page.hidden:
            continue
        members = [control for control in controls if control.form_id == node.id]
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

    page = _Page(nodes)
    return " ".join(" ".join(node.text for node in nodes if node.text and node.id not in page.hidden).split())


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
    custom_date = field.kind == "date" and control.control not in DATE_INPUT_TYPES
    if control.value not in {None, "", "[redacted]"} and field.kind != "boolean" and not custom_date:
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
    return {
        "name": name or form_tool_name(form),
        "description": f"Fill in and submit the form {form.name!r}.",
        "input_schema": {
            "type": "object",
            "properties": {field.key: _field_schema(field) for field in fields},
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
    "visible_text",
]
