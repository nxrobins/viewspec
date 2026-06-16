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

The public host verifier preserves the same fail-closed boundary for one artifact at a time: it runs in a fresh temporary host directory, copies only `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`, requires `--install` before running `npm ci --ignore-scripts`, records the checked manifest summary with compact aesthetic style-delta counts in the proof report, and returns exact `HOST_VERIFY_*` codes instead of treating missing Node, npm, browser, styles, grid column/span counts, profiled aesthetic marker/layout assertions, DOM, or action payload checks as soft failures.

`viewspec prove` preserves the local-first boundary by default: the `html-tailwind` proof performs no package-manager or SDK network calls, writes a bounded proof workspace, compiles through the same public tool path as `viewspec compile`, runs artifact checks, and records `PROOF.md` for humans, `proof_report.json` for tools, and redacted `support_bundle.json` for local support triage. The React Tailwind proof may opt into `npm ci --ignore-scripts` only when `--install` is passed.

Aesthetic Profiles V1 preserves the same free-SDK boundary: one view-level token from `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, or `aesthetic.executive_review` expands into checked local style projections, bounded layout metadata, compact style-delta counts in manifest summaries, and closed React Tailwind recipes; it is not arbitrary CSS, remote assets, runtime LLM calls, or host-app certification.

Public pricing, version, hosted-call, API, package, and proof-scope facts live in `demos/public-facts.json`; the static smoke test fails with `PUBLIC_FACTS_DRIFT` if README, landing, LLM, OpenAPI, or version metadata disagree with it.

## Generated Demo Drift Gate

Generated-demo tracked-diff checks cover deterministic public demos whose builders expose stable in-memory page generation. The pytest suite verifies the builder-backed `aesthetic-profiles`, `fifteen-lines`, `invariants`, `live-builder`, `motif-switcher`, `provenance-inspector`, `stateful-collections`, and `style-derivation` pages against their builders without rewriting tracked files. Add more generated pages only after their scripts expose deterministic output that avoids timestamps, measured durations, and unstable ordering.
