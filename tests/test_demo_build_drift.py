import difflib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"

# Files under demos/proof-explorer that are authored by hand next to the builder output.
PROOF_EXPLORER_HAND_AUTHORED = frozenset({"index.html", "proof-explorer.css", "proof-explorer.js"})

# demos/cross-platform-dashboard/artifacts/** is a hosted fixture, not a local build product.
# The hosted compiler emitted it under the ``hosted_extended_v1`` contract profile (inputs, rules,
# projections) and it includes SwiftUI and Flutter targets that the local SDK has no emitter for,
# so no builder in this repository can regenerate it. It is therefore excluded from the byte-level
# drift checks below and only its self-consistency is pinned.
HOSTED_FIXTURES = ("demos/cross-platform-dashboard/artifacts",)


def _load_demo_builder(module_name: str) -> ModuleType:
    if str(DEMOS) not in sys.path:
        sys.path.insert(0, str(DEMOS))
    module_path = DEMOS / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_demo_page_current(relative_path: str, generated: str) -> None:
    output_path = ROOT / relative_path
    expected = output_path.read_text(encoding="utf-8")
    if expected == generated:
        return
    diff = difflib.unified_diff(
        expected.splitlines(),
        generated.splitlines(),
        fromfile=f"checked-in {relative_path}",
        tofile=f"generated {relative_path}",
        lineterm="",
    )
    preview = "\n".join(list(diff)[:80])
    raise AssertionError(f"{relative_path} is out of date. Regenerate its demo builder output.\n{preview}")


def _assert_demo_bytes_current(relative_path: str, generated: bytes) -> None:
    output_path = ROOT / relative_path
    expected = output_path.read_bytes()
    if expected == generated:
        return
    diff = difflib.unified_diff(
        expected.decode("utf-8", errors="replace").splitlines(),
        generated.decode("utf-8", errors="replace").splitlines(),
        fromfile=f"checked-in {relative_path}",
        tofile=f"generated {relative_path}",
        lineterm="",
    )
    preview = "\n".join(list(diff)[:80])
    raise AssertionError(f"{relative_path} is out of date. Regenerate its demo builder output.\n{preview}")


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _assert_demo_tree_current(
    relative_dir: str,
    generated_root: Path,
    *,
    hand_authored: frozenset[str] = frozenset(),
) -> None:
    """Every builder output must be checked in byte-for-byte, and nothing else may be checked in."""
    checked_in_files = _relative_files(ROOT / relative_dir)
    generated_files = _relative_files(generated_root)
    assert generated_files, f"{relative_dir} builder produced no files"
    lost = sorted(hand_authored - checked_in_files)
    assert not lost, f"{relative_dir} lost hand-authored files: {lost}"
    stale = sorted(checked_in_files - generated_files - hand_authored)
    assert not stale, f"{relative_dir} contains files its builder no longer produces: {stale}"
    missing = sorted(generated_files - checked_in_files)
    assert not missing, f"{relative_dir} is missing builder outputs: {missing}"
    for name in sorted(generated_files):
        _assert_demo_bytes_current(f"{relative_dir}/{name}", (generated_root / name).read_bytes())


def _aesthetic_profiles_page() -> str:
    builder = _load_demo_builder("build_aesthetic_profiles")
    return builder.build_page(builder.compile_profiles())


def test_aesthetic_profiles_demo_proof_tracks_semantics_style_and_layout():
    builder = _load_demo_builder("build_aesthetic_profiles")
    profiles = builder.compile_profiles()
    evidence = builder.profile_evidence(profiles)
    generated = builder.build_page(profiles)

    assert evidence["version"] == "aesthetic_profile_demo_proof.v2"
    assert evidence["profileCount"] == len(builder.AESTHETIC_PROFILE_TOKENS)
    assert evidence["semanticIdsStable"] is True
    assert evidence["semanticHash"]
    assert evidence["nodeCount"] > 0
    assert evidence["styleProjectionDistinct"] is True
    assert evidence["styleProjectionHashCount"] == evidence["profileCount"]
    assert set(evidence["styleProjectionHashes"]) == set(builder.AESTHETIC_PROFILE_TOKENS)
    assert evidence["layoutProjectionDiverges"] is True
    assert evidence["layoutSignatureCount"] >= 3
    assert set(evidence["layoutSignatures"]) == set(builder.AESTHETIC_PROFILE_TOKENS)
    assert evidence["comparisonAxisCount"] == 5
    assert set(evidence["comparisonAxisLabels"]) == {
        "color_tone",
        "surface_depth",
        "density_spacing",
        "type_rhythm",
        "layout_composition",
    }
    assert set(evidence["comparisonAxes"]) == set(builder.AESTHETIC_PROFILE_TOKENS)
    for axes in evidence["comparisonAxes"].values():
        assert set(axes) == set(evidence["comparisonAxisLabels"])
        assert axes["color_tone"]["changedTokenCount"] > 0
        assert axes["surface_depth"]["changedTokenCount"] > 0
        assert axes["density_spacing"]["changedTokenCount"] > 0
        assert axes["type_rhythm"]["changedTokenCount"] > 0
        assert axes["layout_composition"]["signature"]
    assert 'id="aesthetic-profile-evidence"' in generated
    assert 'aria-label="Compiler-derived aesthetic comparison axes"' in generated
    assert '"semanticIdsStable": true' in generated
    assert '"styleProjectionDistinct": true' in generated


