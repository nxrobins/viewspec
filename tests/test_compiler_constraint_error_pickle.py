"""CompilerConstraintError must survive pickle, copy.copy and copy.deepcopy with its fields intact.

Like the other coded errors (viewspec.compiler_errors.StableCodeError, ConvergeError), its ``args``
hold only the formatted ``"CODE: message"`` text, so the default ``cls(*args)`` rebuild raised
``TypeError`` (missing ``message``). A Tailwind constraint failure raised in a ProcessPoolExecutor
worker then surfaced as ``BrokenProcessPool`` instead of the ``ValueError`` callers catch.
"""

from __future__ import annotations

import copy
import pickle

import pytest

from viewspec.emitters.react_tailwind_tsx import CompilerConstraintError
from viewspec.emitters.react_tailwind_tsx.recipes import _fail


def _clones(exc: BaseException) -> list[BaseException]:
    clones = [pickle.loads(pickle.dumps(exc, protocol)) for protocol in range(pickle.HIGHEST_PROTOCOL + 1)]
    return [*clones, copy.copy(exc), copy.deepcopy(exc)]


def test_compiler_constraint_error_survives_pickle_and_copy():
    with pytest.raises(CompilerConstraintError) as exc_info:
        _fail("TAILWIND_LIMIT_EXCEEDED_NODES", "IR has 601 nodes; the limit is 600.")
    exc = exc_info.value
    exc.add_note("raised in a worker")
    exc.detail = {"nodes": 601}

    for clone in _clones(exc):
        assert type(clone) is CompilerConstraintError
        assert isinstance(clone, ValueError)
        assert clone.code == "TAILWIND_LIMIT_EXCEEDED_NODES"
        assert clone.message == "IR has 601 nodes; the limit is 600."
        assert str(clone) == str(exc) == "TAILWIND_LIMIT_EXCEEDED_NODES: IR has 601 nodes; the limit is 600."
        assert clone.args == exc.args
        assert clone.__notes__ == ["raised in a worker"]
        assert clone.detail == {"nodes": 601}

    deep = copy.deepcopy(exc)
    deep.detail["nodes"] = 0
    assert exc.detail == {"nodes": 601}


def test_compiler_constraint_error_keeps_the_remapped_code_through_pickle():
    """An unregistered code is replaced at construction; a round trip must not undo or redo that."""
    exc = CompilerConstraintError("NOT_A_CHECKED_IN_CODE", "original text")
    assert exc.code == "TAILWIND_RECIPE_CONFLICT"

    for clone in _clones(exc):
        assert (clone.code, clone.message, str(clone)) == (exc.code, exc.message, str(exc))
