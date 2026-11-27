# Browser tool comparison: revised Books contracts

Fresh application cohorts can use [`transport.py`](transport.py), the shared streaming HTTP/WS
relay, with the same public origin for every arm. It preserves binary journals and invalidates
attempts with incomplete audits. See the [readiness and transport contract](../../../docs/browser-readiness-contract.md).
The legacy Books/static runner below retains its original read-only proxy protocol; its old
measurements are not retroactively reclassified as streaming-conformant.

This is the reusable harness from the Agent Lift comparison, with the diagnosed evidence and
output-contract problems in WebArena-Verified tasks 68 and 69 corrected under protocol
`books-evidence-v1`. It is a local experimental revision, not an upstream dataset release.
These tasks have been inspected during development; they are diagnostic cases, not fresh holdouts.

The original task records and scores remain unchanged. The runner writes a separate effective
dataset, protocol manifest, exact model prompts, tool traces and results into a new output folder.
It refuses a nonempty output folder so results from different protocols cannot be silently reused.

## Contract

Both tasks inspect the top ten Hot posts in Books. Eligible evidence is each post's **title and
body**; comments, linked pages and outside knowledge are excluded.

- Task 68 returns `retrieved_data` as an array of objects with exactly `book` and `author`.
  Titles and author names are copied from eligible evidence. An unstated author is JSON `null`;
  a narrator, poster or platform does not identify the book's author. This repairs the upstream
  string-only author schema, whose own reference answer contains null.
- Task 69 returns `retrieved_data` as a deduplicated array of strings. A visible bare domain counts;
  no scheme is invented. The reference retains the literal domain and drops the upstream's
  synthesized `https://` alternative, making the exact-wording rule consistent with grading.
- Both return an object with exactly `task_type: "RETRIEVE"`, `status`, and `retrieved_data`.
  `SUCCESS` requires a nonempty array. `NOT_FOUND_ERROR` requires an empty array.

`holdout_contracts.py` produces the prompt and format validator from the same JSON schema.
The full schema is sent identically to all three tools. Reference answers remain in the private
grading dataset. Before any model attempts, both reference answers must pass the schema and the
installed WebArena grader. The official grader still checks content, while the shared schema
validator separately checks format; `success` requires both, plus valid execution within the
attempt deadline. Results record `reference_match`, `schema_valid` and `schema_errors` separately.

The active intent and result schema are locally overridden; upstream IDs and source metadata are
retained for provenance. No post content or tool implementation is changed by this protocol.

## Reproduction

Prerequisites: Python with the repo's `dev,agents` dependencies, `webarena-verified==1.2.3`,
`tiktoken` for cost analysis, Codex CLI, a Chrome executable, and the original forum fixture running
locally. The November 2026 run used GPT-6.2 Sol at low reasoning, Chrome 159.0.8154.6, Playwright MCP
0.0.86, and agent-browser 0.43.0. Supply the pinned baseline binaries with `--node-bin`; their
package/lock files are preserved in the evaluation evidence archive.

Install the bridge's Playwright with `npm ci --ignore-scripts` in the parent directory. For a
cross-tool comparison, use a disposable source copy and set `executablePath` in `bridge.js`'s
Chromium launch options to `process.env.LIFT_EVAL_CHROME`. The harness supplies that environment
variable from `--chrome`. This is the same benchmark-only override used in the original experiment;
it must not be applied to the production bridge.

```sh
python experiments/browser-agent-probe/comparison/run.py \
  --data /path/to/webarena-verified.json \
  --chrome /path/to/chrome \
  --node-bin /path/to/pinned-baselines/node_modules/.bin \
  --out /path/to/new-results \
  --tools lift playwright agent-browser --tasks wa-68 wa-69 \
  --repeats 3 --jobs 6 --calls 24 --timeout 135
python experiments/browser-agent-probe/comparison/summarize.py /path/to/new-results
```

`--origin` defaults to `http://127.0.0.1:19999`. `--protocol original` retains the original prompts
and dataset contract for a separate run. The tool allowlists, relay, model, 25-second tool timeout,
per-attempt browser sessions and seeded scheduling come from the original harness. The relay
refuses requests after the 24-call budget: the result's `calls` includes rejected attempts, so it
can exceed 24 even though at most 24 requests reach the browser. Do not omit these refusals when
interpreting an incomplete answer.

`tests/test_browser_holdout_contracts.py` covers schema consistency, nullable authors, required
fields, bare arrays, missing-result behavior, private reference separation and dataset isolation.
The tests use the emitted JSON schema as a machine-readable contract; they do not infer model
compliance from the wording of a prompt. Real model behavior belongs in the separate comparison.
