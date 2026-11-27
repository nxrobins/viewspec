"""What an action did, as far as the bridge can tell, and what two readings of a page cannot show.

An agent that has acted must decide whether to go on, wait, or try again. ``outcome_of`` reads the
result of ``act`` or ``call`` and gives it one of four statuses. They are about whether the action
took effect, not about whether the page's answer was good news:

    succeeded  the bridge saw the effect: what the agent waited for showed, the page navigated,
               every step was checked on its control, or the page answered (a change in the view, a
               request that was sent, a saved file, a new tab, an alert);
    blocked    the write policy stopped a request, including one with a simulated response;
    failed     it did not take effect: the request was refused, the browser would not send the
               form, or a control does not hold what was set;
    uncertain  it ran, and nothing shows whether it worked, or what the agent waited for did not
               show. ``page`` then says what the page did meanwhile: ``busy`` (a request the action
               started is still in flight), ``reacted`` (it changed, in a way the view does not
               show or the agent did not wait for), or ``quiet`` (no DOM change and no request).

``retry`` says whether repeating the same request could do the thing twice: ``not_needed``,
``safe`` (nothing happened, or the action only sets a state), or ``may_repeat``. Dismissing a
question does not prove the action was canceled: it may have taken effect before the dialog.

``noted_messages`` and ``address_lines`` find what a comparison of two readings misses: a message
the page showed again, one that came and went in between, and a new address in the same document.

Only the standard library is imported here, so the rules can be tested without a browser.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SUCCEEDED, FAILED, UNCERTAIN = "succeeded", "failed", "uncertain"
NOT_NEEDED, SAFE, MAY_REPEAT = "not_needed", "safe", "may_repeat"
BUSY, REACTED, QUIET = "busy", "reacted", "quiet"

# Steps that set a control to a state the bridge reads back. Doing one twice changes nothing more.
# Every other step (a click, a submit, a choice from a list, an upload, a download) may act again.
SETS_A_STATE = frozenset({"fill", "check", "uncheck", "select", "scroll"})

# Refusals where the refused step did nothing in the browser.
NOT_RUN = frozenset(
    {
        "BROWSER_NO_PAGE",
        "BROWSER_INVALID_REQUEST",
        "BROWSER_HANDLE_REQUIRED",
        "BROWSER_HANDLE_UNKNOWN",
        "BROWSER_OBSERVATION_UNKNOWN",
        "BROWSER_OBSERVATION_STALE",
        "BROWSER_TARGET_DRIFT",
        "BROWSER_TARGET_AMBIGUOUS",
        "BROWSER_TARGET_BLOCKED",
        "BROWSER_TARGET_STALE",
        "BROWSER_ORIGIN_NOT_ALLOWED",
        "BROWSER_ADDRESS_NOT_ALLOWED",
        "BROWSER_ADDRESS_PRIVATE",
        "BROWSER_SECRET_REFUSED",
        "BROWSER_UPLOAD_NOT_ALLOWED",
        "BROWSER_UPLOAD_NOT_FOUND",
        "BROWSER_UPLOAD_TOO_LARGE",
        "BROWSER_DOWNLOAD_NOT_ALLOWED",
    }
)
# Refusals where the step ran and did not do what was asked. The value says whether it left the
# page as it was (a list it opened is closed again), or clicked something that may have acted.
RAN_WITHOUT_EFFECT = {
    "BROWSER_OPTION_AMBIGUOUS": SAFE,
    "BROWSER_OPTION_NOT_FOUND": SAFE,
    "BROWSER_DATE_UNAVAILABLE": SAFE,
    "BROWSER_DATE_NOT_PICKED": SAFE,
    "BROWSER_UPLOAD_REFUSED": MAY_REPEAT,
    "BROWSER_DOWNLOAD_NONE": MAY_REPEAT,
    "BROWSER_DOWNLOAD_TOO_LARGE": MAY_REPEAT,
    "BROWSER_DOWNLOAD_FAILED": MAY_REPEAT,
}
# Every other refusal (a timeout, a plan the page interrupted, an error with no code of its own)
# stopped part-way: uncertain.

REASON_LIMIT = 300
TEXT_PREVIEW = 80
# What a line of changes says about one control: "changed [7] text 'Email': value '' -> 'bo'".
_FIELD_LINE = re.compile(r"changed (?:\[\d+\] )?\S+ (?P<name>'.*'|\".*\"): (?P<fields>.+)")
_SET_FIELDS = ("value ", "checked ", "options ")


def _clip(text: Any, limit: int = REASON_LIMIT) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _made(status: str, reason: str, retry: str, page: str | None = None) -> dict[str, Any]:
    outcome: dict[str, Any] = {"status": status, "reason": _clip(reason), "retry": retry}
    if page is not None:
        outcome["page"] = page
    return outcome


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _name(step: Mapping[str, Any]) -> str:
    planned = step.get("step") or {}
    return f"{planned.get('op', 'step')} {planned.get('name', '')!r}"


def _answers(changes: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]) -> list[str]:
    """The lines of ``changes`` that say something the steps did not do themselves.

    A step that set a field and was checked on it shows in the changes as that field's new value.
    That is what the step did, not an answer from the page: after a form is filled in and
    submitted, it says nothing about whether the submit did anything.
    """

    own = {
        repr((step.get("step") or {}).get("name", ""))
        for step in steps
        if step.get("verified") is True and (step.get("step") or {}).get("op") in SETS_A_STATE
    }
    lines = []
    for line in changes.get("lines") or []:
        if line == "no change":
            continue
        told = _FIELD_LINE.fullmatch(line)
        if told and told["name"] in own and all(part.startswith(_SET_FIELDS) for part in told["fields"].split("; ")):
            continue
        lines.append(line)
    return lines


def _page_state(activity: Mapping[str, Any] | None, changed: bool) -> str | None:
    """What the page did after the action began, from how busy the bridge found it."""

    if activity is None:
        return REACTED if changed else None
    if activity.get("pending"):
        return BUSY
    if changed or activity.get("mutations") or activity.get("requests"):
        return REACTED
    return QUIET


def _unseen(activity: Mapping[str, Any]) -> str:
    done = [_count(activity.get("mutations") or 0, "DOM change"), _count(activity.get("requests") or 0, "request")]
    return f"The page did something the view does not show ({', '.join(done)}, none still in flight)."


def action_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    """Versioned evidence alongside the legacy effect-based outcome; never infer persistence.

    A satisfied explicit condition proves that UI condition only. Without one, a reaction or
    verified control does not establish application completion, including after all requests end.
    """
    steps = result.get("steps") or []
    verified = [step.get("verified") for step in steps]
    progress = result.get("progress") or {}
    attempted = steps or any(field.get("status") != "not_run" for field in progress.get("fields", []))
    execution = "dispatched" if steps else "unknown" if attempted else "not_dispatched"
    if progress.get("submission") == "withheld":
        execution = (
            "partial" if any((step.get("step") or {}).get("op") in SETS_A_STATE for step in steps) else "unknown"
        )
    verification = (
        "mismatch" if False in verified else "matched" if verified and all(v is True for v in verified) else "unknown"
    )
    wait = result.get("wait")
    network = (result.get("readiness") or {}).get("pending") or {}
    finite_pending = network.get("finite", 0) + network.get("unknown", 0)
    form_pending = any(error.get("code") == "BROWSER_FORM_PENDING" for error in result.get("errors", []))
    completion: dict[str, Any] = {
        "scope": "application",
        "state": "pending" if finite_pending or form_pending else "unknown",
        "requested": False,
    }
    if wait:
        completion = {
            "scope": "ui_condition",
            "state": "met" if wait.get("met") else "timed_out",
            "requested": True,
            "condition": wait.get("condition"),
        }
    if result.get("blocked_writes") or any(
        w.get("state") in {"blocked", "blocked-and-simulated"} for step in steps for w in step.get("writes", [])
    ):
        completion = {"scope": "transport", "state": "blocked", "requested": bool(wait)}
    return {"version": 1, "execution": execution, "control_verification": verification, "completion": completion}


def outcome_of(result: Mapping[str, Any], activity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The outcome of one ``act`` or ``call`` result.

    ``activity`` is what the page did from the first step to the last reading: ``mutations`` (DOM
    changes, or None on a new document), ``requests`` (started), ``pending`` (of those, still in
    flight), and ``watched_ms``. Without it, an uncertain outcome has no ``page``.
    """

    steps: Sequence[Mapping[str, Any]] = result.get("steps") or []
    # A page can navigate or display "Saved" in response to a synthetic HTTP response.
    # Only the transport knows whether the request actually left the browser.
    recorded = [write for step in steps for write in step.get("writes", ())]
    passed = bool(result.get("passed_writes")) or any(write.get("state") == "passed" for write in recorded)
    blocked = result.get("blocked_writes") or [
        write for write in recorded if write.get("state") in {"blocked", "blocked-and-simulated"}
    ]
    if blocked:
        write = blocked[0]
        reason = f"{write.get('method', 'Request')} {write.get('url', '')} was not sent by policy."
        if write.get("state") == "blocked-and-simulated":
            reason += " The page received a simulated response."
        return _made("blocked", reason, MAY_REPEAT)
    dialogs = [*(item for step in steps for item in step.get("dialogs") or []), *(result.get("dialogs") or [])]
    asked = [
        item for item in dialogs if item.get("type") in {"confirm", "prompt"} and item.get("answer") == "dismissed"
    ]
    # Even a field's handler may perform work before asking a question. A dismissed dialog is
    # evidence that page code ran, not that its effects were canceled or rolled back.
    acts_again = bool(asked) or any((step.get("step") or {}).get("op") not in SETS_A_STATE for step in steps)
    if not result.get("ok"):
        refusal = _refused(result, acts_again)
        changed_choice = any(
            (field.get("selection") or {}).get("changed") for field in (result.get("progress") or {}).get("fields", [])
        )
        if (passed or changed_choice) and refusal["retry"] == SAFE:
            refusal["retry"] = MAY_REPEAT
        return refusal

    # ---- It did not take effect. ----------------------------------------------------------------
    invalid = [field for step in steps for field in step.get("invalid") or []]
    if invalid:
        reasons = "; ".join(f"{field.get('name') or 'a field'}: {field.get('message', '')}" for field in invalid)
        return _made(FAILED, f"The browser did not send the form: {reasons}", MAY_REPEAT if asked or passed else SAFE)
    stopped = result.get("blocked_navigations") or []
    if stopped:
        where = stopped[0].get("url", "another site")
        return _made(
            FAILED,
            f"It tried to leave for {where}, which this server does not allow. The page stayed.",
            MAY_REPEAT if asked or passed else SAFE,
        )
    unheld = [step for step in steps if step.get("verified") is False]
    if unheld:
        op = (unheld[0].get("step") or {}).get("op")
        retry = SAFE if op in SETS_A_STATE and not asked and not passed else MAY_REPEAT
        return _made(FAILED, f"{_name(unheld[0])} did not hold: the control does not show it afterwards.", retry)

    # ---- What the agent waited for is its own test of success. ------------------------------------
    wait = result.get("wait")
    if wait and wait.get("met"):
        return _made(SUCCEEDED, f"The page showed what was waited for: {wait.get('for', 'the change')}.", NOT_NEEDED)
    changes = result.get("changes") or {}
    lines = [] if changes.get("empty", True) else _answers(changes, steps)
    changed = bool(lines)
    retry = MAY_REPEAT if acts_again or passed else SAFE
    if wait:
        state = _page_state(activity, changed)
        said = f"What was waited for ({wait.get('for', 'the change')}) did not show in {wait.get('timeout_ms')} ms."
        if state == BUSY:
            said += " A request the action started is still in flight: wait again before you repeat it."
        elif changed:
            said += f" The page changed in another way: {lines[0]}. Read the view before you repeat it."
        elif state == REACTED and activity is not None:
            said += f" {_unseen(activity)} Read the view before you repeat it."
        elif state == QUIET:
            said += " Nothing on the page reacted: no DOM change and no request."
        return _made(UNCERTAIN, said, retry, state)

    # ---- The bridge saw the effect. ---------------------------------------------------------------
    if result.get("navigated") or changes.get("new_document"):
        return _made(SUCCEEDED, f"The page navigated to {result.get('url', 'a new address')}.", NOT_NEEDED)
    if steps and not asked and all(step.get("verified") is True for step in steps):
        what = _name(steps[0]) if len(steps) == 1 else f"Each of the {len(steps)} steps"
        return _made(SUCCEEDED, f"{what} was checked on its control afterwards.", NOT_NEEDED)
    if changed:
        more = f" (and {_count(len(lines) - 1, 'more line')} in changes)" if len(lines) > 1 else ""
        return _made(SUCCEEDED, f"The page answered: {lines[0]}{more}.", NOT_NEEDED)
    saved = [step["download"].get("file", "a file") for step in steps if step.get("download")]
    if saved:
        return _made(SUCCEEDED, f"It saved {', '.join(saved)} in the download folder.", NOT_NEEDED)
    opened = [*(url for step in steps for url in step.get("opened") or []), *(result.get("opened") or [])]
    if opened and all(url in {"", "about:blank"} for url in opened):
        return _made(
            UNCERTAIN,
            "An empty tab was opened and closed. No destination or completed action was observed.",
            MAY_REPEAT,
        )
    if opened:
        return _made(
            SUCCEEDED, f"It opened a new tab ({opened[0]}), which the bridge closed and did not follow.", NOT_NEEDED
        )
    unsaved = [name for step in steps for name in step.get("unsaved_downloads") or []]
    if unsaved:
        return _made(SUCCEEDED, f"It started a download ({', '.join(unsaved)}) that was not saved.", NOT_NEEDED)
    alerts = [item for item in dialogs if item.get("type") == "alert"]
    if alerts:
        said = _clip(alerts[0].get("message", ""), TEXT_PREVIEW)
        return _made(SUCCEEDED, f"The page answered with an alert: {said!r}.", NOT_NEEDED)
    if asked:
        question = _clip(asked[0].get("message", ""), TEXT_PREVIEW)
        return _made(
            UNCERTAIN,
            f"The bridge dismissed {question!r} ({asked[0].get('type')}). That does not prove the action "
            "was canceled; it may already have taken effect. Check the page before repeating it.",
            MAY_REPEAT,
            _page_state(activity, False),
        )
    if not steps:
        return _made(SUCCEEDED, "Nothing to do: the control was already in that state.", NOT_NEEDED)

    # ---- It ran, and nothing shows whether it worked. ---------------------------------------------
    what = _name(steps[0]) if len(steps) == 1 else f"The {len(steps)} steps"
    state = _page_state(activity, False)
    said = f"{what} ran, and the view shows no answer."
    if state == BUSY and activity is not None:
        flying = activity.get("pending") or 0
        verb = "is" if flying == 1 else "are"
        said += f" {_count(flying, 'request')} it started {verb} still in flight: wait for the answer (wait_for) before you repeat it."
    elif state == REACTED and activity is not None:
        said += f" {_unseen(activity)}"
    elif state == QUIET and activity is not None:
        watched = activity.get("watched_ms")
        within = f" in the {watched} ms before it was read" if watched is not None else ""
        said += f" Nothing on the page reacted{within}: no DOM change and no request. It may have done nothing."
    else:
        said += " Nothing shows whether it worked."
    return _made(UNCERTAIN, said, retry, state)


