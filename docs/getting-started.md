# Getting Started: First ViewSpec In 5 Minutes

ViewSpec compiles semantic JSON into checked HTML or React. Everything runs locally: the SDK makes no
network calls, and there is no account or API key.

## Install

```bash
python -m pip install --pre viewspec
```

Python 3.11 or newer. The current SDK is a beta, so `--pre` is required.

## First Proof

```bash
viewspec prove --out .viewspec-proof
```

This writes a starter dashboard, compiles it, checks it, and leaves a proof bundle. Read
`.viewspec-proof/PROOF.md` first. `proof_report.json` is the machine-readable report, and
`support_bundle.json` is a redacted copy that is safe to share when asking for help. See
[Proof Bundle](proof-bundle.md).

ViewSpec proof is not pixel-perfect visual regression, accessibility certification, or arbitrary
host-app certification.

## Canonical Intent Lifecycle

```bash
viewspec init --out viewspec.intent.json
# Edit viewspec.intent.json; optionally author DESIGN.md.
viewspec check viewspec.intent.json --json
viewspec compile viewspec.intent.json --out dist/
viewspec check dist/
```

`viewspec compile` writes `dist/index.html`. For local React source, use `--target`:

```bash
viewspec compile viewspec.intent.json --target react-tsx --out react-output/
viewspec compile viewspec.intent.json --target react-tailwind-tsx --out react-tailwind-output/
```

That writes `react-output/ViewSpecView.tsx`. Treat compiled output as an artifact: change
`viewspec.intent.json` and recompile. Run `viewspec init design --out DESIGN.md` once when the
repository has no governed design file, then add `--design DESIGN.md` to compile and prove.

Generated source can be revised by regenerating the whole bundle or by applying a standard JSON
Patch to it; `viewspec check` validates the result either way.

## Preview with Studio

```bash
viewspec studio
```

Studio finds the one `viewspec.app.json` or `viewspec.intent.json` in the current directory, or takes
a path, then serves a preview on `http://127.0.0.1:4388/`. Editing the source or `--design DESIGN.md`
rebuilds the preview and reloads the page. A broken edit leaves the last good build on screen with
the error shown over it. Stop it with Ctrl-C.

Studio previews the static HTML targets. It listens on loopback only and refuses requests for any
other host. `--port` picks another port, `--no-open` skips launching a browser, and `--json` prints
one line with the URL once it is serving, which suits an agent starting it in the background.

## Browser Proof

`viewspec prove` adds a real browser run when it has a React Tailwind artifact and permission to
install its pinned dependencies:

```bash
viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

`--install` authorizes a bounded `npm ci --ignore-scripts` in an isolated template. Pass a directory
to verify an artifact that is already compiled:

```bash
viewspec prove react-tailwind-output/ --install --out .viewspec-verification
```

That writes `result.json` and its evidence under the output directory. The exit code is `0` for
conformant, `1` for nonconformant, and `2` for indeterminate. `--plan FILE` supplies a custom
verification plan.

## Runnable React App Golden Path

Use the checked AppBundle V4 starter when the goal is a running multi-screen React application rather than a source proof:

```bash
viewspec init app --template react-app --out viewspec.app.json
viewspec compile viewspec.app.json --target react-tailwind-app --out app-dist
cd app-dist
npm ci
npm run dev
```

The target writes a complete Vite/React/Tailwind package. Browser-history routes, generated reducer events, resource-backed text, selectors, and visibility update at runtime. This is bounded browser navigation and frontend-state proof; host applications can pass resource records and typed callbacks through `ViewSpecAppProps`.

Make changes in `viewspec.app.json` and recompile with `--force`; do not edit generated React. Prove the exact generated package in Chromium with:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install
```

With `--install`, the proof is allowed to run `npm ci --ignore-scripts`; keep that flag on the
public `viewspec prove` workflow because it verifies a freshly generated tree. The lower-level artifact
verifier is the separate path for an existing app directory with dependencies already installed.
The proof then runs strict TypeScript checking before the Vite production build and generated
Playwright assertions for every static route, browser history, the unknown-route fallback,
declared state actions, live resource rebinding, selector replay, and visibility.
Authentication, persistence, arbitrary API clients, and production infrastructure remain host-owned.

