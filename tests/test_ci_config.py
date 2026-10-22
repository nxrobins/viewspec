"""Invariants of the CI workflows and pinned dev dependencies that a plain lint would not catch."""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

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
