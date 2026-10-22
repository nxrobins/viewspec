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
version pinned.

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

## Dependency updates

Dependabot (`.github/dependabot.yml`) checks once a week and opens at most one grouped version-update
pull request per ecosystem: `pip` (`pyproject.toml` and `requirements-ci.txt`), `npm` (the three
React dependency seeds `src/viewspec/host_verify_template`, `tests/react-tailwind-host` and
`conformance/agent-ui-v2/react-dependencies`, all in the same pull request), and GitHub Actions.
A grouped pull request carries only minor and patch updates. Dependabot never proposes a
semver-major update, and never proposes the packages whose exact version is a contract in the code,
the tests or the workflows; a maintainer bumps those by hand (see below).

Dependabot cannot run the repository's generators, so its pull requests usually need one follow-up
commit, pushed to the Dependabot branch, before CI passes. Once that commit is there Dependabot
stops rebasing the pull request, so rebase it yourself if `main` moves; `@dependabot recreate`
discards the commit, so redo it after a recreate. When one package in a group breaks the build and
cannot be fixed in the pull request, comment `@dependabot ignore <package> minor version` (or
`patch version`): Dependabot closes the pull request and the next weekly run groups the remaining
updates without that version.

### Landing the grouped npm pull request

The React target copies `src/viewspec/host_verify_template/package.json` and its lockfile into every
generated app, and `tests/fixtures/corpus_goldens.json` pins their hashes, so every npm update
changes the goldens. Check out the Dependabot branch, then:

```bash
for seed in src/viewspec/host_verify_template tests/react-tailwind-host conformance/agent-ui-v2/react-dependencies; do
    (cd "$seed" && npm ci --ignore-scripts && npm audit --package-lock-only --audit-level=high)
done
python scripts/update_corpus_goldens.py
python -m pytest -q tests/test_react_dependency_seeds.py tests/test_agent_eval_runner.py tests/test_corpus_goldens.py
```

Commit the regenerated `tests/fixtures/corpus_goldens.json` to the pull request. Nothing else
hard-codes the shared versions: the eval runner's pinned target contract reads the template's
`package.json`. `tests/test_react_dependency_seeds.py` checks that the seeds still agree: one version
for every package two seeds declare, identical `overrides`, and identical lockfile entries
(version, resolved URL and integrity) for every package the lockfiles share. If a seed resolved a
shared transitive package differently, run `npm update <package> --ignore-scripts` in the seed with
the older version and rerun the test. Then run the full checks above.

### Landing the grouped pip pull request

Dependabot moves each exact pin to its latest minor or patch release: every pin in
`requirements-ci.txt` and the `hatchling` build requirement in `pyproject.toml`. It leaves the
ranges in `pyproject.toml` alone while they allow the new release
(`versioning-strategy: increase-if-necessary`): they declare what the published package supports,
and the tests and the `protobuf_floor` CI job pin their floors, so raising a floor is a separate
change with its own changelog entry. Dependabot does not rerun the `uv pip compile` resolution
either. Install with the constraints, which fails when the new set conflicts:

```bash
PIP_CONSTRAINT=requirements-ci.txt python -m pip install -e ".[dev,remote,agents]"
```

On a conflict, regenerate the file with the command from its header plus
`--upgrade-package <name>` for each package the pull request updates, copy the header back, and
push the result. Then run the full checks; `pretext-e2e.yml` also runs, because the pins changed.

### Landing the GitHub Actions pull request

Every action is pinned to a commit with a `# vN` comment naming its major version
(`tests/test_ci_config.py` enforces that form). When Dependabot moves a pin within a major it can
rewrite the comment to the full release, for example `# v4.5.0`; change it back to `# v4` in every
workflow before merging.

### Packages Dependabot never proposes

Bump these by hand, each in its own pull request. Search the whole repository for the old version
string as well (`git grep -nF <old version>`): error messages, tests and root files such as
`.pre-commit-config.yaml` can quote it. Leave dated records, such as the audit, as they are.

- **`ruff`**: change the pin in the `dev` extra of `pyproject.toml`, in `requirements-ci.txt`, and
  the `rev` of the ruff hooks in `.pre-commit-config.yaml` (`v<version>`); `tests/test_ci_config.py`
  keeps the three equal. Reinstall, run `ruff format .` and `ruff check .`, and commit the
  reformatting and lint fixes with the bump.
