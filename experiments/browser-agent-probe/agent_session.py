"""Read a live page with the ViewSpec agent lift, and act on it.

``BrowserSession`` drives ``bridge.js`` (a Playwright browser) and closes the loop:

    observe  snapshot the composed tree, lift it with ``profile="agent"``, render the page view,
             and save each control's identity and current blocked state
    act      plan one action on a control (``plan_control_action``) and run it
    call     plan a whole form tool call (``plan_form_call``) and run it

Every request goes through the package planner first, so a bad request fails before the browser
does anything. Each step names its element by ref (exact, while the element is in the page) and
carries the control's identity: its tag, its name, and the items around it (the card or row it
belongs to). The bridge acts only on an element with that identity, so a page that redraws itself
cannot move a step to another item's control; and it refuses a control that a dialog covers or the
page has switched off, and says what is in the way.

The operator chooses read and write permissions before the session starts. Each write records
its policy disposition. Both blocked and simulated writes remain blocked in action outcomes.
Open the next URL in the same session to retain its cookies and local storage.

``changes(before, after)`` says what an action changed. Besides the diff of the two readings it
reports what no two readings can show: a message the page showed again, one that came and went in
between (the page keeps a note of every write into a message region), and a new address in the
same document. Each step also says what the page did in answer: the dialogs it raised, the tabs it
opened, and how much it reacted.

This is an experiment. It is not part of the ViewSpec package or its guarantees.
"""

from __future__ import annotations

import base64
import contextlib
import email
import email.policy
import json
import mimetypes
import secrets
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from action_outcome import address_lines, message_lines, noted_messages
from boundary import Boundary, PrivacyRedactor
from page_view import ViewLine, view_lines

from viewspec.raw_html import HtmlLiftResult, lift_html
from viewspec.raw_html_actions import ActionStep, FormPlan, plan_control_action, plan_form_call
from viewspec.raw_html_agent import HINT_REF, HtmlControl, control_aria_role
from viewspec.raw_html_diff import ChangeCondition, LiftDiff, diff_lifts

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "bridge.js"
MAX_WAIT_MS = 30000
SCROLL_WAIT_MS = 2500  # The longest wait for new content after one scroll.
MAX_SCROLLS = 30
READ_ATTEMPTS = 3


class BridgeError(RuntimeError):
    """The browser could not do what was asked. ``steps`` holds the steps of the plan that ran
    before the one that could not."""

    steps: list[StepResult]

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.steps = []
        self.selection: dict[str, Any] | None = None
        self.failed_step: ActionStep | None = None
        self.observation_failed = False

    @property
    def interrupted_read(self) -> bool:
        return any(
            text in str(self)
            for text in (
                "read-interrupted:",
                "Execution context was destroyed",
                "Cannot find context with specified id",
            )
        )


@dataclass
class Observation:
    url: str
    title: str
    html: str
    lift: HtmlLiftResult
    view: str
    lines: list[ViewLine]
    handles: dict[int, HtmlControl]
    # Saved target identities in every reading; selector fields only with locate=True.
    locators: dict[str, dict[str, Any]]
    interactive: list[dict[str, str]]
    action_ids: int
    doc_id: str
    navigation_id: int
    secrets: list[str] = field(default_factory=list, repr=False)
    # Node id -> why a person could not use that control when the page was read ("covered by ...").
    blocked: dict[str, str] = field(default_factory=dict)
    # The page recorder's notes, one for each write into a message region ({seq, ref, kind, text}),
    # and its sequence number at this reading: a later reading's notes above it came in between.
    notes: tuple[dict[str, Any], ...] = ()
    note_seq: int = 0
    # How busy the page was: DOM mutations and requests so far, and the requests still in flight.
    activity: dict[str, Any] = field(default_factory=dict)
    reading_id: int = 0

    def handle_of(self, node_id: str) -> int | None:
        return next((handle for handle, control in self.handles.items() if control.node_id == node_id), None)


