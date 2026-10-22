"""Golden hash pins (dev-only).

Every other hash assertion in the suite is `X == X` computed in one process, so a change to the
hash algorithm or the `_stable_json` canonicalization would ship green and silently rotate the
contract/artifact identity that downstream consumers pin. These frozen literals are the regression
net: a deliberate change to sha256, `sort_keys`/`separators`, or the contract normalization must
UPDATE the literal here -- that update is the intended human signal.

Only byte-stable, content-controlled surfaces are pinned here; `bytes_hash` (which `file_hash` calls
on the raw bytes) pins the algorithm they share. The emitter output itself -- `artifact_hash`,
`semantic_digest` and the sha256 of every emitted file for the conformance corpus -- is pinned in
tests/test_corpus_goldens.py against tests/fixtures/corpus_goldens.json (the writers emit LF on
every platform, so those hashes are not CRLF-sensitive); regenerate that file with
`python scripts/update_corpus_goldens.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from viewspec._canonical import (
    canonical_json_ascii,
    canonical_json_ascii_bytes,
    canonical_json_utf8,
    canonical_json_utf8_bytes,
    canonical_sha256,
)
from viewspec.local_tools_hash import bytes_hash, source_hash
from viewspec.state_ir import _hash_json, _stable_json, state_contract_hash

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_bundle import _stateful_app_bundle  # noqa: E402
from test_app_visibility import _visibility_app_bundle  # noqa: E402

# A fixed nested value with intentionally-unsorted keys and mixed scalar types, so the golden pins
# sort_keys (top-level and nested) + separators + the JSON scalar rendering.
_FIXED = {"b": 1, "a": [3, 2], "z": {"y": 1, "x": 2}, "n": None, "bool": True}

# Non-ASCII text, a fraction, an integer above 2**53, an exponent float, negative zero and the
# characters JSON must always escape. The two canonical forms differ only in the non-ASCII rendering.
_UNICODE = {"b": "Müller", "a": ["Zoë", 1.5, 10**20, 1e21, -0.0, ' \t"\\'], "z": {"y": None, "x": True}}
_SHA256_VIEWSPEC = "d932d68ca67b034926857d03efde628cde885c3defbccdc617021222efb4bb86"


def test_sha256_algorithm_is_pinned():
    # bytes_hash / source_hash are sha256; file_hash = bytes_hash(read_bytes) shares this algorithm.
    assert bytes_hash(b"viewspec") == _SHA256_VIEWSPEC
    assert source_hash("viewspec") == _SHA256_VIEWSPEC


def test_canonical_sha256_is_pinned():
    # canonical_sha256 hashes str as UTF-8 and bytes as given, with the same sha256 as bytes_hash.
    assert canonical_sha256("viewspec") == _SHA256_VIEWSPEC
    assert canonical_sha256(b"viewspec") == _SHA256_VIEWSPEC
    assert (
        canonical_sha256(canonical_json_ascii(_UNICODE))
        == "c2a498327ba48494d7b268c8c65b9f27be2a7fed52d7589c4f7094d1f6e7729e"
    )
    assert (
        canonical_sha256(canonical_json_utf8(_UNICODE))
        == "d28ddbc8c25487800d9955879cb53802a33e48c08cdfcc0f8aabab765dc9f745"
    )


def test_canonical_json_ascii_form_is_pinned():
    # ensure_ascii=True: non-ASCII text becomes \uXXXX escapes, so the text is pure ASCII.
    assert canonical_json_ascii(_FIXED) == '{"a":[3,2],"b":1,"bool":true,"n":null,"z":{"x":2,"y":1}}'
    assert canonical_json_ascii(_UNICODE) == (
        '{"a":["Zo\\u00eb",1.5,100000000000000000000,1e+21,-0.0,"\\u2028\\t\\"\\\\"],'
        '"b":"M\\u00fcller","z":{"x":true,"y":null}}'
    )
    assert canonical_json_ascii_bytes(_UNICODE) == canonical_json_ascii(_UNICODE).encode("ascii")


def test_canonical_json_utf8_form_is_pinned():
    # ensure_ascii=False: non-ASCII text is written as itself; JSON's mandatory escapes still apply.
    assert canonical_json_utf8(_FIXED) == '{"a":[3,2],"b":1,"bool":true,"n":null,"z":{"x":2,"y":1}}'
    assert canonical_json_utf8(_UNICODE) == (
        '{"a":["Zoë",1.5,100000000000000000000,1e+21,-0.0," \\t\\"\\\\"],"b":"Müller","z":{"x":true,"y":null}}'
    )
    assert canonical_json_utf8_bytes(_UNICODE) == (
        b'{"a":["Zo\xc3\xab",1.5,100000000000000000000,1e+21,-0.0,"\xe2\x80\xa8\\t\\"\\\\"],'
        b'"b":"M\xc3\xbcller","z":{"x":true,"y":null}}'
    )


def test_stable_json_canonicalization_is_pinned():
    # Keys sorted (top-level and nested), compact separators, lowercase true/null.
    assert _stable_json(_FIXED) == '{"a":[3,2],"b":1,"bool":true,"n":null,"z":{"x":2,"y":1}}'
    # The state contract uses the UTF-8 form; moving it to the ASCII form would rotate every pin below.
    assert _stable_json is canonical_json_utf8


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
