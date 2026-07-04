# Known Limits: SwiftUI Emitter

> SwiftUI is a **hosted-only** emitter, produced by the hosted compiler — not the local SDK, which emits `html-tailwind`, `react-tsx`, and `react-tailwind-tsx`.

- Output is a SwiftUI view source file, not a complete Xcode project.
- Links and submits are callback-driven; host apps decide navigation and networking.
- Raw SVG is represented as text fallback in V1.
- Runtime video requires an external Mac/Xcode simulator environment.
