# Known Limits: HTML/Tailwind Emitter

- Output is standalone HTML with Tailwind CDN by default.
- Actions dispatch browser events; they do not perform network requests unless the host app listens and submits.
- Visibility rules support scalar equality, empty checks, and boolean checks.
- Complex widgets are represented by ViewSpec primitives, not arbitrary component libraries.
