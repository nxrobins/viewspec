# ViewSpec Review V0

Status: Proposed
Date: 2026-07-14
Product surface: local SDK and CLI
Working product name: **ViewSpec Review**
Reserved future name: **ViewSpec Studio**

## Summary

ViewSpec Review is a local-first browser review loop for ViewSpec sources. A reviewer opens a
compiled `IntentBundle` or `AppBundle`, points at rendered UI, and sends feedback that is anchored
to stable ViewSpec source identity rather than only to DOM selectors. A coding agent receives a
bounded, machine-readable review batch containing the human request, exact source address,
artifact identity, viewport context, and any completed verifier diagnostics.

The agent edits semantic source, not generated output. ViewSpec validates, recompiles, checks, and
reloads the review while preserving comments across revisions. V0 does not mutate source and does
not claim that human approval is conformance proof.

The product promise is:

> Review the pixels. Repair the intent. Prove the result.

## Why This Product

ViewSpec already owns the difficult substrate required for a source-aware review loop:

- emitted DOM carries `data-ir-id`, content refs, intent refs, binding ids, and action ids;
- `provenance_manifest.json` is checked against the emitted DOM;
- `diff-intent` and `diff-app` describe semantic changes;
- AppBundle declares routes, state, mutations, selectors, replay assertions, and visibility;
- rendered verification produces canonical viewport screenshots, DOM evidence, accessibility
  evidence, stable diagnostics, and deterministic repair plans;
- repair directives already address `ir:<id>` and `screen:<id>/ir:<id>` source paths;
- convergence already records bounded repair lineage and no-progress termination.

What is missing is the product loop connecting those primitives to human judgment. Generic HTML
review tools can identify a rendered element and return prose. ViewSpec can identify the semantic
source of that element, preserve the target across recompilation, combine human feedback with
machine evidence, and verify the regenerated result.

## Goals

V0 must:

1. Open a valid `IntentBundle` or `AppBundle` in a local browser with one CLI command.
2. Let the reviewer switch between normal interaction and annotation without breaking native
   controls.
3. Resolve every compiler-owned annotation to manifest-backed source identity.
4. Deliver feedback to an agent as bounded JSON with at-least-once, acknowledgement-based
   delivery.
5. Bind every feedback event to the exact source, compiler, artifact, and manifest revision the
   reviewer saw.
6. Recompile when source or `DESIGN.md` changes, retain the last good render on failure, and remap
   open annotations by stable source id.
7. Include the latest completed semantic diff and optional verifier result in the agent batch.
8. Remain local-first, loopback-only, no-network, and fail closed at every file and identity
   boundary.
9. Preserve ViewSpec's rule that generated artifacts are outputs, never editing surfaces.
10. Make session end attribution explicit and prevent an agent from reopening a user-ended review
    without an explicit `--reopen`.

## Non-Goals

V0 is not:

- a generic visual website builder or Figma replacement;
- a direct DOM, generated React, CSS, or Tailwind editor;
- an automatic source repair system;
- a pixel-perfect visual regression system;
- accessibility certification or cross-browser certification;
- a hosted sharing, multi-user, presence, approval, or CRDT system;
- a review surface for authenticated or network-dependent arbitrary applications;
- a whiteboard or Mermaid editor;
- a promise that arbitrary imported HTML has ViewSpec-native provenance;
- permission to install packages, open network connections, or publish artifacts implicitly.

Typed source patch proposals are now the additive IntentPatch V1 contract; state time travel,
cross-emitter synchronized review, hosted sharing, and arbitrary HTML compatibility remain follow-on
capabilities described later in this document.

## Users and Jobs

### Reviewer

The reviewer wants to point at the result, explain what should change, and know that the agent will
receive enough context to change the correct source construct.

### Coding agent

The agent wants a compact batch of stable source references, user language, current revision
identity, and machine diagnostics without needing to infer the target from a screenshot.

### Application author

The author wants reviewers and agents to modify only `viewspec.intent.json`, `viewspec.app.json`, or
`DESIGN.md`, and wants every regenerated artifact to remain checked and attributable.

## Core Workflow

```text
IntentBundle/AppBundle
        |
        | validate -> compile -> check
        v
local review revision + provenance manifest
        |
        | browser inspect / interact / annotate
        v
manifest-validated review events
        |
        | acknowledged long poll
        v
agent receives ReviewBatch
        |
        | edits semantic source
        v
semantic diff -> recompile -> check -> optional verify
        |
        `---- browser reloads; annotations remap by source id
```

The minimum useful round-trip is:

```bash
viewspec review viewspec.app.json
viewspec review-poll viewspec.app.json --json
```

After the agent receives a batch, the next poll acknowledges it and may send a reply:

```bash
viewspec review-poll viewspec.app.json \
  --ack vrb_0123456789abcdef0123456789abcdef \
  --agent-reply "I updated the density and moved the incident summary above the timeline." \
  --json
```

When the agent is finished:

```bash
viewspec review-end viewspec.app.json --json
```

## CLI Contract

### `viewspec review`

```text
viewspec review SOURCE
  [--design DESIGN.md]
  [--target html-tailwind|html-tailwind-app|react-tailwind-app]
  [--verify]
  [--install]
  [--no-open]
  [--port PORT]
  [--state-dir PATH]
  [--reopen]
  [--json]
```

Behavior:

- `SOURCE` must be one canonical local `IntentBundle` or `AppBundle` JSON file in V0.
- An `IntentBundle` defaults to `html-tailwind`.
- An `AppBundle` defaults to the local, no-install `html-tailwind-app` target. The caller may
  explicitly select `react-tailwind-app` when its runtime dependencies are available.
- The command validates, compiles into a private session revision directory, and runs `check`
  before the browser is considered ready.
- `--verify` runs the existing canonical viewport verifier and surfaces only a completed result.
  It does not weaken or replace the mandatory compile/check gate.
- `--install` has the same explicit dependency-install meaning and restrictions as existing proof
  and verification commands. `--verify` never implies `--install`.
- The source file and explicitly selected `DESIGN.md` are watched. V0 does not recursively watch
  arbitrary workspace files.
- A second invocation resumes an active session only when the complete session configuration
  identity matches; a different design, target, compiler, contract profile, plugin registry,
  state directory, bound port, or verification plan fails with
  `REVIEW_SESSION_CONFIGURATION_CONFLICT`.
- A user-ended session refuses to reopen unless `--reopen` is supplied.
- `--no-open` creates or resumes the server and session without launching a browser.
- Human output prints the local URL, source kind, target, revision, check status, verifier status,
  and the exact poll command.

Successful JSON output:

```json
{
  "schema_version": 1,
  "ok": true,
  "summary": "ViewSpec review is ready.",
  "diagnostics": [],
  "external_refs": [],
  "paths": {},
  "errors": [],
  "next_actions": [
    "Open the local review URL and inspect the compiled interface.",
    "Run viewspec review-poll viewspec.app.json --json to wait for feedback."
  ],
  "metadata": {
    "sdk_version": "0.3.0b4",
    "network_calls": "none"
  },
  "review": {
    "review_id": "vrw_0123456789abcdef0123456789abcdef",
    "status": "active",
    "source_kind": "app_bundle",
    "target": "html-tailwind-app",
    "revision": 1,
    "check_status": "passed",
    "verification_status": "not_run",
    "url": "http://127.0.0.1:4388/open/<single-use-bootstrap-token>"
  }
}
```

The bootstrap token is sensitive local session material. It appears only in the immediate
`viewspec review` result, expires after 60 seconds, and is consumed by the first valid navigation,
which sets a host-only session cookie and redirects to a token-free review URL. The token must not
be written into later feedback events, status output, proof artifacts, support bundles,
review-controlled browser storage, referrers, or logs.

### `viewspec review-poll`

```text
viewspec review-poll SOURCE
  [--ack BATCH_ID]
  [--agent-reply TEXT]
  [--timeout-ms MILLISECONDS]
  [--json]
