# Pretext Vendor Bundle

This directory vendors `@chenglou/pretext@0.0.6` for static demo pages.

Source: https://github.com/chenglou/pretext
License: MIT

The ESM bundle is intentionally kept local so GitHub Pages can serve the demos without a package install or bundler.
The `dist/*.js` files mirror the upstream `dist/*.mjs` files so local static servers that serve `.mjs` as `text/plain` can still load the module graph.
`pretext.global.js` is a local classic-script bundle generated from the same vendored files for static pages that need a deterministic `window.ViewSpecPretext` runtime without dynamic imports.
