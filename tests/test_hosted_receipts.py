from __future__ import annotations

import base64
import hashlib
import json

from hypothesis import given, strategies as st

from viewspec import ReceiptPublicKey, verify_usage_receipt


SECRET = "customer-verifiable-receipt-test-secret"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_and_receipt(payload: dict) -> tuple[dict, dict]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = hashlib.sha256(b"viewspec-usage-receipt-v1\0" + SECRET.encode()).digest()
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = f"vsk_{hashlib.sha256(public_bytes).hexdigest()[:16]}"
    key = {"algorithm": "ed25519", "key_id": key_id, "public_key": _encoded(public_bytes)}
    receipt = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "payload": payload,
        "signature": _encoded(private_key.sign(_canonical(payload))),
    }
    return key, receipt


def test_public_receipt_key_parses_and_verifies_a_receipt() -> None:
    key, receipt = _key_and_receipt({"customer_id": "cust_1", "usage": 42})

    parsed = ReceiptPublicKey.from_json(key)

    assert parsed.key_id == key["key_id"]
    assert verify_usage_receipt(receipt, parsed)


@given(delta=st.integers(min_value=-1_000_000, max_value=1_000_000))
def test_any_receipt_payload_change_is_rejected(delta: int) -> None:
    key, receipt = _key_and_receipt({"usage": 42})
    changed = {**receipt, "payload": {"usage": 42 + delta}}

    assert verify_usage_receipt(changed, key) is (delta == 0)


def test_receipt_rejects_wrong_key_identity_and_malformed_signature() -> None:
    key, receipt = _key_and_receipt({"usage": 42})

    assert not verify_usage_receipt({**receipt, "key_id": "vsk_wrong"}, key)
    assert not verify_usage_receipt({**receipt, "signature": "not-base64"}, key)
