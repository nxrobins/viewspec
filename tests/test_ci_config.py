"""Invariants of the CI workflows and pinned dev dependencies that a plain lint would not catch."""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_react_dependency_seeds import SEEDS

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<action>[\w.-]+/[\w./-]+)@(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*))?$")


def _load(name: str) -> dict[Any, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key ``on`` as the boolean True.
    return workflow.get("on", workflow.get(True))


@pytest.mark.parametrize("name", ["ci.yml", "pretext-e2e.yml"])
def test_only_pull_request_runs_are_superseded(name: str) -> None:
    concurrency = _load(name)["concurrency"]

    # A shared group with cancel-in-progress cancels a running push-to-main run, and even
    # without it a third queued run drops the pending one, so non-PR runs get a group of
    # their own (the run id) and only pull request runs are cancelled when superseded.
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}" in concurrency["group"]


def test_pretext_e2e_runs_when_its_install_constraints_change() -> None:
    text = (WORKFLOWS / "pretext-e2e.yml").read_text(encoding="utf-8")
    triggers = _triggers(yaml.safe_load(text))
    constraint_files = set(re.findall(r"pip install[^\n]*?-c\s+(\S+)", text))

    assert constraint_files == {"requirements-ci.txt"}
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        assert "pyproject.toml" in paths
        assert constraint_files <= set(paths), f"{event} path filter misses {constraint_files - set(paths)}"


def _action_pins() -> list[tuple[str, int, str, str, str | None]]:
    pins = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _USES.match(line)
            if match:
                pins.append((path.name, number, match["action"], match["ref"], match["comment"]))
    return pins


def test_every_action_is_pinned_to_a_commit_with_a_consistent_major_comment() -> None:
    pins = _action_pins()
    assert pins
    labels_by_commit: dict[tuple[str, str], set[str]] = defaultdict(set)
    commits_by_label: dict[tuple[str, str], set[str]] = defaultdict(set)
    for name, number, action, ref, comment in pins:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name}:{number} {action} is not pinned to a commit"
        assert comment is not None and re.fullmatch(r"v\d+", comment), f"{name}:{number} lacks a '# vN' comment"
        labels_by_commit[(action, ref)].add(comment)
        commits_by_label[(action, comment)].add(ref)

    # One commit carries one major label everywhere, and one label names one commit.
    assert all(len(labels) == 1 for labels in labels_by_commit.values()), dict(labels_by_commit)
    assert all(len(commits) == 1 for commits in commits_by_label.values()), dict(commits_by_label)


def test_pretext_e2e_keeps_upload_artifact_v5() -> None:
    # origin/main used actions/upload-artifact@v5; the pin must not fall back to the v4 line.
    pins = [pin for pin in _action_pins() if pin[0] == "pretext-e2e.yml" and pin[2] == "actions/upload-artifact"]

    assert [(ref, comment) for _n, _l, _a, ref, comment in pins] == [("330a01c490aca151604b8cf639adc76d48f6c5d4", "v5")]


# The first grpcio-tools release that publishes wheels for a CPython minor version. With an
# older pin, every dev install on that Python compiles grpcio-tools from source, which needs a
# C++ toolchain and takes minutes. Releases from 1.75.1 on publish cp311 through cp314 wheels.
_FIRST_GRPCIO_TOOLS_WITH_WHEELS = {"3.14": (1, 75, 1)}


def _exact_pins(requirements: list[str]) -> dict[str, str]:
    pins = {}
    for requirement in requirements:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*==\s*([0-9][0-9A-Za-z.]*)", requirement.split(";", 1)[0].strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_dev_extra_grpcio_tools_ships_wheels_for_every_declared_python() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {
        classifier.rsplit(" :: ", 1)[1]
        for classifier in project["classifiers"]
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", classifier)
    }
    pinned = _exact_pins(project["optional-dependencies"]["dev"])["grpcio-tools"]
    version = tuple(int(part) for part in pinned.split("."))

    assert "3.14" in declared
    for python, first in _FIRST_GRPCIO_TOOLS_WITH_WHEELS.items():
        if python in declared:
            assert version >= first, f"grpcio-tools=={pinned} ships no cp{python.replace('.', '')} wheel"


