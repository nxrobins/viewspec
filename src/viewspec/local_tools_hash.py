from __future__ import annotations

from pathlib import Path
import hashlib

def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def file_hash(path: Path) -> str:
    return bytes_hash(path.read_bytes())
