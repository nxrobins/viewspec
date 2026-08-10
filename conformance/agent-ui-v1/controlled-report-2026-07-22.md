# ViewSpec Agent UI Controlled Run — 2026-07-22

## Outcome

The controlled run completed successfully after the runner invalidated and restarted an initial attempt whose resumed
turns had silently fallen back to a read-only sandbox. The valid study pinned `gpt-5.6-sol`, used the same protocol,
reference, seed, toolchain, and independent scorer for all three arms, and retained source, browser, proof, token, timing,
command, and provenance evidence for every turn.

The result is unfavorable to the current ViewSpec workflow. Code-first finished with substantially higher visual
fidelity while using fewer tokens and less wall time. ViewSpec Core and Deep preserved the requested semantics,
interaction, and responsive checks, but both emitted the same sparse, vertically expanded static shell and exposed two
visible `main` landmarks. Neither ViewSpec arm passed a final layout-anchor gate.

This remains a one-task, one-replicate controlled pilot. It validates the instrumentation and identifies product gaps;
it does not establish population-level effects.

| Measure | Code-first | ViewSpec Core | ViewSpec Deep |
|---|---:|---:|---:|
| Final independent checks | 71/75 (94.7%) | 51/75 (68.0%) | 51/75 (68.0%) |
| Functional checks (excluding layout anchors) | 100% | 94.4% | 94.4% |
| Reference-layout anchor gates | 17/21 (81.0%) | 0/21 (0%) | 0/21 (0%) |
| Processed input + output tokens | 1.284M | 3.945M | 3.456M |
| Uncached input tokens | 169,891 | 272,997 | 422,930 |
| Incremental-turn tokens | 1.192M | 3.689M | 3.168M |
| Total wall time | 4.23 min | 8.26 min | 6.60 min |
| Incremental-turn wall time | 2.73 min | 5.31 min | 4.04 min |
| Deterministic-check overhead | 4.6% | 5.0% | 5.6% |
| Stable-criterion regressions | 4 | 4 | 0 |
| Native composite proofs | n/a | 3/5 | 2/5 |

Relative to code-first, Core used 3.07x total tokens and took 1.95x as long on the four incremental turns. Deep used
2.69x total tokens and took 1.48x as long on incremental turns. Deep prevented the four stable-criterion regressions
seen in the other arms, but that benefit did not offset its fidelity, token, speed, or proof-health failures.

## What the controlled run demonstrated

### 1. The monitoring now detects invalid agent execution

The first controlled attempt is retained under `.agent-eval-runs/controlled-20260722-gpt-5p6-sol-v2/` and explicitly
marked invalid. Its first turn wrote normally, but turns two through five reported that the resumed thread was read-only
and made no file changes. Source snapshots, model messages, and file-change telemetry exposed the failure immediately.

The runner now explicitly restores `sandbox_mode="workspace-write"` on every `codex exec resume`, with a regression test
covering the command construction. The study was then restarted from fresh isolated workspaces as v3; no output from the
invalid attempt contributed to the reported result.

### 2. The provenance and bounded-evidence controls worked

All valid arms recorded `gpt-5.6-sol`, seed `104729`, the same protocol/reference/scorer/runner hashes, and the same
repository commit and tool versions. User configuration was ignored, each model process exited successfully, and all
five source snapshots in every arm were distinct.

Large analyzer evidence was externalized successfully. On Deep's passing analyzer turns, approximately 188-200 KB of
evidence was retained out of band while the primary proof reports stayed below 39 KB. The prior proof-envelope overflow
did not recur.

### 3. Fidelity is still the dominant product failure

The code-first final output closely matched the compact desktop reference and passed 17 of 21 geometry gates. Both
ViewSpec arms retained all required content and behavior but produced generic static-shell chrome, excessive vertical
spacing, split navigation, weak hierarchy, and a page much taller than the reference. Their final layout result was a
genuine 0 of 21 rather than a scorer artifact.

Both ViewSpec outputs also placed the generated semantic `main` inside the static shell's `main`, so the independent
accessibility check correctly found two visible main landmarks at all three viewports. That accounts for their only
final functional miss.

This isolates a specific missing layer: ViewSpec needs governed constraints for shell topology, density, typography,
anchor relationships, and breakpoint reflow, plus an emitter that can preserve those constraints without requiring
arbitrary post-generated CSS.

### 4. The current file-first semantic workflow still costs more context

Core processed 207% more total tokens than code-first; Deep processed 169% more. For the incremental turns, Core used
209% more tokens and Deep used 166% more. The exact-model run therefore reproduces the pilot's directional finding:
the current full-AppBundle authoring workflow does not deliver the hypothesized token savings.

These results do not evaluate the task-scoped IntentPatch/Converge/MCP workflow. A delta-first arm remains necessary to
test whether ViewSpec can save tokens when the agent sees only the relevant semantic slice and compact proof deltas.

### 5. Deep proofing now completes, but proof health is not yet acceptable

Core passed native proof on turns three through five. Its first two turns failed resource-binding assertions while the
agent was still constructing the AppBundle.

Deep passed the complete Freerange + Pretext proof on turns three and five. It failed the same initial resource-binding
checks on turns one and two, then caught an `APP_STATE_REPLAY_ASSERTION_FAILED` on the state-behavior turn. This is useful
failure detection and the error is attributable, but a 2/5 complete-proof rate fails the pre-registered proof-health
gate.

Deep's zero recorded regressions is promising: the stronger checks changed agent behavior in a measurable way. The next
evaluation should distinguish prevention from delayed construction by recording criterion time-to-first-pass and repair
latency alongside regression count.

## Validity limits

- There is one task and one replicate per arm; the protocol requires 18 sessions per arm for the full study.
- The run intentionally scores the current usable workflows, not an ideal compiler-only or delta-first ViewSpec path.
- The independent scorer evaluates the static shell; native proof separately exercises generated React output.
- Layout fidelity is a normalized anchor-geometry gate, not pixel similarity or blinded aesthetic judgment.
- No mutation suite ran, so the deep mutation-detection threshold remains unevaluated.
- The working tree was intentionally dirty but identically recorded across arms; all controlled inputs are hash-bound.

## Decision and next experiment

Do not launch the 54-session study yet. The next bounded cycle should:

1. remove the nested static-shell landmark and add explicit shell-topology conformance;
2. add governed density, typography, anchor, and responsive-layout constraints to the semantic path;
3. make resource-binding failures actionable during the first construction turn;
4. repair and regression-test the state replay failure exposed by Deep;
5. add a `viewspec-delta` arm that uses task-scoped context and semantic operations rather than full-file rewriting;
6. run at least three tasks with three pinned replicates before considering the full study; and
7. add mutations only after Deep completes healthy proof on every baseline turn.

The immediate success target is: **ViewSpec must match code-first functional acceptance and visual fidelity while using
no more uncached context or incremental wall time, with complete native proof on every applicable turn.**

Machine-readable study output and all bound artifacts are retained under
`.agent-eval-runs/controlled-20260722-gpt-5p6-sol-v3/`. The aggregate is `study-report.json`; each arm also contains its
own `environment.json`, `session.json`, `summary.json`, turn artifacts, screenshots, command logs, and proof evidence.