def test_exact_dev_pins_match_the_ci_constraints() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    constraints = _exact_pins(
        [line for line in (ROOT / "requirements-ci.txt").read_text(encoding="utf-8").splitlines() if "==" in line]
    )
    declared = {
        name: version
        for extra in project["optional-dependencies"].values()
        for name, version in _exact_pins(extra).items()
    }

    assert declared
    assert {name: constraints.get(name) for name in declared} == declared


def test_pre_commit_runs_the_ruff_release_ci_pins() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pinned = _exact_pins(project["optional-dependencies"]["dev"])["ruff"]
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    (ruff,) = [repo for repo in config["repos"] if repo["repo"] == "https://github.com/astral-sh/ruff-pre-commit"]

    # The hooks are the local copy of CI's `ruff check .` and `ruff format --check .` (audit P-8).
    # Another ruff release formats and lints differently, so code its hooks accept can fail CI.
    assert ruff["rev"] == f"v{pinned}"


def test_protobuf_floor_job_installs_the_declared_floor() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (requirement,) = [dependency for dependency in project["dependencies"] if re.match(r"protobuf\b", dependency)]
    floor = re.search(r">=\s*([0-9][0-9A-Za-z.]*)", requirement)
    commands = "\n".join(step.get("run", "") for step in _load("ci.yml")["jobs"]["protobuf_floor"]["steps"])

    # The job imports the package with the oldest protobuf it declares. A new protoc moves the floor
    # (tests/test_schema_source.py ties it to the gencode version), and the job's exact pin has to
    # move with it: a pin below the new floor makes the job's install unresolvable.
    assert floor is not None, requirement
    assert re.findall(r"protobuf==([0-9][0-9A-Za-z.]*)", commands) == [floor.group(1)]


# --- Dependabot -------------------------------------------------------------------------------

DEPENDABOT = ROOT / ".github" / "dependabot.yml"
_MAJOR = "version-update:semver-major"
# Packages whose exact version is a contract in code, tests, workflows or generated output.
# Dependabot never proposes them; CONTRIBUTING.md has the hand-bump steps for each.
_HAND_BUMPED = {
    "pip": {"grpcio-tools", "pyright", "ruff"},
    "npm": {"@chenglou/pretext", "@chenglou/freerange", "@playwright/test", "playwright", "typescript"},
}


def _dependabot_updates() -> dict[str, dict[str, Any]]:
    updates = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"]
    ecosystems = [update["package-ecosystem"] for update in updates]
    # One entry per ecosystem: separate entries cannot share a group, so they open separate PRs.
    assert len(ecosystems) == len(set(ecosystems)), ecosystems
    return {update["package-ecosystem"]: update for update in updates}


def _groups(update: dict[str, Any], applies_to: str) -> list[dict[str, Any]]:
    """The groups of a Dependabot entry for one kind of update; ``applies-to`` defaults to version updates."""
    return [group for group in update["groups"].values() if group.get("applies-to", "version-updates") == applies_to]


def test_dependabot_opens_one_grouped_minor_and_patch_pull_request_per_ecosystem() -> None:
    updates = _dependabot_updates()

    assert set(updates) == {"pip", "npm", "github-actions"}
    for ecosystem, update in updates.items():
        assert update["schedule"]["interval"] == "weekly", ecosystem
        # A grouped run opens one pull request; a small limit caps the damage if grouping ever
        # stops applying (the ungrouped configuration opened 24 at once).
        assert 1 <= update["open-pull-requests-limit"] <= 3, ecosystem
        # One group for version updates and at most one for security updates, nothing else.
        (group,) = _groups(update, "version-updates")
        security = _groups(update, "security-updates")
        assert len(security) <= 1, ecosystem
        assert len(update["groups"]) == 1 + len(security), ecosystem
        assert group["patterns"] == ["*"], ecosystem
        assert sorted(group["update-types"]) == ["minor", "patch"], ecosystem
        # group-by: dependency-name would split the group into one pull request per package.
        assert "group-by" not in group, ecosystem
        # Majors are never proposed as version updates for any package.
        assert {"dependency-name": "*", "update-types": [_MAJOR]} in update["ignore"], ecosystem
        # The update-types ignores do not apply to security updates, so a security update can be a
        # major. A security group takes every one of them: any filter (update-types, exclude-patterns,
        # dependency-type, group-by) would send the updates it leaves out to pull requests of their own.
        for security_group in security:
            assert security_group == {"applies-to": "security-updates", "patterns": ["*"]}, ecosystem


