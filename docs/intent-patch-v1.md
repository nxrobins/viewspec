# IntentPatch V1 — Proof-Carrying Semantic Transactions

IntentPatch V1 is the local `local_v1` contract for making a small, reviewable change to an
existing ViewSpec `IntentBundle` or `AppBundle`. It closes the loop between human Review feedback,
verification repairs, semantic source, compiler proof, explicit approval, and an auditable write
without allowing agents to patch DOM, CSS, generated code, or arbitrary JSON paths.

The published schema is `https://viewspec.dev/intent-patch.schema.json`. The runtime parser is the
authority for UTF-8 byte caps, source cross-references, old-value preconditions, candidate
validation, and transaction behavior that JSON Schema cannot express.

## Authority Boundary

Review batches and verification repair plans are **proposal evidence only**. They may be converted
to bounded `IntentPatchContext` values, but neither a human change request nor verifier output is
approval to write source.

The only write authority accepted by `patch-apply` is the exact `approval_token` produced by
`patch-preview` for the same patch ID, base source hash, candidate source hash, semantic diff,
compile/check result, verification result, and inverse patch. Any source-byte change invalidates
that authority.

## Wire Contract

An IntentPatch is strict JSON with exactly these root fields:

```json
{
  "schema_version": 1,
  "contract_profile": "local_v1",
  "source_kind": "intent_bundle",
  "base_source_sha256": "<64 lowercase hex characters>",
  "operations": [
    {
      "op": "replace_semantic_attr",
      "node_id": "starter_dashboard",
      "attr": "title",
      "old_value": "Starter Dashboard",
      "value": "Operations Dashboard"
    }
  ],
  "evidence_refs": []
}
```

Unknown fields, duplicate JSON keys, comments, code fences, `NaN`, infinity, unsupported schema
versions, and unsupported contract profiles fail closed. `patch_id`, `preview_id`, approval token,
candidate hash, and inverse patch are derived; an author does not supply them.

For an `app_bundle`, each screen-scoped operation must include `screen_id`. App-level fixture and
visibility operations never accept `screen_id`.

## Closed Operation Vocabulary

| Operation | Stable target | Exact precondition | Replacement |
|---|---|---|---|
| `set_aesthetic_profile` | optional `screen_id`; the one view profile | `old_value` profile or null | `value` profile or null |
| `set_style_token` | optional `screen_id`, `style_id` | `old_value` token | `value` token |
| `set_region_layout` | optional `screen_id`, `region_id` | `old_value` layout | `value` layout |
| `move_region` | optional `screen_id`, `region_id` | `old_parent_id` | `parent_id` |
| `reorder_region_children` | optional `screen_id`, `region_id` | exact ordered `old_children` | same child-ID set in `children` |
| `set_binding_presentation` | optional `screen_id`, `binding_id` | `old_value` `present_as` | `value` `present_as` |
| `replace_semantic_attr` | optional `screen_id`, `node_id`, `attr` | exact existing scalar `old_value` | scalar `value` |
| `replace_fixture_scalar` | `resource_id`, `record_id`, `field` | exact existing scalar `old_value` | scalar `value` |
| `set_visibility_condition` | `visibility_id` | exact existing `when` object | valid bounded `when` object |

All targets use existing ViewSpec IDs. IntentPatch V1 cannot add or delete semantic nodes,
regions, bindings, styles, records, fields, routes, screens, resources, state entries, mutations,
selectors, or visibility rules; those changes require a full source revision.

Every operation must change its target. A patch cannot write the same semantic target twice, even
if the writes appear consistent, because operation order must never hide the value being approved.

## Evidence Adapters

`patch_context_from_review_batch()` accepts one validated `ReviewBatch`, retains only
`change_request` events, preserves manifest-backed target IDs and refs, and binds the context to
the batch revision's exact source hash and `local_v1` profile. Questions, notes, and approvals are
not converted into source-change requests.

`patch_context_from_repair_plan()` accepts only a `VerificationRepairPlan` whose disposition is
`repair`. A `done` result requires no source change, and an indeterminate `retry` result is not
evidence for guessing a repair.

