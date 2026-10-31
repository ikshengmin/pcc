"""Owned Java bytecode AOT: explicit, closed-world static int/void slice.

No Java source parser, javac subprocess, JVM fallback, or independent GC.
Future Java object/runtime support must use PCC's shared runtime contracts.
"""
from .classfile import JavaError, UnsupportedJava, parse_class
from .compiler import JavaCompilation, compile_class, method_symbol

__all__ = ["JavaCompilation", "JavaError", "UnsupportedJava", "compile_class", "method_symbol", "parse_class"]
