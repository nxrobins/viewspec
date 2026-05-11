# Known Limits: HTML/Tailwind Emitter

- `HtmlTailwindEmitter` is kept as the compatibility name, but output now uses offline CSS instead of the Tailwind CDN.
- Actions dispatch browser events; they do not perform network requests unless the host app listens and submits.
- Visibility rules support scalar equality, empty checks, and boolean checks.
- Complex widgets are represented by ViewSpec primitives, not arbitrary component libraries.

## Raw HTML Wedge

- Raw HTML compile is sanitize + theme + manifest + diff. It does not recover full ViewSpec IR.
- The sanitizer uses an allowlist of readable structural tags and safe attributes. Active surfaces such as scripts, inline handlers, forms, iframes, embeds, objects, templates, SVG/math wrappers, CSS URLs, and refresh metadata are removed.
- Remote images are replaced with inert external-image links and disclosed in `external_refs`; generated raw-HTML artifacts should not auto-fetch network resources when opened.
- User-clicked external anchors remain clickable and receive `rel="noopener noreferrer"`.
