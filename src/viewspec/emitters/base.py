"""Emitter plugin base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from viewspec.types import ASTBundle


class EmitterPlugin(ABC):
    """Interface implemented by deterministic AST emitters."""

    @abstractmethod
    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        """Emit artifacts for an AST bundle and return artifact paths."""