Contexts are input to patch authorship, not patches and not authorization. An author must still
choose closed operations, supply exact old values, and pass the full preview boundary.

## Preview Protocol

`viewspec patch-preview SOURCE PATCH --candidate-out CANDIDATE --json` performs these steps without
mutating `SOURCE`:

1. Parse the patch as strict, closed, bounded JSON and derive its deterministic patch ID.
2. Require `base_source_sha256` to equal the exact current UTF-8 source bytes.
3. Validate the complete base source and detect its declared source kind.
4. Resolve every stable target exactly once and enforce every old-value precondition.
5. Apply operations to an isolated in-memory copy and serialize one deterministic candidate.
6. Validate the complete candidate, compute the semantic diff, compile it through the public local
   target, and check the emitted artifact.
7. If `--verify` was requested, require canonical verification to be conformant; `--install` is
   honored only as the existing explicit verification install permission.
8. Derive the inverse patch, preview ID, and approval token from the full proof identity.

Writing `--candidate-out` is optional and cannot overwrite the source or patch file. A preview
failure writes no candidate and returns no approval token.

## Apply and Recovery Protocol

`viewspec patch-apply SOURCE PATCH --approval TOKEN --json` acquires a per-source interprocess lock,
recovers any provable interrupted transaction, and then repeats the entire preview under the lock.
It accepts only the exact current approval token.

Apply writes a durable `prepared` receipt, writes and syncs an exact base backup, atomically replaces
the source with the candidate, commits an `applied` receipt containing the inverse patch, removes
the backup, and syncs the parent directory. A normal failure rolls the source back and records an
`aborted` receipt.

After process death, recovery recognizes only two physical source states: the recorded base hash or
the recorded candidate hash. Base finalizes as aborted; candidate finalizes as applied only when
the exact recorded base backup exists. Any third hash, malformed receipt, unsafe backup, or
conflicting prepared transaction returns `PATCH_RECOVERY_REQUIRED` without overwriting a byte.

Receipts live beside the source under `.viewspec/patch-receipts/`. The inverse patch is itself
source-bound to the candidate hash and must be separately previewed and approved before use.

## Constraint Matrix

This matrix is normative and contains the existential threats that must remain physically bounded.
Every maximum is inclusive; limit-plus-one must fail before source mutation.

