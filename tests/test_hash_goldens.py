"""Golden hash pins (dev-only).

Every other hash assertion in the suite is `X == X` computed in one process, so a change to the
hash algorithm or the `_stable_json` canonicalization would ship green and silently rotate the
contract/artifact identity that downstream consumers pin. These frozen literals are the regression
net: a deliberate change to sha256, `sort_keys`/`separators`, or the contract normalization must
UPDATE the literal here -- that update is the intended human signal.

Only byte-stable, content-controlled surfaces are pinned. No file-read/artifact hash is pinned --
those are CRLF-sensitive on Windows vs LF on CI; `bytes_hash` (which `file_hash` calls on the raw
bytes) pins the algorithm they share.
"""

from __future__ import annotations

import sys
from pathlib import Path

from viewspec.local_tools_hash import bytes_hash, source_hash
from viewspec.state_ir import _hash_json, _stable_json, state_contract_hash

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_bundle import _stateful_app_bundle  # noqa: E402
from test_app_visibility import _visibility_app_bundle  # noqa: E402

# A fixed nested value with intentionally-unsorted keys and mixed scalar types, so the golden pins
# sort_keys (top-level and nested) + separators + the JSON scalar rendering.
_FIXED = {"b": 1, "a": [3, 2], "z": {"y": 1, "x": 2}, "n": None, "bool": True}


def test_sha256_algorithm_is_pinned():
    # bytes_hash / source_hash are sha256; file_hash = bytes_hash(read_bytes) shares this algorithm.
    assert bytes_hash(b"viewspec") == "d932d68ca67b034926857d03efde628cde885c3defbccdc617021222efb4bb86"
    assert source_hash("viewspec") == "d932d68ca67b034926857d03efde628cde885c3defbccdc617021222efb4bb86"


def test_stable_json_canonicalization_is_pinned():
    # Keys sorted (top-level and nested), compact separators, lowercase true/null.
    assert _stable_json(_FIXED) == '{"a":[3,2],"b":1,"bool":true,"n":null,"z":{"x":2,"y":1}}'


def test_canonical_json_hash_is_pinned():
    assert _hash_json(_FIXED) == "f58408b3e0d3944f0bd616d4eab5f59a39d27ab0cbfbb8cc7dcdf79bf0f77708"


def test_state_contract_hash_is_pinned():
    # End-to-end contract identity (normalize_state_ir -> _stable_json -> sha256) that provenance
    # consumers pin. A deliberate change to the shared fixture or the normalization updates this.
    assert (
        state_contract_hash(_stateful_app_bundle())
        == "292554461c2c9dc7ea48c250422f4edd24c0607557b2d273ec796bdd8c3295fa"
    )


def test_v4_state_contract_hash_is_pinned():
    # V4 normalization (visibility rules + expect_visibility_ids in the contract). The v3 pin above
    # must NEVER change because of visibility work; this pin moves only on deliberate v4 contract
    # normalization changes.
    assert (
        state_contract_hash(_visibility_app_bundle())
        == "cb2f9b5dd73b15b228afe1bf55f6421e217064096c6af5f33610a2525ccebb5a"
    )
