# ViewSpec documentation index

User-facing documentation for the local ViewSpec SDK and CLI. Start with the top section; the others
are reference material for when a command, contract, or proof points you there.

## Start here

- [Getting Started](getting-started.md): Install, the first `viewspec prove` bundle, Studio and its
  inspector, and the intent lifecycle in five minutes.
- [Agent Integration](agent-integration.md): The agent contract. An agent writes the compact form by
  default and runs the repair loop: lossless `fixes` first, then `correction_prompt` or an RFC 6902
  `--patch` for what remains. Then it compiles and proves. `viewspec mcp` serves six tools: `init`,
  `compile`, `check`, `patch`, `prove`, and `studio`.
- [Proof Bundle](proof-bundle.md): How to read `.viewspec-proof/`: `PROOF.md`, `proof_report.json`,
  the redacted support bundle, and failure codes.

## Concepts

One short page for each idea behind the JSON. Each page has one complete example that passes
`viewspec check`.

- [Substrate](concepts/substrate.md): The data: nodes, attributes, slots, and edges.
- [Binding](concepts/binding.md): How one substrate value reaches the screen, exactly once.
- [Motif](concepts/motif.md): The eleven UI patterns and what each one needs.
- [Region](concepts/region.md): The layout tree that holds bindings and motifs.
- [Compact form](concepts/compact-form.md): The default authoring form and how ViewSpec checks it.
- [Proof](concepts/proof.md): What `check` and `prove` verify, and what they do not.

## For agents

- [Agent skill](../skills/viewspec/SKILL.md): The authoring rules as an agent skill. Install it with
  `npx skills add nxrobins/viewspec --skill viewspec`.
- [ViewSpec next to json-render, A2UI, and MCP Apps](comparison.md): Where each runs, who writes the
  spec, and what each checks.

## Source contracts

- [AppBundle V1 to V5](app-bundle-v0.md): Multi-screen internal tools: fixtures, bounded state,
  visibility, bounded expressions, the Static Shell V0 proof target, and the runnable
  `react-tailwind-app` target.
- [Custom Motif Authoring](custom-motif-authoring.md): Trusted local motif plugins that lower into
  `CompositionIR`.
- [Local HTML Wedge](local-html-wedge.md): Importing existing HTML as a fallback path.
- [MCP Apps](mcp-apps.md): Rendering an MCP tool's result as an interactive `ui://` view from Python.
- [`manifest-v1.schema.json`](manifest-v1.schema.json): JSON Schema for the
  `provenance_manifest.json` that every compiled artifact carries.

## Emitters and known limits

- [Emitter Matrix](emitter-matrix.md): Capability matrix across HTML/Tailwind, React TSX, and React
  Tailwind TSX.
- [React Host Component Registry](react-registry.md): `--registry` for the React targets, the
  primitive contract, the manifest section, and the `REGISTRY_*` codes. The registry is host input:
  agents never write it.
- [Known Limits: HTML/Tailwind](known-limits-html-tailwind.md)
- [Known Limits: React TSX](known-limits-react-tsx.md)
- [Known Limits: React Tailwind TSX](known-limits-react-tailwind-tsx.md)
- [Known Limits: Accessibility Proof](known-limits-a11y.md): Exactly what the fail-closed contrast and
  accessible-name checks prove, and what they deliberately do not.

## Stability and releases

- [Compatibility](compatibility.md): What is covered by the stable contract.
- [SDK Reliability](sdk-reliability.md): Determinism guarantees and the checks behind them.
- [Release Checklist](release-checklist.md): How a release is built and published.
