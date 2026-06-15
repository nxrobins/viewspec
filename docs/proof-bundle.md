# ViewSpec Proof Bundle

`viewspec prove --out .viewspec-proof` writes a local proof bundle for the current ViewSpec SDK. Start with `.viewspec-proof/PROOF.md`; use `.viewspec-proof/proof_report.json` when a tool or agent needs the stable JSON contract; use `.viewspec-proof/support_bundle.json` when you need a redacted first-run failure summary for local support triage.

The terminal output mirrors the key proof evidence for quick review: status, proof level, output paths, checked manifest summary, and React Tailwind host assertion counts when the reference host was used.

## What It Proves

The default `html-tailwind` proof is source artifact and provenance proof. It validates an IntentBundle, compiles through the public local path, runs artifact checks, records artifact and manifest hashes, and names the exact generated files that were checked.

For `react-tailwind-tsx`, the proof may also include the bounded React/Vite/Tailwind reference-host verifier when `--install` is passed. That is a reference-host runtime proof for the generated artifact, not a claim about arbitrary React apps, custom Tailwind configuration, or global CSS resets.

ViewSpec proof bundles are not pixel-perfect visual regression, accessibility certification, arbitrary host-app certification, or hosted compiler publish automation.

## How To Read `PROOF.md`

- `Status` is the first pass/fail signal. A failed proof is still useful because it records the exact failure code and next fix.
- `Proof level` tells you whether the proof is `source_artifact` or `react_tailwind_reference_host`.
- `Claim` states the strongest result the proof is allowed to claim for this run.
- `Inputs And Outputs` shows the IntentBundle, DESIGN.md, generated artifact, manifest, diagnostics, JSON report, and human summary paths.
- `Hashes` lets reviewers compare the checked artifact, manifest, and report with the files on disk.
- `Manifest Summary` records bounded manifest facts such as emitter, node count, root aesthetic profile, and checked aesthetic layout columns/spans.
- `Checks` shows whether intent setup, compile, artifact check, host verification, summary writing, and support bundle writing ran.
- `Host Verification` appears for React Tailwind runs and summarizes observed browser assertions, including profiled aesthetic marker/layout counters when applicable, when the reference host was used.
- `Policy` records network/install behavior, including whether `npm ci --ignore-scripts` was explicitly allowed.
- `Errors` lists stable codes and fixes. If validation failed, use the JSON validation `repair_checklist` or correction prompt, regenerate the full IntentBundle, and rerun `viewspec prove`.

## Redacted Support Bundle

`support_bundle.json` is a local-only triage artifact for failed first proofs. It is generated from observed proof report data, capped at 16 KiB, and must not include raw IntentBundle JSON, DESIGN.md content, generated artifact content, diagnostics content, absolute paths, environment variables, credentials, or automatic telemetry/upload behavior.

If the support bundle cannot be written or would include forbidden path content, `viewspec prove` fails closed with `PROVE_SUPPORT_BUNDLE_WRITE_FAILED` or `PROVE_SUPPORT_BUNDLE_CONTENT_FORBIDDEN`. Share `support_bundle.json` before sharing `PROOF.md` or `proof_report.json`, because those full proof files may contain local absolute paths.

## Review Workflow

1. Read `PROOF.md` first.
2. If status passed, inspect the generated artifact and `provenance_manifest.json` named in the proof.
3. If status failed, fix the first stable error code and rerun `viewspec prove`; do not patch generated artifacts.
4. Use `proof_report.json` for CI, MCP, or agent automation because it is the machine-readable proof contract.
5. Use `support_bundle.json` for privacy-preserving support triage before sharing any full proof files.
