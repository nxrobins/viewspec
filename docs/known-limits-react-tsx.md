# Known Limits: React TSX Emitter

- The local SDK emitter supports the bounded local V1 `ASTBundle` contract and writes `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`.
- The generated file is a client component and expects the host app to provide routing, mutation behavior, bundling, and CSS reset.
- Styling is emitted as deterministic inline `React.CSSProperties` plus stable `vs-*` class names for host overrides.
- Inputs are local React state seeded from compiled values.
- Actions are surfaced through an `onAction` callback with `schemaVersion: 1`, `source: "viewspec-react-tsx"`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`.
- `viewspec check` remains the HTML artifact verifier; React output is source artifact generation, not a rendered DOM proof.
- The emitter does not create a full Next.js, Vite, or React Native app.
