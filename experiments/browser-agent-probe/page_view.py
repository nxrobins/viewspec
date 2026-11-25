"""A compact, agent-facing text view of an agent-profile lift, and slices of it.

Each control gets a numbered handle (``[7]``). An agent reads the view and acts by handle; the session
maps the handle back to the control, its ref, and its locator. A control that the page shows but a
person could not use now ends with ``blocked``. A long view is read in slices (``view_page``): by
offset and limit, or only the lines that contain some text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from viewspec.raw_html import HtmlLiftResult, LiftNode
from viewspec.raw_html_agent import HtmlControl, form_tool_names, hidden_ids, is_inline
from viewspec.raw_html_agent import subtree_text as agent_subtree_text

VIEW_GROUP_MIN = 3
TEXT_PREVIEW = 70
OPTION_PREVIEW = 8


def control_line(
    handle: int, control: HtmlControl, *, blocked: bool = False, redact: Callable[[Any], Any] = lambda value: value
) -> str:
    """One control as a line of the view. ``blocked``: the page shows it, but a person could not use
    it now (see ``view_lines``)."""

    label = redact(control.name or control.field_name or "(unnamed)")
    line = f"[{handle}] {control.control} {label[:TEXT_PREVIEW]!r}"
    if control.kind == "field":
        if control.checked is not None:
            line += " [x]" if control.checked else " [ ]"
        elif control.value and not control.secret:
            line += f" = {redact(control.value)[:TEXT_PREVIEW]!r}"
        if control.options:
            shown = "|".join(
                (f"[{option.kind}] " if option.kind in {"create", "unknown", "navigate"} else "")
                + redact(option.name or option.label)
                for option in control.options[:OPTION_PREVIEW]
            )
            more = f"|+{len(control.options) - OPTION_PREVIEW}" if len(control.options) > OPTION_PREVIEW else ""
            line += f" options: {shown}{more}"
        if control.choice is not None:
            if control.choice.query and not control.secret:
                line += f" query: {redact(control.choice.query)[:TEXT_PREVIEW]!r}"
            if control.choice.operation == "add":
                line += " (select adds one existing item; preserves selected items)"
            if control.multiple:
                line += " multiple (select an exact array; [] clears)"
            if not control.options:
                line += f" choices: {control.choice.state}"
        if control.required:
            line += " required"
        if control.secret:
            line += f" secret {control.value_state}"
        if control.readonly:
            line += " read-only"
        if control.date and control.control == "text":
            line += " date"  # A custom date field: select a date, and its calendar is used.
        if control.date_range == "range":
            line += " range"  # One field that holds a whole date range.
        elif control.date_range:
            line += f" (range {control.date_range})"  # Select a range on either field to set both.
        if control.control == "file":
            line += " multiple" if control.multiple else ""
            line += f" accepts {control.accept}" if control.accept else ""
    if control.href:
        line += f" -> {control.href}"
    if control.download is not None:
        line += f" download {control.download!r}" if control.download else " download"
    if control.submits:
        line += " (submits)"
    if control.action_id:
        line += f" #{control.action_id}"
    # ARIA state a click toggles: shown so the agent can tell a pressed toolbar button, the
    # selected tab, or an open disclosure from the others, and see the change in the diff.
    if control.pressed is not None:
        line += " pressed" if control.pressed else " not pressed"
    if control.selected:
        line += " selected"
    if control.expanded is not None and control.kind == "action":
        line += " expanded" if control.expanded else " collapsed"
    if control.sort and control.sort != "none":
        line += f" sorted {control.sort}"
    if control.disabled:
        line += " disabled"
    if blocked:
        line += " blocked"
    return redact(line)


def subtree_text(node: LiftNode, by_id: dict[str, LiftNode]) -> str:
    return agent_subtree_text(node, by_id)


# Elements that hold a section of the page. A heading names the content of its own section, not
# the content of a section next to it or around it.
SECTIONING = frozenset(
    {"article", "aside", "details", "fieldset", "footer", "form", "header", "li", "main", "nav", "section", "table"}
)
FIND_BLOCK = 30  # The most lines a matching heading brings with it from its section.


@dataclass(frozen=True)
class ViewLine:
    """One line of the view: blocked, message, group, form, row, control, heading, value, or text.

    ``node_id`` is the lifted node the line shows. ``heading_id`` is the node id of the heading whose
    section holds the line (for a heading line: the heading of the section around it). ``form_id`` is
    the form that a ``form`` line opens or that a ``control`` belongs to.
    """

    text: str
    kind: str
    node_id: str
    heading_id: str | None = None
    handle: int | None = None
    form_id: str | None = None


class _Sections:
    """Which visible heading names the section that holds each node."""

    def __init__(self, lift: HtmlLiftResult) -> None:
        self.by_id = {node.id: node for node in lift.nodes}
        self.order = {node.id: index for index, node in enumerate(lift.nodes)}
        self.headings: dict[str | None, list[tuple[int, str]]] = {}
        for role in lift.roles:
            if role.role == "heading":
                self.headings.setdefault(self.scope(role.node_id), []).append((self.order[role.node_id], role.node_id))

    def scope(self, node_id: str) -> str | None:
        """The nearest sectioning ancestor, or None for the page itself."""

        parent = self.by_id[node_id].parent_id
        while parent is not None and self.by_id[parent].tag not in SECTIONING:
            parent = self.by_id[parent].parent_id
        return parent

    def heading_for(self, node_id: str) -> str | None:
        index = self.order[node_id]
        scope = self.scope(node_id)
        while True:
            before = [heading for at, heading in self.headings.get(scope, []) if at < index and heading != node_id]
            if before:
                return before[-1]
            if scope is None:
                return None
            scope = self.scope(scope)


def view_lines(
    lift: HtmlLiftResult,
    *,
    blocked: Mapping[str, str] | None = None,
    redact: Callable[[Any], Any] = lambda value: value,
) -> tuple[list[ViewLine], dict[int, HtmlControl]]:
    """Build the view as lines, and return the control behind each handle.

    The view lists messages (alerts, status, live regions), forms, headings, controls, values, table
    rows, ordinary text, and repeated groups in document order. Ordinary text keeps inline wording
    and is not preview-truncated, so paging and find can reach the whole paragraph. Handles count
    controls from 1 in document order, so the same page gives the same handles.

    ``blocked`` maps the node id of a control to why a person could not use it now: a dialog covers
    it, or the page has switched it off. The lift cannot know this (it depends on what the browser
    draws over what), so the browser session supplies it. Such a control keeps its line and its
    handle, since the agent may need to read it, and the line ends with ``blocked``. The view then
    opens with one line per reason, so the agent knows what to deal with first.
    """

    blocked = blocked or {}
    group_of: dict[str, tuple[str, ...]] = {}
    for group in lift.group_candidates:
        if len(group) >= VIEW_GROUP_MIN:
            for member in group:
                group_of[member] = group
    controls = {control.node_id: control for control in lift.controls}
    represented = {
        option.node_id
        for control in lift.controls
        for option in control.options
        if option.kind not in {"create", "unknown"}
    }
    label_refs = {control.choice.label_ref for control in lift.controls if control.choice and control.choice.label_ref}
    represented.update(node.id for node in lift.nodes if node.attrs.get("data-lift-ref") in label_refs)
    forms = {form.node_id: form for form in lift.forms}
    messages = {message.node_id: message for message in lift.messages}
    tool_names = {form.node_id: name for form, name in zip(lift.forms, form_tool_names(lift.forms), strict=True)}
    roles = {role.node_id: role for role in lift.roles}
    by_id = {node.id: node for node in lift.nodes}
    sections = _Sections(lift)
    hidden = hidden_ids(lift.nodes)
    lines: list[ViewLine] = []
    handles: dict[int, HtmlControl] = {}
    in_row: set[str] = set()
    owned_text: set[str] = set()
    inline_text: set[str] = set()

    def text_for(node: LiftNode) -> str:
        """Join inline wording, leaving blocks and independently rendered content on their lines."""

        def skip(child: LiftNode) -> bool:
            role = roles.get(child.id)
            control = controls.get(child.id)
            if (
                child.id in hidden
                or not is_inline(child)
                or child.tag in {"label", "legend"}
                or child.id in messages
                or (role is not None and role.role == "heading")
                or (control is not None and control.control != "link")
            ):
                return True
            # Numeric values still have their own semantic line (for example a page counter),
            # even when their wording also belongs to the surrounding paragraph.
            if role is None or role.role != "value":
                inline_text.add(child.id)
            return False

        return agent_subtree_text(node, by_id, skip=skip)

    def add(text: str, kind: str, node_id: str, **extra: Any) -> None:
        lines.append(ViewLine(redact(text), kind, node_id, sections.heading_for(node_id), **extra))

    for node in lift.nodes:
        if node.id in hidden:
            continue  # A closed dialog's rows and lists are not on the page the user sees.
        if node.id in represented:
            owned_text.add(node.id)
            continue
        if node.parent_id in owned_text or node.tag in {"label", "legend"}:
            owned_text.add(node.id)
        if node.parent_id in in_row and node.id not in controls:
            in_row.add(node.id)
            continue
        group = group_of.get(node.id)
        if (
            group
            and group[0] == node.id
            and node.tag not in {"option", "optgroup"}
            and node.id not in owned_text
            and node.id not in inline_text
        ):
            add(f"group {len(group)}x <{node.tag}>", "group", node.id)
        if node.id in messages:
            owned_text.add(node.id)
            message = messages[node.id]
            add(f"message {message.kind} {redact(message.text)[:TEXT_PREVIEW]!r}", "message", node.id)
        if node.id in forms:
            form = forms[node.id]
            text = f"form {form.name!r} {form.method} {form.action or ''}".rstrip() + f" tool={tool_names[node.id]}"
            add(text, "form", node.id, form_id=node.id)
        if node.tag == "tr":
            # One line per table row keeps each label next to its value. Controls in a row still
            # get their own line below it.
            cells = [redact(subtree_text(by_id[child_id], by_id)) for child_id in node.child_ids]
            add("row " + " | ".join(cell[:TEXT_PREVIEW] for cell in cells), "row", node.id)
            in_row.add(node.id)
            continue
        if node.id in controls:
            control = controls[node.id]
            if control.choice is None:
                owned_text.add(node.id)
            handle = len(handles) + 1
            handles[handle] = control
            line = control_line(handle, control, blocked=node.id in blocked, redact=redact)
            add(line, "control", node.id, handle=handle, form_id=control.form_id)
            continue
        if node.id in owned_text or node.id in inline_text:
            continue
        role = roles.get(node.id)
        if role is not None and role.role == "heading":
            owned_text.add(node.id)
            add(f"{node.tag.upper()} {redact(role.text)[:TEXT_PREVIEW]}", "heading", node.id)
        elif role is not None and role.role == "value":
            owned_text.add(node.id)
            add(f"value {role.text}", "value", node.id)
        elif (node.text or node.tag == "p") and (text := text_for(node)):
            add(f"text {text}", "text", node.id)
    return [*_blocked_notices(lines, blocked), *lines], handles


def _blocked_notices(lines: list[ViewLine], blocked: Mapping[str, str]) -> list[ViewLine]:
    """One line per reason that controls in the view are blocked, most controls first."""

    reasons = Counter(blocked[line.node_id] for line in lines if line.kind == "control" and line.node_id in blocked)
    return [
        ViewLine(f"blocked {count} control{'' if count == 1 else 's'}: {reason}", "blocked", "")
        for reason, count in reasons.most_common()
    ]


def page_view(lift: HtmlLiftResult) -> tuple[str, dict[int, HtmlControl]]:
    """The whole view as text, and the control behind each handle."""

    lines, handles = view_lines(lift)
    return "\n".join(line.text for line in lines), handles


@dataclass(frozen=True)
class ViewPage:
    """A slice of the view.

    ``total`` counts the lines that could be shown: all lines, or the result of ``find``.
    ``next_offset`` is where the next slice starts, or None at the end. ``context`` holds the lines
    above ``offset`` that the first shown line sits under: its headings, outermost first, and the
    form of the first shown control.
    """

    lines: tuple[str, ...]
    offset: int
    total: int
    next_offset: int | None
    context: tuple[str, ...] = ()
    find: str | None = None
    matches: int | None = None
    # Internal identity, never inferred from redacted text or exposed as a public node id.
    form_ids: frozenset[str] = frozenset()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "offset": self.offset,
            "returned": len(self.lines),
            "total": self.total,
            "next_offset": self.next_offset,
        }
        if self.context:
            payload["context"] = list(self.context)
        if self.find is not None:
            payload["find"] = self.find
            payload["matches"] = self.matches
        return payload

    def describe(self, observation_id: int) -> str:
        """One sentence on what this slice holds and how to get more; empty for a whole view."""

        if self.find is not None and not self.matches:
            return f"No line contains {self.find!r}."
        plural = "match" if self.matches == 1 else "matches"
        what = f"result lines for {self.find!r} ({self.matches} {plural})" if self.find is not None else "lines"
        if self.offset == 0 and self.next_offset is None:
            return f"{self.total} {what}." if self.find is not None else ""
        if not self.lines:
            return f"No {what} after {self.offset}; there are {self.total}."
        shown = f"Showing {what} {self.offset + 1}-{self.offset + len(self.lines)} of {self.total}."
        if self.next_offset is None:
            return shown
        find = f", find={self.find!r}" if self.find is not None else ""
        return f"{shown} More: observe(observation_id={observation_id}, offset={self.next_offset}{find})."


def view_page(lines: list[ViewLine], *, offset: int = 0, limit: int = 200, find: str | None = None) -> ViewPage:
    """Slice the view: ``limit`` lines from ``offset``, of the whole view or of the result of ``find``.

    ``find`` matches lines that contain the text, ignoring case and runs of whitespace. A matching
    heading comes with the lines of its section (up to 30); any other matching line comes after the
    heading it sits under. Lines that are not next to each other in the view are split by ``…``.
    Handles are those of the whole view, so any of them works with ``act``.
    """

    offset = max(0, offset)
    limit = max(1, limit)
    if find is None:
        shown = lines[offset : offset + limit]
        end = offset + len(shown)
        context = _breadcrumb(lines, offset, shown)
        return ViewPage(
            tuple(line.text for line in shown),
            offset,
            len(lines),
            end if end < len(lines) else None,
            tuple(line.text for line in context),
            form_ids=frozenset(line.form_id for line in [*shown, *context] if line.form_id is not None),
        )
    result, matches = _find(lines, find)
    shown = result[offset : offset + limit]
    end = offset + len(shown)
    return ViewPage(
        tuple(line.text for line in shown),
        offset,
        len(result),
        end if end < len(result) else None,
        find=find,
        matches=matches,
        form_ids=frozenset(line.form_id for line in shown if line.form_id is not None),
    )


def _parent_headings(lines: list[ViewLine]) -> dict[str, str | None]:
    return {line.node_id: line.heading_id for line in lines if line.kind == "heading"}


def _within(line: ViewLine, heading_id: str, parents: dict[str, str | None]) -> bool:
    current = line.heading_id
    while current is not None:
        if current == heading_id:
            return True
        current = parents.get(current)
    return False


def _find(lines: list[ViewLine], find: str) -> tuple[list[ViewLine], int]:
    needle = " ".join(find.split()).casefold()
    parents = _parent_headings(lines)
    index_of = {line.node_id: index for index, line in enumerate(lines) if line.kind == "heading"}
    chosen: list[int] = []
    taken: set[int] = set()
    cut: dict[int, int] = {}  # Index of the last line taken from a section -> where that section goes on.
    matches = 0

    def take(index: int) -> None:
        if index not in taken:
            taken.add(index)
            chosen.append(index)

    for index, line in enumerate(lines):
        if needle not in " ".join(line.text.split()).casefold() or index in taken:
            continue
        matches += 1
        if line.kind == "heading":
            take(index)
            following = index + 1
            while following < len(lines) and _within(lines[following], line.node_id, parents):
                if following - index > FIND_BLOCK:
                    cut[following - 1] = following
                    break
                take(following)
                following += 1
        else:
            if line.heading_id is not None and line.heading_id in index_of:
                take(index_of[line.heading_id])
            take(index)
    result: list[ViewLine] = []
    previous = None
    for index in chosen:
        if previous is not None and index != previous + 1:
            result.append(ViewLine("…", "separator", ""))
        result.append(lines[index])
        if index in cut:
            result.append(ViewLine(f"… the section goes on at offset {cut[index]} of the whole view", "separator", ""))
        previous = index
    return result, matches


def _breadcrumb(lines: list[ViewLine], start: int, shown: list[ViewLine]) -> tuple[ViewLine, ...]:
    if start <= 0 or start >= len(lines):
        return ()
    first = lines[start]
    parents = _parent_headings(lines)
    headings = {line.node_id: line for line in lines if line.kind == "heading"}
    chain: list[str] = []
    current = first.heading_id
    while current is not None:
        chain.append(current)
        current = parents.get(current)
    crumbs = [headings[node_id] for node_id in reversed(chain) if node_id in headings]
    control = next((line for line in shown if line.kind == "control"), None)
    if control is not None and control.form_id is not None:
        form = next((line for line in lines[:start] if line.kind == "form" and line.form_id == control.form_id), None)
        if form is not None:
            crumbs.append(form)
    return tuple(crumbs)
