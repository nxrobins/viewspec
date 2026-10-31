"""Documentation claims that the workflows, the packaging and the code must back.

Each test derives a fact from the file that makes it true (a workflow, ``pyproject.toml``, the
distribution checker, the code) and checks that the documents state that fact: the names, numbers,
paths and commands they cite. The checks look for facts, not for exact sentences, so rewording a
document keeps them green while a document that drifts from its source turns them red.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
MIB = 1024 * 1024


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _clauses(text: str) -> list[str]:
    """Split squashed prose into clauses at sentence ends and semicolons."""
    return [clause for clause in re.split(r"(?<=[.;])\s+", _squash(text)) if clause]


def _changelog_sections() -> list[str]:
    return re.split(r"\n(?=## \[)", _read("CHANGELOG.md"))[1:]


def _workflow(name: str) -> dict[Any, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _jobs() -> dict[str, dict[Any, Any]]:
    jobs: dict[str, dict[Any, Any]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).items():
            jobs[f"{path.name}:{job_id}"] = job
    return jobs


def _steps_text(job: dict[Any, Any]) -> str:
    steps = job.get("steps") or []
    return "\n".join(f"{step.get('uses', '')} {step.get('run', '')} {step.get('with', '')}" for step in steps)


def test_each_changelog_release_has_one_subsection_per_change_type() -> None:
    for section in _changelog_sections():
        headings = re.findall(r"^### (.+)$", section, flags=re.MULTILINE)
        assert len(headings) == len(set(headings)), (section.splitlines()[0], headings)


def test_platform_statements_match_the_workflow_runners() -> None:
    jobs = _jobs()
    runners = {str(job.get("runs-on", "")) for job in jobs.values()}
    windows_jobs = [job for job in jobs.values() if str(job.get("runs-on", "")).startswith("windows")]
    for name in ("README.md", "CONTRIBUTING.md"):
        clauses = _clauses(_read(name))
        ci_clauses = [clause for clause in clauses if re.search(r"\bCI\b", clause)]
        mac = [clause for clause in ci_clauses if "macOS" in clause]
        if not any(runner.startswith("macos") for runner in runners):
            # No macOS runner: the document says so, and never says CI runs on macOS.
            assert mac, f"{name} does not say whether CI exercises macOS"
            assert all(re.search(r"\bnot\b", clause) for clause in mac), (name, mac)
        windows = [
            clause
            for clause in clauses
            if "Windows" in clause and re.search(r"\b(?:CI|job)\b", clause) and not re.search(r"\bnot\b", clause)
        ]
        if windows_jobs:
            assert windows, f"{name} does not mention the Windows CI job"
            assert "Ubuntu only" not in _squash(_read(name))
            if any("update_corpus_goldens.py --check" in _steps_text(job) for job in windows_jobs):
                assert any("golden" in clause for clause in windows), (name, windows)
        else:
            assert not windows, (name, windows)


def test_contributing_says_missing_prerequisites_skip_tests() -> None:
    # The CI job that runs the suite provides the npm seeds, which is what lets CONTRIBUTING say
    # that a local run with skips is not full coverage.
    suite_jobs = [job for job in _workflow("ci.yml")["jobs"].values() if "pytest" in _steps_text(job)]

    assert any("npm ci" in _steps_text(job) for job in suite_jobs)

    contributing = _read("CONTRIBUTING.md")
    assert "fail with dependency-missing errors" not in _squash(contributing)
    assert "shallow clone fails" not in _squash(contributing)
    items = re.split(r"\n(?=\d+\. \*\*)", contributing)
    for topic in ("seed",):
        topic_items = [item for item in items if topic in item.lower() and re.match(r"\d+\. \*\*", item)]
        assert topic_items, f"CONTRIBUTING has no prerequisite item about {topic}"
        assert any(re.search(r"\bskips?\b", item) for item in topic_items), topic
    assert "-rs" in contributing


def _pip_compile_command(text: str) -> str:
    match = re.search(r"uv pip compile .*?-o requirements-ci\.txt", _squash(text.replace("\\", " ")))
    assert match is not None
    return match.group(0)


def test_contributing_regenerates_the_pins_with_the_header_command() -> None:
    header = "\n".join(line.lstrip("#") for line in _read("requirements-ci.txt").splitlines() if line.startswith("#"))
    command = _pip_compile_command(header)
    blocks = re.findall(r"```(?:bash|sh|console)?\n(.*?)```", _read("CONTRIBUTING.md"), flags=re.DOTALL)
    documented = [_pip_compile_command(block) for block in blocks if "uv pip compile" in block]
    assert documented == [command]


def test_release_checklist_names_the_single_version_source() -> None:
    pyproject = tomllib.loads(_read("pyproject.toml"))
    assert "version" in pyproject["project"]["dynamic"]
    version_path = pyproject["tool"]["hatch"]["version"]["path"]
    checklist = _read("docs/release-checklist.md")
    assert f"`{version_path}`" in checklist
    assert "Confirm `pyproject.toml`," not in _squash(checklist)


def test_release_checklist_takes_artifacts_from_the_push_to_main_run() -> None:
    ci = _workflow("ci.yml")
    cancel = str(ci["concurrency"]["cancel-in-progress"])
    assert "pull_request" in cancel, "CI cancels superseded push runs; the checklist's advice no longer holds"
    names = [
        str(step["with"]["name"])
        for job in ci["jobs"].values()
        for step in job.get("steps", [])
        if "upload-artifact" in str(step.get("uses", "")) and "dist/" in str(step.get("with", {}).get("path", ""))
    ]
    assert len(names) == 1, names
    prefix = names[0].split("${{", 1)[0]
    assert prefix
    checklist = _squash(_read("docs/release-checklist.md"))
    assert prefix in checklist
    assert re.search(r"push[- ]to[- ]`?main`?", checklist)


def test_integer_range_claims_are_scoped_to_the_validation_boundary() -> None:
    import viewspec
    from viewspec.agent import validate_agent_intent_bundle
    from viewspec.intent_tools import starter_intent_payload
    from viewspec.types import IntentBundle

    # The facts: the agent validator rejects an integer past 2^53, the Python compile() API does not.
    payload = starter_intent_payload()
    node = next(iter(payload["substrate"]["nodes"].values()))
    node["attrs"]["value"] = 2**53 + 1
    issues = validate_agent_intent_bundle(payload).issues
    assert [issue.code for issue in issues] == ["INTEGER_OUT_OF_SAFE_RANGE"]
    viewspec.compile(IntentBundle.from_json(payload))

    guide = _read("docs/agent-integration.md")
    paragraphs = [_squash(part) for part in guide.split("\n\n") if "`INTEGER_OUT_OF_SAFE_RANGE`" in part]
    assert paragraphs, "docs/agent-integration.md no longer names INTEGER_OUT_OF_SAFE_RANGE"
    for text in paragraphs:
        assert "fails closed with `INTEGER_OUT_OF_SAFE_RANGE`" not in text
        assert "compile()" in text, text
        assert "validate_agent_intent_bundle" in text, text
        assert "2^53" in text, text


def test_compact_json_rendering_is_documented_as_it_renders() -> None:
    from viewspec.compiler import _text_from_value

    rendered = _text_from_value(["Ann", "Bob"])
    assert f"`{rendered}`" in _read("CHANGELOG.md")
    assert f"`{rendered}`" in _read("docs/agent-integration.md")


def test_contributing_explains_landing_dependabot_updates_and_every_hand_bump() -> None:
    contributing = _read("CONTRIBUTING.md")
    commands = {
        line.strip()
        for block in re.findall(r"```(?:bash|sh|console)?\n(.*?)```", contributing, flags=re.DOTALL)
        for line in block.splitlines()
    }
    updates = {
        update["package-ecosystem"]: update for update in yaml.safe_load(_read(".github/dependabot.yml"))["updates"]
    }

    # A grouped npm update changes the template's package files, whose hashes the goldens pin, so
    # landing it takes a goldens refresh, and that is the refresh command.
    assert "npm" in updates
    goldens = _read("tests/fixtures/corpus_goldens.json")
    assert '"package.json"' in goldens and '"package-lock.json"' in goldens
    assert "python scripts/update_corpus_goldens.py" in commands
    # Every package the configuration never proposes has a hand-bump entry: a bullet that opens
    # with a bold package name and names this one.
    entries = [item for item in re.split(r"\n(?=- )", contributing) if item.startswith("- **`")]
    for ecosystem, update in updates.items():
        for rule in update.get("ignore", []):
            name = rule["dependency-name"]
            if name != "*":
                assert any(f"`{name}`" in entry for entry in entries), (ecosystem, name)


def _contributing_section(heading: str) -> str:
    """The text under ``### heading`` in CONTRIBUTING.md, up to the next heading."""
    parts = _read("CONTRIBUTING.md").split(f"\n### {heading}\n", 1)
    assert len(parts) == 2, f"CONTRIBUTING.md has no '### {heading}' section"
    return re.split(r"\n#{2,3} ", parts[1], maxsplit=1)[0]