```

Behavior:

- The command resolves the active session by canonical source path; callers do not need to retain
  an opaque review id.
- It long-polls until human feedback, a user-ended session, a compile/check failure, or a newly
  completed verifier result is available.
- A returned batch remains pending until a later call supplies its exact `--ack` value.
- Without acknowledgement, the same batch is redelivered with the same batch and event ids.
- An issued, unacknowledged batch is immutable; later events wait for a later batch.
- Events therefore have **at-least-once** delivery. Consumers deduplicate by `event_id`.
- `--ack` is applied before waiting for the next batch. Unknown, stale, or out-of-order batch ids
  fail closed.
- `--agent-reply` is accepted only in the same call as a valid `--ack`. It is shown in the browser
  conversation and is not treated as human feedback.
- `--timeout-ms` must be an integer from 1 through 55,000. A timeout returns `status: "timeout"`
  without acknowledging or deleting any event; an out-of-range value returns CLI exit `2` with
  `REVIEW_REQUEST_INVALID`.
- Interrupting the command does not lose or acknowledge feedback.
- V0 permits exactly one active delivery lease per session; a concurrent CLI or MCP poll fails with
  `REVIEW_POLL_CONFLICT`.

### `viewspec review-end`

```text
viewspec review-end SOURCE [--json]
```

Behavior:

- Ends the session with `ended_by: "agent"`.
- Any already queued human feedback remains deliverable and must be acknowledged.
- Agent-ended sessions may be resumed normally.
- A reviewer may instead choose **Send & End** or **End review** in the browser. Such sessions use
  `ended_by: "human"` and require `--reopen` for a later browser open.
- The local server exits within 5 seconds after it has no valid browser capability, browser, poll,
  or in-flight mutation; durable ended and resumable session state remains on disk.

### `viewspec review-status`

```text
viewspec review-status [SOURCE] [--json]
```

Lists the selected session, or all local sessions when no source is provided. Output must not
include capability tokens, absolute source paths in JSON mode, raw feedback bodies, or local
environment data.

## Review UI

The browser contains three regions:

1. **Toolbar** — revision, route/screen, viewport, explore/annotate mode, verifier status, reload,
   and end controls.
2. **Artifact frame** — the exact checked revision served with an ephemeral review SDK injected in
   transit. The stored compiled artifact remains byte-identical and unmodified.
3. **Review panel** — source trace, selected text, annotation composer, queued feedback,
   conversation, diagnostics, and stale-target state.

### Explore and annotate modes

- Explore mode preserves native controls, links, form inputs, declared actions, and AppBundle
  interactions.
- Annotate mode intercepts pointer selection for compiler-owned nodes.
- `Cmd+I` on macOS and `Ctrl+I` elsewhere toggles modes even when focus is inside a form control.
- Keyboard users can move through manifest-backed elements in document order and open the same
  annotation composer without a pointer.
- The active mode, selected node, and queued count are exposed through accessible text, not color
  alone.

### Source trace

For a selected compiler-owned element, the panel shows the available chain:

```text
DOM id
  -> screen id / IR id
  -> binding, action, motif, or region intent refs
  -> content refs
  -> source revision
  -> artifact and manifest hashes
