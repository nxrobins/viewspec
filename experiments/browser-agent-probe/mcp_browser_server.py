"""The ViewSpec agent lift as MCP tools: observe a live page, act on a control, call a form tool.

    python experiments/browser-agent-probe/mcp_browser_server.py [--allow-origin URL ...] [--allow-writes]

Three tools, served over stdio:

    observe  open a URL (optional) and read the page: a compact view with a numbered handle for each
             control, and one typed tool per form
    act      click, fill, check, uncheck, or select one control, by handle
    call     fill in and submit a form, by its tool name and typed arguments

``act`` and ``call`` check the request against the page before the browser does anything
(``viewspec.raw_html_actions``), run the steps, and observe again, so each result carries the new
view. A handle belongs to the observation that showed it; pass ``observation_id`` to act from an
older one, and a re-rendered element is found again by its saved locator, or the step is refused.

Safety is set by whoever starts the server, never by the agent:

- Writes are blocked unless ``--allow-writes`` is given: a form post or any other request that is not
  GET, HEAD, or OPTIONS is recorded and answered with a stub page. Results list each blocked write
  with its form fields; secret fields are shown as ``[redacted]``.
- With ``--allow-origin``, the agent can only open, and only navigate to, those origins.
- No result contains a secret value: the lift never keeps one, and logged steps redact them.

This server is not ``viewspec mcp``. That server makes no network calls; this one drives a browser
that loads whatever pages the agent opens. It is an experiment, not part of the ViewSpec package.
"""

from __future__ import annotations

import argparse
import base64
import os
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

from agent_session import (
    BridgeError,
    BrowserSession,
    Observation,
    ScrollResult,
    StepResult,
    WaitResult,
    changes,
    request_fields,
)
from page_view import view_page

from viewspec.local_tools_response import tool_error_response, tool_response
from viewspec.raw_html_actions import (
    MAX_UPLOAD_BYTES,
    ActionStep,
    AgentActionError,
    UploadFile,
    plan_control_action,
    plan_form_call,
    plan_scroll,
)
from viewspec.raw_html_agent import SENSITIVE_NAME_RE, form_fields, form_tool_names
from viewspec.raw_html_diff import ChangeCondition

