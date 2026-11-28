# Rust browser foundation

An experimental, trusted-host library for the next Lift adapter. Rust launches
Chromium, owns its disposable profile/context, routes CDP messages, and dispatches
native input. It does **not** use Playwright at runtime or fall back to it. SIGIL is
not a dependency. The normal Python/Node bridge still defaults to Playwright;
an explicit operator setting selects the independent Rust backend.

The native Rust API is a vertical slice. Its first supported workflow is observing top-document native text fields and
buttons, preparing several text fields, activating Save separately, and letting
the caller verify persistence. A new JavaScript driver binding connects the
shared Lift bridge to the Rust-owned process and transport. Capability conformance
is in progress; neither API is promoted as a production replacement yet.
No model calls are involved in the fixture tests.

The active capability-parity goal and performance gate are tracked in
[PARITY.md](PARITY.md). Performance comparisons must wait until that gate passes.

`../check_conformance.py` runs the same shared correctness cases on both
backends. It rejects skipped, failed, duplicate, or missing test cases and compares
their identities, rather than accepting equal pass counts. CI also runs the
scripted agent and MCP demos explicitly in Rust mode.

## Run

Use a Chromium executable and the pinned Rust toolchain (1.96.1). The Unix launch
implementation supports macOS/Linux; Windows and other browser engines are not
implemented. From this directory:

```sh
cargo test --locked --lib
cargo build --locked --bin viewspec-browser-host
VIEWSPEC_CHROMIUM=/absolute/path/to/chromium \
  cargo test --locked --test browser -- --include-ignored --test-threads=1
VIEWSPEC_CHROMIUM=/absolute/path/to/chromium VIEWSPEC_RUN_RUST_BROWSER_E2E=1 \
  node --test ../../../tests/rust_browser_transport.test.cjs ../../../tests/rust_browser_driver.test.cjs
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
```

Ordinary `cargo test` explicitly marks browser cases ignored. The command above
runs all of them and fails if the executable is missing; there is no silent
browser-test skip. Existing bridge CI supplies Chromium using its locked
Playwright downloader and runs this command. Playwright only provisions the
binary there; the Rust tests do not import or invoke its driver.

The HTTP fixture and its request journal are implemented in Rust. The workflow
asserts exact saved values, Unicode round-tripping, clearing, preservation of an
omitted password/tier, trusted input/change/click events, no fixture writes before
Save, exactly one Save, a real subsequent document read, and session-cookie reuse.
Additional tests cover route changes (including away-and-back), document reload,
control replacement, product identity changes, overlay/disabled/readonly checks,
batch preflight, focus-induced navigation, blur rewrites, cancellation, cross-page
handles, browser exit, and fresh-browser isolation. Transport tests use real socket
pairs for framing, multiplexing, timeouts, cancellation, late replies, malformed
and oversized messages, sanitized errors, and cleanup.

## Private bridge transport

`viewspec-browser-host` exposes a private JSON-lines protocol on stdin/stdout.
`../rust_host.js` is the shared bridge's transport binding. Launch requires an
absolute Chromium executable and optionally the loopback policy proxy. Rust
forces loopback traffic through that proxy and disables QUIC/non-proxied WebRTC
UDP. The host does not accept arbitrary Chromium launch flags or a user profile.

The first message is `launch`; subsequent `send` messages contain a CDP method,
parameters and optional session ID. `close` or stdin EOF tears down the owned
browser. The protocol bounds incoming messages and in-flight commands. Lost or
malformed events close the connection instead of silently weakening evidence.

Events carry a sequence; each command reply carries the sequence of all events
that preceded its CDP response. The JavaScript binding processes those events
before resolving the corresponding promise, even if IPC writer scheduling puts
the response first. This preserves navigation and request-lifecycle ordering
across the extra process boundary. Raw payloads belong to the trusted bridge and
must still pass through the existing policy/privacy layers before public output.

The binding permits 128 in-flight commands and queues at most 256 additional
commands, with a combined queued-payload limit of 16 MiB. Queued work keeps its
original deadline and is sent once, in order, when capacity becomes available.
Disconnect rejects both active and queued work. Close bypasses the active window
and discards work that has not been sent. The Rust host releases a completed
command's capacity before publishing its reply, so the next command cannot race
with retirement of the previous one. The regression suite covers a 300-asset
page, a 300-command burst, queue limits, timeout, disconnect and saturated close.

Transport limits accommodate Lift's existing 10 MiB per-file and 25 MiB batch
upload contract. Commands are bounded at 64 MiB. Incoming Chromium frames are
bounded at 256 MiB because Chromium can duplicate binary bodies as both JSON-escaped
text and base64 entries. When complete binary entries are present, the redundant
text field is removed before forwarding the event over the 128 MiB host IPC
limit; incomplete entries retain the text fallback. Oversized buffers are released
after decoding. A native regression verifies the maximum batch byte-for-byte,
including null and non-UTF-8 bytes, with no submission before explicit Save.

Navigation returns the main document's response, even when scripts and styles
share its loader ID. Finished request records are released; the 300-asset fixture
checks that assets do not replace the document HTML or accumulate completed work.