```

The client may suggest a target, but the server must rebuild the target from the checked manifest.
Client-supplied provenance arrays are never trusted.

If the clicked child has no direct manifest entry, the SDK walks to the closest manifest-backed
ancestor and records `target_resolution: "ancestor"`. If no source-backed ancestor exists, the UI
offers a page-level annotation with `target_resolution: "page"`; it never invents an IR id.

### Selected text

Selected-text feedback is anchored within one manifest-backed IR node and records:

- exact selected text, capped and normalized without changing its visible characters;
- bounded prefix and suffix context;
- selected-text SHA-256;
- owning source target;
- revision and viewport context.

DOM range offsets are browser hints only and are not the durable anchor. After recompilation, the
reviewer sees whether the quote is `exact`, `moved`, `ambiguous`, or `stale` within the same source
node.

### Annotation kinds

V0 supports four human event kinds:

- `change_request`
- `question`
- `approval`
- `note`

Approval is a human review event. It is not a ViewSpec proof, verification result, merge approval,
or authorization for an agent to publish or deploy anything.

### Revision changes

When a watched source changes, the server assigns a monotonically increasing observed generation.
It waits for 250 ms of quiet, but no longer than 2 seconds from the first observation, before
capturing one immutable source/design snapshot:

1. Read and hash the complete source and design bytes once.
2. Validate it through the existing public contract.
3. Compile into a new immutable session revision directory.
4. Run `check` against that exact artifact.
5. Compute the semantic diff from the previous successful source.
6. Promote the revision only after all mandatory steps pass and only if no later generation has
   been observed.
7. Reload the artifact frame and apply only the bounded, identity-checked route and scroll fallback
   defined in Constraints & Fallbacks.
8. Remap annotations by `(screen_id, ir_id)` or `ir_id`.

If validation, compilation, or check fails, the last successful revision remains visible. The UI
shows the failed candidate source hash and stable error code without claiming it is the displayed
revision. The failure becomes a pollable machine event. No partial candidate artifact is served.

Targets remap as:

- `exact` — the same source path exists in the new manifest;
- `changed` — the path exists and the semantic diff reports a relevant change;
- `stale` — the path no longer exists;
- `page` — the original annotation was page-level.

V0 never guesses a replacement source node for a stale target.

Validation, compilation, check, diff, and verification consume the captured snapshot or files
materialized from it; no stage reopens the watched source pathname. A superseded candidate is
discarded with `REVIEW_REVISION_SUPERSEDED` and can never become the promoted head.

## Source and Revision Identity

Review runtime state is intentionally stateful and is not part of ViewSpec's deterministic compiler
output promise. Every promoted revision is nevertheless bound to deterministic identities:

```json
{
  "revision": 3,
  "source_kind": "app_bundle",
  "source_sha256": "<64 lowercase hex>",
  "design_sha256": "<64 lowercase hex or null>",
  "compiler": {
    "package_version": "0.3.0b4",
    "contract_profile": "local_v1"
  },
  "artifact": {
    "target": "html-tailwind-app",
    "artifact_set_sha256": "<64 lowercase hex>",
    "root_manifest_kind": "shell_manifest",
    "root_manifest_sha256": "<64 lowercase hex>",
    "semantic_digest": "<existing digest or null>"
  },
  "check_status": "passed"
}
```

For an IntentBundle, `root_manifest_kind` is `provenance_manifest`. For an AppBundle it is
`shell_manifest` or the corresponding checked outer app manifest. `artifact_set_sha256` is a
domain-separated digest over the ordered, allowlisted file paths and hashes in the promoted
revision. Each source-node event also records the exact per-screen `provenance_manifest` hash used
to validate its target.

`review_id`, `event_id`, and `batch_id` are cryptographically random 128-bit ids with the `vrw_`,
`vre_`, and `vrb_` prefixes. Random review ids do not weaken compiler determinism because session
state is not a generated UI artifact or proof result.

The canonical absolute source path is stored only in the private local session index. JSON returned
to agents uses the basename plus source hashes and never relies on the basename for identity.

## ReviewBatch V1

`review-poll --json` returns the standard ViewSpec tool envelope. Its `batch` field contains one
`ReviewBatch`:

```json
{
  "schema_version": 1,
  "review_id": "vrw_0123456789abcdef0123456789abcdef",
  "batch_id": "vrb_0123456789abcdef0123456789abcdef",
  "status": "feedback",
  "delivery": {
    "first_sequence": 8,
    "last_sequence": 9,
    "requires_ack": true,
    "redelivered": false
  },
  "revision": {
    "number": 3,
    "source_kind": "app_bundle",
    "source_sha256": "<64 lowercase hex>",
    "design_sha256": null,
    "target": "html-tailwind-app",
    "artifact_set_sha256": "<64 lowercase hex>",
    "root_manifest_kind": "shell_manifest",
    "root_manifest_sha256": "<64 lowercase hex>",
    "compiler_version": "0.3.0b4",
    "contract_profile": "local_v1"
  },
  "events": [
    {
      "schema_version": 1,
      "event_id": "vre_0123456789abcdef0123456789abcdef",
      "sequence": 8,
      "actor": "human",
      "kind": "change_request",
      "body": "Make this summary denser and move it above the timeline.",
      "target": {
        "kind": "source_node",
        "screen_id": "incident_detail",
        "ir_id": "motif_incident_summary",
        "source_ref": "screen:incident_detail/ir:motif_incident_summary",
        "dom_id": "dom-motif_incident_summary",
        "binding_id": null,
        "action_id": null,
        "intent_refs": ["viewspec:motif:incident_summary"],
        "content_refs": [],
        "provenance_manifest_sha256": "<64 lowercase hex>",
        "target_resolution": "exact"
      },
      "context": {
        "route": "/incident",
        "viewport": {
          "name": "desktop",
          "width": 1440,
          "height": 1000
        },
        "selected_text": null,
        "control_values": {}
      }
    }
  ],
  "semantic_diff": {
    "status": "available",
    "from_revision": 2,
    "to_revision": 3,
    "summary": []
  },
  "verification": {
    "status": "not_run",
    "verification_id": null,
    "result_sha256": null,
    "diagnostics": [],
    "repair_plan": null
  },
  "source_failure": null,
  "end": null,
  "next_step": "Apply feedback to semantic source, recompile, and acknowledge this batch on the next poll."
}
```

### Batch statuses

- `feedback` — contains one or more human events.
- `source_failed` — the watched candidate failed validation, compile, or check.
- `verification_completed` — a new completed verifier result is available.
- `ended` — the human or agent ended the session and no earlier batch is awaiting acknowledgement.
- `timeout` — only when the caller supplied a timeout; never requires acknowledgement.

One batch may include human events and the latest completed verifier snapshot. A batch never mixes
events from different promoted revisions. If feedback arrives while a candidate compile is in
progress, it remains anchored to the currently displayed promoted revision.

For `source_failed`, `revision` continues to identify the displayed last-good revision and
`source_failure` contains the candidate source hash, failing stage, original bounded ViewSpec
errors, and retry guidance. Candidate source bytes and partial artifacts are never included. For
`ended`, `end` contains the ending actor, final event sequence, and whether an acknowledgement is
still required.

### Context capture

V0 captures only bounded, review-relevant context:

- current route up to 2 KiB UTF-8 or screen id up to 128 UTF-8 bytes when known;
- one of the three named canonical viewports and its exact integer dimensions;
- selected-text quote up to 4 KiB with at most 512 bytes each of prefix and suffix;
- values of native controls inside the selected manifest-backed form or question scope, capped and
  redacted by input type;
- one visibility marker for the selected source node from `visible`, `hidden`, or `not_rendered`;
- at most 64 completed verifier evidence references, each at most 256 UTF-8 bytes and 16 KiB
  aggregate.

V0 never captures password values, file inputs, browser storage, cookies, authorization headers,
environment variables, arbitrary console logs, full document HTML, or unbounded application state.
Full declared AppBundle state snapshots and replay checkpoints are a V1 capability.

## Machine Diagnostics

Compile and check are mandatory session gates. Canonical viewport verification is optional in V0
because it may require Node, browser dependencies, and an explicit install decision.

When verification runs:

- Review reuses the existing `VerificationResult` and `VerificationRepairPlan` contracts.
- Only a complete, hash-checked result is surfaced.
- A result is current only when its revision number, source hash, artifact-set hash, manifest hash,
  plan hash, verifier version, and viewport definitions match the displayed revision exactly.
- Diagnostics remain addressed by stable `source_ref` and viewport.
- Human comments and machine diagnostics remain distinct arrays; Review does not rewrite one into
  the other.
- The batch may provide a presentation-level merged ordering, but the underlying identities and
  severities stay unchanged.
- A human approval cannot suppress an error-level verifier diagnostic.
- An indeterminate result is reported as indeterminate and does not block human review of the last
  checked artifact.

The review UI labels these outcomes precisely:

- **Checked** — compilation and provenance check passed.
- **Conformant** — the selected verification plan completed with a conformant result.
- **Needs repair** — verification completed nonconformant.
- **Verification unavailable** — verification was not run or was indeterminate.

## Persistence and Delivery

State defaults to `~/.viewspec/review/` and may be overridden by
`VIEWSPEC_REVIEW_STATE_DIR` or `--state-dir`.

Each session stores:

- a private session index and capability digest;
- immutable promoted revision directories;
- an append-only event journal;
- batch delivery and acknowledgement state;
- agent replies;
- end attribution;
- bounded completed verification references.

Event records use length-delimited, SHA-256-framed append records capped at 24 KiB and are flushed
and `fsync`-committed before the browser receives success. Event journal recovery validates every
frame and either restores every committed event or fails the session with
`REVIEW_JOURNAL_INVALID`; it never truncates or skips a malformed tail automatically.

No event is deleted on delivery. Acknowledgement advances the single V0 agent-consumer cursor only
after any same-request agent reply is durably committed. Active journals compact after 256
acknowledged events or 8 MiB, retain all unacknowledged events, retain event identity metadata, and
fail without replacing the old journal if compaction cannot complete in 2 seconds.

## Constraints & Fallbacks

This matrix is normative and contains the **Existential Threats** that require an engineered
constraint. `KiB` means 1,024 bytes, `MiB` means 1,048,576 bytes, every stated maximum is inclusive,
and an implementation must accept a valid value at the limit and reject limit-plus-one exactly as
specified.

Every limit is checked before allocation, mutation, promotion, or acknowledgement. Raw transport
limits count received bytes, serialized limits count compact UTF-8 JSON bytes, and no failure may be
silently weakened into partial success. Process-lifetime durations and deadlines use a monotonic
clock; wall-clock timestamps are presentation and retention data only.

### Existential Threats — Revision and identity

| Failure class | The boring limit | Fail-fast mode |
|---|---|---|
| Mutable input / TOCTOU | IntentBundle source retains its 256 KiB limit, AppBundle source retains its 1 MiB limit, `DESIGN.md` is capped at 64 KiB, and aggregate captured input is capped at 1,114,112 bytes. Each revision opens each pathname once, reads the descriptor once into immutable bytes, and requires its device, inode, byte size, nanosecond modification time, and nanosecond change time to match before and after the read and the pathname to still identify that descriptor. | An oversized input returns CLI exit `2` with `REVIEW_SOURCE_TOO_LARGE`; any changed identity, size, or timestamp returns CLI exit `2` or HTTP `409` with `REVIEW_SOURCE_CHANGED_DURING_CAPTURE`, and no candidate is created. |
| Source churn and stale promotion | Watch changes are coalesced after 250 ms of quiet and at most 2 seconds after the first observation; each session has one in-flight candidate and a monotonically increasing 64-bit observed generation. A candidate whose generation is not the newest observed generation is physically ineligible for promotion. | The candidate worker terminates with `REVIEW_REVISION_SUPERSEDED`; a synchronous waiter receives HTTP `409` or CLI exit `2`, while a background rebuild records the same code in session diagnostics and returns no success event. |
| Revision phase hangs | Snapshot gets 5 seconds, validation 5 seconds, compilation 30 seconds, check 10 seconds, and semantic diff 10 seconds, with a 60-second total promotion deadline. Optional verification retains the existing 180-second total deadline. | The phase is terminated and returns CLI exit `2` or HTTP `504` with `REVIEW_SNAPSHOT_TIMEOUT`, `REVIEW_VALIDATE_TIMEOUT`, `REVIEW_COMPILE_TIMEOUT`, `REVIEW_CHECK_TIMEOUT`, `REVIEW_DIFF_TIMEOUT`, or `REVIEW_VERIFICATION_TIMEOUT`; the last promoted revision remains current. |
| Session configuration collision | One canonical source may have one active configuration tuple: source identity, design identity, target, compiler version, contract profile, plugin registry, state directory, bound port, and verification-plan hash. No option in that tuple is defaulted from or overwritten by an existing session. | A mismatching invocation returns CLI exit `2` or HTTP `409` with `REVIEW_SESSION_CONFIGURATION_CONFLICT`; it does not resume, mutate, or create a second implicit session. |
| Cross-revision identity mixing | Every target, event, diff, check, verifier result, evidence reference, route context, and control context carries one exact revision number and its source, artifact-set, and root-manifest hashes. A current status may reference only the displayed revision. | Any internal mismatch marks the session `degraded`, suppresses the mixed field, and returns HTTP `500` or CLI exit `1` with `REVIEW_REVISION_IDENTITY_MISMATCH`; stale data is never shown as current. |
| Stale or oversized verification | A verification result is accepted only for the exact revision, artifact-set hash, plan hash, verifier version, and exactly the 3 canonical viewports; evidence retains the existing 5 MiB per-file and 20 MiB aggregate caps. Its Review projection permits at most 64 diagnostics of 2 KiB each and 96 KiB aggregate plus 64 evidence refs of 256 bytes each and 16 KiB aggregate; at most one verifier runs globally, and only the newest completed result per revision is retained. | A nonmatching result uses HTTP `409` or CLI exit `2` and `REVIEW_VERIFICATION_STALE`; timeout uses HTTP `504`, evidence overflow HTTP `507`, and projection overflow HTTP `422`, all with CLI exit `2` and `REVIEW_VERIFICATION_TIMEOUT` or `REVIEW_VERIFICATION_FAILED` as applicable, without changing current revision status. |
| Semantic diff explosion | A semantic-diff Review projection contains at most 128 entries, 1 KiB compact UTF-8 JSON per entry, and 64 KiB total, computed only from the exact previous and candidate captured source hashes. The full projection and its SHA-256 must exist before promotion. | Overflow or identity mismatch terminates the candidate with HTTP `422` or CLI exit `2` and `REVIEW_DIFF_TOO_LARGE` or `REVIEW_REVISION_IDENTITY_MISMATCH`; the last-good revision remains current and no truncated diff is labeled complete. |
| Source aliases and replacement | Source and design inputs must be regular files owned and readable by the effective user, not symlinks, and have a link count of exactly one; active sessions are compared with the OS same-file primitive to prevent aliases. An atomic editor save may replace the inode only at the same canonical pathname and is treated as a new observed generation. | Symlinks, hard links, non-regular files, ownership mismatch, or a same-file collision return CLI exit `2` with `REVIEW_FILESYSTEM_UNSAFE`; no session or revision is created. |
| Ambiguous manifest identity | A revision may expose at most 4,096 annotatable manifest nodes, with unique DOM ids and unique `(screen_id, ir_id)` pairs; a stable pair may not change primitive kind or semantic ref family across revisions. Target intent/content refs are capped at 32 each, 256 UTF-8 bytes per ref, and 8 KiB aggregate. | Duplicate, missing, oversized, or meaning-reused identity prevents readiness with CLI exit `2` or HTTP `422` using `REVIEW_MANIFEST_AMBIGUOUS` or `REVIEW_TARGET_LIMIT_EXCEEDED`; a forged target returns HTTP `422` with `REVIEW_TARGET_NOT_IN_MANIFEST`. |
| Ancestor and unsupported target resolution | Ancestor lookup is limited to 32 light-DOM ancestors and may not leave the compiler-owned `[data-viewspec-root]`; node annotations inside Shadow DOM, nested frames, canvas, WebGL, or outside that root are zero-supported in V0. The reviewer may explicitly choose a page-level annotation instead. | Unsupported node targeting returns HTTP `422` with `REVIEW_TARGET_UNSUPPORTED`; the server never substitutes the root or another node automatically. |
| Selected-text ambiguity | A selection is capped at 4 KiB with 512-byte prefix and suffix, must remain inside one manifest-backed IR node, and must have exactly one quote match to be remapped. Zero matches become `stale` and more than one becomes `ambiguous`; neither state is re-anchored. | Cross-node or oversized initial selection returns HTTP `422` with `REVIEW_SELECTION_UNSUPPORTED`; recompilation ambiguity is surfaced as state and never converted to `exact` or `moved`. |
| Context envelope | Event context is a closed object containing at most one 2 KiB route, one 128-byte screen id, one canonical viewport, one bounded selection, 16 review-safe controls, one visibility enum, and 64 bounded evidence refs; revision and manifest identity are rebuilt server-side. Unknown keys and client-supplied identity hashes are never retained. | Any unknown, oversized, noncanonical, or contradictory context rejects the entire event with HTTP `422` and `REVIEW_CONTEXT_FORBIDDEN`; no sanitized subset is journaled. |
| Viewport misstatement | V0 exposes exactly `mobile` 390×844, `tablet` 768×1,024, and `desktop` 1,440×1,000 CSS pixels; the artifact frame's measured `innerWidth` and `innerHeight` must match the selected pair within 1 CSS pixel before annotation. V0 does not emulate device pixel ratio, browser chrome, virtual keyboards, safe areas, zoom, or non-Chromium engines. | A size mismatch disables annotation and every context submission for that frame and returns HTTP `422` with `REVIEW_VIEWPORT_MISMATCH`; it never records the declared viewport as observed context. |
| Reload context carryover | A route string is capped at 2 KiB UTF-8 and is retained only when the same route ID and path exist byte-for-byte in the new checked AppBundle; scroll coordinates must be integers from 0 through 1,000,000 and are restored only after the new frame handshakes within 5 seconds. | Any failed condition resets to the declared default route and `(0, 0)`, records `REVIEW_CONTEXT_RESET` in session status and the review UI, and forbids later events from claiming the discarded route or coordinates. |

### Existential Threats — Delivery and persistence

| Failure class | The boring limit | Fail-fast mode |
|---|---|---|
| Event and reply size | A human body or agent reply is capped at 8 KiB UTF-8, one serialized event at 24 KiB, one session at 1,024 human events, and unacknowledged events at 256. An event may include at most 16 review-safe control values of 256 bytes each and 4 KiB aggregate. | Oversized HTTP bodies return `413` with `REVIEW_REQUEST_TOO_LARGE`; oversized events return `413` with `REVIEW_EVENT_TOO_LARGE`, and exhausted event counts return `429` with `REVIEW_EVENT_LIMIT_EXCEEDED` without accepting any bytes as an event. |
| Submission retries | Every event submission requires a 32-lowercase-hex idempotency key retained with session metadata; official clients generate all 128 bits with Web Crypto or the operating system random generator, but authorization never depends on the key being secret. A repeated key with the identical canonical request SHA-256 returns the original event ID, sequence, and status without appending; reuse with different bytes is a conflict. | A missing or malformed key returns HTTP `400` with `REVIEW_IDEMPOTENCY_REQUIRED`; conflicting reuse returns HTTP `409` with `REVIEW_IDEMPOTENCY_CONFLICT`, and neither request reaches the journal. |
| Durable event acceptance | Each accepted event is one length-delimited frame of at most 24 KiB with SHA-256, flushed and `fsync`-committed before success; first journal creation also syncs the parent directory, and the journal is capped at 16 MiB. The browser may display **Sent** only after the commit response. | Write, flush, or sync failure returns HTTP `507` with `REVIEW_JOURNAL_WRITE_FAILED`; the UI keeps the annotation unsent and the server does not allocate an event sequence. |
| Batch construction | One batch contains at most 8 events and 240 KiB serialized output, leaving 16 KiB for its tool envelope, and an issued unacknowledged batch is byte-immutable. The builder packs sequential events until the next full event would exceed the byte cap, then includes a complete diff or verification projection only if it still fits; deferred events and projections wait intact for later batches. | An internal overflow returns HTTP `500` or CLI exit `1` with `REVIEW_BATCH_TOO_LARGE`, marks the session `degraded`, and leaves the delivery cursor unchanged; no event, diff, or verification object is truncated. |
| Concurrent polling | Each session permits one delivery lease, one outstanding batch, and one long poll of at most 55 seconds; no SSE client or second CLI/MCP consumer may share that lease. A normal timeout returns one `status: "timeout"` response and releases the lease. | A concurrent consumer returns HTTP `409` or CLI exit `2` with `REVIEW_POLL_CONFLICT`; it receives no batch and cannot acknowledge. |
| Acknowledgement and reply atomicity | Acknowledgement may name only the exact outstanding batch; a reply of at most 8 KiB is accepted only with that acknowledgement and is durably committed before the cursor advances. The batch ID plus canonical reply SHA-256 is the transaction identity: an identical retry reuses the stored commit and polls from the current cursor without creating a second reply, while the same batch with different reply bytes is never accepted. | Unknown, stale, forward, or conflicting acknowledgement returns HTTP `409` with `REVIEW_ACK_INVALID` or `REVIEW_ACK_OUT_OF_ORDER`; reply persistence failure returns HTTP `507` with `REVIEW_JOURNAL_WRITE_FAILED` and leaves the batch unacknowledged. |
| End ordering | Human **Send & End** requires a 32-lowercase-hex idempotency key and commits the final feedback events and one end record in the same journal transaction; an identical retry returns its stored result, and agent end is serialized after the latest accepted sequence. No browser event is accepted after durable end, and human end invalidates browser capability immediately. | Any later or conflicting browser mutation returns HTTP `409` with `REVIEW_SESSION_ENDED` or `REVIEW_IDEMPOTENCY_CONFLICT`; failure to commit the final transaction returns HTTP `507` with `REVIEW_JOURNAL_WRITE_FAILED` and leaves the session active with the draft visibly unsent. |
| Journal recovery | Startup scans at most the 16 MiB journal and validates every length and SHA-256 frame; zero automatic truncation or salvage is supported. Recovery gets 2 seconds per session. | Any malformed frame or recovery timeout marks the session `corrupt` and returns CLI exit `2` or HTTP `500` with `REVIEW_JOURNAL_INVALID`; no later event, acknowledgement, or promotion is allowed. |
| Active compaction and retention | Compaction runs after 256 acknowledged events or 8 MiB, gets 2 seconds, preserves every unacknowledged event, and replaces the journal only after the compacted file and parent directory are synced. Ended-session bodies live 7 days and identity/ack/end metadata 30 days; an unended suspended session lives 30 days after last activity, then its entire directory is purged. | Compaction failure keeps the old journal and exposes `REVIEW_COMPACTION_FAILED` in the next status or poll response; at 16 MiB, new events return HTTP `507` with `REVIEW_JOURNAL_FULL`, and expired retention is reported as `REVIEW_SESSION_NOT_FOUND` rather than partially restored. |
| Error payload ambiguity | Tool errors use only `source_errors`, capped at 32 issues, 2 KiB per issue, and 64 KiB aggregate; the alternative `cause` field is forbidden in Review V0. Every failure still returns one top-level stable `REVIEW_*` code. | A larger projection returns HTTP `500` or CLI exit `1` with `REVIEW_ERROR_REPORT_TOO_LARGE`, observed issue count, and failing stage; it does not return a partial list as if complete. |

### Existential Threats — Server, security, and resources

| Failure class | The boring limit | Fail-fast mode |
|---|---|---|
| Session and capability lifetime | The state store permits 16 active and 64 total retained sessions; activity means a successful authenticated browser request, an open browser connection, or an active agent poll, and after 30 idle minutes the session becomes suspended while filesystem events do not reset that timer. A bootstrap token has 128 random bits and lives 60 seconds, the host-only `HttpOnly; SameSite=Strict` session cookie has 128 random bits and lives at most 8 hours or 30 idle minutes, and a 128-bit read-only frame ticket is revision-scoped and lives 5 minutes; explicit resume or reopen rotates all applicable values. | A 17th active or 65th retained session returns CLI exit `2` with `REVIEW_SESSION_LIMIT_EXCEEDED`; reused, expired, ended, unknown, or malformed capability material returns HTTP `403` with `REVIEW_CAPABILITY_INVALID`, and a suspended session requires a new explicit CLI invocation. |
| Request framing | A request permits at most 64 headers, 16 KiB aggregate header bytes, 8 KiB per header, and a 2 KiB URI; every mutating request requires exactly one decimal `Content-Length`, absent or exact `Content-Encoding: identity`, and a body of at most 256 KiB, so chunked or compressed mutations are unsupported. Header and body reads each get 5 seconds, and ordinary handlers get 10 seconds. | The server returns HTTP `431`, `414`, `411`, `415`, `413`, or `408` with `REVIEW_REQUEST_HEADERS_TOO_LARGE`, `REVIEW_REQUEST_URI_TOO_LONG`, `REVIEW_REQUEST_LENGTH_REQUIRED`, `REVIEW_REQUEST_INVALID`, `REVIEW_REQUEST_TOO_LARGE`, or `REVIEW_REQUEST_TIMEOUT` before parsing or allocating the rejected payload. |
| JSON structural complexity | A JSON request must be UTF-8 `application/json`, at most 16 containers deep and 4,096 total values, with arrays capped at 256 items, object keys at 128 UTF-8 bytes, strings at 8 KiB unless a field has a lower limit, signed integers in the 64-bit range, and no duplicate keys, floats, `NaN`, or infinities. Parsing uses the 256 KiB request buffer and may allocate at most 1 MiB for the decoded value graph. | Any content-type, encoding, duplicate-key, depth, count, numeric, or allocation-limit violation returns HTTP `400` or `415` as applicable with `REVIEW_REQUEST_INVALID`; the handler receives no partial value and performs no mutation. |
| Origin and frame authorization | Every request requires `Host: 127.0.0.1:<bound-port>`; browser mutations additionally require `Origin: http://127.0.0.1:<bound-port>`, `Sec-Fetch-Site: same-origin`, the session cookie, and the current 128-bit revision/frame nonce, with absence treated as failure. The artifact iframe uses `sandbox="allow-scripts allow-forms"` without same-origin, top-navigation, popup, or download permission. | Any missing or mismatched authorization value returns HTTP `403` with `REVIEW_REQUEST_FORBIDDEN`; a stale revision nonce returns HTTP `409` with `REVIEW_REVISION_MISMATCH`. |
| Instrumentation non-interference | The packaged review SDK plus CSS is capped at 512 KiB and identified by one SHA-256; it may read compiler-owned nodes and post bounded messages but may render only in the parent chrome, never inside `[data-viewspec-root]`. The frame must complete the SDK handshake in 5 seconds, and the parent accepts messages only from the exact frame window carrying its current nonce. | Asset mismatch, CSP failure, prohibited root mutation, or handshake timeout makes readiness fail with CLI exit `2`; later mutation attempts return HTTP `409`, using `REVIEW_INSTRUMENTATION_VIOLATION`, `REVIEW_SECURITY_POLICY_FAILED`, or `REVIEW_BROWSER_HANDSHAKE_TIMEOUT` exactly. |
| External network surfaces | The chrome, SDK, and artifact are allowed zero non-loopback requests and zero remote runtime references; chrome CSP is `default-src 'none'` with hash-bound scripts/styles, `img-src 'self' data:`, `connect-src 'self'`, `frame-src 'self'`, and all base/form/object sources `none`. Artifact CSP is `default-src 'none'` with only per-response hash-bound scripts/styles, `img-src 'self' data:`, `font-src 'self'`, `connect-src 'none'`, and all base/form/object sources `none`; browser extensions, the OS, and unrelated local processes are outside this counter. | A compile result containing a remote runtime reference fails readiness with CLI exit `2` and `REVIEW_EXTERNAL_REFERENCE_FORBIDDEN`; an observed policy violation disables the frame, and every later frame mutation returns HTTP `409` with `REVIEW_SECURITY_POLICY_FAILED`. |
| Control-value privacy | The default capture allowance is zero controls; only source-declared `review_safe` controls may be captured, with the 16-value/256-byte/4-KiB limits above. Password, file, hidden, undeclared, and unknown control types are always forbidden regardless of client claims. | A request containing forbidden control context is rejected in full with HTTP `422` and `REVIEW_CONTEXT_FORBIDDEN`; the server never silently drops only the sensitive fields and accepts the rest. |
| Filesystem ownership and locking | State directories use mode `0700`, files use `0600`, every state path component is owned by the effective user, non-symlink, and owner-controlled, and one OS lock owns the state directory plus one lock per session. Lock acquisition gets 2 seconds; recognized network filesystems and non-regular state files are zero-supported. | Unsafe ownership, mode, link, or filesystem returns CLI exit `2` with `REVIEW_FILESYSTEM_UNSAFE`; lock timeout returns CLI exit `2` with `REVIEW_STATE_LOCKED`, and no second writer starts. |
| Stored-data budget | One promoted revision may retain at most 24 MiB of artifact bytes, one verification at most 20 MiB of evidence, one session 256 MiB, all active sessions 1 GiB, and all retained sessions 4 GiB. At most 32 revision identities, 8 artifact revisions, and 2 verification evidence sets are retained per session, with space reserved before a build or event commit. | Reservation failure returns HTTP `507` or CLI exit `2` with `REVIEW_STORAGE_LIMIT_EXCEEDED`; the prior promoted revision and all unacknowledged events remain untouched. |
| Connection and process budget | The server accepts at most 32 simultaneous connections, 2 browser connections plus 1 stream and 1 poll per session, 2 compiles globally, 1 compile per session, and 1 verifier globally. Excess work is never queued beyond one newest pending source generation per session. | Excess connections or work return HTTP `503` with `Retry-After: 1` and `REVIEW_SERVER_BUSY`; superseded queued generations are discarded with `REVIEW_REVISION_SUPERSEDED`. |
| Response materialization | Every JSON response is capped at 256 KiB before headers, every artifact file at 24 MiB, and streaming writes at 64 KiB per chunk with a 5-second write-stall and 30-second total deadline; an artifact descriptor must be a regular allowlisted file whose size and SHA-256 match the promoted manifest before headers. The server never builds an unbounded response in memory. | JSON overflow returns HTTP `500` or CLI exit `1` with `REVIEW_RESPONSE_TOO_LARGE`; an absent, non-regular, changed, or unlisted artifact returns HTTP `404` with `REVIEW_ARTIFACT_NOT_FOUND`, while a post-header stall closes the connection, records `REVIEW_RESPONSE_TIMEOUT`, and never marks the frame ready. |
| Port and bind selection | V0 binds exactly `127.0.0.1`; an explicit port must be an integer from 1,024 through 65,535, and the default is 4,388. The server makes one bind attempt and never scans for another port or accepts `localhost`, wildcard, IPv6, or non-loopback aliases. | An invalid address returns CLI exit `2` with `REVIEW_NON_LOOPBACK_FORBIDDEN`; an invalid or occupied port returns CLI exit `2` with `REVIEW_PORT_UNAVAILABLE`, and no server process remains. |
| Log data boundary | Normal logs use a closed schema of timestamp, severity, stable code, review ID, revision number, stage, and bounded counters, with 8 KiB per record, one 8 MiB active file, and one 8 MiB prior generation. Capability material, bootstrap/frame tickets, absolute paths, bodies, control values, cookies, headers, source bytes, and environment values are forbidden keys and values. | Log schema validation and any required atomic rotation occur before the related mutation; a policy violation returns HTTP `500` or CLI exit `1` with `REVIEW_LOG_POLICY_VIOLATION`, while write or rotation failure returns HTTP `507` or exit `1` with `REVIEW_LOG_WRITE_FAILED`, and neither path performs the journal or revision write. |
| Entropy generation | Server-generated review IDs, event IDs, batch IDs, bootstrap tokens, cookies, frame tickets, and nonces are drawn only from the operating system cryptographic random generator and contain at least 128 independently generated bits. Generation gets 3 attempts to avoid an in-store collision and has no pseudorandom or timestamp fallback. | Entropy failure or a third collision aborts startup or the request with HTTP `500` or CLI exit `1` and `REVIEW_ENTROPY_UNAVAILABLE`; no predictable identifier is emitted and no state is written. |
| Browser and server startup | Server bind gets 5 seconds, browser launch gets 10 seconds, and frame handshake gets 5 seconds; the session is not called browser-ready before all applicable steps succeed. Browser-launch failure does not delete an otherwise checked session. | Bind failure returns CLI exit `2` with `REVIEW_SERVER_START_FAILED`; browser launch or handshake failure returns exit `2` with `REVIEW_BROWSER_OPEN_FAILED` or `REVIEW_BROWSER_HANDSHAKE_TIMEOUT` and prints the resumable local URL when one exists. |
| Trusted platform boundary | V0 supports one trusted OS user, one trusted browser profile, and the installed ViewSpec/compiler/verifier toolchain; it provides zero protection from root, a malicious same-user process, compromised browser extension, debugger, terminal recorder, or malicious installed plugin. The product makes no recovery claim after kernel panic, controller failure, or filesystem behavior that violates documented sync/atomicity guarantees. | Detectable ownership or tool identity failure returns CLI exit `2` with `REVIEW_FILESYSTEM_UNSAFE` or `REVIEW_SECURITY_POLICY_FAILED`; later checksum corruption fails the session with `REVIEW_JOURNAL_INVALID`, while undetectable compromise is governed by the explicit anti-goals below. |

