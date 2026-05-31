# Interactive Compose Walkthrough

Interactive compose adds input bindings, declarative visibility rules, and action payload collection to ViewSpec.

The compiler owns:

- input primitives for text, toggle, and select controls
- rule metadata for scalar equality, emptiness, and boolean checks
- submit and navigate action nodes
- readonly payload values from compiled bindings and projections

The host app owns side effects. Generated artifacts dispatch or call action intents; they do not silently submit data to a remote service. Local HTML dispatches `viewspec-action` with `detail.schemaVersion: 1`, `source: "viewspec-html-tailwind"`, `id`, `kind`, `targetRef`, `payloadBindings`, and collected `payloadValues`. Pressing Enter inside an inert ViewSpec form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif.
