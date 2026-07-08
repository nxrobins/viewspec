import difflib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"


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
