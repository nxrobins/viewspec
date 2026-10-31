"""Documentation cites only error codes that exist in the closed registry.

`docs/compatibility.md` describes `viewspec.error_codes.ERROR_CODES` as a closed registry, so a document that
names a code which the registry does not contain is wrong. Every backticked
UPPER_SNAKE token in README.md and docs/*.md that looks like a registry code must be a member of
the registry, unless it is listed in the explicit allowlist of documented non-registry identifiers
below. A token "looks like a registry code" when its first segment is a registered code prefix
(`A11Y_`, `APP_`, `HOST_`, `INTENT_`, ...) or its last segment is a
registry-style suffix (`_FAILED`, `_MISSING`, `_INVALID`, ...).
"""

from __future__ import annotations

import re
from pathlib import Path

import viewspec
from viewspec.error_codes import ERROR_CODES

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
BACKTICKED_UPPER_SNAKE_RE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")

# Environment variables follow the VIEWSPEC_* convention and are never registry codes.
ENVIRONMENT_VARIABLE_PREFIX = "VIEWSPEC_"

# Documented identifiers that look like registry codes but are deliberately not emitted error
# codes. Each entry says why, and test_allowlist_entries_are_accurate checks the reason stays true.
PUBLIC_CONSTANTS: set[str] = set()
REPOSITORY_SCRIPT_CODES = {
    # Emitted by the repository's own Node checks, which are not part of the Python package.
    "HOST_VERIFY_DOCS_OVERCLAIM": ROOT / "tests/react-tailwind-host/scripts/verify.mjs",
    "PUBLIC_FACTS_DRIFT": ROOT / "tests/seo_static_smoke.mjs",
}
# Tokens that match neither a registry prefix nor a registry suffix need no allowlist entry; the
# heuristic already skips them.
ALLOWED_NON_REGISTRY_TOKENS = PUBLIC_CONSTANTS | set(REPOSITORY_SCRIPT_CODES)

REGISTRY_PREFIXES = frozenset(code.split("_", 1)[0] for code in ERROR_CODES)
REGISTRY_SUFFIXES = frozenset(code.rsplit("_", 1)[-1] for code in ERROR_CODES)


def _backticked_tokens() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for match in BACKTICKED_UPPER_SNAKE_RE.finditer(text):
            found.setdefault(match.group(1), set()).add(path.relative_to(ROOT).as_posix())
    return found


def _looks_like_registry_code(token: str) -> bool:
    if token.startswith(ENVIRONMENT_VARIABLE_PREFIX):
        return False
    return token.split("_", 1)[0] in REGISTRY_PREFIXES or token.rsplit("_", 1)[-1] in REGISTRY_SUFFIXES


def test_docs_cite_only_registered_error_codes() -> None:
    offenders = {
        token: sorted(paths)
        for token, paths in _backticked_tokens().items()
        if _looks_like_registry_code(token) and token not in ERROR_CODES and token not in ALLOWED_NON_REGISTRY_TOKENS
    }
    assert not offenders, (
        "Documentation names codes that are not in ERROR_CODES. Either fix the document to "
        "name the code that fires, register the code in src/viewspec/error_codes.py, or add a "
        f"documented non-registry identifier to the allowlist in this test: {offenders}"
    )


def test_scan_finds_known_registry_codes() -> None:
    # Guards the regex and file set: these codes are documented and registered, so the scan must see them.
    tokens = _backticked_tokens()
    for expected, path in (
        ("A11Y_CONTRAST_BELOW_AA", "docs/known-limits-a11y.md"),
        ("HOST_VERIFY_TIMEOUT", "docs/known-limits-react-tailwind-tsx.md"),
    ):
        assert expected in ERROR_CODES
        assert path in tokens.get(expected, set()), f"{expected} not found in {path}"
    assert "A11Y_UNRESOLVABLE_BACKGROUND" not in tokens


def test_allowlist_entries_are_accurate() -> None:
    tokens = _backticked_tokens()
    for token in sorted(ALLOWED_NON_REGISTRY_TOKENS):
        assert token not in ERROR_CODES, f"{token} is now registered; remove it from the allowlist"
        assert token in tokens, f"{token} is no longer cited in the docs; remove it from the allowlist"
        assert _looks_like_registry_code(token), (
            f"{token} no longer looks like a registry code; the allowlist entry is unnecessary"
        )
    for token in sorted(PUBLIC_CONSTANTS):
        assert token in viewspec.__all__, f"{token} is documented as a public constant but is not exported"
    for token, script in REPOSITORY_SCRIPT_CODES.items():
        assert token in script.read_text(encoding="utf-8"), (
            f"{token} is no longer emitted by {script.relative_to(ROOT)}"
        )
