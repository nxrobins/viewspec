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
    value: str
    label: str
    selected: bool
    disabled: bool

    def to_json(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "selected": self.selected, "disabled": self.disabled}


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

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_id": self.node_id,
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

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "action": self.action,
            "method": self.method,
            "control_ids": list(self.control_ids),
            "submit_ids": list(self.submit_ids),
        }


@dataclass(frozen=True)
class ControlExtraction:
    controls: tuple[HtmlControl, ...]
    forms: tuple[HtmlForm, ...]
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
    if node.tag not in {"input", "select", "textarea"}:
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
                    )
                )

    visit(node, False)
    return tuple(options)


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
    return page.text(node) or attrs.get("aria-valuenow"), None


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
    for node in nodes:
        if node.id in page.hidden:
            continue
        if node.parent_id is not None and node.parent_id in inside_control:
            # A control's content is part of that control: its name, its options, or its value.
            # Like the browser's tab order, a control nested in another control is not counted twice.
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
        options = _options(page, node) if node.tag == "select" else ()
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
            )
        )
    return ControlExtraction(
        controls=tuple(controls), forms=tuple(forms), roles=tuple(roles), excluded_ids=frozenset(excluded)
    )


def _slug(text: str) -> str:
    return SLUG_RE.sub("_", text.lower()).strip("_")


def _number(value: str | None) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def form_tool_schema(form: HtmlForm, controls: Mapping[str, HtmlControl]) -> dict[str, Any]:
    """Describe one form as a callable tool: a JSON Schema for its inputs plus its submit controls.

    Each property carries ``x-node-id`` (or ``x-node-ids`` for a radio group) so the caller can map
    an argument back to the element to fill in. Disabled fields are left out.
    """

    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for control_id in form.control_ids:
        control = controls[control_id]
        if control.kind != "field" or control.disabled:
            continue
        key = control.field_name or _slug(control.name) or control.node_id
        if control.control == "radio" and control.field_name:
            group = properties.setdefault(
                key, {"description": control.group or key, "type": "string", "enum": [], "x-node-ids": {}}
            )
            option = control.value or control.name
            group["enum"].append(option)
            group["x-node-ids"][option] = control.node_id
            if control.checked:
                group["default"] = option
            if control.required and key not in required:
                required.append(key)
            continue
        base = key
        suffix = 2
        while key in properties:
            key = f"{base}_{suffix}"
            suffix += 1
        prop: dict[str, Any] = {"description": control.name, "x-node-id": control.node_id}
        if control.control in {"checkbox", "switch"}:
            prop["type"] = "boolean"
            if control.checked is not None:
                prop["default"] = control.checked
        elif control.control in NUMBER_INPUT_TYPES or control.control in {"slider", "spinbutton"}:
            prop["type"] = "number"
        elif control.control in {"select", "select-multiple"}:
            values = [option.value for option in control.options if not option.disabled]
            if control.control == "select-multiple":
                prop["type"] = "array"
                prop["items"] = {"type": "string", "enum": values}
            else:
                prop["type"] = "string"
                prop["enum"] = values
        else:
            prop["type"] = "string"
            if control.control in STRING_FORMATS:
                prop["format"] = STRING_FORMATS[control.control]
        if control.value not in {None, "", "[redacted]"} and control.control not in {"checkbox", "switch"}:
            prop["default"] = control.value
        if control.control == "password":
            prop["writeOnly"] = True
        if control.required:
            required.append(key)
        properties[key] = prop
    return {
        "name": _slug(form.name) or form.node_id,
        "description": f"Fill in and submit the form {form.name!r}.",
        "input_schema": {"type": "object", "properties": properties, "required": required},
        "x-form-node-id": form.node_id,
        "x-submit-node-ids": list(form.submit_ids),
        "x-method": form.method,
        "x-action": form.action,
    }


__all__ = [
    "AGENT_HINT_ATTR_PREFIX",
    "AGENT_VIEWSPEC_DATA_ATTRS",
    "ControlExtraction",
    "HtmlControl",
    "HtmlForm",
    "HtmlOption",
    "extract_controls",
    "form_tool_schema",
    "is_sensitive_control",
]
