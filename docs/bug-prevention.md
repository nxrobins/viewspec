# Historical bug prevention contract

ViewSpec treats every confirmed repository regression through pull request 135 as a closed prevention class. The canonical catalogue is [`tests/bug_prevention_manifest.json`](../tests/bug_prevention_manifest.json): each `VSBUG-*` entry names the failure, states the invariant that replaced the patch-level fix, links to the original fix, and points to executable evidence.

`tests/test_bug_prevention_manifest.py` makes the catalogue structural rather than ceremonial. CI fails if an ID disappears, a referenced test or browser assertion is renamed, a high-variance parser or reducer class loses property-based coverage, a browser-observable class loses real-browser coverage, or a mandatory prevention gate becomes optional.

## Adding a newly discovered bug

1. Reproduce it with a failing example, property, or browser test before changing production behavior.
2. Fix the narrow behavior, then state the broader invariant that makes the whole failure class invalid.
3. Add the next sequential `VSBUG-*` entry and its fixing PR or commit. If the failure has unbounded inputs, prefer a bounded Hypothesis strategy; if it depends on browser semantics, require a Playwright guard.
4. Keep failures explicit. Rejected inputs must retain stable error codes or HTTP/CLI failure status, and multi-step state changes must remain atomic.

## Mandatory gates

- Python tests, including the prevention ledger and Hypothesis properties, run on Python 3.11 through 3.14.
- Ruff, byte-compilation of `src`, `tests`, `examples`, and `scripts`, and whitespace-damage checks fail the build.
- Static landing, configuration, commerce, checkout, and SEO contracts run under Node.js.
- Chromium executes the generated React host proof and every public demo at desktop and mobile widths, including clipboard rejection and checkout-key copy fallback.
- A clean PEP 517 build must produce exactly one wheel and one source archive. `scripts/check_distribution.py` rejects version drift, traversal or link entries, repository/dependency bloat, unexpected files, more than 512 members, compressed archives over 4 MiB, expanded archives over 8 MiB, or any member over 2 MiB.