Build the host before use. `VIEWSPEC_RUST_BROWSER_HOST` can select another built
binary; otherwise the binding uses `target/debug/viewspec-browser-host`. It never
builds on a tool call or falls back to Playwright.

## Shared Lift bridge

From the repository root, after building the host and installing the probe's
locked JavaScript dependencies:

```sh
VIEWSPEC_BROWSER_BACKEND=rust VIEWSPEC_CHROMIUM=/absolute/path/to/chromium \
  python experiments/browser-agent-probe/mcp_browser_server.py \
  --allow-origin https://your-test-app.example \
  --allow-read 'https://your-test-app.example/*'
```

`rust_browser.js` implements the browser surface used by `bridge.js` over the
private Rust host. `rust_dom.js` supplies CDP DOM/input operations. Semantic
choices, editors, preparation, network policy, privacy, and outcome classification
remain in the existing shared JavaScript/Python contracts. Rust mode does not
load Playwright or silently fall back if the host fails.

Chromium's URL-blocking API does not reliably intercept WebSockets. Rust mode
therefore adds an enforced document CSP blocking native socket connections and
installs a WebSocket facade. `rust_socket.js` routes each requested connection
through the shared write policy, then through the mandatory loopback proxy; the
`ws` runtime dependency handles the authorized wire protocol. Tests retain a
native constructor to verify that recovering it cannot bypass the CSP. Worker
execution and popup networking retain the shared bridge's refusal policy.

The IPC accepts optional explicit SHA-256 SPKI pins for isolated TLS fixtures;
it never accepts a blanket certificate-verification bypass. Pins must come from
the trusted host, not an agent command or page.

## Ownership and contract

| Component | Owns | Failure behavior |
| --- | --- | --- |
| `Browser` | Child process, temporary profile, one reusable incognito context | Failed launch/Drop kills and reaps the child; explicit close requests graceful shutdown first |
| `Connection` | NUL-framed CDP pipe, command IDs, pending replies, navigation epochs | Bounded messages and deadlines; no retries; broken framing/EOF closes the connection and fails pending commands |
| `Page` | Serial actions, current observation, opaque control handles | Rejects handles from another page, old observation, navigation or consumed click; interrupted input requires a fresh observation |
| Shared Lift JavaScript | Element refs, semantic identity, cover/inert checks | Rechecks original element and identity; no successor fallback in this slice |
| Rust driver glue | Native enabled/visible/editable checks, focus, selection, input and readback | Refuses unsupported/ambiguous controls; verifies all prepared values after blur handlers |
| Calling host | Authorization, request policy, privacy, business outcome | The shared bridge supplies these layers; the raw Rust/CDP API must not be exposed directly to agents |

CDP uses Chromium's inherited FD 3/4 pipe protocol, carried over a private Unix
socket pair. No debugging TCP port is opened. The browser sandbox is not disabled.
Both helper libraries are embedded at build time and installed once per isolated
execution world/document. Installation is checked again at each observation.
The helper sources are reused without modifying the current bridge.

Controls retain their document/context, navigation epoch, observation, element
reference, native signature and Lift identity. Selectors are resolved only when
observing. The current slice conservatively rejects replacement elements instead
of guessing a successor. Preflight validates the complete requested batch before
focus/input begins; it is not an atomic transaction. A page can change between
protocol calls, and handlers can invalidate later fields. Such failures report
`ActionUncertain` and never trigger rollback or replay.

`PreparationReceipt` reports field readback and `submission_requested: false`.
It does not promise that page input/blur handlers were free of side effects:
autosave is still a write. `ClickReceipt` means input was dispatched, **not** that
an order or save completed. Save consumes its observed control handle even when
its response is lost. Persistence needs independent evidence, as in the fixture.

After cancellation/uncertainty, only a successful fresh observation enables a
new action. This does not authorize a retry or classify the prior business write.
The host must reconcile its evidence before deciding what to do next.

## Integration boundary and remaining work

The public Rust API is for trusted host code. `evaluate` deliberately permits
arbitrary JavaScript and raw results, including secrets. Error messages omit page
payloads, but this is **not** Lift's output privacy projection. The library has no
MCP endpoint and does not accept untrusted workflow modules.

The native action slice by itself does not contain the browser's network channels
or project private output. Use the shared bridge integration for those contracts.
The integration's network/session and MCP fixtures are now being run through the
independent backend; full conformance and real-workflow correctness remain the
gate in [PARITY.md](PARITY.md). A passing subset is not capability parity.

An eventual SIGIL host should expose bounded operations over host-owned handles
and separately granted commit authority. Rust should retain process/session
ownership, network enforcement and actual input. Raw CDP and `evaluate` are not
appropriate guest imports. No speculative SIGIL binding is shipped here.

The next adapter experiment must finish the same conformance and business-workflow judges
against the optimized Playwright backend. Fixture success or Rust implementation
alone is not performance evidence; this slice makes no speed or parity claim.

Protocol references: [Chromium's pipe FD contract](https://chromium.googlesource.com/chromium/src/+/e972c575b9a075ab5dcadddf269d60bb23d4af35),
[CDP](https://chromedevtools.github.io/devtools-protocol/), and
[Chrome's isolated debugging-profile guidance](https://developer.chrome.com/blog/remote-debugging-port).
