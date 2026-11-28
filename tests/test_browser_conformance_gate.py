"""The parity gate must not accept a green but incomplete test report."""

import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe/check_conformance.py"
SPEC = importlib.util.spec_from_file_location("browser_conformance_gate", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "<testcase classname='suite' name='one'><skipped/></testcase>",
        "<testcase classname='suite' name='one'><failure/></testcase>",
        "<testcase classname='suite' name='one'><error/></testcase>",
        "<testcase classname='suite' name='one'/><testcase classname='suite' name='one'/>",
    ],
)
def test_incomplete_or_failed_reports_cannot_prove_parity(tmp_path, content):
    report = tmp_path / "result.xml"
    report.write_text(f"<testsuites><testsuite>{content}</testsuite></testsuites>")
    with pytest.raises(ValueError):
        MODULE.passed_cases(report)


def test_gate_compares_case_identities_not_just_counts(tmp_path):
    report = tmp_path / "result.xml"
    report.write_text(
        "<testsuites><testsuite><testcase classname='suite' name='one'/><testcase classname='suite' name='two'/></testsuite></testsuites>"
    )
    assert MODULE.passed_cases(report) == {("suite", "one"), ("suite", "two")}


def test_runner_opts_in_both_backends_and_rejects_different_cases(tmp_path, monkeypatch):
    from tests.test_browser_bridge_e2e import E2E_OPT_IN

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("VIEWSPEC_CHROMIUM", str(executable))
    runs = []

    def run(command, *, cwd, env, check):
        assert check and cwd == MODULE.ROOT and env[E2E_OPT_IN] == "1"
        assert "tests/test_browser_bridge_e2e.py" in command
        assert "tests/test_browser_bridge_network_e2e.py" in command
        backend = env["VIEWSPEC_BROWSER_BACKEND"]
        runs.append(backend)
        report = Path(next(item.split("=", 1)[1] for item in command if item.startswith("--junitxml=")))
        report.write_text(
            f"<testsuites><testsuite><testcase classname='suite' name='{backend}'/></testsuite></testsuites>"
        )

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    with pytest.raises(SystemExit, match="identical conformance cases"):
        MODULE.main()
    assert runs == ["playwright", "rust"]
