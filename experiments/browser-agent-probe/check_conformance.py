"""Run the same correctness cases on both drivers; skips or differing cases fail the gate.

This does not collect or compare performance. Build the Rust host first and set
VIEWSPEC_CHROMIUM to the operator-provisioned Chromium executable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITES = (
    "test_browser_bridge_e2e",
    "test_browser_bridge_network_e2e",
    "test_browser_bridge_choices",
    "test_browser_editor_contract",
    "test_browser_form_contract",
    "test_browser_form_preparation",
    "test_browser_dialog_preparation",
    "test_browser_readiness",
    "test_browser_feedback",
    "test_browser_response_view",
    "test_browser_bounded_responses",
    "test_browser_submit_recovery",
)


def passed_cases(report: Path) -> set[tuple[str, str]]:
    cases = ET.parse(report).findall(".//testcase")
    identities = {(case.attrib["classname"], case.attrib["name"]) for case in cases}
    if not cases or len(identities) != len(cases):
        raise ValueError("Conformance report has no cases or duplicate case identities")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("Conformance requires zero failures, errors, and skips")
    return identities


def main() -> None:
    executable = Path(os.environ.get("VIEWSPEC_CHROMIUM", ""))
    if not executable.is_absolute() or not executable.is_file():
        raise SystemExit("Set VIEWSPEC_CHROMIUM to the provisioned Chromium executable")
    previous = None
    with tempfile.TemporaryDirectory(prefix="viewspec-conformance-") as folder:
        for backend in ("playwright", "rust"):
            print(f"Shared correctness conformance: {backend}", flush=True)
            report = Path(folder) / f"{backend}.xml"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *(f"tests/{name}.py" for name in SUITES),
                    "-q",
                    "--strict-markers",
                    f"--junitxml={report}",
                ],
                cwd=ROOT,
                env={**os.environ, "VIEWSPEC_BROWSER_BACKEND": backend, "VIEWSPEC_RUN_BROWSER_BRIDGE_E2E": "1"},
                check=True,
            )
            cases = passed_cases(report)
            if previous is not None and previous != cases:
                raise SystemExit("The browser backends did not execute identical conformance cases")
            previous = cases
    print(f"Both backends passed the same {len(previous or ())} cases, with no skips.")


if __name__ == "__main__":
    main()
