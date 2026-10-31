"""Owned, typed Go scalar frontend (not yet a complete Go implementation).

Public entrypoints parse/analyze original Go source and return PCC public IR.
They neither execute an external compiler nor silently lower Go through C or
Python. Runtime-bearing features remain explicit diagnostics until they use
pcc-Python's shared runtime/GC contract; there is no independent Go runtime.
"""

from pcc.frontends.go.lowering import GO_MAIN_SYMBOL, function_symbol
from pcc.frontends.go.packages import compile_packages
from pcc.frontends.go.syntax import GoDiagnostic, GoParser
from pcc.frontends.go.diagnostics import UnsupportedGo


DEFAULT_TARGET = "x86_64-unknown-linux-gnu"


def compile_go_packages(packages, *, main_package="main", target_triple=DEFAULT_TARGET):
    """Compile an explicit import-path -> original source-unit package graph.

    Each package contains (filename, original_source) pairs. The caller selects
    files; no module fetch, host tool, alternate standard library, or source
    preprocessing is hidden here. Only imported packages are compiled.
    """
    return compile_packages(packages, main_package=main_package, target_triple=target_triple)


def compile_go_sources(units, *, target_triple=DEFAULT_TARGET):
    """Compile separate files in package main, without textual merging."""
    return compile_go_packages({"main": tuple(units)}, target_triple=target_triple)


def compile_go(source, filename="<go>", *, target_triple=DEFAULT_TARGET):
    """Parse, type-check and lower one unchanged Go source file to public IR."""
    return compile_go_sources(((filename, source),), target_triple=target_triple)


__all__ = [
    "GoDiagnostic", "UnsupportedGo", "GoParser", "compile_go", "compile_go_sources", "compile_go_packages",
    "function_symbol", "GO_MAIN_SYMBOL", "DEFAULT_TARGET",
]
