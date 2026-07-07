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
  engine the host renders in) and are checked against the same scoped thresholds; the base recipe
  and all eight profiles clear AA and fail closed otherwise.
- **Accessible-name presence.** Interactive controls (input, button, image) must carry an
  author-provided accessible name — input `aria_label`, image `alt`/`label`, or a button's visible
  `text`/`label`. A name that would come only from the emitter's generic fallback (e.g. an input's
  binding id) counts as *unnamed* and **fails** the proof (`a11y_names`). Detection is by
  composition-IR primitive, so it applies to both the html and React emitters. (Follow-up: the
  emitter will associate a form field's visible label with its input, so a labeled input needs no
  separate `aria_label`.)

## Explicitly out of scope (no fallback owed)

- **Text on gradient / image / video backgrounds** — the compiler never emits text on them; an
  unresolvable background fails loudly (`A11Y_UNRESOLVABLE_BACKGROUND`), never silently.
- **Focus / tab order, ARIA-state truthfulness, keyboard operability, heading hierarchy, target
  size (WCAG 2.5.8), reduced-motion, live-region politeness, and screen-reader runtime behavior** —
  not statically decidable and/or later slices.
- **Wide-gamut / non-sRGB color** — the registry is 6-digit sRGB hex; anything else is rejected
  upstream by the style validator.

Scoped contrast + name presence is a real, checked handle — not a full WCAG audit or screen-reader
certification.