SERVER_NAME = "viewspec-browser"
HISTORY = 8  # Observations kept, so an agent can act from a recent one.
REDACTED = "[redacted]"
INSTRUCTIONS = (
    "Read and operate web pages through the ViewSpec agent lift. Start with observe(url). Its view lists "
    "every control as [n]; act(handle=n, action=...) clicks, fills, checks, unchecks, or selects one; "
    'act(action="scroll") scrolls the page, and loads more of an infinite list. Each '
    "form is also a typed tool in form_tools; call(tool=..., arguments={...}) fills it in and submits it. "
    "Requests are checked against the page first: on ok=false, read errors and fix the request. act and "
    "call return the new view and changes, the lines that say what the action changed (messages first); "
    "use the new handles next. observe(since=id) shows what changed since an earlier reading. A page "
    "that updates after a delay (a server answer) shows nothing at first: pass wait_for, for example "
    '{"change": "message"} or {"text": "Order placed"}, and the tool reads the page again as it '
    "changes, until that shows or timeout_ms ends; wait.met says which. Writes may be blocked: a submitted "
    "form then shows 'Write blocked', and blocked_writes lists what would have been sent."
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


class BrowserTools:
    """The three tools, over one browser session that starts on the first ``observe``."""

    def __init__(
        self,
        *,
        block_writes: bool = True,
        allowed_origins: tuple[str, ...] = (),
        upload_dir: Path | None = None,
        download_dir: Path | None = None,
    ) -> None:
        self.block_writes = block_writes
        self.upload_dir = upload_dir.resolve() if upload_dir is not None else None
        self.download_dir = download_dir.resolve() if download_dir is not None else None
        self.allowed_origins = tuple(origin.rstrip("/") for origin in allowed_origins)
        self._session: BrowserSession | None = None
        self._history: dict[int, Observation] = {}
        self._latest = 0
        self._reported = {"writes": 0, "navigations": 0}
        self._secret_fields: set[str] = set()
        self._lock = threading.Lock()

    # ---- tools ------------------------------------------------------------------------------------
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

        Returns ``view``: the page as text, one line per message, heading, value, table row, or
        control. Each control line starts with a handle such as [7], then its kind, name, and state
        (value, [x] for checked, options, required, secret, disabled). ``form_tools`` describes each
        form as a tool for ``call``. ``observation_id`` names this reading; its handles stay valid for
        ``act``. With ``since`` (an earlier ``observation_id``, and no ``url``), ``changes`` lists what
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
                    self._reported = {"writes": 0, "navigations": 0}
                    if condition is None:
                        return self._observe_result("Observed the page.", paging=paging)
                    base = session.observe(locate=False)
                    waited = session.wait(condition, base, timeout_ms=timeout_ms)
                    return self._waited_result("Observed the page.", waited, since=base, paging=paging)
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

    def act(
        self,
        action: Literal["click", "fill", "check", "uncheck", "select", "scroll", "upload", "download"],
        handle: int | None = None,
        value: str | list[str] | dict[str, str] | None = None,
        observation_id: int | None = None,
        wait_for: WaitFor | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        files: list[FileArg] | None = None,
    ) -> dict[str, Any]:
        """Do one thing to the control with this handle, then read the page again.

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

        ``fill`` takes text in ``value``. ``select`` takes an option value or label (a list for a
        multiple select). On a custom combobox or listbox, ``select`` chooses from its list: it opens
        the list (typing the value into an editable combobox), waits for the option, and clicks the one
        whose name is the value, or starts with it; ``steps[0].chosen`` names it. On a date field,
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
                    steps = plan_control_action(observation.handles[handle], action, value, lift=observation.lift)
            except AgentActionError as error:
                return _planning_error(error)
            except _ToolError as error:
                return error.result
            return self._run(steps, observation, condition, timeout_ms)

    def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        submit: bool = True,
        observation_id: int | None = None,
        wait_for: WaitFor | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Fill in a form with ``arguments`` and submit it, then read the page again.

        ``tool`` is a name from ``form_tools``; ``arguments`` follows its ``input_schema``. Arguments you
        leave out keep the value the page shows. ``submit=false`` only fills the form. Every problem
        with the arguments is reported at once, before the browser does anything. ``wait_for`` and
        ``timeout_ms`` work as for ``act``, for example ``{"text": "Order placed"}``.
        """

        with self._lock:
            observation = self._observation(observation_id)
            if isinstance(observation, dict):
                return observation
            try:
                condition = _condition(wait_for)
                steps = plan_form_call(
                    observation.lift, tool, self._with_files(observation, tool, arguments), submit=submit
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
            self._session = BrowserSession(
                block_writes=self.block_writes, allowed_origins=self.allowed_origins, download_dir=self.download_dir
            )
        return self._session

    def _refuse_origin(self, url: str) -> dict[str, Any] | None:
        if not self.allowed_origins:
            return None
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else url
        if origin in self.allowed_origins:
            return None
        return _error(
            "BROWSER_ORIGIN_NOT_ALLOWED",
            f"{origin} is not an allowed origin.",
            f"Open a page on one of: {', '.join(self.allowed_origins)}.",
        )

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
        return result

    def _run(
        self,
        steps: tuple[ActionStep, ...],
        observation: Observation,
        condition: ChangeCondition | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        session = self._browser()
        try:
            results = session.run(steps, observation=observation) if steps else []
        except BridgeError as error:
            # Some steps may have run; show the agent the page as it is now, if it can be read.
            try:
                current: dict[str, Any] | None = self._observe_data(since=observation)
                current.pop("_note")
            except BridgeError:
                current = None
            return tool_error_response(
                _bridge_code(error),
                str(error),
                "Read the new view, then act again with its handles.",
                data=current,
            )
        summary = "Nothing to do: the control is already in that state." if not steps else _summary(results)
        if condition is None:
            result = self._observe_result(summary, since=observation)
        else:
            try:
                waited = session.wait(condition, observation, timeout_ms=timeout_ms)
            except BridgeError as error:
                return _error(_bridge_code(error), str(error), "Observe the page, then decide.")
            result = self._waited_result(summary, waited, since=observation, acted=bool(steps))
        result["steps"] = [item.to_json() for item in results]
        result["navigated"] = any(item.navigated for item in results)
        return result

    def _observe_result(
        self, summary: str, *, since: Observation | None = None, paging: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = self._observe_data(since=since, paging=paging)
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

        page = view_page(
            observation.lines, offset=offset, limit=max(1, min(int(limit), MAX_VIEW_LIMIT)), find=find or None
        )
        data = {
            "observation_id": observation_id,
            "view": page.text,
            "view_page": page.to_json(),
            "form_tools": [_public_schema(schema) for schema in observation.lift.form_tool_schemas()],
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
        observation = session.observe()
        self._latest += 1
        self._history[self._latest] = observation
        for key in sorted(self._history)[:-HISTORY]:
            del self._history[key]
        self._secret_fields |= {c.field_name for c in observation.lift.controls if c.secret and c.field_name}
        view, note = self._view_data(observation, self._latest, **(paging or {}))
        data: dict[str, Any] = {"url": observation.url, "title": observation.title, **view, "_note": note}
        if since is not None:
            data["changes"] = changes(since, observation)
        writes = session.blocked_writes()
        navigations = session.blocked_navigations()
        if len(writes) > self._reported["writes"]:
            data["blocked_writes"] = [self._redact_write(w) for w in writes[self._reported["writes"] :]]
        if len(navigations) > self._reported["navigations"]:
            data["blocked_navigations"] = navigations[self._reported["navigations"] :]
        self._reported = {"writes": len(writes), "navigations": len(navigations)}
        return data

    def _redact_write(self, write: dict[str, Any]) -> dict[str, Any]:
        """A blocked request, with secret form fields redacted. A body that is not a form is not shown."""

        shown: dict[str, Any] = {"method": write["method"], "url": write["url"]}
        fields = request_fields(write)
        if fields:
            shown["form_fields"] = {
                key: REDACTED if key in self._secret_fields or SENSITIVE_NAME_RE.search(key) else value
                for key, value in fields.items()
            }
        elif write.get("post_data") or write.get("post_data_b64"):
            shown["body_bytes"] = (
                len(base64.b64decode(write["post_data_b64"]))
                if write.get("post_data_b64")
                else len((write.get("post_data") or "").encode("utf-8"))
            )
        return shown


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
    return f"Did {len(results)} step(s): {'; '.join(parts)}.{tail}"


def _public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A form tool as the agent sees it: the internal ``x-`` keys (node ids) are left out."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if not str(key).startswith("x-")}
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
    if message.startswith("stale-view:"):
        return "BROWSER_OBSERVATION_STALE"
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
    if message.startswith("stale:"):
        return "BROWSER_TARGET_STALE"
    if "did not run" in message:
        return "BROWSER_PLAN_INTERRUPTED"
    if "Timeout" in message or "timeout" in message:
        return "BROWSER_TIMEOUT"
    return "BROWSER_ERROR"


def build_server(tools: BrowserTools) -> Any:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, log_level="ERROR")
    changes_world = not tools.block_writes
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
        help="an origin the agent may open and navigate to, such as https://shop.example (repeatable)",
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="let form posts and other non-GET requests through (by default they are blocked)",
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
    args = parser.parse_args(argv)
    if args.download_dir is not None and not args.download_dir.is_dir():
        parser.error(f"--download-dir {args.download_dir} is not a folder")
    tools = BrowserTools(
        block_writes=not args.allow_writes,
        allowed_origins=tuple(args.allow_origin),
        upload_dir=args.upload_dir,
        download_dir=args.download_dir,
    )
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
