"""Stable-code exceptions for the local compiler and its artifact validators.

``str(exc)`` is exactly ``"<CODE>: <message>"``, the text that existing callers and tests match
on, while ``exc.code`` and ``exc.message`` expose the two halves so that no caller has to parse
the string. Every code is a member of :data:`viewspec.error_codes.ERROR_CODES`;
tests/test_error_code_registry.py enforces that at each raise site.

The two concrete classes keep their historical base so that existing ``except`` clauses, the
CLI exit codes and the validator's error collection keep working unchanged:

* :class:`CompilerInternalError` is a ``RuntimeError``: a compiler invariant was violated, which
  is a bug in the compiler rather than bad input, and the CLI reports it as an internal error.
* :class:`LocalToolContractError` is a ``ValueError``: a compile request or an emitted artifact
  violates a stable local-tool contract, and the CLI reports it as a usage error.
"""

from __future__ import annotations

from typing import Any


class StableCodeError(Exception):
    """Base class for exceptions that carry a registry code beside a human-readable message."""

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def __reduce__(self) -> tuple[Any, ...]:
        # ``args`` holds only the formatted "CODE: message" text, so the default
        # ``cls(*args)`` rebuild would fail. Rebuild from the two halves instead, so the
        # exception survives pickle (for example across a ProcessPoolExecutor), copy.copy and
        # copy.deepcopy. The instance dict carries any other attribute, such as __notes__.
        return (type(self), (self.code, self.message), self.__dict__ or None)


class CompilerInternalError(StableCodeError, RuntimeError):
    """A compiler invariant was violated.

    Raised when planner or aesthetic-profile metadata no longer describes the IR shape it was
    derived from (for example a product role that should map to a grid node but does not), or
    when a planner pass produced a duplicate it must never produce.
    """


class LocalToolContractError(StableCodeError, ValueError):
    """A compile request or emitted artifact violates a stable local-tool contract.

    Raised for an unsupported import target, a ``ViewSpecView.tsx`` render shape the semantic
    validator cannot parse, or a semantic-digest projection over its byte budget.
    """


__all__ = ["CompilerInternalError", "LocalToolContractError", "StableCodeError"]