@dataclass
class StepResult:
    step: ActionStep
    resolved_by: str
    verified: bool | None
    navigated: bool
    url: str
    chosen: str | None = None  # The option, day, or range that was picked, or the names of the files set.
    # A saved download: {file, path, bytes, sha256, type, suggested, url}. Never its contents.
    download: dict[str, Any] | None = None
    unsaved_downloads: tuple[str, ...] = ()  # Downloads this step started but did not save.
    # Fields the browser refused when this click or submit tried to send a form, and why:
    # [{"name": "Email", "message": "Please include an '@' in the email address."}]. The form was not sent.
    invalid: tuple[dict[str, str], ...] = ()
    # What the page did in answer that a reading of it does not show. ``dialogs``: the boxes it
    # raised, [{"seq", "type": "confirm", "message": "Delete this draft?", "answer": "dismissed"}].
    # ``opened``: the tabs it opened, [{"seq", "url"}]; they are closed, not followed. ``reaction``:
    # {"mutations": DOM changes (None on a new document), "requests": started, "pending": of those,
    # still in flight} when the step ended.
    dialogs: tuple[dict[str, Any], ...] = ()
    opened: tuple[dict[str, Any], ...] = ()
    writes: tuple[dict[str, str], ...] = ()
    reaction: dict[str, Any] | None = None
    # Where the page's counts stood when the step began, to measure a later reading against.
    mutation_mark: int | None = None
    request_mark: int = 0
    selection: dict[str, Any] | None = None

    @property
    def event_seqs(self) -> frozenset[int]:
        return frozenset(item["seq"] for item in (*self.dialogs, *self.opened))

    def to_json(self) -> dict[str, Any]:
        payload = {
            "step": self.step.to_json(),
            "resolved_by": self.resolved_by,
            "verified": self.verified,
            "navigated": self.navigated,
            "url": self.url,
        }
        if self.chosen is not None:
            payload["chosen"] = self.chosen
        if self.selection is not None:
            payload["selection"] = self.selection
        if self.download is not None:
            payload["download"] = self.download
        if self.unsaved_downloads:
            payload["unsaved_downloads"] = list(self.unsaved_downloads)
        if self.invalid:
            payload["invalid"] = [{"name": item["name"], "message": item["message"]} for item in self.invalid]
        if self.dialogs:
            payload["dialogs"] = [dialog_json(item) for item in self.dialogs]
        if self.opened:
            payload["opened"] = [item["url"] for item in self.opened]
        if self.writes:
            payload["writes"] = list(self.writes)
        if self.reaction is not None:
            payload["reaction"] = dict(self.reaction)
        return payload


def dialog_json(dialog: dict[str, Any]) -> dict[str, str]:
    """A dialog the page raised, as an agent is told of it."""

    return {"type": dialog["type"], "message": dialog["message"], "answer": dialog["answer"]}


@dataclass
class WaitResult:
    condition: ChangeCondition
    met: bool
    waited_ms: int
    timeout_ms: int
    readings: int

    def to_json(self) -> dict[str, Any]:
        return {
            "for": self.condition.describe(),
            "condition": self.condition.to_json(),
            "met": self.met,
            "waited_ms": self.waited_ms,
            "timeout_ms": self.timeout_ms,
        }


@dataclass
class ScrollResult:
    """What scrolling did. ``scrolls`` is 0 when the ``until`` condition was already true."""

    step: ActionStep
    scrolls: int
    moved: bool
    loaded: bool
    at_end: bool
    page: bool
    met: bool | None
    end_reached: bool
    waited_ms: int

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "area": "page" if self.page else self.step.name,
            "direction": self.step.value,
            "scrolls": self.scrolls,
            "moved": self.moved,
            "loaded": self.loaded,
            "at_end": self.at_end,
            "end_reached": self.end_reached,
            "waited_ms": self.waited_ms,
        }
        if self.met is not None:
            payload["met"] = self.met
        return payload


def request_fields(write: dict[str, Any]) -> dict[str, Any] | None:
    """The form fields of a blocked request, or None when its body is not a form.

    A text field is its text. A file is ``{"file": name, "bytes": size, "type": media type}``; its
    bytes are never returned. A name sent more than once (several files) maps to a list.
    """

    content_type = write.get("content_type") or ""
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type not in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        return None
    if write.get("post_data_b64"):
        raw = base64.b64decode(write["post_data_b64"])
    else:
        raw = (write.get("post_data") or "").encode("utf-8")
    fields: dict[str, Any] = {}

    def add(name: str, value: Any) -> None:
        if name in fields:
            fields[name] = [*fields[name], value] if isinstance(fields[name], list) else [fields[name], value]
        else:
            fields[name] = value

    if media_type == "multipart/form-data":
        message = email.message_from_bytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + raw, policy=email.policy.HTTP
        )
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not isinstance(name, str):
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename is not None:
                add(name, {"file": filename, "bytes": len(payload), "type": part.get_content_type()})
            else:
                add(name, payload.decode("utf-8", "replace"))
        return fields
    text = raw.decode("utf-8", "replace")
    if "=" not in text:
        return None
    for name, values in parse_qs(text, keep_blank_values=True).items():
        for value in values:
            add(name, value)
    return fields


