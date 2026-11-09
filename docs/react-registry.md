# React Host Component Registry

`viewspec compile --registry REGISTRY.json` renders the React targets through host-owned components.
The compiler keeps structure, semantics, and provenance. The host supplies one module whose named
exports render each primitive kind. The agent still writes only the IntentBundle, canonical or
compact: it never writes components, and it never writes registries.

The flag applies to `--target react-tsx` and `--target react-tailwind-tsx`. Any other target fails
with `REGISTRY_TARGET_UNSUPPORTED`. Without `--registry`, both targets write the same bytes they wrote
before.

The registry composes with the source pipeline. `--patch`, compact normalization, and the lossless
fixes run first, in that order, and the registry renders the canonical bundle they produce. A
compact source and its canonical twin compiled with one registry give the same `ViewSpecView.tsx`.

## Getting started

```bash
viewspec init registry --out react-tailwind-output/viewspec-registry.json
viewspec compile viewspec.intent.json --target react-tailwind-tsx \
  --registry react-tailwind-output/viewspec-registry.json --out react-tailwind-output/
viewspec check react-tailwind-output/
```

`viewspec init registry` writes two files: the default registry JSON and the default module,
`viewspec-registry.tsx`, beside it. The default module reproduces the elements ViewSpec emits without
a registry. Keep the module beside `ViewSpecView.tsx`, then replace one export at a time.

`examples/react_registry/` holds a reference module in a shadcn/ui style that uses class names only.
Its layout components (`Root`, `Stack`, `Grid`, `Cluster`) keep the compiled `className`, so the
compiler's grid columns, spans, and gaps still apply. Its visual components keep only the semantic
`vs-*` classes and add their own Tailwind classes, so no host class competes with a recipe class.

## The registry file

```json
{
  "schema_version": 1,
  "module": "./viewspec-registry",
  "primitives": {
    "badge": "Badge",
    "button": "Button",
    "cluster": "Cluster",
    "error_boundary": "ErrorBoundary",
    "grid": "Grid",
    "image_slot": "ImageSlot",
    "input": "Input",
    "label": "Label",
    "root": "Root",
    "rule": "Rule",
    "stack": "Stack",
    "surface": "Surface",
    "svg": "Svg",
    "text": "Text",
    "value": "Value"
  }
}
```

The compiler validates the file before it writes anything:

- `schema_version` is `1`; the only other fields are `module` and `primitives` (`REGISTRY_INVALID`).
- Every primitive kind is mapped exactly once (`REGISTRY_PRIMITIVE_MISSING`). Two kinds may share one
  export.
- No key outside the closed kind list (`REGISTRY_PRIMITIVE_UNKNOWN`).
- Every export is an ASCII TypeScript identifier that starts with an uppercase letter, so JSX treats it
  as a component (`REGISTRY_EXPORT_INVALID`).
- `module` is a relative path that starts with `./`, carries no scheme, URL, backslash, or file
  extension, and does not escape above the output directory with `..` (`REGISTRY_MODULE_INVALID`).

The host's bundler resolves the import relative to `ViewSpecView.tsx` in the output directory. The
compiler does not read, copy, or check the module itself; the host owns it.

## The primitive contract

The React emitters produce fifteen primitive kinds. The Python constant
`viewspec.emitters.react_registry.REACT_PRIMITIVE_KINDS` lists them, and a `ReactPrimitiveContract`
entry in the same module describes each one. A registry build renders every node as
`<Export as="tag" ...props>children</Export>`; the compiled view writes no HTML element of its own.

Every component receives these props:

| Prop | Meaning |
|---|---|
| `as` | The element the default registry renders for this node, from the closed vocabulary `button`, `dd`, `div`, `dl`, `dt`, `h1`, `h2`, `header`, `hr`, `input`, `li`, `main`, `p`, `section`, `table`, `td`, `th`, `tr`, `ul`. A `stack` with `as="table"` receives its rows as children; the component wraps them in `<tbody>`. |
| `id` | The DOM id, `dom-<ir id>`. |
| `data-ir-id`, `data-ir-primitive`, `data-content-refs`, `data-intent-refs`, `data-style-tokens` | Provenance. A component must spread these onto its root element. |
| `className` | `react-tsx`: the closed `vs-*` layout, role, motif, and span classes. `react-tailwind-tsx`: the Tailwind recipe followed by the same `vs-*` classes. |
| `style` | `react-tsx` only: the inline `React.CSSProperties` object, including grid columns and spans. |
| `data-binding-id`, `data-resource-id`, `data-resource-view-id`, `data-record-id`, `data-resource-field`, `data-aesthetic-profile`, `data-visibility-rule`, `data-visibility-state`, `hidden`, `data-state-text-id` | Present when the node carries them. Spread them with the other `data-*` props. |
| `children` | Child nodes for a container; the text expression for a text-bearing kind. |

Per kind:

