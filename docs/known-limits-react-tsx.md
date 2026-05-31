# Known Limits: React TSX Emitter

- The local SDK emitter supports the bounded local V1 `ASTBundle` contract and writes `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`.
- The generated file is a client component and expects the host app to provide routing, mutation behavior, bundling, and CSS reset.
- Styling is emitted as deterministic inline `React.CSSProperties` plus stable `vs-*` class names for host overrides.
- Inputs are local React state seeded from compiled values.
- Actions are surfaced through an `onAction` callback with `schemaVersion: 1`, `source: "viewspec-react-tsx"`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`.
- `viewspec check` verifies the React source artifact manifest, exact `ViewSpecView.tsx` hash, generated-source markers, diagnostics shape, and absence of active network/runtime escape surfaces.
- React checking is source artifact verification, not a rendered DOM proof inside the host app.
- The emitter does not create a full Next.js, Vite, or React Native app.
