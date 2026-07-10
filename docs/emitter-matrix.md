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
