"""How compile(design=...) and load_design_system() take a DESIGN.md (audit C-9).

A ``str`` passed as ``design`` is DESIGN.md content. File paths are an explicit input as an
``os.PathLike``; a single-line ``str`` that names an existing file still reads that file, but warns
because it relies on guessing. ``load_design_system(lint=...)`` never had a local effect, so passing it
warns too.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from viewspec import DesignRequest, DesignSystemError, ViewSpecBuilder, compile, load_design_system

DESIGN = """---
name: Path Or Content
colors:
  primary: "#123456"
  secondary: "#654321"
---
"""

WARNING_DESIGN = """---
name: Warns
colors:
  primary: "red"
---
"""


def _bundle():
    builder = ViewSpecBuilder("design_argument")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")
    return builder.build_bundle()


class _StrPathLike:
    """An os.PathLike that is not a pathlib.Path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __fspath__(self) -> str:
        return str(self._path)


@pytest.fixture
def design_file(tmp_path: Path) -> Path:
    path = tmp_path / "DESIGN.md"
    path.write_text(DESIGN, encoding="utf-8")
    return path


def test_compile_reads_a_pathlike_design_as_a_file_without_warning(design_file):
    from_content = compile(_bundle(), design=DESIGN)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from_path = compile(_bundle(), design=design_file)
        from_pathlike = compile(_bundle(), design=_StrPathLike(design_file))

    assert "#123456" in from_path.style_values["tone.neutral"]
    assert from_path.style_values == from_content.style_values
    assert from_pathlike.style_values == from_content.style_values
    assert from_path.result.root == from_content.result.root


def test_compile_str_naming_an_existing_file_is_read_as_a_path_with_a_deprecation_warning(design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)
    expected = compile(_bundle(), design=design_file)

    # The exact call from the audit: a relative path in a str.
    with pytest.warns(DeprecationWarning, match=r"Pass pathlib\.Path\(\.\.\.\) for a file") as caught:
        ast = compile(_bundle(), design="DESIGN.md")

    assert ast.style_values == expected.style_values
    assert len(caught) == 1
    # stacklevel points the warning at the caller of compile(), not at the compiler internals.
    assert caught[0].filename == __file__


def test_compile_str_with_line_breaks_stays_content_without_warning(design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ast = compile(_bundle(), design=DESIGN)

    assert "#123456" in ast.style_values["tone.neutral"]


@pytest.mark.parametrize("design", ["missing-DESIGN.md", "name: Inline", "", "x" * 5000, "nul\x00byte"])
def test_compile_single_line_str_that_names_no_file_is_still_parsed_as_content(design, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DesignSystemError, match="YAML front matter"):
            compile(_bundle(), design=design)


def test_compile_str_naming_a_directory_is_still_parsed_as_content(tmp_path, monkeypatch):
    (tmp_path / "DESIGN.md").mkdir()
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DesignSystemError, match="YAML front matter"):
            compile(_bundle(), design="DESIGN.md")


def test_compile_pathlike_design_honours_strict_design_and_reports_the_path(tmp_path):
    path = tmp_path / "WARN.md"
    path.write_text(WARNING_DESIGN, encoding="utf-8")

    compile(_bundle(), design=path)
    with pytest.raises(DesignSystemError, match="strict") as info:
        compile(_bundle(), design=path, strict_design=True)

    assert info.value.report is not None
    assert info.value.report.source == str(path)


def test_compile_missing_pathlike_design_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        compile(_bundle(), design=tmp_path / "missing.md")


def test_compile_rejects_other_design_types_and_names_the_path_form():
    with pytest.raises(TypeError, match="os.PathLike"):
        compile(_bundle(), design=b"---\n---\n")  # type: ignore[arg-type]


@pytest.mark.parametrize("lint", [True, False])
def test_load_design_system_lint_argument_is_deprecated_and_has_no_effect(lint):
    expected = load_design_system(content=WARNING_DESIGN)

    with pytest.warns(DeprecationWarning, match=r"lint=\.\.\.\) is deprecated and has no effect") as caught:
        context = load_design_system(content=WARNING_DESIGN, lint=lint)

    assert caught[0].filename == __file__
    assert context.to_meta() == expected.to_meta()
    assert context.style_values == expected.style_values
    assert [finding.code for finding in context.lint_report.findings] == ["DESIGN_COLOR_FORMAT_WARNING"]


def test_load_design_system_lint_false_does_not_skip_fatal_errors():
    with pytest.warns(DeprecationWarning):
        with pytest.raises(DesignSystemError):
            load_design_system(content='---\nname: Broken\ncolors:\n  primary: "{colors.missing}"\n---\n', lint=False)


def test_load_design_system_without_lint_does_not_warn(design_file):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_design_system(content=DESIGN)
        load_design_system(path=design_file)
        load_design_system(path=str(design_file))


@pytest.mark.parametrize("lint", [True, False])
def test_compile_with_a_design_request_ignores_its_hosted_lint_flag_without_warning(lint):
    expected = compile(_bundle(), design=DESIGN)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ast = compile(_bundle(), design=DesignRequest(content=DESIGN, lint=lint))

    assert ast.style_values == expected.style_values
