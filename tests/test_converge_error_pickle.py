"""ConvergeError must survive pickle, copy.copy and copy.deepcopy with its fields intact.

Its ``args`` hold only the message, so the default ``cls(*args)`` rebuild used to raise
``TypeError`` (missing ``fix``), which turns a worker failure in a ProcessPoolExecutor into
``BrokenProcessPool`` instead of the ``ValueError`` callers catch.
"""

from __future__ import annotations

import copy
import pickle

from viewspec.converge_sessions import ConvergeError


def test_converge_error_survives_pickle_and_copy():
    exc = ConvergeError(
        "CONVERGE_STATE_INVALID",
        "Convergence state is not finite JSON.",
        "Use only bounded finite JSON values.",
        cli_exit=1,
    )
    clones = [pickle.loads(pickle.dumps(exc, protocol)) for protocol in range(pickle.HIGHEST_PROTOCOL + 1)]
    clones += [copy.copy(exc), copy.deepcopy(exc)]
    for clone in clones:
        assert type(clone) is ConvergeError
        assert isinstance(clone, ValueError)
        assert (clone.code, clone.message, clone.fix, clone.cli_exit) == (
            "CONVERGE_STATE_INVALID",
            "Convergence state is not finite JSON.",
            "Use only bounded finite JSON values.",
            1,
        )
        assert str(clone) == str(exc) == "Convergence state is not finite JSON."
        assert clone.args == exc.args
        assert clone.to_json() == exc.to_json()


def test_converge_error_default_cli_exit_survives_pickle():
    exc = ConvergeError("CONVERGE_STATE_INVALID", "message", "fix")
    assert pickle.loads(pickle.dumps(exc)).cli_exit == 2
