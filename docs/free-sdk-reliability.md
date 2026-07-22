# Free SDK Reliability Audit

## Supported Surface

The free SDK is the local Python package under `src/viewspec`. Its supported reliability surface is:

- fluent bundle construction through `ViewSpecBuilder`
- JSON and protobuf round trips for `IntentBundle` and `ASTBundle`
- deterministic local compilation for `table`, `dashboard`, `outline`, and `comparison`
- HTML emission with offline CSS, provenance, and diagnostics artifacts
- raw HTML `compile`, `lift`, and `diff` CLI/Python APIs for the local wedge
- local `DESIGN.md` parsing for shared offline tokens
- mocked hosted fallback client behavior through `compile_remote()` and `compile_auto()`
- landing-page payload compatibility with `IntentBundle.from_json()`
- React Tailwind TSX host proof through an isolated Vite/Tailwind/Playwright fixture
- per-artifact React Tailwind host verification through `viewspec verify-host`
- first-run proof orchestration through `viewspec prove`
- strict TypeScript checking and opt-in, manifest-scoped Freerange numeric proof for generated
  `react-tailwind-app` packages
- opt-in, manifest-scoped Pretext native-DOM text-layout proof for generated `react-tailwind-app`
  packages

The canonical hosted compiler domain is `https://api.viewspec.dev`. Fly deployment URLs are internal implementation details and should not be used in SDK defaults or public docs.

The static landing page keeps a deployment fallback endpoint so the live demo does not collapse to a static sample while the custom API domain is being cut over. The canonical SDK contract remains `https://api.viewspec.dev`.

## Reliability Guarantees

- Local compilation performs no network calls and no LLM calls.
- Local raw HTML `compile`, `lift`, and `diff` perform no network calls and no LLM calls.
- Raw HTML artifacts do not auto-fetch remote resources when opened; remote image sources are made inert and recorded in `external_refs`.
- Unsupported motif kinds raise `UnsupportedMotifError` so `compile_auto()` can fall back to the hosted compiler.
- Fatal root-shape failures raise `CompilerInputError`.
- Recoverable malformed input returns compiler diagnostics while preserving all valid output.
- For duplicate IDs or duplicate `exactly_once` addresses, the first valid occurrence wins.
- Valid binding IR nodes carry content provenance; all emitted IR nodes carry intent provenance.
- Remote compiler behavior is tested with mocks only; the SDK test suite performs no live network calls.
- React AppBundle verification runs strict `tsc --noEmit` before optional Freerange analysis and
  before Vite build or Chromium execution.
- The Freerange integration accepts only the exact pinned `@chenglou/freerange` `0.0.1` package and
  its expected lock, integrity, binary, protocol, and toolchain identities. Its human-readable CLI
  output is accepted only through ViewSpec's strict version-pinned transcript adapter.
- The Pretext integration accepts only the exact pinned `@chenglou/pretext` `0.0.8` runtime
  dependency and expected resolved URL, npm integrity, installed metadata, complete package tree,
  profile, protocol, scope, and bounded browser report. It requires no Bun.
- A composed React AppBundle proof runs exact-artifact and dependency preflight, strict TypeScript,
  optional Freerange, Vite build, Chromium observation, Pretext report validation, and final input,
  package, and artifact integrity in that order.

## Release Gate

The SDK reliability gate is the `SDK Reliability` GitHub Actions workflow. It runs:

```bash
python -m pip install ".[dev,remote]"
ruff check .
python -m compileall src tests examples
python -m pytest -q
node tests/landing_payload_smoke.mjs
node tests/landing_config_smoke.mjs
node tests/seo_static_smoke.mjs
python -m pip wheel . --no-deps
cd tests/react-tailwind-host
npm ci
npx playwright install --with-deps chromium
npm run verify
```

The workflow sets up Node.js explicitly with `actions/setup-node@v4` before the landing payload smoke test and React Tailwind host proof. The React Tailwind fixture now runs the public `viewspec verify-host ... --install --json` path so the CI proof exercises the same verifier users can run locally.

## Constraints & Fallbacks

The React Tailwind host proof is a fail-closed CI gate: it must delete and regenerate the component during the same run, run `viewspec check` before build, import exactly that checked artifact, use `npm ci` from a checked lockfile, and fail on stale artifacts, hash drift, skipped checks, tracked generated files, console/page errors, or any forbidden host CSS. The fixture is intentionally bounded: host CSS is capped to Tailwind import/source plus root sizing/reset, fixture source is capped to 12 tracked non-lock files and 40KB, prep/build/preview/test phases time out at 30s/60s/20s/30s, and docs must describe this as a host proof rather than pixel-perfect visual equivalence.

The public host verifier preserves the same fail-closed boundary for one artifact at a time: it runs in a fresh temporary host directory, copies only `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`, requires `--install` before running `npm ci --ignore-scripts`, records the checked manifest summary with compact aesthetic style-delta counts plus `assertion_requirements` in the proof report, and returns exact `HOST_VERIFY_*` codes instead of treating missing Node, npm, browser, styles, grid column/span counts, profiled aesthetic marker/layout assertions, DOM, or action payload checks as soft failures. The requirement report always includes base `dom_count` and `style_assertion_count` minimums and manifest-derived `aesthetic_layout_assertion_count`, `aesthetic_profile_assertion_count`, and `grid_span_assertion_count`.

