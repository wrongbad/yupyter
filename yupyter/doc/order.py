"""Fractional indexing for cell order keys.

Keys are strings over a base-62 alphabet that compare correctly with plain
string ordering (``a < b``). ``between(a, b)`` returns a key strictly between
``a`` and ``b``; ``None`` means "no bound" on that side. Keys never end in
the zero digit.
"""
from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(DIGITS)
ZERO = DIGITS[0]


def _midpoint(a: str, b: str | None) -> str:
    if b is not None and a >= b:
        raise ValueError(f"{a!r} >= {b!r}")
    if b is not None:
        n = 0
        while (a[n] if n < len(a) else ZERO) == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:])
    da = DIGITS.index(a[0]) if a else 0
    db = DIGITS.index(b[0]) if b else BASE
    if db - da > 1:
        return DIGITS[(da + db + 1) // 2]
    if b is not None and len(b) > 1:
        return b[:1]
    return DIGITS[da] + _midpoint(a[1:], None)


def between(a: str | None, b: str | None) -> str:
    """Return a key ``k`` with ``a < k < b`` (``None`` = unbounded)."""
    return _midpoint(a or "", b or None)


def key_after(last: str | None) -> str:
    return between(last, None)


def key_before(first: str | None) -> str:
    return between(None, first)
