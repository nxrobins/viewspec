# ViewSpec Release Checklist

Use this checklist before publishing a new ViewSpec package or public-site update.

## Public Facts

- Update `demos/public-facts.json` with the current SDK version, package URL, and proof-scope wording. Run `python scripts/export_agent_contract.py` so the published agent contract matches the SDK.
- Confirm `src/viewspec/_version.py` (the only version source; `pyproject.toml` declares the version dynamic and reads it from that file), README, Getting Started, `demos/index.html`, `demos/llms.txt`, `demos/llms-full.txt`, and `demos/public-facts.json` agree.
- Move `CHANGELOG.md` `[Unreleased]` entries under the new version heading with the release date; note any deprecations explicitly.
- For a prerelease, confirm every current-SDK install surface uses `--pre` or an exact prerelease pin. Plain `pip install viewspec` resolves the stable channel and must not be presented as the current prerelease command.
- Re-read `docs/compatibility.md` and confirm every claim still holds for this release.
- Run the static smoke tests; `PUBLIC_FACTS_DRIFT` means a public surface still disagrees with the manifest.

## Package Build

- Run `python -m build`.
- Inspect the generated README/long-description source before upload so PyPI does not publish stale version or proof-scope claims.
- Run `python scripts/check_distribution.py dist`.
- Install Node.js 22 and the pinned Chromium runner:
  `npx --yes --package=@playwright/test@1.60.0 playwright install --with-deps chromium`
  (omit `--with-deps` on macOS).
- Run `python scripts/run_verification_corpus.py --install`; all public cases must compile and
  produce the required browser evidence roles.

## Manual Publish

- Know what the merge deploys. **Deploy to GitHub Pages** runs on every push to `main`. Merging the
  release branch publishes the new-version homepage at once, and until the package is on PyPI its
  `python -m pip install --pre viewspec` command installs the previous version. Keep that window
  short by uploading as soon as the merged commit's distribution artifacts exist.
- Publish to PyPI manually after the release branch is merged and the package artifacts are verified.
- Upload the exact artifacts retained by the successful SDK Reliability `distribution` job of the
  push-to-`main` run for the merged release commit: the wheel and sdist in one
  `release-python-*` artifact. CI supersedes only pull request runs, so every merged commit
  gets its own complete run; do not use a pull request run, which builds a different commit.
- Tag `v<version>` on the merged release revision (`git tag -a v<version> <merged-sha>`, then
  `git push origin v<version>`) and create a GitHub Release for that tag pointing at the retained
  CI artifacts. Every published version needs both the tag and the Release; the compare links at
  the bottom of `CHANGELOG.md` depend on the tags.
- Do not add automated PyPI upload in this checklist; release credentials and publish approval remain outside the SDK Reliability CI workflow.