`viewspec prove` preserves the local-first boundary by default: the `html-tailwind` proof performs no package-manager or SDK network calls, writes a bounded proof workspace, compiles through the same public tool path as `viewspec compile`, runs artifact checks, and records `PROOF.md` for humans, `proof_report.json` for tools, and redacted `support_bundle.json` for local support triage. The React Tailwind proof may opt into `npm ci --ignore-scripts` only when `--install` is passed.

The Freerange integration is also explicit opt-in. `viewspec doctor --freerange` is a read-only
readiness probe that may execute `bun --version` but never installs packages, mutates an app, or
invokes a network-capable package runner. For an applicable numeric scope, stable Bun 1.x or newer
must be installed separately on `PATH`; ViewSpec never installs Bun. In
`prove-app --target react-tailwind-app --freerange`, only `--install` permits the pinned
`npm ci --ignore-scripts` dependency step and possible registry access. Use `--install` with the
public `prove-app` workflow because it verifies a freshly generated artifact; only the lower-level
artifact verifier can consume an existing app directory with preinstalled dependencies.

The Freerange result is coverage-gated. `passed` requires at least one manifest-required generated
function, exact and complete findings/audit coverage for all required functions, complete module
initialization, zero partial/unsupported/skipped paths, no unproven assertion verdicts, no assumptions or
unapproved caller requirements, required guarantees, and no error findings. A generated app with no
supported numeric operations reports `not_applicable` with Bun `not_required`, never `passed`.
Runtime/package/integrity/protocol drift, incomplete coverage, unsafe contracts, findings,
timeouts/output limits, or mutated source/config/tool/runtime inputs fail closed with stable
`APP_FREERANGE_*` codes and bounded machine evidence.

This reliability claim covers only the generated numeric kernel and its recorded generated
call-site hashes. It does not cover CSS or Tailwind, rendered geometry, or arbitrary host apps; the
existing Vite and Chromium phases keep their own bounded claims.

The Pretext integration is independently opt-in through
`prove-app --target react-tailwind-app --pretext`. For an applicable manifest-derived scope, the
`viewspec_pretext_native_dom_v1` profile uses the named `Arial, sans-serif` stack, waits for
`document.fonts.ready`, and accounts exactly once for every eligible compiler-owned text surface at
390×844, 768×1024, and 1440×1000 in Chromium. Prepared text is cached by text and supported
typography inputs without width and reused across width-specific layouts. The proof accepts only
matching predicted/native-DOM line counts under a fixed 1px line-fit tolerance with no actual
horizontal or vertical overflow and validates
the claimed cache counters. Hidden surfaces are accounted explicitly; no eligible surfaces reports
`not_applicable`, never `passed`.

Pretext uses the existing semantic DOM as evidence and does not return raw text, mutate or replace
DOM nodes, apply predicted layout, or render the app to canvas. Machine evidence is exposed as
`text_layout` and `analyses.pretext`, including engine/profile/protocol and package identity,
environment and viewports, coverage and cache counts, bounded observations, scope/observation/report
digests, phases, timings, and errors. Package, scope, protocol, coverage, layout, cache, report, or
immutable-input drift fails closed with stable `APP_PRETEXT_*` codes.

This reliability claim is limited to the recorded loaded-font Chromium environment. It is not a
cross-browser or operating-system matrix, Retina/device-pixel-ratio coverage, or canvas-rendering
proof; it is not pixel-perfect visual equivalence, accessibility certification, or arbitrary
host-app certification. If combined with Freerange, Bun is required only for the applicable
Freerange phase.

Aesthetic Profiles V1 preserves the same free-SDK boundary: one view-level token from `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, or `aesthetic.warm_organic` expands into checked local style projections, bounded layout metadata, compact style-delta counts in manifest summaries, and closed React Tailwind recipes; it is not arbitrary CSS, remote assets, runtime LLM calls, or host-app certification.

Public pricing, version, hosted-call, API, package, proof-scope, proof identity, host assertion requirement, and agent asset contract facts live in `demos/public-facts.json`; the static smoke test fails with `PUBLIC_FACTS_DRIFT` if README, landing, LLM, OpenAPI, agent asset, proof assertion, or version metadata disagree with it.

## Generated Demo Drift Gate

Generated-demo tracked-diff checks cover deterministic public demos whose builders expose stable in-memory page generation. The pytest suite verifies the builder-backed `aesthetic-profiles`, `fifteen-lines`, `invariants`, `live-builder`, `motif-switcher`, `provenance-inspector`, `stateful-collections`, and `style-derivation` pages against their builders without rewriting tracked files. Add more generated pages only after their scripts expose deterministic output that avoids timestamps, measured durations, and unstable ordering.