def test_contributing_says_the_grouped_pip_update_leaves_the_declared_ranges_alone() -> None:
    updates = yaml.safe_load(_read(".github/dependabot.yml"))["updates"]
    (pip,) = [update for update in updates if update["package-ecosystem"] == "pip"]
    section = _squash(_contributing_section("Landing the grouped pip pull request"))

    # The strategy decides whether the pull request rewrites the floors in pyproject.toml, so the
    # guide names it next to its account of what the pull request changes.
    assert f"`versioning-strategy: {pip['versioning-strategy']}`" in section


def _hand_bump_entries() -> dict[str, str]:
    """The squashed bullets under "Packages Dependabot never proposes", by the package each opens with."""
    return {
        match.group(1): _squash(entry)
        for entry in re.split(r"\n(?=- )", _contributing_section("Packages Dependabot never proposes"))
        if (match := re.match(r"- \*\*`([^`]+)`", entry))
    }


def test_hand_bump_entries_name_the_pins_outside_the_package_files() -> None:
    entries = _hand_bump_entries()
    hooks = {hook["id"] for repo in yaml.safe_load(_read(".pre-commit-config.yaml"))["repos"] for hook in repo["hooks"]}
    floor_job = _steps_text(_workflow("ci.yml")["jobs"]["protobuf_floor"])

    # The pre-commit hooks run ruff at their own rev, so a ruff bump moves that rev as well.
    assert "ruff" in hooks
    assert "`.pre-commit-config.yaml`" in entries["ruff"]
    # The protobuf_floor job installs the protobuf floor by its exact version, so a grpcio-tools bump
    # that moves the floor moves that pin as well; a search for the old grpcio-tools version misses it.
    assert "protobuf==" in floor_job
    assert "`protobuf_floor`" in entries["grpcio-tools"]


