from __future__ import annotations

from pathlib import Path


def test_primary_docs_are_intent_first():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "README.md",
        root / "docs/getting-started.md",
        root / "docs/agent-integration.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "demos/agent-system-prompt.txt",
        root / "integrations/claude-code/SKILL.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "IntentBundle" in text, path
        assert "viewspec.intent.json" in text or path.name == "agent-system-prompt.txt", path
        if path.name != "agent-system-prompt.txt":
            assert "viewspec init design --out DESIGN.md" in text, path
        assert "Your agent writes HTML" not in text, path
        assert "agent HTML governance first" not in text, path
        assert "agent output governance" not in text, path
        assert "agent-native UI IR" not in text, path
        assert "semantic UI IR" not in text, path


def test_local_html_wedge_is_explicitly_import_fallback():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("docs/local-html-wedge.md").read_text(encoding="utf-8")

    assert "import/fallback" in text
    assert "For new UI, agents should emit `viewspec.intent.json`" in text


def test_primary_docs_treat_compiled_outputs_as_artifacts():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "README.md",
        root / "docs/getting-started.md",
        root / "docs/agent-integration.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "integrations/claude-code/SKILL.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "dist/index.html" in text, path
        assert "react-output/ViewSpecView.tsx" in text, path
        assert "viewspec.intent.json" in text, path


def test_claude_code_skill_matches_native_agent_workflow():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("integrations/claude-code/SKILL.md").read_text(encoding="utf-8")

    assert "Do not write HTML, CSS, React, SwiftUI, Flutter, or CompositionIR as source." in text
    assert "viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/" in text
    assert (
        "viewspec compile viewspec.intent.json --design DESIGN.md --target react-tailwind-tsx --out react-tailwind-output/"
        in text
    )
    assert "viewspec check react-output/" in text
    assert "viewspec prove react-tailwind-output/ --install --json" in text
    assert "viewspec prove --out .viewspec-proof" in text
    assert (
        "viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json" in text
    )
    assert "Do not upload, share, call hosted APIs" in text
    assert "Never patch or recursively compile generated artifacts" in text


def test_first_proof_is_public_and_bounded():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "README.md",
        root / "docs/getting-started.md",
        root / "docs/agent-integration.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "demos/agent-system-prompt.txt",
        root / "demos/proof-bundle/index.html",
        root / "integrations/claude-code/SKILL.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "--out .viewspec-proof" in text, path
        assert "PROOF.md" in text, path
        assert "support_bundle.json" in text, path
        assert "pixel-perfect visual" in text, path

    proof_bundle = root.joinpath("docs/proof-bundle.md").read_text(encoding="utf-8")
    for expected in [
        "Status",
        "Proof level",
        "Claim",
        "Hashes",
        "Checks",
        "Host Verification",
        "Policy",
        "Errors",
        "repair_checklist",
        "proof_report.json",
        "support_bundle.json",
        "PROVE_SUPPORT_BUNDLE_WRITE_FAILED",
        "not pixel-perfect visual regression",
    ]:
        assert expected in proof_bundle

    getting_started = root.joinpath("docs/getting-started.md").read_text(encoding="utf-8")
    assert getting_started.index("viewspec prove --out .viewspec-proof") < getting_started.index(
        "viewspec init --out viewspec.intent.json"
    )

    release = root.joinpath("docs/release-checklist.md").read_text(encoding="utf-8")
    assert "demos/public-facts.json" in release
    assert "Publish to PyPI manually" in release
    assert "Do not add automated PyPI upload" in release


def test_public_docs_include_app_bundle_v0_contract():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "README.md",
        root / "docs/getting-started.md",
        root / "docs/agent-integration.md",
        root / "docs/app-bundle-v0.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "integrations/claude-code/SKILL.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "AppBundle" in text, path
        assert "viewspec init app" in text, path
        assert "viewspec check viewspec.app.json" in text, path
        assert "viewspec compile viewspec.app.json" in text, path
        assert "viewspec prove viewspec.app.json" in text, path
        assert "unbound_v0" in text, path
        assert "fixture_readonly_v0" in text, path
        assert "Static Shell V0" in text, path
        assert "runtime navigation" in text or "browser navigation" in text, path


def test_public_docs_include_aesthetic_profile_contract():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "README.md",
        root / "docs/getting-started.md",
        root / "docs/agent-integration.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "demos/agent-system-prompt.txt",
    ]
    tokens = [
        "aesthetic.calm_ops",
        "aesthetic.premium_saas",
        "aesthetic.data_dense",
        "aesthetic.editorial_product",
        "aesthetic.executive_review",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, path
        assert "not CSS" in text or "not arbitrary CSS" in text, path
        assert "pixel-perfect" in text, path


def test_reference_grounding_is_explicit_opt_in():
    root = Path(__file__).resolve().parents[1]
    # Authored prose only. demos/index.html is now compiled ViewSpec output (from
    # build_landing.py), not hand-written copy, so the "affirm the opt-in stance" positive
    # assertion does not apply to it.
    docs = [
        root / "docs/agent-integration.md",
        root / "demos/llms.txt",
        root / "demos/llms-full.txt",
        root / "demos/agent-system-prompt.txt",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "Lazyweb" not in text, path
        assert "query an MCP-accessible UI reference library" not in text, path
        assert "Agents should query a reference library" not in text, path
        assert "remote reference libraries" in text, path
