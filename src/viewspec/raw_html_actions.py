"""Plan browser actions from an agent lift: one control action, or a whole form tool call.

The planner is the checked half of "act on what you read". It takes what an agent asked for, checks
it against the lifted page (does the control exist, is it enabled, can it take this value, is the
value one the form allows), and returns concrete steps for a browser driver to run. It never touches
a browser itself, makes no network calls, and gives the same steps for the same input.

Each step names its control by lift ``node_id`` and by the snapshot ``ref`` (``data-lift-ref``) when
the snapshot set one, so the driver can find the exact element again.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import mimetypes
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from viewspec.raw_html_agent import (
    DATE_INPUT_TYPES,
    FormField,
    HtmlControl,
    HtmlForm,
    form_fields,
    form_tool_names,
)

if TYPE_CHECKING:
    from viewspec.raw_html import HtmlLiftResult

ACTION_OPS = ("click", "fill", "check", "uncheck", "select", "upload", "submit")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # One file.
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024  # All files of one step.
SCROLL_DIRECTIONS = ("end", "down", "up", "top")
ARIA_CHOICE = frozenset({"combobox", "listbox"})
MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_WEEKDAY = r"(?:[a-z]+,?\s+)?"
_ORDINAL = r"(?:st|nd|rd|th)?"
ISO_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
DAY_MONTH_RE = re.compile(_WEEKDAY + r"(\d{1,2})" + _ORDINAL + r"\s+([a-z]+)\.?,?\s+(\d{4})")
MONTH_DAY_RE = re.compile(_WEEKDAY + r"([a-z]+)\.?\s+(\d{1,2})" + _ORDINAL + r",?\s+(\d{4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])?\.?m?\.?")
MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")
MONTH_NAME_RE = re.compile(r"([a-z]+)\.?,?\s+(\d{4})")
WEEK_RE = re.compile(r"(\d{4})-w(\d{1,2})")
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
class UploadFile:
    """A file to upload: its name, its bytes, and its media type. Its bytes are never logged."""

    name: str
    data: bytes = field(repr=False)
    mime_type: str = "application/octet-stream"

    @classmethod
    def from_json(cls, payload: Any) -> UploadFile:
        """Read ``{"name", "content", "encoding": "text" | "base64", "mime_type"?}``.

        ``content`` is text (sent as UTF-8) unless ``encoding`` is ``base64``. A ``path`` is not read
        here: whoever runs the browser decides which folder files may come from, and reads them.
        """

        if isinstance(payload, UploadFile):
            return payload
        if not isinstance(payload, Mapping):
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"A file is an object with a name, not {payload!r}.")
        if "path" in payload:
            raise AgentActionError(
                "AGENT_ACTION_VALUE_INVALID", "A file path must be read by the browser driver, from its upload folder."
            )
        name = payload.get("name")
        if not isinstance(name, str):
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", "A file needs a name, as text.")
        content = payload.get("content")
        encoding = payload.get("encoding", "text")
        if not isinstance(content, str) or encoding not in {"text", "base64"}:
            raise AgentActionError(
                "AGENT_ACTION_VALUE_INVALID", "A file needs content as text, or as base64 with encoding 'base64'."
            )
        if encoding == "base64":
            try:
                data = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError):
                raise AgentActionError(
                    "AGENT_ACTION_VALUE_INVALID", f"The content of {name!r} is not base64."
                ) from None
        else:
            data = content.encode("utf-8")
        mime_type = payload.get("mime_type")
        return cls(name=name, data=data, mime_type=mime_type) if mime_type else cls.named(name, data)

    @classmethod
    def named(cls, name: Any, data: bytes) -> UploadFile:
        """A file whose media type is guessed from its name."""

        guess = mimetypes.guess_type(name)[0] if isinstance(name, str) else None
        return cls(name=name, data=data, mime_type=guess or "application/octet-stream")

    @property
    def size(self) -> int:
        return len(self.data)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "mime_type": self.mime_type, "size": self.size}


def accepts(accept: str | None, file: UploadFile) -> bool:
    """Whether a file input's ``accept`` (``.pdf,image/*,text/csv``) allows this file. No ``accept``
    allows any file."""

    tokens = [token.strip().lower() for token in (accept or "").split(",") if token.strip()]
    if not tokens:
        return True
    name = file.name.lower()
    mime = file.mime_type.lower()
    for token in tokens:
        if token.startswith(".") and name.endswith(token):
            return True
        if token.endswith("/*") and mime.startswith(token[:-1]):
            return True
        if token == mime:
            return True
    return False


@dataclass(frozen=True)
class ActionStep:
    """One thing for a browser driver to do.

    ``op`` is ``click``, ``fill``, ``check``, ``uncheck``, ``select`` (``values`` holds option values),
    ``choose`` (pick the option named ``value`` from an ARIA combobox or listbox: open its list if
    needed, find the option, click it), ``choose_date`` (pick the date ``value``, as YYYY-MM-DD, from
    the calendar of a custom date field), ``choose_date_range`` (pick the range ``value``, as
    YYYY-MM-DD/YYYY-MM-DD, in the calendar of one field that holds a range, or of a pair of fields
    whose end field is ``end_node_id``), ``scroll`` (scroll the page, or the scrollable area that holds
    the control ``node_id``, in the direction ``value``), ``upload`` (give ``files`` to a file input, or
    to the file chooser a button opens), or ``submit`` (submit the form ``node_id`` without a submit
    button). ``secret`` marks a value that must not be logged; ``to_json()`` redacts it unless asked
    not to. ``to_json()`` shows each file's name, type, and size, never its bytes.
    """

    op: str
    node_id: str
    ref: str | None
    name: str
    value: str | None = None
    values: tuple[str, ...] = field(default_factory=tuple)
    secret: bool = False
    files: tuple[UploadFile, ...] = field(default_factory=tuple)
    end_node_id: str | None = None  # For a range on a pair of fields: the field that holds the end.
    end_ref: str | None = None
    end_name: str | None = None

    def to_json(self, *, redact: bool = True) -> dict[str, Any]:
        hide = redact and self.secret
        payload: dict[str, Any] = {"op": self.op, "node_id": self.node_id, "ref": self.ref, "name": self.name}
        if self.value is not None:
            payload["value"] = REDACTED if hide else self.value
        if self.values:
            payload["values"] = [REDACTED] if hide else list(self.values)
        if self.op == "upload":
            payload["files"] = [item.to_json() for item in self.files]
        if self.end_node_id is not None:
            payload["end"] = {"node_id": self.end_node_id, "ref": self.end_ref, "name": self.end_name}
        if self.secret:
            payload["secret"] = True
        return payload


def plan_control_action(
    control: HtmlControl, op: str, value: Any = None, *, lift: HtmlLiftResult | None = None
) -> tuple[ActionStep, ...]:
    """Plan one action on one control. The result is empty when the control is already in that state.

    ``check`` and ``uncheck`` on an ARIA checkbox or switch become a ``click`` when the state must
    change. ``select`` takes one option value or label (a list for a multiple select). On a field that
    holds a date range, ``select`` takes a range (see ``parse_date_range``). On either field of a
    start and end pair, ``select`` with a range sets both; that needs ``lift``, to find the other field.
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
    if op == "upload":
        return (plan_upload(control, value),)
    if control.date_range == "range":
        return plan_date_range(control, None, value)
    if control.date_range in {"start", "end"} and control.range_with and _is_range(value):
        if lift is None:
            raise AgentActionError(
                "AGENT_ACTION_VALUE_INVALID", f"{_label(control)} holds one date; plan a range with the page's lift."
            )
        other = lift.control(control.range_with)
        start, end = (control, other) if control.date_range == "start" else (other, control)
        return plan_date_range(start, end, value)
    return (_select(control, value),)


