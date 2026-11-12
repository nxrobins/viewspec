"""Which ``pattern`` attributes of a page are safe to run in Python.

A page's ``pattern`` is text the agent does not trust. The planner runs it on the agent's value, and
the tool schema hands it to a host that may run it again, so both ask ``pattern_is_bounded`` first.
A pattern it turns down is left to the browser, which reports its refusal through the step's
``invalid`` list.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from functools import lru_cache

# A quantified group ("(a+)+", "(\\w|\\s)*") can make Python's backtracking ``re`` run for an
# unbounded time on a value made to defeat it. Such a pattern, from a page the agent does not
# trust, is left to the browser, which reports its refusal through the step's ``invalid`` list.
RISKY_PATTERN_RE = re.compile(r"\)\s*[*+{]")
MAX_PATTERN_LENGTH = 200
MAX_PATTERN_VALUE_LENGTH = 1000
# Quantifiers in a row that can each take part of the same text backtrack polynomially without any
# group: "[a-z]+.*[a-z]+.*x" on 1000 letters tries about n**k splits for k such quantifiers, and
# "a?" * 20 + "a" * 20 tries 2**20. On a 1000-character value, two take milliseconds, three up to
# 0.4 s, and four more than 20 s (CPython 3.15), so more than two is left to the browser.
MAX_CHOICE_QUANTIFIERS = 2
# A quantifier as written, with its lazy "?" or possessive "+": "*", "+?", "{2,}", "{,5}", "{3}".
# "{}" is not one; Python reads it as text.
QUANTIFIER_RE = re.compile(r"(?:[*+?]|\{(?!\})\d*(?:,\d*)?\})[?+]?")


def _quantifiers(pattern: str) -> Iterator[str]:
    """Each quantifier in ``pattern``, as QUANTIFIER_RE reads it. A mark after a backslash or inside a
    character class is text, and the "?" that opens a group's syntax ("(?:", "(?=") is not one."""

    index, in_class = 0, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
        elif in_class:
            in_class = char != "]"
            index += 1
        elif char == "[":
            index += 1
            index += pattern.startswith("^", index)
            index += pattern.startswith("]", index)  # A "]" first in a class is text.
            in_class = True
        elif char == "(":
            index += 1 + pattern.startswith("?", index + 1)
        elif match := QUANTIFIER_RE.match(pattern, index):
            yield match.group()
            index = match.end()
        else:
            index += 1


def _leaves_choice(quantifier: str) -> bool:
    """Whether ``quantifier`` (as ``_quantifiers`` yields it) lets the matcher try more than one
    length of text, so a failed match can come back to it and try another.

    A lazy one ("+?") still backtracks, only in the other order. A possessive one ("++") does not, but
    it counts too: JavaScript has no possessive quantifiers, so in the browser the pattern means
    something else, and leaving it to the browser is the safe side. A range counts unless its two
    ends are the same: "{1,3}" leaves three lengths to try, "{3}" and "{3,3}" only one.
    """

    base = quantifier[:-1] if len(quantifier) > 1 and quantifier[-1] in "?+" else quantifier
    if not base.startswith("{"):
        return True  # "*", "+", "?"
    low, comma, high = base[1:-1].partition(",")
    if not comma:
        return False  # "{3}": exactly that many.
    return not high or int(low or 0) != int(high)  # "{2,}" and "{,}" are open; "{,5}" is "{0,5}".


# A choice between branches is a second way to backtrack, with no quantifier at all: "(a|aa)" written
# n times tries 2**n ways to split a run of "a" that ends in a letter no branch takes. 24 groups took
# 0.3 s and each further pair of groups four times as long. The number of ways is at most the product
# of the branch counts of every group that has a choice, and more than this many is left to the
# browser. One group of many words ("mr|mrs|ms|dr|prof") stays well under it.
MAX_ALTERNATION_PATHS = 16


def _alternation_paths(pattern: str) -> int:
    """An upper bound on the ways the choices ("|") in ``pattern`` can combine: the product, over the
    whole pattern and each group in it, of that group's number of branches. A "|" after a backslash or
    inside a character class is text."""

    paths, index, in_class = 1, 0, False
    branches = [1]  # Branch count of the whole pattern, then of each group still open.
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            index += pattern.startswith("^", index + 1)
            index += pattern.startswith("]", index + 1)  # A "]" first in a class is text.
            in_class = True
        elif char == "(":
            branches.append(1)
        elif char == ")" and len(branches) > 1:
            paths *= branches.pop()
        elif char == "|":
            branches[-1] += 1
        index += 1
    for count in branches:
        paths *= count
    return paths


@lru_cache(maxsize=256)
def bounded_pattern(pattern: str) -> re.Pattern[str] | None:
    """``pattern`` compiled, when it can be run on a value of up to MAX_PATTERN_VALUE_LENGTH characters
    in a short, bounded time. None for one that could backtrack without bound, a long one, and one
    Python cannot read or reads as too large ("a{4294967296}")."""

    if (
        len(pattern) > MAX_PATTERN_LENGTH
        or RISKY_PATTERN_RE.search(pattern)
        or sum(_leaves_choice(quantifier) for quantifier in _quantifiers(pattern)) > MAX_CHOICE_QUANTIFIERS
        or _alternation_paths(pattern) > MAX_ALTERNATION_PATHS
    ):
        return None
    try:
        with warnings.catch_warnings():
            # A page's pattern may use syntax Python warns about ("[[a]"); that is not the caller's to fix.
            warnings.simplefilter("ignore")
            return re.compile(pattern)
    except (re.error, OverflowError, RecursionError, ValueError):
        return None


def pattern_is_bounded(pattern: str) -> bool:
    """Whether ``bounded_pattern`` accepts ``pattern``."""

    return bounded_pattern(pattern) is not None
