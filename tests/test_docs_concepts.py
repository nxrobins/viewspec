"""Every complete example in docs/concepts passes `viewspec check` and needs no fix.

A fenced block whose info string is ``json viewspec`` holds a complete document: a canonical or
compact IntentBundle. Each concept page carries exactly one, and the test checks it as a file with
the public `check()`, so the page and the contract cannot drift apart. A JSON block that holds a
whole document must carry the marker, so no complete example escapes the check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from viewspec import check

ROOT = Path(__file__).resolve().parents[1]
CONCEPTS = ROOT / "docs" / "concepts"
PAGES = ("substrate", "binding", "motif", "region", "compact-form", "proof")
FENCE_RE = re.compile(r"^```([^\n]*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
COMPLETE_MARKER = "json viewspec"


def _fences(page: Path) -> list[tuple[str, str]]:
    return [(info.strip(), body) for info, body in FENCE_RE.findall(page.read_text(encoding="utf-8"))]


def _complete_examples() -> list[object]:
    return [
        pytest.param(page.name, body, id=page.stem)
        for page in sorted(CONCEPTS.glob("*.md"))
        for info, body in _fences(page)
        if info == COMPLETE_MARKER
    ]


def test_each_concept_page_has_one_complete_example() -> None:
    assert {page.stem for page in CONCEPTS.glob("*.md")} >= set(PAGES)
    for page in sorted(CONCEPTS.glob("*.md")):
        markers = [info for info, _body in _fences(page) if info == COMPLETE_MARKER]
        assert len(markers) == 1, f"{page.name} needs exactly one ```{COMPLETE_MARKER} block"


def test_unmarked_json_blocks_are_not_complete_documents() -> None:
    for page in sorted(CONCEPTS.glob("*.md")):
        for info, body in _fences(page):
            if info == COMPLETE_MARKER or not info.startswith("json"):
                continue
            try:
                document = json.loads(body)
            except json.JSONDecodeError:
                continue
            is_document = isinstance(document, dict) and (
                {"substrate", "view_spec"} <= document.keys() or document.get("format") == "compact"
            )
            assert not is_document, f"{page.name} has a complete document without the {COMPLETE_MARKER} marker"


@pytest.mark.parametrize(("page", "text"), _complete_examples())
def test_complete_example_passes_check_without_fixes(tmp_path: Path, page: str, text: str) -> None:
    json.loads(text)
    source = tmp_path / "example.json"
    source.write_text(text, encoding="utf-8")

    report = check(source)

    assert report["ok"], (page, report.get("issues"))
    assert report["issues"] == [], page
    assert report["fixes"] == [], (page, report["fixes"])