def plan_date_range(start: HtmlControl, end: HtmlControl | None, value: Any) -> tuple[ActionStep, ...]:
    """Plan a date range on one field that holds a range (``end`` is None), or on a pair of fields.

    One field, or a pair of custom date fields, gives one ``choose_date_range`` step whose value is
    ``START/END`` (YYYY-MM-DD/YYYY-MM-DD): a driver picks both days, in one calendar when it stays
    open after the first. A pair with a native date field gives a step for each field. Each day is
    checked against its field's ``min`` and ``max``, and the end must not be before the start.
    """

    first, last = parse_date_range(value)
    for control in (start, end):
        if control is not None and control.disabled:
            raise AgentActionError("AGENT_ACTION_DISABLED", f"{_label(control)} is disabled.")
    if end is None:
        span = f"{date_value(start, first.isoformat())}/{date_value(start, last.isoformat())}"
        return (_step("choose_date_range", start, value=span),)
    begins, ends = date_value(start, first.isoformat()), date_value(end, last.isoformat())
    if _native_date(start) or _native_date(end):
        return (_date(start, begins), _date(end, ends))
    step = _step("choose_date_range", start, value=f"{begins}/{ends}")
    return (replace(step, end_node_id=end.node_id, end_ref=end.ref, end_name=_name(end)),)


