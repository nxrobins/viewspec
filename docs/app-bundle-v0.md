# AppBundle V1/V2/V3/V4

AppBundle is the bounded app-generation contract for local multi-screen internal tools. Its default Static Shell V0 target remains a proof artifact. The additive `react-tailwind-app` target emits a complete runnable Vite/React/Tailwind package with browser-history routes, host resource props, generated state reducers, selectors, conditional visibility, typed host callbacks, and exact-artifact browser verification. `schema_version: 1` keeps fixtures unbound; `schema_version: 2` adds proof-only read-only fixture binding; `schema_version: 3` adds bounded declarative state, mutations, selectors, replay assertions, and a generated pure TypeScript reducer artifact. `schema_version: 4` adds bounded visibility rules and is the golden-path runtime contract.

## Runnable React App Golden Path

```bash
viewspec init-app --template react-app --out viewspec.app.json
viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
```

Edit the AppBundle and regenerate with `--force`; do not edit generated React. To compile and prove the exact generated app in one workflow:

```bash
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install
```

The proof checks the same embedded screen provenance and resource assertions as the source path,
verifies generated reducer replay under Node, checks every generated file hash, runs strict
TypeScript checking before the Vite production build, and exercises routes, browser history,
unknown-route fallback, state actions, live resource-field rebinding, selector expectations, and
visibility in Chromium.

The generated `ViewSpecApp` accepts fixture-compatible resource records plus typed navigation, action, state-change, and error callbacks. Network requests, authentication, persistence, optimistic updates, and deployment infrastructure remain host-owned.

## Optional Freerange Numeric Proof

`react-tailwind-app` may add a fail-closed proof of its manifest-described generated numeric kernel:

```bash
viewspec doctor --freerange
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --freerange --json
```

This option is supported only for `react-tailwind-app`. The generated package and lockfile pin
`@chenglou/freerange` exactly to `0.0.1`; version, resolved package, integrity, binary metadata, and
the accompanying TypeScript toolchain are verified before analysis. Stable Bun 1.x or newer must
be installed explicitly on `PATH` for an applicable numeric scope. ViewSpec never installs Bun.
Because Freerange `0.0.1` exposes human-readable CLI output rather than a versioned JSON API,
ViewSpec uses a strict version-pinned transcript adapter and rejects protocol drift.
`doctor --freerange` is read-only: it may execute `bun --version` and reports the expected engine
identity, while the package itself remains unchecked until app proof. It does not mutate the app,
install dependencies, or invoke a network-capable package runner.

In the exact-app proof, `--install` is the only permission to run `npm ci --ignore-scripts` and
therefore the only dependency-preparation phase that may access the registry. Keep it on the
public `prove-app` command because that workflow verifies a freshly generated artifact. The
lower-level artifact verifier can use an existing artifact that already contains `node_modules`;
`--install` still does not install Bun. After dependency preparation, the proof order is strict
and fail-closed:

1. Validate and privately snapshot the exact generated artifact.
2. Run strict TypeScript checking with `npm run typecheck` (`tsc --noEmit`).
3. When requested, run Freerange findings and `--audit` over only the declared numeric kernel.
4. Run the Vite production build.
5. Run the generated Chromium verification and recheck immutable hashes.

An applicable Freerange scope contains at least one kernel file, required function, and recorded
runtime call site. It reports `static_analysis.status: "passed"` only when findings and audit agree,
module initialization is fully analyzed, the observed function set exactly matches the required
set, every required function is fully analyzed with zero partial, unsupported, or skipped work,
required guarantees are present, any reported assertions are proven, caller requirements are explicitly
allowlisted, no assumptions are accepted, and no error findings remain. A generated app with no
supported numeric operations reports `static_analysis.status: "not_applicable"`, zero coverage,
and `runtime.status: "not_required"`; it is not relabeled as a passed analysis.

The machine report records pinned engine/protocol and Bun identity, required functions, per-file
coverage and bounded contract evidence, findings, analyzed-source/call-site/configuration/tool
hashes, findings and audit transcript hashes, explicit phase statuses, timings, and errors. Missing
or unsupported Bun,
package/version/integrity drift, malformed or contradictory output, incomplete coverage, unsafe
contracts, unproven assertions, error findings, timeout/output bounds, or changed source, config,
runtime, or tool inputs fail with stable `APP_FREERANGE_*` codes and cannot produce `passed`.

