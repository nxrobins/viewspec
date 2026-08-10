# ViewSpec Agent UI Pilot — 2026-07-22

## Outcome

The pilot successfully validated the evaluation machinery and falsified the optimistic token/speed hypothesis for
the **current file-first workflow**. It does not establish a population-level product result: this was one held-out
interface, one nondeterministic replicate, and five cumulative turns per arm.

Code-first produced the closest interface and used the least model context. ViewSpec Core preserved requested
semantics, responsiveness, and state behavior and passed all five native proofs, but it was slower, processed much
more context, missed the independent landmark rule, and remained far from the reference layout. ViewSpec Deep
matched Core's independent UI result while taking substantially more model time and context. Freerange passed in
four applicable turns, but the composed proof passed zero of five turns because the workflow encountered three
separate integration/envelope failures.

| Measure | Code-first | ViewSpec Core | ViewSpec Deep |
|---|---:|---:|---:|
| Final independent checks | 74/75 (98.7%) | 52/75 (69.3%) | 52/75 (69.3%) |
| Functional checks (excluding layout anchors) | 100% | 94.4% | 94.4% |
| Reference-layout anchor gates | 95.2% | 4.8% | 4.8% |
| Processed input + output tokens | 8.54M | 27.38M | 67.44M |
| Uncached input tokens | 497k | 822k | 1.89M |
| Cached input tokens | 7.91M | 26.40M | 65.25M |
| Incremental-turn tokens | 8.24M | 26.59M | 63.74M |
| Total wall time | 14.6 min | 18.5 min | 33.9 min |
| Incremental-turn wall time | 10.9 min | 13.1 min | 22.1 min |
| Deterministic check overhead | 1.3% | 4.7% | 2.2%* |
| Stable-criterion regressions | 1 | 1 | 1 |
| Native composite proofs | n/a | 5/5 | 0/5 |

\* Deep's apparently low deterministic overhead is not a success: every composite proof failed, and several exited
before completing all evidence phases.

Relative to code-first, Core processed 3.21× total context and 1.65× uncached input and took 20% longer on the four
incremental turns. Deep processed 7.90× total context and 3.80× uncached input and took 103% longer on incremental
turns. These are directional pilot observations, not expected population means.

## What the pilot actually demonstrated

### 1. The semantic contract is working; the agent protocol is not yet efficient

Core ended with every semantic, interaction, and responsive check passing. Its only functional miss was the same
page-landmark failure at all three viewports. All five React/native proofs passed. That is strong evidence for
semantic retention and bounded behavior.

The cost came from repeatedly reading, editing, validating, and repairing a 37 KB AppBundle plus verbose compiler
and proof output. The token-saving thesis cannot be realized by asking an agent to manipulate the entire semantic
document as a file. ViewSpec needs to make its compact semantic-delta path the default agent surface:

- emit a task-scoped context slice instead of the whole AppBundle;
- author stable-id operations through IntentPatch/Converge-style tools;
- return compact, attributable proof deltas while storing full evidence out of band;
- prevent unchanged schema/source content from re-entering the model context each turn.

The existing patch, convergence, and MCP capabilities were not used by this pilot. A subsequent arm must test them
directly before concluding that ViewSpec itself is intrinsically token-heavy.

### 2. Fidelity is the largest current product gap

Code-first closely reproduced the supplied visual reference. The ViewSpec arms retained the requested content and
behavior but emitted a different information architecture: generic ViewSpec chrome, vertically expanded cards,
different navigation placement, different density, and different typographic hierarchy. Core and Deep each passed
only one of 21 final anchor-geometry gates.

This is not solved by Freerange or Pretext. The missing layer is a governed visual constraint model that can express
shell topology, density, anchor relationships, typography, wrapping, and responsive reflow without falling back to
arbitrary CSS. The agent also needs a visual-diff feedback tool that translates geometry misses back into legal
semantic/design operations.

### 3. Deeper proofs found issues, but the composed workflow is not yet reliable

