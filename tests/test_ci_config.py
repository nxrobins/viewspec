"""Invariants of the CI workflows and pinned dev dependencies that a plain lint would not catch."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
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


# The steps below run the pretext-e2e job's own shell code against stubbed inputs, so the
# assertions are checked as the runner executes them rather than as text.
_RUNS_LIKE_THE_E2E_JOB = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("bash") is None,
    reason="the pretext-e2e job runs its steps with bash on Linux",
)


def _browser_proof_step(name: str) -> tuple[str, dict[str, str]]:
    job = _load("pretext-e2e.yml")["jobs"]["browser-proof"]
    (step,) = [step for step in job["steps"] if step.get("name") == name]
    # Without a ``shell`` key, GitHub runs the step as ``bash -e {0}``, as _run_step does.
    assert "shell" not in step and "shell" not in job.get("defaults", {}).get("run", {})
    env = {key: str(value) for key, value in job["env"].items() if "${{" not in str(value)}
    return step["run"], env


def _run_step(
    script: str, env: dict[str, str], tmp_path: Path, tools: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool, body in tools.items():
        stub = bin_dir / tool
        stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    script_path = tmp_path / "step.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", str(script_path)],
        env={
            **env,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "VIEWSPEC_PRETEXT_E2E_ARTIFACT_DIR": str(artifact_dir),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def _printing(output: str) -> str:
    return f"printf '%s\\n' {shlex.quote(output)}"


def _next_patch(version: str) -> str:
    *head, patch = version.split(".")
    return ".".join([*head, str(int(patch) + 1)])


def _toolchain(env: dict[str, str], **overrides: str) -> dict[str, str]:
    versions = {
        "python": env["PYTHON_VERSION"],
        "node": f"v{env['NODE_VERSION']}",
        "npm": f"{env['NPM_MAJOR_MINOR']}.8",
        "bun": env["BUN_VERSION"],
    }
    versions.update(overrides)
    return {tool: _printing(version) for tool, version in versions.items()}


def test_pretext_e2e_pins_npm_to_major_minor_and_the_rest_exactly() -> None:
    _script, env = _browser_proof_step("Record pinned toolchain")

    assert "NPM_VERSION" not in env
    assert re.fullmatch(r"\d+\.\d+", env["NPM_MAJOR_MINOR"])
    for key in ("PYTHON_VERSION", "NODE_VERSION", "BUN_VERSION", "PLAYWRIGHT_VERSION"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", env[key]), key


@_RUNS_LIKE_THE_E2E_JOB
@pytest.mark.parametrize("patch", ["0", "8", "31"])
def test_pretext_e2e_toolchain_accepts_any_npm_patch_of_the_pinned_line(tmp_path: Path, patch: str) -> None:
    script, env = _browser_proof_step("Record pinned toolchain")

    result = _run_step(script, env, tmp_path, _toolchain(env, npm=f"{env['NPM_MAJOR_MINOR']}.{patch}"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{env['NPM_MAJOR_MINOR']}.{patch}" in (tmp_path / "artifacts" / "toolchain.txt").read_text(encoding="utf-8")


# Each case builds, from the pinned line's two numbers, an npm version that is outside that
# line or is not a plain X.Y.Z. Only the regex's ``$`` anchor rejects the last two cases: the
# text after their patch has no dot, so stripping the patch still leaves the pinned line.
_REJECTED_NPM_VERSIONS = {
    "next-minor": lambda major, minor: f"{major}.{minor + 1}.0",
    "previous-minor": lambda major, minor: f"{major}.{minor - 1}.0",
    "next-major": lambda major, minor: f"{major + 1}.{minor}.0",
    "minor-with-a-trailing-digit": lambda major, minor: f"{major}.{minor}0.0",
    "major-with-a-leading-digit": lambda major, minor: f"1{major}.{minor}.0",
    "no-patch": lambda major, minor: f"{major}.{minor}",
    "prerelease": lambda major, minor: f"{major}.{minor}.8-pre.1",
    "four-parts": lambda major, minor: f"{major}.{minor}.8.1",
    "non-numeric-patch": lambda major, minor: f"{major}.{minor}.x",
    "v-prefix": lambda major, minor: f"v{major}.{minor}.8",
    "empty": lambda major, minor: "",
    "build-metadata": lambda major, minor: f"{major}.{minor}.8+build",
    "trailing-space": lambda major, minor: f"{major}.{minor}.8 ",
}


@_RUNS_LIKE_THE_E2E_JOB
@pytest.mark.parametrize("case", sorted(_REJECTED_NPM_VERSIONS))
def test_pretext_e2e_toolchain_rejects_npm_outside_the_pinned_line(tmp_path: Path, case: str) -> None:
    script, env = _browser_proof_step("Record pinned toolchain")
    major, minor = (int(part) for part in env["NPM_MAJOR_MINOR"].split("."))
    npm = _REJECTED_NPM_VERSIONS[case](major, minor)

    result = _run_step(script, env, tmp_path, _toolchain(env, npm=npm))

    assert result.returncode != 0, f"npm {npm!r} was accepted"


@_RUNS_LIKE_THE_E2E_JOB
@pytest.mark.parametrize("tool", ["python", "node", "bun"])
def test_pretext_e2e_toolchain_still_pins_the_other_tools_exactly(tmp_path: Path, tool: str) -> None:
    script, env = _browser_proof_step("Record pinned toolchain")
    pinned = {"python": env["PYTHON_VERSION"], "node": f"v{env['NODE_VERSION']}", "bun": env["BUN_VERSION"]}[tool]

    result = _run_step(script, env, tmp_path, _toolchain(env, **{tool: _next_patch(pinned)}))

    assert result.returncode != 0, f"{tool} {_next_patch(pinned)} was accepted"


def _junit(*suites: dict[str, int], root: str = "testsuites") -> str:
    elements = []
    for counts in suites:
        attributes = " ".join(f'{key}="{value}"' for key, value in counts.items())
        elements.append(f'<testsuite name="pytest" {attributes}></testsuite>')
    if root == "testsuite":
        (element,) = elements
        return element
    return f'<testsuites name="pytest tests">{"".join(elements)}</testsuites>'


def _passing(tests: int) -> dict[str, int]:
    return {"errors": 0, "failures": 0, "skipped": 0, "tests": tests}


def _run_result_contract(tmp_path: Path, junit: str) -> subprocess.CompletedProcess[str]:
    script, env = _browser_proof_step("Validate E2E result contract")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "junit.xml").write_text(junit, encoding="utf-8")
    python = f'exec {shlex.quote(sys.executable)} "$@"'
    return _run_step(script, env, tmp_path, {"python": python})


@_RUNS_LIKE_THE_E2E_JOB
@pytest.mark.parametrize(
    "junit",
    [
        _junit(_passing(7)),
        _junit(_passing(7), root="testsuite"),
        _junit(_passing(8)),
        _junit(_passing(23)),
        _junit(_passing(5), _passing(3)),
    ],
    ids=["seven", "seven-bare-suite", "eight", "twenty-three", "two-suites"],
)
def test_pretext_e2e_result_contract_accepts_at_least_seven_clean_tests(tmp_path: Path, junit: str) -> None:
    result = _run_result_contract(tmp_path, junit)

    assert result.returncode == 0, result.stdout + result.stderr


@_RUNS_LIKE_THE_E2E_JOB
@pytest.mark.parametrize(
    "junit",
    [
        _junit(_passing(6)),
        _junit(_passing(3), _passing(3)),
        _junit(),
        _junit({**_passing(7), "failures": 1}),
        _junit({**_passing(23), "failures": 1}),
        _junit({**_passing(7), "errors": 1}),
        _junit({**_passing(23), "errors": 1}),
        _junit({**_passing(7), "skipped": 1}),
        _junit({**_passing(23), "skipped": 16}),
        _junit(_passing(7), {**_passing(1), "failures": 1}),
        # Equal non-zero counts pass the chained comparison; only its trailing ``== 0`` fails them.
        _junit({**_passing(9), "failures": 1, "errors": 1, "skipped": 1}),
    ],
    ids=[
        "six",
        "two-suites-of-three",
        "no-suites",
        "failure",
        "failure-in-larger-run",
        "error",
        "error-in-larger-run",
        "skip",
        "skips-in-larger-run",
        "failure-in-second-suite",
        "one-of-each",
    ],
)
def test_pretext_e2e_result_contract_rejects_too_few_or_unclean_tests(tmp_path: Path, junit: str) -> None:
    result = _run_result_contract(tmp_path, junit)

    assert result.returncode != 0
    assert "AssertionError" in result.stderr


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
