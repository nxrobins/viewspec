# Known Limits: Flutter Emitter

> Flutter is a **hosted-only** emitter, produced by the hosted compiler — not the local SDK, which emits `html-tailwind`, `react-tsx`, and `react-tailwind-tsx`.

- Output is a Dart widget source file, not a complete Flutter project.
- Host apps provide navigation, persistence, and network submission behavior.
- Generated text inputs use controllers owned by the widget state.
- Runtime video requires an external Flutter/Android emulator environment.
