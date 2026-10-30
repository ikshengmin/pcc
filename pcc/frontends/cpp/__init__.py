"""Owned C++ frontend under construction; see docs/cpp-frontend.md.

This is not yet the public driver and is not a complete C++17 implementation.
Unsupported semantics fail before they can be lowered through the C frontend.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.parser import CppParser

__all__ = ["CppDiagnostic", "CppParser"]
