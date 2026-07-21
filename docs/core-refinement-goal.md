# Core Workflow Refinement Goal

## Goal

Refine ViewSpec's existing `intent -> compile -> inspect -> correct -> verify -> ship`
workflow until a developer or coding agent can turn a supported UI brief into a desirable,
traceable, shippable interface without editing generated output, learning compiler internals, or
having to interpret an ambiguous failure.

This is a refinement program, not a new product area. Work may improve semantic clarity,
defaults, generated output, state coverage, inspection, correction, diagnostics, terminology,
documentation, fixtures, and tests. It must not add a major new subsystem.

## Fixed evaluation corpus

The goal is judged against ten representative briefs. The briefs and their fixtures remain fixed
during a scoring run so improvements cannot be obtained by narrowing the test set.

| Brief | User outcome | Required states or constraints |
| --- | --- | --- |
| Executive dashboard | Understand business health and the most important change | Summary, trend, priority metric; desktop and mobile |
| List or table | Find, compare, and act on an item | Populated collection, clear row hierarchy, primary action |
| Detail view | Understand one record and its current status | Identity, status, ownership, supporting attributes |
| Form | Enter valid data and understand how to continue | Labels, inputs, primary submit action |
| Settings | Review and change bounded preferences | Current values, grouped controls, save action |
| Multi-step workflow | Understand progress and complete the next step | Current step, completed steps, next action |
| Dense operational console | Identify a problem quickly without losing context | Summary plus dense records and failure emphasis |
| Collection states | Understand what is happening without collection data | Loading and empty states with useful next guidance |
| Outcome states | Understand completion or recover from a failure | Success and recoverable failure states |
| Responsive content surface | Preserve hierarchy and task clarity across sizes | Canonical mobile, tablet, and desktop viewports |

## Success criteria

The goal is complete only when every gate below passes in the same revision.

### 1. The workflow is immediately understandable

- One canonical path is documented from brief to proof.
- A new user or agent can identify the editable semantic source, generated artifact, proof, and
  repair instruction without reading compiler implementation code.
- Every command or tool result names the next useful action on failure.
- Generated files consistently identify themselves as artifacts and direct corrections back to
  `IntentBundle`, `AppBundle`, or `DESIGN.md` source.

Evidence: getting-started documentation, agent instructions, and automated command-output tests.

### 2. All ten briefs complete the supported workflow

- 10/10 sources validate.
- 10/10 compile to a checked `react-tailwind-tsx` artifact.
- 10/10 verify as `conformant` at the canonical mobile, tablet, and desktop viewports.
- Required accessibility, DOM, runtime log, and screenshot evidence is present for every brief.
- No generated file is edited to obtain a pass.

Evidence: the public executable conformance report.

### 3. The default result reaches a product-quality bar

For each brief, a review of the canonical screenshots scores these five dimensions from 1 to 5:

1. task clarity,
2. information hierarchy,
3. state completeness,
4. responsive composition, and
5. fit and finish.

A brief passes when no dimension is below 3 and its mean score is at least 4. The corpus passes
when at least 9/10 briefs pass on the first compile and all 10 pass after at most one semantic
correction. Any critical usability issue, inaccessible visible state, clipped primary content, or
unrecoverable failure is an automatic failure regardless of the numeric score.

Evidence: versioned review scorecard linked to the exact source and artifact hashes, plus canonical
screenshots. Browser conformance is necessary but does not substitute for this review.

### 4. Corrections are semantic and predictable

- Every brief has at least one representative correction expressed against a semantic ID.
- 10/10 corrections can be previewed and applied through the bounded correction workflow.
- The semantic diff contains only the intended nodes, bindings, regions, styles, or states.
- The corrected source recompiles and re-verifies without editing emitted HTML, CSS, or TSX.
- A stale source, mismatched old value, or unsupported operation fails closed with a stable code and
  no partial write.

Evidence: correction fixtures, preview/apply receipts, semantic diff assertions, and verification
results.

### 5. Output is deterministic and traceable

- Compiling the same semantic source and design input twice produces byte-identical artifacts and
  identical artifact hashes.
- Every rendered element covered by the public provenance contract resolves to a stable semantic
  source reference.
- Every verification result and correction receipt binds to the exact source or artifact it judged.
- Tampering or hash mismatch is rejected before approval or shipping.

Evidence: determinism, provenance, receipt, and tamper tests across all ten briefs.

### 6. Failures are actionable

The negative corpus covers invalid semantic input, unresolved targets, unsupported constructs,
artifact tampering, browser-environment failure, and visible conformance failure. For every seeded
failure:

- the result is non-successful,
- the result includes a stable diagnostic code,
- the result explains the problem in product terms,
- the result points to semantic source or exact evidence when available,
- the result gives one bounded next action, and
- no partial or misleading success artifact is produced.

Evidence: negative fixtures and exact diagnostic contract tests.

### 7. Repeated use gets easier

- A successful first run leaves behind the intent, proof, evidence, and correction vocabulary needed
  for the next revision.
- The second revision uses semantic diff and bounded correction rather than requiring a full restart.
- The canonical happy path requires no more than four user-invoked commands from semantic source to
  browser proof; an integrated command may perform several internal checks.

Evidence: a clean-workspace journey test covering first compile and one subsequent revision.

### 8. Existing guarantees do not regress

- The full automated test suite passes.
- The public conformance corpus passes.
- Determinism, no-network defaults, safety limits, accessibility checks, and existing supported
  targets retain their documented behavior.

Evidence: CI on the same revision as the scorecard.

## Exit rule

Progress on individual gates is useful, but the goal is not declared complete until all eight gates
pass together on the fixed corpus. Passing schema validation or browser conformance alone is not
evidence that the generated interface is desirable or shippable.

## Exit evidence

The first complete evaluation passed on 2026-07-21. The canonical record is
[`conformance/refinement/gate-status-v1.json`](../conformance/refinement/gate-status-v1.json). It
binds all eight gates to the checked-in ten-case browser report, responsive screenshots, product
quality scorecard, correction proof, negative corpus, clean-workspace journey, determinism tests,
and full-suite result from the same working revision.