Freerange does not analyze CSS or Tailwind, prove rendered geometry, or certify arbitrary host
applications. It proves only the supported generated numeric helpers under their recorded
contracts; ViewSpec separately binds that evidence to generated call-site hashes, strict
TypeScript, exact-artifact hashes, and the later bounded browser proof.

## Optional Pretext Native-DOM Text Proof

`react-tailwind-app` may also add a fail-closed proof for compiler-owned native text surfaces:

```bash
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --pretext --json
viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install --freerange --pretext --json
```

`--pretext` is opt-in and supported only for `react-tailwind-app`. For an applicable scope, the
generated runtime dependency and npm lock pin `@chenglou/pretext` exactly to `0.0.8`, resolved as
`https://registry.npmjs.org/@chenglou/pretext/-/pretext-0.0.8.tgz` with integrity
`sha512-yqm2GMxnPI7VHcHwe84P8ZF0JK/2d2DMKPqMN+s95jQhwDMYYXKVFVJUMEaVWckQStdsjdLav/0Vu+d9YbtGxA==`.
Before accepting evidence, ViewSpec also checks the installed package metadata and complete package
tree against SHA-256
`e5a45193af0a178be6f0c9138f704ba92bdb18eff1da39da99533c2c17b4f103`. `--install` remains the
explicit permission for `npm ci --ignore-scripts` and possible registry access; otherwise
`node_modules` must already exist. Pretext does not require Bun. A combined proof requires Bun only
when its Freerange scope is applicable.

The `viewspec_pretext_native_dom_v1` profile generates the named `Arial, sans-serif` font stack and
`overflow-wrap: anywhere` for compiler-owned IR nodes. Its proof probe waits for
`document.fonts.ready`, resolves only the exact manifest-derived element and IR identities, and
compares Pretext's predicted line count with native DOM `Range` line rectangles plus horizontal and
vertical overflow. It covers every eligible visible text surface at exactly these Chromium
viewports:

- mobile: 390×844
- tablet: 768×1024
- desktop: 1440×1000

The bounded profile accepts the named, loaded `Arial, sans-serif` stack rather than `system-ui` or
`-apple-system`, horizontal writing, normal/pre-wrap white space, normal/keep-all word breaking,
`overflow-wrap: anywhere`, numeric letter spacing, and a numeric line height or `normal`
interpreted as 1.2× font size. Unsupported computed typography is a proof failure. Pretext
`prepare()` results are cached by hashed text and supported typography inputs without width;
`layout()` reuses that prepared value at each width with a fixed 1px line-fit tolerance. Actual DOM
overflow remains disallowed. The cache counters are validated so claimed cross-width reuse cannot
be fabricated.

The native DOM remains the rendered and semantic authority. The probe reads and hashes text but
does not return raw text, write layout decisions into the page, replace elements, or render the UI
to a canvas. Pretext may use Canvas 2D internally for measurement; this proof does not certify a
canvas renderer or the separate demo canvas shim.

The composite phase order is strict:

1. Validate and privately snapshot the exact generated artifact, then prepare dependencies and
   validate the applicable Pretext installation.
2. Run strict TypeScript checking.
3. When requested, run Freerange over the declared numeric kernel.
4. Run the Vite production build.
5. Run the generated core Chromium verification.
6. Run the isolated manifest-scoped Pretext Chromium observations, then validate the bounded report
   and its full surface × viewport coverage.
7. Recheck Freerange inputs when present, the installed Pretext tree when applicable, and all final
   artifact hashes.

Machine output exposes the result at `text_layout` and `analyses.pretext`; a composed proof also
keeps `static_analysis` and `analyses.freerange`. Pretext evidence records `status`, pinned
`engine`, `profile`, `protocol`, `environment`, canonical `viewports`, bounded per-surface items,
`coverage` (`required`, `accounted`, `measured`, `hidden`, `unsupported`, `failed`), `cache`
(`prepare_calls`, `unique_inputs`, `layout_calls`, `cache_hits`), installation identity, scope,
observation and report digests, timings, phase statuses, and errors. `proof_identity` binds
`pretext_scope_digest`, `pretext_observation_digest`, and `pretext_report_hash` when present.

The scope is derived only from eligible compiler-owned nodes in checked screen manifests and must
account exactly once for their cross-product with the three viewports. Hidden surfaces are explicit;
unsupported or failed observations, missing or duplicate coverage, line-count or overflow
mismatches, cache contradictions, package/version/integrity drift, invalid protocols or reports,
and changed inputs fail with stable `APP_PRETEXT_*` codes. No eligible surface reports
`text_layout.status: "not_applicable"` with zero coverage; it is not a passed layout proof.