| Kind | Export (default) | `as` values | Children | Also receives |
|---|---|---|---|---|
| `root` | `Root` | `main`, `div` | nodes | `data-viewspec-screen-root` when embedded |
| `stack` | `Stack` | `div`, `dl`, `section`, `table`, `ul` | nodes | `role="form"` and `aria-label` for a form motif |
| `grid` | `Grid` | `div` | nodes | |
| `cluster` | `Cluster` | `div`, `tr` | nodes | |
| `surface` | `Surface` | `div`, `header`, `li`, `section` | nodes | `role`, `aria-label`, `aria-busy` for form, state, and hero motifs |
| `text` | `Text` | `div`, `dd`, `p`, `td` | text | `scope="row"` for a table row header |
| `label` | `Label` | `div`, `dt`, `p`, `th` | text | `scope="row"` for a table row header |
| `value` | `Value` | `div`, `dd`, `h1`, `h2`, `td` | text | heading level is the `as` value |
| `badge` | `Badge` | `div`, `dd`, `td` | text | |
| `input` | `Input` | `input` | none | `type="text"`, `name`, `value`, `onChange`, `aria-label` or `aria-labelledby` |
| `image_slot` | `ImageSlot` | `div` | text (the alt text) | `role="img"`, `aria-label` |
| `rule` | `Rule` | `hr` | none | |
| `svg` | `Svg` | `div` | text (the label) | `role="img"`, `aria-label` |
| `button` | `Button` | `button` | text | `type="button"`, `data-action-id`, `data-action-kind`, `data-action-target-ref`, `data-payload-bindings`, `onClick` |
| `error_boundary` | `ErrorBoundary` | `div` | text | `role="alert"` |

The `onClick` and `onChange` handlers are the compiled view's own: they collect payload values and call
`onAction`, or update the view's input state. A component passes them through. Layout facts beyond
`as` live in `className` and `style`; `provenance_manifest.json` carries each node's full props under
`nodes[<id>].props` for a component that wants more.

## What the manifest records

The manifest gains a `registry` section:

```json
"registry": {
  "sha256": "<sha256 of the canonical registry JSON>",
  "module": "./viewspec-registry",
  "primitive_count": 15,
  "primitives": { "badge": "Badge", "...": "..." }
}
```

`sha256` is the SHA-256 of the registry as JSON with sorted keys and no whitespace:
`{"module": ..., "primitives": {...}, "schema_version": 1}`. The same registry file gives the same
`sha256` and the same emitted bytes. `command_args` records `--registry <file name>` after any
`--patch <file name>`. The section sits beside the other additive keys: a compact source still adds
`source_format` and `compact_source_sha256`.

`viewspec check DIR` verifies the section against the emitted source and fails with
`REGISTRY_MANIFEST_MISMATCH` when:

- the section is malformed, or its `sha256` or `primitive_count` does not match its content;
- the import in `ViewSpecView.tsx` does not name the module, or imports exports the view does not use;
- a node renders through an export other than the one the registry maps for its `data-ir-primitive`;
- a node has no closed `as` element;
- the source imports a registry while the manifest declares none, or an HTML artifact declares one.

The semantic digest reads `as` as the node's element, so a registry build and a plain build of the same
bundle share one `semantic_digest`.

## Proving a registry build

`viewspec prove DIR --install --json` on a `react-tailwind-tsx` artifact whose manifest names a
registry copies the module from `DIR/<name>.tsx` into the reference host beside `ViewSpecView.tsx`,
builds it, and runs the same DOM, provenance, style, and action checks. It also asserts
`data-ir-primitive` on every manifest node in every viewport and reports the count as
`registry_primitive_assertion_count`; `assertion_requirements` sets its minimum to the manifest node
count. The host report, `evidence/host-report.json`, gains
`registry: {sha256, module, primitive_count, module_file, module_hash}`.

The reference host is bounded: the module must be a single file named by a single-segment specifier
(`./name`), import only `react`, call no `require()`, and contain no active surface such as `fetch()`
or `dangerouslySetInnerHTML`. Otherwise the proof fails with `REGISTRY_MODULE_INVALID`. A host module
that imports its own design system is proved in the host's own test suite, not here.

The proof's computed-style checks read the compiled classes in the manifest. The default module and the
CI fixture keep those classes, so they pass. A module that replaces them, such as the shadcn-style
example, builds and renders in the reference host but fails those checks by design: the proof reports
`nonconformant` with `HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK`. Prove such a module in the host's own suite.

CI runs the React Tailwind host proof a second time through `tests/fixtures/react_registry/`, a registry
whose components spread the compiler's props and mark each element with `data-host-component`.

## What this does not do

- It does not let the model author components or registries. Both are host input.
- It does not change the default output. `--registry` is opt-in per compile.
- It does not pass design tokens, theme values, or free-form props to components. The props are the
  ones listed above.
- It does not prove an arbitrary host module. The reference host proves a self-contained module;
  `viewspec check` proves the compiled view against the manifest.
