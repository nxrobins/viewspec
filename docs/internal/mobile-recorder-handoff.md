# Mobile Recorder Handoff

Use the canonical launch bundle under `demos/cross-platform-dashboard/artifacts`.

## Source Files

- SwiftUI: `demos/cross-platform-dashboard/artifacts/swiftui/ViewSpecView.swift`
- Flutter: `demos/cross-platform-dashboard/artifacts/flutter/viewspec_view.dart`
- Prompt: `demos/cross-platform-dashboard/artifacts/agent_prompt.txt`
- IntentBundle: `demos/cross-platform-dashboard/artifacts/intent_bundle.json`
- ASTBundle: `demos/cross-platform-dashboard/artifacts/ast_bundle.json`

## Expected Visual State

- Title: `Launch Operations Dashboard`.
- Status badge: `On track`.
- KPIs: `Emitter targets = 4`, `Docs pages = 8`, `Demo assets = 7`, `Blockers = 1`.
- Status rows: HTML/Tailwind, React TSX, SwiftUI, Flutter.
- Form controls: phase filter, owner email, include mobile handoff, save launch review.
- Changing phase filter to `Mobile` should reveal the mobile recording note.

## SwiftUI Recording

- Use a Mac with Xcode and a current iPhone simulator.
- Create a minimal SwiftUI app, add `ViewSpecView.swift`, and set the root view to `ViewSpecView()`.
- Record portrait first, then landscape if time allows.
- Capture 10-15 seconds: initial dashboard, phase filter changed to `Mobile`, and save action tapped if instrumented.

## Flutter Recording

- Use Android Studio or Flutter CLI with an Android emulator.
- Create a minimal app, copy `viewspec_view.dart` into `lib/`, and set `home: const ViewSpecView()`.
- Record portrait first, then landscape if time allows.
- Capture 10-15 seconds: initial dashboard, phase filter changed to `Mobile`, and save action tapped if instrumented.

## Delivery

- Preferred: MP4, 1080p or higher, no cursor overlays.
- Include simulator/emulator device name, OS/runtime version, and any wrapper code used.
- Do not edit the generated source unless a compile issue blocks recording; report any required patch separately.
