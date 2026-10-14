# ViewSpec Release Checklist

Use this checklist before publishing a new ViewSpec package or public-site update.

## Public Facts

- Update `demos/public-facts.json` with the current SDK version, pricing, hosted-call limits, canonical API URL, package URL, proof-scope wording, proof identity metadata contract, host assertion requirement contract, and agent asset contract metadata.
- Confirm `pyproject.toml`, `src/viewspec/_version.py`, README, Getting Started, `demos/index.html`, `demos/llms.txt`, `demos/llms-full.txt`, and `demos/agent-assets.json` agree with `demos/public-facts.json`.
- Regenerate `demos/openapi.json` from the running `viewspec-api` contract. Do not hand-edit it; the API deployment gate compares the complete document byte-for-structure.
- Refresh the hosted `compiler/intent_contract.py` mirror from
  `src/viewspec/intent_contract.py`; the deployment gate requires byte-for-byte identity.
- Move `CHANGELOG.md` `[Unreleased]` entries under the new version heading with the release date; note any deprecations explicitly.
- For a prerelease, confirm every current-SDK install surface uses `--pre` or an exact prerelease pin. Plain `pip install viewspec` resolves the stable channel and must not be presented as the current prerelease command.
- Re-read `docs/compatibility.md` and confirm every claim still holds for this release: covered surfaces unchanged or extended additively, caps only rose, error codes add-only, environment floors accurate.
- Run the static smoke tests; `PUBLIC_FACTS_DRIFT` means a public surface still disagrees with the manifest.

## Package Build

- Run `python -m build` and `python -m pip wheel . --no-deps`.
- Inspect the generated README/long-description source before upload so PyPI does not publish stale pricing, version, proof-scope, or hosted-call claims.
- Confirm the package exposes `viewspec prove`, `viewspec check`, `viewspec verify-host`, and
  `viewspec verify`, including documented local no-network defaults and status-specific exits.
- Run `python scripts/check_distribution.py dist`; both archives must include the Studio
  CLI, creation/review daemons, and locked React build resources.
- Install Node.js 22 and the pinned Chromium runner:
  `npx --yes --package=@playwright/test@1.60.0 playwright install --with-deps chromium`
  (omit `--with-deps` on macOS).
- Run `python scripts/check_studio_release.py --dist dist --out studio-release-check.json`.
  This creates a fresh virtual environment outside the checkout, exposes the candidate wheel
  and its downloaded dependencies through a private wheelhouse, and executes the unchanged
  homepage `python -m pip install --pre viewspec` command. It checks the installed version
  and agent assets, opens the exact brief/reference creation command, submits a controlled
  candidate, and follows its checked handoff. It then runs the exact comparison command with
  a cold dependency install and proves both targets render and handshake in Chromium.
  The browser-open request enters the headless check, including Studio's required browser
  handshake during comparison launch; no launch flags are substituted.
  Port 4388 must be free. No development extras, editable install, checkout import, or dependency
  seed may satisfy the gate. SDK Reliability runs this gate on Python 3.11 and 3.14 and retains
  the tested package artifacts and redacted report.
- Run `python scripts/run_verification_corpus.py --install`; all public cases must compile and
  produce the required browser evidence roles.

## Manual Publish

- Publish to PyPI manually after the release branch is merged and the package artifacts are verified.
- Upload the exact artifacts retained by the successful SDK Reliability distribution job.
  PyPI artifacts are immutable: a Studio-capable package must have a new version, never reuse
  `0.3.0b7`, which predates Studio.
- Run `python scripts/check_studio_release.py --published --out studio-public-release-check.json`
  after upload. This executes the same install and launch flow against public PyPI and requires
  the version and agent assets advertised by this checkout. A package with missing Studio,
  stale version, failed browser entry, or broken React build blocks website deployment.
- After the published check passes, dispatch **Deploy to GitHub Pages** on the merged release
  revision. Its `published_studio` job runs the public check again before deploying. A new-version
  site update may initially fail this gate while manual publication is pending; the previous
  site remains deployed until the public package is ready.
- Do not add automated PyPI upload in this checklist; release credentials and publish approval remain outside the SDK Reliability CI workflow.