def plan_upload(control: HtmlControl, files: Any) -> ActionStep:
    """Plan an upload of ``files`` (``UploadFile`` objects, or inline file objects) to a control.

    A file input takes the files directly; its ``accept`` and ``multiple`` are checked here, and an
    empty list clears it. Any other control (an "Add photos" button whose file input is hidden) must
    open a file chooser when clicked; the driver checks that chooser's ``accept`` and ``multiple``.
    """

    if control.disabled:
        raise AgentActionError("AGENT_ACTION_DISABLED", f"{_label(control)} is disabled.")
    is_input = control.control == "file" and control.tag == "input"
    if not is_input and control.kind != "action":
        raise AgentActionError(
            "AGENT_ACTION_UNSUPPORTED",
            f"{_label(control)} ({control.control}) takes no files; use a file input or its button.",
        )
    items = list(files) if isinstance(files, (list, tuple)) else [files]
    uploads = tuple(UploadFile.from_json(item) for item in items)
    if not uploads and not is_input:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{_label(control)} needs at least one file.")
    if is_input and not control.multiple and len(uploads) > 1:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{_label(control)} takes one file, not {len(uploads)}.")
    total = 0
    for upload in uploads:
        if not isinstance(upload.name, str) or not upload.name.strip() or any(c in upload.name for c in "/\\\0"):
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{upload.name!r} is not a file name.")
        if upload.size > MAX_UPLOAD_BYTES:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{upload.name!r} is larger than 10 MB.")
        total += upload.size
        if is_input and not accepts(control.accept, upload):
            raise AgentActionError(
                "AGENT_ACTION_VALUE_INVALID",
                f"{upload.name!r} ({upload.mime_type}) is not a type {_label(control)} accepts ({control.accept}).",
            )
    if total > MAX_UPLOAD_TOTAL_BYTES:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", "The files are larger than 25 MB together.")
    return ActionStep(
        op="upload",
        node_id=control.node_id,
        ref=control.ref,
        name=control.name or control.field_name or control.node_id,
        files=uploads,
    )


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
    by_node = {item.control.node_id: item for item in fields}
    planned: set[str] = set()  # Keys whose step another field's step already holds.
    for item in fields:
        if item.key in planned:
            continue
        end = by_node.get(item.control.range_with or "") if item.control.date_range == "start" else None
        if end is not None and item.key in arguments and end.key in arguments:
            steps.extend(_range_steps(item, end, arguments, problems))
            planned.add(end.key)
            continue
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
    if item.kind in {"select", "multi", "choose", "date"}:
        return plan_control_action(control, "select", value)
    if item.kind == "file":
        return (plan_upload(control, value),)
    return plan_control_action(control, "fill", value)


def _range_steps(
    start: FormField, end: FormField, arguments: Mapping[str, Any], problems: list[dict[str, str]]
) -> tuple[ActionStep, ...]:
    """Steps for both fields of a date range pair, given together. Problems go to ``problems``."""

    days: dict[str, str] = {}
    for item in (start, end):
        try:
            days[item.key] = date_value(item.control, arguments[item.key])
        except AgentActionError as error:
            problems.append({"argument": item.key, "problem": "invalid", "message": error.message})
    if len(days) < 2:
        return ()
    if days[end.key] < days[start.key]:
        problems.append(
            {
                "argument": end.key,
                "problem": "invalid",
                "message": f"{end.key!r} ({days[end.key]}) is before {start.key!r} ({days[start.key]}).",
            }
        )
        return ()
    try:
        return plan_date_range(start.control, end.control, {"start": days[start.key], "end": days[end.key]})
    except AgentActionError as error:
        problems.append({"argument": start.key, "problem": "invalid", "message": error.message})
        return ()


