"""What changed between two agent lifts of the same page: for an agent, after it acts.

``diff_lifts(before, after)`` matches the controls of the two lifts and reports:

- controls whose state changed (value, checked, disabled, name, options, ...), field by field;
- controls that appeared or went away (a dialog opened, a section expanded, a row was deleted);
- page text that appeared or went away (a heading, a price, a label);
- messages that appeared (``role="alert"``, ``role="status"``, ``aria-live``): "Email is required";
- forms that appeared or went away.

Controls are matched in three passes: by snapshot ``ref`` (the same element in the browser), then by
control kind and accessible name when that pair is unique on both sides, then by node id (the same
place in the page) and control kind. A secret field's value never appears in a diff.

Both lifts should come from the same document. After a navigation to a new document nearly
everything differs; read the new page instead.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from viewspec.raw_html_agent import HtmlControl, form_tool_names

if TYPE_CHECKING:
    from viewspec.raw_html import HtmlLiftResult

CONTENT_ROLES = ("heading", "value", "prose", "label")
COMPARED_FIELDS = (
    "control",
    "name",
    "value",
    "checked",
    "disabled",
    "required",
    "readonly",
    "options",
    "href",
    "submits",
)
TEXT_PREVIEW = 80


@dataclass(frozen=True)
class ControlChange:
    """A control in both lifts whose state differs. ``fields`` maps a field to (before, after)."""

    before: HtmlControl
    after: HtmlControl
    fields: dict[str, tuple[Any, Any]]

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.after.node_id,
            "ref": self.after.ref,
            "control": self.after.control,
            "name": self.after.name,
            "fields": {name: {"before": old, "after": new} for name, (old, new) in self.fields.items()},
        }


@dataclass(frozen=True)
class LiftDiff:
    changed: tuple[ControlChange, ...] = ()
    added: tuple[HtmlControl, ...] = ()
    removed: tuple[HtmlControl, ...] = ()
    text_added: tuple[tuple[str, str], ...] = ()
    text_removed: tuple[tuple[str, str], ...] = ()
    messages: tuple[tuple[str, str], ...] = ()
    forms_added: tuple[str, ...] = ()
    forms_removed: tuple[str, ...] = ()
    same_layout: bool = True
    unchanged: int = field(default=0)

    @property
    def empty(self) -> bool:
        return not (
            self.changed
            or self.added
            or self.removed
            or self.text_added
            or self.text_removed
            or self.messages
            or self.forms_added
            or self.forms_removed
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "empty": self.empty,
            "same_layout": self.same_layout,
            "unchanged_controls": self.unchanged,
            "changed": [change.to_json() for change in self.changed],
            "added": [_control_json(control) for control in self.added],
            "removed": [_control_json(control) for control in self.removed],
            "text_added": [{"role": role, "text": text} for role, text in self.text_added],
            "text_removed": [{"role": role, "text": text} for role, text in self.text_removed],
            "messages": [{"kind": kind, "text": text} for kind, text in self.messages],
            "forms_added": list(self.forms_added),
            "forms_removed": list(self.forms_removed),
        }

    def lines(self, handle_of: Callable[[str], int | None] | None = None, *, limit: int = 40) -> list[str]:
        """The diff as short lines for an agent. ``handle_of`` maps an ``after`` node id to a view handle."""

        def tag(control: HtmlControl) -> str:
            handle = handle_of(control.node_id) if handle_of else None
            return f"[{handle}] " if handle is not None else ""

        lines: list[str] = []
        for kind, text in self.messages:
            lines.append(f"message {kind} {_short(text)!r}")
        for change in self.changed:
            parts = [f"{name} {_show(old)} -> {_show(new)}" for name, (old, new) in change.fields.items()]
            lines.append(f"changed {tag(change.after)}{change.after.control} {change.after.name!r}: {'; '.join(parts)}")
        for control in self.added:
            lines.append(f"added {tag(control)}{control.control} {control.name!r}")
        for control in self.removed:
            lines.append(f"removed {control.control} {control.name!r}")
        for name in self.forms_added:
            lines.append(f"form added {name}")
        for name in self.forms_removed:
            lines.append(f"form removed {name}")
        for role, text in self.text_added:
            lines.append(f"text + {role} {_short(text)!r}")
        for role, text in self.text_removed:
            lines.append(f"text - {role} {_short(text)!r}")
        if not lines:
            lines.append("no change")
        if len(lines) > limit:
            lines = [*lines[:limit], f"... {len(lines) - limit} more"]
        return lines


def diff_lifts(before: HtmlLiftResult, after: HtmlLiftResult) -> LiftDiff:
    """What changed from ``before`` to ``after``. Both must be lifts with ``profile="agent"``."""

    for lift in (before, after):
        if lift.profile != "agent":
            raise ValueError("diff_lifts needs two lifts made with profile='agent'")
    pairs, added, removed = _match(before.controls, after.controls)
    changed: list[ControlChange] = []
    for old, new in pairs:
        fields = _compare(old, new)
        if fields:
            changed.append(ControlChange(old, new, fields))
    before_text = Counter(_content(before))
    after_text = Counter(_content(after))
    # A message is new unless the page showed the same text in the same kind of region before.
    shown = Counter((message.kind, message.text) for message in before.messages)
    new_messages: list[tuple[str, str]] = []
    for message in after.messages:
        key = (message.kind, message.text)
        if shown[key] > 0:
            shown[key] -= 1
        else:
            new_messages.append(key)
    before_forms = set(form_tool_names(before.forms))
    after_forms = set(form_tool_names(after.forms))
    return LiftDiff(
        changed=tuple(changed),
        added=tuple(added),
        removed=tuple(removed),
        text_added=tuple(_ordered(after_text - before_text, _content(after))),
        text_removed=tuple(_ordered(before_text - after_text, _content(before))),
        messages=tuple(new_messages),
        forms_added=tuple(sorted(after_forms - before_forms)),
        forms_removed=tuple(sorted(before_forms - after_forms)),
        same_layout=before.topology_fingerprint.hex == after.topology_fingerprint.hex,
        unchanged=len(pairs) - len(changed),
    )


def _match(
    before: Sequence[HtmlControl], after: Sequence[HtmlControl]
) -> tuple[list[tuple[HtmlControl, HtmlControl]], list[HtmlControl], list[HtmlControl]]:
    left = list(before)
    right = list(after)
    pairs: list[tuple[HtmlControl, HtmlControl]] = []

    def take(key: Callable[[HtmlControl], Any], *, unique: bool) -> None:
        left_keys = Counter(key(control) for control in left)
        right_keys = Counter(key(control) for control in right)
        by_key: dict[Any, list[HtmlControl]] = {}
        for control in right:
            by_key.setdefault(key(control), []).append(control)
        kept: list[HtmlControl] = []
        for control in left:
            value = key(control)
            if value is None or not by_key.get(value):
                kept.append(control)
                continue
            if unique and (left_keys[value] != 1 or right_keys[value] != 1):
                kept.append(control)
                continue
            match = by_key[value].pop(0)
            right.remove(match)
            pairs.append((control, match))
        left[:] = kept

    take(lambda control: control.ref, unique=False)
    take(lambda control: (control.control, control.name) if control.name else None, unique=True)
    take(lambda control: (control.node_id, control.control), unique=False)
    order = {control.node_id: index for index, control in enumerate(after)}
    pairs.sort(key=lambda pair: order[pair[1].node_id])
    return pairs, right, left


def _compare(old: HtmlControl, new: HtmlControl) -> dict[str, tuple[Any, Any]]:
    fields: dict[str, tuple[Any, Any]] = {}
    for name in COMPARED_FIELDS:
        if name == "value" and (old.secret or new.secret):
            continue  # A secret value is never in the lift; never suggest one changed.
        if name == "options":
            before: Any = [(o.label, o.selected, o.disabled) for o in old.options]
            after: Any = [(o.label, o.selected, o.disabled) for o in new.options]
            if before != after:
                fields["options"] = (_options(old), _options(new))
            continue
        before, after = getattr(old, name), getattr(new, name)
        if before != after:
            fields[name] = (before, after)
    return fields


def _options(control: HtmlControl) -> list[str]:
    return [
        f"{o.label}{' (selected)' if o.selected else ''}{' (disabled)' if o.disabled else ''}" for o in control.options
    ]


def _content(lift: HtmlLiftResult) -> list[tuple[str, str]]:
    return [(role.role, role.text) for role in lift.roles if role.role in CONTENT_ROLES and role.text]


def _ordered(counts: Counter[tuple[str, str]], order: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Items of a Counter difference, in the order the page shows them."""

    remaining = Counter(counts)
    out: list[tuple[str, str]] = []
    for item in order:
        if remaining[item] > 0:
            out.append(item)
            remaining[item] -= 1
    return out


def _control_json(control: HtmlControl) -> dict[str, Any]:
    return {"node_id": control.node_id, "ref": control.ref, "control": control.control, "name": control.name}


def _short(text: str) -> str:
    return text if len(text) <= TEXT_PREVIEW else text[: TEXT_PREVIEW - 1] + "…"


def _show(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value).lower() if value is not None else "none"
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return repr(_short(str(value)))


__all__ = ["ControlChange", "LiftDiff", "diff_lifts"]
