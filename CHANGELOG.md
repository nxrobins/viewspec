# Changelog

All notable changes to ViewSpec are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and ViewSpec follows
[Semantic Versioning](https://semver.org/) with the compatibility rules in
[docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

## [0.3.0b5] - 2026-07-21

### Added

- ViewSpec Review V0, a local-first loopback browser review workflow for IntentBundle and
  AppBundle sources with manifest-backed semantic annotations, exact revision identity,
  acknowledgement-based agent batches, semantic diffs, and last-good-artifact reloads.
- IntentPatch V1 as the additive `local_v1` contract for bounded semantic transactions: strict
  source-bound operations, deterministic previews, candidate compile/check proof, exact approval
  tokens, inverse patches, crash-consistent atomic apply, and durable receipts.
- A historical bug-prevention ledger with executable invariants, property and browser guards, and
  a distribution auditor that rejects version drift, unsafe archive paths, repository bloat, and
  oversized release artifacts.
- Durable Converge Sessions V1 for Review- and verifier-driven semantic repair, with source-bound
  legal-operation tasks, three-attempt/ten-minute limits, cycle detection, checksum-protected local
  state, and strict set-wise progress certificates backed by property tests.
- A human approval panel in ViewSpec Review that shows semantic before/after and progress proof,
  withholds both write capabilities from browser and agent status, and applies or rejects only the
  exact current preview after the existing origin, cookie, frame nonce, and handshake checks.
- Agent-safe Converge MCP adapters, expert CLI commands, public Python types, the published
  `converge-task.schema.json` contract and example, and agent asset schema version 13.
- Crash-consistent Converge recovery from durable IntentPatch receipts, resumable post-apply
  verification, immutable terminal-session archives, complete nine-operation task generation,
  exact verifier-context binding, bounded CLI readers, and custom-state Review integration.
- A fixed ten-case core-workflow corpus spanning AppBundle queue/detail screens, collection and
  outcome states, dense operational and dashboard surfaces, forms, landing pages, multi-step
  workflows, and settings.
- Same-revision product-quality evidence for all ten cases: canonical mobile, tablet, and desktop
  screenshots plus DOM, accessibility, and log artifacts, with a scored rubric and eight explicit
  refinement gates.
- A receipt-backed semantic-correction proof for every corpus case, plus journey, determinism,
  negative-corpus, and correction regression tests.

### Changed

- CI now treats lint, byte compilation, static public-site contracts, browser demo regressions,
  historical prevention guards, and bounded wheel/sdist inspection as mandatory release gates.
- Semantic root titles now project into traceable page headers when a brief does not author an
  explicit header; starter queue, dashboard, collection-state, and verifier-capture behavior were
  refined for clearer hierarchy, actions, state meaning, and initial-value evidence.
- The beginner workflow now leads with `viewspec prove`, gives failures a bounded next action, and
  documents the validate, compile, check, verify, and semantic-correction loop against the public
  ten-case corpus.
- No covered `local_v1` surface was removed or deprecated; the release is additive within the
  pre-1.0 compatibility policy.

### Security

- Review is loopback-only and binds browser events, batches, revision updates, cookies, frame
  nonces, and handshakes to the exact session and checked artifact; stale or cross-origin authority
  fails closed.
- IntentPatch treats Review and verifier output as proposal evidence only. Source writes require
  the exact approval token for the re-proved base, candidate, semantic diff, compile/check result,
  optional verification result, and inverse patch under an interprocess transaction lock.
- Convergence approval uses a private random 256-bit outer capability; stale authority, changed
  source bytes, out-of-task targets, evidence substitution, corrupt state, and post-apply verifier
  drift all fail closed without silently rebasing or continuing.
- Existing shared state directories are rejected without permission mutation, and already-applied
  candidates can be reconciled only from exact source hashes plus matching durable receipts.

## [0.3.0b4] - 2026-07-12

### Added

- `viewspec verify` for canonical mobile, tablet, and desktop browser conformance with
  integrity-addressed screenshots, DOM snapshots, accessibility evidence, stable `VERIFY_*`
  diagnostics, deterministic result identities, and status-specific exit codes.
- Deterministic `VerificationRepairPlan` output with canonical source-node paths, grouped
  cross-viewport repair directives, recurrence fingerprints, and exact retry lineage.
- Typed hosted `/v1/verifications` client contracts with idempotent job identities, bounded
  evidence decoding, complete artifact-set checks, and independently verified Ed25519 receipts.
- `compile_until_conformant_remote(...)`, a bounded paid workflow that combines hosted
  compile-and-verify attempts with a caller-owned semantic repair agent, plan stability,
  no-progress detection, and auditable convergence runs.
- A five-case executable public conformance corpus covering AppBundle queue/detail screens,
  data-dense and landing intents, and editable form action payloads in real Chromium.
- A canonical, dependency-free IntentBundle envelope boundary shared byte-for-byte with the
  hosted compiler, with stable error codes and JSON paths plus property-based cross-process
  parity tests.
- Typed hosted artifact compiler identity, including the deterministic compiler build id,
  source revision, contract profile, API and SDK versions, and IntentBundle contract digest.
- `compile_app_remote(...)` and `AppBundleBuildResponse` for paid, verified hosted builds of
  complete React/Tailwind AppBundle projects. The client validates nested paths, every file hash,
  the complete outer manifest, deterministic build identity, and the Ed25519 build receipt before
  returning or materializing source.
- `verify_signed_receipt(...)` as the receipt-type-neutral verifier; `verify_usage_receipt(...)`
  remains a backward-compatible alias.
- Customer-side Ed25519 verification for hosted usage receipts through
  `ReceiptPublicKey` and `verify_usage_receipt(...)`.
- Property-based remote error tests that preserve hosted error codes, JSON paths, HTTP status,
  request ids, and retry metadata on `CompilerAPIError`.

### Changed

- React/Tailwind verification accepts CSS display blockification for `inline-flex` controls and
  verifies both editable and manifest-backed static action payload bindings.
- Browser process failures retain the stable Playwright assertion line instead of replacing it
  with Node startup warnings, and blank runtime failures normalize to valid retry diagnostics.
- Hosted browser workers can use one prebuilt read-only Node dependency bundle, eliminating
  per-attempt package network access while preserving explicit local `--install` behavior.
- Hosted and local parsing now agree with JSON Schema integer semantics for
  `schema_version: 1.0`, and release checks reject any source drift in the shared boundary.
- Hosted compile metadata, health, and paid artifacts identify the exact immutable compiler
  revision that produced them.
- `demos/openapi.json` is generated from the running hosted API's typed route models and exposes
  only the public contract; admin, webhook, legacy, health, and readiness routes are excluded.
- The `remote` extra now includes `cryptography` for Ed25519 receipt verification, and the
  development extra includes Hypothesis for contract properties.
- Hosted artifact verification now ties provenance filename, role, hash, entry count, and
  diagnostics metadata back to the already verified artifact files.

### Security

- Repair attempts are bound to a verified parent, exact next attempt, stable plan, and
  owner-scoped hosted lineage before worker admission or quota use.
- Hosted verification rejects evidence path traversal, undeclared or duplicate files, forged
  hashes, oversized artifact sets, result/artifact divergence, and receipt/result drift.
- Hosted usage receipts are independently verifiable without sharing service signing material.
- Hosted artifact clients reject provenance or diagnostics metadata that diverges from the
  integrity-addressed files.

## [0.3.0b3] - 2026-07-11

### Added

- `compile_artifact_remote(...)` and typed hosted artifact response models for paid
  HTML/Tailwind, React TSX, SwiftUI, and Flutter delivery. The client fails closed on unsafe
  filenames, content or artifact-set hash mismatches, input drift, and forged build identity.
- A no-store checkout success page that exchanges Stripe's one-time Checkout session for an API
  key, removes the session id from browser history, and never writes the key to browser storage.
- Static OpenAPI discovery for plans, paid artifacts, signed usage receipts, checkout claim, and
  API-key rotation and revocation.
- Cross-repository commercial contract checks covering price, quota, custom motifs, checkout URL,
  artifact targets, signed receipts, and the exact production-canary starter.

### Changed

- Hosted product copy now matches the enforced paid contract: Pro is $149/month for 10,000 hosted
  compile calls/day, up to five custom motifs per compile, four integrity-checked artifact targets,
  signed usage receipts, and email support.
- Hosted integration documentation now distinguishes the free local compiler, portable AST
  compile response, and paid artifact-delivery response.

### Security

- Hosted artifact clients verify every file and the aggregate deterministic build identity before
  returning generated source to an agent workflow.
- Checkout key delivery is one-time and deliberately avoids local or session storage.

## [0.3.0b2] - 2026-07-11

### Added

- IntentBundle documents may declare an optional root `schema_version` (integer `1`). A document
  without the field is schema version 1; any other value fails closed with
  `UNSUPPORTED_SCHEMA_VERSION`. Starters, `viewspec init-intent`, and the exported agent example
  are now self-describing, and `viewspec.starter_intent_payload()` is the canonical starter
  payload helper.
- `viewspec.ERROR_CODES`: the closed, enumerable registry of every stable machine-readable code
  the local tool surface emits, enforced two-way by the test suite.
- PEP 561 `py.typed` marker ships in the wheel, so type checkers consume the SDK's inline
  annotations.
- `SECURITY.md` (private vulnerability reporting, the no-network posture, and the
  `--allow-outside-cwd` boundary) and `docs/compatibility.md` (the `local_v1` stability policy).
- AppBundle `schema_version: 4`: bounded `visibility_v0` conditional show/hide rules with baked
  initial visibility, `expect_visibility` replay proof, an `evaluateViewSpecVisibility` reducer
  export, and a bounded Static Shell V0 runtime that toggles visibility on declared action
  clicks.
- Runnable AppBundle React app generation: `viewspec init-app --template react-app`,
  `compile-app --target react-tailwind-app`, and `prove-app --target react-tailwind-app --install`
  now produce and verify a bounded Vite/React/Tailwind app with browser-history routes,
  host-provided resources, state mutations/selectors, visibility, and exact-artifact host proof.
- Fail-closed accessibility proofs across emitters: scoped WCAG AA contrast enumeration and
  accessible-name presence (including structural label-input association).

### Changed

- Agent asset schema version is now `11` (latest exported assets reflect the IntentBundle
  `schema_version` refresh and the runnable React app target prompt/public-surface updates).
- Documentation corrections: the Node.js requirement applies to AppBundle V3/V4 reducer
  conformance (previously stated as V3-only), and stale beta labeling was removed from the raw
  HTML import docs.
- Demo and public-site copy now aligns pricing, provenance language, and the Pro checkout link
  with `demos/public-facts.json`.

## [0.2.0] - earlier release

Pre-changelog release line. The public surfaces as of this release are described in the README,
`docs/`, and `demos/public-facts.json` at the corresponding tag.
