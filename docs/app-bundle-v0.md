# AppBundle V1/V2/V3/V4/V5

> **Naming note.** This file keeps the name `app-bundle-v0.md` for link stability, but it documents
> AppBundle `schema_version` 1 through 5 (V1 to V5). "V0" survives only in the Static Shell V0 proof
> target and in the `unbound_v0`, `fixture_readonly_v0`, `interactive_state_v0`, and `visibility_v0`
> contract names used below.

AppBundle is the bounded app-generation contract for local multi-screen internal tools. Its default Static Shell V0 target remains a proof artifact. The additive `react-tailwind-app` target emits a complete runnable Vite/React/Tailwind package with browser-history routes, host resource props, generated state reducers, selectors, conditional visibility, scalar state text, typed host callbacks, and exact-artifact browser verification. `schema_version: 1` keeps fixtures unbound; `schema_version: 2` adds proof-only read-only fixture binding; `schema_version: 3` adds bounded declarative state, mutations, selectors, replay assertions, and a generated pure TypeScript reducer artifact. `schema_version: 4` adds bounded visibility and optional scalar-to-text rules and is the golden-path runtime contract. `schema_version: 5` adds bounded expressions: composed visibility conditions, multi-value state text, and closed formatters.

## Runnable React App Golden Path

```bash
viewspec init app --template react-app --out viewspec.app.json
viewspec compile viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
```

Edit the AppBundle and regenerate with `--force`; do not edit generated React. To compile and prove the exact generated app in one workflow:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install
```

The proof checks the same embedded screen provenance and resource assertions as the source path,
verifies generated reducer replay under Node, checks every generated file hash, runs strict
TypeScript checking before the Vite production build, and exercises routes, browser history,
unknown-route fallback, state actions, live resource-field rebinding, selector expectations,
visibility, and declared state text in Chromium.

The generated `ViewSpecApp` accepts fixture-compatible resource records plus typed navigation, action, state-change, and error callbacks. Network requests, authentication, persistence, optimistic updates, and deployment infrastructure remain host-owned.

## Optional Freerange Numeric Proof

`react-tailwind-app` may add a fail-closed proof of its manifest-described generated numeric kernel:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange --json
```

This option is supported only for `react-tailwind-app`. The generated package and lockfile pin
`@chenglou/freerange` exactly to `0.0.1`; version, resolved package, integrity, binary metadata, and
the accompanying TypeScript toolchain are verified before analysis. Stable Bun 1.x or newer must
be installed explicitly on `PATH` for an applicable numeric scope. ViewSpec never installs Bun.
Because Freerange `0.0.1` exposes human-readable CLI output rather than a versioned JSON API,
ViewSpec uses a strict version-pinned transcript adapter and rejects protocol drift.

In the exact-app proof, `--install` is the only permission to run `npm ci --ignore-scripts` and
therefore the only dependency-preparation phase that may access the registry. Keep it on the
public `viewspec prove` command because that workflow verifies a freshly generated artifact. The
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

The `viewspec_numeric_kernel_v2` contract enumerates every eligible mutation or selector operation
occurrence from the independently emitted normalized state contract. The state-contract file hash,
contract hash, ordered operation inventory, inventory digest, operation count, required-function
count, generated kernel, and call site must all agree. Machine evidence exposes
`operation_coverage.required`, `operation_coverage.accounted`, and the inventory digest, so an
analysis manifest cannot silently discard a repeated operation while retaining the same helper.

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
viewspec prove viewspec.app.json --target react-tailwind-app --install --pretext --json
viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange --pretext --json
```

The Pretext profile requires exact line-count parity for ordinary UI text. For text already wrapping to at least ten lines, it permits at most one line of Canvas-to-DOM shaping drift while retaining both counts in evidence. Horizontal overflow and clipped overflow fail; visible vertical glyph ink is retained as telemetry without being mistaken for clipping.

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

The `viewspec_pretext_native_dom_v2` profile generates the named `Arial, sans-serif` font stack and
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

Each scoped screen records its source-manifest hash plus an eligible-surface count and digest. At
runtime the browser independently enumerates the emitted `data-ir-primitive` DOM inventory for
every route and viewport; those matrix inventories must exactly match the manifest-derived values
before any text-layout report can pass. Evidence includes `eligible_surface_coverage` and the
per-matrix inventories alongside the per-surface observations.

This proves only the reported native text wrapping in the recorded loaded-font Chromium
environment. It is not cross-browser or cross-operating-system evidence, a Retina/device-pixel-ratio
matrix, canvas-rendering proof, pixel-perfect visual equivalence, accessibility certification, or
arbitrary host-app certification.

```bash
viewspec init app --out viewspec.app.json
viewspec init app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json
viewspec check viewspec.app.json --json
viewspec compile viewspec.app.json --out app-dist --target html-tailwind-app --json
viewspec prove viewspec.app.json --out .viewspec-app-proof --with-shell --json
viewspec prove viewspec.app.json --out .viewspec-app-proof --json
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

