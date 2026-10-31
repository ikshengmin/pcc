"""Owned Java source frontend plus a separate bytecode compatibility experiment.

Source parsing/analysis lowers directly to public IR, never through .class.
No javac subprocess, JVM fallback or independent GC. Runtime-bearing Java
features must use PCC's shared pcc-Python runtime contracts.
"""
from .diagnostics import JavaError, UnsupportedJava
from .artifact import JavaCompilation, method_symbol
from .source import compile_source, compile_sources


def analyze_class_sources(sources):
    """Source class/constructor model only; not managed-object execution."""
    from .source_class_model import analyze_class_sources as analyze
    return analyze(sources)


def parse_class(data):
    """Explicit compatibility experiment; never imported by source lowering."""
    from .classfile import parse_class as read
    return read(data)


def compile_class(data, *, entry="run", descriptor="()I", target="x86_64-unknown-linux-gnu"):
    """Explicit class compatibility route, not the Java source intermediate."""
    from .compiler import compile_class as compile_compatibility
    return compile_compatibility(data, entry=entry, descriptor=descriptor, target=target)


__all__ = ["JavaCompilation", "JavaError", "UnsupportedJava", "compile_source", "compile_sources", "analyze_class_sources", "compile_class", "method_symbol", "parse_class"]
