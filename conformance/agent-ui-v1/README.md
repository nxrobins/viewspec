# ViewSpec Agent UI Eval V1

This directory pre-registers the first reproducible pilot for measuring the agent-facing value of ViewSpec.
It compares three isolated arms using the same model, visual reference, task sequence, acceptance checks, and
seed:

1. `code-first`: author a standalone interface directly.
2. `viewspec-core`: author an AppBundle as the source of truth and compile/prove it with ViewSpec.
3. `viewspec-deep`: use the same AppBundle workflow with Freerange and Pretext enabled for the final proof.

The pilot is intentionally one task by three arms by one seed. Its purpose is to validate the instrumentation,
surface unfairness, and estimate the cost and variance of a later six-task by three-arm by three-seed study. It
must not be presented as population-level evidence that one workflow is better.

## Recorded measures

- Codex uncached input, cached input, cache-write input, output, and reasoning-output tokens.
- Model wall time and deterministic compile/proof/browser time separately.
- Cumulative semantic, interaction, responsive, accessibility, and anchor-layout acceptance.
- Regressions between iterations and tokens per accepted iteration.
- ViewSpec proof status and, in the deep arm, explicit Freerange and Pretext status.
- Exact model/runtime/repository provenance and whether user Codex configuration was isolated.
- Per-turn command, failure, output-volume, file-change, and loaded-skill telemetry from the raw Codex event stream.
- Authored-source snapshots, byte/line growth, unified diffs, and semantic AppBundle diffs.
- Compile, native-proof, browser, and source-snapshot phase timings rather than one opaque deterministic total.
- Criterion gains, losses, and recoveries; proof phase/coverage/artifact hashes; browser console, request, navigation,
  DOM, screenshot-hash, overflow, clipping, and opt-in text-wrapping evidence.

The browser scorer captures desktop, tablet, and mobile evidence. Anchor-layout comparison is deliberately not
called pixel fidelity: it compares the normalized geometry and typography of implementation-independent text
anchors. Screenshots remain available for blinded human or vision-model review.

## Pre-registered pilot thresholds

The candidate full-study thresholds are stored in `protocol.json`: no acceptance loss, 30% fewer total tokens,
40% fewer iteration tokens, 30% faster median iteration, 50% fewer regressions, at least 90% applicable deep
mutation detection, and less than 20% deterministic proof overhead. A pilot result is directional and cannot
pass the future full-study sample-size gate.

## Commands

```bash
PYTHONPATH=src python scripts/run_agent_ui_eval.py plan
PYTHONPATH=src python scripts/run_agent_ui_eval.py run --task field-dispatch --arm code-first --seed 104729 --model <exact-model-id> --out <empty-dir>
PYTHONPATH=src python scripts/run_agent_ui_eval.py summarize --runs <directory-containing-session-json-files>
```

Live runs use the authenticated `codex exec --json` runtime. The runner requires an exact model id and ignores
user Codex config/plugins by default so unrelated skills cannot confound token measurements; pass
`--allow-user-config` only for an explicitly non-controlled diagnostic run. ViewSpec proof and browser phases may install pinned
dependencies and launch Chromium, so they are explicit opt-in work rather than part of ordinary unit tests.

The first completed run and its validity audit are recorded in
[`pilot-report-2026-07-22.md`](pilot-report-2026-07-22.md). It is a harness-validation pilot, not a product claim.

V1 is now archived and remains unchanged because completed evidence is hash-bound to this protocol. The active
evaluation thesis and cost-premium design are in [`../agent-ui-v2/README.md`](../agent-ui-v2/README.md).