def _with_type(download: dict[str, Any] | None) -> dict[str, Any] | None:
    if download is None:
        return None
    return {**download, "type": mimetypes.guess_type(download["file"])[0] or "application/octet-stream"}


def condition_met(condition: ChangeCondition, since: Observation, current: Observation) -> bool:
    """Whether ``current`` shows the change, compared with ``since``.

    Only the session knows when the document changed: that is ``navigation``. After a navigation,
    ``any`` is met, and ``message`` is met by any message on the new page. In the same document,
    ``message`` is also met by a message the page showed again or showed and took away, and ``any``
    by those and by a new address or title: none of them is in the diff of the two lifts.
    """

    new_document = current.doc_id != since.doc_id
    if condition.kind == "navigation":
        return new_document
    if new_document and condition.kind == "any":
        return True
    if new_document and condition.kind == "message":
        return bool(current.lift.messages)
    if condition.kind not in {"any", "message"}:
        return condition.met(since.lift, current.lift)
    diff = diff_lifts(since.lift, current.lift)
    if diff.messages or _noted(since, current, diff):
        return True
    return condition.kind == "any" and (not diff.empty or bool(_moved(since, current)))


def _noted(before: Observation, after: Observation, diff: LiftDiff) -> list[tuple[str, str, str]]:
    """Messages the page wrote between two readings of one document that their diff does not show."""

    if not any(note["seq"] > before.note_seq for note in after.notes):
        return []
    regions = {message.node_id: (message.kind, message.text) for message in after.lift.messages}
    shown = {
        node.attrs[HINT_REF]: regions[node.id]
        for node in after.lift.nodes
        if node.id in regions and node.attrs.get(HINT_REF)
    }
    return noted_messages(after.notes, since=before.note_seq, shown=shown, new=diff.messages)


def _moved(before: Observation, after: Observation) -> list[str]:
    return address_lines(before.url, after.url, before.title, after.title)


def changes(before: Observation, after: Observation, privacy: PrivacyRedactor | None = None) -> dict[str, Any]:
    """What changed from one observation to a later one, as short lines for an agent.

    Handles in the lines are those of ``after``. After a navigation to a new document a diff means
    nothing, so the result only says that the page is new.

    Messages come first: the new ones, then those the page showed again (``(shown again)``) or
    showed and took away (``(shown, now gone)``) between the two readings. Then a new address or
    title in the same document, then the rest of the diff.
    """

    if privacy is not None:
        before, after = privacy.clean(before), privacy.clean(after)
    if before.doc_id != after.doc_id:
        return {"new_document": True, "empty": False, "lines": [f"new page {after.title!r} {after.url}"]}
    diff = diff_lifts(before.lift, after.lift)
    lines = [] if diff.empty else diff.lines(after.handle_of)
    unseen = [*message_lines(_noted(before, after, diff)), *_moved(before, after)]
    head = len(diff.messages)
    lines = [*lines[:head], *unseen, *lines[head:]] or ["no change"]
    return {"new_document": False, "empty": diff.empty and not unseen, "lines": lines}


