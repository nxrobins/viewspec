"""Importing the library must stay silent: no output, and no logging handlers beyond the NullHandler."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_PROBE = r"""
import io, json, logging, sys
captured_out, captured_err = io.StringIO(), io.StringIO()
real_out, real_err = sys.stdout, sys.stderr
sys.stdout, sys.stderr = captured_out, captured_err
try:
    import viewspec
finally:
    sys.stdout, sys.stderr = real_out, real_err
package_logger = logging.getLogger("viewspec")
owned = {
    name: logger
    for name, logger in logging.Logger.manager.loggerDict.items()
    if isinstance(logger, logging.Logger) and (name == "viewspec" or name.startswith("viewspec."))
}
print(json.dumps({
    "stdout": captured_out.getvalue(),
    "stderr": captured_err.getvalue(),
    "root_handlers": [type(handler).__name__ for handler in logging.getLogger().handlers],
    "root_level": logging.getLogger().level,
    "viewspec_handlers": [type(handler).__name__ for handler in package_logger.handlers],
    "handlers": {name: [type(h).__name__ for h in logger.handlers] for name, logger in owned.items() if logger.handlers},
    "levels": {name: logger.level for name, logger in owned.items() if logger.level != logging.NOTSET},
    "disabled": [name for name, logger in owned.items() if logger.disabled or not logger.propagate],
}))
"""


def test_import_emits_no_output_and_configures_no_handlers():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={"PATH": os.defpath, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    probe = json.loads(lines[0])
    assert probe["stdout"] == ""
    assert probe["stderr"] == ""
    # The package installs exactly one NullHandler on its own logger and touches nothing else.
    assert probe["viewspec_handlers"] == ["NullHandler"]
    assert probe["handlers"] == {"viewspec": ["NullHandler"]}
    assert probe["levels"] == {}
    assert probe["disabled"] == []
    assert probe["root_handlers"] == []
    assert probe["root_level"] == logging.WARNING


def test_package_logger_has_only_a_null_handler_in_process():
    import viewspec  # noqa: F401

    package_logger = logging.getLogger("viewspec")
    assert [type(handler).__name__ for handler in package_logger.handlers] == ["NullHandler"]
    assert package_logger.level == logging.NOTSET
    assert package_logger.propagate is True
