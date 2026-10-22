<!--
Thanks for contributing. CONTRIBUTING.md describes the development setup and the checks below.
Do not use a pull request to disclose a vulnerability; follow SECURITY.md first.
-->

## Summary

<!-- What does this change, and why? Link the issue it resolves, for example "Fixes #123". -->

## Testing

<!-- The tests you added or ran. For a bug fix, name the test that fails without the change. -->

## Checklist

The checks CI runs, from the repository root (see "Running the checks" in CONTRIBUTING.md):

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `python -m compileall -q src tests examples scripts`
- [ ] `python -m pytest -q --strict-markers -m "not e2e"` (add `-rs` to see every skip and its reason)
- [ ] `python scripts/regenerate_schema.py check`
- [ ] `python scripts/update_corpus_goldens.py --check` (if compiled output changed on purpose, the regenerated `tests/fixtures/corpus_goldens.json` is committed and the description says why)
- [ ] `python scripts/check_pyright_baseline.py`
- [ ] `node tests/seo_static_smoke.mjs`

The repository rules:

- [ ] New behaviour comes with tests; a bug fix comes with a test that fails without the fix.
- [ ] A user-visible change has a bullet under `## [Unreleased]` in `CHANGELOG.md`.
- [ ] An error code this change starts or stops emitting is added to or removed from `src/viewspec/error_codes.py`.
- [ ] If a dependency range in `pyproject.toml` changed, `requirements-ci.txt` is regenerated with the command in its header and the description says the pins changed.
- [ ] No generated artifact (`dist/`, `react-output/`, generated React, lockfiles, manifests, proof reports) is edited by hand.
- [ ] If the change touches what the opt-in `e2e` suites cover, they were run, or the description says why not.
