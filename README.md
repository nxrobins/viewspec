# ViewSpec

**Describe intent. Compile a checked interface.**

ViewSpec is a local, deterministic UI compiler for coding agents. An agent describes semantic UI,
state, and actions as `IntentBundle` or `AppBundle` JSON. ViewSpec compiles that source into HTML or
React with provenance for every node, then checks and proves the result. There is no hosted
service: the SDK makes no network calls and is MIT licensed.

It also works in the other direction. The agent lift describes a page that already exists (its
controls, forms, and messages) to an agent that has to operate it, and checks each request before a
browser acts. See [Reading and operating live pages](#reading-and-operating-live-pages).

Generated DOM and framework code are compiler output. Change the semantic source and recompile
instead of patching output by hand.

## Quick Start

```bash
python -m pip install viewspec
viewspec prove --out .viewspec-proof
```

The package is on [PyPI](https://pypi.org/project/viewspec/). Start with `.viewspec-proof/PROOF.md`. The same
directory holds the machine-readable `proof_report.json`, a redacted `support_bundle.json`, the
semantic source, the generated artifact, and its provenance manifest.

## Six commands

```bash
viewspec init                                      # write viewspec.intent.json
viewspec compile viewspec.intent.json --out dist/  # write dist/index.html
viewspec check dist/                               # fast static, provenance, and security checks
viewspec prove viewspec.intent.json                # compile, verify, and write a proof bundle
viewspec studio                                    # live-reloading local preview
viewspec mcp                                       # the five above, plus patch, as MCP tools for an agent
```

| Command | Takes | Does |
|---|---|---|
| `viewspec init [intent\|app\|design\|compact\|registry]` | what to write | Writes a starter `viewspec.intent.json`, `viewspec.app.json`, `DESIGN.md`, compact-form `viewspec.compact.json`, or the default React component registry with its module. |
| `viewspec compile INPUT --out DIR` | IntentBundle, AppBundle, or imported HTML | Writes the artifact and its manifest. The kind is detected from the file. |
| `viewspec check PATH` | a source file, or a compiled directory | Contract checks for a source; provenance, tamper, and active-content checks for a directory. |
| `viewspec prove [SOURCE]` | nothing, a source file, or a compiled directory | Compiles and verifies, adding browser layout, Pretext, and Freerange evidence on request. |
| `viewspec studio [SOURCE]` | a source file | Serves a preview on 127.0.0.1 that rebuilds whenever the source or `DESIGN.md` changes. |
| `viewspec mcp` | nothing | Serves the other five, plus a `patch` tool, to a coding agent as six MCP tools over stdio. Needs `viewspec[agents]`. |

Every command accepts `--json`. Exit codes are `0` for success, `2` for input or contract failures,
and `1` for internal errors. Run `viewspec COMMAND --help` for the options.

`check` and `compile` apply lossless fixes to an IntentBundle before validation and report them
under `fixes`; `--no-fix` turns that off, `--fixed-out PATH` keeps the fixed source, and `--patch
OPS.json` applies an RFC 6902 JSON Patch to the source in memory first. Lossy problems are never
fixed. See [agent integration](docs/agent-integration.md#error-repair-loop).

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

The same bundle can be written in the compact form: the Python builder's calls as JSON, at about a
fifth of the size. Set `"format": "compact"` at the root and list each motif with its items.
`viewspec check`, `compile`, and `prove` detect a compact file and normalize it into the canonical
bundle. The lossless fixes, the unchanged validator, and the compiler then run on that bundle, and
`--patch` applies to the compact file as written. `--canonical-out viewspec.intent.json` on `check`
or `compile` writes the canonical bundle after the fixes. `viewspec init compact --out
viewspec.compact.json` writes the starter dashboard in this form. See
[Compact form](docs/agent-integration.md#compact-form).

For React with Tailwind, the compiled artifact can also be verified in a real browser across the
canonical 390, 768, and 1440 pixel viewports:

```bash
viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json
```

`--install` authorizes a bounded `npm ci --ignore-scripts` in an isolated template; nothing is
installed without it. To verify an artifact that is already compiled, pass its directory:
`viewspec prove react-tailwind-output/ --install`.

`html-tailwind` and `mcp-app` documents get the same proof with nothing to build, and `--pretext`
adds a native-DOM text-layout check. An MCP App runs inside the official SDK's `AppBridge`:

```bash
viewspec prove viewspec.intent.json --target mcp-app --install --pretext
```

Without `--install` or `--pretext`, `viewspec prove` on these targets stays a fast source proof that
starts no browser.

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
state with a generated TypeScript reducer, checked under Node against the Python reference at the
initial state and on each declared replay; V4 adds conditional visibility and scalar
state text. V5 composes visibility conditions with `all`, `any`, and `not` and fills text with
several values through closed formatters that use no locale or time zone. See the
[AppBundle contract](docs/app-bundle-v0.md).

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

The package exports `IntentBundle`, `AppBundle`, `ASTBundle`, `ViewSpecBuilder`,
`DesignSystemContext`, `load_design_system`, `compile`, `compile_mcp_app`, `check`, `prove`,
`ViewSpecError`, `ERROR_CODES`, and `__version__`. Anything else is internal and may change.
`ViewSpecError` is the base of the compiler, design, AppBundle, and MCP App errors. The
motif-specific builders live in `viewspec.sdk`.

## MCP Apps

Render a Python MCP tool's result as an interactive view inside an MCP host, with no JavaScript
build. `compile_mcp_app()` turns a bundle into a checked `ui://` resource, and `register_mcp_app()`
publishes it on FastMCP:

```python
from viewspec import compile_mcp_app
from viewspec.mcp_apps import register_mcp_app

app = compile_mcp_app(builder.build_bundle(), uri="ui://ops/invoice")
tool = register_mcp_app(server, app)

@tool()
def show_invoice() -> dict:
    return {"viewspecBindings": {"items_row_1_value": "$4,500"}}
```

To change how many rows the view shows, return `app.render(new_bundle)` instead, which compiles the
new bundle and swaps it in. See [MCP Apps](docs/mcp-apps.md) for actions, live updates, and known
limits, and
[`examples/mcp_app_incident_console.py`](examples/mcp_app_incident_console.py) for a runnable server.

## Reading and operating live pages

ViewSpec also works in the other direction: it describes a page that already exists to an agent
that has to use it. `lift_html(html, profile="agent")` takes a rendered page and lists every visible
control with the name the browser gives it and its live state, each form as a typed tool, and the
messages the page shows. It does not keep the value of a field it takes for a secret: a password
field, a one-time code or card field, or a field whose name or label names one, such as a token or
an API key. The rule reads names, so it cannot see a secret in a field that is named for something
else; [docs/local-html-wedge.md](docs/local-html-wedge.md) gives the rule in full. The planner
checks a request before a browser does anything, and a diff of two lifts says what an action
changed:

```python
from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_form_call
from viewspec.raw_html_diff import diff_lifts

lift = lift_html(rendered_html, profile="agent")
[signup] = lift.form_tool_schemas()  # {"name": "sign_up", "input_schema": {...}}

try:
    plan_form_call(lift, "sign_up", {"email": "bo", "password": "hunter", "terms": True})
except AgentActionError as error:
    error.details  # 'bo' is not an email address; the value is shorter than 8 characters

arguments = {"email": "bo@example.com", "password": "correct horse", "terms": True}
steps = plan_form_call(lift, "sign_up", arguments)  # fill, fill, check, click

after = lift_html(html_after_submit, profile="agent")
diff_lifts(lift, after).lines()  # ["message alert 'Check your inbox'"]
```

The planner refuses what the page cannot take, with the reason: a disabled control, a value not in
the list, and anything the browser's own form validation would refuse when the form is sent (an
email or URL format, `minlength`, `pattern`, `min`, `max`, `step`). Names follow the accessible-name
rules, text stays in page order around inline elements, and a control inside a clickable wrapper
is still found. See the [agent lift profile](docs/local-html-wedge.md#agent-lift-profile).
This part is pure Python and makes no network calls.

Driving a real browser lives in
[`experiments/browser-agent-probe`](experiments/browser-agent-probe/README.md): a Playwright bridge
and an MCP server with three tools, `observe`, `act`, and `call`. It types into comboboxes and picks
their options, picks dates and ranges from custom calendars, uploads and downloads files within
folders you name, scrolls infinite lists, waits for a change, and reports which fields the browser
refused when a form is sent. Whoever starts the server decides whether a form post goes through
(it is blocked by default) and which origins the browser may open; the agent cannot change either.
It is an experiment, outside the package and its guarantees, because it drives a browser that
loads whatever the agent opens.

Measured on all 61 [W3C ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/example-index/)
examples, the reference implementations of every ARIA widget pattern:

| Measure | Result |
|---|---:|
| Visible controls found | 3,532 of 3,551 (99.5%) |
| Names that agree with the in-page library's | 99.6% |
| Locators confirmed by Playwright | 3,548 of 3,548 |
| Values of fields the secret rule marks, found in any output | 0 |
| One action on each kind of widget in each example | 58 of 64 |

Two of these rows check the lift against itself, not against the browser. The names are compared
with the probe's in-page library, which follows the same naming rules in JavaScript. The secret
count searches the outputs for the values of the fields the secret rule marked, so it cannot show a
secret the rule did not mark. The locators are checked by Playwright, which is independent.

Of the 19 it misses, 11 are drawn inside inline SVG, which the sanitizer removes; the other 8 are
wrappers it drops on purpose, listing the controls inside them instead. `apg_eval.py` in the probe
folder repeats the measurement, and `real_sites_eval.py` re-runs seventeen tasks on public sites
(Wikipedia, python.org, GOV.UK, react-select, flatpickr, jQuery UI, and others), among them each bug
those sites found. This is evidence that an agent can operate these patterns, not accessibility
certification.

## Agents

An agent writes the compact form by default: `viewspec.compact.json`, an IntentBundle written as the
Python builder's calls. For the starter dashboard it is about a sixth the size of the canonical
bundle. The agent writes the canonical form only for what the compact form cannot express: a
`bulk_action`, or a screen inside an AppBundle.

Then it runs the repair loop until `ok` is true. `viewspec check FILE --json` applies lossless fixes
and lists them under `fixes`. It never fixes a lossy problem. For each issue that remains, the agent
regenerates the file from `correction_prompt` or applies an RFC 6902 JSON Patch with
`--patch OPS.json`. On a compact file, patch pointers name the compact document, and
`--canonical-out PATH` writes the canonical bundle that the fixes and canonical issues name.

```bash
viewspec init compact --out viewspec.compact.json
viewspec check viewspec.compact.json --json
viewspec compile viewspec.compact.json --out dist/
```

Give your agent the published contract, then let it generate JSON with structured output:

- [`agent-system-prompt.txt`](https://viewspec.dev/agent-system-prompt.txt): the authoring rules.
- [`agent-intent-compact.schema.json`](https://viewspec.dev/agent-intent-compact.schema.json): the
  JSON Schema of the compact form.
- [`agent-intent-bundle.schema.json`](https://viewspec.dev/agent-intent-bundle.schema.json) and
  [`agent-app-bundle.schema.json`](https://viewspec.dev/agent-app-bundle.schema.json): the JSON
  Schemas of the canonical IntentBundle and the AppBundle.
- [`skills/viewspec/SKILL.md`](skills/viewspec/SKILL.md): the same rules as an agent skill. Install
  it with `npx skills add nxrobins/viewspec --skill viewspec`.

The [agent integration guide](docs/agent-integration.md) has the full loop. One short page explains
each idea behind the JSON: [substrate](docs/concepts/substrate.md),
[binding](docs/concepts/binding.md), [motif](docs/concepts/motif.md),
[region](docs/concepts/region.md), [compact form](docs/concepts/compact-form.md), and
[proof](docs/concepts/proof.md). [ViewSpec next to json-render, A2UI, and MCP Apps](docs/comparison.md)
compares where each runs and what each checks.

A React component registry is host input. The agent never writes a registry or the components it
names. See [React Host Component Registry](docs/react-registry.md).

[Contributing](CONTRIBUTING.md#opt-in-end-to-end-suites) shows how to run the repository's
model-in-the-loop eval, which measures how often a model's first and corrected answers pass
`viewspec check`, against Anthropic models or an Azure OpenAI deployment.

To give an MCP client the tools `init`, `compile`, `check`, `patch`, `prove`, and `studio`:

```bash
python -m pip install "viewspec[agents]"
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
- **Emitters:** `html-tailwind` (the default), `mcp-app`, `react-tsx`, and `react-tailwind-tsx`.

```
IntentBundle JSON -> ViewSpec compiler -> HTML / React / CompositionIR
       |-- validate the agent contract
       |-- apply DESIGN.md
       |-- generate and replay TypeScript reducers (AppBundle V3 to V5)
       |-- write provenance_manifest.json
       `-- keep DOM and framework code as compiler output
```

Existing HTML can be imported as a [fallback path](docs/local-html-wedge.md): `viewspec compile
page.html --out dist/` sanitizes it and applies `DESIGN.md`. New UI should start from an
`IntentBundle`. The same sanitizer and lift read a live page for an agent; see
[Reading and operating live pages](#reading-and-operating-live-pages).

The React targets can render through host-owned components. `viewspec compile --target
react-tailwind-tsx --registry REGISTRY.json` maps each of the fifteen primitive kinds to a named
export of one host module; `ViewSpecView.tsx` imports those exports and passes each component the
same props and `data-*` provenance attributes it writes today, plus `as`, the element the default
rendering uses. The compiler owns structure, semantics, and provenance; the host owns the components;
the agent still writes only the IntentBundle, canonical or compact. `viewspec init registry` writes the default
registry and a module that reproduces today's elements, so a host can replace one component at a time.
Without `--registry` the output is unchanged. See [React Host Component Registry](docs/react-registry.md).

## Requirements

Python 3.11 or newer. AppBundle V3 to V5 reducer conformance also needs Node.js 18 or newer on `PATH`.
The Freerange proof needs a stable Bun 1.x on `PATH`; ViewSpec never installs it. The browser agent
probe needs Node.js and Playwright for Node with Chromium.

Linux and macOS are supported. Browser verification uses POSIX process groups and is not supported
on Windows, while the pure-Python `compile` and `check` path has no POSIX-only dependency. CI runs
the test suite on Ubuntu, and a Windows job compiles the conformance corpus and compares it with the
pinned goldens. macOS is supported but not exercised in CI.

## More

- [Getting started](docs/getting-started.md)
- [Agent integration](docs/agent-integration.md)
- [Reading a proof bundle](docs/proof-bundle.md)
- [Agent lift profile](docs/local-html-wedge.md#agent-lift-profile) and the
  [browser agent probe](experiments/browser-agent-probe/README.md)
- [Emitter matrix](docs/emitter-matrix.md) and known limits for each target
- [Compatibility](docs/compatibility.md)
- [Security](SECURITY.md) and [contributing](CONTRIBUTING.md)

## License

MIT
