# ViewSpec Converge Sessions V1

Converge Sessions turn Review feedback or verifier failures into a durable sequence of small, source-bound IntentPatch proposals. The controller may propose, preview, prove, apply, and re-verify; only a human may authorize a source write.

## Operator contract

The normal human workflow has three actions: open ViewSpec Review, inspect the semantic before/after plus its progress proof, and approve or reject. Humans are not expected to know hashes, task ids, patch operation names, tool names, or approval tokens.

The agent automatically starts or resumes a session, reads the constrained authoring task, submits one legal IntentPatch, reports terminal failures, and continues after re-verification. The expert CLI and MCP functions exist for integration and diagnosis; they are not a human command vocabulary.

> Humans express intent and grant authority. Agents operate the workflow. ViewSpec enforces the physics.

## Authority boundary

Review and verifier evidence are proposal authority only. They can create an authoring task, but they can never authorize mutation.

Agent-facing start, submit, and status responses omit both the outer convergence approval capability and the inner IntentPatch approval capability. ViewSpec Review retains the outer capability privately and uses it only after an authenticated, same-origin, revision-handshaken browser submits the exact pending `preview_id`; the expert CLI can reveal it only through the explicit `--show-authority` option.

Every proposal is bound to all of the following:

- the canonical source path identity;
- the exact current UTF-8 source hash;
- the complete Review batch or repair-plan evidence set;
- one generated authoring task and its legal semantic target keys;
- one compiled semantic diff and candidate hash;
- one human-review or verifier progress certificate.

A stale, substituted, rebased, out-of-task, or unproved proposal is rejected without writing source.

## State machine

```text
start
  -> awaiting_proposal
  -> full_revision_required   (no legal IntentPatch target)

awaiting_proposal + submit
  -> awaiting_approval        (candidate is valid and progress is accepted)
  -> stalled                  (cycle, regression, no strict progress, or indeterminate proof)
  -> exhausted                (attempt or deadline limit)

awaiting_approval + reject
  -> rejected

awaiting_approval + approve
  -> applied                  (human Review session)
  -> conformant               (verification session, zero remaining errors)
  -> awaiting_proposal        (verification session, strict partial progress)
  -> stalled                  (post-apply proof drift or indeterminate verification)
  -> exhausted                (third attempt still nonconformant)
  -> full_revision_required   (remaining failure has no legal patch target)
```

Only `awaiting_proposal` and `awaiting_approval` accept new proposals or decisions. `applied` is terminal for a human Review session but is a durable reconciliation checkpoint for verifier-driven sessions: status/resume repeats the identical post-apply verification until it reaches `conformant`, `awaiting_proposal`, `stalled`, `exhausted`, or `full_revision_required`.

## Convergence Authoring Task

The published schema is `https://viewspec.dev/converge-task.schema.json`. A task contains exact source fragments, exact old values, replacement fields, optional closed allowed-value menus, and target keys for the only operations the agent may propose.

The agent must copy fixed fields exactly, fill only the declared replacement field, use only an allowed value when one is supplied, and copy the task evidence refs exactly. The task generator covers all nine IntentPatch operations, including aesthetic profiles, fixture scalars, and visibility conditions; if the evidence cannot be resolved to an existing IntentPatch target, the controller returns `full_revision_required` rather than escaping through arbitrary JSON Patch, DOM, CSS, generated files, field creation, or field deletion.

## Progress certificate

Human-review sessions use a certificate whose accepted meaning is only “this candidate is valid and explicit human approval is still required.” It is not verifier conformance and cannot authorize itself.

Verification sessions compare stable error-obligation identities built from diagnostic code, severity, source reference, and viewport; mutable prose and evidence paths do not affect identity. A candidate is accepted if and only if it is complete, uses the exact baseline verification plan, introduces no error obligation, and removes at least one baseline error obligation:

```text
candidate_errors ⊂ baseline_errors
```

The certificate records fixed, remaining, and introduced obligations plus both result identities. Warnings do not satisfy or defeat strict error progress, but an incomplete or indeterminate verification always fails closed.

## Apply and reconciliation

Approval rechecks the deadline, session status, exact random outer capability, exact source bytes, and current preview. The controller then invokes the existing IntentPatch transaction with its private inner capability, atomically replaces the source, and durably records the normal inverse-patch receipt.

If the source transaction commits but the session-state write is interrupted, the next status call reconstructs `applied` only from the matching durable IntentPatch receipt and exact candidate bytes. Verifier-driven sessions then run the same verification plan again against those applied bytes; verifier failure leaves the resumable checkpoint intact, while a completed result must have the same completeness, status, plan, and error obligations as the approved candidate proof.

Starting a replacement session archives the complete checksum-protected terminal state by session id before moving the active pointer. At most 64 terminal sessions are retained per source; exceeding that bound fails closed until an operator exports and removes old archives.

## Constraints & Fallbacks

