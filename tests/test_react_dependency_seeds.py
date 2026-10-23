"""Shipped builds, browser proof, and active evals share exact dependency bytes."""

import json
import re
from pathlib import Path

import yaml

from viewspec.app_freerange import FREERANGE_PACKAGE, FREERANGE_TYPESCRIPT_VERSION, FREERANGE_VERSION
from viewspec.app_pretext import PRETEXT_PACKAGE, PRETEXT_VERSION

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (
    "src/viewspec/host_verify_template",
    "tests/react-tailwind-host",
    "conformance/agent-ui-v2/react-dependencies",
)


def _seeds():
    return [
        (
            name,
            json.loads((ROOT / name / "package.json").read_text()),
            json.loads((ROOT / name / "package-lock.json").read_text()),
        )
        for name in SEEDS
    ]


def test_shared_dependency_versions_and_integrities_are_identical():
    observed = {}
    for seed, _, lock in _seeds():
        for name, package in lock["packages"].items():
            if not name:
                continue
            identity = tuple(package.get(key) for key in ("version", "resolved", "integrity"))
            if name in observed:
                assert observed[name][1] == identity, (name, observed[name][0], seed)
            else:
                observed[name] = (seed, identity)


def test_each_seed_matches_its_exact_direct_dependency_contract():
    seeds = _seeds()
    runtime = seeds[0][1]["dependencies"]
    for seed, manifest, lock in seeds:
        assert lock["lockfileVersion"] == 3
        for group in ("dependencies", "devDependencies"):
            assert manifest[group] == lock["packages"][""][group], seed
            for name, version in manifest[group].items():
                assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), (seed, name)
                assert lock["packages"]["node_modules/" + name]["version"] == version, (seed, name)
        assert all(manifest["dependencies"].get(name) == version for name, version in runtime.items()), seed


def test_dependency_security_floors_cannot_regress():
    # PostCSS includes the symlink-aware source-map boundary; Nano ID includes
    # both negative-size and zero-size termination fixes. npm audit remains the
    # independent, current advisory gate rather than this static version floor.
    # vite 8.3.5 replaced 8.0.16, which is in the range of HIGH advisories up to 8.1.5 (audit P-2).
    floors = {"postcss": (8, 5, 26), "nanoid": (3, 3, 18), "vite": (8, 3, 5)}
    for seed, _, lock in _seeds():
        for name, minimum in floors.items():
            matches = [package for path, package in lock["packages"].items() if path.endswith("node_modules/" + name)]
            assert matches, (seed, name)
            for package in matches:
                assert tuple(map(int, package["version"].split("."))) >= minimum, (seed, name)


def _shared_pin(name):
    """The one version every seed that declares ``name`` pins it at."""
    pins = {
        seed: manifest[group][name]
        for seed, manifest, _ in _seeds()
        for group in ("dependencies", "devDependencies")
        if name in manifest.get(group, {})
    }
    assert pins, name
    assert len(set(pins.values())) == 1, (name, pins)
    return next(iter(pins.values()))


def test_seeds_pin_identical_versions_and_overrides():
    # Dependabot updates the three seeds in one grouped pull request, and the generated React app,
    # the browser proof and the eval targets must keep building from the same bytes, so a package
    # two seeds declare has one exact version, and the npm overrides (the P-7 floors) are identical.
    seeds = _seeds()
    pins = {}
    for seed, manifest, _ in seeds:
        for group in ("dependencies", "devDependencies"):
            for name, version in manifest[group].items():
                pins.setdefault(name, {})[seed] = version
    shared = {name: by_seed for name, by_seed in pins.items() if len(by_seed) > 1}
    assert shared
    for name, by_seed in shared.items():
        assert len(set(by_seed.values())) == 1, (name, by_seed)

    overrides = {seed: manifest.get("overrides") for seed, manifest, _ in seeds}
    assert overrides[SEEDS[0]], "the template declares no npm overrides"
    assert all(value == overrides[SEEDS[0]] for value in overrides.values()), overrides


def test_playwright_pins_outside_the_seeds_follow_the_seeds():
    # Dependabot never bumps Playwright: these pins and the Chromium revision move with it by hand.
    version = _shared_pin("@playwright/test")
    for seed, _, lock in _seeds():
        assert lock["packages"]["node_modules/playwright"]["version"] == version, seed

    workflow = yaml.safe_load((ROOT / ".github/workflows/pretext-e2e.yml").read_text(encoding="utf-8"))
    environments = [workflow.get("env") or {}, *((job.get("env") or {}) for job in workflow["jobs"].values())]
    workflow_pins = [env["PLAYWRIGHT_VERSION"] for env in environments if "PLAYWRIGHT_VERSION" in env]
    assert workflow_pins == [version]

    for relative in ("scripts/check_studio_release.py", "docs/release-checklist.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        pins = re.findall(r"@playwright/test@([^\s\"'`]+)", text)
        assert pins, f"{relative} no longer pins @playwright/test"
        assert set(pins) == {version}, (relative, pins)


def test_hand_bumped_seed_pins_match_the_code_that_pins_them():
    # Dependabot never bumps these either: the code pins the exact artifacts the seeds install.
    assert _shared_pin("typescript") == FREERANGE_TYPESCRIPT_VERSION
    assert _shared_pin(PRETEXT_PACKAGE) == PRETEXT_VERSION
    assert _shared_pin(FREERANGE_PACKAGE) == FREERANGE_VERSION
