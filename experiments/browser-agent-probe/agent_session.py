"""Read a live page with the ViewSpec agent lift, and act on it.

``BrowserSession`` drives ``bridge.js`` (a Playwright browser) and closes the loop:

    observe  snapshot the composed tree, lift it with ``profile="agent"``, render the page view,
             and verify a locator for every control
    act      plan one action on a control (``plan_control_action``) and run it
    call     plan a whole form tool call (``plan_form_call``) and run it

Every request goes through the package planner first, so a bad request fails before the browser
does anything. Each step names its element by ref (exact, while the element is in the page) and by
a verified locator (frame chain + CSS, or role + name), which finds it again after a re-render.

By default the session blocks writes: a form post or any other non-GET request is recorded and
answered with a stub page, so an agent can complete a real form without changing anything.

This is an experiment. It is not part of the ViewSpec package or its guarantees.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from page_view import page_view

from viewspec.raw_html import HtmlLiftResult, lift_html
from viewspec.raw_html_actions import ActionStep, plan_control_action, plan_form_call
from viewspec.raw_html_agent import HtmlControl, control_aria_role
from viewspec.raw_html_diff import diff_lifts

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "bridge.js"


class BridgeError(RuntimeError):
    """The browser could not do what was asked."""


@dataclass
class Observation:
    url: str
    title: str
    html: str
    lift: HtmlLiftResult
    view: str
    handles: dict[int, HtmlControl]
    locators: dict[str, dict[str, Any]]
    interactive: list[dict[str, str]]
    action_ids: int
    doc_id: str
    secrets: list[str] = field(default_factory=list, repr=False)

    def handle_of(self, node_id: str) -> int | None:
        return next((handle for handle, control in self.handles.items() if control.node_id == node_id), None)


@dataclass
class StepResult:
    step: ActionStep
    resolved_by: str
    verified: bool | None
    navigated: bool
    url: str

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step.to_json(),
            "resolved_by": self.resolved_by,
            "verified": self.verified,
            "navigated": self.navigated,
            "url": self.url,
        }


def changes(before: Observation, after: Observation) -> dict[str, Any]:
    """What changed from one observation to a later one, as short lines for an agent.

    Handles in the lines are those of ``after``. After a navigation to a new document a diff means
    nothing, so the result only says that the page is new.
    """

    if before.doc_id != after.doc_id:
        return {"new_document": True, "empty": False, "lines": [f"new page {after.title!r} {after.url}"]}
    diff = diff_lifts(before.lift, after.lift)
    return {"new_document": False, "empty": diff.empty, "lines": diff.lines(after.handle_of)}


class BrowserSession:
    def __init__(self, *, block_writes: bool = True, allowed_origins: tuple[str, ...] = ()) -> None:
        self.block_writes = block_writes
        self.allowed_origins = tuple(allowed_origins)
        self.observation: Observation | None = None
        self._next_id = 0
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
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._next_id += 1
        self._proc.stdin.write(json.dumps({"id": self._next_id, "cmd": cmd, **args}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            stderr = (self._proc.stderr.read() if self._proc.stderr else "").strip()
            last = stderr.splitlines()[-1] if stderr else "no message"
            raise BridgeError(f"bridge.js stopped: {last[:300]}")
        reply = json.loads(line)
        if reply.get("id") != self._next_id:
            raise BridgeError(f"bridge.js answered request {reply.get('id')}, not {self._next_id}")
        if not reply["ok"]:
            raise BridgeError(reply["error"])
        return reply["result"]

    def close(self) -> None:
        if self._proc.poll() is None:
            with contextlib.suppress(BridgeError):
                self._call("close")
            self._proc.wait(timeout=10)

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- reading ----------------------------------------------------------------------------------
    def open(self, url: str) -> dict[str, Any]:
        """Load a page. Returns its status and server HTML; call ``observe()`` to read it."""

        self.observation = None
        return self._call(
            "open", url=url, block_writes=self.block_writes, allowed_origins=list(self.allowed_origins) or None
        )

    def observe(self, *, locate: bool = True, collect_secrets: bool = False) -> Observation:
        """Snapshot, lift, and (by default) verify a locator for every control and form."""

        snap = self._call("snapshot", collect_secrets=collect_secrets)
        lift = lift_html(snap["html"], profile="agent")
        view, handles = page_view(lift)
        locators: dict[str, dict[str, Any]] = {}
        if locate:
            requests = [
                {"key": c.node_id, "ref": c.ref, "role": control_aria_role(c), "name": c.name}
                for c in lift.controls
                if c.ref
            ]
            requests += [{"key": f.node_id, "ref": f.ref, "role": None, "name": None} for f in lift.forms if f.ref]
            if requests:
                locators = self._call("locate", requests=requests)
            # What the element must still be when a re-render forces the locator fallback.
            expected = {c.node_id: (c.tag, c.name) for c in lift.controls}
            expected |= {f.node_id: ("form", None) for f in lift.forms}
            for node_id, locator in locators.items():
                locator["tag"], locator["expect_name"] = expected[node_id]
        self.observation = Observation(
            url=snap["url"],
            title=snap["title"],
            html=snap["html"],
            lift=lift,
            view=view,
            handles=handles,
            locators=locators,
            interactive=snap["interactive"],
            action_ids=snap["actionIds"],
            doc_id=snap["docId"],
            secrets=snap.get("secrets", []),
        )
        return self.observation

    def aria_snapshot(self) -> str:
        return self._call("aria")["aria"]

    def reload(self) -> None:
        self._call("reload")
        self.observation = None

    def blocked_writes(self) -> list[dict[str, Any]]:
        """Requests that ``block_writes`` stopped since the page was opened."""

        return self._call("writes")["writes"]

    def blocked_navigations(self) -> list[dict[str, Any]]:
        """Top-level navigations that ``allowed_origins`` stopped since the page was opened."""

        return self._call("writes")["navigations"]

    # ---- acting -----------------------------------------------------------------------------------
    def _current(self) -> Observation:
        return self.observation if self.observation is not None else self.observe()

    def act(self, handle: int, op: str, value: Any = None) -> list[StepResult]:
        """Plan and run one action on the control with this handle in the current view."""

        observation = self._current()
        if handle not in observation.handles:
            raise KeyError(f"no control [{handle}] in the current view")
        return self.run(plan_control_action(observation.handles[handle], op, value))

    def call(self, tool_name: str, arguments: dict[str, Any], *, submit: bool = True) -> list[StepResult]:
        """Plan and run a form tool call from the current view's ``form_tool_schemas()``."""

        return self.run(plan_form_call(self._current().lift, tool_name, arguments, submit=submit))

    def run(self, steps: tuple[ActionStep, ...], *, observation: Observation | None = None) -> list[StepResult]:
        """Run planned steps in order. The view is stale afterwards; call ``observe()`` again.

        ``observation`` is the view the steps were planned from (by default, the current one). Its
        locators find an element again when the page has replaced it since that view.
        """

        observation = observation or self._current()
        if self._call("document")["doc_id"] != observation.doc_id:
            # Refs and locators describe a document that is gone. A control with the same name on the
            # new page is a different control, so refuse rather than guess.
            raise BridgeError(
                "stale-view: the page has loaded a new document since this view; observe it and plan again"
            )
        results: list[StepResult] = []
        for step in steps:
            locator = observation.locators.get(step.node_id)
            reply = self._call(
                "act",
                op=step.op,
                ref=step.ref,
                locator=locator if locator and locator.get("strategy") else None,
                value=step.value,
                values=list(step.values),
            )
            results.append(
                StepResult(
                    step=step,
                    resolved_by=reply["resolved_by"],
                    verified=reply["verified"],
                    navigated=reply["navigated"],
                    url=reply["url"],
                )
            )
            if reply["navigated"] and len(results) < len(steps):
                # The rest of the plan was made for the page that is gone.
                self.observation = None
                raise BridgeError(
                    f"The page navigated after step {len(results)} of {len(steps)} ({step.op} {step.name!r}); "
                    f"{len(steps) - len(results)} step(s) did not run. Observe the new page and plan again."
                )
        self.observation = None
        return results
