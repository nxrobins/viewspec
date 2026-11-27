"""The ViewSpec agent lift as MCP tools: observe a live page, act on a control, call a form tool.

    python experiments/browser-agent-probe/mcp_browser_server.py --allow-origin ORIGIN --allow-read PATTERN

Three tools, served over stdio:

    observe  open a URL (optional) and read the page: a compact view with a numbered handle for each
             control, and one typed tool per form
    act      click, fill, check, uncheck, or select one control, by handle
    call     fill in and submit a form, by its tool name and typed arguments

``act`` and ``call`` check the request against the page before the browser does anything
(``viewspec.raw_html_actions``), run the steps, and observe again, so each result carries the new
view. A handle belongs to the observation that showed it; pass ``observation_id`` to act from an
older one, and a re-rendered element is found again by its saved locator, or the step is refused.

Each ``act`` and ``call`` result also carries ``outcome``: whether the action took effect
(``succeeded``, ``blocked``, ``failed``, or ``uncertain``), why, and whether repeating it could do it twice
(``action_outcome``). A refused request has one too.

Safety is set by whoever starts the server, never by the agent:

- The operator selects trial origins or explicitly enables public read-only browsing. Request
  and redirect destinations, mutation endpoints, and connection channels have separate permissions.
- A blocked HTTP write normally receives 403. Simulation returns 200 only to the page. In both
  cases the agent sees a blocked outcome and the write disposition. A passed write is permission
  to dispatch. Its business effect still depends on the server.
- Privacy is declared by ``--private``. Strict mode withholds all text-entry field values;
  heuristic mode withholds recognized sensitive fields. Both redact learned and operator-supplied
  credentials from output. Arbitrary secrets in ordinary prose are not automatically recognized.

This server is not ``viewspec mcp``. That server makes no network calls; this one drives a browser
that loads whatever pages the agent opens. It is an experiment, not part of the ViewSpec package.
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import FrameType
from typing import Any, Literal
from urllib.parse import urlsplit

from action_outcome import action_evidence, outcome_of, scroll_outcome
from agent_session import (
    BridgeError,
    BrowserSession,
    Observation,
    ScrollResult,
    StepResult,
    WaitResult,
    changes,
    dialog_json,
    request_fields,
)
from boundary import Boundary, BoundaryError, PrivacyLimit
from page_view import view_page
from typing_extensions import TypedDict

from viewspec.local_tools_response import tool_error_response, tool_response
from viewspec.raw_html_actions import (
    MAX_UPLOAD_BYTES,
    ActionStep,
    AgentActionError,
    FormPlan,
    UploadFile,
    plan_control_action,
    plan_form_call,
    plan_scroll,
)
from viewspec.raw_html_agent import form_fields, form_tool_names, form_tool_schema, names_a_secret
from viewspec.raw_html_diff import ChangeCondition

SERVER_NAME = "viewspec-browser"
HISTORY = 8  # Observations kept, so an agent can act from a recent one.
REDACTED = "[redacted]"
NOT_SHOWN = "[not shown]"
INSTRUCTIONS = (
    "Read and operate web pages through the ViewSpec agent lift. Start with observe(url). Its view lists "
    "every control as [n]; act(handle=n, action=...) clicks, fills, checks, unchecks, or selects one; "
    'act(action="scroll") scrolls the page, and loads more of an infinite list. Each '
    "form represented by a form or control line in this slice has a complete typed tool in form_tools; "
    "page or find to discover other forms. call(tool=..., arguments={...}) fills a form in and submits it. "
    "Requests are checked against the page first: on ok=false, read errors and fix the request. act and "
    "call return the new view and changes, the lines that say what the action changed (messages first); "
    "use the new handles next. observe(since=id) shows what changed since an earlier reading. A page "
    "that updates after a delay (a server answer) shows nothing at first: pass wait_for, for example "
    '{"change": "message"} or {"text": "Order placed"}, and the tool reads the page again as it '
    "changes, until that shows or timeout_ms ends; wait.met says which. Writes may be blocked: a submitted "
    "write then appears in blocked_writes, even if the page displays success. A control line "
    "that ends with 'blocked' is on the page but cannot be used now; the first lines of the view say what "
    "is in the way (a dialog over the page, for example). Deal with that first. A handle names one control "
    "in one item: if the page redraws itself, the step still goes to that item's control, or is refused. "
    "The server runs inside limits that you cannot change: observe(url) returns them as limits (which pages "
    "may be opened, which writes are sent, which secrets it holds). What a limit stops is listed in "
    "blocked_navigations, blocked_writes, or blocked_requests. To type a secret the server holds, pass its "
    'name, value={"secret": "NAME"}, to fill a secret field: you never hold the value, and no result shows it. '
    "Every act and call result has outcome: status says whether the action took effect (succeeded, blocked, failed, "
    "or uncertain), reason says why, and retry says whether repeating it is not_needed, safe, or may_repeat "
    "(it could do the thing twice). When uncertain, page says what the page did meanwhile: busy (a request "
    "is still in flight: wait, with wait_for or observe), reacted, or quiet (nothing reacted). Read outcome "
    "before you repeat an action. evidence separates execution, control verification, and completion. "
    "A succeeded outcome can mean only a UI reaction; evidence.completion never infers persisted success. "
    "Use wait_for for the task's UI condition when needed; pending or unknown completion does not undo a verified field. "
    "readiness reports finite, streaming, and unclassified pending work. Initial content may arrive later. "
    "A form call can return BROWSER_FORM_PENDING with submission withheld; observe or wait for its prerequisite, "
    "and check prior writes before repeating it."
)
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_VIEW_LIMIT = 200
MAX_VIEW_LIMIT = 1000


class FileArg(TypedDict, total=False):
    """A file to upload: ``name`` and ``content`` (text, or base64 with ``encoding: "base64"``), or a
    ``path`` inside the upload folder the server was started with."""

    name: str
    content: str
    encoding: Literal["text", "base64"]
    mime_type: str
    path: str


class _ToolError(Exception):
    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.result = _error(code, message, fix)


class WaitFor(TypedDict, total=False):
    """What to wait for after the action. Give exactly one of change, text, gone, or control."""

    change: Literal["any", "message", "navigation"]
    text: str
    gone: str
    control: str
    enabled: bool


def _judged(tool: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Give a tool's result an ``outcome`` when it has none yet: a request that was refused before
    any step ran. A result of steps that ran gets its outcome where the page's activity is known."""

    @functools.wraps(tool)
    def judged(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = tool(*args, **kwargs)
        if result.get("blocked_writes") or any(
            write.get("state") in {"blocked", "blocked-and-simulated"}
            for step in result.get("steps", ())
            for write in step.get("writes", ())
        ):
            result["outcome"] = outcome_of(result)
            result["summary"] = result["outcome"]["reason"]
        elif "outcome" not in result:
            result["outcome"] = outcome_of(result)
        result["evidence"] = action_evidence(result)
        return result

    return judged


def _sealed(tool: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Take the value of every declared secret out of a tool's result, wherever the page put it. This
    is the last thing done to a result, so it does not depend on which code built which part."""

    @functools.wraps(tool)
    def sealed(self: BrowserTools, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            try:
                self.privacy.require_capacity()
                result = tool(self, *args, **kwargs)
                return self.privacy.clean(result)
            except PrivacyLimit:
                result = _error("BROWSER_PRIVACY_LIMIT", "Privacy limit reached.", "Start a fresh session.")
                if tool.__name__ in {"act", "call"}:
                    result["outcome"] = {
                        "status": "uncertain",
                        "retry": "may_repeat",
                        "reason": "Privacy limit reached during the session.",
                    }
                return result
            except Exception:
                return _error(
                    "BROWSER_INTERNAL_ERROR", "The browser operation failed.", "Observe the page before retrying."
                )
            finally:
                self._write_source = None
                self._said_for_write = ""

    return sealed


class BrowserTools:
    """The three tools, over one browser session that starts on the first ``observe``."""

    def __init__(
        self,
        *,
        block_writes: bool = True,
        allowed_origins: tuple[str, ...] = (),
        upload_dir: Path | None = None,
        download_dir: Path | None = None,
        boundary: Boundary | None = None,
    ) -> None:
        # The limits of this server (see boundary.py). ``block_writes`` and ``allowed_origins`` are
        # the older way to state two of them; a combination the boundary refuses is refused here.
        self.boundary = boundary or Boundary.build(allow_origin=allowed_origins, allow_writes=not block_writes)
        self.privacy = self.boundary.make_redactor()
        block_writes = not self.boundary.sends_writes
        allowed_origins = self.boundary.origins
        self.block_writes = block_writes
        self.upload_dir = upload_dir.resolve() if upload_dir is not None else None
        self.download_dir = download_dir.resolve() if download_dir is not None else None
        self.allowed_origins = tuple(origin.rstrip("/") for origin in allowed_origins)
        self._session: BrowserSession | None = None
        self._history: dict[int, Observation] = {}
        self._latest = 0
        # How far each of the bridge's records has been reported: a count, or for dialogs and
        # opened tabs ("events") the highest sequence number.
        self._reported = {"writes": 0, "passed": 0, "navigations": 0, "events": 0}
        self._on_steps: frozenset[int] = frozenset()  # Dialogs and tabs that the latest steps report.
        self._secret_fields: set[str] = set()
        # The names of the fields a view has shown. A stopped request's other fields (a hidden
        # token, a field no reading listed) are named in blocked_writes but not shown.
        self._shown_fields: set[str] = set()
        self._fields_page: tuple[str, int] | None = None
        self._write_source: Observation | None = None
        self._said = ""  # What the agent has sent since the page was opened.
        self._said_for_write = ""
        self._lock = threading.RLock()

    # ---- tools ------------------------------------------------------------------------------------
    @_sealed
    def observe(
        self,
        url: str | None = None,
        since: int | None = None,
        wait_for: WaitFor | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        observation_id: int | None = None,
        offset: int = 0,
        limit: int = DEFAULT_VIEW_LIMIT,
        find: str | None = None,
    ) -> dict[str, Any]:
        """Read the current page, or open ``url`` first and read it.

        A long view comes in slices of ``limit`` lines (200 by default, at most 1000). ``view_page``
        says which lines you have and ``next_offset``. To read more of a reading without reading the
        page again, pass its ``observation_id`` and an ``offset``. ``find`` keeps only the lines that
        contain some text, each with the heading it sits under; a matching heading brings its section,
        so ``find="lamp 250"`` shows that product and its buttons. Handles are those of the whole view.

        Returns ``view``: the page as text, one line per message, heading, value, table row,
        paragraph, or control. Ordinary text starts with ``text`` and keeps the complete wording,
        including inline formatting; paging and find include it. Each control line starts with a
        handle such as [7], then its kind, name, and state
        (value, [x] for checked, options, required, secret empty/filled/unknown, disabled).
        Secret state reveals only whether a value is present; unknown does not mean empty. A line that ends with
        ``blocked`` is a control you can read but not use now: something covers it, or the page has
        switched it off. The view then opens with ``blocked N controls: <why>``. ``form_tools`` describes
        only forms represented by form or control lines in this slice or its context, with their
        complete inputs. Page or find to discover other forms; names stay stable within the reading,
        and previously seen forms remain callable. ``observation_id`` names this reading; its handles
        stay valid for ``act``. With ``since`` (an earlier ``observation_id``, and no ``url``), ``changes`` lists what
        changed on the page since that reading, for a page that updates by itself.

        ``wait_for`` waits, up to ``timeout_ms``, until the page shows a change, compared with ``since``
        (or the latest reading, or the page as it first loads): ``{"change": "any"}``, ``{"change":
        "message"}``, ``{"text": "..."}``, ``{"gone": "Loading"}``, or ``{"control": "...",
        "enabled": true}``. ``wait.met`` says whether it showed.
        """

        with self._lock:
            paging = {"offset": offset, "limit": limit, "find": find}
            if observation_id is not None:
                if url is not None or since is not None or wait_for is not None:
                    return _error(
                        "BROWSER_INVALID_REQUEST",
                        "observation_id pages through a kept reading; it cannot be used with url, since, or wait_for.",
                        "Pass observation_id with offset, limit, or find; or leave it out to read the page again.",
                    )
                kept = self._observation(observation_id)
                if isinstance(kept, dict):
                    return kept
                data, note = self._view_data(kept, observation_id, **paging)
                data |= {"url": kept.url, "title": kept.title}
                return tool_response(True, _join(f"Reading {observation_id}, not read again.", note), data=data)
            try:
                condition = _condition(wait_for)
            except AgentActionError as error:
                return _planning_error(error)
            try:
                if url is not None:
                    if since is not None:
                        return _error(
                            "BROWSER_INVALID_REQUEST",
                            "since compares readings of the current page; it cannot be used with url.",
                            "Pass url to open a page, or since to see what changed on the current one.",
                        )
                    refused = self._refuse_origin(url)
                    if refused is not None:
                        return refused
                    session = self._browser()
                    session.open(url)
                    self._reported |= {"writes": 0, "passed": 0, "navigations": 0, "refusals": 0}
                    self._said = ""
                    limits = {"limits": [*self.boundary.describe(), self.boundary.privacy_description()]}
                    if condition is None:
                        return self._observe_result("Observed the page.", paging=paging) | limits
                    base = session._observe(locate=False)
                    waited = session.wait(condition, base, timeout_ms=timeout_ms)
                    return self._waited_result("Observed the page.", waited, since=base, paging=paging) | limits
                earlier = self._observation(since) if since is not None else None
                if isinstance(earlier, dict):
                    return earlier
                if self._session is None:
                    return _error("BROWSER_NO_PAGE", "No page is open.", "Call observe with a url first.")
                if condition is None:
                    return self._observe_result("Observed the page.", since=earlier, paging=paging)
                base = earlier or self._history[self._latest]
                waited = self._session.wait(condition, base, timeout_ms=timeout_ms)
                return self._waited_result("Observed the page.", waited, since=base, paging=paging)
            except BridgeError as error:
                return _error(_bridge_code(error), str(error), "Check the url, or observe again.")

    @_sealed
    @_judged
    def act(
        self,
        action: Literal["click", "fill", "edit", "check", "uncheck", "select", "scroll", "upload", "download"],
        handle: int | None = None,
        value: str | list[str] | dict[str, Any] | None = None,
        observation_id: int | None = None,
        wait_for: WaitFor | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        files: list[FileArg] | None = None,
    ) -> dict[str, Any]:
        """Do one thing to the control with this handle, then read the page again.

        ``edit`` applies one typed editor operation from the view: value is
        {"base_revision": "editor-...", "operations": [{"op": "append_paragraph", "text": "..."}]}.
        append_paragraph preserves the existing rich document and adds one plain-text paragraph.
        replace_block_text replaces one observed plain-text paragraph or heading while preserving
        its kind and all other blocks: {"op": "replace_block_text", "block": "block-...", "text": "..."}.
        Use the block ref from the view's blocks list (position is one-based document block order),
        with that editor's current base_revision. At most 32 eligible blocks are advertised.
        replace_text is available only for supported plain/source or empty rich editors. No Markdown
        import or automatic mode switching. Nested checklist controls use check/uncheck. The revision
        must come from the current view. Verification concerns the editor, not a persisted save.

        ``upload`` gives ``files`` to a file input, or to the file chooser that a button opens (an "Add
        photos" button whose file input is hidden). A file is ``{"name", "content"}`` (text, or base64
        with ``"encoding": "base64"``), or ``{"path"}`` inside the server's upload folder. The input's
        accepted types and whether it takes several files are checked. Results show file names and
        sizes, never contents.

        ``download`` clicks a link or button and saves the file it downloads in the server's download
        folder, under the name the page suggests (made safe; a name already there gets " (1)").
        ``steps[0].download`` gives ``file``, ``path``, ``bytes``, ``sha256``, and ``type``, never
        the contents. A link that downloads shows ``download`` in the view. A click with any other
        action that starts a download does not save it; ``steps[0].unsaved_downloads`` names it.

        ``scroll`` scrolls the page (no handle), or the scrollable area that holds the control with
        this handle (a side pane, a feed in a box). ``value`` is ``end`` (the default; where an infinite
        list loads more), ``down``, ``up``, or ``top``. After each scroll it waits for what loads.
        With ``wait_for``, it keeps scrolling until that shows, the list ends, or ``timeout_ms`` ends:
        ``act(action="scroll", wait_for={"text": "Post 75"})``. ``scroll`` in the result says how many
        scrolls it took, whether more loaded, and whether the end was reached.

        ``fill`` takes text in ``value``, or ``{"secret": "NAME"}`` for a secret the server holds, which
        goes only into a field the view marks secret. ``select`` takes an option value or label (a list for a
        multiple select). On a custom combobox or listbox, ``select`` chooses from its list: it opens
        the list (typing the value into an editable combobox), waits for the option, and clicks the one
        whose name is the value, or starts with it; ``steps[0].chosen`` names it. Query text is not
        selection evidence. Create or unknown actions keep explicit handles and are never an
        implicit fallback for select. When marked "select adds", selection adds one existing item
        and preserves the others. A failed verification can follow a completed activation: inspect
        current state and writes before another mutation. On a date field,
        ``select`` takes a date and picks it from the field's calendar. On a field marked ``range``, or
        on either field of a pair marked ``(range start)`` and ``(range end)``, it takes a range:
        ``{"start": "2026-11-20", "end": "2026-11-23"}``, ``["2026-11-20", "2026-11-23"]``, or
        ``"2026-11-20/2026-11-23"``; both days are picked in one step. ``check`` and
        ``uncheck`` take no value. The handle comes from the latest
        observation unless ``observation_id`` names an earlier one.

        A page that answers after a delay shows nothing at first. ``wait_for`` waits, up to
        ``timeout_ms``, for ``{"change": "any" | "message" | "navigation"}``, ``{"text": "..."}``,
        ``{"gone": "..."}``, or ``{"control": "...", "enabled": true}``. The action is done either way;
        ``wait.met`` says whether the change showed. Do not repeat the action only because it is false.

        ``outcome`` says whether the action took effect. ``status`` is ``succeeded`` (the wait was
        met or the page showed an effect), ``blocked`` (a write was stopped by policy), ``failed``
        (refused or the browser would not send the form), or ``uncertain`` (it ran and nothing shows
        whether it worked, or what you waited for did not show). Dismissing a ``confirm`` or ``prompt``
        does not prove the action was canceled: without other evidence it is ``uncertain`` and
        ``may_repeat``; an observed effect still counts. ``reason`` is one line. ``retry`` is
        ``not_needed``, ``safe``, or ``may_repeat`` (repeating it could do the thing twice). When
        uncertain, ``page`` is ``busy`` (a request the action started is still in flight: wait),
        ``reacted``, or ``quiet`` (no DOM change and no request: it may have done nothing).
        ``steps[0].dialogs`` lists the boxes the page raised and their answers,
        ``steps[0].opened`` the tabs it opened (they are closed, not followed), and
        ``steps[0].reaction`` how much the page did. In ``changes``, ``(shown again)`` marks a message
        the page repeated, and ``(shown, now gone)`` one that came and went.
        """

        with self._lock:
            observation = self._observation(observation_id)
            if isinstance(observation, dict):
                return observation
            try:
                condition = _condition(wait_for)
            except AgentActionError as error:
                return _planning_error(error)
            if action == "scroll":
                return self._scroll(observation, handle, value, condition, timeout_ms)
            if handle is None:
                return _error(
                    "BROWSER_HANDLE_REQUIRED",
                    f"{action} needs the handle of a control.",
                    "Pass a handle from the view; only scroll works without one.",
                )
            if handle not in observation.handles:
                return _error(
                    "BROWSER_HANDLE_UNKNOWN",
                    f"Observation {self._id_of(observation)} has no control [{handle}].",
                    "Use a handle from the view of that observation.",
                )
            if action == "download" and self.download_dir is None:
                return _error(
                    "BROWSER_DOWNLOAD_NOT_ALLOWED",
                    "This server has no download folder, so it saves no files.",
                    "Start the server with --download-dir to let the agent download files.",
                )
            try:
                if action == "upload":
                    steps = plan_control_action(observation.handles[handle], "upload", self._files(files or []))
                else:
                    control = observation.handles[handle]
                    value = self._secret(value, control if action == "fill" else None, f"{action} on {control.name!r}")
                    steps = plan_control_action(control, action, value, lift=observation.lift)
            except AgentActionError as error:
                return _planning_error(error)
            except _ToolError as error:
                return error.result
            return self._run(steps, observation, condition, timeout_ms)

    @_sealed
    @_judged
    def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        submit: bool = True,
        observation_id: int | None = None,
        wait_for: WaitFor | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        submitter: int | None = None,
    ) -> dict[str, Any]:
        """Fill in a form with ``arguments`` and submit it, then read the page again.

        ``tool`` is a name from ``form_tools``; ``arguments`` follows its ``input_schema``. Arguments you
        leave out preserve their live semantic field values; hidden protocol fields remain app-owned. Explicitly send an empty
        string (``""``) to clear an editable text/password field; omission does not clear it.
        ``x-value-state`` is empty, filled, or unknown; unknown does not mean empty.
        With multiple submit actions, supply ``submitter`` as the button handle from this observation.
        A unique submitter can start disabled: fields are prepared before readiness is checked.
        Preparation guards native submits, finalizes the last field and focuses the chosen button
        before waiting for validation. Autosaves can still occur; this is not a transaction.
        Before automatic submission, known pending requests and scoped busy states must settle within
        the remaining ``timeout_ms`` budget; otherwise submission is withheld with BROWSER_FORM_PENDING.
        This checks current UI prerequisites, not arbitrary future application work or persistence.
        ``submit=false`` only fills the form. Every problem
        with the arguments is reported at once, before the browser does anything. ``wait_for`` and
        ``timeout_ms`` work as for ``act``, for example ``{"text": "Order placed"}``, and so does
        ``outcome``: ``failed`` when the browser would not send the form.
        """

        with self._lock:
            observation = self._observation(observation_id)
            if isinstance(observation, dict):
                return observation
            try:
                condition = _condition(wait_for)
                tool, arguments = self._resolve_public_call(observation, tool, arguments)
                arguments = self._with_secrets(observation, tool, self._with_files(observation, tool, arguments))
                chosen = observation.handles.get(submitter) if type(submitter) is int else None
                if submitter is not None and chosen is None:
                    raise AgentActionError(
                        "AGENT_FORM_SUBMITTER_INVALID", "Use a submitter handle from this observation."
                    )
                steps = plan_form_call(
                    observation.lift, tool, arguments, submit=submit, submitter=chosen.node_id if chosen else None
                )
            except AgentActionError as error:
                return _planning_error(error)
            except _ToolError as error:
                return error.result
            return self._run(steps, observation, condition, timeout_ms)

    # ---- files ------------------------------------------------------------------------------------
    def _files(self, items: list[Any]) -> list[Any]:
        """Files to upload, with each ``path`` read. Other items are checked when the step is planned."""

        return [self._read_path(item) if isinstance(item, dict) and "path" in item else item for item in items]

    def _read_path(self, item: dict[str, Any]) -> UploadFile:
        """Read a file by its path. A path is read only from inside the upload folder, never from elsewhere."""

        if self.upload_dir is None:
            raise _ToolError(
                "BROWSER_UPLOAD_NOT_ALLOWED",
                "This server has no upload folder, so it reads no files by path.",
                "Send the file's content instead, or start the server with --upload-dir.",
            )
        # resolve() follows links, so a link inside the folder to a file outside it is refused too.
        target = (self.upload_dir / str(item["path"])).resolve()
        if not target.is_relative_to(self.upload_dir):
            raise _ToolError(
                "BROWSER_UPLOAD_NOT_ALLOWED",
                f"{item['path']!r} is outside the upload folder.",
                "Use a path inside the upload folder.",
            )
        if not target.is_file():
            raise _ToolError(
                "BROWSER_UPLOAD_NOT_FOUND", f"No file {item['path']!r} in the upload folder.", "Check the name."
            )
        if target.stat().st_size > MAX_UPLOAD_BYTES:
            raise _ToolError(
                "BROWSER_UPLOAD_TOO_LARGE", f"{item['path']!r} is larger than 10 MB.", "Choose a smaller file."
            )
        named = UploadFile.named(item.get("name") or target.name, target.read_bytes())
        return UploadFile(named.name, named.data, item.get("mime_type") or named.mime_type)

    def _with_files(self, observation: Observation, tool: str, arguments: Any) -> Any:
        """The arguments of a form call, with the ``path`` files of its file fields read."""

        names = form_tool_names(observation.lift.forms)
        if tool not in names or not isinstance(arguments, dict):
            return arguments
        form = observation.lift.forms[names.index(tool)]
        controls = {control.node_id: control for control in observation.lift.controls}
        keys = {field.key for field in form_fields(form, controls) if field.kind == "file"}
        resolved = dict(arguments)
        for key in keys & set(arguments):
            value = arguments[key]
            if isinstance(value, list):
                resolved[key] = self._files(value)
            elif isinstance(value, dict):
                resolved[key] = self._files([value])
        return resolved

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    # ---- internals --------------------------------------------------------------------------------
    def _browser(self) -> BrowserSession:
        if self._session is None:
            self._session = BrowserSession(boundary=self.boundary, download_dir=self.download_dir, privacy=self.privacy)
        return self._session

    def _resolve_public_call(self, observation: Observation, tool: str, arguments: Any) -> tuple[str, Any]:
        """Map projected schema names to real field names without changing the private lift."""
        for schema, shown, aliases in self._schema_pairs(observation):
            if tool not in {schema["name"], shown["name"]}:
                continue
            if not isinstance(arguments, dict):
                return schema["name"], arguments
            return schema["name"], {aliases.get(key, key): value for key, value in arguments.items()}
        return tool, arguments

    def _schema_names(self, observation: Observation) -> list[tuple[str, str]]:
        """Assign names against the whole reading, so paging cannot renumber duplicate names or aliases."""
        names = form_tool_names(observation.lift.forms)
        reserved = set(names)
        result = []
        for number, name in enumerate(names, 1):
            public = name if self.privacy.clean(name) == name else self._schema_alias("f", number, reserved)
            reserved.add(public)
            result.append((name, public))
        return result

    def _schema_pairs(
        self, observation: Observation, *, form_ids: frozenset[str] | None = None
    ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, str]]]:
        result = []
        controls = {control.node_id: control for control in observation.lift.controls}
        for form, (name, public) in zip(observation.lift.forms, self._schema_names(observation), strict=True):
            if form_ids is not None and form.node_id not in form_ids:
                continue
            schema = form_tool_schema(form, controls, name=name)
            safe_schema = _public_schema(schema)
            shown = self.privacy.clean(safe_schema)
            shown["name"] = public
            shown["submitters"] = [
                handle for handle, control in observation.handles.items() if control.node_id in form.submit_ids
            ]
            properties = safe_schema.get("input_schema", {}).get("properties", {})
            reserved_fields = set(properties)
            names = {}
            for index, name in enumerate(properties, 1):
                public = name if self.privacy.clean(name) == name else self._schema_alias("p", index, reserved_fields)
                names[name] = public
                reserved_fields.add(public)
            shown["input_schema"]["properties"] = {
                names[name]: self.privacy.clean(properties[name]) for name in properties
            }
            if "required" in shown["input_schema"]:
                shown["input_schema"]["required"] = [names[name] for name in schema["input_schema"]["required"]]
            result.append((schema, shown, {public: raw for raw, public in names.items()}))
        return result

    def _schema_alias(self, kind: str, number: int, reserved: set[str]) -> str:
        # Opaque stable identifiers carry no bytes or digest of the original field name.
        for salt in range(4096):
            alias = kind + hashlib.sha256(f"{kind}:{number}:{salt}".encode()).hexdigest()[:16]
            if alias not in reserved and self.privacy.clean(alias) == alias:
                return alias
        raise BoundaryError("Cannot project this form's identifiers safely; start a new session.")

    def _refuse_origin(self, url: str) -> dict[str, Any] | None:
        refusal = self.boundary.refusal(url)
        if refusal is None:
            return None
        code, message = refusal
        allowed = (
            f"a page on one of: {', '.join(self.allowed_origins)}" if self.allowed_origins else "a public web page"
        )
        return _error(code, message, f"Open {allowed}.")

    def _secret(self, value: Any, control: Any, what: str) -> Any:
        """The value to type for ``value``. ``{"secret": NAME}`` stands for a secret this server was
        started with: the agent names it and never holds it. It goes only into a field the lift
        reads as secret, whose value no view shows, so naming it cannot bring it into a reading."""

        if not (isinstance(value, dict) and set(value) == {"secret"}):
            return value
        name = value["secret"]
        if name not in self.boundary.secrets:
            known = ", ".join(self.boundary.secrets) or "none"
            raise _ToolError(
                "BROWSER_SECRET_REFUSED", f"This server holds no secret named {name!r}.", f"Secrets it holds: {known}."
            )
        if control is None or not control.secret:
            raise _ToolError(
                "BROWSER_SECRET_REFUSED",
                f"{what} is not a secret field, so secret {name} is not typed into it: its value would show.",
                "Name a secret only for a password field or another field the view marks secret.",
            )
        return self.boundary.secrets[name]

    def _with_secrets(self, observation: Observation, tool: str, arguments: Any) -> Any:
        """The arguments of a form call, with each ``{"secret": NAME}`` replaced by its value."""

        names = form_tool_names(observation.lift.forms)
        if tool not in names or not isinstance(arguments, dict):
            return arguments
        form = observation.lift.forms[names.index(tool)]
        controls = {control.node_id: control for control in observation.lift.controls}
        fields = {field.key: field.control for field in form_fields(form, controls)}
        return {key: self._secret(value, fields.get(key), f"{key!r}") for key, value in arguments.items()}

    def _observation(self, observation_id: int | None) -> Observation | dict[str, Any]:
        if self._session is None or not self._history:
            return _error("BROWSER_NO_PAGE", "No page is open.", "Call observe with a url first.")
        key = self._latest if observation_id is None else observation_id
        if key not in self._history:
            return _error(
                "BROWSER_OBSERVATION_UNKNOWN",
                f"Observation {key} is not kept; the server keeps the last {HISTORY}.",
                "Call observe and use the new handles.",
            )
        return self._history[key]

    def _id_of(self, observation: Observation) -> int:
        return next(key for key, value in self._history.items() if value is observation)

    def _scroll(
        self,
        observation: Observation,
        handle: int | None,
        value: Any,
        condition: ChangeCondition | None,
        timeout_ms: int,
    ) -> dict[str, Any]:
        if handle is not None and handle not in observation.handles:
            return _error(
                "BROWSER_HANDLE_UNKNOWN",
                f"Observation {self._id_of(observation)} has no control [{handle}].",
                "Use a handle from the view of that observation, or leave it out to scroll the page.",
            )
        try:
            step = plan_scroll("end" if value is None else value, observation.handles[handle] if handle else None)
        except AgentActionError as error:
            return _planning_error(error)
        session = self._browser()
        try:
            scrolled = session.scroll(step, observation=observation, until=condition, timeout_ms=timeout_ms)
        except BridgeError as error:
            return _error(_bridge_code(error), str(error), "Observe the page, then decide.")
        data = self._observe_data(since=observation)
        result = tool_response(True, _join(_scroll_summary(scrolled), data.pop("_note")), data=data)
        result["scroll"] = scrolled.to_json()
        result["outcome"] = outcome_of(result) if result.get("blocked_writes") else scroll_outcome(result["scroll"])
        return result

    def _run(
        self,
        steps: tuple[ActionStep, ...],
        observation: Observation,
        condition: ChangeCondition | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        session = self._browser()
        started = time.monotonic()
        self._write_source = observation
        # What the agent itself sends: the values it set and the files it gave, on this page so far.
        # A stopped request may show these back to it (see _field_shown).
        self._said += "\n" + "\n".join(
            str(item)
            for step in steps
            if not step.secret
            for item in [step.value, *step.values, *(file.name for file in step.files)]
            if item
        )
        self._said_for_write = self._said
        try:
            results = (
                session.run(steps, observation=observation, timeout_ms=timeout_ms)
                if steps or isinstance(steps, FormPlan)
                else []
            )
        except BridgeError as error:
            # Some steps may have run; show the agent the page as it is now, if it can be read.
            self._on_steps = frozenset(seq for item in error.steps for seq in item.event_seqs)
            try:
                current: dict[str, Any] | None = self._observe_data(since=observation)
                current.pop("_note")
            except BridgeError:
                current = None
            code = "BROWSER_OBSERVATION_FAILED" if error.observation_failed else _bridge_code(error)
            refused = tool_error_response(code, str(error), _bridge_fix(code), data=current)
            if error.steps:
                refused["steps"] = [item.to_json() for item in error.steps]  # The ones that ran.
            refused["progress"] = self._progress(steps, error.steps, observation, error)
            if session.action_progress:
                refused["progress"]["writes"] = dict(session.action_progress["writes"])
                refused["progress"]["submission"] = session.action_progress["submission"]
                for key in ("contract", "preparation"):
                    if key in session.action_progress:
                        refused["progress"][key] = session.action_progress[key]
            return refused
        self._on_steps = frozenset(seq for item in results for seq in item.event_seqs)
        self._said += "".join(f"\n{item.chosen}" for item in results if item.chosen and not item.step.secret)
        self._said_for_write = self._said
        summary = "Nothing to do: the control is already in that state." if not steps else _summary(results)
        try:
            if condition is None:
                result = self._observe_result(summary, since=observation)
            else:
                waited = session.wait(
                    condition, observation, timeout_ms=max(0, timeout_ms - int((time.monotonic() - started) * 1000))
                )
                result = self._waited_result(summary, waited, since=observation, acted=bool(steps))
        except BridgeError as error:
            result = _error(
                "BROWSER_OBSERVATION_FAILED",
                str(error),
                "Observe the page; do not repeat the action to recover its result.",
            )
        result["steps"] = [item.to_json() for item in results]
        result["progress"] = self._progress(steps, results, observation)
        if session.action_progress:
            result["progress"]["writes"] = dict(session.action_progress["writes"])
            result["progress"]["submission"] = session.action_progress["submission"]
            for key in ("contract", "preparation"):
                if key in session.action_progress:
                    result["progress"][key] = session.action_progress[key]
        result["navigated"] = any(item.navigated for item in results)
        result["outcome"] = outcome_of(result, self._activity(results, observation, started))
        return result

    @staticmethod
    def _progress(steps, results, observation, error=None):
        """Report partial field application without promising a form transaction or rollback."""
        controls = {control.node_id: control for control in observation.lift.controls}
        fields = []
        submitted = False
        requested = False
        for index, step in enumerate(steps):
            result = results[index] if index < len(results) else None
            failed = error is not None and error.failed_step == step
            status = (
                "failed"
                if failed
                else "verified"
                if result and result.verified is True
                else "applied"
                if result
                else "not_run"
            )
            entry = {"name": step.name, "status": status}
            selection = result.selection if result else error.selection if failed else None
            if selection:
                entry["selection"] = selection
            fields.append(entry)
            control = controls.get(step.node_id)
            if step.op == "submit" or (control and control.submits):
                requested = True
                submitted |= result is not None or failed
        return {
            "fields": fields,
            "submission": "attempted" if submitted else "not_run" if requested else "not_requested",
        }

    def _activity(self, results: list[StepResult], before: Observation, started: float) -> dict[str, Any] | None:
        """What the page did from the first step to the reading just made: DOM mutations (None on a
        new document, where the count began again), requests started, and those still in flight."""

        if not results:
            return None
        first, now = results[0], self._history[self._latest]
        counted = now.doc_id == before.doc_id and first.mutation_mark is not None
        return {
            "mutations": now.activity["mutations"] - first.mutation_mark if counted else None,
            "requests": now.activity["requests"] - first.request_mark,
            "pending": sum(1 for seq in now.activity["pending"] if seq > first.request_mark),
            "watched_ms": int((time.monotonic() - started) * 1000),
        }

    def _observe_result(
        self, summary: str, *, since: Observation | None = None, paging: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = self._observe_data(since=since, paging=paging)
        if data.get("blocked_navigations"):
            stopped = data["blocked_navigations"][0]
            summary = f"Navigation blocked: {stopped['reason']}."
        return tool_response(True, _join(summary, data.pop("_note")), data=data)

    def _view_data(
        self,
        observation: Observation,
        observation_id: int,
        *,
        offset: int = 0,
        limit: int = DEFAULT_VIEW_LIMIT,
        find: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """A slice of a reading's view, and one sentence on how to get more."""

        shown = self._browser().project(observation)
        form_names = {
            form.node_id: public
            for form, (_, public) in zip(observation.lift.forms, self._schema_names(observation), strict=True)
        }
        lines = [
            replace(line, text=line.text.rsplit(" tool=", 1)[0] + " tool=" + form_names[line.node_id])
            if line.kind == "form"
            else line
            for line in shown.lines
        ]
        page = view_page(lines, offset=offset, limit=max(1, min(int(limit), MAX_VIEW_LIMIT)), find=find or None)
        schemas = self._schema_pairs(observation, form_ids=page.form_ids)
        data = {
            "observation_id": observation_id,
            "view": page.text,
            "view_page": page.to_json(),
            "form_tools": [pair[1] for pair in schemas],
            "readiness": {
                "version": 1,
                "scope": "observation",
                "state": "pending"
                if (
                    observation.activity.get("network", {}).get("finite", 0)
                    + observation.activity.get("network", {}).get(
                        "unknown", len(observation.activity.get("pending", []))
                    )
                )
                else "observed",
                "pending": observation.activity.get(
                    "network", {"finite": 0, "streams": 0, "unknown": len(observation.activity.get("pending", []))}
                ),
            },
        }
        return data, page.describe(observation_id)

    def _waited_result(
        self,
        summary: str,
        waited: WaitResult,
        *,
        since: Observation,
        acted: bool = False,
        paging: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A result after a wait: the view, the changes since ``since``, and whether the change showed."""

        if waited.met:
            note = f" Waited {waited.waited_ms} ms for {waited.condition.describe()}: it showed."
        else:
            advice = "The action is done; read the view before you act again." if acted else "Read the view."
            note = f" Waited {waited.timeout_ms} ms for {waited.condition.describe()}: it did not show. {advice}"
        data = self._observe_data(since=since, paging=paging)
        result = tool_response(True, _join(summary + note, data.pop("_note")), data=data)
        result["wait"] = waited.to_json()
        return result

    def _observe_data(
        self, *, since: Observation | None = None, paging: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Read the page, keep the reading, and describe it. ``since`` adds what changed from it.

        The view is a slice (``paging``: offset, limit, find); ``_note`` says how to get more.
        """

        session = self._browser()
        observation = session._observe()
        page_key = (observation.doc_id, observation.navigation_id)
        if page_key != self._fields_page:
            self._secret_fields.clear()
            self._shown_fields.clear()
            self._said = ""
            self._fields_page = page_key
        self._latest += 1
        self._history[self._latest] = observation
        for key in sorted(self._history)[:-HISTORY]:
            del self._history[key]
        self._secret_fields = {c.field_name for c in observation.lift.controls if c.secret and c.field_name}
        self._shown_fields = {c.field_name for c in observation.lift.controls if c.field_name and not c.secret}
        view, note = self._view_data(observation, self._latest, **(paging or {}))
        data: dict[str, Any] = {"url": observation.url, "title": observation.title, **view, "_note": note}
        if since is not None:
            data["changes"] = changes(since, observation, self.privacy)
        records = session._records()
        writes, navigations = records["writes"], records["navigations"]
        if len(writes) > self._reported["writes"]:
            source = self._write_source
            readings = (observation,) if source is None else (source, observation)
            visible: dict[str, set[str]] = {}
            secret_names = set(self._secret_fields)
            for reading in readings:
                for control in reading.lift.controls:
                    if not control.field_name:
                        continue
                    if control.secret:
                        secret_names.add(control.field_name)
                    else:
                        values = visible.setdefault(control.field_name, set())
                        values.update(
                            value for value in [control.value, *(option.value for option in control.options)] if value
                        )
            # Agent-supplied values remain known during this action's response even if it navigated.
            said = self._said_for_write if source is not None else self._said
            seen = "\n".join([*(reading.view for reading in readings), said])
            data["blocked_writes"] = [
                self._redact_write(w, seen, visible=visible, secret_names=secret_names)
                for w in writes[self._reported["writes"] :]
            ]
        self._write_source = None
        passed = records.get("passed_writes", [])
        if len(passed) > self._reported["passed"]:
            data["passed_writes"] = [session._request_summary(item) for item in passed[self._reported["passed"] :]]
        if len(navigations) > self._reported["navigations"]:
            data["blocked_navigations"] = navigations[self._reported["navigations"] :]
        refused = [item for item in records.get("refusals", []) if item["seq"] > self._reported.get("refusals", 0)]
        if refused:
            data["blocked_requests"] = _by_host(refused)
        # Dialogs the page raised and tabs it opened that no step reports: they came while the
        # bridge waited, or between two tool calls (a timer, a late answer).
        events = [*records["dialogs"], *records["popups"]]
        unreported = {e["seq"] for e in events if e["seq"] > self._reported["events"]} - self._on_steps
        late_dialogs = [dialog_json(item) for item in records["dialogs"] if item["seq"] in unreported]
        late_tabs = [item["url"] for item in records["popups"] if item["seq"] in unreported]
        if late_dialogs:
            data["dialogs"] = late_dialogs
        if late_tabs:
            data["opened"] = late_tabs
        self._reported = {
            "writes": len(writes),
            "passed": len(passed),
            "navigations": len(navigations),
            "events": max([self._reported["events"], *(e["seq"] for e in events)]),
            "refusals": max([self._reported.get("refusals", 0), *(item["seq"] for item in refused)]),
        }
        return data

    def _field_shown(
        self,
        key: str,
        value: Any,
        seen: str,
        *,
        visible: dict[str, set[str]] | None = None,
        secret_names: set[str] | None = None,
    ) -> Any:
        """A field of a stopped request as a result may show it. The rule is what a person at the
        page could see: the value of a field a view listed, or a value the page itself shows (a
        custom list keeps its choice in a hidden field, and shows the choice beside it), or a value
        the agent itself sent on this page. A secret field is ``[redacted]``. Anything else, such as
        a hidden token, is ``[not shown]``, whatever the field is called. ``seen`` is the text of the
        views around the request and of what the agent sent."""

        if key in (self._secret_fields if secret_names is None else secret_names) or names_a_secret(key):
            return REDACTED
        if isinstance(value, str) and value in (visible or {}).get(key, set()):
            return value
        if isinstance(value, list):
            return [self._field_shown(key, item, seen, visible=visible, secret_names=secret_names) for item in value]
        # A file is shown as its name, size, and type, when the agent gave a file of that name.
        text = value.get("file") if isinstance(value, dict) else value
        return value if isinstance(text, str) and text.strip() and text in seen else NOT_SHOWN

    def _redact_write(
        self,
        write: dict[str, Any],
        seen: str = "",
        *,
        visible: dict[str, set[str]] | None = None,
        secret_names: set[str] | None = None,
    ) -> dict[str, Any]:
        """A blocked request, with secret form fields redacted and fields that no view showed left
        out by value. A body that is not a form is not shown."""

        shown: dict[str, Any] = {"method": write["method"], "url": write["url"]}
        if "state" in write:
            shown["state"] = write["state"]
        if write.get("new_tab"):
            shown["new_tab"] = True  # A tab the page opened sent it, not the page the agent reads.
        fields = request_fields(write) if self.privacy.mode == "heuristic" else None
        if fields:
            shown["form_fields"] = {
                key: self._field_shown(key, value, seen, visible=visible, secret_names=secret_names)
                for key, value in fields.items()
            }
        elif write.get("post_data") or write.get("post_data_b64"):
            shown["body_bytes"] = (
                len(base64.b64decode(write["post_data_b64"]))
                if write.get("post_data_b64")
                else len((write.get("post_data") or "").encode("utf-8"))
            )
        return shown


def _by_host(refused: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Requests the boundary stopped, one entry for each place they were for: a page that asks ten
    times for the same tracker is one line, not ten."""

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in refused:
        parts = urlsplit(item["url"] if "//" in item["url"] else f"//{item['url']}")
        key = (parts.netloc or item["url"], item["reason"])
        entry = grouped.setdefault(key, {"host": key[0], "reason": item["reason"], "count": 0, "first": item["url"]})
        entry["count"] += 1
    return list(grouped.values())[:20]


def _scroll_summary(scrolled: ScrollResult) -> str:
    if scrolled.scrolls == 0:
        return "It already shows on the page; no scroll was needed."
    area = "the page" if scrolled.page else repr(scrolled.step.name)
    times = "once" if scrolled.scrolls == 1 else f"{scrolled.scrolls} times"
    parts = [f"Scrolled {area} {times} ({scrolled.step.value})."]
    if not scrolled.moved:
        tip = " To scroll a pane, pass the handle of a control inside it." if scrolled.page else ""
        parts.append("It did not move." + tip)
    elif scrolled.loaded:
        parts.append("More content loaded.")
    else:
        parts.append("Nothing new loaded.")
    if scrolled.end_reached:
        parts.append("It is at the end of the list.")
    if scrolled.met is True:
        parts.append("The page now shows what you waited for.")
    elif scrolled.met is False:
        parts.append("What you waited for did not show.")
    return " ".join(parts)


def _summary(results: list[StepResult]) -> str:
    parts = [
        f"{item.step.op} {item.step.name!r}" + (f" and {item.step.end_name!r}" if item.step.end_name else "")
        for item in results
    ]
    tail = " The page navigated." if any(item.navigated for item in results) else ""
    for item in results:
        if item.download is not None:
            saved = item.download
            tail += f" Saved {saved['file']} ({saved['bytes']} bytes, {saved['type']}) in the download folder."
        if item.unsaved_downloads:
            names = ", ".join(item.unsaved_downloads)
            tail += f" It started a download ({names}) that was not saved; use action='download' to save it."
        if item.invalid:
            reasons = "; ".join(f"{field['name'] or 'a field'}: {field['message']}" for field in item.invalid)
            tail += f" The browser did not send the form: {reasons}"
        for dialog in item.dialogs:
            tail += f" The page raised a {dialog['type']} box, {dialog['message']!r}; it was {dialog['answer']}."
        if item.opened:
            urls = ", ".join(tab["url"] for tab in item.opened)
            tail += f" It opened a new tab ({urls}), which was closed and not followed."
    return f"Did {len(results)} step(s): {'; '.join(parts)}.{tail}"


def _public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep public value-state metadata; leave out internal ``x-`` keys such as node ids."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip(item)
                for key, item in value.items()
                if key in {"x-value-state", "x-editor"} or not str(key).startswith("x-")
            }
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return {key: strip(schema[key]) for key in ("name", "description", "input_schema")}


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _condition(wait_for: WaitFor | dict[str, Any] | None) -> ChangeCondition | None:
    return None if wait_for is None else ChangeCondition.from_json(dict(wait_for))


def _error(code: str, message: str, fix: str) -> dict[str, Any]:
    return tool_error_response(code, message, fix)


def _planning_error(error: AgentActionError) -> dict[str, Any]:
    """A request the page cannot take. Nothing was done in the browser."""

    fixes = {
        "unknown": "Remove it; the tool's input_schema lists the arguments.",
        "missing": "Add it; the page shows this required field empty.",
        "invalid": "Use a value that the tool's input_schema allows.",
    }
    errors = [
        {
            "code": error.code,
            "argument": item["argument"],
            "problem": item["problem"],
            "message": item["message"],
            "fix": fixes.get(item["problem"], "Fix the argument and retry."),
        }
        for item in error.details
    ] or [{"code": error.code, "message": error.message, "fix": "Change the request and retry."}]
    return tool_response(
        False,
        f"{error.message} Nothing was done in the browser.",
        errors=errors,
        next_actions=["Fix the request and retry."],
    )


def _bridge_code(error: BridgeError) -> str:
    message = str(error)
    if message.startswith("form-mismatch:"):
        return "BROWSER_FORM_MISMATCH"
    if message.startswith("form-unknown:"):
        return "BROWSER_FORM_UNKNOWN"
    if message.startswith("form-pending:"):
        return "BROWSER_FORM_PENDING"
    if message.startswith("stale-view:"):
        return "BROWSER_OBSERVATION_STALE"
    if message.startswith("boundary-origin:"):
        return "BROWSER_ORIGIN_NOT_ALLOWED"
    if message.startswith("boundary:"):
        return "BROWSER_ADDRESS_NOT_ALLOWED"
    if message.startswith("choose: more than one"):
        return "BROWSER_OPTION_AMBIGUOUS"
    if message.startswith("choose: no option"):
        return "BROWSER_OPTION_NOT_FOUND"
    if message.startswith(("choose_date:", "choose_date_range:")) and "not available" in message:
        return "BROWSER_DATE_UNAVAILABLE"
    if message.startswith(("choose_date:", "choose_date_range:")):
        return "BROWSER_DATE_NOT_PICKED"
    if message.startswith("upload:"):
        return "BROWSER_UPLOAD_REFUSED"
    if message.startswith("download: no download started"):
        return "BROWSER_DOWNLOAD_NONE"
    if message.startswith("download:") and "larger than" in message:
        return "BROWSER_DOWNLOAD_TOO_LARGE"
    if message.startswith("download: this session has no download folder"):
        return "BROWSER_DOWNLOAD_NOT_ALLOWED"
    if message.startswith("download:"):
        return "BROWSER_DOWNLOAD_FAILED"
    if message.startswith("drift:"):
        return "BROWSER_TARGET_DRIFT"
    if message.startswith("ambiguous:"):
        return "BROWSER_TARGET_AMBIGUOUS"
    if message.startswith("blocked:"):
        return "BROWSER_TARGET_BLOCKED"
    if message.startswith("stale:"):
        return "BROWSER_TARGET_STALE"
    if "did not run" in message or message.startswith("navigated:"):
        return "BROWSER_PLAN_INTERRUPTED"
    if "Timeout" in message or "timeout" in message:
        return "BROWSER_TIMEOUT"
    return "BROWSER_ERROR"


# What to do about a step the bridge refused because of the control it was aimed at. The view that
# comes back with the error is the page as it is now. "This step" and not "nothing": in a form call,
# the steps before the refused one have run.
_TARGET_FIXES = {
    "BROWSER_FORM_PENDING": (
        "Submission was withheld while preparatory work remained unresolved. Observe or wait for the form's condition; check prior writes before repeating the call."
    ),
    "BROWSER_FORM_MISMATCH": (
        "Requested state was not verified. Earlier actions may already have taken effect. "
        "Inspect the current selection and recorded writes before another mutation; do not repeat blindly."
    ),
    "BROWSER_FORM_UNKNOWN": (
        "Selection evidence is incomplete. An activation may already have taken effect. "
        "Inspect the current state before another mutation; do not repeat blindly."
    ),
    "BROWSER_TARGET_DRIFT": (
        "The page changed under this view, and this step was not run. Find the control in the new view "
        "and use its handle."
    ),
    "BROWSER_TARGET_AMBIGUOUS": (
        "This step was not run. Pick the control you mean in the new view, by the lines around it, and use its handle."
    ),
    "BROWSER_TARGET_BLOCKED": (
        "This step was not run. Deal with what is in the way first (close or answer it; its controls "
        "are the ones not marked blocked), then act again."
    ),
}


def _bridge_fix(code: str) -> str:
    return _TARGET_FIXES.get(code, "Read the new view, then act again with its handles.")


def build_server(tools: BrowserTools) -> Any:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, log_level="ERROR")
    changes_world = tools.boundary.sends_writes or tools.boundary.read_policy == "permissive"
    app.add_tool(
        tools.observe,
        annotations=ToolAnnotations(title="Observe a page", readOnlyHint=False, openWorldHint=True),
    )
    app.add_tool(
        tools.act,
        annotations=ToolAnnotations(title="Act on a control", destructiveHint=changes_world, openWorldHint=True),
    )
    app.add_tool(
        tools.call,
        annotations=ToolAnnotations(title="Submit a form", destructiveHint=changes_world, openWorldHint=True),
    )
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="an origin the agent may open, such as https://shop.example (repeatable). With one, the session "
        "is sealed: the browser reaches the listed origins and hosts and nothing else",
    )
    parser.add_argument(
        "--public-read",
        action="store_true",
        help="explicitly allow browsing public web origins without operator credentials or writes",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST",
        help="a host, with a port if it needs one, that pages may load from but the agent may not open: "
        "a CDN (repeatable; needs --allow-origin)",
    )
    parser.add_argument(
        "--allow-write",
        action="append",
        default=[],
        metavar="RULE",
        help="a write that is sent: a method and an address with * for any run of characters, such as "
        "'POST https://shop.example/cart/*'. Methods include GET, POST, PUT, PATCH, DELETE, WEBSOCKET, EVENTSOURCE and * (repeatable; "
        "needs --allow-origin)",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="send every write to the listed origins (needs --allow-origin; prefer --allow-write rules)",
    )
    parser.add_argument(
        "--allow-read",
        action="append",
        default=[],
        metavar="PATTERN",
        help="operator-classified read URL pattern for GET, HEAD and OPTIONS (repeatable)",
    )
    parser.add_argument(
        "--mutation-url",
        action="append",
        default=[],
        metavar="PATTERN",
        help="URL pattern that changes state even on GET; needs a matching --allow-write (repeatable)",
    )
    parser.add_argument(
        "--read-policy",
        choices=["auto", "explicit", "permissive"],
        default="auto",
        help="auto requires classified reads in origin-scoped trials; permissive trusts unclassified GETs",
    )
    parser.add_argument(
        "--deny-url",
        action="append",
        default=[],
        metavar="PATTERN",
        help="an address that is never requested, by any method, with * for any run of characters: "
        "'*/logout*' (repeatable). The limit for a site that changes data on GET",
    )
    parser.add_argument(
        "--secret",
        action="append",
        default=[],
        metavar="NAME",
        help="an environment variable that holds a secret. The agent fills it in by name, into a secret field "
        "only, and its value is taken out of every result (repeatable; needs --allow-origin)",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        metavar="FILE",
        help="a Playwright storage state file (cookies and local storage) to start signed in (needs --allow-origin)",
    )
    parser.add_argument(
        "--private",
        choices=["strict", "heuristic"],
        default="strict",
        help="strict withholds all text-entry values; heuristic withholds only recognized sensitive fields. "
        "Both redact known credentials; neither recognizes arbitrary secrets in ordinary prose.",
    )
    parser.add_argument(
        "--upload-dir",
        type=Path,
        metavar="DIR",
        help="the only folder the agent may upload files from by path (by default it may send contents only)",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        metavar="DIR",
        help="the folder the agent may save downloaded files in (by default it saves none)",
    )
    parser.add_argument(
        "--save-storage-state",
        type=Path,
        metavar="FILE",
        help="save host-only cookies and local storage for the single trial origin to a new private file on close",
    )
    parser.add_argument(
        "--confirm",
        choices=["accept", "dismiss"],
        default="dismiss",
        help="fixed answer to JavaScript confirm dialogs; accepting requires a listed origin",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME=ENV",
        help="Origin-scoped credential header from an environment variable.",
    )
    parser.add_argument(
        "--cookie",
        action="append",
        default=[],
        metavar="NAME=ENV",
        help="Host-only session cookie from an environment variable.",
    )
    parser.add_argument(
        "--simulate-writes",
        action="store_true",
        help="Return synthetic success to the page for a blocked HTTP write. The agent still sees blocked.",
    )
    args = parser.parse_args(argv)
    if args.download_dir is not None and not args.download_dir.is_dir():
        parser.error(f"--download-dir {args.download_dir} is not a folder")
    try:
        boundary = Boundary.build(
            allow_origin=args.allow_origin,
            allow_host=args.allow_host,
            allow_write=args.allow_write,
            allow_writes=args.allow_writes,
            allow_read=args.allow_read,
            read_policy=args.read_policy,
            mutation_url=args.mutation_url,
            deny_url=args.deny_url,
            secret=args.secret,
            private=args.private,
            storage_state=args.storage_state,
            save_storage_state=args.save_storage_state,
            confirm=args.confirm,
            header=args.header,
            cookie=args.cookie,
            simulate_writes=args.simulate_writes,
            environ=os.environ,
        )
    except BoundaryError as error:
        parser.error(str(error))
    if bool(args.allow_origin) == args.public_read:
        parser.error("Choose --allow-origin for a trial or --public-read for public browsing.")
    # The limits, for whoever starts the server. stdout carries the protocol, so they go to stderr.
    lines = [*boundary.describe(), boundary.privacy_description()]
    print(boundary.scrub("\n".join(["viewspec-browser limits:", *(f"  {line}" for line in lines)])), file=sys.stderr)
    tools = BrowserTools(boundary=boundary, upload_dir=args.upload_dir, download_dir=args.download_dir)
    app = build_server(tools)

    def stop(signum: int, _frame: FrameType | None) -> None:
        tools.close()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app.run()
    finally:
        tools.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