`intent_bundle` values must be full local V1 IntentBundles. The schema example above is structural only; use `viewspec init app` for a valid starter with embedded screen intents.

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

V4 may also add optional root `state_text` rules:

```json
{
  "id": "review_count_text",
  "screen_id": "dispatch",
  "target_ref": "binding:review_count_label",
  "state": "reviewed_count",
  "template": "Review count: {value}"
}
```

The target must be one `exactly_once` binding presented as `badge`, `label`, `text`, or `value` on
that screen. The source must be scalar app state or scalar state scoped to the same screen. The
template is a literal string with exactly one `{value}` placeholder and no other braces. Initial
text is derived and cross-checked during screen compilation; the generated reducer exports
`evaluateViewSpecText(state)`; the static shell updates `textContent`; React consumes the same
derived map. Replay assertions may add `expect_text` maps, and the generated Chromium test verifies
each final string at its stable `data-state-text-id`. The value is inserted literally, so `$&`
stays `$&`. A number renders as JavaScript `String()` renders it, in the bake, the replay, and the
runtime alike: `1e-05` renders as `0.00001`, `1e21` as `1e+21`, and an integer beyond 2^53 as the
double JavaScript holds. The V4 contract deliberately excludes HTML,
expressions, paths, localization, formatters, and heuristic source discovery. V5 adds only named
placeholders and the closed formatter set described in [AppBundle V5](#appbundle-v5).

### Shared presentation and responsive anchors

Every screen may declare a target-neutral `presentation` contract. `profile` is one of `neutral`,
`operations_workspace`, or `editorial_dashboard`; `rules` target stable `region:`, `motif:`, or
`binding:` identities; and `anchors` describe relationships that must survive later content and
state changes. A rule's `base` applies at every width and its `variants` may override properties at
`compact`, `medium`, and `wide`. Supported properties include display, direction, columns, named
areas, child area assignment, ordering, spans, spacing, sizing, visibility, alignment, text
wrapping, and bounded typography/surface tokens. A motif rule may also declare `items.base` and
`items.variants` to lay out each stable direct record or semantic group inside the motif.
The `identity_sm`, `identity_md`, and `identity_lg` column tokens provide stable record-key tracks
for responsive resource rows without coupling either generated target to pixel-authored CSS.

Compilation resolves this into one deterministic `presentation_plan.json`. Static and React
consume identical plan bytes and report the same plan hash; neither emitter independently chooses
an AppBundle layout recipe. A missing declaration uses a deterministic inferred plan but emits
`APP_PRESENTATION_INFERRED`, so reference-sensitive final work cannot silently depend on fallback.
Declared anchors use `inside`, `before`, `after`, `aligned_start`, `aligned_center`, or `same_row`.
Validation resolves the effective layout at every claimed viewport, so a `same_row` anchor fails
early when its semantic branches stack there. An `operations_workspace` with sibling sidebar and
main regions must keep its rail visible and place rail/content on one wide row; compact and medium
may stack. Use a `hero` motif for a screen's primary eyebrow/title when native `header`/`h1`
semantics are required.
Generated React proof checks each applicable anchor at 390×844, 768×1024, and 1440×1000 and reports
the first screen, anchor, target, relation, property, expected value, and actual value through
`APP_PRESENTATION_ANCHOR_DIVERGED`.

### Resource repeats and identity proof

A list `resource_view` may add a `repeat` declaration instead of hand-authoring one semantic node
and binding set per record:

```json
{
  "id": "queue_incidents",
  "resource_id": "incidents",
  "mode": "list",
  "record_ids": ["inc_1042", "inc_1043"],
  "fields": ["id", "severity", "status"],
  "target_motif_id": "incidents",
  "repeat": {
    "group_id": "incident_rows",
    "field_presentations": [
      {"field": "id", "present_as": "label"},
      {"field": "severity", "present_as": "value"},
      {"field": "status", "present_as": "badge"}
    ]
  }
}
```

`field_presentations` must cover the view's fields exactly once. ViewSpec materializes stable
per-record nodes and bindings before intent validation and exposes `data-resource-id`,
`data-resource-view-id`, `data-record-id`, and `data-resource-field` in both targets. Proof matches
the canonical resource → record → field → binding identity, so two records may legally share the
same visible scalar. Authored/generated ID or DOM collisions fail with the conflicting canonical
identity and generated binding details. A repeat owns its record-field bindings inside the target
motif: keeping a hand-authored prototype field there fails with
`APP_RESOURCE_REPEAT_AUTHORED_DUPLICATE` instead of rendering the first record twice.

### Compact action replay

Replay events may name a mutation directly or use `{ "screen_id", "action_id", "repeat" }` to
model real user actions compactly. Action events resolve to exactly one declared mutation and
normalize into the same deterministic state IR; `repeat` expands into bounded identical events.
Replay mismatches carry the assertion ID, event index, mutation ID, state/selector/visibility path,
expected value, and actual value. This makes reveal, increment, derived visibility, and independent
transition failures actionable before generated reducer code is accepted.

## AppBundle V5

`schema_version: 5` adds bounded expressions to the V4 contract. It accepts every V4 construct
unchanged and adds composed visibility conditions, multi-value state text, and a closed formatter
set. Use it only when a screen needs one of those three. V4 stays the golden-path contract, and
`viewspec init app` and every starter keep their schema version. A bundle that declares
`schema_version` 1 to 4 and uses a V5 construct fails with
`APP_V5_FEATURE_REQUIRES_SCHEMA_VERSION_5`; the fix is to set `schema_version` to 5.

### Composed visibility

A V5 `when` is a V4 leaf condition, `{"all": [when, ...]}`, `{"any": [when, ...]}`, or
`{"not": when}`:

```json
{
  "id": "escalation",
  "screen_id": "queue",
  "target_ref": "binding:escalate",
  "when": {
    "all": [
      {"selector": "active", "is": "non_empty"},
      {"not": {"state": "acked", "is": "truthy"}}
    ]
  }
}
```

Each `all` and `any` list holds 2 to 8 conditions. A leaf directly in `when` sits at depth 1, and
nesting stops at depth 3. A rule holds at most 8 leaves. The 64-rule cap and the one rule per
`(screen_id, target_ref)` pair stay as in V4. Python replay and the generated
`evaluateViewSpecVisibility` evaluate the same tree, and both stay total for any state shape.
Broken trees fail with `APP_VISIBILITY_CONDITION_INVALID`, `APP_VISIBILITY_CONDITION_ARITY_INVALID`,
`APP_VISIBILITY_CONDITION_TOO_DEEP`, or `APP_VISIBILITY_CONDITION_TOO_MANY_LEAVES`, and each leaf
keeps its V4 checks.

### Multi-value state text

A V5 `state_text` rule may name several placeholders and fill each one from `values`:

```json
{
  "id": "open_summary",
  "screen_id": "queue",
  "target_ref": "binding:open_text",
  "template": "{open} open of {total}, {spend} spent",
  "values": {
    "open": {"selector": "active", "measure": "count"},
    "total": {"selector": "all_incidents", "measure": "count"},
    "spend": {"state": "spend", "format": {"format": "currency", "currency": "USD"}}
  }
}
```

The template is a literal of at most 512 characters with 1 to 8 placeholders and no other braces.
Each placeholder is `{name}`, its name matches `^[a-z][a-z0-9_]{0,31}$`, and it appears once.
Every placeholder has exactly one `values` entry, and every entry is used. An entry is either
`{"state": id, "format"?: F}`, which reads scalar app state or scalar state of the rule's screen,
or `{"selector": id, "measure": "count", "format"?: F}`, which reads the length of a selector
result over collection or selection state. The V4 form `{id, screen_id, target_ref, state,
template}` with one `{value}` stays valid in V5 and may add an optional `format`. Broken rules fail
with `APP_STATE_TEXT_VALUES_INVALID`, `APP_STATE_TEXT_VALUES_MISMATCH`,
`APP_STATE_TEXT_SELECTOR_MISSING`, `APP_STATE_TEXT_SELECTOR_SOURCE_UNSUPPORTED`,
`APP_STATE_TEXT_FORMAT_INVALID`, or the V4 state text codes. Replay `expect_text`, Node reducer
conformance, the baked screens, the static shell, React, and the generated Playwright proof all
cover the new form. Text renders literally: a template never becomes markup.

### Closed formatters

| Format | Shape | Example |
| --- | --- | --- |
| integer | `{"format": "integer"}` | `1234.5` renders `1,235` |
| decimal | `{"format": "decimal", "digits": n}`, n from 0 to 6 | `2.675` with 2 digits renders `2.68` |
| percent | `{"format": "percent", "digits": n}`, n from 0 to 6 | `0.1875` with 1 digit renders `18.8%` |
| currency | `{"format": "currency", "currency": CODE}` | `-1234.5` in USD renders `-$1,234.50` |
| date_iso | `{"format": "date_iso"}` | `1700000000000` renders `2023-11-14` |

The currency table is closed: USD `$`, EUR `€`, GBP `£`, and INR `₹` print 2 minor digits, JPY `¥`
prints 0, and CHF prints `CHF ` (with a space) and 2. Python and the generated TypeScript follow
one rule set:

- Numeric formats accept only finite JSON numbers. Booleans, numeric strings, null, and every other
  value render exactly as V4 renders that scalar, so text never throws.
- A number is read from its shortest round-trip text (Python `repr(float(x))`, JavaScript
  `String(x)`). Both exponent forms normalize into digits and a decimal point position.
- Rounding works half away from zero in that digit space. No binary floating point rounding takes
  place, so `1.005` with 2 digits renders `1.01`.
- `percent` shifts the decimal point two places, formats like `decimal`, and appends `%`.
- Grouping puts a comma every three integer digits, and the decimal separator is a period.
  `decimal` always prints exactly n fraction digits.
- A result that rounds to zero prints no minus sign.
- `currency` prints the sign, then the symbol, then the grouped amount, as in `-$1,234.50` and
  `¥1,235`.
- `date_iso` prints the date of a string that starts with a valid `YYYY-MM-DD` calendar date, with
  leap years checked. It also converts integer epoch milliseconds in UTC, from 0000-01-01 through
  9999-12-31, with a pure civil-from-days algorithm. Every other value falls back.
- No formatter calls Intl, a locale, a time zone, or a local-time Date API.

`conformance/formatters/vectors.json` is the specification. It holds over 120 cases: rounding ties,
exponent-form inputs, negative zero, results that round to zero, every currency, leap days, invalid
dates, booleans, strings, and null. A Python test and a Node test assert every vector.

In a V5 bundle, a number without a format renders as JavaScript `String()` renders it, in Python
too. V5 also rejects an integer above 2^53 in magnitude anywhere in `resources`, `state`,
`mutations`, `selectors`, `visibility`, or `state_replay_assertions` with
`APP_STATE_INTEGER_OUT_OF_SAFE_RANGE`, because the generated reducer reads such a number as the
nearest 64-bit double. Write larger identifiers as strings.

### Generated code and caps

The generated reducer carries formatter code only for a V5 bundle that uses a format. V3 and V4
reducers, shells, and React apps stay byte-identical. The 64 KiB reducer cap and the 96 KiB static
shell runtime cap apply unchanged. V5 escapes `<`, `>`, and `&` in the reducer's JSON literals, so a
template cannot end the static shell's inline script. For the incident console fixture
(`tests/fixtures/app_bundle_v5_incident_console.json`), the TypeScript reducer uses about 20 KiB of
its 64 KiB cap, and the static shell state runtime uses about 20 KiB of its 96 KiB cap.

### What V5 does not do

- It adds no free expressions, arithmetic, string functions, or comparisons beyond the V4 leaf
  forms.
- A placeholder reads one declared state value or one selector count; there are no JSON paths.
- It calls no host functions and has no Intl, locale, time zone, relative date, or custom number
  pattern behavior.
- It does not pluralize, localize, convert currencies, or render HTML.
- It adds no animation, transitions, or focus management on show and hide.

## Constraints & Fallbacks

AppBundle V0 is physically bounded: max 1 MiB raw JSON, 16 screens, 32 routes, 8 fixture resources, 100 records per resource, 32 scalar fields per record, 2,048 characters per scalar string, 256 KiB per embedded IntentBundle, 1 MiB aggregate embedded IntentBundle JSON, 256 KiB app proof report, and 16 KiB redacted support bundle. V3 state adds max 32 state entries, 128 mutations, 16 ops per mutation, 64 selectors, 8 selector ops, 32 replay assertions, 32 events per replay assertion, 64 KiB generated reducer, and 64 KiB state manifest. V4 visibility adds max 64 rules, one rule per screen target, and equals values bounded to JSON scalars of at most 2,048 characters. V5 adds 2 to 8 conditions per `all` or `any` list, depth 3, 8 leaves per rule, 512-character multi-value templates, and 8 placeholders per rule. Validation fails closed with stable error codes before writing proof artifacts when any bound is exceeded.

Routes are static and canonical only: paths are unique, at most 96 characters, start with `/`, contain only letters, digits, `_`, `.`, `~`, `-`, and `/`, and must not contain `//`, `/../`, `/./`, `%`, `?`, `#`, or `\`. Proof output paths derive only from validated safe ids, never route paths, labels, titles, resource values, or user copy.

AppBundle V0 is local-only and no-network. AppBundle-owned fields reject URL schemes, environment references, credentials, adapter config, fetch config, package-install flags, and unknown fields. V1/V2 reject mutation fields; V3 admits only the bounded `interactive_state_v0` profile and no arbitrary code, expressions beyond `from_payload`, network calls, time, randomness, package installs, or host-framework APIs.

Embedded screen intents pass the existing local V1 IntentBundle validator with compile check enabled by default. Fixture resources are recorded and bounded for app context but remain `resource_binding: "unbound_v0"` and are not required to match, feed, deduplicate, or prove data consistency with embedded screen intents.

Resource Binding V0 is physically bounded and proof-only: max 32 resource views per app, 8 per screen, 50 record refs per view, 16 fields per view, 800 record-field assertions per app, and 128 KiB serialized assertion report. Assertions use only compiler semantic inventory for the declared `target_motif_id`, canonical resource/record/field/binding identity, exact byte-for-byte scalar matching after JSON string decoding only, zero full-HTML scans, zero hidden/comment/attribute sources, zero transforms, and zero query features. Repeated scalar values are legal when their canonical identities differ.

If `resource_binding: "fixture_readonly_v0"` is declared, validation, compile, and proof fail closed with stable `APP_RESOURCE_BINDING_*` or `APP_RESOURCE_REPEAT_*` errors on schema mismatch, empty assertion set, unsupported source, canonical identity or generated DOM collision, incomplete repeat field coverage, limit overflow, report overflow, digest mismatch, or missing `binding_scope: "declared_resource_views_only"`. Commands never downgrade to `unbound_v0`, skip assertions, imply runtime/live/adapter/state/data-flow execution, or return a partial successful proof.

Static Shell V0 is physically bounded: max 16 screens, 32 routes, 2 MiB shell HTML, 64 KiB shell JS, 64 KiB serialized route table, 8 MiB aggregate embedded checked screen HTML, 64 KiB generated reducer, 64 KiB state manifest, 0 external network surfaces, 0 dynamic route features, and 0 third-party executable/embed surfaces. For V4, the shell additionally embeds the generated reducer (as a bounded inline runtime, max 96 KiB combined) plus exactly one delegated click listener that dispatches declared mutations for `(screen, action)` triggers and toggles `hidden` + `data-visibility-state` on `data-visibility-rule` nodes — bounded visibility binding only; data and text rebinding stay out of scope, and a halted mutation sequence marks the screen section with `data-viewspec-state-halted` instead of failing silently. The shell manifest records the runtime under `state_runtime` (hashes, sizes, `listener_count: 1`). Shell generation fails closed before writing a successful report with stable `APP_SHELL_*` or `APP_STATE_*` error codes if any bound is exceeded, any route/screen/state assertion fails, any output path escapes the shell/proof root, any preexisting output exists without `--force`, any screen validation/compile/check/hash fails, or `viewspec compile` and `viewspec prove --with-shell` would produce non-identical shell artifacts.

`viewspec compile` writes `app-dist/index.html`, `shell_manifest.json`, `diagnostics.json`, `presentation_plan.json`, and checked screen artifacts using `target: "html-tailwind-app"` and `route_navigation: "static_shell_v0"`. For V3 it also writes `state_reducer.ts` and schema-versioned `state_manifest.json`; the state manifest records the normalized state contract, `state_contract_hash`, state event schemas, replay report, reducer exports, reducer hash, and local reducer conformance report. It rejects external network/embed/script surfaces, renders unknown route state as one local 404 panel with zero selected screen containers, and remains a local proof artifact rather than a deployable framework app.

## Proof Output

`viewspec prove` writes:

- `.viewspec-app-proof/APP_PROOF.md`
- `.viewspec-app-proof/app_proof_report.json`
- `.viewspec-app-proof/app_support_bundle.json`
- `.viewspec-app-proof/screens/<screen_id>/viewspec.intent.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/index.html`
- `.viewspec-app-proof/screens/<screen_id>/artifact/provenance_manifest.json`
- `.viewspec-app-proof/screens/<screen_id>/artifact/diagnostics.json`

React proofs also write `.viewspec-app-proof/app_analysis_evidence.json`. The bounded main report contains compact
Freerange/Pretext status, coverage, engine, cache, phase, timing, and error summaries plus the evidence file's byte
count and SHA-256. The separately bounded evidence file retains the complete host/analyzer reports for local forensic
inspection, avoiding duplicate analyzer payloads overflowing `app_proof_report.json`.

The proof report keeps report schema metadata separate from app contract metadata: `schema_version` describes the report format, while `app_schema_version` records the validated AppBundle contract version. It uses `proof_level: "app_contract_source_artifacts"`, `target: "html-tailwind"`, `policy.network_calls: "none"`, route assertions, screen hashes, manifest summaries, check status, and the validated resource binding mode. V2 reports include `resource_binding: "fixture_readonly_v0"`, `binding_scope: "declared_resource_views_only"`, concrete assertion counts, per-view status, and a binding digest.

With `--with-shell`, `viewspec prove` also writes `.viewspec-app-proof/app-shell/index.html`, `.viewspec-app-proof/app-shell/shell_manifest.json`, and `.viewspec-app-proof/app-shell/diagnostics.json`; the proof report uses `target: "html-tailwind-app"`, `route_navigation: "static_shell_v0"`, `shell_artifact_hash`, `shell_manifest_hash`, shell route assertions, and the same per-screen proof data.

For V3/V4 shell proofs, `viewspec compile` and `viewspec prove --with-shell` also write matching `state_reducer.ts` and `state_manifest.json`, record `state_contract_hash`, `state_reducer_hash`, `state_manifest_hash`, replay status, and reducer conformance status. Replay assertions execute through the normalized mutation definitions in the Python interpreter, and the generated reducer is imported with local Node and compared against the Python replay before returning a successful report. **Node.js (>=18) on `PATH` is a prerequisite for V3/V4/V5 conformance** — without it, `viewspec compile` and `viewspec prove` fail with `APP_STATE_REDUCER_NODE_UNAVAILABLE` (install Node, or use a V1/V2 AppBundle, which needs no Node).

## Explicit Anti-Goals

- AppBundle V0 is not required to prove browser runtime navigation, back/forward behavior, deep linking, focus restoration, scroll restoration, or route transition animation.
- AppBundle V0 is not required to support dynamic routes, route params, query strings, hash fragments, encoded route aliases, redirects, route guards, nested routers, or locale-aware routing.
- AppBundle V0 is not required to detect every semantic mismatch between fake fixture resources and duplicated data inside embedded screen IntentBundles.
- AppBundle V0 is not required to bind fixture resources into screen rendering, infer screen data dependencies, deduplicate repeated data across screens, or prove data-flow consistency.
- The `react-tailwind-app` target emits a runnable Vite/React/Tailwind frontend and bounded host adapter; it does not generate Next.js, arbitrary routers, API clients, backends, database schemas, authentication, persistence, or optimistic server mutation handlers.
- AppBundle V0 is not required to optimize for very large apps, streaming validation, incremental proof, cross-bundle imports, shared screen libraries, or monorepo-scale app composition.
- AppBundle V0 is not required to certify accessibility, pixel-perfect visual equivalence, cross-browser behavior, production deployment readiness, or arbitrary host-app compatibility.
- Static Shell V0 does not support browser back/forward history semantics, live data or text rebinding, or deployable framework generation. Those bounded frontend behaviors belong to `react-tailwind-app`; scroll/focus restoration, transitions, multi-tab synchronization, service workers, persisted state, nested or dynamic routing, redirects, route guards, query strings, locale routing, lazy loading, and bundle splitting remain out of scope for both local targets.
- Resource Binding V0 is not required to prove formatted, localized, case-normalized, concatenated, abbreviated, rounded, pluralized, date-formatted, currency-formatted, or otherwise transformed fixture values.
- Resource Binding V0 is not required to infer that display labels, aliases, derived columns, badges, icons, colors, severity ordering, or human-readable summaries correspond to fixture fields.
- Resource Binding V0 is not required to support nested record fields, arrays, objects, joins, cross-resource references, computed fields, filters, sorting, pagination, grouping, aggregation, or query languages.
- Static Shell Resource Binding V0 remains proof-only. The React target projects declared scalar resource-view fields from host props or generated state into checked bindings, but does not infer undeclared data flow, persist state, or synchronize with a server.
- Resource Binding V0 is not required to resolve semantic intent when multiple fixture fields intentionally contain the same scalar value unless the declared record-field assertion boundary is unambiguous.
- State IR V0 is not required to generate Zustand, Redux, SwiftData, CRDT, websocket, optimistic reconciliation, persistence, auth, backend/API client, package install, or gesture/pointer runtimes.
- State IR V0 is not required to relax local V1/V2/V3 caps, stream massive dynamic apps, discover plugins, execute untrusted code,.
- Visibility V0 in AppBundle V4 is not required to support boolean condition composition (and/or/not), animation or transitions, focus management on show/hide, `view:` targets, or any data/text rebinding; it is bounded conditional show/hide only, and conditions over states driven to unexpected JSON shapes stay total (never crash) under Python-equals-JavaScript semantics verified by Node conformance. AppBundle V5 adds bounded `all`, `any`, and `not` composition and keeps every other limit.
- State Text V0 in AppBundle V4 is not required to support formatters or more than one placeholder. AppBundle V5 adds the closed formatter set and up to 8 named placeholders, and still excludes free expressions, JSON paths, host functions, HTML, localization, and locale or time zone behavior.