### Optional Freerange Numeric Proof

When the generated app contains ViewSpec's supported numeric operations, add the pinned Freerange
phase:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange --json
```

Install stable Bun 1.x or newer explicitly and put it on `PATH`; ViewSpec never installs Bun.
The generated proof package pins
`@chenglou/freerange` exactly to `0.0.1`. For this proof, `--install` is the explicit permission for
`npm ci --ignore-scripts`, which may access the package registry. Keep it on the public
`viewspec prove` command, which verifies a freshly generated tree.

The phase order is fixed: exact-artifact preflight and dependency preparation, strict
`tsc --noEmit`, Freerange findings plus coverage audit, Vite build, then Chromium verification.
An applicable analysis reports `passed` only when every manifest-required function is present and
fully analyzed, findings and audit coverage agree, module setup is complete, any reported
assertions are proven, contracts introduce no unapproved requirements or assumptions, and no error
findings remain. A
scope with no supported numeric operations reports `static_analysis.status: "not_applicable"` and
does not require Bun; `not_applicable` is never presented as a passed analysis.

The integration fails closed with stable `APP_FREERANGE_*` codes for missing or unsupported Bun,
package/version/integrity drift, invalid transcripts, incomplete or mismatched coverage, unsafe or
unproven contracts, analyzer findings, bounded-output or timeout failures, and changed proof inputs.
It analyzes only generated numeric-helper source described by the manifest. It does not analyze CSS
or Tailwind, prove rendered geometry, or certify arbitrary hosts.

### Optional Pretext Native-DOM Text Proof

Add Pretext when you want manifest-scoped text-wrapping evidence for the generated React app:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install --pretext --json
# Both independent analyses may run in one immutable proof:
viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange --pretext --json
```

The generated package pins `@chenglou/pretext` exactly to `0.0.8`. ViewSpec validates the lock's
resolved npm artifact and integrity, the installed metadata, and the complete installed package
tree. Pretext itself does not require Bun; Bun remains necessary only for an applicable Freerange
scope. As with the base React proof, keep `--install` on public `viewspec prove`; only the lower-level
artifact verifier accepts an existing app directory with the exact dependencies already installed.

The `viewspec_pretext_native_dom_v2` support profile uses the named `Arial, sans-serif` stack and
measures existing compiler-owned DOM text after `document.fonts.ready` at mobile 390×844, tablet
768×1024, and desktop 1440×1000 in Chromium. Pretext preparation is cached by text and supported
typography inputs without width, then reused for each width-specific layout. The probe compares
predicted and observed native-DOM line counts under a fixed 1px line-fit tolerance and rejects
actual horizontal or vertical overflow; it does
not replace the DOM with a canvas or write predicted layout into the app.

The combined order is artifact/dependency preflight, strict TypeScript, optional Freerange, Vite
build, Chromium observation, Pretext report validation, then final package and artifact integrity.
Read `text_layout` or its `analyses.pretext` alias for engine/profile/environment, viewports,
coverage, cache counts, hashes, phases, timings, and errors; proof identity includes the Pretext
scope, observation, and report digests. Scope, package, protocol, coverage, layout, cache, report,
or immutable-input drift fails closed with `APP_PRETEXT_*`. No eligible compiler-owned text reports
`not_applicable`, not `passed`.

This is bounded native text-wrapping evidence for the recorded Chromium environment. It is not a
cross-browser, Retina/device-pixel-ratio, canvas-rendering, pixel-perfect, accessibility, or
arbitrary-host claim. See [AppBundle V1/V2/V3/V4](app-bundle-v0.md) for the exact support profile
and evidence fields.

## AppBundle V1/V2/V3/V4

Use `viewspec.app.json` for a multi-screen internal tool:

