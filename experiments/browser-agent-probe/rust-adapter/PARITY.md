# Rust backend capability gate

Goal: complete the current Lift bridge's capabilities before any performance
comparison. Correctness and boundary failures are not timing samples. Unsupported
cases, skipped tests, and fallback to Playwright do not count as parity.

## Architecture

Rust owns Chromium launch/profile lifecycle, the private CDP transport, command
correlation, event ordering and browser input primitives. The existing JavaScript
bridge retains its policy gate and semantic orchestration; Python retains its
planning, privacy projection, bounded responses, observation-qualified handles,
feedback and outcome contracts. The browser-driver binding beneath that shared
bridge must not import, invoke or fall back to Playwright in Rust mode.

The private Rust host protocol is trusted process-to-process plumbing, not an
agent tool or a SIGIL guest API. Any future SIGIL integration belongs above bounded
host operations, with separate commit authority.

## Completion checklist

| Area | Required capabilities and evidence | Status |
| --- | --- | --- |
| Independent foundation | Process/profile ownership, CDP framing/multiplexing, bounded messages, cancellation, native text and click, stale route/control rejection | Implemented; native tests pass |
| Shared bridge integration | Explicit operator-selected backend, streaming private host protocol, ordered events/replies, no runtime Playwright dependency/fallback | Implemented; runtime import guard passes |
| Network boundary | Forced proxy including loopback, DNS/address vetting, blocked UDP bypasses, request/channel classification, redirect checks, CSP and write disposition journal | 29 network cases pass, including tunnel resets; background contacts reduced, one remains (2026-11-28 checkpoint); separate baseline investigation deferred below |
| Session/authentication | Reuse context across opens, cookie/header/storage-state import/export, origin-scoped credentials, private-data learning/projection | Implemented; shared MCP fixtures pass |
| Observation | Composed snapshot, targets/locators, document/SPA epochs, paging/find, bounded metadata/replies, privacy and public SVG fragments | Implemented; shared MCP and bounded-response fixtures pass |
| Actions and preparation | Native fields, checks/selects, keyboard, custom choices, dates/ranges, multi-field form/dialog contracts, explicit Save, stale item/scope refusal | Implemented; combined conformance passes |
| Document structures | Same-origin frames/srcdoc, open shadow roots, nested controls, current actionability and verified successor handling | Implemented for same-origin frames/open shadows; shared fixtures pass |
| Editors | Shared editor detection, normalized values, scoped title/paragraph replacement and omitted-content preservation | Implemented; shared editor fixtures pass |
| Readiness/evidence | Request lifecycle, navigation/load state, scoped waits, feedback watching, resumable evidence and conservative retry classification | Implemented; shared fixtures pass; queued navigation regression added |
| Other browser channels | Dialog dismissal with evidence, popup and worker policy, WebSocket/EventSource policy, downloads, uploads and stray-download cleanup | Shared fixtures and native socket test pass within the declared bridge policy |
| Conformance | Same complete browser/MCP task suite on both backends; zero failures/errors/skips; compare collection counts | 412 identical cases pass on both backends, zero failures/errors/skips (2026-11-28) |
| Real workflow correctness | Scripted workflows through the public Lift API, using the original task values and independent persistence/write oracles; Save remains separate | All three scripted workflows pass: Memos profile, NocoDB create and NocoDB edit |

Current scope is Chromium on macOS/Linux, matching the independent foundation;
Windows launch and other engines are not claimed. Policies that intentionally
refuse a channel (for example worker execution) must reproduce that refusal and
its evidence, not silently enable the channel in pursuit of feature coverage.

## Performance gate

Only after every required capability and shared conformance check above is green
may a new performance campaign begin. Freeze implementation and judges, preserve
the original prompts, use the optimized Playwright baseline, count correctness
first and retain failures. Do not use the earlier partial CDP reader experiment
or this native fixture's execution time as independent-driver performance evidence.

Changes remain on `codex/rust-browser-foundation`; the normal backend is unchanged
until explicit promotion. This checklist records implementation evidence, not an
automatic approval to publish, merge or change the default.

## Current checkpoint — 2026-11-27

