"""Documentation claims that the workflows, the packaging and the code must back.

Each test derives a fact from the file that makes it true (a workflow, ``pyproject.toml``, the
distribution checker, the code) and checks that the documents state that fact: the names, numbers,
paths and commands they cite, and the absence of claims that were found false in the review of
pull request #171 (F5, F8, F19, F20, F22, F24, F25, F26, F27, N3, N7, N10, N11, plus the P-6 and
R-8 resolution rows). The checks look for facts, not for exact sentences, so rewording a document
keeps them green while a document that drifts from its source turns them red.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import tomllib
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


def _changelog_bullets(marker: str) -> str:
    """The bullets of the newest changelog section that mention ``marker``, joined and squashed.

    Searching every section, newest first, keeps the check valid after a release moves the
    ``[Unreleased]`` entries under a version heading.
    """
    for section in _changelog_sections():
        bullets = [_squash(bullet) for bullet in re.split(r"\n(?=- )", section) if marker in _squash(bullet)]
        if bullets:
            return " ".join(bullets)
    raise AssertionError(f"no changelog bullet contains {marker!r}")


def _audit_row(finding_id: str) -> list[str]:
    for line in _read("docs/project-audit-2026-10-22.md").splitlines():
        if line.startswith(f"| {finding_id} |"):
            return [cell.strip() for cell in line.strip().strip("|").split("|")]
    raise AssertionError(f"no section 13 row for {finding_id}")


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
    # The CI job that runs the suite provides every prerequisite (full history, both npm seeds,
    # the Chromium runner), which is what lets CONTRIBUTING say that a local run with skips is not
    # full coverage.
    suite_jobs = [job for job in _workflow("ci.yml")["jobs"].values() if "pytest" in _steps_text(job)]

    def provides_prerequisites(job: dict[Any, Any]) -> bool:
        checkouts = [step for step in job["steps"] if "actions/checkout" in str(step.get("uses", ""))]
        steps = _steps_text(job)
        return (
            any((step.get("with") or {}).get("fetch-depth") == 0 for step in checkouts)
            and "npm ci" in steps
            and "playwright install" in steps
            and "chromium" in steps
        )

    assert any(provides_prerequisites(job) for job in suite_jobs)

    contributing = _read("CONTRIBUTING.md")
    assert "fail with dependency-missing errors" not in _squash(contributing)
    assert "shallow clone fails" not in _squash(contributing)
    items = re.split(r"\n(?=\d+\. \*\*)", contributing)
    for topic in ("history", "seed", "browser"):
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


def test_release_checklist_matches_the_pages_workflow() -> None:
    pages = _workflow("pages.yml")
    jobs = pages["jobs"]
    checklist = _read("docs/release-checklist.md")
    squashed = _squash(checklist)
    check_job = jobs["published_studio"]
    deploy = jobs["deploy"]
    if check_job.get("continue-on-error") is True and deploy.get("needs") == "published_studio" and "if" not in deploy:
        # The deploy runs even when the PyPI check fails, so the checklist must not promise a
        # blocked deploy, must name the job to watch, and must give the exact command that stops
        # the workflow from running ahead of PyPI.
        assert "blocks website deployment" not in squashed
        assert "previous site remains deployed" not in squashed
        assert "`published_studio`" in checklist
        assert f'gh workflow disable "{pages["name"]}"' in checklist
    uploads = [step for step in check_job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert len(uploads) == 1
    artifact = uploads[0]["with"]["name"]
    mentions = [clause for clause in _clauses(checklist) if f"`{artifact}`" in clause]
    assert mentions, f"the checklist does not name the {artifact} artifact"
    if "if" not in uploads[0]:
        # Without an always()/failure() condition the artifact exists only after a passing check.
        assert "records it in the" not in squashed
        assert all(re.search(r"\bpass", clause) for clause in mentions), mentions


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


def test_bug_prevention_caps_match_the_distribution_checker() -> None:
    spec = importlib.util.spec_from_file_location("_check_distribution", ROOT / "scripts" / "check_distribution.py")
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    (text,) = [line for line in _read("docs/bug-prevention.md").splitlines() if "check_distribution.py" in line]
    assert re.search(rf"\b{checker.MAX_MEMBERS} members\b", text), text
    for limit in (checker.MAX_ARCHIVE_BYTES, checker.MAX_UNPACKED_BYTES, checker.MAX_MEMBER_BYTES):
        assert limit % MIB == 0
        assert re.search(rf"\b{limit // MIB} MiB\b", text), (limit, text)


def _root_entries_the_tests_need() -> set[str]:
    """Top-level repository entries that some test reads through the repository root or imports."""
    top = {path.name for path in ROOT.iterdir()}
    joined = re.compile(
        r"""\b(?:ROOT|REPO_ROOT|PROJECT_ROOT|root|repo_root)\s*(?:/\s*|\.joinpath\(\s*)["']([^"'\\]+)["']"""
    )
    imported = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", flags=re.MULTILINE)
    needed: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        needed |= {match.group(1).split("/")[0] for match in joined.finditer(text)} & top
        needed |= {name for name in imported.findall(text) if name in top and (ROOT / name).is_dir()}
    return needed


def test_sdist_claims_name_what_the_archive_leaves_out() -> None:
    include = tomllib.loads(_read("pyproject.toml"))["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    shipped = {entry.strip("/").split("/")[0] for entry in include}
    assert "tests" in shipped
    missing = sorted(_root_entries_the_tests_need() - shipped)
    assert "packagers can run the suite" not in _read("CHANGELOG.md")
    assert "can run the tests from the archive" not in _read("scripts/check_distribution.py")
    if not missing:
        return
    bullet = _changelog_bullets("(R-8)")
    status, resolution = _audit_row("R-8")[1:]
    assert status != "Fixed"
    for text in (bullet, resolution):
        assert "git checkout" in text
        for name in missing:
            assert f"`{name}`" in text or f"`{name}/`" in text, (name, text)


def test_mcp_cap_in_the_changelog_and_audit_matches_the_agents_extra() -> None:
    (requirement,) = tomllib.loads(_read("pyproject.toml"))["project"]["optional-dependencies"]["agents"]
    upper = re.search(r"<(\d+)", requirement)
    assert upper is not None
    assert f"`{requirement}`" in _changelog_bullets("(P-6)")
    assert f"`mcp<{upper.group(1)}`" in _audit_row("P-6")[2]


def test_integer_range_claims_are_scoped_to_the_validation_boundary() -> None:
    import viewspec
    from viewspec.intent_tools import starter_intent_payload
    from viewspec.types import IntentBundle

    # The facts: the agent validator rejects an integer past 2^53, the Python compile() API does not.
    payload = starter_intent_payload()
    node = next(iter(payload["substrate"]["nodes"].values()))
    node["attrs"]["value"] = 2**53 + 1
    issues = viewspec.validate_agent_intent_bundle(payload).issues
    assert [issue.code for issue in issues] == ["INTEGER_OUT_OF_SAFE_RANGE"]
    viewspec.compile(IntentBundle.from_json(payload))

    guide = _read("docs/agent-integration.md")
    paragraphs = [_squash(part) for part in guide.split("\n\n") if "`INTEGER_OUT_OF_SAFE_RANGE`" in part]
    assert paragraphs, "docs/agent-integration.md no longer names INTEGER_OUT_OF_SAFE_RANGE"
    for text in (*paragraphs, _changelog_bullets("(C-1)"), _audit_row("C-1")[2]):
        assert "fails closed with `INTEGER_OUT_OF_SAFE_RANGE`" not in text
        assert "compile()" in text, text
        assert "validate_agent_intent_bundle" in text, text
        assert "2^53" in text, text


def test_compact_json_rendering_is_documented_as_it_renders() -> None:
    from viewspec.compiler import _text_from_value

    rendered = _text_from_value(["Ann", "Bob"])
    assert f"`{rendered}`" in _read("CHANGELOG.md")
    assert f"`{rendered}`" in _read("docs/agent-integration.md")


# Digests and serializers that do not go through viewspec._canonical: the function that does the
# serializing, and the name the C-5 changelog bullet and audit row must give it.
_OWN_FORM_DIGESTS = [
    ("viewspec.agent_eval_value", "canonical_bytes", "agent_eval_value.canonical_bytes"),
    ("viewspec.agent_eval", "canonical_json", "agent_eval.canonical_json"),
    ("viewspec.prove", "_report_text", "proof_report_hash"),
    ("viewspec.app_reports", "app_proof_report_hash", "app_proof_report_hash"),
    ("viewspec.local_verify", "_artifact_identity", "local_verify.py"),
    ("viewspec.emitters.react_tailwind_tsx", "tailwind_recipe_registry_digest", "tailwind_recipe_registry_digest"),
    ("viewspec.app_numeric", "numeric_operation_inventory_sha256", "numeric_operation_inventory_sha256"),
    ("viewspec.compiler_benchmarks", "stable_summary_json", "compiler_benchmarks.py"),
    ("viewspec.review_manifest", "ReviewManifestIndex", "semantic_identity_sha256"),
]


def test_canonical_json_claim_names_every_digest_that_keeps_its_own_form() -> None:
    bullet = _changelog_bullets("(C-5)")
    resolution = _audit_row("C-5")[2]
    # The claims the review found false: not every canonical helper is an alias.
    assert "every form now rejects NaN" not in _read("CHANGELOG.md")
    assert "every named canonical helper is an alias" not in resolution
    assert "all forms reject NaN" not in resolution
    assert "The canonical JSON helpers are thin aliases" not in bullet
    assert "tests/test_canonical.py" in bullet and "tests/test_canonical.py" in resolution
    for module_name, attribute, name in _OWN_FORM_DIGESTS:
        source = inspect.getsource(getattr(importlib.import_module(module_name), attribute))
        # If one of these moves onto the shared forms, drop it here and from the documents.
        assert "json.dumps(" in source and "canonical_json_" not in source, (module_name, attribute)
        assert name in bullet, (name, bullet)
        assert name in resolution, (name, resolution)


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
    after = _read("CONTRIBUTING.md").split(f"\n### {heading}\n", 1)[1]
    return re.split(r"\n#{2,3} ", after, maxsplit=1)[0]


def test_contributing_says_the_grouped_pip_update_leaves_the_declared_ranges_alone() -> None:
    updates = yaml.safe_load(_read(".github/dependabot.yml"))["updates"]
    (pip,) = [update for update in updates if update["package-ecosystem"] == "pip"]
    section = _squash(_contributing_section("Landing the grouped pip pull request"))

    # The strategy decides whether the pull request rewrites the floors in pyproject.toml, so the
    # guide names it next to its account of what the pull request changes.
    assert f"`versioning-strategy: {pip['versioning-strategy']}`" in section


def test_hand_bump_entries_name_the_pins_outside_the_package_files() -> None:
    entries = {
        match.group(1): _squash(entry)
        for entry in re.split(r"\n(?=- )", _contributing_section("Packages Dependabot never proposes"))
        if (match := re.match(r"- \*\*`([^`]+)`", entry))
    }
    hooks = {hook["id"] for repo in yaml.safe_load(_read(".pre-commit-config.yaml"))["repos"] for hook in repo["hooks"]}

    # The pre-commit hooks run ruff at their own rev, so a ruff bump moves that rev as well.
    assert "ruff" in hooks
    assert "`.pre-commit-config.yaml`" in entries["ruff"]
