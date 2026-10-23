# Changelog

All notable changes to ViewSpec are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and ViewSpec follows
[Semantic Versioning](https://semver.org/) with the compatibility rules in
[docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

Follow-up to the 2026-10-22 project audit (`docs/project-audit-2026-10-22.md`). Finding ids
in parentheses refer to that document.

### Fixed

- Integer attribute and slot values render as integers after JSON and protobuf round trips:
  `100` no longer renders as `100.0`, and for integers within +/-2^53 the same bundle compiles to
  identical bytes from an in-memory dict, a JSON file, or proto bytes. `SemanticNode` takes a
  normalized snapshot of `attrs` and slot lists when it is built (tuples inside them become lists,
  as JSON and protobuf would make them); the caller's dicts and lists are left untouched, and
  later changes to them no longer reach the node. `viewspec validate-intent`, `viewspec compile`
  and the agent and MCP tools reject integers beyond +/-2^53 with `INTEGER_OUT_OF_SAFE_RANGE`
  instead of rounding them silently. The Python `compile()` API does not validate: run
  `validate_agent_intent_bundle()` on the JSON payload first, and keep larger identifiers as
  strings in bundles built in Python, which hold doubles once they pass through `to_json()` or
  `to_proto()` (C-1).
- Non-ASCII text in list and dict bindings renders as UTF-8 instead of `\u` escapes (C-2).
- `compile_auto` falls back to the hosted compiler only for unsupported motifs; a local
  `DESIGN.md` error no longer uploads the bundle (C-3).
- `viewspec mcp` exits `1`, not `120`, when its client goes away on Python 3.14. It points the
  broken stdout at the null device before it exits, so Python's exit-time flush no longer fails
  and adds two `BrokenPipeError` reports to stderr (C-9).
- The declared protobuf requirement matches the generated schema: `protobuf>=6.31.1,<8`. The
  package did not import with protobuf 5.x (P-1).
- The `agents` extra requires `mcp>=1.9.0,<2`. MCP SDK 2.x removed the `mcp.server.fastmcp`
  module that `viewspec mcp` imports, so the server could not start with it, and
  `viewspec doctor --agents` now reports `mcp_dependency: true` only when that module imports
  (P-6). An SDK whose import fails with any exception, not only `ImportError`, is reported as
  `mcp_dependency: false`, and the doctor still prints its report.
- `ConvergeError` and `IntentPatchError` survive `pickle`, `copy.copy` and `copy.deepcopy` with
  `code`, `message`, `fix` and `cli_exit` intact, and the React Tailwind emitter's
  `CompilerConstraintError` with `code` and `message` intact. Rebuilding them used to raise
  `TypeError`, so a `ProcessPoolExecutor` worker that raised one broke the pool.
- The local review daemon no longer answers an agent poll with `REVIEW_SERVER_START_FAILED` when a
  temporary file or candidate directory disappears while the session storage is being sized:
  the size walk skips entries that vanish between listing and inspection and still rejects
  symbolic links.

### Changed

- The three React dependency seeds pin `vite` 8.3.5, replacing 8.0.16, which carried a HIGH
  advisory, and declare the `source-map-js>=1.2.2` floor as an npm override (P-2, P-7).
- Lists and dicts rendered as text use compact JSON separators, `["Ann","Bob"]` and `{"a":1}`
  instead of `["Ann", "Bob"]` and `{"a": 1}`, so page text and artifact hashes change for every
  bundle that binds such a value (C-2).
- CI installs the host-verify dependency seed before the host proof and points the verifier at
  it (R-10); pins every GitHub Action to a commit at the major version each workflow already used
  (R-1); resolves Python dependencies through the pinned `requirements-ci.txt` and runs the
  Pretext workflow when the pins change (P-4); caches pip and npm, sets job timeouts, installs
  Playwright from the lockfile, installs the `agents` extra in every test leg and starts the real
  stdio MCP server there (`initialize` and `tools/list`), and cancels superseded runs only for pull
  requests, so every push to `main` runs to completion and each merged commit keeps its own
  status and distribution artifacts (R-6); imports the package at the protobuf floor (P-1); fails
  the Pretext fidelity gate when the reference fonts are missing (R-4); blocks pull requests on
  high and critical npm advisories while a weekly workflow reports every level (R-3); and reports
  the PyPI launch check without blocking the documentation deploy, so a push to `main` deploys
  the site even while that check fails (R-2).
- Dependabot watches pip, the three npm seeds and GitHub Actions (P-5). `cryptography` allows up
  to 52.x (P-6). The classifiers declare Linux and macOS; CI runs on Linux plus a Windows job
  that checks the corpus goldens, and does not exercise macOS (H-1).
- The source archive ships `tests/`, `conformance/`, `proto/`, the changelog, the contributing
  guide and the security policy, so it carries the library tests, the conformance corpus and the
  schema source. The full suite still needs a git checkout: some tests import or read
  `scripts/`, `demos/`, `docs/`, `examples/`, `integrations/` or `.github/`, or the root file
  `requirements-ci.txt`, none of which the archive includes (R-8). The package version is read
  from `src/viewspec/_version.py` alone (R-7).
- The protobuf schema source is committed as `proto/universal_ui.proto` with a regeneration
  script and a typed `.pyi` stub next to `py.typed`; CI checks the source against the generated
  code (P-3, Q-1). The `dev` extra pins `grpcio-tools` 1.80.0, which bundles protoc 31.1 and
  ships wheels for Python 3.11 through 3.14.
- `check_artifact_dir()` and the MCP check tools return structured `issues` entries
  (`{code, path, message, fix}`) in addition to the string `errors`; prefixed messages surface
  under their own registered code instead of `CHECK_FAILED`.
- `proof_report.json` and `app_proof_report.json` record every path relative to the directory
  that holds the report file, with `/` separators. At the default location that is the proof
  directory and `paths.proof_dir` is `.`; when `--report-out` puts the report elsewhere,
  `paths.proof_dir` names the proof directory from the report's directory. `PROOF.md` and
  `APP_PROOF.md` record paths relative to the proof directory they sit in. The in-memory reports
  returned by `prove()` and `prove_app()` keep absolute paths. `proof_identity.proof_report_hash`
  and the hash printed in the summaries cover the on-disk report with the top-level `timings_ms`
  removed; `prove.proof_report_hash()` and `app_reports.app_proof_report_hash()` compute it and
  return the same value for a report read back from disk, from any working directory.
  `local_tools_io.absolute_report_paths()` turns such a report back into the absolute paths the
  proof returned, `host_report.artifact_dir` included; pass it the report's directory, whose
  symlinks it resolves first. Two proofs of one input share the hash for the source-artifact
  targets (`html-tailwind`, `react-tsx`, and `prove-app --target html-tailwind`) when every
  recorded input keeps its position relative to the report, for example the same repository
  layout cloned elsewhere; an input outside the proof directory at a different depth changes the
  hash. `react-tailwind-tsx` and `react-tailwind-app` proofs get a new hash on every run because
  the host reports keep their own timings, and `react-tailwind-app` proofs also get a new hash in
  every directory because `app_analysis_evidence.json` names the absolute React app directory.
  `support_bundle.json` is at `schema_version` 2 and no longer carries `timings_ms` (C-7).
- Coded compiler and validator failures raise `CompilerInternalError` and `LocalToolContractError`
  (subclasses of `RuntimeError` and `ValueError`) with `.code` and `.message` attributes; the text
  of `str(exc)` is unchanged, and both survive `pickle` and `copy` with those attributes intact
  (Q-8). The canonical JSON helpers pinned in `tests/test_canonical.py` are aliases or thin
  wrappers of the two forms in `viewspec._canonical`, both of which reject NaN and infinity: the
  verification, repair, convergence, presentation-plan, semantic-digest, Pretext coverage and
  hosted contract helpers use the ASCII form, and the state-IR, intent-tool, AppBundle
  validation, intent-patch, converge-session and Review V0 contract helpers (and the review and
  Studio modules that reuse the Review V0 one) use the UTF-8 form. Other digests keep their own
  serialization and do not pass `allow_nan=False`: the agent-eval manifest and checkpoint digests
  (`agent_eval_value.canonical_bytes`, compact JSON plus a trailing newline), the proof report
  hashes (`prove.proof_report_hash` and `app_reports.app_proof_report_hash`, over the indented
  report text), and the inline `json.dumps` digests, whose inputs hold no non-finite numbers,
  for the verified-artifact identity in `local_verify.py`, `tailwind_recipe_registry_digest`,
  `numeric_operation_inventory_sha256`, the `compiler_benchmarks.py` summaries and the Studio
  comparison digest `ReviewManifestIndex.semantic_identity_sha256`.
  `agent_eval.canonical_json`, which writes the agent-eval output files, is indented and rejects
  NaN. No digest changed (C-5).
- Library modules log through `logging` with a `NullHandler`. `python -m viewspec.review_daemon`
  and `python -m viewspec.studio_creation_daemon` log their lifecycle and errors through a
  formatted stderr handler (`<time> LEVEL viewspec.<module>: ...`); when `viewspec review` or
  Studio starts a daemon, its stderr goes to `/dev/null`, so these logs are visible only when the
  daemon is run directly. The review server and the Studio creation room log requests at DEBUG
  with the request target redacted, so bootstrap tokens, frame tickets and the creation room's
  `/open/` token never reach their logs (Q-9). Best-effort writes that fail are logged instead
  of silently ignored; when both the app support bundle and `APP_PROOF.md` fail to write,
  `app_proof_report.json` is rewritten so that it also lists `APP_PROOF_SUMMARY_WRITE_FAILED`,
  matching the returned report and `proof_identity.proof_report_hash` (Q-10). `raise` inside
  `except` chains its cause, and `assert` statements in request and tool paths are explicit
  checks (Q-10, Q-11). The hosted compiler client lives in `viewspec.hosted_compile` and is
  re-exported from `viewspec.compiler`, which still exports every public name it had in 0.3.0b12, including `CompileRequestPayload`,
  `CompileResponse` and `DesignSystemError` (Q-2). `viewspec.sdk` exports `ListBuilder`,
  `FormBuilder` and `DetailBuilder` (Q-12). The HTML emitter no longer imports the compiler, and
  the app pipeline imports public helper names (Q-5). Dead code and unreferenced constants were
  removed from `compiler.py`, `types.py` and the Tailwind recipes (C-8). The three emitters and
  the raw-HTML importer write their files through one atomic writer that records the
  `.viewspec_write_failed.json` sentinel (Q-3).
- Every Python file is formatted with `ruff format`, which CI now checks, and the lint rule set
  adds import order, syntax upgrades, bugbear and simplification rules (Q-7). `SECURITY.md`
  describes what `viewspec check` does and does not establish, the `--force` rule, the MCP
  working-directory resolution and the input size caps (S-10, S-12).
- Internal planning notes moved to `docs/internal/`; `docs/README.md` indexes the documentation;
  README states the evaluated version of its evidence, the supported platforms and the
  compatibility horizon (D-2 to D-7, H-1). `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` were added
  (H-2); the release checklist tags and publishes each release (R-7) and says that the site
  deploy does not wait for the PyPI check (R-2).
- The single-column product surface detector matches its primary role the way the workspace
  detector does (C-9). A role now counts as primary when any of its words is `main` or `primary`,
  ignoring case, where every character other than an ASCII letter or digit separates words;
  before, only the exact roles `main`, `primary`, `primary_column` and `workspace_primary` did.
  `Main`, `Primary Column`, `primary-column` and `main_content` now match, and so do `not_main`,
  `non-primary`, `main-nav`, `main_navigation` and `primary-footer`, as they already did inside a
  workspace; `domain`, `remain` and `mainstream` do not. This changes compiled output: a bundle
  whose root has a single child region, a `stack` whose role newly matches, and no other regions
  now compiles to the `single_column_v1` product surface. Its HTML and TSX output, on all three
  targets, gains product-role styling for the page shell, the column and the dashboard, form,
  detail and hero motifs in it, and, when the root has a one-line `title` that no binding shows
  and the column holds no `hero`, an implicit page header that shows that title. Recompile such
  bundles and review the output. No corpus output changes.

### Deprecated

- `load_design_system(lint=...)`. The argument never had a local effect, because local DESIGN.md
  validation always runs; passing it now emits a `DeprecationWarning`, and it will be removed in
  the next major version. `DesignRequest(lint=...)` is still sent to the hosted compiler, which
  reads it; `compile()` no longer passes a `DesignRequest`'s `lint` flag to the local loader, so a
  hosted request compiled locally does not warn (C-9).

### Security

- `viewspec check` rejects inline event handlers (`CHECK_INLINE_EVENT_HANDLER`), `javascript:`,
  `vbscript:` and non-image `data:` URL schemes on every URL-bearing attribute
  (`CHECK_UNSAFE_URL_SCHEME`), unclosed `<script` openers (`CHECK_UNCLOSED_SCRIPT`), remote
  `feImage` and `ping` targets, auto-fetch URLs written as `http:` or `https:` without `//`
  (`src="https:evil.example/x"`), and CSS auto-fetch references hidden behind CSS escapes,
  comments or `image-set()`. It also rejects markup that browsers and Python's `html.parser` parse
  differently, where active content could hide from the scan (`CHECK_AMBIGUOUS_MARKUP`):
  comments, `<![CDATA[` and other `<![` sections, declarations other than `<!DOCTYPE html>`,
  processing instructions, raw-text elements such as `<style>` or `<title>` inside `<svg>` or
  `<math>`, the elements `noscript`, `textarea`, `xmp`, `noembed`, `noframes`, `plaintext` and
  `iframe`, a `<title>`, `<style>` or `<script>` whose text holds nested markup or whose end tag
  carries a solidus or attributes, and NUL characters. The rules scan the raw text, so they do
  not depend on how a given Python release recovers from malformed HTML. Compiler and raw-HTML
  import output never contains these constructs. Raw-HTML
  artifacts fail closed unless every element carries a manifest node id with the exact attribute
  set (`CHECK_RAW_HTML_DOM_UNGOVERNED`). The `<style>`, `<script>` and `<meta>` scans are linear
  with a block cap (`CHECK_SCAN_LIMIT_EXCEEDED`) instead of quadratic (S-1, S-2, S-3).
- `--force` on `prove`, `prove-app` and `compile-app` replaces an existing directory only when it
  is empty or holds a file ViewSpec itself writes; anything else fails closed with
  `PROVE_OUTPUT_UNRECOGNIZED`, `APP_PROOF_OUTPUT_UNRECOGNIZED` or `APP_SHELL_OUTPUT_UNRECOGNIZED`.
  `prove` and `prove-app` write a `.viewspec-output` file into their output directory as soon as
  they create it, so a proof interrupted before it wrote its report, including Studio creation's
  task-bound proof directory, can be replaced with `--force`; `compile-app` output gets no such
  file. When Studio creation still cannot replace its proof directory, the error's fix names
  that directory instead of the candidate (S-4).
- `DESIGN.md` is capped at 64 KiB (`DESIGN_FILE_TOO_LARGE`) and its front matter rejects YAML
  aliases before expansion; HTML inputs are size-gated before they are read; the MCP server
  resolves its working directory so the review tools work under the default `--cwd .`; the raw-HTML
  importer no longer drops the rest of a document after an unclosed `<base>` or `<embed>`; the
  Studio review adapter strips reserved internal headers itself instead of trusting the transport
  (S-5 to S-9).

### Added

- Hypothesis property tests over scalar value types through JSON, protobuf and compile round
  trips (T-4), and regression tests for the value rendering and hosted fallback fixes.
- Golden `artifact_hash`, `semantic_digest` and file hashes for every conformance corpus case and
  local target in `tests/fixtures/corpus_goldens.json`, checked on Linux and Windows in CI; the
  React app manifest writes POSIX paths on every platform (T-2, C-6).
- The default test suite skips, with exact reasons, the tests that need installed Node seeds, full
  git history or a Playwright browser; drift tests cover the landing page and proof explorer;
  the release and corpus scripts have smoke tests (T-1, T-3, T-5).
- Documentation tests: every error code cited in the docs exists in the registry, and the Claude
  Code skill matches the managed instructions (D-3, D-6).
- Every CLI subcommand accepts `--json` (C-9). The nine that did not, `compile`, `lift`, `doctor`,
  `init-intent`, `init-design`, `init-app`, `init-agent`, `export-agent-assets` and `mcp`, now do.
  The first eight print the standard tool envelope that the MCP tools return (`schema_version`,
  `ok`, `summary`, `diagnostics`, `external_refs`, `paths`, `next_actions`, `errors`, `metadata`),
  with command data beside it: `lift` adds `lift`, `doctor` adds `checks`, `init-agent` adds
  `target`, `root` and `changes`, and `export-agent-assets` adds `assets`. A failure is the same
  envelope on stdout with `ok: false` and `{code, message, fix}` errors, and the exit code human
  mode uses (`2` for input and contract errors, `1` for internal ones); a coded exception's message
  does not repeat its code. An invalid IntentBundle passed to `compile --json` carries `validation`
  and `correction_prompt`, and a DESIGN.md error lists its findings under `diagnostics`.
  `doctor --json` names each failing check as a `DOCTOR_CHECK_FAILED` error whose `fix` repairs
  that check: the Freerange probe's own fix for `--freerange`, whose coded errors such as
  `APP_FREERANGE_RUNTIME_MISSING` follow, a re-export or an SDK upgrade for stale `.viewspec` or
  `demos` agent assets, and the install hint for a missing MCP SDK. `init-app --json` reports the
  `resource_binding` and `binding_scope` of the bundle it wrote, as the MCP `init_app` tool does,
  so the `react-app` template reports `fixture_readonly_v0`. `viewspec mcp --json` leaves stdout
  to the protocol and writes a failure that stops the server, at startup (`MCP_DEPENDENCY_MISSING`,
  `INVALID_PATH`) or later (`MCP_SERVER_FAILED`), to stderr as one line of JSON whose message names
  the underlying error, such as `[Errno 32] Broken pipe`, rather than the task group that wrapped
  it; it also lowers the MCP SDK's log level to `ERROR`, so the SDK's line for every request stays
  off stderr (`run_mcp_server()` takes the matching `log_level`). A failure's JSON message is
  never empty: an exception without text is named by its type. Adding `--json`
  leaves output without it and exit codes unchanged, and `compile --json` writes the same artifact
  bytes as `compile`; compiled output itself changes only where the single-column role matching
  under Changed applies. New registry codes: `DOCTOR_CHECK_FAILED`, `MCP_DEPENDENCY_MISSING` and
  `MCP_SERVER_FAILED`.
- `compile(bundle, design=...)` accepts an `os.PathLike`, such as `pathlib.Path("DESIGN.md")`, and
  reads it as a DESIGN.md file with `strict_design` applied (C-9). A `str` is still DESIGN.md
  content and never touches the filesystem: `compile()` does not open it, read it or check whether
  a file by that name exists, so a `str` taken from a request can neither make `compile()` read a
  local file nor reveal which local paths exist. `compile(bundle, design="DESIGN.md")` still fails
  with `DesignSystemError` ("must begin with YAML front matter"); that error now carries an
  exception note that says to pass `pathlib.Path`, the same note for every one-line `str`.

## [0.3.0b12] - 2026-10-14

### Fixed

- Studio creation and review offer an accessible, copyable agent handoff with the exact local
  project, preserved task or active session, and recovery instructions. The handoff contains no
  access tokens and includes a manual-copy fallback when clipboard access is unavailable.
- `studio-agent-task` validates the preserved first-creation task and reference, checks in to its
  exact active room, and returns the agent task. Browser refreshes cannot impersonate agent
  activity; after a minute without activity, Studio offers Resume agent with the same brief.
- Review stops displaying Agent working after a minute without delivery activity. Resuming
  returns the same outstanding batch without resetting feedback or approving a change.
- Browser coverage executes the actual copied creation and review commands, verifies inactivity
  and recovery, preserves the brief through a failed candidate, and checks mobile copy fallback.

## [0.3.0b11] - 2026-10-14

### Fixed

- The `react-app` starter uses readable incident-record typography and stacks fields at mobile
  widths in both Static and Live renderers. Wider layouts retain three columns, and both screens
  have bounded content width and source-declared responsive presentation.
- Generated implicit app titles use compact mobile sizing instead of a large title card. Titles
  with printable Unicode characters render with their exact source provenance; one-line controls
  and the existing byte limit remain enforced.
- A browser release regression checks actual word geometry, clipping, navigation labels, long
  German fixture values, and heading presence at 390, 768, and 1440 pixels independently of
  cross-target coherence.

## [0.3.0b10] - 2026-10-14

### Fixed

- Studio keyboard annotation now reaches visible semantic headings, data fields, and actions
  through roving focus. Up/Down and Home/End move, Enter/Space selects, and selection is announced.
  Hidden or disabled targets leave the annotation order; Preview restores original tab order and
  native controls.
- Closing a request panel returns focus to the selected element in its originating preview,
  including fields whose DOM identity also appears on an inactive screen. Both visible mobile
  comparison frames are keyboard reachable from the initial launch.

## [0.3.0b9] - 2026-10-14

### Fixed

- Studio renders the exact current and proposed interfaces side by side before approval,
  without changing canonical source. Both versions must render and pass a source-bound
  comparison handshake; failed previews and changed source or design block approval.
- The approval comparison shares viewport, navigation, and scroll, supports Live or Static
  rendering and fit or full-size inspection, and can show the initial state or active declared
  checkpoint. Rejection stays available when the proposed result cannot render.

## [0.3.0b8] - 2026-10-13

### Added

- Release checks install the exact homepage command into a clean environment outside the
  checkout, open the brief/reference creation room, complete a checked candidate handoff,
  and launch the advertised Static/React comparison with a cold dependency install and a
  Chromium render check. Website deployment requires the same flow to pass against PyPI.
- The first Studio-capable package release includes the local Preview → Comment → Approve
  workflow and the packaged creation/review daemons. Archive checks now require those modules
  and the locked React build resources in both wheel and source distributions.
- Private Studio review now has a concrete replay-safe API→review ingress. Its HMAC contract binds
  the protocol, direction, method, path, exact creation headers, archive hash, timestamp, and nonce;
  a durable bounded nonce store rejects replay across process restarts; production composition can
  close direct creation; reserved internal headers are stripped from external routes before adapter
  dispatch; and the authenticated response is bound back to the originating request nonce. A
  production-like integration test proves the full create → reviewer comment → owner approval
  journey without forwarding paid or internal credentials.
- An empty workspace can now enter Studio with one agent-facing command: `viewspec studio
  --brief-file ...`. The local creation room preserves the existing task-bound acceptance contract,
  truthfully shows waiting, checking, and bounded repair states, retries only after candidate bytes
  change, and replaces the same browser tab with the checked Preview → Comment → Approve product.
  It exposes no source, schema, filesystem path, or capability to the browser and retains a
  hash-bound transition receipt with proof and timing evidence.
- Studio now has a fail-closed, explicit private-review path. `studio --share` verifies a
  short-lived Ed25519-signed production-canary release before rendering Share; the first click
  prepares and discloses the exact package without upload, and only a second confirmation creates
  a link through the daemon-held credential. Returned ingress and revision identities must match
  before owner or reviewer fragment-capability links are shown.
- The production canary verifier can emit the exact unsigned, hash-bound Share release payload for
  the production API's separate receipt signer. Missing, incomplete, expired, tampered, or
  differently originated releases keep the browser control and local upload endpoints unavailable.
- Studio comparisons now include browser-observed **Target coherence**. Exact visible static/React
  semantic pairs are checked for text, geometry, typography, and color; one deterministic mismatch
  is explained in human language, and **Review this** carries its exact checked identity into the
  existing Comment → proposal → Approve loop. The UI and machine contract retain
  `visual_parity: not_proven`.
- Dedicated Chromium coverage injects a real geometry mismatch, proves exact source-bound review
  routing and recovery, and carries a second detected mismatch through an approved semantic source
  change and coherent static/React rebuild.
- AppBundle V4 can now declare bounded scalar `state_text` projections that update the same
  exactly-once semantic binding in Static and React. The shared reducer evaluator, baked initial
  text, stable DOM marker, replay `expect_text` assertion, generated browser proof, and Studio
  checkpoint summary make the visible outcome—not only internal state—part of the checked contract.
- The retained Field Dispatch Core and Deep fixtures now prove the review counter as visible text:
  one click reveals `Review count: 1`, while the two-event replay lands both targets on
  `Review count: 2`. The recurring Studio Chromium journey exercises both outcomes at mobile,
  tablet, and desktop widths.

### Changed

- The shipped React build, browser proof, and evaluation locks now use source-map-js 1.2.2,
  fixing CVE-2026-93749. The bounded dependency regression verifies that excessive indexed
  source-map offsets are rejected and ordinary indexed maps still resolve correctly.
- Production Share canary initialization now freezes an exact build manifest: role-specific API
  and review/worker images, common backend and SDK revisions, and the SDK wheel hash. Live app
  provenance must match that manifest, and altered manifests stop resume before execution. This
  replaces the incorrect identical-image requirement; production deployment and Share remain gated.
- Private Share readiness now requires the daemon-held API credential, matching the backend's
  active paid beta eligibility check. The public receipt-key fetch remains anonymous, credentials
  stay out of browser state and daemon metadata, and private HTTP transport refuses redirects and
  environment proxies while enforcing the response-size limit during streaming. Production Share
  still requires a separately authorized signed canary release.
- Shipped React builds, browser proof, and the active evaluation seed now share the same locked
  build dependencies, including PostCSS 8.5.26 and Nano ID 3.3.18 security fixes. CI audits all three
  locks, checks shared package integrity, exercises bounded dependency regressions, and independently
  rebuilds checked Studio packages with the shipped worker seed. Direct React/Vite/integration
  versions and archived evaluation evidence are unchanged; new generated artifact identities change.
- The optional `remote` extra now requires `cryptography>=50.0.1,<51`, removing the old
  upper bound that prevented installation of current security fixes. Signed usage, artifact,
  verification, and Studio Share receipts retain their existing Ed25519 wire format.
- Studio is now canvas-first. Its proof, state, conversation, request, and approval rail is inert
  and outside the layout by default, opens at the exact moment context is needed, and closes with an
  explicit control or Escape. At 1280×720, the Desktop product now fits at least 84% instead of
  roughly 60% without changing its checked 1440×1000 viewport.
- Studio now keeps exact target viewports inside an automatically fitted review canvas. Mobile
  comparisons remain side by side; Tablet and Desktop show one canonical Live surface with the
  exact paired Static target mounted and selectable inside Details. Horizontal overflow is absent,
  and both targets remain unchanged for interaction, replay, and Target Coherence.
- Static and React AppBundle targets now share one app-shell markup and sizing contract. React
  screen roots retain the declared route path, while button, badge, shell-kicker, and route-button
  primitives use explicit inherited typography and line-height so browser defaults cannot create
  responsive target drift.
- Agent asset schema version 15 teaches managed agents the additive State Text V0 contract and
  preserves the boundary that live resource rebinding remains React-only.

## [0.3.0b7] - 2026-08-13

### Added

- The structured patch surface is now the documented default agent edit path for existing
  IntentBundle and AppBundle source. Managed agent instructions, the agent system prompt, and the
  integration docs state a two-lane rule — `patch-targets` → `patch-preview` → `patch-apply` for a
  declared value, a whole-file rewrite for anything additive or structural — and explicitly prohibit
  line-based or text-diff editing of bundle JSON and identical failing-command retries.
- Agent UI eval protocol v2.3 installs the shipped managed instruction block into the ViewSpec arm
  workspaces with `viewspec init-agent`, so the evaluation measures the guidance ViewSpec actually
  ships instead of a copy maintained inside the runner. `code-first` still receives no ViewSpec
  guidance, and each session binds the block's identity under
  `environment.inputs.managed_agent_instructions`. v2.2 and v2.3 results are not directly
  comparable.
- New read-only `viewspec patch-targets` command, `list_intent_patch_targets` MCP tool, and
  `intent_patch_targets` / `intent_patch_targets_file` Python API. They return a source's exact
  `base_source_sha256` plus one ready-to-fill stub per legally patchable target, carrying the
  operation, its fixed fields with the exact current old value, its single replacement field, and
  allowed values where the vocabulary is closed, so an agent never computes a source hash or derives
  the operation vocabulary by hand. `--op`, `--screen`, and `--limit` bound large sources, and
  `counts` stays pre-truncation so a truncated response cannot read as full coverage.

- Freerange numeric scope v2 now binds every eligible mutation and selector operation occurrence
  to the independently emitted state contract, an ordered inventory digest, the exact generated
  helper set, and runtime call sites. Reports expose exact operation coverage in addition to 100%
  required-function analysis.
- Pretext native-DOM profile/runtime v2 now binds every eligible checked-manifest text surface to
  an independently observed DOM inventory at every declared route and viewport. Reports expose
  eligible-surface counts, digests, coverage-contract identity, and per-matrix inventories.
- The dedicated opt-in E2E suite now injects division-by-zero, asserted out-of-bounds, unsafe-font,
  unsupported-wrapping, and real-overflow defects and requires real Freerange/Bun and
  Pretext/Chromium rejection. The recurring CI guard requires all three E2E scenarios to execute.
- The agent UI evaluation runner now records controlled model/runtime provenance, Codex tool and
  skill activity, authored-source and semantic diffs, per-phase timings, criterion transitions,
  proof artifact identities, and browser runtime/screenshot/text-geometry telemetry.
- AppBundle screens can now compile one deterministic, hash-bound PresentationPlan for static and
  React, with bounded breakpoint variants, named areas, motif item layouts, surface/typography
  tokens, and native semantic-anchor proof at 390, 768, and 1440 pixels.
- Bound list resource views can declare one `repeat` field-presentation contract that generates
  stable per-record nodes and bindings with canonical resource/record/field provenance. Equal
  visible scalar values across different records are now proved by identity instead of rejected.
- State replay events can name a screen/action pair plus a bounded repeat count; replay failures
  now retain exact assertion, event, mutation, path, expected, actual, selector, and visibility
  context.

### Fixed

- Release builds pin Hatchling 1.31.0 so published archives retain broadly supported Core Metadata
  2.4 instead of silently changing metadata format with the ambient build backend.
- Generated V4 reducers now typecheck state-equality visibility rules with a correctly narrowed
  `VisibilityWhen` union.
- React proof reports externalize complete analyzer payloads to a bounded, hash-linked
  `app_analysis_evidence.json` so successful large Freerange/Pretext evidence cannot overflow the
  bounded operational report.
- Pretext failed runtime items now identify the exact screen, route, viewport, surface, IR binding,
  and reported status without exposing text content.
- Embedded AppBundle screen roots no longer emit nested `<main>` landmarks, and static/React
  compilation now rejects any semantic tag divergence for the same IR identity.

## [0.3.0b6] - 2026-07-22

### Added

- An opt-in `prove-app --target react-tailwind-app --freerange` phase for manifest-scoped generated
  numeric helpers, pinned exactly to `@chenglou/freerange` `0.0.1`, plus a read-only
  `doctor --freerange` Bun readiness probe.
- Machine evidence for applicable Freerange scopes, including complete required-function coverage,
  bounded findings and contracts, source/configuration/tool hashes, transcript hashes, runtime
  identity, timings, and stable failure codes. Apps with no numeric scope report
  `not_applicable`, not `passed`.
- An opt-in `prove-app --target react-tailwind-app --pretext` native-DOM text-layout proof, pinned
  exactly to `@chenglou/pretext` `0.0.8`, with an `Arial, sans-serif` support profile, loaded-font
  Chromium observations at 390×844, 768×1024, and 1440×1000, and width-independent preparation
  cache reuse. It composes with `--freerange` and does not itself require Bun.
- Bounded `text_layout` and `analyses.pretext` evidence for scope, environment, coverage, cache,
  observations, package identity, hashes, phases, timings, and stable failures. Apps with no
  eligible compiler-owned text surfaces report `not_applicable`, not `passed`.
- A committed, environment-gated real-browser E2E suite for standalone Pretext and composed
  Freerange + Pretext proofs, including fresh package installation, exact engine identities,
  Chromium coverage/cache checks, phase integrity, and durable proof artifacts.
- A dedicated Pretext E2E workflow for relevant changes, weekly recurrence, and manual dispatch,
  with pinned Python, Node/npm, Bun, and Playwright versions, all-skipped-run rejection, and 30-day
  JUnit, toolchain, log, and proof retention.

### Changed

- Exact React AppBundle verification now runs strict TypeScript checking before optional Freerange
  analysis and before Vite build and Chromium runtime verification.
- When Pretext is requested, exact React AppBundle verification observes unchanged native DOM in
  Chromium, validates its text-layout report after the browser phase, and rechecks package and
  artifact integrity. With both options, Freerange remains between TypeScript and Vite build.

### Security

- Freerange verification fails closed on runtime, package, version, integrity, protocol, coverage,
  contract, finding, limit, timeout, and proof-input mutation failures. Bun must be installed explicitly;
  `--install` permits only the proof's pinned `npm ci --ignore-scripts` dependency step and does not
  install Bun.
- Pretext verification validates the exact lock URL, npm integrity, installed package tree,
  manifest-derived scope, protocol, full cross-viewport coverage, cache invariants, line counts,
  overflow, and immutable inputs. Unsupported or contradictory evidence fails with
  `APP_PRETEXT_*`; it is not cross-browser, Retina, canvas-rendering, or pixel-perfect proof.

## [0.3.0b5] - 2026-07-21

### Added

- ViewSpec Review V0, a local-first loopback browser review workflow for IntentBundle and
  AppBundle sources with manifest-backed semantic annotations, exact revision identity,
  acknowledgement-based agent batches, semantic diffs, and last-good-artifact reloads.
- IntentPatch V1 as the additive `local_v1` contract for bounded semantic transactions: strict
  source-bound operations, deterministic previews, candidate compile/check proof, exact approval
  tokens, inverse patches, crash-consistent atomic apply, and durable receipts.
- A historical bug-prevention ledger with executable invariants, property and browser guards, and
  a distribution auditor that rejects version drift, unsafe archive paths, repository bloat, and
  oversized release artifacts.
- Durable Converge Sessions V1 for Review- and verifier-driven semantic repair, with source-bound
  legal-operation tasks, three-attempt/ten-minute limits, cycle detection, checksum-protected local
  state, and strict set-wise progress certificates backed by property tests.
- A human approval panel in ViewSpec Review that shows semantic before/after and progress proof,
  withholds both write capabilities from browser and agent status, and applies or rejects only the
  exact current preview after the existing origin, cookie, frame nonce, and handshake checks.
- Agent-safe Converge MCP adapters, expert CLI commands, public Python types, the published
  `converge-task.schema.json` contract and example, and agent asset schema version 13.
- Crash-consistent Converge recovery from durable IntentPatch receipts, resumable post-apply
  verification, immutable terminal-session archives, complete nine-operation task generation,
  exact verifier-context binding, bounded CLI readers, and custom-state Review integration.
- A fixed ten-case core-workflow corpus spanning AppBundle queue/detail screens, collection and
  outcome states, dense operational and dashboard surfaces, forms, landing pages, multi-step
  workflows, and settings.
- Same-revision product-quality evidence for all ten cases: canonical mobile, tablet, and desktop
  screenshots plus DOM, accessibility, and log artifacts, with a scored rubric and eight explicit
  refinement gates.
- A receipt-backed semantic-correction proof for every corpus case, plus journey, determinism,
  negative-corpus, and correction regression tests.

### Changed

- CI now treats lint, byte compilation, static public-site contracts, browser demo regressions,
  historical prevention guards, and bounded wheel/sdist inspection as mandatory release gates.
- Semantic root titles now project into traceable page headers when a brief does not author an
  explicit header; starter queue, dashboard, collection-state, and verifier-capture behavior were
  refined for clearer hierarchy, actions, state meaning, and initial-value evidence.
- The beginner workflow now leads with `viewspec prove`, gives failures a bounded next action, and
  documents the validate, compile, check, verify, and semantic-correction loop against the public
  ten-case corpus.
- No covered `local_v1` surface was removed or deprecated; the release is additive within the
  pre-1.0 compatibility policy.

### Security

- Review is loopback-only and binds browser events, batches, revision updates, cookies, frame
  nonces, and handshakes to the exact session and checked artifact; stale or cross-origin authority
  fails closed.
- IntentPatch treats Review and verifier output as proposal evidence only. Source writes require
  the exact approval token for the re-proved base, candidate, semantic diff, compile/check result,
  optional verification result, and inverse patch under an interprocess transaction lock.
- Convergence approval uses a private random 256-bit outer capability; stale authority, changed
  source bytes, out-of-task targets, evidence substitution, corrupt state, and post-apply verifier
  drift all fail closed without silently rebasing or continuing.
- Existing shared state directories are rejected without permission mutation, and already-applied
  candidates can be reconciled only from exact source hashes plus matching durable receipts.

## [0.3.0b4] - 2026-07-12

### Added

- `viewspec verify` for canonical mobile, tablet, and desktop browser conformance with
  integrity-addressed screenshots, DOM snapshots, accessibility evidence, stable `VERIFY_*`
  diagnostics, deterministic result identities, and status-specific exit codes.
- Deterministic `VerificationRepairPlan` output with canonical source-node paths, grouped
  cross-viewport repair directives, recurrence fingerprints, and exact retry lineage.
- Typed hosted `/v1/verifications` client contracts with idempotent job identities, bounded
  evidence decoding, complete artifact-set checks, and independently verified Ed25519 receipts.
- `compile_until_conformant_remote(...)`, a bounded paid workflow that combines hosted
  compile-and-verify attempts with a caller-owned semantic repair agent, plan stability,
  no-progress detection, and auditable convergence runs.
- A five-case executable public conformance corpus covering AppBundle queue/detail screens,
  data-dense and landing intents, and editable form action payloads in real Chromium.
- A canonical, dependency-free IntentBundle envelope boundary shared byte-for-byte with the
  hosted compiler, with stable error codes and JSON paths plus property-based cross-process
  parity tests.
- Typed hosted artifact compiler identity, including the deterministic compiler build id,
  source revision, contract profile, API and SDK versions, and IntentBundle contract digest.
- `compile_app_remote(...)` and `AppBundleBuildResponse` for paid, verified hosted builds of
  complete React/Tailwind AppBundle projects. The client validates nested paths, every file hash,
  the complete outer manifest, deterministic build identity, and the Ed25519 build receipt before
  returning or materializing source.
- `verify_signed_receipt(...)` as the receipt-type-neutral verifier; `verify_usage_receipt(...)`
  remains a backward-compatible alias.
- Customer-side Ed25519 verification for hosted usage receipts through
  `ReceiptPublicKey` and `verify_usage_receipt(...)`.
- Property-based remote error tests that preserve hosted error codes, JSON paths, HTTP status,
  request ids, and retry metadata on `CompilerAPIError`.

### Changed

- React/Tailwind verification accepts CSS display blockification for `inline-flex` controls and
  verifies both editable and manifest-backed static action payload bindings.
- Browser process failures retain the stable Playwright assertion line instead of replacing it
  with Node startup warnings, and blank runtime failures normalize to valid retry diagnostics.
- Hosted browser workers can use one prebuilt read-only Node dependency bundle, eliminating
  per-attempt package network access while preserving explicit local `--install` behavior.
- Hosted and local parsing now agree with JSON Schema integer semantics for
  `schema_version: 1.0`, and release checks reject any source drift in the shared boundary.
- Hosted compile metadata, health, and paid artifacts identify the exact immutable compiler
  revision that produced them.
- `demos/openapi.json` is generated from the running hosted API's typed route models and exposes
  only the public contract; admin, webhook, legacy, health, and readiness routes are excluded.
- The `remote` extra now includes `cryptography` for Ed25519 receipt verification, and the
  development extra includes Hypothesis for contract properties.
- Hosted artifact verification now ties provenance filename, role, hash, entry count, and
  diagnostics metadata back to the already verified artifact files.

### Security

- Repair attempts are bound to a verified parent, exact next attempt, stable plan, and
  owner-scoped hosted lineage before worker admission or quota use.
- Hosted verification rejects evidence path traversal, undeclared or duplicate files, forged
  hashes, oversized artifact sets, result/artifact divergence, and receipt/result drift.
- Hosted usage receipts are independently verifiable without sharing service signing material.
- Hosted artifact clients reject provenance or diagnostics metadata that diverges from the
  integrity-addressed files.

## [0.3.0b3] - 2026-07-11

### Added

- `compile_artifact_remote(...)` and typed hosted artifact response models for paid
  HTML/Tailwind, React TSX, SwiftUI, and Flutter delivery. The client fails closed on unsafe
  filenames, content or artifact-set hash mismatches, input drift, and forged build identity.
- A no-store checkout success page that exchanges Stripe's one-time Checkout session for an API
  key, removes the session id from browser history, and never writes the key to browser storage.
- Static OpenAPI discovery for plans, paid artifacts, signed usage receipts, checkout claim, and
  API-key rotation and revocation.
- Cross-repository commercial contract checks covering price, quota, custom motifs, checkout URL,
  artifact targets, signed receipts, and the exact production-canary starter.

### Changed

- Hosted product copy now matches the enforced paid contract: Pro is $149/month for 10,000 hosted
  compile calls/day, up to five custom motifs per compile, four integrity-checked artifact targets,
  signed usage receipts, and email support.
- Hosted integration documentation now distinguishes the free local compiler, portable AST
  compile response, and paid artifact-delivery response.

### Security

- Hosted artifact clients verify every file and the aggregate deterministic build identity before
  returning generated source to an agent workflow.
- Checkout key delivery is one-time and deliberately avoids local or session storage.

## [0.3.0b2] - 2026-07-11

### Added

- IntentBundle documents may declare an optional root `schema_version` (integer `1`). A document
  without the field is schema version 1; any other value fails closed with
  `UNSUPPORTED_SCHEMA_VERSION`. Starters, `viewspec init-intent`, and the exported agent example
  are now self-describing, and `viewspec.starter_intent_payload()` is the canonical starter
  payload helper.
- `viewspec.ERROR_CODES`: the closed, enumerable registry of every stable machine-readable code
  the local tool surface emits, enforced two-way by the test suite.
- PEP 561 `py.typed` marker ships in the wheel, so type checkers consume the SDK's inline
  annotations.
- `SECURITY.md` (private vulnerability reporting, the no-network posture, and the
  `--allow-outside-cwd` boundary) and `docs/compatibility.md` (the `local_v1` stability policy).
- AppBundle `schema_version: 4`: bounded `visibility_v0` conditional show/hide rules with baked
  initial visibility, `expect_visibility` replay proof, an `evaluateViewSpecVisibility` reducer
  export, and a bounded Static Shell V0 runtime that toggles visibility on declared action
  clicks.
- Runnable AppBundle React app generation: `viewspec init-app --template react-app`,
  `compile-app --target react-tailwind-app`, and `prove-app --target react-tailwind-app --install`
  now produce and verify a bounded Vite/React/Tailwind app with browser-history routes,
  host-provided resources, state mutations/selectors, visibility, and exact-artifact host proof.
- Fail-closed accessibility proofs across emitters: scoped WCAG AA contrast enumeration and
  accessible-name presence (including structural label-input association).

### Changed

- Agent asset schema version is now `11` (latest exported assets reflect the IntentBundle
  `schema_version` refresh and the runnable React app target prompt/public-surface updates).
- Documentation corrections: the Node.js requirement applies to AppBundle V3/V4 reducer
  conformance (previously stated as V3-only), and stale beta labeling was removed from the raw
  HTML import docs.
- Demo and public-site copy now aligns pricing, provenance language, and the Pro checkout link
  with `demos/public-facts.json`.

## [0.3.0b1] - 2026-07-11

- Rolled in commit `42d4c8e` ("Roll changelog for 0.3.0b1 beta"); the version string itself was set in `d8d0d3b` on 2026-05-10, its entries were carried forward unchanged into `0.3.0b2`, and the release was never tagged.

## [0.2.0] - 2026-05-03

Pre-changelog release line, dated from the version bump in commit `ac0c2a3`; it was never tagged.
The public surfaces as of this release are described in the README, `docs/`, and
`demos/public-facts.json` at that commit.

Versions `0.3.0b8`, `0.3.0b6`, `0.3.0b4`, `0.3.0b2`, `0.3.0b1`, and `0.2.0` were not tagged on
the remote, so they have no compare links below; the `0.3.0b9` and `0.3.0b7` links therefore each
span two betas.

[Unreleased]: https://github.com/nxrobins/viewspec/compare/v0.3.0b12...HEAD
[0.3.0b12]: https://github.com/nxrobins/viewspec/compare/v0.3.0b11...v0.3.0b12
[0.3.0b11]: https://github.com/nxrobins/viewspec/compare/v0.3.0b10...v0.3.0b11
[0.3.0b10]: https://github.com/nxrobins/viewspec/compare/v0.3.0b9...v0.3.0b10
[0.3.0b9]: https://github.com/nxrobins/viewspec/compare/v0.3.0b7...v0.3.0b9
[0.3.0b7]: https://github.com/nxrobins/viewspec/compare/v0.3.0b5...v0.3.0b7
[0.3.0b5]: https://github.com/nxrobins/viewspec/compare/v0.3.0b3...v0.3.0b5
[0.3.0b3]: https://github.com/nxrobins/viewspec/releases/tag/v0.3.0b3
