# Known Limits: React Tailwind TSX Emitter

- The local SDK emitter supports the bounded local V1 `ASTBundle` contract and writes `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`.
- The compile target is `react-tailwind-tsx`; the manifest emitter is `react_tailwind_tsx`.
- Agents still emit semantic `IntentBundle` JSON only. Tailwind classes are compiler output, not agent or user input.
- The generated file uses literal `className` strings from the checked-in `tailwind_app_v1` recipe registry. It does not emit inline styles, computed classes, arbitrary bracket utilities, or host-config-dependent utilities.
- Actions are surfaced through an `onAction` callback with `schemaVersion: 1`, `source: "viewspec-react-tailwind-tsx"`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`.
- `viewspec check` verifies source markers, artifact hash, import allowlist, static class inventory, recipe inventory, Tailwind limits, and absence of active network/runtime escape surfaces.
- CI includes an isolated React/Vite/Tailwind host proof that regenerates a current fixture, runs `viewspec check`, builds the checked component, and verifies Chromium-rendered DOM, computed Tailwind styles, profiled aesthetic markers/layout when present, and action payload behavior.
- `viewspec verify-host` exposes the bounded reference-host proof for a specific compiled artifact or for compile-then-verify workflows.
- The host React app owns routing, fetching, mutation behavior, app state, Tailwind installation, and content scanning.

## Constraints & Fallbacks

The React Tailwind host proof is a fail-closed CI gate: it must delete and regenerate the component during the same run, run `viewspec check` before build, import exactly that checked artifact, use `npm ci` from a checked lockfile, and fail on stale artifacts, hash drift, skipped checks, tracked generated files, console/page errors, or any forbidden host CSS. The fixture is intentionally bounded: host CSS is capped to Tailwind import/source plus root sizing/reset, fixture source is capped to 12 tracked non-lock files and 40KB, prep/build/preview/test phases time out at 30s/60s/20s/30s, and docs must describe this as a host proof rather than pixel-perfect visual equivalence.

The host verifier is physically bounded: it must run only in a fresh temporary host directory, copy exactly `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`, reject non-`react_tailwind_tsx` manifests, cap phases at check/copy `10s`, install `90s`, build `60s`, preview startup `20s`, browser test `30s`, cleanup `5s`, and total `180s`, require at least one manifest-backed DOM assertion, at least four computed Tailwind style assertions, all emitted action buttons, profiled aesthetic marker/layout assertions when the checked manifest summary declares them, carry the checked manifest summary into the report, and keep the proof report no larger than `64KB`. It fails closed with exact codes (`HOST_VERIFY_WORKSPACE_UNSAFE`, `HOST_VERIFY_UNSUPPORTED_TARGET`, `HOST_VERIFY_ARTIFACT_CHECK_FAILED`, `HOST_VERIFY_ARTIFACT_HASH_MISMATCH`, `HOST_VERIFY_NODE_MISSING`, `HOST_VERIFY_NPM_MISSING`, `HOST_VERIFY_NODE_MODULES_MISSING`, `HOST_VERIFY_NPM_INSTALL_FAILED`, `HOST_VERIFY_TEMPLATE_MISSING`, `HOST_VERIFY_FORBIDDEN_HOST_CSS`, `HOST_VERIFY_FIXTURE_TOO_LARGE`, `HOST_VERIFY_BUILD_FAILED`, `HOST_VERIFY_BROWSER_RUNTIME_ERROR`, `HOST_VERIFY_DOM_NODE_MISSING`, `HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK`, `HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING`, `HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING`, `HOST_VERIFY_ACTION_COUNT_MISMATCH`, `HOST_VERIFY_PAYLOAD_VALUE_MISMATCH`, `HOST_VERIFY_PROOF_REPORT_INVALID`, `HOST_VERIFY_TIMEOUT`, and `HOST_VERIFY_DOCS_OVERCLAIM`) by returning CLI exit `2` or the MCP error envelope without skipping checks or swallowing browser/build errors.

## Explicit Anti-Goals

- This emitter does not guarantee compatibility with arbitrary Tailwind plugins, custom design tokens, custom breakpoints, custom color scales, or nonstandard Tailwind presets.
- This emitter does not infer app semantics from prose, labels, screenshots, business terminology, or user-authored copy.
- This emitter does not support importing arbitrary Tailwind-authored applications back into full ViewSpec IR.
- This emitter does not guarantee visual equivalence across every host reset stylesheet, global CSS override, dark-mode strategy, or application shell.
- This emitter does not implement multiple competing Tailwind recipe packs, theming systems, or user-extensible recipe registries.
- This emitter does not require fallbacks for hosts that run obsolete Tailwind versions, omit Tailwind content scanning for the generated component, or intentionally disable utilities used by the closed recipe pack.
- This host proof is not a full responsive visual-regression suite and is not required to prove every Tailwind breakpoint, viewport, browser zoom level, or host layout container.
- This host proof is not required to exercise every ViewSpec motif, every Tailwind recipe, or every possible app-role derivation. Broader coverage remains the job of compiler and emitter contract tests.
- This host proof is not a cross-browser or cross-operating-system compatibility matrix. The CI gate may use one Linux Chromium path.
- This host proof is not required to detect every possible silent host-app failure that produces no page error, console error, failed assertion, or broken action payload.
- This host proof is not a performance benchmark for npm install time, Vite build time, Tailwind compile time, or Playwright startup time.
- This host proof does not claim pixel-perfect visual equivalence, design review approval, accessibility certification, or compatibility with arbitrary host CSS resets and Tailwind customizations.
- The host verifier is not required to prove compatibility with arbitrary user React applications, app shells, routers, state managers, bundler plugins, Tailwind customizations, or global CSS resets.
- The host verifier is not required to support package managers other than npm, including pnpm, yarn, bun, or workspace package-manager delegation.
- The host verifier is not required to install Playwright browser binaries automatically unless an explicit future browser-install flag is added.
- The host verifier is not required to make claims about hosted-extended contracts, raw HTML imports, `react-tsx`, SwiftUI, Flutter, or arbitrary custom emitters.