def _refused(result: Mapping[str, Any], acts_again: bool) -> dict[str, Any]:
    """The outcome of a result with ``ok`` false. ``acts_again``: a step that ran before the
    refusal was one that may act twice."""

    error = (result.get("errors") or [{}])[0]
    code = str(error.get("code", ""))
    message = error.get("message") or result.get("summary") or "The request was refused."
    if code in {"BROWSER_FORM_MISMATCH", "BROWSER_FORM_UNKNOWN"}:
        return _made(FAILED if code == "BROWSER_FORM_MISMATCH" else UNCERTAIN, message, MAY_REPEAT)
    if code == "BROWSER_OBSERVATION_FAILED":
        return _made(
            UNCERTAIN,
            "The action steps finished, but the page could not be read afterwards. Observe it before deciding what to do next.",
            MAY_REPEAT,
        )
    if code in NOT_RUN or code.startswith("AGENT_"):
        return _made(
            FAILED, f"Refused, and the refused step did not run: {message}", MAY_REPEAT if acts_again else SAFE
        )
    if code in RAN_WITHOUT_EFFECT:
        retry = MAY_REPEAT if acts_again else RAN_WITHOUT_EFFECT[code]
        return _made(FAILED, f"It ran and did not do what was asked: {message}", retry)
    return _made(UNCERTAIN, f"It stopped part-way, and nothing shows how far it got: {message}", MAY_REPEAT)


