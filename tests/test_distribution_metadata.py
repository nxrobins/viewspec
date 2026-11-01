from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_metadata() -> dict:
    return tomllib.loads(ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))


def test_package_metadata_exposes_expected_cli_and_optional_extras():
    pyproject = _project_metadata()

    assert pyproject["project"]["scripts"]["viewspec"] == "viewspec.cli:main"
    extras = pyproject["project"]["optional-dependencies"]
    assert set(extras) == {"agents", "dev"}
    assert "mcp>=1.30,<2" in extras["agents"]
    assert "build>=1.2" in extras["dev"]
    assert "pytest>=8.0" in extras["dev"]
    assert "hypothesis>=6.100,<7" in extras["dev"]
    assert "ruff==0.15.22" in extras["dev"]


def test_host_verify_template_resources_are_packaged():
    root = resources.files("viewspec.host_verify_template")
    required = [
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "playwright.config.ts",
        "tsconfig.json",
        "index.html",
        "src/App.tsx",
        "src/main.tsx",
        "src/index.css",
        "tests/host-verify.spec.ts",
    ]

    for rel in required:
        assert root.joinpath(*rel.split("/")).is_file(), rel


def test_host_verify_template_asserts_grid_column_and_span_counts():
    root = resources.files("viewspec.host_verify_template")
    template = root.joinpath("tests", "host-verify.spec.ts").read_text(encoding="utf-8")

    assert "expectedGridColumnCount" in template
    assert "expectedGridSpanCount" in template
    assert "grid-template-columns" in template
    assert "grid-column-end" in template
    assert "grid_column_assertion_count" in template
    assert "grid_span_assertion_count" in template


def test_react_tailwind_host_fixture_uses_span_aware_layout_language():
    script = ROOT.joinpath("tests", "react-tailwind-host", "scripts", "verify.mjs").read_text(encoding="utf-8")

    assert "aesthetic layout assertions" in script
    assert "aesthetic layout grid assertions" not in script


def test_schema_stub_is_packaged_next_to_py_typed():
    assert resources.files("viewspec").joinpath("py.typed").is_file()
    schema = resources.files("viewspec.schema")
    for name in ("__init__.py", "viewspec_pb2.py", "viewspec_pb2.pyi"):
        assert schema.joinpath(name).is_file(), name
