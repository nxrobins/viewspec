import difflib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


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


def test_aesthetic_profiles_demo_matches_builder_output():
    builder = _load_demo_builder("build_aesthetic_profiles")
    _assert_demo_page_current(
        "demos/aesthetic-profiles/index.html",
        builder.build_page(builder.compile_profiles()),
    )


def test_stateful_collections_demo_matches_builder_output():
    builder = _load_demo_builder("build_stateful_collections")
    _assert_demo_page_current(
        "demos/stateful-collections/index.html",
        builder.build_page(builder.compile_demo_bundles()),
    )