| Existential threat | The boring limit | Fail-fast mode |
|---|---|---|
| Parser or allocation exhaustion | Patch and context are each at most 65,536 UTF-8 bytes; a patch has 1–64 operations, at most 64 evidence refs of 256 bytes, scalar strings of 8,192 bytes, and reorder lists of 32 IDs. | Reject before candidate construction with `PATCH_TOO_LARGE`, `PATCH_OPERATION_LIMIT_EXCEEDED`, `PATCH_EVIDENCE_INVALID`, or `PATCH_VALUE_INVALID`; CLI exit `2`, MCP `ok: false`. |
| Source exhaustion | IntentBundle source keeps its 256 KiB cap and AppBundle source keeps its 1 MiB cap. | Reject before copying or compiling with `PATCH_SOURCE_TOO_LARGE`; CLI exit `2`. |
| Stale or wrong source | One lowercase SHA-256 covers the exact source bytes and `source_kind` must match the validated root shape; no rebase exists. | Reject with `PATCH_BASE_CHANGED` or `PATCH_SOURCE_KIND_MISMATCH`; source and receipt store remain unchanged. |
| Arbitrary writes disguised as semantic edits | Exactly nine operations are supported, all fields are closed, IDs are 1–128 safe characters, field names are bounded, and generated-file/DOM/CSS/selector paths have no representation. | Reject with `PATCH_OPERATION_UNSUPPORTED`, `PATCH_FIELD_UNKNOWN`, or `PATCH_TARGET_INVALID`; CLI exit `2`. |
| Lost preconditions or ambiguous null | Every replacement targets an already-declared field and compares canonical JSON old value exactly; field creation and deletion are zero-supported. | Reject with `PATCH_TARGET_MISSING`, `PATCH_TARGET_AMBIGUOUS`, or `PATCH_PRECONDITION_FAILED`; no partial candidate is returned. |
| Hidden operation ordering | A patch has at most one write per semantic target and every operation must change that target. | Reject with `PATCH_TARGET_CONFLICT` or `PATCH_NO_EFFECT` before preview proof. |
| Invalid whole-model result | Base and candidate both pass the existing full validator; candidate also passes semantic diff, compile, and artifact check, with optional canonical verification. | Reject with `PATCH_SOURCE_INVALID`, `PATCH_DIFF_FAILED`, `PATCH_COMPILE_FAILED`, or `PATCH_VERIFICATION_FAILED`; no approval token is issued. |
| Approval replay or substitution | The token is 256-bit SHA-256 material bound to one preview identity and candidate hash; apply recomputes the preview under lock. | Reject malformed, stale, or nonmatching authority with `PATCH_APPROVAL_INVALID`; CLI exit `2`. |
| Concurrent lost update | One per-source OS lock covers recovery, read, preview, backup, replacement, and receipt commit; lock acquisition waits at most 5 monotonic seconds. | Reject contention with `PATCH_LOCK_TIMEOUT`, CLI exit `1`; the waiting process performs no read-modify-write transaction. |
| Noncooperating editor overwrite | Apply re-hashes bounded source bytes after proof and again after receipt/backup preparation immediately before atomic replacement. | A detected external edit aborts and returns `PATCH_APPLY_FAILED`; the concurrent source bytes are preserved and no applied receipt is written. |
| Partial write or process death | Source, backup, and receipts use same-filesystem temporary writes, flush, `fsync`, and atomic replace; recovery accepts only exact base or candidate hashes. | Roll back and return `PATCH_APPLY_FAILED`, or return `PATCH_RECOVERY_REQUIRED` when recovery cannot be proven; never guess or silently salvage. |
| Symlink or receipt substitution | Apply accepts regular non-symlink source and patch files; receipt directory, receipt files, backups, and lock files cannot be symlinks, and the lock uses `O_NOFOLLOW` where available. | Reject with `PATCH_PATH_INVALID`, `PATCH_RECEIPT_INVALID`, or `PATCH_RECOVERY_REQUIRED`; CLI exit `1` for storage-integrity failures. |
| Receipt-store exhaustion | At most 512 `vpv_*.json` receipts may exist in one source directory's receipt store, and each receipt is capped at 256 KiB before parsing. | Reject before apply with `PATCH_RECEIPT_LIMIT_EXCEEDED` or `PATCH_RECEIPT_INVALID`; archive receipts explicitly and retry. |
| Evidence mistaken for authority | A context has 1–63 actionable requests plus one origin ref and at most 64 unique evidence refs; Review keeps only `change_request`, verification keeps only `repair`. | Reject empty, oversized, nonlocal, or malformed context with `PATCH_CONTEXT_EMPTY`, `PATCH_OPERATION_LIMIT_EXCEEDED`, `PATCH_PROFILE_UNSUPPORTED`, or `PATCH_CONTEXT_INVALID`. |
| Accidental dependency/network permission | Preview/apply perform local validation, diff, compile, and check by default; browser dependency installation occurs only with both an explicit verification flow and `--install`. | Missing verification runtime fails with the existing verification diagnostics and `PATCH_VERIFICATION_FAILED`; it never downgrades verified apply to unverified success. |

## Constraints & Fallbacks

- IntentPatch accepts at most 64 operations and 64 KiB; limit violations reject the whole request before candidate allocation or mutation.
- Exact source hashes and exact old values are mandatory; ViewSpec never rebases, merges, or applies a subset of a stale patch.
- Only nine stable-ID semantic operations exist; unknown fields, arbitrary paths, missing fields, duplicate targets, and per-operation no-ops are rejected.
- Preview must validate, semantic-diff, compile, and check the whole candidate; optional verification must be conformant or no approval token exists.
- Apply must reacquire and re-prove the exact preview under a five-second source lock; any different token or source returns an error without writing source.
- Apply must recheck the source immediately before replacement; a noncooperating edit aborts the transaction without overwriting that edit.
- Source replacement and receipt commit are one recoverable transaction; a post-crash state outside the exact base/candidate hashes halts with `PATCH_RECOVERY_REQUIRED` and requires manual inspection.
- The receipt store is local, non-symlinked, and capped at 512 receipts; unsafe or exhausted storage blocks apply rather than weakening auditability.
- Review and verifier evidence can propose a patch but can never authorize one; explicit approval is bound only to the exact preview token.