def test_dependabot_leaves_the_declared_python_ranges_alone() -> None:
    pip = _dependabot_updates()["pip"]

    # pyproject.toml declares the ranges the published package supports, and tests and the
    # protobuf_floor job pin their floors. The default strategy acts as increase for this project
    # and raises a floor to every release it proposes, so a PyYAML 6.0.4 would turn PyYAML>=6.0 into
    # PyYAML>=6.0.4. increase-if-necessary moves the exact pins and changes a range only when it
    # excludes the new release; lockfile-only would not move any pin. Pull request #194 (mcp
    # 1.30.0 to 2.3.0, which turned mcp>=1.9.0,<2 into mcp>=2.3.0,<3) proposed a release the range
    # excludes, which either strategy rewrites: the semver-major and mcp ignore rules keep it out,
    # and other tests in this file check both rules.
    assert pip.get("versioning-strategy") == "increase-if-necessary"


def test_dependabot_updates_every_npm_seed_from_one_entry() -> None:
    npm = _dependabot_updates()["npm"]

    # One entry with `directories` lets its group update all three seeds in a single pull request,
    # which keeps the pins the seeds must share identical.
    assert "directory" not in npm
    assert len(npm["directories"]) == len(set(npm["directories"]))
    assert set(npm["directories"]) == {"/" + seed for seed in SEEDS}
    assert all((ROOT / seed / "package-lock.json").is_file() for seed in SEEDS)
    # Security updates need a group too. Without one, a fix for a package the seeds share opens one
    # pull request per seed, and each fails tests/test_react_dependency_seeds.py because it moves
    # a single seed. The test above checks that the group takes every security update.
    assert _groups(npm, "security-updates"), "npm security updates are not grouped across the seeds"


def test_dependabot_never_proposes_the_pinned_contract_packages() -> None:
    from viewspec.app_freerange import FREERANGE_PACKAGE
    from viewspec.app_pretext import PRETEXT_PACKAGE

    assert {PRETEXT_PACKAGE, FREERANGE_PACKAGE} <= _HAND_BUMPED["npm"]
    updates = _dependabot_updates()
    for ecosystem, update in updates.items():
        ignored = {rule["dependency-name"] for rule in update["ignore"] if set(rule) == {"dependency-name"}}
        assert ignored == _HAND_BUMPED.get(ecosystem, set()), ecosystem

    # Every ignore rule says why in a comment directly above it.
    lines = DEPENDABOT.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines):
        if line.lstrip().startswith("- dependency-name:"):
            assert lines[number - 1].lstrip().startswith("#"), f"dependabot.yml:{number + 1} has no reason comment"


def test_dependabot_mcp_rule_follows_the_agents_extra_cap() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (requirement,) = project["optional-dependencies"]["agents"]
    cap = re.search(r"<\s*(\d+)", requirement)
    rules = [rule for rule in _dependabot_updates()["pip"]["ignore"] if rule["dependency-name"] == "mcp"]

    # While the extra caps the MCP SDK below a major, Dependabot must not propose that major;
    # once the cap goes, so must the rule.
    expected = [{"dependency-name": "mcp", "versions": [f">={cap.group(1)}"]}] if cap else []
    assert rules == expected
