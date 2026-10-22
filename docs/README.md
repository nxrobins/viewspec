# ViewSpec documentation index

User-facing documentation for the local ViewSpec SDK and CLI. Start with the top section; the
others are reference material you can read when a command, contract, or proof points you there.
Internal planning, launch, and QA notes live in [`internal/`](internal/) and are listed at the end.

## Start here

- [Getting Started](getting-started.md) — Install, the first `viewspec prove` bundle, Studio, and
  the three-command intent lifecycle in five minutes.
- [Agent Integration](agent-integration.md) — The agent-native contract: emit `IntentBundle` JSON,
  then validate, compile, and check; MCP tools and managed native-agent instructions.
- [Hosted Agent Integration](hosted-agent-integration.md) — Using the paid hosted compiler API for
  derivation, projections, custom motifs, and the SwiftUI/Flutter emitters.
- [Proof Bundle](proof-bundle.md) — How to read `.viewspec-proof/`: `PROOF.md`,
  `proof_report.json`, the redacted support bundle, and failure codes.

## Source contracts

- [AppBundle V1/V2/V3/V4](app-bundle-v0.md) — Multi-screen internal tools: fixtures, bounded state,
  visibility, the Static Shell V0 proof target, and the runnable `react-tailwind-app` target.
- [IntentPatch V1](intent-patch-v1.md) — Proof-carrying semantic transactions for changing a
  declared value in existing `IntentBundle` or `AppBundle` source.
- [Converge Sessions V1](converge-sessions-v1.md) — Bounded repeated repairs driven by Review
  feedback or verifier failures, with human approval kept in Review.
- [ViewSpec Review V0](viewspec-review.md) — The local Review protocol underneath `viewspec studio`:
  security, delivery, and lifecycle contract.
- [Interactive Compose](interactive-compose.md) — Inputs, declarative visibility rules, and action
  payload collection; the extended rules are a hosted capability.
- [Custom Motif Authoring](custom-motif-authoring.md) — Hosted schema-driven motifs and trusted local
  motif plugins that lower into `CompositionIR`.
- [Local HTML Wedge](local-html-wedge.md) — Importing existing HTML as a fallback path: sanitize,
  apply `DESIGN.md`, lift, and diff.
- [`manifest-v1.schema.json`](manifest-v1.schema.json) — JSON Schema for the
  `provenance_manifest.json` that every compiled artifact carries.

## Emitters and known limits

- [Emitter Matrix](emitter-matrix.md) — Capability matrix across HTML/Tailwind, React TSX, React
  Tailwind TSX, SwiftUI, and Flutter.
- [Known Limits: HTML/Tailwind](known-limits-html-tailwind.md) — Boundaries of the default local
  standalone HTML target.
- [Known Limits: React TSX](known-limits-react-tsx.md) — Boundaries of the local `react-tsx` target.
- [Known Limits: React Tailwind TSX](known-limits-react-tailwind-tsx.md) — Boundaries of the local
  `react-tailwind-tsx` target and its bounded host verifier.
- [Known Limits: SwiftUI](known-limits-swiftui.md) — Boundaries of the hosted-only SwiftUI emitter.
- [Known Limits: Flutter](known-limits-flutter.md) — Boundaries of the hosted-only Flutter emitter.
- [Known Limits: Accessibility Proof](known-limits-a11y.md) — Exactly what the fail-closed contrast
  and accessible-name checks prove, and what they deliberately do not.

## Policy and process

- [Compatibility Promise](compatibility.md) — What "stable" covers for the `local_v1` profile, the
  rules each release follows, and when the promise binds (from `v1.0.0`).
- [Free SDK Reliability Audit](free-sdk-reliability.md) — The supported surface of the free local
  SDK and how its reliability is checked.
- [Release Checklist](release-checklist.md) — Public facts, package build, manual PyPI publish,
  tagging and GitHub Release, and site deployment.
- [Historical Bug Prevention Contract](bug-prevention.md) — The closed `VSBUG-*` regression
  catalogue through pull request 134 and the gates that keep it structural.

Contributor process (development setup, test commands, the error-code registry rule, and the
changelog rule) lives in [`CONTRIBUTING.md`](../CONTRIBUTING.md) at the repository root, next to
[`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) and [`SECURITY.md`](../SECURITY.md).

## Audit reports

- [project-audit-2026-10-22.md](project-audit-2026-10-22.md): the 2026-10-22 engineering audit, its findings, and the resolution status of each finding.

## Internal notes (`internal/`)

Status, planning, and asset-production notes kept for the record. They describe goals and
evidence at the time they were written, not the supported product contract.

- [ViewSpec Studio Product Goal](internal/viewspec-studio-goal.md) — Studio milestone definition,
  evidence, and deliberately deferred work.
- [Core Workflow Refinement Goal](internal/core-refinement-goal.md) — The refinement plan behind the
  fixed ten-case core workflow corpus.
- [Private Studio Review Deployment Contract](internal/studio-review-deployment.md) — The hosted
  private-review deployment, worker isolation, and canary gate still pending.
- [HN Launch Demo Storyboard](internal/launch-demo-storyboard.md) — Storyboard for the launch video.
- [Launch Headliner Asset](internal/launch-headliner-asset.md) — Production notes for the launch
  proof-chain image.
- [Mobile Recorder Handoff](internal/mobile-recorder-handoff.md) — Recording handoff for the launch
  bundle on mobile.
- [Launch Browser QA Report](internal/launch-browser-qa-report.md) — Browser QA results for the
  launch demos at their baseline commits.