These are pre-1.0 V0 bounds. Once added to a stable contract profile, they follow the existing
caps-only-rise compatibility rule; a limit failure is never reported as a successful partial
review.

### Explicit Anti-Goals (Academic Edge-Cases)

The following scenarios are intentionally outside the V0 engineering obligation; future developers
must not add fallback machinery for them while claiming V0 conformance:

- V0 is not required to defend against root, a malicious process running as the same OS user, a
  compromised browser extension or debugger, a terminal recorder, or a malicious installed
  compiler, verifier, or plugin.
- V0 is not required to recover a committed write after kernel panic, storage-controller failure,
  or filesystem behavior that violates the platform's documented `fsync` and atomic-replace
  guarantees.
- V0 is not required to salvage, truncate, skip, or heuristically repair a journal containing a
  malformed length, failed checksum, or recovery timeout; the whole session may remain corrupt.
- V0 is not required to annotate inside Shadow DOM, nested frames, canvas, WebGL, or DOM outside the
  compiler-owned root, nor to invent a source target for those surfaces.
- V0 is not required to emulate physical devices, browser chrome, zoom, keyboards, safe areas,
  non-default device pixel ratios, or non-Chromium engines, and its three CSS viewports are not
  cross-browser certification.
- V0 is not required to prove that a physical human performed a click, that an approval reflects
  informed intent, or that human approval establishes conformance, authorization, or release
  permission.
