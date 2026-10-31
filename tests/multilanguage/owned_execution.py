"""One-process scalar ABI component gate using four public PCC frontends.

Host Python compiles unchanged authored inputs into four separate owned ELF
objects, then one owned link adds only an observation/startup object. No source
semantics are implemented by that observer. No external compiler, JVM, Go/Rust
runtime, libpython, shared GC, exception or scheduling qualification is implied.
The Rust opt-in is caller attestation, NOT a rustc reference run.
Java source is the mainline entry. A separately labeled historical bytecode
compatibility experiment retains its original class input; it cannot qualify
Java source parsing. Neither route translates source into bytecode.
"""

from dataclasses import dataclass
from pathlib import Path

TARGET = "x86_64-unknown-linux-gnu"
OWNERS = {
    "source": "host-python/pcc-cpp+java-source+go+rust/public-ir/pcc-self/pcc-elf",
    "bytecode-compat": "host-python/pcc-cpp+java-bytecode-compat+go+rust/public-ir/pcc-self/pcc-elf",
}
FIXTURES = Path(__file__).parent / "fixtures"
JAVA_CLASS = "Interop"
JAVA_METHOD = "mix"
JAVA_DESCRIPTOR = "(IIIIIIII)I"

# Independent, literal JVM instructions: a-b-c-d-e-f-g-h, then ireturn.
# This is an authored original class file, not javac output or a rewritten
# library class. The safe class name does not claim arbitrary-label coverage.
JAVA_CODE = bytes.fromhex("1a 1b 64 1c 64 1d 64 15 04 64 15 05 64 15 06 64 15 07 64 ac")

# The record is packed little-endian i32/i64/u32/u64/i64, exactly 32 bytes.
# Linux provides aligned rsp; every call retains SysV's 16-byte alignment.
OBSERVER = """\
.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 32
  call observe_java
  mov dword ptr [rsp], eax
  call observe_go
  mov qword ptr [rsp + 4], rax
  call observe_rust32
  mov dword ptr [rsp + 12], eax
  call observe_rust64
  mov qword ptr [rsp + 16], rax
  call observe_chain
  mov qword ptr [rsp + 24], rax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 32
  syscall
  cmp rax, 32
  jne observation_failed
  mov eax, 60
  xor edi, edi
  syscall
observation_failed:
  mov eax, 60
  mov edi, 111
  syscall
"""


@dataclass(frozen=True)
class Inputs:
    cpp: bytes
    java: bytes
    go: bytes
    rust: bytes
    java_frontend: str


@dataclass(frozen=True)
class Artifact:
    ir: tuple[tuple[str, str], ...]
    objects: tuple[tuple[str, bytes], ...]
    exports: tuple[tuple[str, str], ...]
    executable: bytes
    owner: str


def original_inputs(java_frontend: str) -> Inputs:
    if java_frontend == "source":
        java = (FIXTURES / "Interop.java").read_bytes()
    elif java_frontend == "bytecode-compat":
        from tests.java.class_builder import ClassBuilder

        builder = ClassBuilder(JAVA_CLASS)
        builder.method(JAVA_METHOD, JAVA_CODE, JAVA_DESCRIPTOR, stack=2, locals_=8)
        java = builder.build()
    else:
        raise ValueError("select Java source or the explicit bytecode compatibility experiment")
    return Inputs(
        (FIXTURES / "caller.cpp").read_bytes(),
        java,
        (FIXTURES / "scalars.go").read_bytes(),
        (FIXTURES / "scalars.rs").read_bytes(),
        java_frontend,
    )


def compile_owned(inputs: Inputs) -> Artifact:
    from pcc.backend.elf_x86_64 import (
        emit_relocatable,
        link_static_executable,
        parse_relocatable,
    )
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.go import compile_go_sources, function_symbol
    from pcc.frontends.java import method_symbol
    from pcc.frontends.rust import compile_rust

    cpp_units = CEvaluator(target_triple=TARGET, backend="self").compile_translation_units(
        [TranslationUnit("caller.cpp", None, inputs.cpp.decode("utf-8"))],
        use_system_cpp=False,
        use_compile_cache=False,
        frontend_opt_level=0,
    )
    # Public CEvaluator returns FOUR fields. Do not bypass its language dispatch.
    ((unit_name, cpp_ir, _return_metadata, _external_definitions),) = cpp_units
    assert unit_name == "caller.cpp"
    if inputs.java_frontend == "source":
        from pcc.frontends.java import compile_source

        java = compile_source(inputs.java.decode("utf-8"), filename="Interop.java",
                              entry=JAVA_METHOD, descriptor=JAVA_DESCRIPTOR, target=TARGET)
    elif inputs.java_frontend == "bytecode-compat":
        from pcc.frontends.java import compile_class

        java = compile_class(inputs.java, entry=JAVA_METHOD,
                             descriptor=JAVA_DESCRIPTOR, target=TARGET)
    else:
        raise ValueError("unknown Java frontend")
    go = compile_go_sources((("scalars.go", inputs.go.decode("utf-8")),),
                            target_triple=TARGET)
    rust = compile_rust(inputs.rust.decode("utf-8"),
                        experimental_rustc_checked=True, overflow_checks=False,
                        module_name="scalar_interop", target_triple=TARGET)
    assert rust.module.triple == rust.target_triple == TARGET
    assert rust.module.data_layout == rust.data_layout
    assert rust.experimental and not rust.borrow_checked_by_pcc
    assert not rust.overflow_checks
    exports = (
        ("java", method_symbol(JAVA_CLASS, JAVA_METHOD, JAVA_DESCRIPTOR)),
        ("go", function_symbol("Fold")),
        ("rust32", rust.symbols["mix32"]),
        ("rust64", rust.symbols["mix64"]),
    )
    # Bind authored C++ declarations to frontend exports, never patch names.
    for _language, symbol in exports:
        assert symbol.encode("ascii") + b"(" in inputs.cpp
    assert java.entry_symbol == dict(exports)["java"]
    ir = (("cpp", cpp_ir), ("java", str(java.module)),
          ("go", str(go)), ("rust", rust.ir_text))
    objects = tuple((language, emit_owned_object(text, TARGET)) for language, text in ir)
    objects += (("observer", emit_relocatable(assemble_file(OBSERVER))),)
    executable = link_static_executable(
        [parse_relocatable(data) for _language, data in objects], entry="_start",
    )
    return Artifact(ir, objects, exports, executable, OWNERS[inputs.java_frontend])