def _empty(item: FormField) -> bool:
    if item.kind in {"date", "file"}:
        return not item.control.value
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
    if _native_date(control):
        value = date_value(control, value)  # A native date input takes only its own format.
    return _step("fill", control, value=value)


def _native_date(control: HtmlControl) -> bool:
    return control.tag == "input" and control.control in DATE_INPUT_TYPES


def _date(control: HtmlControl, value: Any) -> ActionStep:
    """A date for a date field: fill a native one; pick a custom one's date from its calendar."""

    if _native_date(control):
        return _fill(control, value)
    return _step("choose_date", control, value=date_value(control, value))


def parse_date(value: Any) -> datetime.date:
    """Read a date written as YYYY-MM-DD (or with / or .), "20 November 2026", "Nov 20, 2026", or
    "Friday, November 20, 2026". A numeric day-month-year date is read only when it cannot be read
    two ways: "20/11/2026" is 20 November, but "11/12/2026" is refused as ambiguous.
    """

    if not isinstance(value, str) or not value.strip():
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"Expected a date, not {value!r}.")
    text = " ".join(value.split()).lower()
    year = month = day = None
    if match := ISO_DATE_RE.fullmatch(text):
        year, month, day = (int(part) for part in match.groups())
    elif match := NUMERIC_DATE_RE.fullmatch(text):
        first, second, year = (int(part) for part in match.groups())
        if first == second or (first > 12) != (second > 12):
            day, month = (first, second) if first > 12 or first == second else (second, first)
        else:
            raise AgentActionError(
                "AGENT_ACTION_VALUE_INVALID", f"{value!r} can be read as two dates; write it as YYYY-MM-DD."
            )
    elif (match := DAY_MONTH_RE.fullmatch(text)) and match.group(2) in MONTHS:
        day, month, year = int(match.group(1)), MONTHS[match.group(2)], int(match.group(3))
    elif (match := MONTH_DAY_RE.fullmatch(text)) and match.group(1) in MONTHS:
        month, day, year = MONTHS[match.group(1)], int(match.group(2)), int(match.group(3))
    if year is None or month is None or day is None:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{value!r} is not a date; write it as YYYY-MM-DD.")
    try:
        return datetime.date(year, month, day)
    except ValueError:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{value!r} is not a real date.") from None


# Words and marks between the two dates of a range written as text.
RANGE_SEPARATOR_RE = re.compile(r"\s+(?:to|until|till|through|thru|-)\s+|\s*[\u2013\u2014]\s*", re.IGNORECASE)
ISO_RANGE_RE = re.compile(r"(\d{4}-\d{1,2}-\d{1,2})\s*/\s*(\d{4}-\d{1,2}-\d{1,2})")


def _range_parts(value: Any) -> list[Any] | None:
    """The two (or more) dates of a range, not yet read; None when ``value`` is not written as a range."""

    if isinstance(value, Mapping):
        return [value.get("start"), value.get("end")] if set(value) == {"start", "end"} else None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if match := ISO_RANGE_RE.fullmatch(text):
            return [match.group(1), match.group(2)]
        parts = RANGE_SEPARATOR_RE.split(text)
        return parts if len(parts) > 1 else None
    return None


def _is_range(value: Any) -> bool:
    return _range_parts(value) is not None