- V0 is not required to provide exactly-once observation across an agent crash; delivery remains
  at-least-once, and consumers must deduplicate the retained 128-bit `event_id`.
- V0's zero-network claim is not required to account for traffic generated by the operating system,
  browser extensions, developer tools, or unrelated local processes; it covers only Review chrome,
  the injected SDK, and served artifacts.
- V0 is not required to remain secure after a practical break of SHA-256 or of the operating
  system's cryptographic random generator; changing cryptographic primitives is a later contract
  migration, not a V0 fallback.
- V0 is not required to preserve wall-clock retention dates across a deliberate clock rollback,
  multi-day clock jump, or corrupted platform clock; live capability and operation deadlines still
  use the monotonic limits above.

## Local Server and Security

The review server is a separate local runtime from generated artifacts.

Required properties:

- bind only to the literal address `127.0.0.1` and reject every wildcard, hostname, IPv6, or other
  address in V0;
- accept one 60-second, single-use bootstrap URL, set a host-only `HttpOnly; SameSite=Strict` session
  cookie with `Path=/r/<review-id>/`, no `Domain` attribute, and an at-most-28,800-second `Max-Age`,
  then return `303 See Other` to a token-free review URL;
- send `Cache-Control: no-store` and `Referrer-Policy: no-referrer` on the bootstrap response, review
  chrome, frame, and API responses;
