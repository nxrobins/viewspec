"""Canonical JSON encodings shared by the contract digests, byte limits and signature checks.

Two canonical forms are in use across the package. Both sort keys at every level, use compact
``(",", ":")`` separators and reject NaN and Infinity (``allow_nan=False``), so a non-finite float
raises ``ValueError`` instead of emitting the non-JSON tokens ``NaN`` or ``Infinity``. They differ
only in how non-ASCII text is written:

* :func:`canonical_json_ascii` escapes non-ASCII characters as ``\\uXXXX`` (``ensure_ascii=True``).
  Used by the verification, repair and convergence contracts, the presentation-plan and
  semantic-digest projections, Pretext coverage digests and every hosted contract (artifacts,
  receipts, AppBundle builds, hosted verification).
* :func:`canonical_json_utf8` writes non-ASCII text as itself (``ensure_ascii=False``). Used by
  the state-IR contract hash, intent diffs, AppBundle validation, intent patches, converge
  sessions and the Review V0 contract.

Each consuming module keeps a thin alias of the form it already used, so every existing digest
stays byte-identical. A few digests keep their own serialization (the agent-eval checkpoints,
the proof report hashes, and some inline identity digests); CHANGELOG.md lists them under C-5. Both forms are pinned against literals in tests/test_hash_goldens.py, and
tests/test_canonical.py pins which module uses which form. Converging the two forms would rotate
digests and is a deliberate canonical-form change, not a consolidation; it is not done here.
"""

from __future__ import annotations

import hashlib
import json


def canonical_json_ascii(value: object) -> str:
    """Return ``value`` as compact, key-sorted JSON with non-ASCII text escaped as ``\\uXXXX``.

    Raises ``ValueError`` for NaN or Infinity and ``TypeError`` for values JSON cannot encode.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_json_utf8(value: object) -> str:
    """Return ``value`` as compact, key-sorted JSON with non-ASCII text written as itself.

    Raises ``ValueError`` for NaN or Infinity and ``TypeError`` for values JSON cannot encode.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_json_ascii_bytes(value: object) -> bytes:
    """UTF-8 bytes of :func:`canonical_json_ascii` (pure ASCII, so identical to the ASCII encoding)."""
    return canonical_json_ascii(value).encode("utf-8")


def canonical_json_utf8_bytes(value: object) -> bytes:
    """UTF-8 bytes of :func:`canonical_json_utf8`."""
    return canonical_json_utf8(value).encode("utf-8")


def canonical_sha256(data: str | bytes) -> str:
    """Hex SHA-256 of canonical text (encoded as UTF-8) or of already-encoded bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "canonical_json_ascii",
    "canonical_json_ascii_bytes",
    "canonical_json_utf8",
    "canonical_json_utf8_bytes",
    "canonical_sha256",
]
