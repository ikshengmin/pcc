"""Host-owned C++ emission for Linux x86_64 and Darwin arm64 tests.

This is not native pcc1. External compilers are reference owners only. Cross
objects are deliberately separate from linking/running on the actual target.
The existing Linux adapter and its assertions remain unchanged.
"""

from dataclasses import dataclass
import platform
import re
import sys

from tests.cpp.owned_execution import (
    ENTRY,
    TARGET as LINUX_TARGET,
    HostArtifact,
    compile_host_cpp,
    entry_definition,
)

DARWIN_TARGET = "arm64-apple-darwin"
TARGETS = (LINUX_TARGET, DARWIN_TARGET)


@dataclass(frozen=True)
class TargetObjects:
    target: str
    ir: tuple[str, ...]
    objects: tuple[bytes, ...]
    entry_symbol: str


def native_target() -> str:
    machine = platform.machine()
    if sys.platform == "linux" and machine in ("x86_64", "AMD64"):
        return LINUX_TARGET
    if sys.platform == "darwin" and machine == "arm64":
        return DARWIN_TARGET
    raise RuntimeError("C++ execution requires Linux x86_64 or Darwin arm64")


def compile_target_objects(units, *, target: str) -> TargetObjects:
    """Only parse/lower/emit; never pretend a cross object was executed."""
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.passes.context import PassContext
    from pcc.frontends.cpp import CppParser

    if target not in TARGETS:
        raise ValueError("unsupported C++ test target: " + target)
    if not units or len({name for name, _ in units}) != len(units):
        raise ValueError("C++ project needs distinct translation units")
    ir_texts, objects = [], []
    entry = None
    for filename, source in units:
        parser = CppParser(target_triple=target)
        ast = parser.parse(source, filename)
        selected = entry_definition(parser.scope.lookup(ENTRY))
        if selected is not None:
            if entry is not None:
                raise ValueError("C++ project defines its entry more than once")
            entry = selected
        generator = CCodeGenerator(
            translation_unit_name=filename,
            pass_ctx=PassContext(opt_level=0, target_triple=target),
        )
        generator.generate_code(ast)
        ir = str(generator.module)
        ir_texts.append(ir)
        objects.append(emit_owned_object(ir, target))
    if entry is None or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", entry):
        raise ValueError("C++ project needs one valid int cpp_test_entry()")
    return TargetObjects(target, tuple(ir_texts), tuple(objects), entry)


def darwin_observer_object(entry: str) -> bytes:
    """Observe an int, not source semantics; preserve all four result bytes.

    Darwin's LC_MAIN calls _main. The owned startup calls the C++ entry, saves
    its result across libSystem write, and returns it. A short write is a test
    failure rather than an exit-code-only success. No host assembler is used.
    """
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.macho_obj import emit_object

    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", entry):
        raise ValueError("invalid C++ observer entry symbol")
    assembly = (
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".p2align 2\n.globl _main\n_main:\n"
        "  paciasp\n  stp x29, x30, [sp, #-32]!\n  mov x29, sp\n"
        f"  bl _{entry}\n"
        "  str w0, [sp, #16]\n"
        "  mov x0, #1\n  add x1, sp, #16\n  mov x2, #4\n  bl _write\n"
        "  cmp x0, #4\n  b.ne Lwrite_failed\n"
        "  ldr w0, [sp, #16]\n  b Ldone\n"
        "Lwrite_failed:\n  mov w0, #125\n"
        "Ldone:\n  ldp x29, x30, [sp], #32\n  autiasp\n  ret\n"
        ".subsections_via_symbols\n"
    )
    sections, undefined = assemble_file(assembly)
    return emit_object(sections, undefined=undefined)


def compile_native_cpp(units) -> HostArtifact:
    """Own emission/link/signing on the real host, never a reference fallback."""
    target = native_target()
    if target == LINUX_TARGET:
        return compile_host_cpp(units)
    from pcc.backend.macho_exec import link_executable

    compiled = compile_target_objects(units, target=target)
    observer = darwin_observer_object(compiled.entry_symbol)
    executable = link_executable([observer, *compiled.objects])
    return HostArtifact(
        compiled.ir, compiled.objects, executable,
        owner="host-python/pcc-cpp/pcc-self/pcc-macho",
    )
