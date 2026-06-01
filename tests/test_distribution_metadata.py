from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _project_metadata() -> dict:
    return tomllib.loads(ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))


def test_package_metadata_exposes_expected_cli_and_optional_extras():
    pyproject = _project_metadata()

    assert pyproject["project"]["scripts"]["viewspec"] == "viewspec.cli:main"
    extras = pyproject["project"]["optional-dependencies"]
    assert "mcp>=1.9.0" in extras["agents"]
    assert "httpx>=0.27.0" in extras["remote"]
    assert "build>=1.2" in extras["dev"]
    assert "pytest>=8.0" in extras["dev"]
    assert "ruff>=0.4" in extras["dev"]
