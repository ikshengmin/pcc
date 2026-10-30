"""Parity test for the native C parser driver (P6C.5 α1) against PLY.

Runs each snippet from ``tests/c_parse_oracle/corpus.py`` through both
the legacy PLY parser and the new ``CParseDriver``. Normalized ASTs
must be byte-identical.

This is the α1 gate: 63/63 green ⇒ parser-side native runtime works
and matches PLY's yacc runtime semantically.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).absolute().parents[2]
sys.path.insert(0, str(REPO))

from pcc.frontends.c.parse.c_parser import CParser  # PLY-based, legacy  # noqa: E402
from pcc.frontends.c.parse.c_parse_driver import CParseDriver  # new native driver  # noqa: E402
from pcc.frontends.c.parse.ast_normalize import normalize, diff  # noqa: E402
from tests.c_parse_oracle.corpus import CORPUS  # noqa: E402


# Reuse one PLY parser per module, initialized on first test use.
_ply = None


def _get_parser():
    """Reuse this module's parser without constructing it during collection."""
    global _ply
    if _ply is None:
        _ply = CParser()
    return _ply


_native = CParseDriver()


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_native_driver_matches_ply(name: str) -> None:
    src = CORPUS[name]
    ply_tree = _get_parser().parse(src, filename=f"<{name}>")
    native_tree = _native.parse(src, filename=f"<{name}>")

    expected = normalize(ply_tree)
    actual = normalize(native_tree)

    diffs = diff(expected, actual)
    if diffs:
        preview = "\n  ".join(diffs[:20])
        pytest.fail(
            f"Native driver diverges from PLY for {name!r} "
            f"({len(diffs)} diffs):\n  {preview}"
        )
