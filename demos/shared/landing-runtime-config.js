// Runtime config for the landing page. Populated at deploy time.
//
// IMPORTANT: GitHub Pages serves this file as-is — anything you put here
// is publicly visible to anyone who views page source. Do not put secrets
// you wouldn't put on a billboard.
//
// publicApiKey: a viewspec-api key (Bearer token). Without it, every
//   landing visitor falls back to the anonymous free tier (500 calls/day,
//   shared across ALL anonymous traffic). With a key, the landing's hero +
//   provenance + style-derivation + motifs + proof compiles route to the
//   key's tier, decoupling the landing demo from public free-tier quota.
//
// To configure: replace YOUR_LANDING_API_KEY with the actual key string.
// Empty string and any value containing "REPLACE_WITH" or "YOUR_" are
// treated as unset and the landing falls back to anonymous (see
// hasPublicApiKey() in landing-config.js).
//
// Recommended: provision a key on viewspec-api scoped to a tier high
// enough to absorb expected landing traffic, and consider Origin-restricting
// it server-side to https://viewspec.dev so a scraped key can't be reused
// from elsewhere.
window.VIEWSPEC_LANDING_CONFIG = window.VIEWSPEC_LANDING_CONFIG || {}
window.VIEWSPEC_LANDING_CONFIG.publicApiKey = window.VIEWSPEC_LANDING_CONFIG.publicApiKey || 'YOUR_LANDING_API_KEY'