- **`pyright`**: change both pins the same way and run `python scripts/check_pyright_baseline.py`.
  If the count dropped, ratchet the baseline down with `--update`; if it grew, fix the new errors,
  or record the new count with `--update` and say why in the pull request.
- **`grpcio-tools`**: change both pins (`tests/test_ci_config.py` also requires wheels for every
  supported Python), reinstall, and run `python scripts/regenerate_schema.py generate`. It has to
  report `viewspec_pb2.py` and `viewspec_pb2.pyi` unchanged. If it warns that the protoc version
  differs, the new protoc changed the generated module. Either keep the old pin (and restore the
  module with `git checkout src/viewspec/schema`), or keep the new module on purpose: move the
  `protobuf` floor in `pyproject.toml` to the new gencode version (`tests/test_schema_source.py`
  checks it) and the `protobuf==` pin of the `protobuf_floor` job in `.github/workflows/ci.yml`
  to the same version (`tests/test_ci_config.py` checks that), regenerate `requirements-ci.txt`,
  and add a changelog entry. Run `python scripts/regenerate_schema.py check` either way.
- **`mcp` 2.x**: the `viewspec mcp` server only works with MCP SDK 1.x, so the `agents` extra
  caps `mcp<2` and Dependabot proposes only 1.x releases. Moving to SDK 2 is a code change that
  raises the cap and moves the `mcp` rule in `dependabot.yml` to the new cap (`>=3` for `mcp<3`),
  or removes the rule if the cap goes (`tests/test_ci_config.py` ties the rule to the cap).
- **`@playwright/test`** (and the `playwright` runtime it pins): run
  `npm install --save-dev --save-exact --ignore-scripts @playwright/test@<version>` in each of the
  three seeds, then set the same version in `PLAYWRIGHT_VERSION` in
  `.github/workflows/pretext-e2e.yml`, in `scripts/check_studio_release.py` and in
  `docs/release-checklist.md` (`tests/test_react_dependency_seeds.py` checks all three). The new
  release usually expects a new Chromium build: reinstall it as in the development setup, refresh
  the goldens, and run the full suite; `pretext-e2e.yml` runs on the pull request.
- **`typescript`**: `FREERANGE_TYPESCRIPT_VERSION` in `src/viewspec/app_freerange.py` must equal
  the seeds' pin, together with the `_RESOLVED` URL and `_INTEGRITY` from the new lockfile entry
  and the `_TREE` hash of the installed package (the script below prints it). Update the seeds as
  for Playwright (`typescript@<version>`), refresh the goldens, and run the opt-in end-to-end
  suites.
- **`@chenglou/pretext`** and **`@chenglou/freerange`**: pinned only in
  `conformance/agent-ui-v2/react-dependencies`, and as exact artifacts in the code:
  `PRETEXT_VERSION`, `PRETEXT_NPM_RESOLVED`, `PRETEXT_NPM_INTEGRITY` and `PRETEXT_PACKAGE_TREE` in
  `src/viewspec/app_pretext.py` and the version in `src/viewspec/app_pretext_runtime.py`; the
  `FREERANGE_*` constants in `src/viewspec/app_freerange.py` and the lockfile entry
  `src/viewspec/app_react.py` writes. Install the new version in that seed with
  `npm install --save-exact --ignore-scripts <package>@<version>` (add `--save-dev` for
  Freerange), update the constants, refresh the goldens, and run both opt-in end-to-end suites.

This prints the package-tree hashes the code pins, for the versions installed in the conformance
seed:

```bash
python - <<'EOF'
from pathlib import Path
from viewspec import app_freerange, app_pretext
seed = Path("conformance/agent-ui-v2/react-dependencies/node_modules")
limit = app_freerange.FREERANGE_TOOL_TREE_MAX_BYTES
print("PRETEXT_PACKAGE_TREE", app_pretext._tree_hash(seed / "@chenglou/pretext"))
print("FREERANGE_PACKAGE_TREE", app_freerange._tree_hash(seed / "@chenglou/freerange", limit))
print("FREERANGE_TYPESCRIPT_TREE", app_freerange._tree_hash(seed / "typescript", limit))
EOF
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
