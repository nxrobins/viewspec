# ViewSpec Studio Human-Value Study V1

## Decision question

Does the complete ViewSpec Studio workflow help a new user create, direct, understand, and privately
hand off a better product—and does the resulting product earn preference over a matched code-first
workflow often enough to justify ViewSpec's compute premium?

This study is confirmatory. It is not a usability demo, a screenshot survey assembled after seeing
results, or permission to expose Share. The mechanical product journey, V2 assurance evidence, human
outcomes, and the production private-review canary remain separate gates.

## Preregistered design

- Two randomized between-subjects arms: `code-first` and `viewspec-studio`.
- 18 analyzable new-user sessions per arm.
- Four reserve participant slots per arm, activated in their seeded arm order only for an allowed
  exclusion before first product exposure.
- 18 independent blinded pairwise reviews, one per independently produced pair.
- Each primary comparison has one preregistered reserve reviewer bound to the identical packet;
  the reserve is used only if the primary reviewer has an allowed pre-review exclusion.
- Every analyzed final product appears in exactly one primary comparison.
- Participants must have used a coding agent for UI work in the prior 30 days, while having no
  prior ViewSpec use or contribution. Participant and reviewer slots are anonymous study
  identifiers. Do not retain names, email addresses, prompts containing personal data, or customer
  data.

Task failure, low ratings, slow work, incomplete changes, a broken render, or dislike of the product
are outcomes. They cannot be exclusions. Review ties remain in the denominator and do not count as a
ViewSpec preference.

## Matched journey

Both arms receive the same Field Dispatch reference and the same four source-contract steps:

1. Create the initial working interface.
2. Apply the content evolution.
3. Apply the hierarchy evolution.
4. Apply the bounded state evolution.

Both arms must retain final static and React products at 390, 768, and 1440 pixels. The code-first
arm authors its targets directly. The Studio arm authors canonical semantic source only and uses
Preview → Comment → Approve. A facilitator may read the script and resolve infrastructure, but may
not coach product decisions or rescue a failed task.

The clock starts when the participant sees the initial brief. `first_value` ends when a working
render is visible. `three_changes` ends when the third requested change is either accepted or the
participant stops. `private_handoff` starts from the checked final revision and ends when a second
browser has opened the production private link and submitted an acknowledged semantic comment.

## Human measurements

The primary ViewSpec arm must meet every preregistered threshold:

| Outcome | Gate |
| --- | --- |
| First working interface | At least 80% within 60 seconds. |
| Desirable first value | At least 80% rate it 4 or 5 on a five-point scale. |
| Three-change completion | At least 80%, with no stable-criterion regression. |
| Health comprehension | At least 80% correctly identify what is checked and what remains unproved. |
| Private handoff | At least 90% complete it through the production canary within five minutes. |
| Blinded preference | At least 65% of all comparisons prefer ViewSpec, and the 95% Wilson lower bound is above 50%. |

The two scored health answers are deliberately exact:

- checked: `source_artifact_and_declared_contract_health`
- unproved: `human_desirability_visual_parity_and_production_behavior`

The questionnaire may use plain-language answer choices, but the retained record stores these
normalized values. The evaluator derives pass/fail; facilitators do not record their own pass flag.

## Blinding contract

Review packets are created only after the analyzed session set is fixed. Each reviewer receives one
`R###` directory containing Candidate A and Candidate B at mobile and desktop, for both static and
React. Packets omit arm names, source session ids, tool chrome, source filenames, and PNG metadata.
The response is bound to the exact packet-tree hash. Reviewers attest that they saw no arm identity
and reviewed both targets before choosing A, B, or tie.

Keep `blinding-key.json` and `review-packet-key.json` away from reviewers. Do not unblind early,
replace an unattractive output, or regenerate a packet after it has been shown.

## Evidence lifecycle

Initialize the study once in an empty, access-controlled directory:

```bash
python scripts/run_studio_product_study.py init --out <study-directory>
```

Generate a closed record template for the assigned slot, then replace every placeholder with
observed evidence:

```bash
python scripts/run_studio_product_study.py session-template \
  --study <study-directory> --slot P001
```

After the participant set is complete and validated, freeze the anonymized review packets:

```bash
python scripts/run_studio_product_study.py build-review-packets \
  --study <study-directory>
```

Generate the reviewer record template only after packets exist:

```bash
python scripts/run_studio_product_study.py preference-template \
  --study <study-directory> --slot R001
```

Finally, verify hashes, allocation, reserve activation, sample size, artifacts, canary evidence, all
rates, and the Wilson interval:

```bash
python scripts/run_studio_product_study.py summarize \
  --study <study-directory> --out <study-directory>/study-report.json
```

Initialization hash-binds the runner, product protocol, task protocol, reference, seeded allocation, and
blinding key. Summary fails closed on missing records, unknown fields, changed packets, invalid
artifact hashes, optional stopping, unused reserve data, or absent production-canary evidence.

The canary evidence is not one operator checkbox. It is a hash-bound nine-stage run—deployment,
ingress, rebuild, isolation, Chromium, Firefox, WebKit, recovery, and leak audit—produced by the
resumable runner and independently rechecked before study analysis:

```bash
python scripts/run_studio_production_canary.py init \
  --out <canary-directory> \
  --driver <deployment-owned-stage-driver.py> \
  --deployment-manifest <reviewed-build.json>
python scripts/run_studio_production_canary.py run --root <canary-directory>
python scripts/check_studio_production_canary.py \
  <canary-directory>/production-canary-evidence.json
```

The reviewed build manifest binds the backend revision, SDK revision and wheel hash, plus separate
immutable API and review/worker images. Initialization retains a canonical copy and derives the
deployment hash; every live role must match its assigned build. The run binds this deployment,
collector, runner, verifier, stage order, command receipts, and stage
artifacts. It checkpoints after every valid stage, retains hashes and byte counts rather than
secret-bearing command output, and refuses changed code or evidence on resume. The human-study
summary invokes the same verifier; copying a report-shaped JSON object into the study does not
satisfy the gate.

## Stop rules

- Do not start participant sessions until the production private-review canary is authorized and
  the exact study build is frozen.
- Do not expose Share merely because the study harness exists.
- Do not inspect aggregate outcomes before the preregistered analysis set is complete.
- Do not add participants, alter thresholds, change exclusions, or revise the task after seeing data.
- Do not claim the full product passed from the human report alone; combine it with the mechanical
  journey, assurance trials, provenance audit, and production canary in a separate final audit.
