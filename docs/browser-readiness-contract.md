# Browser readiness and completion evidence

The experimental browser bridge reads and checks current UI state. It does not infer application
persistence from a quiet network, a navigation, or a message such as “Saving” or “Saved”. Network
permission, target identity, route checks, editor preservation, and event-time form guards remain
independent from readiness.

## Observation and actions

Navigation waits for DOM content and gives outstanding finite or unclassified requests a bounded
one-second startup window. Established `text/event-stream` responses do not have to close. This
includes SSE opened with `fetch`, not only EventSource. A page can still hydrate later; use
`wait_for={"control": "Name", "enabled": true}` or another explicit condition when the task needs it.

Ordinary actions yield once for handlers/rendering, then verify the control and read the page. This
short yield is not a completion assertion. Actions do not wait for global `networkidle`. The
returned observation includes versioned `readiness` with counts of finite, streaming, and
unclassified pending requests. `observed` means a current reading was obtained, not that the whole
application is finished. Paging a retained observation retains its original readiness evidence.

Explicit waits check their condition immediately and recheck on change notifications. Unrelated
DOM activity does not require a quiet interval. Field-property polling still catches updates to
`.value` or `.checked` that do not generate mutation records.

## Automatic form submission

Before the submit step of `call`, the bridge waits within the call's remaining readiness budget
for finite/unclassified requests and the form's visible `aria-busy` states to clear. It exempts only
streams already established when that form call began. A stream opened during field editing is
not automatically known to be background work. Request timing is not proof of causal ownership;
unrelated finite traffic may conservatively delay or withhold automatic submission.

After waiting, the bridge checks the original route, resolves the target again, rechecks requested
and preserved field states, and retains the event-time submit guard. Focus/click handlers and
re-rendered controls cannot silently bypass those field and busy-state checks. If known work stays
unresolved, `BROWSER_FORM_PENDING` reports `progress.submission = "withheld"`. Earlier field handlers
may already have sent writes; withholding submit is not rollback and does not make retry safe.

A native form call binds its form and submission semantics at observation time, before edits.
A unique submit button may initially be disabled. When several buttons submit the form, choose
an observed handle with `submitter`; `form_tools[].submitters` lists the candidates. The choice
never falls back to another enabled action. Whole-form replacement, changed ownership or
submission overrides, and new visible fields require a fresh observation. Equivalent field or
button replacements are accepted only with matching scoped identity and unique semantics.
Hidden protocol values remain app-owned. Private descriptors are bounded to 256 visible members
and 64 KiB; unsupported coverage withholds the call before edits.
The exact owner document is bound even across same-URL frame adoption. Field enabled/disabled
participation is also bound: an initially disabled field becoming enabled requires re-observation,
because it was absent from the original argument and omission contract. Submitter disabled state
remains a readiness condition and may change during preparation.

Preparation prevents native submit events for the bound form from the first edit through cleanup.
The guard follows the form node if page code moves it to another document.
It blurs only the last touched, still-focused field, then focuses the chosen button without
activating it and waits again for observable validation. During activation the guard permits at
most one native submit event for the bound operation. Native validation and button click handlers
are retained; buttonless forms use native `requestSubmit`. Direct `form.submit()`, autosaves and
arbitrary writes from page handlers are outside this event guarantee. This is not a transaction.

`timeout_ms` supplies one remaining budget for preparation readiness, submitter scrolling/focus,
final activation actionability, and a subsequent `wait_for`. Expiry before activation withholds it.
It is not a hard deadline for browser startup, field actionability, an already dispatched action,
or result serialization. `submit=false` finalizes and verifies fields without waiting for application
completion or automatically activating a submitter.

`progress.preparation` is version 1: `phase` describes the last phase reached; `activation` is
`not_dispatched`, `dispatched`, or `unknown`; `native_submissions` and `prevented_submissions`
count observed native events (null when a navigation loses that evidence). A fixed `reason_code`
identifies pending readiness, changed scope, unmatched fields, refused events, preparation,
activation alone, a native submit, or unavailable document evidence. These are distinct
from persistence. `progress.submission` is `withheld` when the final submission was prevented,
even if a click was dispatched. Field contract and passed/blocked write counts are refreshed on
failure too; an earlier autosave still makes retry potentially duplicative.

These are bounded, observable guarantees. Request counts describe HTTP request lifecycles;
WebSocket messages do not establish a finite validation barrier or an application acknowledgement.
Arbitrary future timers, application code that does not expose validation state, and network work
started inside a page's own submit handler cannot be proved complete by this contract. Use an
explicit application condition when stronger readiness is needed. Stream classification never
changes write or origin permissions.

## Additive evidence contract

Existing `outcome` values retain their effect-based meaning for compatibility. In particular,
`succeeded` can mean the page reacted or a control held the requested value. Each action/form
result also includes `evidence.version = 1`:

- `execution` describes whether steps were dispatched, not dispatched, partially applied before a withheld submit, or are unknown.
- `control_verification` is `matched`, `mismatch`, or `unknown`.
- `completion` states its scope and whether a condition was requested. With no explicit condition,
  application completion is `pending` or `unknown`, even if every control was verified.
- A requested `wait_for` produces `completion.scope = "ui_condition"` and `met` or `timed_out`.
  A met UI condition is not an independently verified database commit.
- A policy-blocked write has `completion.scope = "transport"` and `state = "blocked"`, including
  simulated responses that made the page display a success message.

Read completion evidence and retry advice together. Wait or observe after an uncertain effect;
do not repeat a possibly completed write just to recover its result.

## Benchmark transport

`experiments/browser-agent-probe/comparison/transport.py` owns the shared Node relay. Fresh app
cohorts should use the same relay origin and policy for every tool arm, including Lift ablations.
The upstream is a fixed local HTTP origin; the relay supports HTTP and WebSockets in reverse and
forward-proxy modes, and refuses other destinations and nested CONNECT tunnels. Opaque TLS is
unsupported. Configure applications with the public relay origin if they emit absolute URLs;
page contents and Location headers are not rewritten.

Responses stream with backpressure; client cancellation closes the upstream. Request bodies and
both WebSocket directions are retained as binary files with lifecycle events and hashes. Cookies,
HEAD responses, chunked bodies, and trailers are preserved. A WebSocket wire journal is not an
application-level acknowledgement; business grading still needs an independent saved-state oracle.

`BenchmarkProxy.journal()` requires orderly shutdown and a valid final audit record. An audit limit,
missing journal, or child-process failure invalidates the attempt instead of silently scoring it.
All methods, including `WEBSOCKET`, are operator-configured. The default allows only GET and HEAD.
The journal contains synthetic benchmark credentials and raw bodies; keep attempt folders private.

```python
from pathlib import Path
from transport import BenchmarkProxy

proxy = BenchmarkProxy(
    "http://127.0.0.1:29301", Path("attempt/transport"), port=28301,
    methods=("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "WEBSOCKET"),
)
try:
    # Every arm navigates proxy.origin; baseline forward proxies also use proxy.proxy.
    run_attempt(proxy.origin)
finally:
    proxy.shutdown()
journal = proxy.journal()  # Raises if transport auditing was incomplete.
```

Prior evaluation artifacts and the legacy Books/static runner retain their original protocol.
Adopting the relay creates a new harness version; historical timing and fidelity claims are not
retroactively changed.
