from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import viewspec


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


def test_top_level_package_exports_summary_helpers():
    for name in (
        "manifest_aesthetic_layout_summary",
        "manifest_aesthetic_style_summary",
        "manifest_root_aesthetic_profile",
        "profile_style_facts",
        "intent_semantic_change_lines",
        "summarize_host_verification_report",
        "summarize_intent_manifest",
        "AGENT_ASSET_CHECK_COMMAND",
        "AGENT_ASSET_CONTRACT_PROFILE",
        "AGENT_ASSET_EXPORT_COMMAND",
        "AGENT_ASSET_NETWORK_POLICY",
    ):
        assert name in viewspec.__all__
        value = getattr(viewspec, name)
        if name.startswith("AGENT_ASSET_"):
            assert isinstance(value, str)
        else:
            assert callable(value)
