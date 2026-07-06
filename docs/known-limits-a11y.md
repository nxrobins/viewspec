# Known limits — accessibility proof (slice 1)

ViewSpec `prove` checks a bounded, statically-decidable slice of accessibility and **fails closed**
on contrast. This documents exactly what is and is not proven, so reviewers can trust the boundary.

## Proven, fail-closed

- **Scoped WCAG AA contrast.** Every governed text/background pair — enumerated from the closed
  aesthetic style vocabulary plus the emitter's fixed base-CSS pairs — meets a size/role-scoped
  threshold: body text ≥ 4.5:1, large text and UI components ≥ 3.0:1. Because agents may only use
  governed tokens, that enumeration provably covers every rendered node. Any pair below its
  threshold fails the proof (`a11y_contrast`), naming the offending pair.

## Reported, warn-only (this release)

- **Accessible-name presence.** Interactive controls (inputs, buttons, image slots) are checked for
  an author-provided accessible name; a name that would come only from the emitter's generic
  fallback ladder counts as *unnamed* and is reported (`a11y_names`) but does not yet fail the proof
  (existing bundles lean on fallback names). It flips to fail-closed once starters and demos carry
  explicit names.

## Explicitly out of scope (no fallback owed)

- **React-emitter contrast** — the React Tailwind recipes are a different color system (Tailwind
  palette, not the profile hex); React output is checked for accessible-name presence, but **its
  contrast is not yet proven**. Named next slice.
- **Text on gradient / image / video backgrounds** — the compiler never emits text on them; an
  unresolvable background fails loudly (`A11Y_UNRESOLVABLE_BACKGROUND`), never silently.
- **Focus / tab order, ARIA-state truthfulness, keyboard operability, heading hierarchy, target
  size (WCAG 2.5.8), reduced-motion, live-region politeness, and screen-reader runtime behavior** —
  not statically decidable and/or later slices.
- **Wide-gamut / non-sRGB color** — the registry is 6-digit sRGB hex; anything else is rejected
  upstream by the style validator.

Scoped contrast + name presence is a real, checked handle — not a full WCAG audit or screen-reader
certification.
