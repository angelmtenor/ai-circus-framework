"""Which figures in a text did not come from the data it was built from?

An assistant that "never invents numbers" needs a check that can fail. `untraceable(text, sources)` lists the numbers
in `text` that appear in none of `sources` (tool outputs, a rulebook, a record), allowing for the usual rewrites: a
float rounded to 0, 1 or 2 decimals, a share shown as a percentage, a comma for the decimal point, and the digits of
any date or identifier written in the sources. It is a guard against invention, not a proof of correctness: a number
that is in the sources but attached to the wrong thing passes.

Pure Python. Small integers that are structure, not data (1-3 as list numbering or "p50"-style labels) are ignored
through `ignore`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w])")
DEFAULT_IGNORE = frozenset({"0", "1", "2", "3", "10", "50", "90"})  # list numbering and p10/p50/p90 labels


def _norm(token: str) -> str:
    value = token.replace(",", ".")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value or "0"


def _variants(value: float) -> set[str]:
    out = set()
    for scale in (1, 100):
        for digits in (0, 1, 2):
            out.add(_norm(f"{round(abs(value) * scale, digits):.{digits}f}"))
    return out


def numbers_in(text: str) -> set[str]:
    """The numeric tokens of a text, normalised ('12,50' and '12.5' are the same)."""
    return {_norm(m.group(0)) for m in _NUMBER.finditer(text)}


def _collect(source: Any, into: set[str]) -> None:
    if isinstance(source, bool) or source is None:
        return
    if isinstance(source, int | float):
        into |= _variants(float(source))
    elif isinstance(source, str):
        into |= numbers_in(source)
    elif isinstance(source, dict):
        for k, v in source.items():
            _collect(k, into)
            _collect(v, into)
    elif isinstance(source, Iterable):
        for item in source:
            _collect(item, into)


def untraceable(text: str, sources: Iterable[Any], *, ignore: frozenset[str] = DEFAULT_IGNORE) -> list[str]:
    """Numbers in `text` found in none of `sources` (and not in `ignore`), in order of appearance."""
    known: set[str] = set()
    for source in sources:
        _collect(source, known)
    seen: list[str] = []
    for m in _NUMBER.finditer(text):
        token = _norm(m.group(0))
        if token not in known and token not in ignore and token not in seen:
            seen.append(token)
    return seen