_SEMVER_MAJOR = {"dependency-name": "*", "update-types": ["version-update:semver-major"]}


def _dependabot_updates() -> dict[str, dict[str, Any]]:
    updates = yaml.safe_load(_read(".github/dependabot.yml"))["updates"]
    return {update["package-ecosystem"]: update for update in updates}


def _tests_quoting(text: str) -> list[str]:
    """The test modules that hold ``text`` as a string literal, as repository paths."""
    return [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "tests").glob("*.py"))
        if f'"{text}"' in path.read_text(encoding="utf-8")
    ]


def test_contributing_explains_grouped_security_updates_and_the_audit_fallback() -> None:
    updates = _dependabot_updates()
    section = _squash(_contributing_section("Security updates"))
    audit = _workflow("dependency-audit.yml")
    steps = "\n".join(_steps_text(job) for job in audit["jobs"].values())
    audited_seeds = {
        seed for job in audit["jobs"].values() for seed in (job.get("strategy") or {}).get("matrix", {}).get("seed", [])
    }

    # Every ecosystem groups its security updates, and the npm group spans the three seeds.
    for ecosystem, update in updates.items():
        assert any(group.get("applies-to") == "security-updates" for group in update["groups"].values()), ecosystem
    assert len(updates["npm"]["directories"]) == 3 and "all three seeds" in section
    # The update-types ignores do not hold security updates back, so one can be a major, and an npm
    # one changes the shipped seeds, which a Security changelog bullet records.
    assert "semver-major" in section
    assert "`### Security`" in section
    # Packages ignored by name may get no security pull request, so the guide points at the weekly
    # audit, which has to run on a schedule over every npm seed and every pip package ignored by name.
    assert "`.github/workflows/dependency-audit.yml`" in section
    assert "schedule" in audit.get("on", audit.get(True))
    assert audited_seeds == {directory.lstrip("/") for directory in updates["npm"]["directories"]}
    assert "npm audit" in steps and "pip-audit" in steps
    installed = re.search(r'pip install [^\n]*?"\.\[([\w,-]+)\]"', steps)
    assert installed is not None, steps
    extras = tomllib.loads(_read("pyproject.toml"))["project"]["optional-dependencies"]
    audited = {
        re.split(r"[^A-Za-z0-9_.-]", requirement, maxsplit=1)[0].lower()
        for extra in installed.group(1).split(",")
        for requirement in extras[extra]
    }
    by_name = {rule["dependency-name"] for rule in updates["pip"]["ignore"] if set(rule) == {"dependency-name"}}
    assert by_name and by_name <= audited, by_name - audited


