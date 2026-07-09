# AppBundle V1/V2/V3/V4

AppBundle is the first narrow app-generation contract for local multi-screen internal tools. It does not emit a deployable app. It records app metadata, static routes, fixture resources, and embedded local V1 screen `IntentBundle`s, then proves each screen by compiling/checking through the existing `html-tailwind` path. `schema_version: 1` keeps fixtures unbound; `schema_version: 2` adds proof-only read-only fixture binding; `schema_version: 3` adds bounded declarative state, mutations, selectors, replay assertions, and a generated pure TypeScript reducer artifact. `schema_version: 4` adds optional bounded `visibility` rules: per-screen show/hide conditions over the declared state/selector vocabulary, at most 64 rules and one rule per `(screen_id, target_ref)`, where `target_ref` is a `region:`, `motif:`, or `binding:` id declared in that screen's embedded intent and `when` is exactly one of `{"state", "is": "truthy"|"falsy"}`, `{"state", "equals": <JSON scalar>}`, or `{"selector", "is": "non_empty"|"empty"}`. Compiled screens bake initial visibility (`data-visibility-rule` + `data-visibility-state` markers, plus `hidden` when the initial verdict is false, cross-checked against `initial_visibility` at proof time), replay assertions may add `expect_visibility`, and the generated reducer additionally exports `evaluateViewSpecVisibility(state)` with Node conformance comparing Python and JavaScript verdicts.

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
- AppBundle V0 is not required to generate a deployable React/Vite/Next app, runtime router, resource adapter, framework state adapter, API client, backend, database schema, or mutation handler.
- AppBundle V0 is not required to optimize for very large apps, streaming validation, incremental proof, cross-bundle imports, shared screen libraries, or monorepo-scale app composition.
- AppBundle V0 is not required to certify accessibility, pixel-perfect visual equivalence, cross-browser behavior, production deployment readiness, arbitrary host-app compatibility, or hosted extended compiler behavior.
- Static Shell V0 is not required to support browser back/forward history semantics, scroll restoration, focus restoration, route transition animation, multi-tab synchronization, CSP policy generation, service workers, web workers, import maps, production deployment hardening, fixture binding, live data or text rebinding, framework state adapters, persisted local state, nested routers, redirects, route guards, dynamic segments, route params, query strings, AppBundle hash fragments, locale routing, encoded aliases, canonical URL rewriting, lazy loading, bundle splitting, or deployable React/Vite/Next generation.
- Resource Binding V0 is not required to prove formatted, localized, case-normalized, concatenated, abbreviated, rounded, pluralized, date-formatted, currency-formatted, or otherwise transformed fixture values.
- Resource Binding V0 is not required to infer that display labels, aliases, derived columns, badges, icons, colors, severity ordering, or human-readable summaries correspond to fixture fields.
- Resource Binding V0 is not required to support nested record fields, arrays, objects, joins, cross-resource references, computed fields, filters, sorting, pagination, grouping, aggregation, or query languages.
- Resource Binding V0 is not required to bind fixture resources at runtime, update the shell after load, persist state, handle mutations, synchronize data across routes, or prove whole-app data-flow consistency beyond explicitly declared `resource_views`.
- Resource Binding V0 is not required to resolve semantic intent when multiple fixture fields intentionally contain the same scalar value unless the declared record-field assertion boundary is unambiguous.
- State IR V0 is not required to generate Zustand, Redux, SwiftData, CRDT, websocket, optimistic reconciliation, persistence, auth, backend/API client, package install, or gesture/pointer runtimes.
- State IR V0 is not required to relax local V1/V2/V3 caps, stream massive dynamic apps, discover plugins, execute untrusted code, or cross the hosted compiler API boundary.
- Visibility V0 is not required to support boolean condition composition (and/or/not), animation or transitions, focus management on show/hide, `view:` targets, or any data/text rebinding; it is bounded conditional show/hide only, and conditions over states driven to unexpected JSON shapes stay total (never crash) under Python-equals-JavaScript semantics verified by Node conformance.
