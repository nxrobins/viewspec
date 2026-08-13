# ViewSpec Agent UI Eval V2.1 — Value Premium

V2 asks a different question from the archived V1 efficiency pilot:

> Does ViewSpec earn a bounded compute premium by producing interface systems that are more correct, durable,
> diagnosable, and reusable under sustained change?

V1 remains unchanged because its protocol and completed runs are hash-bound. V2 does not claim that ViewSpec should
use fewer tokens than direct implementation. Tokens, latency, and proof runtime are cost ceilings. Quality parity is
the entry requirement; assurance and leverage are the expected return.

## Protocol revisions

**v2.3 — arms receive the shipped managed instructions.** Through v2.2 the runner authored the
entire workspace `AGENTS.md` itself, so a ViewSpec arm never saw the managed instruction block that
`viewspec init-agent` ships to real adopters. Any change to that block was therefore invisible to
this eval. From v2.3, `_prepare_workspace` runs `viewspec init-agent --target codex` inside the
workspace for `viewspec-core` and `viewspec-deep`, appending the real block after the arm rules.
`code-first` is unchanged and receives no ViewSpec guidance. Agents still never read the ViewSpec
repository — the instructions are local workspace files, exactly as an adopter receives them.

Each session's `environment.json` now records `inputs.managed_agent_instructions` with the block's
target, applicable arms, byte count, and SHA-256, so evidence is bound to the exact guidance the
arms received and the freeze check detects any later drift.

Results are not directly comparable across this boundary: v2.2 evidence measures ViewSpec arms
operating without the shipped instructions, v2.3 measures them with. Compare `code-first` first to
separate model and runtime drift from the effect of the guidance itself.

## Primary comparison

`viewspec-deep` is the primary candidate and `code-first` is the baseline. `viewspec-core` remains a diagnostic arm
that isolates the value and cost of Freerange + Pretext from the value and cost of the ViewSpec semantic workflow.

Every session uses the same pinned model, reference, change sequence, evaluator feedback, viewports, and seed.
Sessions are paired by task and seed. Arm order must be randomized within each pair. User configuration, plugins,
network access, and evidence from other arms remain isolated.

## Evaluation shape

The V2.1 calibration protocol contains one persistent ten-turn interface lifecycle and three pinned replicates. It is
large enough to validate the redesigned instrumentation but is still a pilot. The confirmatory study remains six
tasks by three replicates by three arms: 18 sessions per arm.

The lifecycle has five explicit phases:

1. **Establishment** — create a healthy, visually faithful baseline.
2. **Evolution** — apply cumulative content, hierarchy, behavior, policy, and responsive changes.
3. **Leverage** — derive another operational view and compile equivalent static and React targets.
4. **Assurance** — inject independent, equivalent faults and negative controls after a healthy snapshot.
5. **Repair** — give only attributable evaluator feedback to the agent and measure successful recovery.

After the ten lifecycle turns, an arm that is not yet assurance-eligible or remains below the reference-layout
target receives at most two baseline-qualification turns on the same lifecycle thread. Qualification feedback is
limited to failed non-layout functional criteria, per-viewport reference-layout fidelity, stable evaluation
identities, applicable proof health, and the two target builds. Reference-anchor misses trigger this bounded quality
refinement but remain separate from mutation eligibility, so an exhausted layout miss does not suppress assurance
trials. Qualification tokens, model time, and deterministic time are included in total and post-establishment
premiums. An arm that remains functionally unhealthy after the bounded qualification budget records inapplicable
trials; the 100% functional gate is never relaxed.

The first V2 task deliberately keeps its enduring visual anchors in the original dispatch composition. Later content
is appended after that region, so anchor scoring measures whether repeated changes preserve the established layout
rather than rewarding a wholesale redesign.

## Required evidence

### Quality and durability

- Final functional acceptance and anchor-layout fidelity.
- Stable criteria gained, lost, recovered, and unresolved.
- Regression episodes and turns to recovery.
- First healthy turn, healthy-turn rate, and final health.
- Browser runtime, accessibility, overflow, clipping, and interaction evidence at every viewport.

### Assurance trials

Each healthy final snapshot receives at least five independent, task-equivalent mutations, one at a time, with source
restored between trials:

1. break an action-to-state transition;
2. corrupt a numeric state operation or invariant;
3. break a visibility/reveal contract;
4. introduce ambiguous resource-to-view binding; and
5. introduce a text-layout or clipping defect.

Two unchanged negative-control trials measure false positives. A trial records whether the normal arm-specific
evaluator detected the fault, the attributable error code, detection time, whether the agent repaired it, repair
tokens/time/commands, and whether every previously passing criterion remained intact after repair.

Mutations are matched at the observable contract level, not by applying the same source-text edit to dissimilar
implementations. They must be written and hash-bound before any evaluated run.

### Leverage trials

The final semantic system is evaluated as both the static-shell and native React targets. Both targets must pass the
same applicable semantic, interaction, state, accessibility, responsive, and layout contracts. The report records
per-target pass status and cross-target parity. The code-first arm receives equivalent deliverables and setup so this
is additional delivered value, not free work assigned only to ViewSpec.

