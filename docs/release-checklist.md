# ViewSpec Release Checklist

Use this checklist before publishing a new ViewSpec package or public-site update.

## Public Facts

- Update `demos/public-facts.json` with the current SDK version, pricing, hosted-call limits, canonical API URL, package URL, proof-scope wording, proof identity metadata contract, host assertion requirement contract, and agent asset contract metadata.
- Confirm `src/viewspec/_version.py` (the only version source; `pyproject.toml` declares the version dynamic and reads it from that file), README, Getting Started, `demos/index.html`, `demos/llms.txt`, `demos/llms-full.txt`, and `demos/agent-assets.json` agree with `demos/public-facts.json`.
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

- Know what the merge deploys. **Deploy to GitHub Pages** runs on every push to `main` and
  deploys `demos/` even when its `published_studio` job fails: that job reports the PyPI check as
  a non-blocking status (audit R-2). Merging the release branch therefore publishes the
  new-version homepage at once, and until the package is on PyPI its
  `python -m pip install --pre viewspec` command installs the previous version. Keep that window
  short by uploading as soon as the merged commit's distribution artifacts exist. If the site
  must not run ahead of PyPI, disable the workflow before merging
  (`gh workflow disable "Deploy to GitHub Pages"`) and enable it again once the published check
  below passes.
- Publish to PyPI manually after the release branch is merged and the package artifacts are verified.
- Upload the exact artifacts retained by the successful SDK Reliability `distribution` job of the
  push-to-`main` run for the merged release commit: the wheel and sdist in one
  `studio-release-python-*` artifact. CI supersedes only pull request runs, so every merged commit
  gets its own complete run; do not use a pull request run, which builds a different commit.
  PyPI artifacts are immutable: a Studio-capable package must have a new version, never reuse
  `0.3.0b7`, which predates Studio.
- Tag `v<version>` on the merged release revision (`git tag -a v<version> <merged-sha>`, then
  `git push origin v<version>`) and create a GitHub Release for that tag pointing at the retained
  CI artifacts: the SDK Reliability distribution job's wheel and sdist plus its
  `studio-release-check.json` report. Every published version needs both the tag and the Release;
  the compare links at the bottom of `CHANGELOG.md` depend on the tags.
- Run `python scripts/check_studio_release.py --published --out studio-public-release-check.json`
  after upload. This executes the same install and launch flow against public PyPI and requires
  the version and agent assets advertised by this checkout. A package with missing Studio,
  stale version, failed browser entry, or broken React build fails this check. The site deploy
  does not wait for it, so a failure here means the homepage may already advertise that package:
  publish a fixed version.
- After the published check passes, dispatch **Deploy to GitHub Pages** on the merged release
  revision (enabling the workflow first if you disabled it). Its `published_studio` job runs the
  public check again; confirm that job passed, since a failure there does not stop the deploy.
  Only a passing check uploads the `studio-public-release-check` artifact. A failing check exits
  before it writes its report, so no artifact is uploaded: the failure shows as the job's failed
  status and as the `Studio release check failed: ...` line in the job log, while the workflow run
  itself still succeeds because the job is allowed to fail.
- Do not add automated PyPI upload in this checklist; release credentials and publish approval remain outside the SDK Reliability CI workflow.

## Launch Media in Git LFS

The six media files in `demos/launch-assets/` (two GIFs, two MP4s and two PNGs, 3.9 MiB) move to
Git LFS once (audit H-3), from a machine with git-lfs installed that can reach GitHub's LFS
endpoint. The migration is one new commit and rewrites no history: the blobs already in the
history stay there, and later re-encodes go to LFS storage instead of growing every clone.

- Check the quotas first. GitHub meters LFS storage and bandwidth against the repository owner's
  account, and pushes to forks count against this repository. The six files take 3.9 MiB of
  storage, and every re-encode adds its new size, because GitHub keeps each pushed version. Every
  run of the Pages `deploy` job downloads them again (3.9 MiB per push to `main`), as does every
  clone or checkout that fetches LFS content. Compare that with the storage and bandwidth the
  owner's plan includes, in the account's billing settings (Settings, Billing and licensing).
  When the bandwidth runs out, LFS downloads fail: the `deploy` checkout fails and the site stays
  on its previous version. GitHub's source archives (Download ZIP and the Release "Source code"
  files) hold pointer files unless "Include Git LFS objects in archives" is on, and then every
  archive download counts as bandwidth; the package does not need the media, so leave it off.
- Migrate from a clean, up-to-date checkout of `main`: `sh scripts/migrate_launch_media_to_lfs.sh`.
  It creates the branch `lfs/launch-media`, appends the LFS patterns to `.gitattributes`, commits
  the six files as LFS pointers, checks the commit, and prints the push steps. It never pushes,
  and running it again changes nothing.
- Push as printed (`git lfs push origin lfs/launch-media`, then
  `git push --set-upstream origin lfs/launch-media`) and run the printed fresh-clone check, which
  fails if GitHub lacks any of the objects. Then open and merge the pull request.
- Verify the site. Once **Deploy to GitHub Pages** has finished on the merge commit, run
  `sh scripts/migrate_launch_media_to_lfs.sh --verify-site`. It downloads each file from
  `https://viewspec.dev` with a cache-busting query string and compares its SHA-256 and size with
  the committed pointer. `POINTER` means the deploy checked out without `lfs: true`; `MISMATCH` or
  `FAILED` means the site serves something else, or nothing, at that path.
- Only the `deploy` job needs the files themselves, so only its checkout sets `lfs: true`. The SDK
  Reliability `test` job runs `tests/seo_static_smoke.mjs`, which reads the two JSON manifests
  (plain Git files) and checks that each media file is non-empty; a pointer passes that check, and
  Git LFS never turns an empty file into a pointer, so the check keeps its meaning without the
  download.
- Contributors need git-lfs from then on (CONTRIBUTING.md, development setup). A clone without it
  holds pointer files of about 130 bytes, which the test suite accepts, but a re-encoded asset
  committed without git-lfs goes back into the history as a plain blob.
- To undo after the merge, revert the migration commit. The plain blobs come back from history,
  so the repository does not grow.
