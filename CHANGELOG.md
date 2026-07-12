# Changelog

All notable changes to ViewSpec are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and ViewSpec follows
[Semantic Versioning](https://semver.org/) with the compatibility rules in
[docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

### Added

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

- `demos/openapi.json` is generated from the running hosted API's typed route models and exposes
  only the public contract; admin, webhook, legacy, health, and readiness routes are excluded.
- The `remote` extra now includes `cryptography` for Ed25519 receipt verification, and the
  development extra includes Hypothesis for contract properties.
- Hosted artifact verification now ties provenance filename, role, hash, entry count, and
  diagnostics metadata back to the already verified artifact files.

### Security

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