```bash
viewspec init app --out viewspec.app.json
viewspec check viewspec.app.json --json
viewspec compile viewspec.app.json --target html-tailwind-app --out app-dist/
viewspec prove viewspec.app.json --out .viewspec-app-proof --with-shell --json
```

Resource binding is `unbound_v0` by default and `fixture_readonly_v0` with
`viewspec init app --resource-binding fixture-readonly-v0`. The default target is Static Shell V0, a
local proof artifact with bounded browser navigation, not a deployable app. See the
[AppBundle contract](app-bundle-v0.md).

## Machine-readable output

Every command accepts `--json`. `init` and `compile` print one standard tool envelope:

```json
{
  "schema_version": 1,
  "ok": true,
  "summary": "Compiled IntentBundle artifact.",
  "diagnostics": [],
  "external_refs": [],
  "paths": {"html": "dist/index.html", "manifest": "dist/provenance_manifest.json", "diagnostics": "dist/diagnostics.json"},
  "next_actions": ["Run viewspec check dist --json before using the artifact."],
  "errors": [],
  "metadata": {"sdk_version": "...", "network_calls": "none", "target": "html-tailwind", "emitter": "html_tailwind"}
}
```

`check` and `prove` print their report, and `ok` carries the verdict. A failure is the same envelope
with `"ok": false` and `errors: [{code, message, fix}]`. An invalid IntentBundle passed to `compile`
also carries `validation` and `correction_prompt`, and a DESIGN.md error lists its findings under
`diagnostics`.

Exit codes match human mode: `0` on success; `2` for invalid input, including a missing input file,
invalid JSON, an invalid IntentBundle or DESIGN.md, and an existing output without `--force`; and
`1` for an internal error. Some invalid paths exit `1` as well: a directory where a file is expected
(`compile --design`), a file where a directory is expected (`compile --out`), and a permission
error. With `--json` the commands write nothing else to stdout or stderr. Only an argument the
parser rejects is reported as usage text on stderr, with exit `2`, before the command runs.

## Import Existing HTML

Use raw HTML commands only when you already have HTML and need an offline import/fallback artifact:

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec check dist/
```

Raw HTML compile writes `index.html`, `provenance_manifest.json`, and `diagnostics.json`. With `--lift-json`, it also writes `lift.json`. This path is sanitize + theme + manifest. It is not full ViewSpec decompilation, and it does not perform pixel review.

## Programmatic IntentBundle Compile

```python
import json

from viewspec import ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.intent_tools import validate_intent_text

builder = ViewSpecBuilder("invoice")
builder.set_aesthetic_profile("aesthetic.calm_ops")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")

message_binding = builder.add_text_input("message", label="Message", value="")
builder.add_action("send", "submit", "Send", target_region="main", payload_bindings=[message_binding])

bundle = builder.build_bundle()
validation = validate_intent_text(json.dumps(bundle.to_json()))
ast = compile(bundle)
HtmlTailwindEmitter().emit(ast, "output/")
ReactTsxEmitter().emit(ast, "react-output/")
ReactTailwindTsxEmitter().emit(ast, "react-tailwind-output/")
```

The local reference compiler supports safe text inputs, loading/error state motifs, and local action payload events. HTML action events dispatch `viewspec-action` with `detail.schemaVersion: 1`, `source`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`. Collection actions for table/list motifs dispatch `search`, `filter`, `sort`, `paginate`, and `bulk_action` events only; they do not locally mutate data. Pressing Enter inside a local inert form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif. React TSX output uses an `onAction` callback with the same V1 fields and `source: "viewspec-react-tsx"`.