### Cost

Record total and post-establishment model tokens, uncached input, model time, deterministic time, repair cost, and
human review time separately. Never combine them into one opaque score. Report the complete outcome vector and the
Pareto frontier before applying gates.

## Pre-registered decision rule

The primary `viewspec-deep` arm must pass every category:

- final functional acceptance of 100%, with no loss versus code-first;
- at least the protocol's layout-fidelity floor (0.6769 for the seed-104729 shakedown,
  calibrated to its code-first baseline) and no more than two percentage points below code-first;
- at least 75% fewer stable-criterion regressions;
- 100% healthy native proofs on applicable turns;
- at least 90% mutation detection and 90% successful repair;
- at most 5% false positives on negative controls;
- 100% target pass rate and at least 95% cross-target parity;
- no more than 3x total tokens, 2x post-establishment tokens, or 1.75x post-establishment wall time;
- no more than 20% deterministic proof overhead; and
- complete, consistent provenance.

Missing assurance or target evidence is `null`, not a pass. The diagnostic Core arm never substitutes for the primary
Deep result. A study cannot pass until the pre-registered sample size is met.

V2.1 distinguishes structurally well-formed records from substantive evidence. Cross-target evidence is complete
only when both required targets build and retain a functional score plus parity at 390, 768, and 1440 pixels. A
missing React parity makes the session parity `null`; static parity cannot mask it. False-positive rates pool only
controls that actually executed.

This is intentionally a conjunctive rule. Excellent proofs do not excuse poor fidelity, and low cost does not excuse
weak assurance. We are testing whether the premium is earned.

## Machine-readable evidence envelope

The paired runner writes the detailed session-level evidence atomically:

```json
{
  "value_evidence": {
    "mutation_trials": [
      {
        "id": "break-escalation-visibility",
        "order": 2,
        "applicable": true,
        "repair_applicable": true,
        "baseline_sha256": "...",
        "mutated_sha256": "...",
        "expected_detectors": ["replay-or-browser:show_escalation_panel"],
        "observed_detectors": ["replay-or-browser:show_escalation_panel"],
        "detected": true,
        "repaired": true,
        "repaired_sha256": "...",
        "repair_usage": {},
        "repair_wall_time_ms": 1234
      }
    ],
    "negative_control_trials": [
      {
        "id": "control-unchanged-a",
        "order": 5,
        "applicable": true,
        "baseline_sha256": "...",
        "detected": false
      }
    ],
    "target_trials": [
      {
        "id": "native-react",
        "applicable": true,
        "build": {"ok": true},
        "functional_acceptance": 1.0,
        "layout_fidelity": 0.94,
        "passed": true,
        "parity": 0.98,
        "parity_by_viewport": {"390": 0.98, "768": 0.99, "1440": 0.99},
        "score_artifact": ".../browser-score.json"
      }
    ]
  }
}
```

Repair tokens and model time are included in both total and post-establishment premiums. Mutation, control, target,
and repair verification remain separately visible as deterministic overhead.

Both React profiles are built from `conformance/agent-ui-v2/react-dependencies/package-lock.json`. The local
network-free seed includes the exact Core runtime plus `@chenglou/freerange@0.0.1` and
`@chenglou/pretext@0.0.8`; authored target manifests may add only those exact integration pins.

## Commands

```bash
npm ci --ignore-scripts --prefix conformance/agent-ui-v2/react-dependencies

PYTHONPATH=src python scripts/run_agent_ui_eval.py \
  --protocol conformance/agent-ui-v2/protocol.json plan

PYTHONPATH=src python scripts/run_agent_ui_eval.py \
  --protocol conformance/agent-ui-v2/protocol.json \
  run-pair --with-value-trials --task field-dispatch-lifecycle --seed 104729 \
  --model gpt-5.6-sol --out <empty-pair-dir>

# Resume only after protocol, model, source, and checkpoint hashes validate.
PYTHONPATH=src python scripts/run_agent_ui_eval.py \
  --protocol conformance/agent-ui-v2/protocol.json \
  run-pair --with-value-trials --resume --task field-dispatch-lifecycle --seed 104729 \
  --model gpt-5.6-sol --out <existing-pair-dir>

PYTHONPATH=src python scripts/run_agent_ui_eval.py \
  --protocol conformance/agent-ui-v2/protocol.json \
  summarize --runs <directory-containing-session-json-files>
```

`run` remains available for single-arm diagnostics and accepts the same opt-in `--with-value-trials` flag. Value
trials are rejected for V1 protocols. `run-pair` records the seeded arm order before execution, runs the arms
sequentially, retains production React `dist` trees and logs, checkpoints every lifecycle/trial boundary, and emits
a blinded screenshot packet for exploratory review. A repair-attempt checkpoint is written before every isolated
model call, so `--resume` verifies the retained repair workspace instead of ever invoking that repair a second time.
Human ratings do not affect the V2 pass gate.
