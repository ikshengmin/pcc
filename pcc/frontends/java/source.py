"""Owned Java source entrypoints: parse -> source sema -> direct public IR.

This is the Java production direction. The separate class-file frontend is a
compatibility experiment, never an intermediate format or fallback here.
"""
from collections.abc import Mapping

from .artifact import JavaCompilation
from .diagnostics import JavaError, UnsupportedJava
from .source_syntax import SourceParser
from .source_project import SourceProject
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
    raise UnsupportedJava(f"unsupported Java source target/ABI {target!r}; require an owned LP64 Linux or arm64 Darwin target", code="java.unsupported.target-abi")


def _compile_programs(programs, entry_class, entry, descriptor, target):
    project = SourceProject(programs).analyze()
    program = project.classes.get(entry_class)
    if program is None:
        raise JavaError("source entry class not found: " + entry_class.replace("/", "."))
    key = (entry, descriptor)
    if key not in {(method.token.text, method.descriptor) for method in program.methods}:
        raise JavaError(f"source entry method not found: {entry}{descriptor}")
    return SourceLowerer(program, target, project.programs).lower(key)


def compile_source(source: str, *, filename: str = "<java>", entry: str = "run",
                   descriptor: str = "()I", target: str = "x86_64-unknown-linux-gnu") -> JavaCompilation:
    """Compile one original scalar Java class, without javac or bytecode.

    All source methods are checked and emitted, including uncalled methods.
    The selected export is not Java main(String[]) startup. Heap/fields/class
    initialization, exceptions and monitors require the shared runtime.
    """
    try:
        _admit_target(target)
        program = SourceParser(source, filename).parse()
        return _compile_programs((program,), program.name, entry, descriptor, target)
    except RecursionError as error:
        raise UnsupportedJava("Java source exceeds the bounded parser/analysis nesting limit", code="java.unsupported.nesting-limit") from error


def compile_sources(sources: Mapping[str, str], *, entry_class: str, entry: str = "run",
                    descriptor: str = "()I", target: str = "x86_64-unknown-linux-gnu") -> JavaCompilation:
    """Compile a closed source project, retaining every supplied class/method.

    Keys are diagnostic source names, never filesystem paths to open. Values
    are original source text. The entry class uses its Java qualified name.
    Single-type imports and resolved class-qualified static calls are supported;
    no dependency is synthesized, downloaded, ignored or resolved from JARs.
    Limits: 256 source units, 1 MiB each and 8 MiB aggregate source text.
    """
    try:
        _admit_target(target)
        if not isinstance(sources, Mapping) or not sources or len(sources) > 256:
            raise JavaError("source project requires between 1 and 256 supplied units")
        if not isinstance(entry_class, str):
            raise JavaError("source entry class must be a qualified name")
        if any(not isinstance(name, str) or not isinstance(text, str) for name, text in sources.items()):
            raise JavaError("source project names and contents must be text")
        if sum(len(text) for text in sources.values()) > 8_388_608:
            raise UnsupportedJava("source project exceeds the 8 MiB aggregate input limit", code="java.unsupported.project-size-limit")
        programs = [SourceParser(sources[name], name).parse() for name in sorted(sources)]
        return _compile_programs(programs, entry_class.replace(".", "/"), entry, descriptor, target)
    except RecursionError as error:
        raise UnsupportedJava("Java source exceeds the bounded parser/analysis nesting limit", code="java.unsupported.nesting-limit") from error
