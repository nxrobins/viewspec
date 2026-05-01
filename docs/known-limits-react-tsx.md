# Known Limits: React TSX Emitter

- The generated file is a client component and expects the host app to provide routing or mutation behavior.
- Styling is Tailwind-class compatible with inline style objects for ViewSpec token values.
- Actions are surfaced through an `onAction` callback.
- The emitter does not create a full Next.js app; the launch demo mounts the generated component in a wrapper page.
