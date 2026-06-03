# Known Limits: React Tailwind TSX Emitter

- The local SDK emitter supports the bounded local V1 `ASTBundle` contract and writes `ViewSpecView.tsx`, `provenance_manifest.json`, and `diagnostics.json`.
- The compile target is `react-tailwind-tsx`; the manifest emitter is `react_tailwind_tsx`.
- Agents still emit semantic `IntentBundle` JSON only. Tailwind classes are compiler output, not agent or user input.
- The generated file uses literal `className` strings from the checked-in `tailwind_app_v1` recipe registry. It does not emit inline styles, computed classes, arbitrary bracket utilities, or host-config-dependent utilities.
- Actions are surfaced through an `onAction` callback with `schemaVersion: 1`, `source: "viewspec-react-tailwind-tsx"`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`.
- `viewspec check` verifies source markers, artifact hash, import allowlist, static class inventory, recipe inventory, Tailwind limits, and absence of active network/runtime escape surfaces.
- The host React app owns routing, fetching, mutation behavior, app state, Tailwind installation, and content scanning.

## Explicit Anti-Goals

- This emitter does not guarantee compatibility with arbitrary Tailwind plugins, custom design tokens, custom breakpoints, custom color scales, or nonstandard Tailwind presets.
- This emitter does not infer app semantics from prose, labels, screenshots, business terminology, or user-authored copy.
- This emitter does not support importing arbitrary Tailwind-authored applications back into full ViewSpec IR.
- This emitter does not guarantee visual equivalence across every host reset stylesheet, global CSS override, dark-mode strategy, or application shell.
- This emitter does not implement multiple competing Tailwind recipe packs, theming systems, or user-extensible recipe registries.
- This emitter does not require fallbacks for hosts that run obsolete Tailwind versions, omit Tailwind content scanning for the generated component, or intentionally disable utilities used by the closed recipe pack.