def scroll_outcome(scroll: Mapping[str, Any]) -> dict[str, Any]:
    """The outcome of a scroll, from the ``scroll`` part of its result. Scrolling again is safe."""

    if scroll.get("met") is True:
        return _made(SUCCEEDED, "The page shows what was waited for.", NOT_NEEDED)
    if scroll.get("met") is False:
        if scroll.get("end_reached"):
            return _made(UNCERTAIN, "The list ended, and what was waited for did not show.", SAFE)
        return _made(UNCERTAIN, "What was waited for did not show before the time ran out. The list may go on.", SAFE)
    if scroll.get("moved") or scroll.get("loaded"):
        loaded = "More content loaded." if scroll.get("loaded") else "Nothing new loaded."
        return _made(SUCCEEDED, f"It scrolled. {loaded}", NOT_NEEDED)
    return _made(FAILED, "It did not move: it is already at that end, or this is not what scrolls.", SAFE)


# ---- What two readings cannot show -------------------------------------------------------------------


def noted_messages(
    notes: Sequence[Mapping[str, Any]],
    *,
    since: int,
    shown: Mapping[str, tuple[str, str]],
    new: Sequence[tuple[str, str]] = (),
) -> list[tuple[str, str, str]]:
    """Messages the page wrote between two readings that comparing the readings does not show.

    ``notes`` are the page recorder's notes, oldest first: ``{"seq", "ref", "kind", "text"}``, one
    for each write into a message region, with ``"aside": true`` on one written while the bridge
    drove a widget of the page (a calendar it paged through). ``since`` is the recorder's sequence
    number at the earlier reading. ``shown`` maps the ref of each message region in the later reading to its
    ``(kind, text)``. ``new`` lists the messages the diff of the two readings already reports.

    Returns ``(kind, text, how)`` in the order the page wrote them. ``how`` is ``"again"`` for a
    message that was on the page at the earlier reading and was written again, and ``"gone"`` for
    one that is not on the page any more: a toast that came and went, a "Saving…" that became
    "Saved". A note whose text is still part of what its region says (an earlier line of a log) is
    neither, and an aside that is gone is not reported: it was how the step worked, not an answer.
    """

    window = [note for note in notes if note["seq"] > since]
    last = {note["ref"]: note for note in window}
    unclaimed = Counter(new)
    on_page = set(shown.values())
    found: list[tuple[str, str, str]] = []
    for note in window:
        current = shown.get(note["ref"])
        if current is not None and note is last[note["ref"]]:
            if unclaimed[current] > 0:
                unclaimed[current] -= 1  # The diff reports it as a new message.
                continue
            item = (current[0], current[1], "again")
        elif current is not None and note["text"] in last[note["ref"]]["text"]:
            continue
        elif note.get("aside") or (note["kind"], note["text"]) in on_page:
            continue  # A widget's word on the way, or words that another region shows now.
        else:
            item = (note["kind"], note["text"], "gone")
        if item not in found:
            found.append(item)
    return found


def message_lines(noted: Sequence[tuple[str, str, str]]) -> list[str]:
    """``noted_messages`` as lines for an agent, in the style of the diff's message lines."""

    how = {"again": "shown again", "gone": "shown, now gone"}
    return [f"message {kind} {_clip(text, TEXT_PREVIEW)!r} ({how[what]})" for kind, text, what in noted]


def address_lines(before_url: str, after_url: str, before_title: str = "", after_title: str = "") -> list[str]:
    """Lines for an address or a title that changed while the document stayed the same: a ``#hash``
    link, a route a script pushed. The origin is left out when it did not change."""

    lines: list[str] = []
    if before_url != after_url:
        old, new = urlsplit(before_url), urlsplit(after_url)
        if (old.scheme, old.netloc) == (new.scheme, new.netloc):
            local = [urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment)) for parts in (old, new)]
            lines.append(f"address {local[0]} -> {local[1]}")
        else:
            lines.append(f"address {before_url} -> {after_url}")
    if before_title != after_title:
        lines.append(f"title {_clip(before_title, TEXT_PREVIEW)!r} -> {_clip(after_title, TEXT_PREVIEW)!r}")
    return lines
