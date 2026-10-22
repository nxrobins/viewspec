"""Modules run with ``python -m`` log through the ``viewspec`` logger tree, not ``__main__``."""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "viewspec"
_FORMATTED_PREFIX = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ERROR "


def _is_main_guard(node: ast.stmt) -> bool:
    """True for a module-level ``if __name__ == "__main__":`` (either operand order, any quotes)."""

    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left, *test.comparators]
    names = [op for op in operands if isinstance(op, ast.Name) and op.id == "__name__"]
    mains = [op for op in operands if isinstance(op, ast.Constant) and op.value == "__main__"]
    return len(names) == 1 and len(mains) == 1


def _module_entry_points() -> dict[str, ast.Module]:
    """Every viewspec module that can run as ``python -m``, mapped to its parsed source."""

    modules: dict[str, ast.Module] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(_is_main_guard(node) for node in tree.body):
            modules[".".join(path.relative_to(SRC).with_suffix("").parts)] = tree
    return modules


def _get_logger_calls(tree: ast.Module) -> list[ast.Call]:
    """Every ``logging.getLogger(...)`` or bare ``getLogger(...)`` call anywhere in the module."""

    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "getLogger")
            or (isinstance(node.func, ast.Name) and node.func.id == "getLogger")
        )
    ]


def _static_logger_name_problems(module_name: str, tree: ast.Module) -> list[str]:
    """Describe each getLogger call whose name is not a fixed string under the module's own name.

    Under ``python -m`` the module runs as ``__main__``, so ``getLogger(__name__)`` (or any
    computed name) puts the logger outside the ``viewspec`` tree, where neither the package
    NullHandler nor configure_daemon_logging applies. Importing the module in a test cannot
    show that, because an import sets ``__name__`` to the real module name; reading the
    argument from the source can.
    """

    problems: list[str] = []
    for call in _get_logger_calls(tree):
        where = f"{module_name}:{call.lineno} {ast.unparse(call)}"
        if call.keywords or len(call.args) != 1:
            problems.append(f"{where}: pass exactly one positional logger name")
            continue
        (arg,) = call.args
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            problems.append(f"{where}: use the string literal {module_name!r}, not a computed name")
        elif arg.value != module_name and not arg.value.startswith(module_name + "."):
            problems.append(f"{where}: {arg.value!r} is not {module_name!r} or a child of it")
    return problems


def test_known_python_m_modules_are_covered() -> None:
    entry_points = _module_entry_points()
    daemons = {"viewspec.review_daemon", "viewspec.studio_creation_daemon", "viewspec.studio_review_worker"}
    assert daemons | {"viewspec.cli", "viewspec.__main__"} <= set(entry_points)
    # The static check below must see each daemon's logger, or it would pass vacuously.
    for module_name in daemons:
        assert _get_logger_calls(entry_points[module_name]), module_name


@pytest.mark.parametrize("module_name", sorted(_module_entry_points()))
def test_python_m_module_logger_has_a_fixed_viewspec_name(module_name: str) -> None:
    assert _static_logger_name_problems(module_name, _module_entry_points()[module_name]) == []


def test_static_logger_name_check_rejects_a_dunder_name_logger() -> None:
    """The check above must flag the regression it exists for, and accept the fixed form."""

    def problems(source: str) -> list[str]:
        return _static_logger_name_problems("viewspec.review_daemon", ast.parse(source))

    assert problems('import logging\n_LOGGER = logging.getLogger("viewspec.review_daemon")\n') == []
    assert problems('import logging\n_LOGGER = logging.getLogger("viewspec.review_daemon.http")\n') == []
    for bad in (
        "import logging\n_LOGGER = logging.getLogger(__name__)\n",
        "from logging import getLogger\n_LOGGER = getLogger(__name__)\n",
        "import logging\n_LOGGER = logging.getLogger()\n",
        "import logging\n_LOGGER = logging.getLogger(name=__name__)\n",
        'import logging\n_LOGGER = logging.getLogger(f"viewspec.{__name__}")\n',
        'import logging\n_LOGGER = logging.getLogger("viewspec")\n',
        "import logging\ndef main():\n    logging.getLogger(__name__).error('x')\n",
    ):
        assert len(problems(bad)) == 1, bad


def _assigns_module_logger(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_LOGGER" for t in node.targets)
        for node in tree.body
    )


@pytest.mark.parametrize(
    "module_name",
    sorted(name for name, tree in _module_entry_points().items() if _assigns_module_logger(tree)),
)
def test_module_logger_name_does_not_follow_the_run_name(module_name: str) -> None:
    """Run the module body under a run name other than its import name, as ``python -m`` does.

    The run name is not ``"__main__"``, so the entry point itself does not start.
    """

    path = PACKAGE.joinpath(*module_name.split(".")[1:]).with_suffix(".py")
    namespace = runpy.run_path(str(path), run_name="__viewspec_python_m_probe__")
    logger = namespace["_LOGGER"]
    assert isinstance(logger, logging.Logger)
    assert logger.name == module_name


def _run_module(module_name: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module_name, *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=cwd,
        env={"PATH": os.defpath, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(cwd)},
    )


def test_review_daemon_startup_failure_reaches_the_configured_stderr_handler(tmp_path: Path) -> None:
    result = _run_module(
        "viewspec.review_daemon",
        "--source",
        str(tmp_path / "missing.intent.json"),
        "--state-root",
        str(tmp_path / "state"),
        "--port",
        "48731",
        cwd=tmp_path,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "REVIEW_SOURCE_NOT_FOUND"
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert re.match(
        _FORMATTED_PREFIX + r"viewspec\.review_daemon: Review daemon failed to start: REVIEW_SOURCE_NOT_FOUND: ",
        lines[0],
    ), result.stderr


def test_studio_creation_daemon_startup_failure_reaches_the_configured_stderr_handler(tmp_path: Path) -> None:
    result = _run_module(
        "viewspec.studio_creation_daemon",
        "--task",
        "missing-task.json",
        "--cwd",
        str(tmp_path),
        "--state-root",
        str(tmp_path / "state"),
        "--port",
        "48732",
        cwd=tmp_path,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    code = payload["error"]["code"]
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert re.match(
        _FORMATTED_PREFIX
        + r"viewspec\.studio_creation_daemon: Studio creation room failed to start: "
        + re.escape(code)
        + ": ",
        lines[0],
    ), result.stderr
