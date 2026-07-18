# Compatibility Promise

What "stable" means for the local ViewSpec SDK: exactly which surfaces are covered, the rules
every release follows, what is deliberately not promised, and how to verify the contract
mechanically. This policy binds from `v1.0.0` onward and governs every `1.x` release; pre-1.0
betas may still change without a deprecation cycle.

The machine-readable anchor for the local contract is the `local_v1` profile: it is declared in
every exported agent-asset manifest and exposed as `viewspec.AGENT_ASSET_CONTRACT_PROFILE`. If a
future major version ever changes the contract incompatibly, it will declare a new profile
(`local_v2`) — `local_v1` is never silently redefined.

ViewSpec follows Semantic Versioning for the package version: patch releases fix bugs, minor
releases add capability without breaking the surfaces below, and anything that would break them
waits for the next major version.

## The covered surfaces

`local_v1` is the union of these surfaces:

1. **Document contracts.** IntentBundle schema version 1 (including the optional root
   `schema_version` field — a document without it is version 1, and any future intent revision
   will *require* the field, so versionless documents stay unambiguous forever) and AppBundle
   `schema_version` 1–4, plus source-bound IntentPatch `schema_version: 1` under
   `contract_profile: "local_v1"`, plus Convergence Authoring Task `schema_version: 1`. Every
   AppBundle, IntentPatch, and convergence task declares its version; readers discriminate by that
   field.
2. **Published JSON Schemas.** `agent-intent-bundle.schema.json` and
   `agent-app-bundle.schema.json`, plus `intent-patch.schema.json` and
   `converge-task.schema.json`, including their `$id` URIs and `x-viewspec-*` extension fields.
3. **Error codes and error shape.** The closed registry exported as `viewspec.ERROR_CODES`.
   Validation issues are `{code, path, message, fix}` objects; tool-level failures are
   `{code, message, fix}` (no `path`). Codes are stable identifiers: they are never renamed and
   never repurposed to mean something else.
4. **The CLI.** Every documented subcommand, its flags, and the shape of its `--json` output.
   Emitted reports and manifests each carry their own `schema_version` (or
   `manifest_schema_version`) field, as do MCP tool response envelopes.
5. **The Python API.** Every name in `viewspec.__all__`, importable from the top-level package
   with a compatible signature. Anything *not* in `__all__` — including directly importable
   submodules — is internal and may change without notice.
6. **Artifact semantics.** The semantic marker vocabulary in emitted artifacts (`data-ir-id`,
   `data-binding-id`, `data-action-*`, `data-visibility-*`, and the DOM id scheme) keeps its
   names and meaning; proof, replay, and conformance semantics for a given bundle stay fixed.
7. **Determinism.** For a given ViewSpec version, the same input bytes produce the same output
   bytes: artifacts, manifests, digests, hashes, patch identities, candidates, inverses, and
   IntentPatch approval tokens. Converge operator capabilities are deliberately random. See
   [free-sdk-reliability](free-sdk-reliability.md).

## The rules for 1.x releases

- **Additive-only document contracts.** A bundle that validates today and conforms to the
  documented contract keeps validating in every 1.x release. New optional fields and new
  AppBundle schema versions may be added; existing versions stay supported. (If a validator bug
  accepted documents *outside* the documented contract, fixing it is a bug fix, not a break —
  the promise attaches to the documented contract, with the published schemas as the record.)
- **Caps only rise.** Documented byte, count, and depth limits may be raised in a minor release,
  never lowered. A bundle within the caps today stays within the caps.
- **Error codes are add-only.** New codes may appear in minor releases; existing codes are never
  removed from the registry or reused with a different meaning while the behavior they describe
  exists. `viewspec.ERROR_CODES` is the enumerable set, enforced two-way by the test suite.
- **CLI surfaces never shrink.** No command or flag is removed or renamed within 1.x. JSON
  outputs, reports, and manifests may gain keys; documented keys are not removed or retyped
  without bumping that artifact's own schema version.
- **Python API stays importable.** Names in `__all__` keep working with compatible signatures.
  Deprecated names keep functioning (with a documented deprecation) until the next major.
- **Agent assets version every byte.** `AGENT_ASSET_SCHEMA_VERSION` increments whenever any
  exported asset changes, so cached assets are always detectably stale.

## Not promised

- **Cross-version byte stability.** Emitted artifact bytes — and therefore `artifact_hash`,
  `semantic_digest`, `state_contract_hash`, and shell hashes — may change between ViewSpec
  versions. Determinism is a *within-version* guarantee: use these hashes for same-version
  regression testing, and expect them to rotate on upgrade.
- **Schema URL immutability.** The versionless URLs (`https://viewspec.dev/*.schema.json`) serve
  the latest published schema. Discriminate documents by their `schema_version` field, not by
  what a schema URL served on some date; pin exact schema bytes via the SHA-256 hashes in
  `agent-assets.json` when immutability matters.
- **Prose and presentation.** The agent system prompt wording, documentation, demo pages, and
  human-readable report text may change in any release (asset changes always bump the asset
  schema version).
- **Internal layout.** Module structure, private helpers, and anything not in `__all__`.
- **The hosted contract.** Hosted-only fields (`design`, `motif_library`, `view_spec.inputs`,
  `view_spec.projections`, `view_spec.rules`) and hosted emitters are governed by the hosted
  compiler contract, not this page.

## Deprecation and breaking changes

- Anything that breaks a covered surface ships only in a new major version, together with a new
  contract profile and written migration notes.
- Deprecations are announced in `CHANGELOG.md` at least one minor release before any default
  behavior changes, and deprecated surfaces are removed only at the next major version.
- Every release from `1.0.0` onward documents its changes in `CHANGELOG.md`.

## Environment floor

- Python `>=3.11`. The floor may rise in a minor release only for Python versions that have
  reached upstream end-of-life; otherwise it rises only at a major version.
- Node.js `>=18` is required only for AppBundle V3/V4 (`interactive_state_v0`) reducer
  conformance; V1/V2 and all IntentBundle flows are Python-only.

## Verifying mechanically

- `viewspec check-agent-assets .viewspec --json` verifies cached agent assets byte-for-byte
  against the installed SDK (SHA-256 per file). It is a freshness check for the installed
  version, not a cross-version semantic compatibility checker: after upgrading, re-export and
  re-check.
- `from viewspec import ERROR_CODES` enumerates the closed code set.
- Pin the ViewSpec package version wherever byte-reproducibility matters; `viewspec doctor`
  reports local readiness including the Node requirement.
