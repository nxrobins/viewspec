"""Plan browser actions from an agent lift: one control action, or a whole form tool call.

The planner is the checked half of "act on what you read". It takes what an agent asked for, checks
it against the lifted page (does the control exist, is it enabled, can it take this value, is the
value one the form allows), and returns concrete steps for a browser driver to run. It never touches
a browser itself, makes no network calls, and gives the same steps for the same input.

Each step names its control by lift ``node_id`` and by the snapshot ``ref`` (``data-lift-ref``) when
the snapshot set one, so the driver can find the exact element again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from viewspec.raw_html_agent import (
    FormField,
    HtmlControl,
    HtmlForm,
    form_fields,
    form_tool_names,
)

if TYPE_CHECKING:
    from viewspec.raw_html import HtmlLiftResult

ACTION_OPS = ("click", "fill", "check", "uncheck", "select", "submit")
SCROLL_DIRECTIONS = ("end", "down", "up", "top")
ARIA_CHOICE = frozenset({"combobox", "listbox"})
NOT_FILLABLE = frozenset(
    {
        "button",
        "checkbox",
        "color",
        "file",
        "image",
        "listbox",
        "radio",
        "range",
        "reset",
        "select",
        "select-multiple",
        "slider",
        "submit",
        "switch",
    }
)
ARIA_CHECKABLE = frozenset({"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"})
REDACTED = "[redacted]"


class AgentActionError(ValueError):
    """An action or tool call that the page cannot take. ``details`` lists each problem."""

    def __init__(self, code: str, message: str, details: Sequence[Mapping[str, str]] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = tuple(dict(item) for item in details)

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": [dict(item) for item in self.details]}


@dataclass(frozen=True)
class ActionStep:
    """One thing for a browser driver to do.

    ``op`` is ``click``, ``fill``, ``check``, ``uncheck``, ``select`` (``values`` holds option values),
    ``choose`` (pick the option named ``value`` from an ARIA combobox or listbox: open its list if
    needed, find the option, click it), ``scroll`` (scroll the page, or the scrollable area that holds
    the control ``node_id``, in the direction ``value``), or ``submit`` (submit the form ``node_id``
    without a submit button). ``secret`` marks a value that
    must not be logged; ``to_json()`` redacts it unless asked not to.
    """

    op: str
    node_id: str
    ref: str | None
    name: str
    value: str | None = None
    values: tuple[str, ...] = field(default_factory=tuple)
    secret: bool = False

    def to_json(self, *, redact: bool = True) -> dict[str, Any]:
        hide = redact and self.secret
        payload: dict[str, Any] = {"op": self.op, "node_id": self.node_id, "ref": self.ref, "name": self.name}
        if self.value is not None:
            payload["value"] = REDACTED if hide else self.value
        if self.values:
            payload["values"] = [REDACTED] if hide else list(self.values)
        if self.secret:
            payload["secret"] = True
        return payload


def plan_control_action(control: HtmlControl, op: str, value: Any = None) -> tuple[ActionStep, ...]:
    """Plan one action on one control. The result is empty when the control is already in that state.

    ``check`` and ``uncheck`` on an ARIA checkbox or switch become a ``click`` when the state must
    change. ``select`` takes one option value or label (a list for a multiple select).
    """

    if op not in ACTION_OPS or op == "submit":
        raise AgentActionError("AGENT_ACTION_UNSUPPORTED", f"Unknown action {op!r} for a control.")
    if control.disabled:
        raise AgentActionError("AGENT_ACTION_DISABLED", f"{_label(control)} is disabled.")
    if op == "click":
        return (_step("click", control),)
    if op == "fill":
        return (_fill(control, value),)
    if op in {"check", "uncheck"}:
        return _set_checked(control, op == "check")
    return (_select(control, value),)


def plan_form_call(
    lift: HtmlLiftResult, tool_name: str, arguments: Mapping[str, Any], *, submit: bool = True
) -> tuple[ActionStep, ...]:
    """Plan a call of a form tool from ``lift.form_tool_schemas()``.

    Steps fill the given arguments in document order and then submit: they click the first enabled
    submit control, or submit the form when it has none. ``submit=False`` only fills. Arguments the
    call leaves out keep the value the page shows. A required field that the call leaves out is an
    error only when the page shows it empty (or unchecked, for a checkbox).
    """

    names = form_tool_names(lift.forms)
    if tool_name not in names:
        raise AgentActionError(
            "AGENT_FORM_UNKNOWN", f"No form tool {tool_name!r} on this page. Tools: {', '.join(names) or 'none'}."
        )
    form = lift.forms[names.index(tool_name)]
    controls = {control.node_id: control for control in lift.controls}
    fields = form_fields(form, controls)
    by_key = {item.key: item for item in fields}
    problems: list[dict[str, str]] = []
    for key in arguments:
        if key not in by_key:
            problems.append({"argument": key, "problem": "unknown", "message": f"{tool_name} has no {key!r}."})
    steps: list[ActionStep] = []
    for item in fields:
        if item.key not in arguments:
            if item.required and _empty(item):
                problems.append({"argument": item.key, "problem": "missing", "message": f"{item.key!r} is required."})
            continue
        try:
            steps.extend(_field_steps(item, arguments[item.key]))
        except AgentActionError as error:
            problems.append({"argument": item.key, "problem": "invalid", "message": error.message})
    if problems:
        raise AgentActionError(
            "AGENT_FORM_ARGUMENTS_INVALID", f"The call to {tool_name} has {len(problems)} problem(s).", problems
        )
    if submit:
        steps.append(_submit_step(form, controls))
    return tuple(steps)


def plan_scroll(direction: Any = "end", control: HtmlControl | None = None) -> ActionStep:
    """Plan a scroll of the page, or of the scrollable area that holds ``control``.

    ``direction`` is ``end`` (to the bottom, where an infinite list loads more), ``down`` or ``up``
    (about one screen), or ``top``. A scroll step has no ``ref`` and an empty ``node_id`` for the page.
    """

    if direction not in SCROLL_DIRECTIONS:
        raise AgentActionError(
            "AGENT_ACTION_VALUE_INVALID", f"Scroll {direction!r}: use one of {', '.join(SCROLL_DIRECTIONS)}."
        )
    if control is None:
        return ActionStep(op="scroll", node_id="", ref=None, name="page", value=direction)
    return ActionStep(
        op="scroll",
        node_id=control.node_id,
        ref=control.ref,
        name=control.name or control.field_name or control.node_id,
        value=direction,
    )


def _field_steps(item: FormField, value: Any) -> tuple[ActionStep, ...]:
    control = item.control
    if item.kind == "choice":
        allowed = [option for option, _ in item.choices]
        chosen = dict(item.choices).get(value) if isinstance(value, str) else None
        if chosen is None:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{value!r} is not one of {allowed}.")
        return plan_control_action(chosen, "check")
    if item.kind == "boolean":
        if not isinstance(value, bool):
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"Expected true or false, not {value!r}.")
        if control.control == "radio":
            if not value:
                raise AgentActionError("AGENT_ACTION_VALUE_INVALID", "A radio cannot be turned off; choose another.")
            return plan_control_action(control, "check")
        return plan_control_action(control, "check" if value else "uncheck")
    if item.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"Expected a number, not {value!r}.")
        try:
            number = float(value)
        except ValueError:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"Expected a number, not {value!r}.") from None
        text = str(int(number)) if number.is_integer() else str(number)
        return plan_control_action(control, "fill", text)
    if item.kind in {"select", "multi", "choose"}:
        return plan_control_action(control, "select", value)
    return plan_control_action(control, "fill", value)


def _empty(item: FormField) -> bool:
    if item.kind == "choice":
        return not any(member.checked for _, member in item.choices)
    if item.kind == "boolean":
        return not item.control.checked
    if item.control.secret:
        return True  # The lift never shows a secret value, so it cannot know the field is filled.
    return not item.control.value


def _fill(control: HtmlControl, value: Any) -> ActionStep:
    if control.kind != "field" or control.control in NOT_FILLABLE:
        raise AgentActionError("AGENT_ACTION_UNSUPPORTED", f"{_label(control)} ({control.control}) cannot be filled.")
    if control.readonly:
        raise AgentActionError("AGENT_ACTION_READONLY", f"{_label(control)} is read-only.")
    if not isinstance(value, str):
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"Expected text, not {value!r}.")
    return _step("fill", control, value=value)


def _set_checked(control: HtmlControl, checked: bool) -> tuple[ActionStep, ...]:
    if control.kind != "field" or control.control not in ARIA_CHECKABLE:
        verb = "checked" if checked else "unchecked"
        raise AgentActionError("AGENT_ACTION_UNSUPPORTED", f"{_label(control)} ({control.control}) cannot be {verb}.")
    if control.control in {"radio", "menuitemradio"} and not checked:
        raise AgentActionError("AGENT_ACTION_UNSUPPORTED", f"{_label(control)} is a radio; choose another option.")
    if control.readonly:
        raise AgentActionError("AGENT_ACTION_READONLY", f"{_label(control)} is read-only.")
    if control.tag == "input":  # A native checkbox or radio, whatever its ARIA role.
        return (_step("check" if checked else "uncheck", control),)
    # An ARIA widget changes state when clicked; click only when the state must change.
    if bool(control.checked) == checked:
        return ()
    return (_step("click", control),)


def _select(control: HtmlControl, value: Any) -> ActionStep:
    if control.control in ARIA_CHOICE and control.tag != "select":
        return _choose(control, value)
    if control.control not in {"select", "select-multiple"}:
        raise AgentActionError("AGENT_ACTION_UNSUPPORTED", f"{_label(control)} ({control.control}) has no options.")
    wanted = list(value) if isinstance(value, (list, tuple)) else [value]
    if control.control == "select" and len(wanted) != 1:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{_label(control)} takes exactly one option.")
    chosen: list[str] = []
    for item in wanted:
        match = next(
            (option for option in control.options if item in {option.value, option.label} and not option.disabled),
            None,
        )
        if match is None or not isinstance(item, str):
            allowed = [option.value for option in control.options if not option.disabled]
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{item!r} is not one of {allowed}.")
        chosen.append(match.value)
    return _step("select", control, values=tuple(chosen))


def _choose(control: HtmlControl, value: Any) -> ActionStep:
    """Pick one option of an ARIA combobox or listbox by its name.

    A listbox shows all its options, so the name must be one of them. A combobox may show none until
    it is opened or typed into, so its options are checked in the browser, when they show.
    """

    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if not isinstance(value, str) or not value.strip():
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{_label(control)} takes the name of one option.")
    if control.readonly:
        raise AgentActionError("AGENT_ACTION_READONLY", f"{_label(control)} is read-only.")
    wanted = value.strip()
    if control.control == "listbox":
        allowed = [option.label for option in control.options if not option.disabled]
        if wanted.casefold() not in {label.casefold() for label in allowed}:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{wanted!r} is not one of {allowed}.")
    return _step("choose", control, value=wanted)


def _submit_step(form: HtmlForm, controls: Mapping[str, HtmlControl]) -> ActionStep:
    for node_id in form.submit_ids:
        control = controls[node_id]
        if not control.disabled:
            return _step("click", control)
    if form.submit_ids:
        raise AgentActionError("AGENT_ACTION_DISABLED", f"Every submit control of {form.name!r} is disabled.")
    return ActionStep(op="submit", node_id=form.node_id, ref=form.ref, name=form.name)


def _step(op: str, control: HtmlControl, *, value: str | None = None, values: tuple[str, ...] = ()) -> ActionStep:
    return ActionStep(
        op=op,
        node_id=control.node_id,
        ref=control.ref,
        name=control.name or control.field_name or control.node_id,
        value=value,
        values=values,
        secret=control.secret,
    )


def _label(control: HtmlControl) -> str:
    return repr(control.name or control.field_name or control.node_id)


__all__ = [
    "ACTION_OPS",
    "ActionStep",
    "AgentActionError",
    "plan_control_action",
    "plan_form_call",
    "plan_scroll",
]