def test_contributing_says_a_stored_ignore_lasts_until_unignore() -> None:
    contributing = _squash(_read("CONTRIBUTING.md"))

    # On a grouped pull request `@dependabot ignore <package> minor version` closes the pull request
    # and stores an ignore condition, outside dependabot.yml, until `@dependabot unignore <package>`.
    # It does not skip one version for one run (the claim the review of pull request #202 found).
    assert "groups the remaining updates without that version" not in contributing
    assert "@dependabot ignore <package> minor version" in contributing
    assert "@dependabot unignore <package>" in contributing
    assert "@dependabot show <package> ignore conditions" in contributing


def test_contributing_logs_every_update_of_the_shipped_template_in_the_changelog() -> None:
    from viewspec.app_react import REACT_APP_TEMPLATE_PACKAGE

    template = "src/" + REACT_APP_TEMPLATE_PACKAGE.replace(".", "/")
    wheel = tomllib.loads(_read("pyproject.toml"))["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    manifest = json.loads(_read(f"{template}/package.json"))
    shipped = {name for group in ("dependencies", "devDependencies") for name in manifest.get(group, {})}

    npm = _dependabot_updates()["npm"]
    hand_bumped = {rule["dependency-name"] for rule in npm["ignore"] if set(rule) == {"dependency-name"}} & shipped

    # The wheel carries the template that every generated React app copies its package files from,
    # so changing a version it pins is user-visible and needs a changelog entry, however it lands:
    # through the grouped npm pull request or a hand bump.
    assert "/" + template in npm["directories"]
    assert any(f"{template}/".startswith(f"{package.rstrip('/')}/") for package in wheel), wheel
    assert "`CHANGELOG.md`" in _contributing_section("Landing the grouped npm pull request")
    assert hand_bumped
    for name in hand_bumped:
        assert any(f"`{name}`" in entry and "changelog" in entry for entry in _hand_bump_entries().values()), name


def test_contributing_says_how_to_make_each_major_update_by_hand() -> None:
    labels = {"pip": "pip", "npm": "npm", "github-actions": "GitHub Actions"}
    steps = {
        match.group(1): _squash(item)
        for item in re.split(r"\n(?=- )", _contributing_section("Major updates"))
        if (match := re.match(r"- \*\*([^*]+)\*\*", item))
    }
    skipped = [ecosystem for ecosystem, update in _dependabot_updates().items() if _SEMVER_MAJOR in update["ignore"]]

    # Dependabot never proposes a major in these ecosystems, so the guide says how to make one.
    assert skipped
    for ecosystem in skipped:
        assert labels[ecosystem] in steps, ecosystem
    # A range can allow a major that the CI pins never reach; --upgrade-package moves the pins.
    assert "--upgrade-package" in steps["pip"]
    assert "goldens" in steps["npm"] and "changelog" in steps["npm"]
    assert "`# vN`" in steps["GitHub Actions"]


def test_contributing_names_the_pins_and_tests_a_dependency_update_must_keep() -> None:
    updates = _dependabot_updates()
    pip_landing = _squash(_contributing_section("Landing the grouped pip pull request"))
    actions_landing = _squash(_contributing_section("Landing the GitHub Actions pull request"))

    # The grouped pip pull request skips every package ignored by name; the guide lists them.
    for rule in updates["pip"]["ignore"]:
        if rule["dependency-name"] != "*":
            assert f"`{rule['dependency-name']}`" in pip_landing, rule

    # Dependabot treats every use of an action as one dependency, so a release on one major can
    # move a pin on another; the guide names each action pinned at two majors, and the test that
    # pins one of their commits.
    workflows = "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml")))
    pins = re.findall(r"uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*(v\d+)", workflows)
    majors: dict[str, set[str]] = defaultdict(set)
    for action, _commit, major in pins:
        majors[action].add(major)
    assert pins
    for action, labels in majors.items():
        if len(labels) > 1:
            assert f"`{action}`" in actions_landing, (action, labels)
    if any(commit in _read("tests/test_ci_config.py") for _action, commit, _major in pins):
        assert "`tests/test_ci_config.py`" in actions_landing
