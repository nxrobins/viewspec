"""Customer-side verification for hosted Ed25519 usage receipts."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


RECEIPT_ALGORITHM = "ed25519"


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ReceiptPublicKey:
    algorithm: str
    key_id: str
    public_key: str

    @classmethod
    def from_json(cls, payload: Any) -> ReceiptPublicKey:
        if not isinstance(payload, Mapping):
            raise ValueError("Receipt public key must be an object")
        algorithm = payload.get("algorithm")
        key_id = payload.get("key_id")
        public_key = payload.get("public_key")
        if algorithm != RECEIPT_ALGORITHM:
            raise ValueError(f"Receipt public key algorithm must be {RECEIPT_ALGORITHM}")
        if not isinstance(key_id, str) or not key_id.startswith("vsk_"):
            raise ValueError("Receipt public key_id is invalid")
        if not isinstance(public_key, str):
            raise ValueError("Receipt public key is missing")
        try:
            key_bytes = _decode(public_key)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Receipt public key is not valid base64url") from exc
        expected_id = f"vsk_{hashlib.sha256(key_bytes).hexdigest()[:16]}"
        if len(key_bytes) != 32 or key_id != expected_id:
            raise ValueError("Receipt public key does not match key_id")
        return cls(algorithm, key_id, public_key)


def verify_signed_receipt(receipt: Any, public_key: ReceiptPublicKey | Mapping[str, Any]) -> bool:
    """Verify any hosted ViewSpec receipt against the API's published public key."""
    try:
        parsed_key = public_key if isinstance(public_key, ReceiptPublicKey) else ReceiptPublicKey.from_json(public_key)
    except (TypeError, ValueError):
        return False
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("payload"), dict):
        return False
    if receipt.get("algorithm") != RECEIPT_ALGORITHM or receipt.get("key_id") != parsed_key.key_id:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise ImportError(
            "cryptography is required for hosted receipt verification. Install it: pip install viewspec[remote]"
        ) from None
    try:
        signature = _decode(str(receipt.get("signature", "")))
        key_bytes = _decode(parsed_key.public_key)
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, _canonical(receipt["payload"]))
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False
    return True


def verify_usage_receipt(receipt: Any, public_key: ReceiptPublicKey | Mapping[str, Any]) -> bool:
    """Backward-compatible name for verifying a hosted usage receipt."""
    return verify_signed_receipt(receipt, public_key)


__all__ = ["RECEIPT_ALGORITHM", "ReceiptPublicKey", "verify_signed_receipt", "verify_usage_receipt"]
