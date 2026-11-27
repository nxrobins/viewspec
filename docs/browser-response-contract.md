# Bounded browser responses

The experimental browser MCP server supports an opt-in bounded presentation:

```sh
python experiments/browser-agent-probe/mcp_browser_server.py \
  --allow-origin https://app.example --allow-read 'https://app.example/*' \
  --response-mode focused --response-bytes 12288
```

The operator's request, navigation, privacy and write policies still apply. This presentation
does not enable writes, change Save semantics, or assert application persistence. The default
server mode remains `full` for existing clients. Any tool's `response="focused"` also opts the
session into bounded presentation and observation-qualified addressing. Subsequent `response="full"`
expands the presentation within the same byte budget; it does not disable qualified addressing.

## Addresses belong to observations

Bounded views show controls as `[3:7]`, meaning control 7 from observation 3:

```python
act(action="click", handle="3:7")
# Equivalent explicit address:
act(action="click", handle=7, observation_id=3)
```

Bare numeric handles are refused in bounded sessions. Contradictory observation IDs and mixed
observation targets are refused before navigation or mutation. `call` and `prepare` require
`observation_id` from their returned schema. A native form's `submitters` are qualified addresses.
Full-mode clients may also use qualified handles without opting into bounded presentation.

Scoped wait targets accept the same addresses. An action's targets must share its source
observation. A standalone qualified observation wait uses that observation as its baseline;
receipt waits must refer to the receipt's original source observation. An expired observation
does not fall back to the latest reading. The existing eight-observation retention limit applies.

These addresses are provenance, not permission to act through stale state. Action-time identity,
owner, document, frame, route, actionability, field and submit checks still run. A receipt binding
that detects a changed or missing scope refuses mutation before dispatch, including when no
explicit feedback condition was requested. Equivalent control replacements still need to pass
the existing identity checks; replacing an owner does not revive an old binding.

## Focus and coverage

Focused replies prefer one active dialog, or the action's still-observed form/dialog owner.
They include its content and controls, global message regions and obstruction summaries, and
other currently usable controls such as portalled choices. Covered background controls can be
omitted. If no unambiguous owner is available, the ordinary page view remains the starting point.

`view_scope` describes an owner-focused view: returned/total line counts, whether that focused
slice is complete, omitted background lines, and an explicit full-observation expansion. Its
offsets are not ordinary page offsets, so it does not advertise `view_page.next_offset`.
Use its expansion, such as `observe(observation_id=3, response="full")`, to get ordinary paging.
Explicit `find` or nonzero `offset` retains the existing full-reading paging/find semantics.

Every included form schema is complete for that observed scope unless it explicitly has
`schema_coverage="omitted"`. A schema summary must be expanded before using its arguments.
Schemas are rebuilt from each reading: values, empty/filled/unknown state, requiredness, options,
editor revision and membership are never inferred from a cached schema name. This slice does
not implement a cross-observation schema cache or a client-side delta reconstruction protocol.

The canonical result is judged before presentation. Focused replies summarize field-progress
and receipt activity and omit repetitive step/diff material. Per-step dialog, download and
validation events remain visible when they fit, and their counts survive the smallest fallback.
Execution disposition, conservative retry guidance, verification/completion state, policy-event
counts, and partial field-application states survive output reduction. Temporally associated
request completion is not a causal confirmation of a business mutation.

`presentation.omitted` and `presentation.summarized` identify reduced sections. Missing content
never means empty, false, unchanged, or verified. `view_coverage` marks budget-truncated views;
only complete lines are kept. A single oversized line can require detail expansion. If even
the actionable view and essential detail cannot fit, a small partial-result envelope preserves
disposition and tells the caller to expand before deciding or repeating anything.

## Complete output budget

`--response-bytes` accepts 4096–65536 bytes, default 12288. The bound counts both MCP result
channels: the structured object and its equal compact JSON text fallback, including default-spaced
UTF-8 JSON serialization and a 256-byte allowance for JSON-RPC framing. Arbitrarily large
caller-supplied RPC IDs and client-added rendering metadata are outside that allowance. This is
a byte contract, not a model-token or latency guarantee. Both MCP channels remain available.

The budget covers schemas, messages, policy records and evidence as well as view text. Actions
are never replayed to recover omitted results. A smaller presentation does not turn a blocked,
partially applied, unknown or mismatched result into success.

## Retained details

Bounded replies return an opaque `result_id` when the original redacted result can be retained:

```python
observe(details={"result_id": "result_…", "section": "preparation_tools"})
observe(details={"result_id": "result_…", "section": "steps", "offset": 0})
```

Omit `section` for the entire original result. `detail.text` contains a JSON character slice;
append slices using `detail.next_offset` as the next `details.offset` and `detail.revision` as
`details.revision`, then parse the concatenated JSON. If newly learned privacy information changes
the redacted result, restart at offset zero. Revision cursors are random, not public hashes of
field values. Offsets count characters, not UTF-8 bytes. A section can contain a large single
string without escaping the per-reply budget. These reads neither touch the browser nor advance
the active observation. They cannot be combined with navigation, waiting or view paging.

The session retains at most 16 results within 8 MiB. An individual larger result reports
`detail_state="unavailable"`; it does not pretend expansion is possible. Expired IDs return
`BROWSER_RESULT_UNKNOWN`. Recovery is a fresh observation, never replay of the original write.
Retained evidence can outlive its actionable observation; expanding it does not extend the
observation's lifetime. Details are redacted again before serialization so credentials learned
later are also withheld. The retained result is historical evidence, not a current-state claim.

## Receipt-only observations

In bounded sessions, `observe(after_action=id)` defaults to receipt-only output. It samples or
waits on the original receipt and returns its evidence without generating a new full-page
observation or advancing the active handle namespace. `response="receipt"` is only valid for
this operation. `response="full"` requests a new page reading alongside the receipt instead.

Receipt-only output carries the original action/source observation IDs, execution, scope state,
feedback and summarized temporal transport/write evidence. `persistence` remains `unverified`.
Use `observe()` for a current page view, or `details` for the original retained evidence. Save
is still separate from dialog preparation; no new verification mutation or automatic retry is
introduced by this response contract.

This slice does not add reusable field-expectation receipts or cross-document verification.
Those require a separate entity/freshness contract. Existing [readiness and completion evidence](browser-readiness-contract.md)
continues to distinguish field state and UI feedback from application persistence.
