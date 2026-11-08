"""A compact, agent-facing text view of an agent-profile lift.

Each control gets a numbered handle (``[7]``). An agent reads the view and acts by handle; the session
maps the handle back to the control, its ref, and its locator.
"""

from __future__ import annotations

from viewspec.raw_html import HtmlLiftResult, LiftNode
from viewspec.raw_html_agent import HtmlControl

VIEW_GROUP_MIN = 3
TEXT_PREVIEW = 70
OPTION_PREVIEW = 8


def control_line(handle: int, control: HtmlControl) -> str:
    label = control.name or control.field_name or "(unnamed)"
    line = f"[{handle}] {control.control} {label[:TEXT_PREVIEW]!r}"
    if control.kind == "field":
        if control.checked is not None:
            line += " [x]" if control.checked else " [ ]"
        elif control.value and not control.secret:
            line += f" = {control.value[:TEXT_PREVIEW]!r}"
        if control.options:
            shown = "|".join(option.label for option in control.options[:OPTION_PREVIEW])
            more = f"|+{len(control.options) - OPTION_PREVIEW}" if len(control.options) > OPTION_PREVIEW else ""
            line += f" options: {shown}{more}"
        if control.required:
            line += " required"
        if control.secret:
            line += " secret"
        if control.readonly:
            line += " read-only"
    if control.href:
        line += f" -> {control.href}"
    if control.submits:
        line += " (submits)"
    if control.action_id:
        line += f" #{control.action_id}"
    if control.disabled:
        line += " disabled"
    return line


def subtree_text(node: LiftNode, by_id: dict[str, LiftNode]) -> str:
    parts = [node.text, *(subtree_text(by_id[child_id], by_id) for child_id in node.child_ids)]
    return " ".join(part for part in parts if part).strip()


def page_view(lift: HtmlLiftResult) -> tuple[str, dict[int, HtmlControl]]:
    """Return the view text and the control behind each handle.

    The view lists forms, headings, controls, values, table rows, and repeated groups in document
    order. Handles count controls from 1 in document order, so the same page gives the same handles.
    """

    group_of: dict[str, tuple[str, ...]] = {}
    for group in lift.group_candidates:
        if len(group) >= VIEW_GROUP_MIN:
            for member in group:
                group_of[member] = group
    controls = {control.node_id: control for control in lift.controls}
    forms = {form.node_id: form for form in lift.forms}
    tool_names = {
        form.node_id: schema["name"] for form, schema in zip(lift.forms, lift.form_tool_schemas(), strict=True)
    }
    roles = {role.node_id: role for role in lift.roles}
    by_id = {node.id: node for node in lift.nodes}
    lines: list[str] = []
    handles: dict[int, HtmlControl] = {}
    in_row: set[str] = set()
    for node in lift.nodes:
        if node.parent_id in in_row and node.id not in controls:
            in_row.add(node.id)
            continue
        group = group_of.get(node.id)
        if group and group[0] == node.id:
            lines.append(f"group {len(group)}x <{node.tag}>")
        if node.id in forms:
            form = forms[node.id]
            lines.append(
                f"form {form.name!r} {form.method} {form.action or ''}".rstrip() + f" tool={tool_names[node.id]}"
            )
        if node.tag == "tr":
            # One line per table row keeps each label next to its value. Controls in a row still
            # get their own line below it.
            cells = [subtree_text(by_id[child_id], by_id) for child_id in node.child_ids]
            lines.append("row " + " | ".join(cell[:TEXT_PREVIEW] for cell in cells))
            in_row.add(node.id)
            continue
        if node.id in controls:
            handle = len(handles) + 1
            handles[handle] = controls[node.id]
            lines.append(control_line(handle, controls[node.id]))
            continue
        role = roles.get(node.id)
        if role is None:
            continue
        if role.role == "heading":
            lines.append(f"{node.tag.upper()} {role.text[:TEXT_PREVIEW]}")
        elif role.role == "value":
            lines.append(f"value {role.text}")
    return "\n".join(lines), handles
