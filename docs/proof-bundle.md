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
- `Inputs And Outputs` shows the IntentBundle, DESIGN.md, generated artifact, manifest, diagnostics, JSON report, and human summary paths. Every path a proof file records is relative to the directory that holds that file, with `/` separators. `PROOF.md` sits in the proof directory, so its paths are relative to the proof directory. `proof_report.json` is in the proof directory too unless `--report-out` put it elsewhere: then `paths.proof_dir` names the proof directory from the report's own directory, instead of `.`. Join a recorded path with the report's directory before opening a file, or pass the loaded report and that directory to `viewspec.local_tools_io.absolute_report_paths()` to get back the absolute paths the proof returned. That function resolves symlinks in the directory first, as `viewspec prove` did when it wrote the report. `prove-app` follows the same rule for `app_proof_report.json` and `APP_PROOF.md`.
- `Hashes` lets reviewers compare the checked artifact, manifest, and report with the files on disk. The proof report hash covers `proof_report.json` with the top-level wall-clock `timings_ms` section removed. It is the same for the report `prove` returned and for the file read back from disk, from any working directory. Two proofs of one input share it only when their reports record the same thing:
  - For the source-artifact targets (`html-tailwind`, `react-tsx`), every recorded path must keep its position relative to the report. A generated intent inside the proof directory always does, and so does the same repository layout cloned or moved elsewhere. An input outside the proof directory is recorded as `../x` from one depth and `../../x` from another, so proof directories at different depths get different hashes.
  - `react-tailwind-tsx` proofs get a new hash on every run, because the reference-host report keeps its own timings.
  - For `prove-app`, the `html-tailwind` target behaves like the source-artifact targets. `react-tailwind-app` proofs differ by run and by directory: the host report keeps its timings, and the report records the hash of `app_analysis_evidence.json`, which names the absolute React app directory.
- `Manifest Summary` records bounded manifest facts such as emitter, node count, root aesthetic profile, compact style-delta counts, and checked aesthetic layout columns/spans.
- `Checks` shows whether intent setup, compile, artifact check, host verification, summary writing, and support bundle writing ran.
- `Host Verification` appears for React Tailwind runs and summarizes observed browser assertions, including profiled aesthetic marker/layout counters when applicable, when the reference host was used. The machine report also records `assertion_requirements` for expected `dom_count`, `style_assertion_count`, and manifest-derived `aesthetic_layout_assertion_count`, `aesthetic_profile_assertion_count`, and `grid_span_assertion_count`.
- `Policy` records network/install behavior, including whether `npm ci --ignore-scripts` was explicitly allowed.
- `Errors` lists stable codes and fixes. If validation failed, use the JSON validation `repair_checklist` or correction prompt, regenerate the full IntentBundle, and rerun `viewspec prove`.

## Redacted Support Bundle

`support_bundle.json` is a local-only triage artifact for failed first proofs. It is generated from observed proof report data, capped at 16 KiB, and must not include raw IntentBundle JSON, DESIGN.md content, generated artifact content, diagnostics content, absolute paths, environment variables, credentials, or automatic telemetry/upload behavior.

If the support bundle cannot be written or would include forbidden path content, `viewspec prove` fails closed with `PROVE_SUPPORT_BUNDLE_WRITE_FAILED` or `PROVE_SUPPORT_BUNDLE_CONTENT_FORBIDDEN`. Share `support_bundle.json` before sharing `PROOF.md` or `proof_report.json`: the full proof files name every input and output file, while the support bundle keeps only path hints. The support bundle is at `schema_version` 2 and carries no `timings_ms` section.

## Replacing A Proof Directory

`viewspec prove` and `viewspec prove-app` write a small `.viewspec-output` file into the proof directory as soon as they create it. `--force` replaces an existing directory only when it is empty or holds, at its top level, a regular file (not a symbolic link) with a name ViewSpec writes: `.viewspec-output`; a proof report, summary or support bundle (`proof_report.json`, `PROOF.md`, `support_bundle.json`, `app_proof_report.json`, `APP_PROOF.md`, `app_support_bundle.json`); or a manifest that `viewspec compile` or `viewspec compile-app` writes (`provenance_manifest.json`, `shell_manifest.json`, `viewspec_app_manifest.json`). A proof that was interrupted before it wrote its report can therefore be re-run with `--force`, while a directory ViewSpec did not write is refused with `PROVE_OUTPUT_UNRECOGNIZED` or `APP_PROOF_OUTPUT_UNRECOGNIZED`. The same rule lets `--force` replace a directory that holds `viewspec compile` or `viewspec compile-app` output. `viewspec compile-app` output is a deployable site and gets no `.viewspec-output` file.

## Review Workflow

1. Read `PROOF.md` first.
2. If status passed, inspect the generated artifact and `provenance_manifest.json` named in the proof.
3. If status failed, fix the first stable error code and rerun `viewspec prove`; do not patch generated artifacts.
4. Use `proof_report.json` for CI, MCP, or agent automation because it is the machine-readable proof contract.
5. Use `support_bundle.json` for privacy-preserving support triage before sharing any full proof files.
