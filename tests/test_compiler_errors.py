"""Compiler invariant and local-tool contract failures expose their registry code (audit Q-8).

``str(exc)`` keeps the exact ``CODE: message`` text that existing callers match on, while
``exc.code`` and ``exc.message`` make string parsing unnecessary. Each class keeps its historical
base so that ``except RuntimeError`` / ``except ValueError`` callers and the CLI exit codes do not
change.
"""

from __future__ import annotations

import pytest

from viewspec import cli, compiler, local_tools_validators
from viewspec.compiler_errors import CompilerInternalError, LocalToolContractError, StableCodeError
from viewspec.error_codes import ERROR_CODES
from viewspec.local_tools_constants import SEMANTIC_DIGEST_MAX_PROJECTION_BYTES
from viewspec.types import IRNode


def test_stable_code_error_shape():
    exc = CompilerInternalError("PLANNER_ROLE_METADATA_ONLY", "unsupported product role 'x'.")
    assert str(exc) == "PLANNER_ROLE_METADATA_ONLY: unsupported product role 'x'."
    assert exc.code == "PLANNER_ROLE_METADATA_ONLY"
    assert exc.message == "unsupported product role 'x'."
    assert isinstance(exc, (StableCodeError, RuntimeError))
    assert not isinstance(exc, ValueError)

    exc = LocalToolContractError("TAILWIND_IMPORT_NOT_SUPPORTED", "Raw HTML import only supports --target html-tailwind")
    assert str(exc) == "TAILWIND_IMPORT_NOT_SUPPORTED: Raw HTML import only supports --target html-tailwind"
    assert exc.code == "TAILWIND_IMPORT_NOT_SUPPORTED"
    assert isinstance(exc, (StableCodeError, ValueError))
    assert not isinstance(exc, RuntimeError)


def test_compiler_internal_error_is_reachable_from_compiler_module():
    assert compiler.CompilerInternalError is CompilerInternalError


def test_planner_role_assignment_exposes_code():
    node = IRNode(id="n", primitive="stack")
    with pytest.raises(CompilerInternalError) as info:
        compiler._assign_product_role_v1(node, "not_a_role")
    assert info.value.code == "PLANNER_ROLE_METADATA_ONLY"
    assert str(info.value) == "PLANNER_ROLE_METADATA_ONLY: unsupported product role 'not_a_role'."
    with pytest.raises(RuntimeError):
        compiler._assign_product_role_v1(node, "not_a_role")


def test_aesthetic_layout_drift_exposes_code():
    # content_grid must be a grid node under every layout profile; a stack with that role is drift.
    root = IRNode(id="root", primitive="stack", props={"product_role": "content_grid"})
    with pytest.raises(CompilerInternalError) as info:
        compiler._apply_aesthetic_profile_layout_v1(root, "aesthetic.premium_saas")
    assert info.value.code == "AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT"
    assert info.value.message == "product role 'content_grid' no longer maps to a grid node."
    assert str(info.value) == f"{info.value.code}: {info.value.message}"


def test_duplicate_collection_action_bar_exposes_code():
    row_id = "planner_tasks_collection_actions"
    wrapper = IRNode(id="wrapper", primitive="stack")
    parent = IRNode(
        id="parent",
        primitive="stack",
        children=[IRNode(id=row_id, primitive="stack"), IRNode(id=row_id, primitive="stack"), wrapper],
    )
    with pytest.raises(CompilerInternalError) as info:
        compiler._collection_action_bar_for_motif_v1(parent, wrapper, "tasks")
    assert info.value.code == "COLLECTION_ACTION_BAR_DUPLICATE"
    assert str(info.value) == "COLLECTION_ACTION_BAR_DUPLICATE: duplicate action bar for collection motif tasks."


def test_semantic_projection_size_exposes_code():
    projection = {"blob": "x" * (SEMANTIC_DIGEST_MAX_PROJECTION_BYTES + 1)}
    with pytest.raises(LocalToolContractError) as info:
        local_tools_validators._assert_semantic_projection_size(projection, "manifest_projection")
    assert info.value.code == "SEMANTIC_DIGEST_FIELD_FORBIDDEN"
    assert str(info.value) == "SEMANTIC_DIGEST_FIELD_FORBIDDEN: manifest_projection exceeds 128 KiB"
    # The validator collects these through `except ValueError`, so the ValueError base must stay.
    with pytest.raises(ValueError):
        local_tools_validators._assert_semantic_projection_size(projection, "manifest_projection")


def test_unsupported_tsx_shape_exposes_code():
    with pytest.raises(LocalToolContractError) as info:
        local_tools_validators._semantic_tsx_source_nodes("export const x = 1;", primitive_by_dom_id={})
    assert info.value.code == "TAILWIND_TSX_SHAPE_UNSUPPORTED"
    assert info.value.message == "ViewSpecView.tsx render shape is unsupported"

    tsx = "return (\n  <div id={\"a\"} data-ir-id={bad}>x</div>\n);\n"
    with pytest.raises(LocalToolContractError) as info:
        local_tools_validators._semantic_tsx_source_nodes(tsx, primitive_by_dom_id={})
    assert info.value.code == "TAILWIND_TSX_SHAPE_UNSUPPORTED"


def test_cli_raw_html_import_target_exposes_code(tmp_path):
    html_path = tmp_path / "input.html"
    html_path.write_text("<main><h1>Hello</h1></main>", encoding="utf-8")
    args = cli._build_parser().parse_args(
        ["compile", str(html_path), "--target", "react-tailwind-tsx", "--out", str(tmp_path / "out")]
    )
    with pytest.raises(LocalToolContractError) as info:
        args.func(args)
    assert info.value.code == "TAILWIND_IMPORT_NOT_SUPPORTED"
    # main() still reports it through the ValueError path: "error: CODE: message" and exit 2.
    assert cli.main(["compile", str(html_path), "--target", "react-tailwind-tsx", "--out", str(tmp_path / "out")]) == 2


def test_every_code_raised_here_is_registered():
    for code in (
        "PLANNER_ROLE_METADATA_ONLY",
        "AESTHETIC_PROFILE_LAYOUT_ROLE_DRIFT",
        "COLLECTION_ACTION_BAR_DUPLICATE",
        "SEMANTIC_DIGEST_FIELD_FORBIDDEN",
        "TAILWIND_TSX_SHAPE_UNSUPPORTED",
        "TAILWIND_IMPORT_NOT_SUPPORTED",
    ):
        assert code in ERROR_CODES
