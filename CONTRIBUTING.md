# Contributing to ViewSpec

Thanks for helping. This page covers the development setup the test suite expects, how to run the
checks CI runs, and the two repository rules that trip up most first contributions: the closed
error-code registry and the changelog. Security reports go through
[`SECURITY.md`](SECURITY.md), not issues or pull requests. All participation is covered by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Supported platforms

Development is supported on Linux and macOS. CI runs the test suite on Linux (Ubuntu), plus a
Windows job that compiles the conformance corpus and checks it against the pinned goldens; macOS is
supported but not exercised in CI. Review, Converge Sessions, IntentPatch apply, and host
verification use POSIX file locks and process groups (`fcntl`, `os.setsid`), so the full test suite
does not run on Windows.

## Development setup

1. **Clone with full history.** `tests/test_bug_prevention_manifest.py` walks the git history to
   verify the fix commits recorded in `tests/bug_prevention_manifest.json`. In a shallow clone, or
   a copy without `.git`, that check skips and prints the reason; CI checks out with
   `fetch-depth: 0`, so it always runs there. Use a plain `git clone`; if you already have a
   shallow clone, run `git fetch --unshallow`.

2. **Python 3.11 or newer.** Create a virtual environment and install the package editable with the
   development, hosted-fallback, and MCP extras:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev,remote,agents]"
   ```

   `dev` brings `pytest`, `hypothesis`, `ruff` and `pyright` (pinned to the exact versions CI
   uses), `build`, `httpx`, `jsonschema`, and `grpcio-tools` for schema regeneration; `remote` adds
   the hosted-compiler client dependencies; `agents` adds the MCP SDK (1.x; `mcp<2`) so the real
   `viewspec mcp` server starts in the tests instead of its missing-dependency branch.

3. **Node.js 22 and npm.** Two npm dependency seeds are committed as lockfiles and installed with
   `npm ci --ignore-scripts`; several tests and the host verifier look for them in place:

   ```bash
   (cd conformance/agent-ui-v2/react-dependencies && npm ci --ignore-scripts)
   (cd src/viewspec/host_verify_template && npm ci --ignore-scripts)
   ```

   Both `node_modules/` directories are gitignored. Without the seeds, the tests that need them skip
   and print the reason.

4. **Playwright Chromium (one test).** The browser scorer regression test
   (`tests/test_agent_eval_runner.py::test_browser_scorer_records_runtime_screenshot_and_text_geometry_fault`)
   needs the Chromium that the shipped template's Playwright version expects:

   ```bash
   (cd src/viewspec/host_verify_template && npx --no-install playwright install --with-deps chromium)
   ```

   Omit `--with-deps` on macOS. Without that browser the test skips with its reason; everything
   else in the default suite runs without a browser.

   CI installs the seeds and the browser and checks out full history, so nothing in the default
   suite skips for these reasons there. A local run with these skips is not full coverage; add
   `-rs` to the pytest command to list every skip and its reason.

5. **Optional runtimes.** Bun 1.x is only needed for the opt-in Freerange proof and its e2e
   scenarios; ViewSpec never installs it. Nothing in the default suite needs network access.

6. **Git LFS for the launch media.** Once the media in `demos/launch-assets/` move to Git LFS
   (the "Launch Media in Git LFS" section of `docs/release-checklist.md`), install git-lfs and run
   `git lfs install` once per machine. Clones made after that fetch the media; in an older clone,
   run `git lfs pull`. Without git-lfs those six files are pointer files of about 130 bytes: the
   default suite still passes, because the migration tests in
   `tests/test_migrate_launch_media_to_lfs.py` skip (add `-rs` to see why), but adding or
   re-encoding launch media needs git-lfs, or the commit puts the raw file back into the history.

## Running the checks

Run these from the repository root before opening a pull request; CI runs the same commands, the
test suite on Python 3.11 through 3.14.

```bash
ruff check .
ruff format --check .
python -m compileall -q src tests examples scripts
python -m pytest -q --strict-markers -m "not e2e"
python scripts/regenerate_schema.py check
python scripts/update_corpus_goldens.py --check
python scripts/check_pyright_baseline.py
node tests/seo_static_smoke.mjs
```

`update_corpus_goldens.py --check` fails when compiled output changes; if the change is
intended, run it without `--check` and commit the regenerated `tests/fixtures/corpus_goldens.json`.
`check_pyright_baseline.py` fails when the pyright error count grows above `pyright-baseline.json`.

`ruff format` is the only formatter; run `ruff format .` before committing. The lint rule set
(`I`, `UP`, `B`, `SIM` on top of Pyflakes) and the few deliberate ignores live under
`[tool.ruff.lint]` in `pyproject.toml`.
Markers are strict, so a new marker must be declared in `pyproject.toml`.

### Opt-in end-to-end suites

The `e2e` marker is excluded by default because those tests perform fresh registry-backed installs
and launch real Bun and Chromium runtimes. Opt in explicitly:

```bash
VIEWSPEC_RUN_PRETEXT_E2E=1 python -m pytest -q --strict-markers -m e2e tests/test_app_pretext_e2e.py
VIEWSPEC_RUN_AGENT_UI_V2_PRODUCT_E2E=1 python -m pytest -q --strict-markers -m e2e tests/test_agent_eval_runner.py
```

Set `VIEWSPEC_PRETEXT_E2E_ARTIFACT_DIR` to a fresh directory to retain the generated apps, proof
reports, and support bundles. The weekly `pretext-e2e.yml` workflow runs both with every toolchain
version pinned (npm to its major.minor line).

### Pinned CI dependencies

CI resolves Python dependencies through the pinned `requirements-ci.txt` at the repository root,
passed to pip as a constraints file, so every matrix leg installs identical versions. If you change
a dependency range in `pyproject.toml`, regenerate the pins in the same pull request with the exact
command from the file's header:

```bash
uv pip compile pyproject.toml --all-extras --universal --python-version 3.11 \
    --no-header -o requirements-ci.txt