class BrowserSession:
    def __init__(
        self,
        *,
        block_writes: bool = True,
        allowed_origins: tuple[str, ...] = (),
        download_dir: str | Path | None = None,
        boundary: Boundary | None = None,
        privacy: PrivacyRedactor | None = None,
    ) -> None:
        # The limits this session runs inside (see boundary.py). With one, every connection of
        # the browser passes the bridge's gate, and block_writes and allowed_origins are not used.
        self.boundary = boundary or Boundary.build(allow_origin=allowed_origins, allow_writes=not block_writes)
        self.privacy = privacy or self.boundary.make_redactor()
        self._readings: dict[int, Observation] = {}
        self._reading_seq = secrets.randbits(96)
        self.block_writes = block_writes
        self.allowed_origins = tuple(allowed_origins)
        # The only folder a download step saves files in. None: download steps are refused.
        self.download_dir = Path(download_dir).resolve() if download_dir is not None else None
        self._observation: Observation | None = None
        self._next_id = 0
        self._opened = False
        self.action_progress: dict[str, Any] | None = None
        self._proc = subprocess.Popen(
            ["node", str(BRIDGE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    # ---- plumbing ---------------------------------------------------------------------------------
    def _call(self, cmd: str, **args: Any) -> Any:
        if cmd not in {"close", "storage_state"}:
            self.privacy.require_capacity()
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._next_id += 1
        self._proc.stdin.write(json.dumps({"id": self._next_id, "cmd": cmd, **args}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            stderr = (self._proc.stderr.read() if self._proc.stderr else "").strip()
            stderr = self.privacy.clean(stderr)
            last = stderr.splitlines()[-1] if stderr else "no message"
            raise BridgeError(f"bridge.js stopped: {last[:300]}")
        reply = json.loads(line)
        if reply.get("id") != self._next_id:
            raise BridgeError(f"bridge.js answered request {reply.get('id')}, not {self._next_id}")
        if not reply["ok"]:
            error = BridgeError(self.privacy.clean(reply["error"]))
            error.selection = self.privacy.clean(reply.get("selection"))
            raise error
        return reply["result"]

    def _read_call(self, cmd: str, **args: Any) -> Any:
        """Retry only a read interrupted by navigation. Never replay an action."""
        assert cmd in {"snapshot", "document"}
        for attempt in range(READ_ATTEMPTS):
            try:
                return self._call(cmd, **args)
            except BridgeError as error:
                if not error.interrupted_read or attempt == READ_ATTEMPTS - 1:
                    raise
                self._call("read_ready")

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._opened and self.boundary.save_storage_state:
                    self.boundary.save_state(self._call("storage_state"))
            finally:
                with contextlib.suppress(BridgeError):
                    self._call("close")
                self._proc.wait(timeout=10)

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- reading ----------------------------------------------------------------------------------
    def open(self, url: str) -> dict[str, Any]:
        """Load a page. Return its status and privacy-projected HTML; call ``observe()`` to read it."""

        if refused := self.boundary.refusal(url):
            raise BridgeError(self.privacy.clean(f"{refused[0]}: {refused[1]}"))
        self._observation = None
        result = self._call(
            "open",
            url=url,
            block_writes=self.block_writes,
            allowed_origins=list(self.allowed_origins) or None,
            download_dir=str(self.download_dir) if self.download_dir is not None else None,
            policy=self.boundary.browser_config() if self.boundary is not None else None,
        )
        self._opened = True
        self.privacy.learn_html(result.get("static_html", ""))
        self._capture_private()
        if "static_html" in result:
            result["static_html"] = self.privacy.project_html(result["static_html"])
        return self.privacy.clean(result)

    def _capture_private(self) -> dict[str, Any]:
        snap = self._read_call("snapshot", collect_secrets=self.privacy.mode)
        self.privacy.remember(snap.get("secrets", []))
        self.privacy.remember(snap.get("credential_values", []))
        self.privacy.learn_html(snap["html"])
        self.privacy.learn(snap)
        return snap

    @property
    def observation(self) -> Observation | None:
        return self.project(self._observation) if self._observation is not None else None

    def project(self, observation: Observation) -> Observation:
        """A display copy; the original names, refs and values stay in private planning history."""
        # Render from the real structure, redacting each complete piece of text before previews.
        lines, _ = view_lines(observation.lift, blocked=observation.blocked, redact=self.privacy.clean)
        public = self.privacy.clean(observation)
        return replace(
            public,
            html=self.privacy.project_html(observation.html),
            lines=lines,
            view="\n".join(line.text for line in lines),
            secrets=[],
        )

    def _resolve_observation(self, observation: Observation) -> Observation:
        raw = self._readings.get(observation.reading_id)
        if raw is None or (raw.doc_id, raw.navigation_id) != (observation.doc_id, observation.navigation_id):
            raise BridgeError("stale-view: this reading is unknown or expired; observe the page again")
        return raw

    def observe(self, *, locate: bool = False, collect_secrets: bool = False) -> Observation:
        """Return a safe reading. ``collect_secrets`` is retained for compatibility, never disclosed.

        Secret discovery is always enabled internally. Public snapshots cannot opt out of the
        operator's policy. Browser actions resolve the returned reading to its private original.
        ``locate=True`` additionally verifies exportable selectors for diagnostics; actions only
        need the saved refs and identities, which are collected in either mode.
        """
        return self.project(self._observe(locate=locate))

    def _observe(self, *, locate: bool = False) -> Observation:
        """Snapshot, lift, and read target identities in one batch, without preparing selectors."""

        for attempt in range(READ_ATTEMPTS):
            try:
                return self._observe_once(locate=locate)
            except BridgeError as error:
                if not error.interrupted_read or attempt == READ_ATTEMPTS - 1:
                    raise
                self._call("read_ready")

    def _observe_once(self, *, locate: bool = False) -> Observation:

        snap = self._capture_private()
        lift = lift_html(snap["html"], profile="agent")
        locators: dict[str, dict[str, Any]] = {}
        requests = [
            {"key": c.node_id, "ref": c.ref, "role": control_aria_role(c), "name": c.name}
            for c in lift.controls
            if c.ref
        ]
        requests += [{"key": f.node_id, "ref": f.ref, "role": None, "name": None} for f in lift.forms if f.ref]
        if requests:
            locators = self._call(
                "locate" if locate else "targets",
                requests=requests,
                doc_id=snap["docId"],
                navigation_id=snap["navigationId"],
            )
        # What the element must still be when a re-render forces the identity fallback. Never
        # defer this reading until action time: the same element may then belong to another item.
        expected = {c.node_id: (c.tag, c.name) for c in lift.controls}
        expected |= {f.node_id: ("form", None) for f in lift.forms}
        for node_id, locator in locators.items():
            locator["tag"], locator["expect_name"] = expected[node_id]
        # A control a person could not use now (a dialog covers it, or the page has switched it off),
        # and why. The page is asked, not the lift: the answer depends on what is drawn over what.
        blocked = {node_id: locator["blocked"] for node_id, locator in locators.items() if locator.get("blocked")}
        lines, handles = view_lines(lift, blocked=blocked)
        view = "\n".join(line.text for line in lines)
        self._observation = Observation(
            url=snap["url"],
            title=snap["title"],
            html=snap["html"],
            lift=lift,
            view=view,
            lines=lines,
            handles=handles,
            locators=locators,
            blocked=blocked,
            interactive=snap["interactive"],
            action_ids=snap["actionIds"],
            doc_id=snap["docId"],
            navigation_id=snap["navigationId"],
            secrets=[],
            notes=tuple(snap["recorder"]["notes"]),
            note_seq=snap["recorder"]["seq"],
            activity=snap["activity"],
        )
        self._reading_seq += 1
        self._observation.reading_id = self._reading_seq
        self._readings[self._reading_seq] = self._observation
        for key in sorted(self._readings)[:-8]:
            del self._readings[key]
        return self._observation

    def confirm_locators(self, observation: Observation) -> dict[str, bool]:
        """Verify selectors from ``observe(locate=True)`` (by node id)."""

        observation = self._resolve_observation(observation)
        refs = {control.node_id: control.ref for control in observation.lift.controls}
        items = [
            {"key": key, "ref": refs[key], "locator": locator}
            for key, locator in observation.locators.items()
            if locator.get("strategy") and refs.get(key)
        ]
        return self._call("confirm", items=items) if items else {}

    def aria_snapshot(self) -> str:
        self._capture_private()
        return self.privacy.clean(self._call("aria")["aria"])

    def reload(self) -> None:
        self._call("reload")
        self._observation = None

    def wait(self, condition: ChangeCondition, since: Observation, *, timeout_ms: int = 5000) -> WaitResult:
        """Wait until the page shows ``condition`` compared with ``since``, or until ``timeout_ms``.

        The page is read again after each change or navigation, never on a fixed poll. A change is a
        DOM change, or a field that holds something else (a script may set only ``.value``). The view
        is stale afterwards; call ``observe()`` again.
        """

        since = self._resolve_observation(since)
        limit = max(0, min(int(timeout_ms), MAX_WAIT_MS))
        start = time.monotonic()
        readings = 0
        while True:
            current = self._observe(locate=False)
            readings += 1
            met = condition_met(condition, since, current)
            elapsed = int((time.monotonic() - start) * 1000)
            if met or elapsed >= limit:
                break
            try:
                self._call("wait_change", timeout_ms=limit - elapsed)
            except BridgeError as error:
                if not error.interrupted_read:
                    raise
                # The next loop reads the current page and checks the original condition.
                # Its elapsed-time budget still includes this interrupted wait.
        self._observation = None
        return self.privacy.clean(WaitResult(condition, met, elapsed, limit, readings))

    def scroll(
        self,
        step: ActionStep,
        *,
        observation: Observation | None = None,
        until: ChangeCondition | None = None,
        timeout_ms: int = 5000,
    ) -> ScrollResult:
        """Scroll the page, or the area of the step's control, and wait for what loads.

        Without ``until``, scroll once. With it, scroll until the page shows it, the list ends (a scroll
        at the end loads nothing), or ``timeout_ms`` ends. The view is stale afterwards.
        """

        observation = self._resolve_observation(observation) if observation is not None else self._current()
        if self._call("document")["doc_id"] != observation.doc_id:
            raise BridgeError(
                "stale-view: the page has loaded a new document since this view; observe it and plan again"
            )
        locator = observation.locators.get(step.node_id) if step.node_id else None
        limit = max(0, min(int(timeout_ms), MAX_WAIT_MS))
        start = time.monotonic()
        scrolls = 0
        moved = loaded = at_end = end_reached = False
        page = step.ref is None
        met: bool | None = None
        if until is not None and condition_met(until, observation, observation):
            met = True
        while met is not True:
            elapsed = int((time.monotonic() - start) * 1000)
            wait = max(300, min(SCROLL_WAIT_MS, limit - elapsed))
            reply = self._call(
                "act",
                navigation_id=observation.navigation_id,
                op="scroll",
                ref=step.ref,
                locator=locator,
                value=step.value,
                wait_ms=wait,
            )
            info = reply["scroll"]
            scrolls += 1
            moved = moved or info["moved"]
            loaded = loaded or info["loaded"]
            at_end, page = info["at_end"], info["page"]
            if until is None:
                break
            if condition_met(until, observation, self._observe(locate=False)):
                met = True
                break
            stuck = (info["at_start"] if step.value in {"up", "top"} else info["at_end"]) and not info["loaded"]
            elapsed = int((time.monotonic() - start) * 1000)
            if stuck or elapsed >= limit or scrolls >= MAX_SCROLLS:
                end_reached = stuck
                met = False
                break
        self._observation = None
        waited = int((time.monotonic() - start) * 1000)
        return self.privacy.clean(ScrollResult(step, scrolls, moved, loaded, at_end, page, met, end_reached, waited))

    def records(self) -> dict[str, list[dict[str, Any]]]:
        """What the bridge has recorded since the page was opened: ``writes`` and ``navigations``
        that were stopped, ``dialogs`` the page raised, and ``popups``, the tabs it opened."""

        records = self._records()
        # The Python API is a public output boundary too. Raw request bodies are private even
        # when no field label identifies their contents; consumers receive body sizes only.
        for key in ("writes", "passed_writes"):
            records[key] = [self._request_summary(item) for item in records.get(key, [])]
        return self.privacy.clean(records)

    def _records(self) -> dict[str, list[dict[str, Any]]]:
        """Trusted journal for the MCP projection; never hand this to an external reader."""
        return self._call("writes")

    @staticmethod
    def _request_summary(item: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in item.items() if key not in {"post_data", "post_data_b64"}}
        if item.get("post_data_b64"):
            result["body_bytes"] = len(base64.b64decode(item["post_data_b64"]))
        elif item.get("post_data"):
            result["body_bytes"] = len(item["post_data"].encode("utf-8"))
        return result

    def blocked_writes(self) -> list[dict[str, Any]]:
        """Requests that ``block_writes`` stopped since the page was opened."""

        return self.records()["writes"]

    def blocked_navigations(self) -> list[dict[str, Any]]:
        """Top-level navigations that ``allowed_origins`` stopped since the page was opened."""

        return self.records()["navigations"]

    def blocked_requests(self) -> list[dict[str, Any]]:
        """Requests the boundary stopped that are neither a write nor a page: a script, an image, or
        a socket for somewhere the session may not reach, and a redirect that led there."""

        return self.records().get("refusals", [])

    # ---- acting -----------------------------------------------------------------------------------
    def _current(self) -> Observation:
        return self._observation if self._observation is not None else self._observe()

    def act(self, handle: int, op: str, value: Any = None) -> list[StepResult]:
        """Plan and run one action on the control with this handle in the current view."""

        observation = self._current()
        if handle not in observation.handles:
            raise KeyError(f"no control [{handle}] in the current view")
        return self.run(plan_control_action(observation.handles[handle], op, value, lift=observation.lift))

    def call(self, tool_name: str, arguments: dict[str, Any], *, submit: bool = True) -> list[StepResult]:
        """Plan and run a form tool call from the current view's ``form_tool_schemas()``."""

        return self.run(plan_form_call(self._current().lift, tool_name, arguments, submit=submit))

    def run(self, steps: tuple[ActionStep, ...], *, observation: Observation | None = None) -> list[StepResult]:
        self.action_progress = {
            "fields": [{"index": index + 1, "status": "not_run"} for index in range(len(steps))],
            "submission": "not_requested",
            "writes": {"passed": 0, "blocked": 0, "blocked-and-simulated": 0},
        }
        for step in steps:
            if step.secret or (self.privacy.mode == "strict" and step.op in {"fill", "edit"}):
                value = step.value
                if step.op == "edit" and isinstance(value, dict):
                    self.privacy.remember([op["text"] for op in value["operations"]])
                else:
                    self.privacy.remember([value or "", *step.values])
        self._capture_private()
        results = None
        try:
            results = self._run(steps, observation=observation)
            self._capture_private()
            return self.privacy.clean(results)
        except BridgeError as error:
            if results is not None:
                error.steps = results  # The action finished; only its subsequent capture failed.
                error.observation_failed = True
            safe = BridgeError(self.privacy.clean(str(error)))
            safe.steps = self.privacy.clean(error.steps)
            safe.selection = self.privacy.clean(error.selection)
            safe.failed_step = self.privacy.clean(error.failed_step)
            safe.observation_failed = error.observation_failed
            raise safe from None
        finally:
            if isinstance(steps, FormPlan):
                with contextlib.suppress(BridgeError):
                    self._call("clear_form")
            self._observation = None

    def _run(self, steps: tuple[ActionStep, ...], *, observation: Observation | None = None) -> list[StepResult]:
        """Run planned steps in order. The view is stale afterwards; call ``observe()`` again.

        ``observation`` is the view the steps were planned from (by default, the current one). Its
        locators find an element again when the page has replaced it since that view.
        """

        observation = self._resolve_observation(observation) if observation is not None else self._current()
        if self._read_call("document")["doc_id"] != observation.doc_id:
            # Document ids also distinguish observations from different browser sessions, whose
            # navigation counters may happen to match. The bridge checks the counter on every step.
            raise BridgeError(
                "stale-view: the page has loaded a new document since this view; observe it and plan again"
            )
        results: list[StepResult] = []
        controls = {control.node_id: control for control in observation.lift.controls}
        submissions = {
            index
            for index, step in enumerate(steps)
            if step.op == "submit" or (step.node_id in controls and controls[step.node_id].submits)
        }
        if submissions:
            self.action_progress["submission"] = "not_run"
        if isinstance(steps, FormPlan):
            fields = [
                {
                    "ref": item.control.ref,
                    "identity": observation.locators.get(item.control.node_id, {}).get("identity"),
                    "kind": item.kind,
                    "editor_revision": item.control.editor.revision if item.control.editor else None,
                    "preserve": item.preserve,
                    "expected": item.expected,
                }
                for item in steps.intents
            ]
            initial = self._call("begin_form", fields=fields, navigation_id=observation.navigation_id)["fields"]
            self.action_progress["contract"] = initial
            if any(item["status"] == "unknown" for item in initial):
                if submissions:
                    self.action_progress["submission"] = "withheld"
                raise BridgeError("form-unknown: cannot establish the initial field contract; no edits ran")
        for index, step in enumerate(steps):
            # The locator goes along even when it has no selector: it carries the control's identity,
            # which the bridge checks on the element it acts on, however it finds it.
            locator = observation.locators.get(step.node_id)
            end_locator = observation.locators.get(step.end_node_id) if step.end_node_id else None
            self.action_progress["fields"][index]["status"] = "attempted"
            if index in submissions:
                self.action_progress["submission"] = "attempted"
            try:
                reply = self._call(
                    "act",
                    navigation_id=observation.navigation_id,
                    form_submit=isinstance(steps, FormPlan) and index in submissions,
                    op=step.op,
                    ref=step.ref,
                    locator=locator,
                    end_ref=step.end_ref,
                    end_locator=end_locator,
                    value=step.value,
                    values=list(step.values),
                    files=[
                        {"name": f.name, "mime_type": f.mime_type, "data_b64": base64.b64encode(f.data).decode("ascii")}
                        for f in step.files
                    ],
                )
            except BridgeError as error:
                error.steps = list(results)  # What ran before the step that could not.
                error.failed_step = step
                raise
            # Preserve structural evidence before capture/projection can fail. It contains no
            # page strings and is safe to report even when privacy discovery is incomplete.
            self.action_progress["fields"][index]["status"] = "verified" if reply["verified"] is True else "applied"
            for write in reply.get("writes", []):
                if write.get("state") in self.action_progress["writes"]:
                    self.action_progress["writes"][write["state"]] += 1
            contract = reply.get("contract")
            if contract is not None:
                self.action_progress["contract"] = contract["fields"]
                self.action_progress["submission"] = contract["submission"]
            results.append(
                StepResult(
                    step=step,
                    resolved_by=reply["resolved_by"],
                    writes=tuple(reply.get("writes", [])),
                    verified=reply["verified"],
                    navigated=reply["navigated"],
                    url=reply["url"],
                    chosen=reply.get("chosen"),
                    selection=reply.get("selection"),
                    download=_with_type(reply.get("download")),
                    unsaved_downloads=tuple(reply.get("unsaved_downloads") or ()),
                    invalid=tuple(dict(item) for item in reply.get("invalid") or ()),
                    dialogs=tuple(reply.get("dialogs") or ()),
                    opened=tuple(reply.get("opened") or ()),
                    reaction=reply.get("reaction"),
                    mutation_mark=reply.get("mutation_mark"),
                    request_mark=reply.get("request_mark", 0),
                )
            )
            withheld = contract is not None and contract["submission"] == "withheld"
            unknown = (
                reply["verified"] is None
                and not reply["navigated"]
                and step.op
                in {
                    "choose",
                    "choose_set",
                    "choose_date",
                    "choose_date_range",
                }
            )
            if reply["verified"] is False or withheld or unknown:
                if isinstance(steps, FormPlan) and not withheld:
                    with contextlib.suppress(BridgeError):
                        self.action_progress["contract"] = self._call(
                            "check_form", navigation_id=observation.navigation_id
                        )["fields"]
                code = "form-mismatch" if reply["verified"] is False else "form-unknown"
                error = BridgeError(f"{code}: requested state was not verified; remaining steps did not run")
                error.steps = list(results)
                error.failed_step = step
                error.selection = reply.get("selection")
                raise error
            if reply["navigated"] and len(results) < len(steps):
                # The rest of the plan was made for the page that is gone.
                self._observation = None
                interrupted = BridgeError(
                    f"The page navigated after step {len(results)} of {len(steps)} ({step.op} {step.name!r}); "
                    f"{len(steps) - len(results)} step(s) did not run. Observe the new page and plan again."
                )
                interrupted.steps = list(results)
                raise interrupted
        if isinstance(steps, FormPlan) and not submissions:
            final = self._call("check_form", navigation_id=observation.navigation_id)["fields"]
            self.action_progress["contract"] = final
            if any(item["status"] != "matched" for item in final):
                code = "form-mismatch" if any(item["status"] == "mismatch" for item in final) else "form-unknown"
                error = BridgeError(f"{code}: the form does not establish all requested field states")
                error.steps = results
                raise error
        self._observation = None
        return results
