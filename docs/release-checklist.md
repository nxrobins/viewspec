# ViewSpec Release Checklist

Use this checklist before publishing a new ViewSpec package or public-site update.

## Public Facts

- Update `demos/public-facts.json` with the current SDK version, pricing, hosted-call limits, canonical API URL, package URL, proof-scope wording, proof identity metadata contract, host assertion requirement contract, and agent asset contract metadata.
- Confirm `pyproject.toml`, `src/viewspec/_version.py`, README, Getting Started, `demos/index.html`, `demos/llms.txt`, `demos/llms-full.txt`, `demos/agent-assets.json`, and `demos/openapi.json` agree with `demos/public-facts.json`.
- Run the static smoke tests; `PUBLIC_FACTS_DRIFT` means a public surface still disagrees with the manifest.

## Package Build

- Run `python -m build` and `python -m pip wheel . --no-deps`.
- Inspect the generated README/long-description source before upload so PyPI does not publish stale pricing, version, proof-scope, or hosted-call claims.
- Confirm the package exposes `viewspec prove`, `viewspec check`, `viewspec verify-host`, and the documented local no-network defaults.

## Manual Publish

- Publish to PyPI manually after the release branch is merged and the package artifacts are verified.
- Do not add automated PyPI upload in this checklist; release credentials and publish approval remain outside the SDK Reliability CI workflow.
