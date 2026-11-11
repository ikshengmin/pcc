"""Shared setup for the proof checker tests."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _deep_recursion():
    """Unary numerals nest; give the checker room to recurse."""
    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous, 20000))
    yield
    sys.setrecursionlimit(previous)
