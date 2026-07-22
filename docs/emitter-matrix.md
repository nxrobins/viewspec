# Emitter Matrix

| Capability | HTML/Tailwind | React TSX | React Tailwind TSX | SwiftUI | Flutter |
|---|---|---|---|---|---|
| Availability | Local SDK | Local SDK | Local SDK | Hosted extended | Hosted extended |
| CompositionIR primitives | Shipped | Shipped | Shipped | Shipped | Shipped |
| Provenance manifest | Shipped | Shipped | Shipped | Shipped | Shipped |
| Inputs | Native DOM | React state | React state | SwiftUI bindings | Stateful widget |
| Submit actions | CustomEvent | `onAction` callback | `onAction` callback | callback | callback |
| Styling | Offline `vs-*` CSS | Inline styles + `vs-*` classes | Closed Tailwind recipes | Native | Native |
| Visibility rules | Hosted extended | Hosted extended | Hosted extended | Hosted extended | Hosted extended |
| Custom motifs | Hosted extended | Hosted extended | Hosted extended | Hosted extended | Hosted extended |
| Runtime recording | Live static page | Live React page | Host React app | External simulator | External emulator |

Known limits are documented per emitter so launch reviewers can inspect the boundary honestly.

## App Target

`react-tailwind-app` composes the local React Tailwind TSX emitter with AppBundle V4 routes, resource views, generated reducers, selectors, and visibility. It emits a complete Vite package and an exact-artifact Playwright proof. The app target owns bounded frontend runtime behavior; authentication, persistence, arbitrary API clients, optimistic updates, and deployment infrastructure remain host-owned.

Exact app verification always runs strict TypeScript checking before Vite build. The additive
`--freerange` option then places a pinned `@chenglou/freerange` `0.0.1` analysis between typecheck
and build for only the manifest-described generated numeric kernel. Applicable scopes pass only
with complete required-function coverage; scopes with no supported numeric operations report
`not_applicable`. Bun 1.x or newer is an explicit user-installed prerequisite for applicable
analysis, and only `--install` permits the proof to run the pinned npm dependency step. This does
not extend emitter claims to CSS/Tailwind analysis, rendered geometry, or arbitrary host apps.

The separate opt-in `--pretext` app proof pins `@chenglou/pretext` `0.0.8` and validates its npm
integrity and installed tree. Its `viewspec_pretext_native_dom_v1` profile uses named
`Arial, sans-serif`, waits for fonts, and compares predicted line counts under a fixed 1px line-fit
tolerance with the unchanged native
DOM across 390×844, 768×1024, and 1440×1000 Chromium viewports while reusing preparation across
widths. It needs no Bun and composes after optional Freerange as TypeScript → Freerange → build →
Chromium observation → Pretext report validation → final integrity. Results appear under
`text_layout` and `analyses.pretext`; scope, package, coverage, layout, cache, report, or input drift
fails closed with `APP_PRETEXT_*`. This is not a standalone `react-tailwind-tsx` emitter claim,
cross-browser/Retina evidence, a canvas renderer, or pixel-perfect proof.
