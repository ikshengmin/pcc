"""pcc.stdlib.weakref -- the compiled ``weakref`` provider.

Builtin dispatch owns ``ref``, ``proxy`` and the no-argument weak
dictionaries (pipeline_import_policy.native_builtin_dispatch_owns); their
runtime keeps true weak references under every collector. This module holds
what CPython also writes in Python: ``WeakSet`` is CPython's own
``_weakrefset`` implementation over the native ``ref``. A name defined
neither here nor by builtin dispatch fails explicitly; nothing here holds a
strong reference in place of a weak one.
"""
from __future__ import annotations

from _weakrefset import WeakSet

__all__ = ["WeakSet"]