Deep produced four passing Freerange reports. Pretext passed twice and failed twice. No complete composed proof
passed. The retained failures exposed three distinct boundaries:

1. **Generated visibility typing:** the generated TypeScript declared `VisibilityWhen.is` as required even for a
   legal `{state, equals}` condition, causing `APP_REACT_VERIFY_TYPECHECK_FAILED`. The generator is at
   `src/viewspec/state_ir.py`.
2. **Proof-envelope overflow:** successful Freerange and Pretext evidence pushed the app proof beyond the existing
   256 KiB limit, causing `APP_PROOF_REPORT_WRITE_FAILED` twice. Full evidence needs content-addressed external
   storage plus a compact in-context summary.
3. **Opaque Pretext failure attribution:** two turns failed with “Runtime item status must be passed or hidden.” The
   failure may represent a legitimate layout rejection, but the surfaced message did not identify the route,
   viewport, binding, actual status, or repair. That makes a powerful check expensive for an agent to use.

The deep integration should therefore be judged on **repair leverage**, not analyzer count: failures must identify
the exact semantic source target, include the smallest legal repair menu, and avoid flooding the model with raw
evidence.

### 4. Deterministic proof runtime is not the primary bottleneck

Core's compile/proof/browser phases were only 4.7% of total wall time. Model authoring and context processing
dominated. Optimizing proof milliseconds before reducing semantic-document churn would not materially improve the
current experience. Deep's 2.2% figure is artificially low because its proofs did not all finish successfully.

## Evaluation corrections and validity limits

The first summary implementation compared whole-step percentages even when later steps introduced more criteria.
That was corrected after the run using the retained raw criterion transitions: a regression now means a previously
passing, still-applicable viewport criterion later failed. No prompt, artifact, browser result, token record, or raw
score changed. Each arm had one such regression during the final responsive turn.

Important limits remain:

- The exact Codex model was not pinned and was not emitted by the runtime. All arms used the same configured default
  during one continuous run, but the result is not externally reproducible without a model pin.
- One task and one replicate per arm are insufficient for population claims. The full design requires 18 sessions
  per arm (six tasks × three replicates).
- The common independent scorer used ViewSpec's static shell, while native proofs used the generated React app. The
  full study should score the actual React output for every arm.
- “Wrap naturally” was underspecified in the executable scorer. Visible text and horizontal overflow were checked,
  but line count and clipping were not. This remains a post-run visual observation, not a scored success.
- The layout metric is an anchor-geometry gate, not pixel similarity or a blinded aesthetic rating.
- No injected mutation suite ran, so the pre-registered 90% deep mutation-detection threshold is unevaluated.
- The arms intentionally reflect current usable workflows, so scaffolding was asymmetric: code-first began with a
  minimal document; ViewSpec began with the public AppBundle V4 starter. A compiler-only benchmark would answer a
  different question.

## Recommended next run

Do not spend on the 54-session study yet. First:

1. Fix the legal `{state, equals}` generated TypeScript type.
2. Externalize large Freerange/Pretext evidence and keep the model-facing proof summary bounded.
3. Make Pretext errors identify exact route, viewport, binding, actual status, and suggested semantic repair.
4. Add a `viewspec-delta` arm that must use task-scoped context plus IntentPatch/Converge/MCP operations; compare it
   against the file-first Core arm to test the real token-saving hypothesis.
5. Score the generated React app, add explicit line-wrap/clipping assertions, and add blinded visual review.
6. Pin model, reasoning effort, tool versions, task order randomization, and three real replicate ids.
7. Add the mutation suite only after healthy Deep runs complete end to end; then measure catch rate and repair cost.

The clearest product goal coming out of this pilot is: **an agent should never need the full AppBundle or full proof
report in context to make a small, verified UI change.**

Machine-readable results are in `pilot-result-2026-07-22.json`. Full local sessions, browser evidence, generated
artifacts, and logs are retained under `.agent-eval-runs/pilot-20260722/` and bound by SHA-256 in that result file.
