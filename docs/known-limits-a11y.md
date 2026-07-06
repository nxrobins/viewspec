# Known limits — accessibility proof (slice 1)

ViewSpec `prove` checks a bounded, statically-decidable slice of accessibility and **fails closed**
on contrast. This documents exactly what is and is not proven, so reviewers can trust the boundary.

## Proven, fail-closed

- **Scoped WCAG AA contrast.** Every governed text/background pair — enumerated from the closed
  aesthetic style vocabulary plus the emitter's fixed base-CSS pairs — meets a size/role-scoped
  threshold: body text ≥ 4.5:1, large text and UI components ≥ 3.0:1. Because agents may only use
  governed tokens, that enumeration provably covers every rendered node. Any pair below its
  threshold fails the proof (`a11y_contrast`), naming the offending pair.
- **React Tailwind emitter contrast (slice 2).** The `react_tailwind_tsx` recipes + per-profile
  overlays resolve to the browser-grounded Tailwind v4 sRGB palette (rasterized by the same Blink
  engine the host renders in) and are checked against the same scoped thresholds; all eight profiles
  clear AA and fail closed otherwise.

## Reported, warn-only (this release)

- **Accessible-name presence.** Interactive controls (inputs, buttons, image slots) are checked for
  an author-provided accessible name; a name that would come only from the emitter's generic
  fallback ladder counts as *unnamed* and is reported (`a11y_names`) but does not yet fail the proof
  (existing bundles lean on fallback names). It flips to fail-closed once starters and demos carry
  explicit names.

## Explicitly out of scope (no fallback owed)

- **React base-recipe (no-profile) contrast** — the eight profile recipes are proven (above); a
  React artifact compiled without an aesthetic profile falls back to the base slate/teal recipe,
  whose contrast is not yet separately enumerated. Small follow-up.
- **Text on gradient / image / video backgrounds** — the compiler never emits text on them; an
  unresolvable background fails loudly (`A11Y_UNRESOLVABLE_BACKGROUND`), never silently.
- **Focus / tab order, ARIA-state truthfulness, keyboard operability, heading hierarchy, target
  size (WCAG 2.5.8), reduced-motion, live-region politeness, and screen-reader runtime behavior** —
  not statically decidable and/or later slices.
- **Wide-gamut / non-sRGB color** — the registry is 6-digit sRGB hex; anything else is rejected
  upstream by the style validator.

Scoped contrast + name presence is a real, checked handle — not a full WCAG audit or screen-reader
certification.