- require the exact `Host` value on every request and the exact `Origin` and
  `Sec-Fetch-Site: same-origin` values on every browser mutation, in addition to the session cookie
  and current revision/frame nonce;
- set a restrictive CSP for the review chrome;
- serve only generated files recorded in the active revision allowlist;
- reject absolute paths, traversal, symlink escapes, encoded traversal, NULs, and alternate path
  separators;
- never expose a generic local-file endpoint;
- never proxy or fetch remote URLs;
- never inject Review into the stored artifact bytes;
- treat all browser event payloads as untrusted and rebuild provenance from the server-side
  manifest;
- redact capability values, absolute paths, control values, and feedback bodies from normal logs;
- cap every request body before parsing, every JSON response before sending headers, and every
  artifact stream before opening its allowlisted file;
- stop accepting new events after end while still allowing pending batch delivery and
  acknowledgement.

Compiler-produced V0 HTML is served in an artifact frame. The review SDK is injected into the HTTP
response after the stored artifact hash has already been checked; the stored file and proof
identity remain unchanged. The sandboxed frame receives a 5-minute, read-only, revision-scoped
frame ticket, and the SDK communicates only by nonce-bound `postMessage` with its exact parent
window.

V0 accepts only ViewSpec-compiled sources. Arbitrary HTML review is deferred until a separate
compatibility threat model exists.