## Unhappy Paths

- If source bytes change after patch authorship, preview returns `PATCH_BASE_CHANGED`; regenerate from the new source.
- If a target was deleted, duplicated, renamed, or changed, preview returns the corresponding target or precondition code; do not search for a “close enough” target.
- If one operation makes a later operation invalid, the entire preview fails; operation subsets are never retained.
- If the candidate validates but cannot compile or check, preview returns a proof failure; schema validity alone is not success.
- If apply loses power after source replacement, the next apply recovers from the prepared receipt and exact backup before considering a new transaction.
- If a human edits the source after an interrupted transaction, recovery observes a third hash and stops; it never overwrites the human edit with the backup.
- If a noncooperating editor changes source during an active apply, the final hash check aborts and preserves the editor's bytes.
- If receipt commit fails during an ordinary exception, apply restores the exact base and reports failure; it does not claim a successful write without an applied receipt.
- If the same approved apply is retried after success, the matching applied receipt makes the operation idempotent and returns that receipt.

## Explicit Anti-Goals (Academic Edge-Cases)

- IntentPatch V1 is not required to survive a malicious kernel, compromised Python process, faulty RAM, or storage that falsely reports successful atomic replace or `fsync`.
- IntentPatch V1 is not required to defend against a practical SHA-256 collision or provide cryptographic non-repudiation of which human approved a token.
- IntentPatch V1 is not required to provide distributed consensus, remote leases, or correct locking semantics on NFS, SMB, object stores, synchronized folders, or multiple machines writing the same source.
- IntentPatch V1 is not required to prevent a hostile noncooperating writer from changing a local file in the final operating-system scheduling gap between the last hash check and atomic replacement.
- IntentPatch V1 is not required to stream or incrementally patch sources, contexts, or patches above their documented byte and count caps.
- IntentPatch V1 is not required to auto-merge, rebase, CRDT-resolve, fuzzy-match, selector-match, or partially apply concurrent semantic changes.
- IntentPatch V1 is not required to preserve JSON whitespace, key order, trailing-newline preference, or comments; successful candidates use ViewSpec's deterministic JSON serialization.
- IntentPatch V1 is not required to synthesize arbitrary schema additions, deletions, node topology changes, route changes, state-machine changes, or operations outside the nine V1 forms.
- IntentPatch V1 is not required to make an inverse patch overwrite later edits; inverse patches retain exact source-hash and old-value preconditions and require separate approval.
- IntentPatch V1 is not required to prove pixel equivalence, aesthetic quality, complete accessibility, cross-browser behavior, arbitrary host-app compatibility, or production deployment readiness.
- IntentPatch V1 is not required to decide how a model should translate ambiguous prose into operations; it validates and proves a proposed transaction, not the model's intent interpretation.

## CLI and MCP

```bash
viewspec patch-preview viewspec.intent.json change.intentpatch.json \
  --candidate-out candidate.intent.json --json

viewspec patch-apply viewspec.intent.json change.intentpatch.json \
  --approval vapprove_<exact-token-from-current-preview> --json
```

Add `--verify` to bind approval to canonical verification. Add `--install` only when the existing
bounded verifier is allowed to install its locked local browser-host dependencies.

The MCP tools are `build_intent_patch_context`, `preview_intent_patch`, and `apply_intent_patch`. They use the standard ViewSpec
tool envelope, enforce the MCP cwd path boundary, and return patch errors as `{code, message, fix}`
without raising a transport-level success.

## Python API

The package root exports `IntentPatch`, `IntentPatchContext`, `IntentPatchPreview`,
`IntentPatchReceipt`, `parse_intent_patch`, `preview_intent_patch`, `apply_intent_patch_file`,
`patch_context_from_review_batch`, `patch_context_from_repair_plan`,
`INTENT_PATCH_JSON_SCHEMA`, and `starter_intent_patch_payload`.
