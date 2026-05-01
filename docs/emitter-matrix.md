# Emitter Matrix

| Capability | HTML/Tailwind | React TSX | SwiftUI | Flutter |
|---|---|---|---|---|
| CompositionIR primitives | Shipped | Shipped | Shipped | Shipped |
| Provenance manifest | Shipped | Shipped | Shipped | Shipped |
| Inputs | Native DOM | React state | SwiftUI bindings | Stateful widget |
| Submit actions | CustomEvent | `onAction` callback | callback | callback |
| Visibility rules | DOM listeners | conditional JSX | conditional view | conditional widget |
| Custom motifs | Lowered to primitives | Lowered to primitives | Lowered to primitives | Lowered to primitives |
| Runtime recording | Live static page | Live React page | External simulator | External emulator |

Known limits are documented per emitter so launch reviewers can inspect the boundary honestly.
