"""The error-code registry is closed and enforced two-way against src/viewspec.

Direction one: every UPPER_SNAKE code emitted anywhere in src/viewspec must be listed in
viewspec.error_codes.ERROR_CODES. Direction two: every registered code must still be
emitted by src/viewspec, so removed codes cannot silently linger in the public registry.
"""

import ast
import re
from pathlib import Path

from viewspec.error_codes import ERROR_CODES

ROOT = Path(__file__).resolve().parents[1] / "src" / "viewspec"
TOKEN_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
CODE_SHAPE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

# Generated protobuf output and the internal benchmark oracle (which carries its own
# BENCHMARK_* code registry) are not part of the public tool-surface contract.
SKIP_FILES = {"viewspec_pb2.py", "compiler_benchmarks.py"}

# UPPER_SNAKE string literals in src/viewspec that are not emitted codes.
NON_CODE_LITERAL_TOKENS = {
    "APP_PROOF",  # the APP_PROOF.md summary filename
    "APP_SCREEN_INTENT",  # startswith() prefix over APP_SCREEN_INTENT_* codes
    "ARTIFACT_DIR",  # CLI usage/metavar text
    "CHROMIUM_ARIAL_LAYOUT_FIT_TOLERANCE_PX",  # generated Pretext runtime constant
    "CLIPPING_OVERFLOW_VALUES",  # generated Pretext runtime constant
    "EMPTY_SHA256",  # generated Pretext runtime constant
    "FONT_READY_TIMEOUT_MS",  # generated Pretext runtime constant
    "GENERIC_FONT_FAMILIES",  # generated Pretext runtime constant
    "HOST_VERIFY",  # regex fragment matching HOST_VERIFY_* codes
    "LINE_TOP_EPSILON_PX",  # generated Pretext runtime constant
    "LOCALE_RE",  # generated Pretext runtime constant
    "MAX_CACHE_ENTRIES",  # generated Pretext runtime constant
    "MAX_ID_LENGTH",  # generated Pretext runtime constant
    "MAX_SURFACES",  # generated Pretext runtime constant
    "MAX_TEXT_BYTES",  # generated Pretext runtime constant
    "MAX_TOTAL_TEXT_BYTES",  # generated Pretext runtime constant
    "O_NOFOLLOW",  # optional os module flag used to reject symlink lock files
    "OVERFLOW_EPSILON_PX",  # generated Pretext runtime constant
    "PX_RE",  # generated Pretext runtime constant
    "SAFE_ID_RE",  # generated Pretext runtime constant
    "SUPPORTED_FONT_FAMILY",  # generated Pretext runtime constant
    "TOO_MANY",  # startswith() prefix over TOO_MANY_* codes
    "UNSAFE_SYSTEM_FAMILIES",  # generated Pretext runtime constant
    "VIEWSPEC_HOST_VERIFY_BASE_URL",  # environment variable name
    "VIEWSPEC_HOST_VERIFY_BROWSER_REPORT",  # environment variable name
    "VIEWSPEC_HOST_VERIFY_BROWSER_REPORT_DIR",  # environment variable name
    "VIEWSPEC_HOST_VERIFY_EVIDENCE_DIR",  # environment variable name
    "VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR",  # environment variable name
    "VIEWSPEC_HOST_VERIFY_PLAN_JSON",  # environment variable name
    "VIEWSPEC_REVIEW_STATE_DIR",  # environment variable name
    "VIEWSPEC_STATE_PROFILE",  # export symbol in the generated reducer artifact
}


def _skipped_constant_ids(tree: ast.Module) -> set[int]:
    """Constants that are not code-bearing: docstrings and module __all__ name lists."""
    marked: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                marked.add(id(body[0].value))
    for stmt in tree.body:
        targets = (
            stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AugAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Constant):
                        marked.add(id(sub))
    return marked


def _scanned_tokens() -> set[str]:
    tokens: set[str] = set()
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in SKIP_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip_ids = _skipped_constant_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip_ids:
                tokens.update(TOKEN_RE.findall(node.value))
    return tokens


def test_every_emitted_code_is_registered():
    unregistered = _scanned_tokens() - NON_CODE_LITERAL_TOKENS - ERROR_CODES
    assert not unregistered, (
        "src/viewspec emits codes missing from viewspec.error_codes.ERROR_CODES "
        f"(register them, or add true non-codes to NON_CODE_LITERAL_TOKENS): {sorted(unregistered)}"
    )


def test_every_registered_code_is_still_emitted():
    stale = ERROR_CODES - _scanned_tokens()
    assert not stale, f"ERROR_CODES lists codes no longer emitted by src/viewspec: {sorted(stale)}"


def test_exclusion_list_is_not_stale():
    missing = NON_CODE_LITERAL_TOKENS - _scanned_tokens()
    assert not missing, f"NON_CODE_LITERAL_TOKENS entries no longer appear in src/viewspec: {sorted(missing)}"


def test_registry_shape():
    assert ERROR_CODES, "ERROR_CODES must not be empty"
    assert len(ERROR_CODES) >= 350, "ERROR_CODES shrank suspiciously; the registry should be the full closed set"
    malformed = {code for code in ERROR_CODES if not CODE_SHAPE_RE.match(code)}
    assert not malformed, f"ERROR_CODES entries must be UPPER_SNAKE identifiers: {sorted(malformed)}"
    overlap = ERROR_CODES & NON_CODE_LITERAL_TOKENS
    assert not overlap, f"tokens cannot be both registered and excluded: {sorted(overlap)}"
