# ViewSpec

**Describe intent. Compile a checked interface.**

ViewSpec is a local, deterministic UI compiler for coding agents. An agent describes semantic UI,
state, and actions as `IntentBundle` or `AppBundle` JSON. ViewSpec compiles that source into HTML or
React with provenance for every node, then checks and proves the result. There is no hosted
service: the SDK makes no network calls and is MIT licensed.

Generated DOM and framework code are compiler output. Change the semantic source and recompile
instead of patching output by hand.

## Quick Start

```bash
python -m pip install --pre viewspec
viewspec prove --out .viewspec-proof
```

The current SDK is a beta, so `--pre` is required; plain `python -m pip install viewspec` follows the
stable channel. The package is on [PyPI](https://pypi.org/project/viewspec/). Start with `.viewspec-proof/PROOF.md`. The same
directory holds the machine-readable `proof_report.json`, a redacted `support_bundle.json`, the
semantic source, the generated artifact, and its provenance manifest.

## Six commands

```bash
viewspec init                                      # write viewspec.intent.json
viewspec compile viewspec.intent.json --out dist/  # write dist/index.html
viewspec check dist/                               # fast static, provenance, and security checks
viewspec prove viewspec.intent.json                # compile, verify, and write a proof bundle
viewspec studio                                    # live-reloading local preview
viewspec mcp                                       # the five above as MCP tools for an agent
```

| Command | Takes | Does |
|---|---|---|
| `viewspec init [intent\|app\|design]` | what to write | Writes a starter `viewspec.intent.json`, `viewspec.app.json`, or `DESIGN.md`. |
| `viewspec compile INPUT --out DIR` | IntentBundle, AppBundle, or imported HTML | Writes the artifact and its manifest. The kind is detected from the file. |
| `viewspec check PATH` | a source file, or a compiled directory | Contract checks for a source; provenance, tamper, and active-content checks for a directory. |
| `viewspec prove [SOURCE]` | nothing, a source file, or a compiled directory | Compiles and verifies, adding browser layout, Pretext, and Freerange evidence on request. |
| `viewspec studio [SOURCE]` | a source file | Serves a preview on 127.0.0.1 that rebuilds whenever the source or `DESIGN.md` changes. |
| `viewspec mcp` | nothing | Serves the other five to a coding agent as MCP tools over stdio. Needs `viewspec[agents]`. |

Every command accepts `--json`. Exit codes are `0` for success, `2` for input or contract failures,
and `1` for internal errors. Run `viewspec COMMAND --help` for the options.

Run `viewspec init design --out DESIGN.md` once when the repository has no governed design file.

## The intent workflow

```bash
viewspec init --out viewspec.intent.json
# Edit viewspec.intent.json, or have your agent generate it.
viewspec check viewspec.intent.json --json
viewspec compile viewspec.intent.json --design DESIGN.md --out dist/
viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/
viewspec check dist/
```

This writes `dist/index.html` and `react-output/ViewSpecView.tsx`. Treat both as artifacts. To revise
a design, regenerate the bundle or apply a standard JSON Patch to it, then run `viewspec check` on
the file.

An `IntentBundle` is capped at 256 KB. That is small enough for an agent to regenerate whole, which
is the expected way to make a change.

For React with Tailwind, the compiled artifact can also be verified in a real browser across the
canonical 390, 768, and 1440 pixel viewports:

```bash
viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

`--install` authorizes a bounded `npm ci --ignore-scripts` in an isolated template; nothing is
installed without it. To verify an artifact that is already compiled, pass its directory:
`viewspec prove react-tailwind-output/ --install`.

ViewSpec proof is not pixel-perfect visual regression, accessibility certification, or arbitrary
host-app certification.

## AppBundle: multi-screen apps

For a multi-screen internal tool, emit `viewspec.app.json` instead of inventing a router:

```bash
viewspec init app --out viewspec.app.json
viewspec check viewspec.app.json --json
viewspec compile viewspec.app.json --out app-dist/
viewspec prove viewspec.app.json --with-shell
```

The default `html-tailwind-app` target is **Static Shell V0**, a local proof artifact with bounded
browser navigation. For a complete Vite, React, and Tailwind project:

```bash
viewspec init app --template react-app --out viewspec.app.json
viewspec compile viewspec.app.json --target react-tailwind-app --out app-dist/
cd app-dist && npm ci && npm run dev
```

Edit `viewspec.app.json` and recompile with `--force`; do not edit generated React. Before handing it
off, prove it in Chromium:

```bash
viewspec prove viewspec.app.json --target react-tailwind-app --install --freerange --pretext
```

Resources are `unbound_v0` by default, or `fixture_readonly_v0` with
`viewspec init app --resource-binding fixture-readonly-v0`. AppBundle V3 adds bounded interactive
state with a generated, replay-proved TypeScript reducer; V4 adds conditional visibility and scalar
state text. See the [AppBundle contract](docs/app-bundle-v0.md).

Generated React apps can opt into two exact-versioned proof integrations by
[chenglou](https://github.com/chenglou): [Freerange](https://github.com/chenglou/freerange) analyzes
each eligible state operation and its generated numeric kernel, and
[Pretext](https://github.com/chenglou/pretext) measures each eligible native-DOM text surface across
the declared routes and viewports. Each is requested explicitly, reported separately, and fails
closed within its documented scope.

## Python API

```python
from viewspec import ViewSpecBuilder, check, compile, prove

builder = ViewSpecBuilder("invoice")
table = builder.add_table("items", region="main", group_id="rows")
table.add_row(label="Design System Audit", value="$4,200")
table.add_row(label="Component Library", value="$8,500")

ast = compile(builder.build_bundle())

report = check("dist/")
proof = prove("viewspec.intent.json", out_dir=".viewspec-proof")
```

`compile()` turns an `IntentBundle` into an `ASTBundle` in memory. Given an `AppBundle`, it writes to
`out_dir` and returns the build report. `check()` and `prove()` take paths, return a report with
`ok`, and report failures in that report instead of raising.

The package exports `IntentBundle`, `AppBundle`, `ASTBundle`, `ViewSpecBuilder`, `compile`, `check`,
`prove`, `CompilerInputError`, `UnsupportedMotifError`, `DesignSystemError`, and `AppBundleError`.
Anything else is internal and may change. The motif-specific builders live in `viewspec.sdk`.

## Agents

Give your agent the published contract, then let it generate JSON with structured output:

- [`agent-system-prompt.txt`](https://viewspec.dev/agent-system-prompt.txt): the authoring rules.
- [`agent-intent-bundle.schema.json`](https://viewspec.dev/agent-intent-bundle.schema.json) and
  [`agent-app-bundle.schema.json`](https://viewspec.dev/agent-app-bundle.schema.json): JSON Schemas.

`viewspec check FILE --json` returns a `correction_prompt` that an agent can act on directly. The
[agent integration guide](docs/agent-integration.md) has the full loop.

To give an MCP client the tools `init`, `compile`, `check`, `prove`, and `studio`:

```bash
python -m pip install --pre "viewspec[agents]"
claude mcp add viewspec -- viewspec mcp
```

The tools can only reach paths inside the directory the server starts in.

## What the compiler guarantees

1. **Exactly-once provenance.** Every valid data binding is routed exactly once. Conflicting or
   duplicate bindings are resolved deterministically (first occurrence wins) and flagged as a
   diagnostic, never silently dropped or duplicated.
2. **Semantic grouping.** Data is grouped by meaning, not visual adjacency.
3. **Strict ordering.** The original data order is preserved, including across serialization.

Compilation is deterministic: for one ViewSpec version, the same input bytes produce the same output
bytes. CI compares compiled output against pinned goldens on Linux and Windows.

Styling goes through `aesthetic.*` profile tokens and `DESIGN.md`. The profiles are
`aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`,
`aesthetic.editorial_product`, `aesthetic.executive_review`, `aesthetic.brutalist`,
`aesthetic.neon_cyber`, and `aesthetic.warm_organic`. They are deterministic art-direction handles,
not CSS. `DESIGN.md` is strict YAML front matter and requires exact sRGB hex values.

## Motifs

| Builder | Motif | Use case |
|---------|-------|----------|
| `add_table()` | `table` | Tabular data with label-value rows |
| `add_dashboard()` | `dashboard` | KPI cards with label-value pairs |
| `add_outline()` | `outline` | Hierarchical outlines and trees |
| `add_comparison()` | `comparison` | Side-by-side comparisons |
| `add_list()` | `list` | Ordered narrative or task lists |
| `add_form()` | `form` | Inert local form intent with text fields and action payloads |
| `add_detail()` | `detail` | Read-only record, profile, or settings fields |
| `add_empty_state()` | `empty_state` | Absence, no-results, or first-run states |
| `add_loading_state()` | `loading_state` | Current loading state for a collection or region |
| `add_error_state()` | `error_state` | Current error state for a collection or region |
| `add_hero()` | `hero` | Intro sections with eyebrow, title, and description |
| `add_collection_action()` | action helper | `search`, `filter`, `sort`, `paginate`, or `bulk_action` events |

To add motifs, see [custom motif plugins](docs/custom-motif-authoring.md). Plugins are trusted
in-process Python: load only plugins you would trust as any other dependency.

## How it fits together

- **Semantic substrate:** the data graph. What the data is, with no visual intent.
- **ViewSpec:** the intent layer. Regions, bindings, motifs, styles, and actions.
- **CompositionIR:** the compiler's output tree of UI primitives, with full provenance.
- **Emitters:** `html-tailwind` (the default), `react-tsx`, and `react-tailwind-tsx`.

```
IntentBundle JSON -> ViewSpec compiler -> HTML / React / CompositionIR
       |-- validate the agent contract
       |-- apply DESIGN.md
       |-- generate and replay TypeScript reducers (AppBundle V3/V4)
       |-- write provenance_manifest.json
       `-- keep DOM and framework code as compiler output
```

Existing HTML can be imported as a [fallback path](docs/local-html-wedge.md): `viewspec compile
page.html --out dist/` sanitizes it and applies `DESIGN.md`. New UI should start from an
`IntentBundle`.

## Requirements

Python 3.11 or newer. AppBundle V3/V4 reducer conformance also needs Node.js 18 or newer on `PATH`.
The Freerange proof needs a stable Bun 1.x on `PATH`; ViewSpec never installs it.

Linux and macOS are supported. Browser verification uses POSIX process groups and is not supported
on Windows, while the pure-Python `compile` and `check` path has no POSIX-only dependency. CI runs
the test suite on Ubuntu, and a Windows job compiles the conformance corpus and compares it with the
pinned goldens. macOS is supported but not exercised in CI.

## More

- [Getting started](docs/getting-started.md)
- [Agent integration](docs/agent-integration.md)
- [Reading a proof bundle](docs/proof-bundle.md)
- [Emitter matrix](docs/emitter-matrix.md) and known limits for each target
- [Compatibility](docs/compatibility.md)
- [Security](SECURITY.md) and [contributing](CONTRIBUTING.md)

## License

MIT
