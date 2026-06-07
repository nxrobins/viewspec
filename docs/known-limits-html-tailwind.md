# Known Limits: HTML/Tailwind Emitter

- `HtmlTailwindEmitter` is kept as the compatibility name, but output now uses offline CSS instead of the Tailwind CDN.
- Actions dispatch `viewspec-action` browser events; they do not perform network requests unless the host app listens and submits. Event `detail` is versioned as `schemaVersion: 1` and includes `source`, `id`, `kind`, `targetRef`, `payloadBindings`, and `payloadValues`. Pressing Enter inside an inert ViewSpec form dispatches only a declared `submit` action whose `targetRef` exactly matches that form motif.
- Table and list motifs emit semantic `<table>`/`<tr>`/`<th>`/`<td>` and `<ul>`/`<li>` markup. Collection actions such as search, filter, sort, paginate, and bulk_action are event surfaces only; generated artifacts do not locally mutate or query collection data. Dashboard, outline, and comparison motifs emit portable primitive containers with provenance attributes rather than full ARIA-specific widgets.
- Loading and error state motifs emit the current rendered state with checked `role="status"`/`aria-busy="true"` or `role="alert"` semantics; they do not implement async transitions or retry loops.
- Visibility rules support scalar equality, empty checks, and boolean checks.
- Complex widgets are represented by ViewSpec primitives, not arbitrary component libraries.

## Raw HTML Wedge

- Raw HTML compile is sanitize + theme + manifest + diff. It does not recover full ViewSpec IR.
- The sanitizer uses an allowlist of readable structural tags and safe attributes. Active surfaces such as scripts, inline handlers, raw HTML forms, iframes, embeds, objects, templates, SVG/math wrappers, CSS URLs, and refresh metadata are removed. Intent `form` motifs are emitted as inert `role="form"` sections with local `viewspec-action` events, never submitting `<form>` elements.
- Remote images are replaced with inert external-image links and disclosed in `external_refs`; generated raw-HTML artifacts should not auto-fetch network resources when opened.
- User-clicked external anchors remain clickable and receive `rel="noopener noreferrer"`.
