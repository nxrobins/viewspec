<!--
Thanks for contributing. CONTRIBUTING.md describes the development setup and the checks below.
Do not use a pull request to disclose a vulnerability; follow SECURITY.md first.
-->

## Summary

<!-- What does this change, and why? Link the issue it resolves, for example "Fixes #123". -->

## Testing

<!-- The tests you added or ran. For a bug fix, name the test that fails without the change. -->

## Checklist

Run these from the repository root; CI runs the same commands (see "Running the checks" in CONTRIBUTING.md):

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `python -m compileall -q src tests examples scripts`
- [ ] `python -m pytest -q --strict-markers -m "not e2e"` (add `-rs` to see every skip and its reason)
- [ ] `python scripts/regenerate_schema.py check`
- [ ] `python scripts/update_corpus_goldens.py --check` (if compiled output changed on purpose, the regenerated `tests/fixtures/corpus_goldens.json` is committed and the description says why)
- [ ] `python scripts/check_pyright_baseline.py`
- [ ] `node tests/landing_payload_smoke.mjs`
- [ ] `node tests/landing_config_smoke.mjs`
- [ ] `node tests/landing_compile_smoke.mjs`
- [ ] `node tests/commercial_contract_smoke.mjs`
- [ ] `node tests/checkout_claim_smoke.mjs`
- [ ] `node tests/seo_static_smoke.mjs`
- [ ] `git diff --check $(git hash-object -t tree /dev/null) HEAD` (after committing: it checks the committed files only)

When they apply (also under "Running the checks"):

- [ ] If a dependency range in `pyproject.toml` changed, `requirements-ci.txt` is regenerated with the command in its header and the description says the pins changed.
- [ ] If the change touches what the opt-in `e2e` suites cover, they were run, or the description says why not.

The repository rules (see "Repository rules" in CONTRIBUTING.md):

- [ ] **The error-code registry is closed.** An error code this change starts or stops emitting is added to or removed from `src/viewspec/error_codes.py`.
- [ ] **Every user-visible change gets a changelog entry**, a bullet under `## [Unreleased]` in `CHANGELOG.md`.
- [ ] **Generated artifacts are compiler output.** Nothing in `dist/`, `react-output/`, generated React, lockfiles, manifests or proof reports is edited by hand.
- [ ] **Keep pull requests focused.** Reformatting and renames are not mixed into a behaviour change.
- [ ] **New behaviour comes with tests.** A bug fix comes with a test that fails without the fix.
- [ ] **Documentation you touch must stay true.** The pages this changes still match the code, and the test suite ran after the edit, because tests pin wording in several of them.
- [ ] **Do not add network calls, telemetry, or automatic installs to the local SDK.** This change adds none; `SECURITY.md` describes the no-network posture.
