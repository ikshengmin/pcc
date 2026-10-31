"""Original C++ -> Rust readonly record-pointer ABI, using existing owners.

The C++ caller constructs its own stack object and passes its real address to
Rust. No address-to-integer shim, symbol alias, source/IR rewrite, separate
allocator, object header, or GC/runtime contract is introduced here.

Only Linux x86-64 is executed. Darwin arm64 support here is object emission,
not linking or execution. Rust's opt-in is attestation, not a rustc oracle.
"""

from dataclasses import dataclass

from tests.cpp.platform_execution import DARWIN_TARGET
from tests.multilanguage.owned_execution import FIXTURES, TARGET as LINUX_TARGET


RECORD_FIXTURES = FIXTURES / "record_pointer"
CPP_ENTRY = "record_entry"
RUST_ENTRY = "rust_read_record"
SNAPSHOT = "record_snapshot"
OWNER = "host-python/pcc-cpp+rust/public-ir/pcc-self/pcc-elf/readonly-record-pointer"

# Independent SysV/Itanium field offsets: i32 at 0, u64 at 8, i64 at 16.
# Read no padding at offsets 4..7, whose contents C++ does not specify.
# Each snapshot writes packed i32/u64/i64 (20 bytes). The startup appends the
# complete returned u64 (8 bytes): before/after/result is exactly 48 bytes.
# This is observation only; Rust, not this assembly, computes the result.
LINUX_OBSERVER = """\
.intel_syntax noprefix
.text
.globl record_snapshot
record_snapshot:
  cmp rdi, 0
  je record_observer_failed
  mov rax, rdi
  and rax, 7
  cmp rax, 0
  jne record_observer_failed
  sub rsp, 24
  mov eax, dword ptr [rdi]
  mov dword ptr [rsp], eax
  mov rax, qword ptr [rdi + 8]
  mov qword ptr [rsp + 4], rax
  mov rax, qword ptr [rdi + 16]
  mov qword ptr [rsp + 12], rax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 20
  syscall
  cmp rax, 20
  jne record_observer_failed
  add rsp, 24
  ret
.globl _start
_start:
  sub rsp, 16
  call record_entry
  mov qword ptr [rsp], rax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 8
  syscall
  cmp rax, 8
  jne record_observer_failed
  mov eax, 60
  xor edi, edi
  syscall
record_observer_failed:
  mov eax, 60
  mov edi, 111
  syscall
"""


@dataclass(frozen=True)
class RecordInputs:
    cpp: bytes
    rust: bytes


@dataclass(frozen=True)
class RecordObjects:
    target: str
    cpp_ir: str
    rust_ir: str
    cpp_object: bytes
    rust_object: bytes
    rust_export: str


@dataclass(frozen=True)
class RecordExecutable:
    compiled: RecordObjects
    observer_object: bytes
    executable: bytes


def original_record_inputs() -> RecordInputs:
    return RecordInputs(
        (RECORD_FIXTURES / "caller.cpp").read_bytes(),
        (RECORD_FIXTURES / "read_record.rs").read_bytes(),
    )


def compile_record_objects(inputs: RecordInputs, target: str) -> RecordObjects:
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.rust import compile_rust

    if target not in (LINUX_TARGET, DARWIN_TARGET):
        raise ValueError("record ABI test supports only explicit Linux/Darwin targets")
    compiled = CEvaluator(target_triple=target, backend="self").compile_translation_units(
        [TranslationUnit("record-caller.cpp", None, inputs.cpp.decode("utf-8"))],
        use_system_cpp=False,
        use_compile_cache=False,
        frontend_opt_level=0,
    )
    ((name, cpp_ir, _return_metadata, _external_definitions),) = compiled
    assert name == "record-caller.cpp"
    rust = compile_rust(
        inputs.rust.decode("utf-8"),
        experimental_rustc_checked=True,
        overflow_checks=False,
        module_name="record_pointer",
        target_triple=target,
    )
    assert rust.module.triple == rust.target_triple == target
    assert rust.module.data_layout == rust.data_layout
    assert rust.experimental and not rust.borrow_checked_by_pcc
    assert not rust.overflow_checks
    assert rust.symbols[RUST_ENTRY] == RUST_ENTRY
    return RecordObjects(
        target, cpp_ir, rust.ir_text,
        emit_owned_object(cpp_ir, target), rust.emit_object(), RUST_ENTRY,
    )


def compile_record_executable(inputs: RecordInputs) -> RecordExecutable:
    from pcc.backend.elf_x86_64 import (
        emit_relocatable,
        link_static_executable,
        parse_relocatable,
    )
    from pcc.backend.x86_64_asm_driver import assemble_file

    compiled = compile_record_objects(inputs, LINUX_TARGET)
    observer = emit_relocatable(assemble_file(LINUX_OBSERVER))
    executable = link_static_executable(
        [parse_relocatable(data) for data in
         (compiled.cpp_object, compiled.rust_object, observer)],
        entry="_start",
    )
    return RecordExecutable(compiled, observer, executable)
