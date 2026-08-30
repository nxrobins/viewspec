"""Shipped builds, browser proof, and active evals share exact dependency bytes."""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SEEDS = (
    "src/viewspec/host_verify_template",
    "tests/react-tailwind-host",
    "conformance/agent-ui-v2/react-dependencies",
)


def _seeds():
    return [(name, json.loads((ROOT / name / "package.json").read_text()),
             json.loads((ROOT / name / "package-lock.json").read_text())) for name in SEEDS]


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
    floors = {"postcss": (8, 5, 26), "nanoid": (3, 3, 18)}
    for seed, _, lock in _seeds():
        for name, minimum in floors.items():
            matches = [package for path, package in lock["packages"].items()
                       if path.endswith("node_modules/" + name)]
            assert matches, (seed, name)
            for package in matches:
                assert tuple(map(int, package["version"].split("."))) >= minimum, (seed, name)