Use `builder.set_aesthetic_profile("aesthetic.calm_ops")` when you want governed art direction without authoring CSS. V1 supports exactly one view-level profile per IntentBundle: `aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, `aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`, `aesthetic.neon_cyber`, or `aesthetic.warm_organic`; profiles are deterministic style and bounded-layout handles, not CSS, pixel-perfect visual proof, accessibility certification, arbitrary host-app compatibility, or design-review approval.

From the CLI, use `--target react-tsx` when you want component source instead of standalone HTML:

```bash
viewspec compile viewspec.intent.json --target react-tsx --out react-output/
```

Use `--target react-tailwind-tsx` when the host React app already has Tailwind and needs compiler-owned utility classes:

```bash
viewspec compile viewspec.intent.json --target react-tailwind-tsx --out react-tailwind-output/
```

The ViewSpec repo CI includes one bounded host proof for this target: it regenerates a representative fixture, runs `viewspec check`, mounts the exact checked `ViewSpecView.tsx` in a Vite/Tailwind host, builds it, and smoke-tests rendered DOM, computed Tailwind styles including grid column/span counts, profiled aesthetic marker/layout assertions, and action payloads in Chromium. The checked manifest summary also carries compact aesthetic style-delta counts when a profile is present. That proof is not run for every user artifact; `viewspec check` verifies source artifact integrity and provenance for the output you compiled.

Run the same bounded proof for a specific artifact with:

```bash
viewspec prove react-tailwind-output/ --install --json
viewspec prove --target react-tailwind-tsx --install --out .viewspec-proof --json
```

`--install` opts into `npm ci --ignore-scripts` inside the isolated reference host. Without it, the command stays no-install and fails fast when host dependencies are absent. JSON proof reports include `assertion_requirements` for expected `dom_count`, `style_assertion_count`, and manifest-derived `aesthetic_layout_assertion_count`, `aesthetic_profile_assertion_count`, and `grid_span_assertion_count` before comparing observed browser assertions.

`viewspec check` also verifies React TSX source artifacts: manifest shape, exact `ViewSpecView.tsx` hash, generated-source markers, diagnostics shape, and absence of active network/runtime escape surfaces. This is source artifact verification, not a rendered DOM proof inside a host React app.

## Theming with DESIGN.md

For local compilation, parse a strict `DESIGN.md` subset and pass it to IntentBundle or raw HTML import compilation:

```python
from pathlib import Path

from viewspec import compile, compile_html, load_design_system

design = load_design_system(path="DESIGN.md")
html_result = compile_html("<h1>Report</h1>", design=design)
ast = compile(builder.build_bundle(), design=design)
# The same file, read by compile_html and compile themselves:
html_result = compile_html("<h1>Report</h1>", design=Path("DESIGN.md"))
ast = compile(builder.build_bundle(), design=Path("DESIGN.md"))
```

`compile(bundle, design=...)` takes a `DesignSystemContext` from `load_design_system()`, a path as
an `os.PathLike` such as `Path("DESIGN.md")`, or a `str` holding DESIGN.md *content*. A `str` never
touches the filesystem: `compile()` does not open it, read it or check whether a file by that name
exists, so passing request text straight through can neither make `compile()` read a local file
nor reveal which local paths exist. `compile(bundle, design="DESIGN.md")` therefore parses the
text `DESIGN.md` as content and fails with `DesignSystemError` ("must begin with YAML front
matter"), as in earlier releases. The error carries an exception note, shown in tracebacks, that
says to pass `Path("DESIGN.md")`; the error and its note are the same for every one-line `str`,
whether or not a file by that name exists. `compile_html(html, design=...)` takes a
`DesignSystemContext` or an `os.PathLike` path the same way, but no `str`.

`load_design_system()` always validates. Findings are in `context.lint_report`, or in
`DesignSystemError.report` when loading fails.

From the CLI:

```bash
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec compile existing.html --design DESIGN.md --out dist/
```

Parse errors, broken token references, and cycles are fatal. Malformed ignorable tokens produce diagnostics and fall back to defaults. `--strict-design` escalates warnings to failure.

Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named colors are ignored with defaults. React/HTML can receive custom `fontFamily` CSS. In local HTML output, `typography.body` styles normal text and `typography.heading` styles prominent values through the compiler's emphasis token. Flutter and SwiftUI coerce custom families to native system defaults and preserve size, weight, and tracking.