## Architecture

### Python orchestration

Proposed internal modules:

- `viewspec.review_contract` — ids, dataclasses, JSON validation, bounds, and canonical shapes;
- `viewspec.review_session` — session index, revisions, event journal, acknowledgement, and end
  attribution;
- `viewspec.review_compile` — source detection, immutable revision build, check, semantic diff, and
  optional verification;
- `viewspec.review_server` — loopback HTTP server, capability authorization, long poll, and static
  allowlisted files;
- `viewspec.review_cli` — public command adapters and standard tool envelopes.

The implementation may consolidate modules, but contract logic must remain testable without a
browser or live socket.

### Browser assets

Bundled browser assets provide:

- review chrome;
- artifact-frame SDK;
- explore/annotate mode switching;
- manifest-backed hit testing and keyboard navigation;
- selected-text anchoring;
- conversation and queue state;
- revision reload and stale-target display;
- optional verifier result presentation.

Browser assets are package data and require no CDN or runtime network access.

### Local endpoints

Illustrative endpoint surface:

```text
GET  /open/<single-use-bootstrap-token>
GET  /r/<review-id>/
GET  /r/<review-id>/api/v1/session
GET  /r/<review-id>/api/v1/events?after=<sequence>
POST /r/<review-id>/api/v1/events
POST /r/<review-id>/api/v1/replies
POST /r/<review-id>/api/v1/end
GET  /frame/<read-only-frame-ticket>/<revision>/<allowlisted-path>
GET  /r/<review-id>/stream
```

Endpoint names are internal in V0; the CLI and versioned JSON contracts are the supported public
surface. Server responses still carry internal schema versions and are tested against malformed,
oversized, replayed, and cross-session requests.

## Error Namespace

V0 adds stable codes under `REVIEW_*`. At minimum:

```text
REVIEW_ACK_INVALID
REVIEW_ACK_OUT_OF_ORDER
REVIEW_ACK_REQUIRED
REVIEW_ARTIFACT_NOT_FOUND
REVIEW_BATCH_TOO_LARGE
REVIEW_BROWSER_HANDSHAKE_TIMEOUT
REVIEW_BROWSER_OPEN_FAILED
REVIEW_CAPABILITY_INVALID
REVIEW_CHECK_FAILED
REVIEW_CHECK_TIMEOUT
REVIEW_COMPACTION_FAILED
REVIEW_COMPILE_FAILED
REVIEW_COMPILE_TIMEOUT
REVIEW_CONTEXT_RESET
REVIEW_CONTEXT_FORBIDDEN
REVIEW_DESIGN_INVALID
REVIEW_DIFF_TOO_LARGE
REVIEW_DIFF_TIMEOUT
REVIEW_ENTROPY_UNAVAILABLE
REVIEW_ERROR_REPORT_TOO_LARGE
REVIEW_EVENT_INVALID
REVIEW_EVENT_LIMIT_EXCEEDED
REVIEW_EVENT_TOO_LARGE
REVIEW_EXTERNAL_REFERENCE_FORBIDDEN
REVIEW_FILESYSTEM_UNSAFE
REVIEW_IDEMPOTENCY_CONFLICT
REVIEW_IDEMPOTENCY_REQUIRED
REVIEW_INSTRUMENTATION_VIOLATION
REVIEW_JOURNAL_FULL
REVIEW_JOURNAL_INVALID
REVIEW_JOURNAL_WRITE_FAILED
REVIEW_LOG_POLICY_VIOLATION
REVIEW_LOG_WRITE_FAILED
REVIEW_MANIFEST_AMBIGUOUS
REVIEW_NON_LOOPBACK_FORBIDDEN
REVIEW_POLL_CONFLICT
REVIEW_PORT_UNAVAILABLE
REVIEW_REQUEST_FORBIDDEN
REVIEW_REQUEST_HEADERS_TOO_LARGE
REVIEW_REQUEST_INVALID
REVIEW_REQUEST_LENGTH_REQUIRED
REVIEW_REQUEST_TIMEOUT
REVIEW_REQUEST_TOO_LARGE
REVIEW_REQUEST_URI_TOO_LONG
REVIEW_RESPONSE_TOO_LARGE
REVIEW_RESPONSE_TIMEOUT
REVIEW_REVISION_IDENTITY_MISMATCH
REVIEW_REVISION_MISMATCH
REVIEW_REVISION_NOT_PROMOTED
REVIEW_REVISION_SUPERSEDED
REVIEW_REVISION_WRITE_FAILED
REVIEW_SECURITY_POLICY_FAILED
REVIEW_SELECTION_UNSUPPORTED
REVIEW_SERVER_BUSY
REVIEW_SERVER_START_FAILED
REVIEW_SESSION_CONFIGURATION_CONFLICT
REVIEW_SESSION_ENDED
REVIEW_SESSION_ENDED_BY_HUMAN
REVIEW_SESSION_LIMIT_EXCEEDED
REVIEW_SESSION_NOT_FOUND
REVIEW_SNAPSHOT_TIMEOUT
REVIEW_SOURCE_CHANGED_DURING_CAPTURE
REVIEW_SOURCE_INVALID
REVIEW_SOURCE_NOT_FOUND
REVIEW_SOURCE_TOO_LARGE
REVIEW_SOURCE_UNSUPPORTED
REVIEW_STATE_LOCKED
REVIEW_STORAGE_LIMIT_EXCEEDED
REVIEW_TARGET_INVALID
REVIEW_TARGET_LIMIT_EXCEEDED
REVIEW_TARGET_NOT_IN_MANIFEST
REVIEW_TARGET_UNSUPPORTED
REVIEW_VALIDATE_TIMEOUT
REVIEW_VERIFICATION_FAILED
REVIEW_VERIFICATION_STALE
REVIEW_VERIFICATION_TIMEOUT
REVIEW_VIEWPORT_MISMATCH
```

Every error uses the existing `{code, message, fix}` tool-level shape. User-correctable source
validation errors retain their original detailed ViewSpec validation issues inside a bounded
`source_errors` field instead of being flattened into anonymous review errors; Review V0 never
emits the alternative `cause` field.

## Agent Integration

The managed ViewSpec agent instructions gain the following workflow:

1. Prefer `viewspec review` when a user asks to visually review a ViewSpec IntentBundle or
   AppBundle.
2. Keep `review-poll` attached to an active turn or a harness-native completion-aware wait.
3. Do not use detached fire-and-forget polling.
4. Deduplicate events by `event_id`.
5. Acknowledge the prior batch on the next poll only after its content has been durably captured by
   the agent/harness.
6. Edit semantic source or `DESIGN.md`; never patch the private session artifact.
7. Treat human approval separately from verifier conformance.
8. Stop polling after a human-ended session and do not reopen it without a user request.

The optional MCP server exposes equivalents:

- `open_review`
- `poll_review`
- `end_review`
- `get_review_status`

MCP tools use the standard result envelope, the same acknowledgement semantics, and the same local
path-containment policy as CLI tools.

## Acceptance Criteria

### Source and artifact integrity

- Opening a valid IntentBundle compiles and displays the exact artifact whose hash is recorded in
  the current revision.
- Opening an AppBundle displays only checked screen/shell artifacts from that revision.
- A changed source cannot become visible until validation, compile, and check all pass.
- A failed candidate leaves the prior promoted artifact visible and produces a source-failure
  batch.
- The review SDK does not alter stored artifact bytes or provenance hashes.

### Target integrity

- Selecting every emitted manifest node returns the same IR id, content refs, and intent refs as
  the server-side manifest.