| Threat | The boring limit | Fail-fast mode |
|---|---:|---|
| Infinite repair loop | A session permits at most 3 proposals and lasts at most 600 seconds. | Persist `exhausted` with `attempt_limit` or `deadline_exceeded`; reject later approval with `CONVERGE_SESSION_EXPIRED`. |
| Concurrent writers | Exactly 1 active session and 1 pending proposal may own a source; the source lock wait is 2 seconds. | Reject a second session with `CONVERGE_SESSION_ACTIVE`, or fail the operation with `CONVERGE_LOCK_TIMEOUT`; never queue silently. |
| Stale or out-of-band edits | Every transition must match the exact current source SHA-256 and the preview's base hash. | Return `CONVERGE_SOURCE_CHANGED` before preview or apply; never rebase automatically. |
| Agent self-approval | Agent responses contain 0 approval capabilities; the outer operator capability is 256 random bits and exact-preview scoped. | Return `CONVERGE_APPROVAL_INVALID` and leave source byte-for-byte unchanged. |
| Oversized source or state | Source and baseline files are capped at 1 MiB, authoring task at 128 KiB, and context/IntentPatch files at 64 KiB. | Return the corresponding `*_TOO_LARGE` error from the bounded reader before JSON parsing, verification, or mutation. |
| Patch fan-out | One patch contains 1–64 unique semantic operations, and one context contains at most 63 requests. | Reject through the closed IntentPatch parser; no partial operations are applied. |
| Ambiguous authority | Patch evidence must equal the task evidence as a set, and every operation target key must appear in the task. | Return `CONVERGE_EVIDENCE_MISMATCH` or `CONVERGE_TARGET_OUTSIDE_TASK`; do not preview the candidate. |
| Regression disguised as progress | Candidate error obligations must be a proper subset of baseline errors under the identical plan. | Persist `stalled` with `introduced_error`, `no_strict_progress`, `candidate_indeterminate`, or `verification_plan_changed`. |
| Oscillation | Every accepted candidate source hash is retained for the entire session. | Persist `stalled` with `candidate_cycle` when a candidate repeats any seen source hash. |
| Post-approval nondeterminism | Applied verification must reproduce the approved candidate's plan, completeness, status, and error obligations exactly. | Persist `stalled` with `post_apply_verification_drift`; never continue from the divergent result. |
| Interrupted approval | A candidate source may be reconciled only from its exact applied IntentPatch receipt and matching candidate SHA-256. | Preserve or reconstruct `applied`; never leave an already-written source represented as `awaiting_approval`. |
| Interrupted re-verification | Verifier-driven `applied` is a resumable checkpoint with the complete candidate proof and receipt. | Return `CONVERGE_VERIFIER_FAILED` while retaining the checkpoint; the next status call retries reconciliation. |
| Audit retention | Exactly 64 immutable terminal session envelopes may be archived per source. | Return `CONVERGE_ARCHIVE_LIMIT_EXCEEDED` before replacing the active pointer. |
| Unsafe local paths | Source, state, lock, and stored patch entries must be regular non-symlink files under an existing owner-controlled `0700` state directory; files are `0600`. | Return `CONVERGE_PATH_INVALID`, `CONVERGE_STATE_UNSAFE`, or `CONVERGE_STATE_INVALID`; never chmod an existing shared directory. |
| Corrupt durable state | The state envelope is strict JSON, capped at 1 MiB, and carries a SHA-256 checksum over its canonical payload. | Return `CONVERGE_STATE_INVALID`; never guess, repair, or resume corrupted state. |
| Hidden dependency side effects | Canonical candidate verification permits no implicit package installation and no SDK network call. | Return an incomplete verification, causing `candidate_indeterminate` and a terminal `stalled` session. |
| Oversized Review projection | Review JSON responses are capped at 256 KiB and expose only a bounded convergence projection. | Return `REVIEW_RESPONSE_TOO_LARGE`; never truncate proof or authority fields into a misleading response. |

These are physical limits, not tuning defaults. Implementations may lower a bound in a future contract version, but V1 must not silently raise one or convert a fail-fast condition into best-effort behavior.

## Explicit Anti-Goals

Future developers are not required to engineer fallbacks for a malicious process running as the same OS user that can read or rewrite ViewSpec's private state, invoke lower-level filesystem APIs, or patch the installed SDK. Converge Sessions provide workflow authority separation, not a hostile same-UID security boundary.

Future developers are not required to recover a session after SHA-256 collision, filesystem or kernel violation of documented atomic-rename/fsync semantics, or storage hardware that falsely reports durable writes. These scenarios are outside the V1 local-machine fault model.

Future developers are not required to coordinate sessions across machines, containers, network filesystems, or independently replicated source trees. V1 owns one canonical local source path and one local private state directory.

Future developers are not required to preserve a ten-minute session across severe wall-clock rollback or arbitrary manual editing of the system clock. A clock anomaly may require starting a new session, but it must never authorize a mismatched source or preview.

Future developers are not required to synthesize a fallback when a third-party or custom verifier hangs despite violating its own bounded verifier contract. Canonical ViewSpec verification is the supported execution path; nonconforming injected verifier implementations are test/integration responsibility.

Future developers are not required to auto-merge two independently valid human proposals, infer intent after a rejected proposal, or produce an unrestricted full revision when the task reports `full_revision_required`. Those require a new, separately reviewed authoring decision.

## Expert surfaces

The expert CLI commands are `converge-start`, `converge-submit`, `converge-status`, `converge-approve`, and `converge-reject`. Normal submit/status output withholds authority; `--show-authority` is an explicit diagnostic escape hatch for an operator-controlled terminal. When an integration selects a custom Converge state root, the agent starts Review with the matching `--convergence-state-dir`; the path is persisted in Review's private configuration and is not a human workflow concept.

The MCP tools are `start_convergence`, `submit_convergence_patch`, `get_convergence_status`, `approve_convergence`, and `reject_convergence`. The approval tool can consume authority explicitly supplied by an operator, but no agent-facing tool can discover it; normal approval remains ViewSpec Review.
