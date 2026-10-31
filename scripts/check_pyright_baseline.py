"""Fail when pyright reports more errors than the committed baseline.

The package ships ``py.typed`` but still carries hundreds of pyright errors, so the
gate is a ratchet: ``pyright-baseline.json`` records the accepted error count and this
script fails when the count grows. Lower the baseline whenever errors are fixed:

    python scripts/check_pyright_baseline.py --update
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "pyright-baseline.json"


def pyright_error_count() -> int:
    executable = shutil.which("pyright")
    if executable is None:
        raise SystemExit("pyright is not installed; install the dev extras")
    completed = subprocess.run(  # noqa: S603 - fixed argument list, no user input
        [executable, "--outputjson", "src/viewspec"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"pyright did not produce JSON output: {completed.stderr.strip()}") from error
    return int(report["summary"]["errorCount"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="Write the current count as the new baseline.")
    args = parser.parse_args(argv)
    current = pyright_error_count()
    if args.update:
        BASELINE.write_text(
            json.dumps({"error_count": current, "scope": "src/viewspec"}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"pyright baseline set to {current} errors")
        return 0
    baseline = int(json.loads(BASELINE.read_text(encoding="utf-8"))["error_count"])
    if current > baseline:
        print(f"pyright reports {current} errors; the baseline allows {baseline}. Fix the new errors.")
        return 1
    if current < baseline:
        print(f"pyright reports {current} errors, below the baseline of {baseline}; run --update to ratchet down.")
    else:
        print(f"pyright reports {current} errors, matching the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
