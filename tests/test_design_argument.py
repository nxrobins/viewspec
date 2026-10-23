"""How compile(design=...) and load_design_system() take a DESIGN.md (audit C-9).

A ``str`` passed as ``design`` is DESIGN.md content and never touches the filesystem: compile() does
not open it, read it or check whether a file by that name exists, so a ``str`` from an untrusted
source can neither make compile() read a local file nor reveal which local paths exist. File paths
are an explicit input as an ``os.PathLike``. A single-line ``str`` can never be valid content: it
fails with the same ``DesignSystemError`` it always raised, now with a note that says to pass
``pathlib.Path``, and the error is the same whether or not a file by that name exists.
``load_design_system(lint=...)`` never had a local effect, so passing it emits a
``DeprecationWarning``.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from viewspec import DesignRequest, DesignSystemError, ViewSpecBuilder, compile, design_md, load_design_system
from viewspec.design_md import DESIGN_MAX_BYTES

PATH_NOTE = (
    "compile(design=...) parses a str as DESIGN.md content, never as a file path. "
    "Pass pathlib.Path(...) for a file, or a DesignSystemContext from load_design_system(path=...)."
)
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


# Every way compile() could stat, test for or open a path named by a str.
_FILESYSTEM_FUNCTIONS = (
    (os, ("stat", "lstat", "open", "access", "listdir", "scandir")),
    (os.path, ("exists", "lexists", "isfile", "isdir", "islink", "getsize")),
    (builtins, ("open",)),
    (io, ("open", "open_code")),
    (Path, ("stat", "lstat", "open", "exists", "is_file", "is_dir", "read_text", "read_bytes")),
)


@contextlib.contextmanager
def _no_filesystem_access() -> Iterator[list[str]]:
    """Fail on, and record, every stat, existence check or open of a path.

    It covers os, os.path, io, builtins and pathlib. Recording as well as raising catches a call
    whose AssertionError the code under test swallows.
    """
    touched: list[str] = []

    def forbidden(name: str) -> Callable[..., object]:
        def call(*args: object, **_kwargs: object) -> object:
            touched.append(f"{name}{args!r}")
            raise AssertionError(f"compile() touched the filesystem: {name}{args!r}")

        return call

    with pytest.MonkeyPatch.context() as patch:
        for owner, names in _FILESYSTEM_FUNCTIONS:
            for name in names:
                patch.setattr(owner, name, forbidden(f"{owner.__name__}.{name}"))
        yield touched


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


def test_compile_str_naming_an_existing_file_is_still_parsed_as_content(design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)

    # The exact call from the audit: a relative path in a str. It fails as it always did, because
    # the str is content. Only the exception note points at pathlib.Path; there is no warning,
    # because telling an existing file apart would mean checking the filesystem.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DesignSystemError, match="must begin with YAML front matter") as info:
            compile(_bundle(), design="DESIGN.md")

    assert info.value.report is not None
    assert info.value.report.source == "<content>"
    assert info.value.__notes__ == [PATH_NOTE]


def _binary_file(tmp_path: Path) -> Path:
    path = tmp_path / "binary.md"
    path.write_bytes(bytes(range(256)) * 2)
    return path


def _oversized_file(tmp_path: Path) -> Path:
    path = tmp_path / "huge.md"
    path.write_text(DESIGN + "x" * (DESIGN_MAX_BYTES + 1), encoding="utf-8")
    return path


def _readable_design_file(tmp_path: Path) -> Path:
    path = tmp_path / "other-tenant" / "DESIGN.md"
    path.parent.mkdir()
    path.write_text(DESIGN, encoding="utf-8")
    return path


@pytest.mark.parametrize("make_file", [_binary_file, _oversized_file, _readable_design_file])
def test_compile_never_reads_a_file_named_by_a_str(make_file, tmp_path, monkeypatch):
    # A service that forwards a request's DESIGN.md text to compile() and catches
    # DesignSystemError must get that error for any str: never a decode or OS error from reading
    # the named file, never the file's size, and never the file's tokens in the output.
    path = make_file(tmp_path)

    def no_file_reads(*_args, **_kwargs):
        raise AssertionError("compile() read a file named by a str design")

    monkeypatch.setattr(design_md, "_read_design_text", no_file_reads)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with _no_filesystem_access() as touched:
            with pytest.raises(DesignSystemError) as info:
                compile(_bundle(), design=str(path))

    assert touched == []
    assert str(info.value) == "DESIGN.md must begin with YAML front matter"
    assert info.value.report is not None
    assert info.value.report.to_json() == {
        "source": "<content>",
        "summary": {"errors": 1, "warnings": 0, "info": 0},
        "findings": [
            {
                "severity": "error",
                "code": "DESIGN_FRONTMATTER_ERROR",
                "path": "$",
                "message": "DESIGN.md must begin with YAML front matter",
            }
        ],
    }


def test_compile_str_error_does_not_reveal_whether_the_file_exists(design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)
    # Warnings are errors here, as under `python -W error`: a warning raised only for a str that
    # names an existing file would turn compile() into an existence oracle for any path, even an
    # unreadable one, because stat() needs no read permission.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with _no_filesystem_access() as touched:
            with pytest.raises(DesignSystemError) as existing:
                compile(_bundle(), design="DESIGN.md")
            with pytest.raises(DesignSystemError) as missing:
                compile(_bundle(), design="MISSING.md")

    assert touched == []
    assert str(existing.value) == str(missing.value)
    assert existing.value.report is not None and missing.value.report is not None
    assert existing.value.report.to_json() == missing.value.report.to_json()
    assert existing.value.__notes__ == missing.value.__notes__ == [PATH_NOTE]


def test_compile_multi_line_content_error_has_no_path_note():
    with pytest.raises(DesignSystemError, match="must begin with YAML front matter") as info:
        compile(_bundle(), design="name: Broken\ncolors: {}\n")

    assert not hasattr(info.value, "__notes__")


def test_compile_str_with_line_breaks_stays_content_without_warning(design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with _no_filesystem_access() as touched:
            ast = compile(_bundle(), design=DESIGN)

    assert touched == []
    assert "#123456" in ast.style_values["tone.neutral"]


@pytest.mark.parametrize("design", ["missing-DESIGN.md", "name: Inline", "", "x" * 5000, "nul\x00byte"])
def test_compile_single_line_str_that_names_no_file_is_still_parsed_as_content(design, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DesignSystemError, match="YAML front matter"):
            compile(_bundle(), design=design)


@pytest.mark.parametrize(
    "design",
    [
        "DESIGN.md",
        "/etc/shadow",  # stat() needs no read permission, so even an unreadable file would show
        r"\\fileserver\share\DESIGN.md",  # stat() on a UNC path opens an SMB session on Windows
        "/net/fileserver/DESIGN.md",  # stat() on an autofs path triggers a mount on Linux
        "DESIGN.md\n",  # one line: splitlines() drops a trailing line break
    ],
)
def test_compile_str_never_touches_the_filesystem(design, design_file, monkeypatch):
    monkeypatch.chdir(design_file.parent)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with _no_filesystem_access() as touched:
            with pytest.raises(DesignSystemError, match="must begin with YAML front matter") as info:
                compile(_bundle(), design=design)

    assert touched == []
    assert info.value.__notes__ == [PATH_NOTE]


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