- Client-supplied provenance arrays are ignored and rebuilt from the manifest; an unknown or forged
  target identity is rejected.
- Unaddressed descendants resolve only to a real ancestor or page scope.
- Stale targets remain visibly stale and are never heuristically reassigned.

### Delivery

- Killing `review-poll` after feedback is queued does not lose or acknowledge the event.
- Re-polling before acknowledgement returns identical batch and event ids.
- A valid acknowledgement advances delivery exactly through that batch.
- Invalid or out-of-order acknowledgements do not advance delivery.
- Retrying an identical event, acknowledgement/reply, or Send & End transaction returns its original
  identity and creates no duplicate; reusing its idempotency identity with different bytes fails.
- A final **Send & End** batch is deliverable and attributable to the human.

### Interaction and accessibility

- Explore mode preserves native control behavior.
- Annotate mode supports pointer and keyboard selection.
- Mode, selection, queue state, diagnostic state, and stale state have accessible names and visible
  focus.
- Mobile, tablet, and desktop review chrome have no horizontal document overflow at canonical
  viewports.

### Security

- Cross-session capability reuse fails.
- Traversal, symlink escape, forged Host/Origin, oversized body, and non-loopback binding tests fail
  closed.
- Logs and JSON status output contain no capability tokens, raw password/file values, cookies,
  environment variables, or unintended absolute paths.
- Review chrome, its SDK, and served artifacts make zero non-loopback network calls in the default
  workflow.

## Test Plan

### Pure contract tests

- id formats and uniqueness;
- event, target, revision, batch, and end shapes;
- every byte/count/depth bound at limit and limit-plus-one;
- every Constraints & Fallbacks row asserts its exact HTTP status or CLI exit, stable error code,
  unchanged durable state, and absence of partial-success output;
- canonical serialization and schema round trips;
- error registry two-way coverage;
- acknowledgement state-machine properties;
- stale/exact/changed target remapping;
- journal recovery and malformed-tail failure.

### Integration tests

- IntentBundle open, annotate, poll, acknowledge, reply, recompile, end;
- AppBundle route and action exploration;
- source invalidation retains the previous promoted revision;
- semantic diff identity matches the exact two promoted sources;
- optional verification result and repair plan pass through unchanged;
- 30-minute capability expiry, 5-second idle process exit, and explicit session resume with rotated
  capability material;
- user-ended reopen refusal and explicit `--reopen`.

### Browser tests

- pointer and keyboard annotation;
- selected-text anchoring;
- native inputs and declared actions in explore mode;
- mode hotkey inside and outside form controls;
- viewport switching and responsive chrome;
- conversation, queued feedback, agent reply, stale target, and end flows;
- injected SDK isolation from stored artifact output.

### Adversarial tests

- forged provenance ids and arrays;
- cross-session event submission;
- replayed event and acknowledgement requests;
- path encoding and symlink traversal;
- huge annotations, refs, control values, batches, and journals;
- interrupted atomic writes;
- malformed source during live reload;
- browser disconnect during send;
- agent poll disconnect before and after batch serialization.
- bootstrap expiry and reuse, frame-ticket reuse across revisions, missing required browser headers,
  and stale revision nonces;
- source replacement during capture, superseded builds at every promotion boundary, concurrent
  compaction, and cross-revision identity injection;

## Delivery Plan

### Milestone 0 — contracts

- Review ids, revision identity, events, batches, end attribution, and acknowledgements.
- Bounds and `REVIEW_*` error registry.
- Pure session state machine and property tests.

### Milestone 1 — local session and server

- Source detection, compile/check revision promotion, private state directories.
- Loopback capability server, allowlisted artifact serving, status, idle cleanup.
- `review`, `review-status`, and `review-end` CLI commands.

### Milestone 2 — browser review loop

- Review chrome and injected artifact SDK.
- Explore/annotate modes, provenance trace, source-addressed annotations, selected text.
- Event journal, `review-poll`, acknowledgement, agent reply, human end flow.

This milestone is the first externally useful release.

### Milestone 3 — revisions and evidence

- Watched source and DESIGN changes.
- Immutable promoted revisions, last-good fallback, semantic diff, target remapping.
- Optional canonical viewport verification and repair-plan display/delivery.

### Milestone 4 — productization

- Managed agent instructions and MCP tools.
- Packaging/browser asset verification, documentation, examples, changelog, compatibility notes.
- One polished AppBundle review demo showing human feedback, semantic edit, recompile, and proof.

## Recommended First Build Slice

The first implementation slice should prove the source-addressed round-trip without live reload or
verification:

1. Define `ReviewRevision`, `ReviewTarget`, `ReviewEvent`, and `ReviewBatch` V1.
2. Compile one IntentBundle to a private immutable revision and run `check`.
3. Serve the checked HTML on loopback with a capability URL.
4. Inject a minimal SDK that selects `[data-ir-id]` and submits one annotation.
5. Validate the target against `provenance_manifest.json` on the server.
6. Persist the event and return it through `review-poll --json`.
7. Demonstrate interruption-safe redelivery and explicit acknowledgement.

Do not begin with visual patching, arbitrary HTML, sharing, or whiteboards. The decisive product
proof is that a human can point at a rendered element and the agent receives a durable,
manifest-verified semantic source address.

## Implemented Additive Contract: Typed IntentPatch V1

Review V0 feedback and verifier repair plans may now produce proposal context for an id-addressed,
domain-specific `IntentPatch`, not RFC 6902 array-index patches and never generated-code edits. The
normative contract, physical limits, transaction recovery rules, and explicit anti-goals are in
[IntentPatch V1](intent-patch-v1.md).

Closed V1 operations:

- `set_aesthetic_profile`
- `set_style_token`
- `set_region_layout`
- `move_region`
- `reorder_region_children`
- `set_binding_presentation`
- `replace_semantic_attr`
- `replace_fixture_scalar`
- `set_visibility_condition`

Every proposal includes:

- base source SHA-256 and contract profile;
- stable target ids;
- old-value preconditions;
- bounded operations from a closed vocabulary;
- preview source SHA-256;
- semantic diff;
- compile/check result;
- explicit user approval before source write.

`patch-preview` now refuses changed bases, stale targets, unknown fields, ambiguous ids, duplicate
targets, and candidate proof failures before returning an approval token. `patch-apply` re-proves
that exact preview under a source lock, writes a durable receipt and inverse patch, and remains a
separate externally visible action that is not authorized merely by opening a review session or
submitting feedback.

## Implemented Additive Contract: Converge Sessions V1

Review now projects a pending Converge proposal into the side panel with its semantic before/after
and progress certificate. The browser receives no approval token: an authenticated, same-origin,
current-frame Approve or Reject action names only the exact `preview_id`, and the server consumes
its private authority after checking that the proposal is still current.

Agents automatically operate the session workflow—start or resume, author from the legal-operation
menu, submit, and re-verify—while humans express intent and grant authority. The controller is
durable, capped at three attempts and ten minutes, rejects source drift and cycles, and accepts
verifier candidates only when their error-obligation set is a proper subset of the baseline under
the identical complete plan. Review persists an optional custom Converge state root in its private
configuration, and interrupted applies or post-apply verification resume from durable receipts and
checkpoints. See [Converge Sessions V1](converge-sessions-v1.md).

## Follow-On: State and Cross-Surface Review

ViewSpec Studio can extend the same protocol with:

- declared AppBundle state snapshots and replay checkpoints;
- deterministic time travel through mutation events;
- annotations anchored to `(screen, source node, replay checkpoint)`;
- before/after visual and semantic comparison;
- synchronized HTML, React, SwiftUI, and Flutter projections where supported;
- one semantic repair recompiled and verified across selected targets;
- hosted private review links, teams, approvals, audit retention, and signed receipts.

Those capabilities are the long-term differentiation. V0 establishes the trust boundary they all
depend on.

## Positioning

Recommended product language:

> Agents should not author pixels. People should still review the result. ViewSpec Review connects
> every comment on the rendered interface to the semantic source the agent can safely change.

Avoid claiming that visual review is unnecessary. The stronger claim is that visual feedback no
longer needs to collapse into screenshots, selectors, and guessed code locations.
