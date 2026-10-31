"""Owned Java source entrypoint: parse -> source sema -> direct public IR.

This is the Java production direction. The separate .class/JAR frontend is a
compatibility experiment, never an intermediate format or fallback here.
"""
from .artifact import JavaCompilation
from .diagnostics import JavaError, UnsupportedJava
from .source_syntax import SourceParser
from .source_sema import SourceAnalyzer
from .source_lowering import SourceLowerer


def _admit_target(target):
    from pcc.backend.self_backend_target_match import (
        is_aarch64_darwin_triple,
        is_aarch64_linux_triple,
        is_x86_64_linux_triple,
    )
    if type(target) is str:
        parts = target.lower().split("-")
        if is_aarch64_darwin_triple(target) and len(parts) == 3:
            return
        if (is_x86_64_linux_triple(target) or is_aarch64_linux_triple(target)) and (
                len(parts) != 4 or parts[3] in ("gnu", "musl")):
            return
    raise UnsupportedJava(f"unsupported Java source target/ABI {target!r}; require an owned LP64 Linux or arm64 Darwin target")


def compile_source(source: str, *, filename: str = "<java>", entry: str = "run",
                   descriptor: str = "()I", target: str = "x86_64-unknown-linux-gnu") -> JavaCompilation:
    """Compile an original scalar Java source class without javac or bytecode.

All source methods are checked and emitted, including methods not called by
the selected entry. `entry` selects an export, not Java main(String[]) startup.
References, imports, fields, class initialization, exceptions, monitors and
dynamic operations require the common pcc-Python runtime and fail explicitly.
    """
    try:
        _admit_target(target)
        program = SourceParser(source, filename).parse()
        SourceAnalyzer(program).analyze()
        key = (entry, descriptor)
        if key not in {(method.token.text, method.descriptor) for method in program.methods}:
            raise JavaError(f"source entry method not found: {entry}{descriptor}")
        return SourceLowerer(program, target).lower(key)
    except RecursionError as error:
        raise UnsupportedJava("Java source exceeds the bounded parser/analysis nesting limit") from error
