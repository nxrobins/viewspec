# Known limits — accessibility proof (slice 1)

ViewSpec `prove` checks a bounded, statically-decidable slice of accessibility and **fails closed**
on contrast. This documents exactly what is and is not proven, so reviewers can trust the boundary.

A browser proof adds two runtime checks at each viewport, in every target that has one: no duplicate
DOM ids, and an accessible name on every button, input, and image. They read the rendered DOM, so
they also cover a swapped MCP App root.

## Proven, fail-closed

- **Scoped WCAG AA contrast, measured from the artifact.** `prove` reads the compiled artifact and
  checks every text-bearing node: its ink against the background of its nearest ancestor that
  paints one, the node itself included. A translucent background is composited over the layer
  below it, down to the white canvas. For `html-tailwind` and `mcp-app` the colours are the
  document's own: its inline `style` values, which hold the aesthetic profile's values and the
  colours a DESIGN.md applied, and the rules of its `<style>` block, cascaded by origin,
  importance, specificity and order. For `react-tailwind-tsx` they are the classes each node is
  emitted with, resolved to the browser-grounded Tailwind v4 palette (rasterized by the same Blink
  engine the reference host renders in). Each node meets a size/role-scoped threshold: body text
  ≥ 4.5:1, large text (18pt, or 14pt bold, read from the font size and weight the artifact sets)
  and UI components (a button's label) ≥ 3.0:1. A node below its threshold fails the proof
  (`a11y_contrast`) with `A11Y_CONTRAST_BELOW_AA`, naming the node, both colours and the ratio.
- **A colour the proof cannot measure fails it.** The measurement reads hex colours, `rgb()` and
  `rgba()`, `transparent`, `white` and `black`, and the Tailwind classes in its palette. A
  `var()` with no value, any other colour syntax, a class outside the palette, a background image
  or gradient, an `opacity` below 1, a matching rule inside `@media` or another conditional group,
  or a selector it cannot evaluate leaves the node's contrast unresolved, and the proof fails
  with `A11Y_CONTRAST_UNRESOLVED`, naming the node and the value. DESIGN.md colours are validated
  `#RRGGBB` hex and the profiles and recipes are closed tables, so an unresolved colour points to a
  gap in those tables, not to a guess the proof should make.
- **A DESIGN.md is measured like any other colour.** Its colours reach the artifact as inline
  styles, so a design whose ink does not contrast with its surfaces fails the proof; a design of
  white on white is 1.0:1.
- **Accessible-name presence.** Interactive controls (input, button, image) must carry an
  author-provided accessible name — image `alt`/`label`, a button's visible `text`/`label`, and for
  inputs one of two compiler-derived label associations: the source node's `label` attribute
  (resolved into the input's `aria-label` automatically — form fields are named by their visible
  label text with zero extra authoring), or an unambiguous structural association (exactly one
  label and one input in the same field wrapper, rendered as `aria-labelledby` across all
  emitters). A binding id is never claimed as a name: the compiler no longer writes the fallback
  into the manifest, so an unlabeled input genuinely counts as *unnamed* and **fails** the proof
  (`a11y_names`). Detection is by composition-IR primitive, so it applies to both the html and
  React emitters.

## Explicitly out of scope (no fallback owed)

- **The `react-tsx` target's contrast.** Its colours are inline style objects the measurement does
  not read, so its proof reports `a11y_contrast: not_applicable`, never `passed`.
- **States other than the default render.** Rules for `:hover`, `:focus` and the other interaction
  states, and Tailwind's interaction variants, are not applied; the resting colours are measured.
- **Rendered pixels.** The measurement is static: it does not see anti-aliasing, text shadows,
  overlapping or positioned elements, or a background painted by an element that is not the text's
  ancestor. A font size it cannot read counts as body text, the strictest threshold.
- **Text on gradient / image / video backgrounds** — the compiler never emits them; an artifact
  that has one fails closed with `A11Y_CONTRAST_UNRESOLVED`.
- **Focus / tab order, ARIA-state truthfulness, keyboard operability, heading hierarchy, target
  size (WCAG 2.5.8), reduced-motion, live-region politeness, and screen-reader runtime behavior** —
  not statically decidable and/or later slices.
- **Wide-gamut / non-sRGB color** — DESIGN.md and the profiles are 6-digit sRGB hex; any other
  colour syntax in an artifact is unresolved and fails the proof.
- **Initially-hidden visibility targets (AppBundle V4)** — nodes carrying `data-visibility-rule`
  markers are still name- and contrast-checked even when baked `hidden`: a hidden node can become
  visible at runtime, so exempting it would under-check. Intentional, not an oversight.

Scoped contrast + name presence is a real, checked handle — not a full WCAG audit or screen-reader
certification.
