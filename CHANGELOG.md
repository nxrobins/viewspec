# Changelog

All notable changes to ViewSpec are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and ViewSpec follows
[Semantic Versioning](https://semver.org/) with the compatibility rules in
[docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

### Added

- `viewspec.compile_mcp_app()` and the `mcp-app` compile target turn an `IntentBundle` into an
  [MCP Apps](docs/mcp-apps.md) `ui://` resource. `viewspec compile --target mcp-app` takes `--uri`
  and `--action-tool ACTION=TOOL` and writes `mcp_app.json` beside the artifact, and the MCP
  `compile` tool takes `uri` and `action_tools`. A pinned bridge handles the host handshake and
  sizing, sends actions as `tools/call`, `ui/message`, or `ui/update-model-context`, and applies
  `structuredContent.viewspecBindings` from tool results. `viewspec check` pins the bridge and
  enforces a 120,000 byte ceiling (`MCP_APP_BRIDGE_MISMATCH`, `MCP_APP_TOO_LARGE`). Other
  `html-tailwind` output is byte-identical.
- `viewspec.mcp_apps.register_mcp_app()` publishes the resource on a FastMCP server and returns a
  decorator for the tools linked to it, including view-only tools through `visibility=["app"]`.
- `examples/mcp_app_incident_console.py`, and a CI job that runs compiled apps against the
  `@modelcontextprotocol/ext-apps` SDK host in Chromium.
- `viewspec.ViewSpecError`, the base of `CompilerInputError`, `UnsupportedMotifError`,
  `DesignSystemError`, `AppBundleError`, and the stable-code errors.

### Changed

- `viewspec.__all__` is now the 13 names in the README: it gains `compile_mcp_app`,
  `ViewSpecError`, `DesignSystemContext`, `load_design_system`, and `ERROR_CODES`, and the concrete
  errors leave it. They stay importable from `viewspec`.
- The `agents` extra now needs `mcp>=1.30`. `FastMCP.resource()` takes `meta` from 1.26, and before
  1.30 a settings warning reaches the stderr that `viewspec mcp --json` keeps for its failure report.

## [0.4.0b1] - 2026-10-31

ViewSpec is now a local, open-source compiler with no hosted service and a much smaller surface.
This is a breaking release; `docs/compatibility.md` allows that before 1.0.

### Added

- `viewspec mcp` is rebuilt as a thin stdio server with exactly five tools, `init`, `compile`,
  `check`, `prove`, and `studio`, over the same code as the CLI. It keeps the `agents` extra
  (`mcp>=1.9.0,<2`), confines every path to its working directory unless `--allow-outside-cwd` is
  passed, and returns failures as reports with a code and a fix. The `studio` tool runs one
  background preview and returns its URL. The server ends promptly on SIGTERM or SIGINT and removes
  the preview's build directory.
- `viewspec.check()`, and the `viewspec.AppBundle` class with `load()`, `from_json()`, and `to_json()`.
- `viewspec check` on a compiled AppBundle directory checks every screen and compares the files
  above the screens with the hashes in their manifest. It used to fail, because the provenance
  manifests live one level down.
- Golden `artifact_hash`, `semantic_digest` and file hashes for every conformance corpus case and
  local target in `tests/fixtures/corpus_goldens.json`, checked on Linux and Windows in CI; the
  React app manifest writes POSIX paths on every platform.
- The default test suite skips, with exact reasons, the tests that need installed Node seeds, full
  git history or a Playwright browser; drift tests cover the landing page;
  the release and corpus scripts have smoke tests.
- Documentation tests: every error code cited in the docs exists in the registry, and the Claude
  Code skill matches the managed instructions.
- `compile(bundle, design=...)` and `compile_html(html, design=...)` accept an `os.PathLike`, such
  as `pathlib.Path("DESIGN.md")`, and read it as a DESIGN.md file; `compile()` applies
  `strict_design` to it. For `compile()`, a `str` is still DESIGN.md content and never
  touches the filesystem: `compile()` does not open it, read it or check whether a file by that
  name exists, so a `str` taken from a request can neither make `compile()` read a local file nor
  reveal which local paths exist. `compile(bundle, design="DESIGN.md")` still fails with
  `DesignSystemError` ("must begin with YAML front matter"); that error now carries an exception
  note that says to pass `pathlib.Path`, the same note for every one-line `str`.
- `TailwindRecipeRegistry`, a read-only set of the React Tailwind emitter's recipe tables, and
  `DEFAULT_TAILWIND_RECIPE_REGISTRY`, the checked-in one. `ReactTailwindTsxEmitter`,
  `emit_compiler_result`, both `resolve_manifest_recipe_metadata` functions,
  `tailwind_recipe_registry_projection`, `tailwind_recipe_registry_digest` and
  `react_contrast_report` take an optional `recipe_registry=` keyword (default
  `DEFAULT_TAILWIND_RECIPE_REGISTRY`); build a variant with `dataclasses.replace`. A registry
  copies its tables into read-only mappings and tuples when it is built (contract values given as
  a set in sorted order), and its digest covers every field. It is checked before an emit writes
  anything and before it has a projection or digest: unsafe classes, a missing recipe, overlay or
  rule id, or a value of the wrong type, such as a contract value given as a string or a
  `rule_precedence` given as a set, fail with the emitter's stable constraint codes. The grid
  classes (`GRID_CLASS_BY_COLUMNS`, `GRID_SPAN_CLASS_BY_COLUMNS`) are not registry fields and stay
  fixed.

### Changed

- The CLI is five commands. `init` replaces `init-intent`, `init-app`, and `init-design`
  (`viewspec init [intent|app|design]`). `compile` now also compiles an AppBundle, replacing
  `compile-app`. `check` now also validates a source file, replacing `validate-intent` and
  `validate-app`. `prove` takes the source as an argument and also verifies a compiled directory,
  replacing `prove --intent`, `prove-app`, `verify`, and `verify-host`.
- `viewspec studio` is now a small loopback preview that rebuilds and reloads when the source or
  `DESIGN.md` changes. It no longer starts a background daemon: it runs until interrupted.
- `viewspec.__all__` has 12 names: `IntentBundle`, `AppBundle`, `ASTBundle`, `ViewSpecBuilder`,
  `compile`, `check`, `prove`, `__version__`, and four exceptions. `compile()` now also accepts an
  `AppBundle`; `prove()` now takes a source path first.
- The three React dependency seeds pin `vite` 8.3.5, replacing 8.0.16, which carried a HIGH
  advisory, and declare the `source-map-js>=1.2.2` floor as an npm override.
- Lists and dicts rendered as text use compact JSON separators, `["Ann","Bob"]` and `{"a":1}`
  instead of `["Ann", "Bob"]` and `{"a": 1}`, so page text and artifact hashes change for every
  bundle that binds such a value.
- Dependabot opens one grouped minor-and-patch pull request a week for pip, the three npm seeds
  and GitHub Actions, and groups each ecosystem's security updates into one pull request, so an
  npm security fix updates the seeds together. It proposes no semver-major version update
  and never proposes the packages pinned as contracts in the code, tests or workflows;
  `CONTRIBUTING.md` gives the hand-bump steps for a major update in each ecosystem and for each
  of those packages. Pip version updates move the exact pins
  (`versioning-strategy: increase-if-necessary`) and leave the dependency ranges in
  `pyproject.toml`, the minimum versions the package asks of its users, alone while they allow
  the new release.
  `cryptography` allows up to 52.x. The classifiers declare Linux and macOS; CI runs on
  Linux plus a Windows job that checks the corpus goldens, and does not exercise macOS.
- The source archive ships `tests/`, `conformance/`, `proto/`, the changelog, the contributing
  guide and the security policy, so it carries the library tests, the conformance corpus and the
  schema source. The full suite still needs a git checkout: some tests import or read
  `scripts/`, `demos/`, `docs/`, `examples/`, `integrations/` or `.github/`, or the root files
  `requirements-ci.txt` and `.pre-commit-config.yaml`, none of which the archive includes.
  The package version is read from `src/viewspec/_version.py` alone.
- The protobuf schema source is committed as `proto/universal_ui.proto` with a regeneration
  script and a typed `.pyi` stub next to `py.typed`; CI checks the source against the generated
  code. The `dev` extra pins `grpcio-tools` 1.80.0, which bundles protoc 31.1 and
  ships wheels for Python 3.11 through 3.14.
- Internal planning notes moved to `docs/internal/`; `docs/README.md` indexes the documentation;
  README states the evaluated version of its evidence, the supported platforms and the
  compatibility horizon (D-2 to D-7, H-1). `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` were added
  (H-2); the release checklist tags and publishes each release (R-7) and says that the site
  deploy does not wait for the PyPI check.
- The Pretext E2E workflow now pins npm to its major.minor line (10.9) instead of the exact patch
  release 10.9.8. Each Node release bundles one npm version, so a later Node patch bump that moves
  npm within 10.9 no longer needs a separate npm edit. The job still requires a plain
  major.minor.patch npm version, and Python, Node, Bun and Playwright stay pinned exactly.
- The Pretext E2E result contract now requires at least seven tests with none failed, errored or
  skipped, instead of exactly seven, so adding an e2e test no longer fails the gate.
- The single-column product surface detector matches its primary role the way the workspace
  detector does. A role now counts as primary when any of its words is `main` or `primary`,
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
- The React Tailwind recipe tables (`RECIPE_BY_KEY` and the rest) are read once, at import, into
  `DEFAULT_TAILWIND_RECIPE_REGISTRY`. Reassigning or editing them afterwards changes neither what
  the emitter writes nor what `viewspec check` accepts or `viewspec prove` reports.
  `viewspec check` and `check_artifact_dir()` verify React Tailwind artifacts against the default
  registry, so an artifact whose classes came from another registry fails them with
  `TAILWIND_INVENTORY_MISMATCH`.
- Generated React apps start from React and React DOM 19.3.1 (was 19.2.7), Tailwind CSS and
  `@tailwindcss/vite` 4.3.3 (was 4.3.0), `@vitejs/plugin-react` 6.1.2 (was 6.0.2), `@types/react`
  and `@types/react-dom` 19.3.0 (was 19.2.16 and 19.2.3), and `@types/node` 25.9.9 (was 25.5.0).
  The shipped template's `package.json` and lockfile change, so the hashes of every generated
  React app's package files change too. The other two React dependency seeds move to the same
  versions of the packages they declare.

### Removed

- The hosted SaaS client and everything built on it: `compile_remote`, `compile_auto`,
  `compile_app_remote`, `compile_artifact_remote`, the hosted verification client, signed usage
  receipts (`verify_usage_receipt`, `verify_signed_receipt`), `CompilerAPIError`, `DesignRequest`,
  `CompileResponse`, `ViewSpecBuilder.attach_design()`, and the `remote` extra. The `cryptography`
  and `httpx` dependencies are gone. SwiftUI and Flutter were hosted-only, so they are gone too.
- IntentPatch and Converge: `viewspec patch-targets`, `patch-preview`, `patch-apply`, and every
  `converge-*` command, with their Python APIs and published schemas. Regenerate a bundle or apply a
  standard JSON Patch, then run `viewspec check`.
- Review, private Share, and creation rooms: `viewspec review*`, `studio-create`, `studio-accept`,
  `studio-agent-task`, `studio-share-prepare`, `.vsreview` archives, and Studio's Comment and Approve
  loop, `--compare`, `--share`, and `--brief`.
- Every MCP tool except `init`, `compile`, `check`, `prove`, and `studio`. `init-agent`,
  `export-agent-assets`, and `check-agent-assets` are gone; the agent prompt and schemas stay
  published and importable.
- `viewspec doctor`, `lift`, `diff`, `diff-intent`, `diff-app`, `verify-host`, and `verify --repair-out`.
- 247 names from `viewspec.__all__`. Modules such as `viewspec.agent` and `viewspec.intent_tools`
  remain importable but are internal. `load_design_system()` no longer accepts `lint`.
- The `HOSTED_ONLY_FIELD` validation code: those fields are now rejected as `UNKNOWN_FIELD`.
- `demos/launch-assets/` and the Git LFS migration script that was to move them.
- The agent UI evaluation harness, the Studio production canary, the refinement scorecards, the
  human study protocol, and the internal audit and planning documents.

### Fixed

- `--force` refuses an output directory that holds the source or DESIGN.md. It used to delete them
  along with the old output.
- The landing page's section headings render at their designed 25px to 40px. A missing space around
  `+` in `calc()` made every `h2` inherit the 16px body size. A static check and a browser test now
  cover this. The `.sub` and `.lead` rules failed the same way; they are removed and stay at 16px.
- Non-ASCII text in list and dict bindings renders as UTF-8 instead of `\u` escapes.
- The declared protobuf requirement matches the generated schema: `protobuf>=6.31.1,<8`. The
  package did not import with protobuf 5.x.
- The local review daemon no longer answers an agent poll with `REVIEW_SERVER_START_FAILED` when a
  temporary file or candidate directory disappears while the session storage is being sized:
  the size walk skips entries that vanish between listing and inspection and still rejects
  symbolic links.
- The React Tailwind emitter no longer writes to another module's globals: it assigned seven
  attributes on `recipes.py` on every call. Emits with different recipe registries can run at the
  same time, and `tailwind_recipe_registry_digest()` no longer depends on names that earlier code
  reassigned. Compiled output and the default registry digest are unchanged.

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
  with a block cap (`CHECK_SCAN_LIMIT_EXCEEDED`) instead of quadratic.

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

[Unreleased]: https://github.com/nxrobins/viewspec/compare/v0.4.0b1...HEAD
[0.4.0b1]: https://github.com/nxrobins/viewspec/compare/v0.3.0b12...v0.4.0b1
[0.3.0b12]: https://github.com/nxrobins/viewspec/compare/v0.3.0b11...v0.3.0b12
[0.3.0b11]: https://github.com/nxrobins/viewspec/compare/v0.3.0b10...v0.3.0b11
[0.3.0b10]: https://github.com/nxrobins/viewspec/compare/v0.3.0b9...v0.3.0b10
[0.3.0b9]: https://github.com/nxrobins/viewspec/compare/v0.3.0b7...v0.3.0b9
[0.3.0b7]: https://github.com/nxrobins/viewspec/compare/v0.3.0b5...v0.3.0b7
[0.3.0b5]: https://github.com/nxrobins/viewspec/compare/v0.3.0b3...v0.3.0b5
[0.3.0b3]: https://github.com/nxrobins/viewspec/releases/tag/v0.3.0b3
