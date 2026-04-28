# Free SDK Reliability Audit

## Supported Surface

The free SDK is the local Python package under `src/viewspec`. Its supported reliability surface is:

- fluent bundle construction through `ViewSpecBuilder`
- JSON and protobuf round trips for `IntentBundle` and `ASTBundle`
- deterministic local compilation for `table`, `dashboard`, `outline`, and `comparison`
- HTML/Tailwind emission with provenance and diagnostics artifacts
- mocked hosted fallback client behavior through `compile_remote()` and `compile_auto()`
- landing-page payload compatibility with `IntentBundle.from_json()`

The canonical hosted compiler domain is `https://api.viewspec.dev`. Fly deployment URLs are internal implementation details and should not be used in SDK defaults or public docs.

The static landing page keeps a deployment fallback endpoint so the live demo does not collapse to a static sample while the custom API domain is being cut over. The canonical SDK contract remains `https://api.viewspec.dev`.

## Reliability Guarantees

- Local compilation performs no network calls and no LLM calls.
- Unsupported motif kinds raise `UnsupportedMotifError` so `compile_auto()` can fall back to the hosted compiler.
- Fatal root-shape failures raise `CompilerInputError`.
- Recoverable malformed input returns compiler diagnostics while preserving all valid output.
- For duplicate IDs or duplicate `exactly_once` addresses, the first valid occurrence wins.
- Valid binding IR nodes carry content provenance; all emitted IR nodes carry intent provenance.
- Remote compiler behavior is tested with mocks only; the SDK test suite performs no live network calls.

## Release Gate

The SDK reliability gate is the `SDK Reliability` GitHub Actions workflow. It runs:

```bash
python -m pip install ".[dev,remote]"
ruff check .
python -m compileall src tests examples
python -m pytest -q
node tests/landing_payload_smoke.mjs
python -m pip wheel . --no-deps
```

The workflow sets up Node.js explicitly with `actions/setup-node@v4` before the landing payload smoke test.

## Deferred Gate

Generated-demo tracked-diff checks are intentionally not part of CI yet. They should only be added after demo build scripts expose a deterministic mode that strips timestamps, durations, and unstable ordering.