This proves only the reported native text wrapping in the recorded loaded-font Chromium
environment. It is not cross-browser or cross-operating-system evidence, a Retina/device-pixel-ratio
matrix, canvas-rendering proof, pixel-perfect visual equivalence, accessibility certification, or
arbitrary host-app certification.

```bash
viewspec init-app --out viewspec.app.json
viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json
viewspec validate-app viewspec.app.json --json
viewspec diff-app old.app.json new.app.json --json
viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json
viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --json
```

## Contract Shape

```json
{
  "schema_version": 1,
  "app": {
    "id": "incident_console",
    "title": "Incident Console",
    "kind": "internal_tool",
    "root_route": "/"
  },
  "routes": [
    { "id": "queue", "path": "/", "label": "Queue", "screen_id": "queue" },
    { "id": "detail", "path": "/incident", "label": "Incident", "screen_id": "detail" }
  ],
  "resources": [
    { "id": "incidents", "kind": "fixture", "records": [{ "id": "inc_1042" }] }
  ],
  "screens": [
    { "id": "queue", "title": "Incident Queue", "intent_bundle": {} },
    { "id": "detail", "title": "Incident Detail", "intent_bundle": {} }
  ]
}
```

`intent_bundle` values must be full local V1 IntentBundles. The schema example above is structural only; use `viewspec init-app` for a valid starter with embedded screen intents.

AppBundle V2 adds required root `resource_binding: "fixture_readonly_v0"` and per-screen `resource_views`:

```json
{
  "id": "queue_incidents",
  "resource_id": "incidents",
  "mode": "list",
  "record_ids": ["inc_1042"],
  "fields": ["id", "severity", "status"],
  "target_motif_id": "incidents"
}
```

V2 validates every resource view against existing fixture resources, unique record ids, existing scalar fields, and an existing target motif id on that screen.

AppBundle V3 adds required root `interactive_state: "interactive_state_v0"`, `state`, `mutations`, and `selectors`, with optional `state_replay_assertions`. Mutation triggers reference declared embedded screen actions by `{ "screen_id": "...", "action_id": "..." }`, and `from_payload` reads must reference that action's declared `payload_bindings`. Supported mutation ops are `set`, `patch`, `toggle`, `append`, `remove`, `move`, and `increment`. Supported selector ops are `filter_eq`, `sort_by`, and `slice`.

AppBundle V4 keeps the full V3 contract and adds an optional root `visibility` array. Each rule is `{ "id", "screen_id", "target_ref", "when" }`: `target_ref` is `region:<id>`, `motif:<id>`, or `binding:<id>` declared in that screen's embedded intent (`view:` is excluded — whole-screen visibility is the router's job), and `when` is exactly one closed condition form: `{"state": id, "is": "truthy"|"falsy"}` (scalar or selection state), `{"state": id, "equals": <JSON scalar>}` (scalar state, JS strict-equality semantics), or `{"selector": id, "is": "non_empty"|"empty"}` (selectors sourcing collection or selection state). At most one rule per `(screen_id, target_ref)`. Replay assertions may declare `expect_visibility` maps of rule id to boolean; replay and Node reducer conformance both verify the verdicts, and compiled screens bake initial visibility that proof cross-checks against `initial_visibility` (`APP_VISIBILITY_BAKE_MISMATCH` fails closed).

## Constraints & Fallbacks

AppBundle V0 is physically bounded: max 1 MiB raw JSON, 16 screens, 32 routes, 8 fixture resources, 100 records per resource, 32 scalar fields per record, 2,048 characters per scalar string, 256 KiB per embedded IntentBundle, 1 MiB aggregate embedded IntentBundle JSON, 256 KiB app proof report, and 16 KiB redacted support bundle. V3 state adds max 32 state entries, 128 mutations, 16 ops per mutation, 64 selectors, 8 selector ops, 32 replay assertions, 32 events per replay assertion, 64 KiB generated reducer, and 64 KiB state manifest. V4 visibility adds max 64 rules, one rule per screen target, and equals values bounded to JSON scalars of at most 2,048 characters. Validation fails closed with stable error codes before writing proof artifacts when any bound is exceeded.