def _fifteen_lines_page() -> str:
    builder = _load_demo_builder("build_fifteen_lines")
    fragments, stats = builder.compile_fragments()
    return builder.build_page(fragments, builder.code_line_data(), stats)


def _invariants_page() -> str:
    builder = _load_demo_builder("build_invariants")
    return builder.build_page(builder.compile_sections())


def _live_builder_page() -> str:
    builder = _load_demo_builder("build_live_builder")
    return builder.build_page(builder.compile_presets())


def _motif_switcher_page() -> str:
    builder = _load_demo_builder("build_motif_switcher")
    return builder.build_page(builder.compile_variants())


def _provenance_inspector_page() -> str:
    builder = _load_demo_builder("build_provenance_inspector")
    fragment, data, bundle = builder.compile_demo()
    return builder.build_page(fragment, data, bundle)


def _stateful_collections_page() -> str:
    builder = _load_demo_builder("build_stateful_collections")
    return builder.build_page(builder.compile_demo_bundles())


def _style_derivation_page() -> str:
    builder = _load_demo_builder("build_style_derivation")
    fragment, stats = builder.compile_demo()
    return builder.build_page(fragment, stats)


def _style_range_page() -> str:
    builder = _load_demo_builder("build_style_range")
    return builder.build_page(builder.compile_profiles())


DETERMINISTIC_DEMO_PAGES: tuple[tuple[str, Callable[[], str]], ...] = (
    ("demos/aesthetic-profiles/index.html", _aesthetic_profiles_page),
    ("demos/fifteen-lines/index.html", _fifteen_lines_page),
    ("demos/invariants/index.html", _invariants_page),
    ("demos/live-builder/index.html", _live_builder_page),
    ("demos/motif-switcher/index.html", _motif_switcher_page),
    ("demos/provenance-inspector/index.html", _provenance_inspector_page),
    ("demos/stateful-collections/index.html", _stateful_collections_page),
    ("demos/style-derivation/index.html", _style_derivation_page),
    ("demos/style-range/index.html", _style_range_page),
)


@pytest.mark.parametrize(("relative_path", "generate"), DETERMINISTIC_DEMO_PAGES)
def test_generated_demo_matches_builder_output(relative_path: str, generate: Callable[[], str]):
    _assert_demo_page_current(relative_path, generate())


def test_landing_page_and_compiled_mirror_match_builder_output(tmp_path, monkeypatch):
    # build_landing.build() writes to fixed module-level locations, so point them at a temporary
    # copy. Its only inputs are the compiler and demos/public-facts.json; two builds are identical.
    builder = _load_demo_builder("build_landing")
    compiled = tmp_path / "landing-compiled"
    public_index = tmp_path / "index.html"
    monkeypatch.setattr(builder, "COMPILED_DIR", compiled)
    monkeypatch.setattr(builder, "PROFILE_DIR", compiled / "profiles")
    monkeypatch.setattr(builder, "PUBLIC_INDEX", public_index)

    builder.build()

    _assert_demo_bytes_current("demos/index.html", public_index.read_bytes())
    _assert_demo_tree_current("demos/landing-compiled", compiled)


def test_proof_explorer_data_and_screenshots_match_builder_output(tmp_path):
    # build_proof_explorer.build() projects conformance/refinement/** into proof-data.json and a
    # screenshots/ tree; the page shell next to them is hand-authored.
    builder = _load_demo_builder("build_proof_explorer")
    generated = tmp_path / "proof-explorer"

    builder.build(generated)

    _assert_demo_tree_current("demos/proof-explorer", generated, hand_authored=PROOF_EXPLORER_HAND_AUTHORED)


def test_cross_platform_dashboard_artifacts_are_a_self_consistent_hosted_fixture():
    (relative_dir,) = HOSTED_FIXTURES
    artifacts = ROOT / relative_dir
    index = json.loads((artifacts / "artifact_index.json").read_text(encoding="utf-8"))

    assert index["contract_profile"] == "hosted_extended_v1"
    targets = {name: index[name] for name in ("html", "react_tsx", "swiftui", "flutter")}
    listed = {index["ast_bundle"], index["intent_bundle"], index["prompt"]}
    listed.update(path for target in targets.values() for path in target.values())
    for relative_path in sorted(listed):
        assert (artifacts / relative_path).is_file(), f"{relative_dir} index names a missing file: {relative_path}"
    assert _relative_files(artifacts) == listed | {"artifact_index.json"}, (
        f"{relative_dir} holds files its artifact index does not describe"
    )
    # The local SDK ships no SwiftUI or Flutter emitter. If one lands, this fixture can be
    # regenerated locally and belongs in the byte-level drift checks above instead.
    assert importlib.util.find_spec("viewspec.emitters.swiftui") is None
    assert importlib.util.find_spec("viewspec.emitters.flutter") is None