The high-level binding now connects the real Lift API to the independent host.
The native suite passes 20 tests, and the JavaScript host/driver suite passes 18,
with zero failures or skips. The latter includes an allowed WebSocket echo with
Unicode, binary data and cookies, denied relay connections, a recovered native
constructor blocked by CSP, reload/history readiness, focus-triggered route
round trips, and queued form-navigation synchronization for clicks and Enter.
Typing covers printable ASCII and Unicode, including literal plus and punctuation
whose character codes differ from keyboard virtual-key codes. Focus and route
changes stop subsequent typed characters instead of redirecting them to another
control.

The NocoDB fixture exposed a startup burst exceeding the transport's active
command limit. Bounded queuing now handles the burst, with explicit count/byte
limits and the original deadlines. Capacity is released before replies become
visible, and close cannot get stuck behind a saturated window. Native regressions
cover 300 concurrent commands and a 300-asset document. The final shared-suite
rerun uses this completed implementation; earlier partial results are not its
final conformance evidence.

Shared-suite progress: the existing 27 network cases passed; 224 form/preparation/readiness/
feedback/bounded-response cases passed; the 186-case MCP/network/choice/editor
run passed 185 and found an intermittent queued-navigation race. After adding
the renderer barrier, all 39 choice cases passed. The direct-agent demo passed
126 checks and the MCP demo passed 100 in explicit Rust mode. The original
410-case Playwright baseline passed. These are correctness tests, not a
performance comparison. The final combined gate and extended channel checks
remain required.

A separate blank-popup issue was observed in the pre-existing Playwright path.
At the user's request, that investigation is deferred. Its unfinished guard and
additional experimental cases are excluded from this change; the existing shared
conformance suite remains intact. Passing that suite does not resolve or disprove
the deferred finding. No broader security assessment is claimed here.

`../check_conformance.py` rejects failures, errors, skips, duplicate cases, or
different case identities between backends. CI now invokes that gate plus both
Rust-mode demos, but GitHub CI has not run for the uncommitted changes.
Formatting, Clippy and the local gate/CI-contract checks pass; 41 CI-shell checks
are Linux-specific and skip on this Mac. No performance campaign has run.

## Checkpoint — 2026-11-28

The final 410-case Rust run of the previous checkpoint failed one case, which the
earlier runs had passed: `bridge.js` exited during a test's first observation. The
cause was in the shared policy gate, not the Rust host. Node's HTTP server removes
its own error listener from a tunnel's socket before emitting `connect`, and the gate
added one only after vetting the destination. A browser that reset a `CONNECT`
during vetting, or after the gate's 403, raised an unhandled `ECONNRESET` that
stopped the bridge. The gate now adds its listener first and drops an upstream
connection it dialled for a browser that has gone. The new
`test_reset_tunnel_does_not_stop_the_bridge` reproduces the exit deterministically
on both backends without the fix.

The Rust backend exposed this because its browser opened tunnels the Playwright
baseline does not. Counted over the 14 bounded-response cases: Playwright 0,
Rust 64 (`www.google.com` 28, `content-autofill.googleapis.com` 22,
`accounts.google.com` 14). Sealed sessions refused them all, but each was a refusal
journal entry and, in a public session, an outside contact. Disabling
`AutofillServerCommunication`, `AimServerEligibilityEnabled` and
`AimServerRequestOnStartupEnabled` leaves 14: one `accounts.google.com/ListAccounts`
per launch, from the account reconciler. Neither `--allow-browser-signin=false` nor
the `AvoidAutoTriggerListAccountsOnStale` feature stops it. `--gaia-url` with a
`.invalid` host does, but it changes how Chrome treats Google's account origin, so
it is not applied.

Results: the conformance gate passes 412 identical cases on both backends (the 410
plus the two reset cases) with zero failures, errors or skips. Native Rust tests
pass 21, the JavaScript host/driver suite 19, and the non-browser Python suite
passes. Three parallel copies of the bounded-response suite, three rounds, produced
2 failures in 9 before the fix and none after.

Open before a performance campaign:

- The backends do not run the same browser. Playwright's headless mode uses
  `chromium_headless_shell` from its own revision (1223 with Playwright 1.60), while
  the Rust host runs the full Chrome for Testing build named by `VIEWSPEC_CHROMIUM`
  (1234 here). Timing comparisons need one build, or the difference stated.
- The reconciler contact above.
- `rust_socket.js` `connect()` holds the tunnelled socket without an error listener
  while it reads cookies, before `ws` takes it over. A reset there would stop the
  bridge the same way. No current test reaches that window.
- `ws` is a runtime dependency of the bridge, and Dependabot does not watch
  `experiments/browser-agent-probe`, so its security updates are by hand.