Routes are static and canonical only: paths are unique, at most 96 characters, start with `/`, contain only letters, digits, `_`, `.`, `~`, `-`, and `/`, and must not contain `//`, `/../`, `/./`, `%`, `?`, `#`, or `\`. Proof output paths derive only from validated safe ids, never route paths, labels, titles, resource values, or user copy.

AppBundle V0 is local-only and no-network. AppBundle-owned fields reject URL schemes, environment references, credentials, adapter config, fetch config, package-install flags, hosted compiler behavior, and unknown fields. V1/V2 reject mutation fields; V3 admits only the bounded `interactive_state_v0` profile and no arbitrary code, expressions beyond `from_payload`, network calls, time, randomness, package installs, or host-framework APIs.

Embedded screen intents pass the existing local V1 IntentBundle validator with compile check enabled by default. Fixture resources are recorded and bounded for app context but remain `resource_binding: "unbound_v0"` and are not required to match, feed, deduplicate, or prove data consistency with embedded screen intents.

Resource Binding V0 is physically bounded and proof-only: max 32 resource views per app, 8 per screen, 50 record refs per view, 16 fields per view, 800 record-field assertions per app, and 128 KiB serialized assertion report. Assertions use only compiler semantic inventory for the declared `target_motif_id`, exact byte-for-byte scalar matching after JSON string decoding only, zero full-HTML scans, zero hidden/comment/attribute sources, zero transforms, and zero query features.

If `resource_binding: "fixture_readonly_v0"` is declared, validation, compile, and proof fail closed with stable `APP_RESOURCE_BINDING_*` errors on schema mismatch, empty assertion set, unsupported source, ambiguous repeated value, limit overflow, report overflow, digest mismatch, or missing `binding_scope: "declared_resource_views_only"`. Commands never downgrade to `unbound_v0`, skip assertions, imply runtime/live/adapter/state/data-flow execution, or return a partial successful proof.

`diff-app` reports app metadata, route, screen, resource, resource-view, V3 state, mutation, selector, replay assertion, and per-screen embedded intent semantic summaries. If a changed embedded screen intent cannot be validated or diffed, `diff-app` fails with `APP_DIFF_SCREEN_INTENT_INVALID`.

Static Shell V0 is physically bounded: max 16 screens, 32 routes, 2 MiB shell HTML, 64 KiB shell JS, 64 KiB serialized route table, 8 MiB aggregate embedded checked screen HTML, 64 KiB generated reducer, 64 KiB state manifest, 0 external network surfaces, 0 dynamic route features, and 0 third-party executable/embed surfaces. For V4, the shell additionally embeds the generated reducer (as a bounded inline runtime, max 96 KiB combined) plus exactly one delegated click listener that dispatches declared mutations for `(screen, action)` triggers and toggles `hidden` + `data-visibility-state` on `data-visibility-rule` nodes — bounded visibility binding only; data and text rebinding stay out of scope, and a halted mutation sequence marks the screen section with `data-viewspec-state-halted` instead of failing silently. The shell manifest records the runtime under `state_runtime` (hashes, sizes, `listener_count: 1`). Shell generation fails closed before writing a successful report with stable `APP_SHELL_*` or `APP_STATE_*` error codes if any bound is exceeded, any route/screen/state assertion fails, any output path escapes the shell/proof root, any preexisting output exists without `--force`, any screen validation/compile/check/hash fails, or `compile-app` and `prove-app --with-shell` would produce non-identical shell artifacts.

`compile-app` writes `app-dist/index.html`, `shell_manifest.json`, `diagnostics.json`, and checked screen artifacts using `target: "html-tailwind-app"` and `route_navigation: "static_shell_v0"`. For V3 it also writes `state_reducer.ts` and schema-versioned `state_manifest.json`; the state manifest records the normalized state contract, `state_contract_hash`, state event schemas, replay report, reducer exports, reducer hash, and local reducer conformance report. It rejects external network/embed/script surfaces, renders unknown route state as one local 404 panel with zero selected screen containers, and remains a local proof artifact rather than a deployable framework app.

## Proof Output

`prove-app` writes:

- `.viewspec-app-proof/APP_PROOF.md`
- `.viewspec-app-proof/app_proof_report.json`
- `.viewspec-app-proof/app_support_bundle.json`
- `.viewspec-app-proof/screens/<screen_id>/viewspec.intent.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/index.html`
- `.viewspec-app-proof/screens/<screen_id>/artifact/provenance_manifest.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/diagnostics.json`

The proof report keeps report schema metadata separate from app contract metadata: `schema_version` describes the report format, while `app_schema_version` records the validated AppBundle contract version. It uses `proof_level: "app_contract_source_artifacts"`, `target: "html-tailwind"`, `policy.network_calls: "none"`, route assertions, screen hashes, manifest summaries, check status, and the validated resource binding mode. V2 reports include `resource_binding: "fixture_readonly_v0"`, `binding_scope: "declared_resource_views_only"`, concrete assertion counts, per-view status, and a binding digest.

With `--with-shell`, `prove-app` also writes `.viewspec-app-proof/app-shell/index.html`, `.viewspec-app-proof/app-shell/shell_manifest.json`, and `.viewspec-app-proof/app-shell/diagnostics.json`; the proof report uses `target: "html-tailwind-app"`, `route_navigation: "static_shell_v0"`, `shell_artifact_hash`, `shell_manifest_hash`, shell route assertions, and the same per-screen proof data.

For V3/V4 shell proofs, `compile-app` and `prove-app --with-shell` also write matching `state_reducer.ts` and `state_manifest.json`, record `state_contract_hash`, `state_reducer_hash`, `state_manifest_hash`, replay status, and reducer conformance status. Replay assertions execute through the normalized mutation definitions in the Python interpreter, and the generated reducer is imported with local Node and compared against the Python replay before returning a successful report. **Node.js (>=18) on `PATH` is a prerequisite for V3/V4 conformance** — without it, `compile-app` and `prove-app` fail with `APP_STATE_REDUCER_NODE_UNAVAILABLE` (install Node, or use a V1/V2 AppBundle, which needs no Node).

## Explicit Anti-Goals

- AppBundle V0 is not required to prove browser runtime navigation, back/forward behavior, deep linking, focus restoration, scroll restoration, or route transition animation.
- AppBundle V0 is not required to support dynamic routes, route params, query strings, hash fragments, encoded route aliases, redirects, route guards, nested routers, or locale-aware routing.
- AppBundle V0 is not required to detect every semantic mismatch between fake fixture resources and duplicated data inside embedded screen IntentBundles.
- AppBundle V0 is not required to bind fixture resources into screen rendering, infer screen data dependencies, deduplicate repeated data across screens, or prove data-flow consistency.
- The `react-tailwind-app` target emits a runnable Vite/React/Tailwind frontend and bounded host adapter; it does not generate Next.js, arbitrary routers, API clients, backends, database schemas, authentication, persistence, or optimistic server mutation handlers.
- AppBundle V0 is not required to optimize for very large apps, streaming validation, incremental proof, cross-bundle imports, shared screen libraries, or monorepo-scale app composition.
- AppBundle V0 is not required to certify accessibility, pixel-perfect visual equivalence, cross-browser behavior, production deployment readiness, arbitrary host-app compatibility, or hosted extended compiler behavior.
- Static Shell V0 does not support browser back/forward history semantics, live data or text rebinding, or deployable framework generation. Those bounded frontend behaviors belong to `react-tailwind-app`; scroll/focus restoration, transitions, multi-tab synchronization, service workers, persisted state, nested or dynamic routing, redirects, route guards, query strings, locale routing, lazy loading, and bundle splitting remain out of scope for both local targets.
- Resource Binding V0 is not required to prove formatted, localized, case-normalized, concatenated, abbreviated, rounded, pluralized, date-formatted, currency-formatted, or otherwise transformed fixture values.
- Resource Binding V0 is not required to infer that display labels, aliases, derived columns, badges, icons, colors, severity ordering, or human-readable summaries correspond to fixture fields.
- Resource Binding V0 is not required to support nested record fields, arrays, objects, joins, cross-resource references, computed fields, filters, sorting, pagination, grouping, aggregation, or query languages.
- Static Shell Resource Binding V0 remains proof-only. The React target projects declared scalar resource-view fields from host props or generated state into checked bindings, but does not infer undeclared data flow, persist state, or synchronize with a server.
- Resource Binding V0 is not required to resolve semantic intent when multiple fixture fields intentionally contain the same scalar value unless the declared record-field assertion boundary is unambiguous.
- State IR V0 is not required to generate Zustand, Redux, SwiftData, CRDT, websocket, optimistic reconciliation, persistence, auth, backend/API client, package install, or gesture/pointer runtimes.
- State IR V0 is not required to relax local V1/V2/V3 caps, stream massive dynamic apps, discover plugins, execute untrusted code, or cross the hosted compiler API boundary.
- Visibility V0 is not required to support boolean condition composition (and/or/not), animation or transitions, focus management on show/hide, `view:` targets, or any data/text rebinding; it is bounded conditional show/hide only, and conditions over states driven to unexpected JSON shapes stay total (never crash) under Python-equals-JavaScript semantics verified by Node conformance.
