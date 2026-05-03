# HN Launch Demo Storyboard

The HN launch demo is a 45-60 second silent video with burned-in captions. It is optimized for skeptical technical viewers who want to see inspectable artifacts quickly, not a sales walkthrough.

## Output Specs

- Primary: `demos/launch-assets/hn-launch-demo.mp4`, 1920x1080 landscape, silent, 15 fps.
- Fallback: `demos/launch-assets/hn-launch-demo.gif`, 12 fps, tight palette, under 8 MB.
- Poster: `demos/launch-assets/hn-launch-demo-poster.png`.
- Manifest: `demos/launch-assets/hn-launch-demo-manifest.json`.
- Code/source shots must use 150% zoom or larger.
- SwiftUI and Flutter are shown as generated source ready for external simulator/emulator recording handoff.

## Storyboard

| Time | Shot | Caption | Capture |
| --- | --- | --- | --- |
| 0-6s | Headliner proof chain | One agent prompt. One IntentBundle. Four emitter artifacts. | `demos/launch-assets/headliner-prompt-four-outputs.png` |
| 6-14s | Prompt, IntentBundle, ASTBundle at 150% zoom | Intent, AST, diagnostics, and provenance are inspectable. | Generated code viewer from `demos/cross-platform-dashboard/artifacts` |
| 14-18s | Canonical cross-platform demo page | The public demo links every generated source and manifest. | `https://viewspec.dev/cross-platform-dashboard/` |
| 18-27s | Generated HTML/Tailwind dashboard | HTML/Tailwind is generated from the same ASTBundle. | `https://viewspec.dev/cross-platform-dashboard/artifacts/html/index.html` |
| 27-31s | React TSX source at 150% zoom | React TSX is a source artifact, not a hand-written rewrite. | `ViewSpecView.tsx` |
| 31-35s | SwiftUI source at 150% zoom | SwiftUI generated source is ready for recorder handoff. | `ViewSpecView.swift` |
| 35-39s | Flutter source at 150% zoom | Flutter generated source is ready for recorder handoff. | `viewspec_view.dart` |
| 39-47s | Interactive compose with phase set to Mobile | Inputs, rules, and submit payloads compile into deterministic events. | `https://viewspec.dev/interactive-compose/` |
| 47-55s | Live playground CTA | Try the compiler: viewspec.dev | `https://viewspec.dev/#playground` |

## Acceptance Criteria

- No player controls, cursor overlays, bottom progress bars, clipped captions, or unreadable code.
- The GIF fallback stays under 8 MB.
- The manifest records duration, output sizes, GIF settings, source captures, and code zoom.
- The demo must not claim SwiftUI or Flutter runtime recordings exist before external mobile recordings land.
- Smoke tests and media verification pass before commit or embed work.
