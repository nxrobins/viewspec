# ViewSpec Proof Bundle

`viewspec prove --out .viewspec-proof` writes a local proof bundle for the current ViewSpec SDK. Start with `.viewspec-proof/PROOF.md`; use `.viewspec-proof/proof_report.json` when a tool or agent needs the stable JSON contract.

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
- `Checks` shows whether intent setup, compile, artifact check, host verification, and summary writing ran.
- `Host Verification` appears for React Tailwind runs and summarizes observed browser assertions when the reference host was used.
- `Policy` records network/install behavior, including whether `npm ci --ignore-scripts` was explicitly allowed.
- `Errors` lists stable codes and fixes. If validation failed, use the JSON validation `repair_checklist` or correction prompt, regenerate the full IntentBundle, and rerun `viewspec prove`.

## Review Workflow

1. Read `PROOF.md` first.
2. If status passed, inspect the generated artifact and `provenance_manifest.json` named in the proof.
3. If status failed, fix the first stable error code and rerun `viewspec prove`; do not patch generated artifacts.
4. Use `proof_report.json` for CI, MCP, or agent automation because it is the machine-readable proof contract.