```

`--universal` keeps the platform-specific pins the Windows job needs, and `--python-version 3.11`
resolves for the oldest supported Python rather than your interpreter. uv rewrites the whole file,
so copy the explanatory comment block back to the top afterwards (`--no-header` only stops uv from
adding its own). Say in the pull request description that the pins changed. Local development is
not required to use the constraints file, but reproducing a CI failure usually means installing
with it:

```bash
PIP_CONSTRAINT=requirements-ci.txt python -m pip install -e ".[dev,remote,agents]"
```

## Repository rules

### The error-code registry is closed

Every `UPPER_SNAKE` code that validation, compilation, `prove`, `diff`, the shell, host
verification, MCP, or motif-plugin tooling can emit in an error, warning, or diagnostic is listed
exactly once in `src/viewspec/error_codes.py`. `tests/test_error_code_registry.py` enforces both
directions: a code used anywhere in `src/viewspec` must be registered, and a registered code must
still be emitted. When you add a code, add it to the registry in the same change; when you stop
emitting one, remove it from the registry. Codes are part of the `local_v1` compatibility contract
(see `docs/compatibility.md`): they are never repurposed, and after 1.0 they are never removed
within a major version. Documentation that names a code is checked too:
`tests/test_docs_error_codes.py` verifies that every registry-looking code cited in `README.md`
and `docs/*.md` exists, with a small explicit allowlist for documented non-registry identifiers.

### Every user-visible change gets a changelog entry

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Add a bullet
under `## [Unreleased]` in the matching `Added`, `Changed`, `Fixed`, `Deprecated`, `Removed`, or
`Security` subsection as part of the pull request that makes the change; describe the behaviour a
user sees, not the implementation. At release time the maintainer moves the `[Unreleased]` entries
under a dated version heading, tags `v<version>`, and adds the compare link at the bottom of the
file (`docs/release-checklist.md` has the full sequence). Do not edit entries for versions that
have already shipped except to fix factual errors.

### Other expectations

- Generated artifacts are compiler output. Never hand-edit `dist/`, `react-output/`, generated
  React, lockfiles, manifests, or proof reports to make a check pass; fix the semantic source or the
  compiler and regenerate.
- Keep pull requests focused. Reformatting, renames, and behaviour changes are easier to review in
  separate changes.
- New behaviour comes with tests. Prefer a bounded Hypothesis strategy for parser or reducer input
  spaces and a Playwright guard for browser-observable behaviour, following the patterns already in
  `tests/`.
- Documentation you touch must stay true. Several tests pin wording in `README.md`,
  `docs/getting-started.md`, `docs/agent-integration.md`, and `integrations/claude-code/SKILL.md`;
  run the suite after editing them.
- Do not add network calls, telemetry, or automatic installs to the local SDK. The no-network
  posture described in `SECURITY.md` is part of the product.