def parse_date_range(value: Any) -> tuple[datetime.date, datetime.date]:
    """Read a date range: ``{"start", "end"}``, a list of two dates, or text with two dates
    ("2026-11-20/2026-11-23", "Nov 20, 2026 to Nov 23, 2026", "20/11/2026 - 23/11/2026").

    Each date is read by ``parse_date``. The end may be the start, but not before it.
    """

    parts = _range_parts(value)
    if parts is None or len(parts) != 2:
        raise AgentActionError(
            "AGENT_ACTION_VALUE_INVALID",
            f"Expected a date range with a start and an end, such as 2026-11-20/2026-11-23, not {value!r}.",
        )
    start, end = parse_date(parts[0]), parse_date(parts[1])
    if end < start:
        raise AgentActionError(
            "AGENT_ACTION_VALUE_INVALID", f"The range ends ({end.isoformat()}) before it starts ({start.isoformat()})."
        )
    return start, end


def date_value(control: HtmlControl, value: Any) -> str:
    """The value a date field takes, in its own format, checked against its ``min`` and ``max``.

    A custom date field takes YYYY-MM-DD. Native fields take YYYY-MM-DD (date), YYYY-MM (month),
    HH:MM (time), YYYY-MM-DDTHH:MM (datetime-local), or YYYY-Www (week).
    """

    kind = control.control if _native_date(control) else "date"
    if not isinstance(value, str) or not value.strip():
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{_label(control)} takes a {kind}, not {value!r}.")
    text = " ".join(value.split())
    if kind == "date":
        result = parse_date(text).isoformat()
    elif kind == "time":
        result = _time(text)
    elif kind == "datetime-local":
        date_part, _, time_part = text.replace("T", " ").replace("t", " ").partition(" ")
        if not time_part:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{value!r} has no time; write YYYY-MM-DDTHH:MM.")
        result = f"{parse_date(date_part).isoformat()}T{_time(time_part)}"
    elif kind == "month":
        result = _month(text)
    else:
        match = WEEK_RE.fullmatch(text.lower())
        if not match or not 1 <= int(match.group(2)) <= 53:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{value!r} is not a week; write YYYY-Www.")
        result = f"{int(match.group(1)):04d}-W{int(match.group(2)):02d}"
    if control.minimum and len(control.minimum) == len(result) and result < control.minimum:
        raise AgentActionError(
            "AGENT_ACTION_VALUE_INVALID", f"{result} is before the earliest allowed, {control.minimum}."
        )
    if control.maximum and len(control.maximum) == len(result) and result > control.maximum:
        raise AgentActionError(
            "AGENT_ACTION_VALUE_INVALID", f"{result} is after the latest allowed, {control.maximum}."
        )
    return result


def _time(text: str) -> str:
    match = TIME_RE.fullmatch(text.strip().lower())
    if not match:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{text!r} is not a time; write HH:MM.")
    hour, minute = int(match.group(1)), int(match.group(2))
    if match.group(4):
        if not 1 <= hour <= 12:
            raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{text!r} is not a time.")
        hour = hour % 12 + (12 if match.group(4) == "p" else 0)
    if hour > 23 or minute > 59 or (match.group(3) and int(match.group(3)) > 59):
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{text!r} is not a time.")
    return f"{hour:02d}:{minute:02d}" + (f":{int(match.group(3)):02d}" if match.group(3) else "")


def _month(text: str) -> str:
    lowered = text.lower()
    if match := MONTH_RE.fullmatch(lowered):
        year, month = int(match.group(1)), int(match.group(2))
    elif (match := MONTH_NAME_RE.fullmatch(lowered)) and match.group(1) in MONTHS:
        year, month = int(match.group(2)), MONTHS[match.group(1)]
    else:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{text!r} is not a month; write YYYY-MM.")
    if not 1 <= month <= 12:
        raise AgentActionError("AGENT_ACTION_VALUE_INVALID", f"{text!r} is not a month.")
    return f"{year:04d}-{month:02d}"


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
    if control.date:
        return _date(control, value)
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
        name=_name(control),
        value=value,
        values=values,
        secret=control.secret,
    )


def _name(control: HtmlControl) -> str:
    return control.name or control.field_name or control.node_id


def _label(control: HtmlControl) -> str:
    return repr(_name(control))


__all__ = [
    "ACTION_OPS",
    "ActionStep",
    "AgentActionError",
    "plan_control_action",
    "plan_form_call",
    "plan_scroll",
    "plan_upload",
    "UploadFile",
    "accepts",
    "date_value",
    "parse_date",
    "parse_date_range",
    "plan_date_range",
]
