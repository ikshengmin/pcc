"""Owned native execution of C++ translation units on an arm64 Darwin host.

Each unit is compiled by PCC for arm64-apple-darwin (C++ frontend, shared C
code generator, owned object emitter); an observer `main` assembled by PCC's
own assembler calls `int cpp_test_entry()`, writes its result as four
little-endian bytes and returns it; PCC's own Mach-O linker links them. No
external compiler, assembler or linker runs. The Linux x86-64 lane stays in
tests.cpp.owned_execution.

The reference helper compiles the same unchanged units with a development
`clang++ -std=c++17` (PCC_CPP_REFERENCE, else PATH) and an equivalent observer
main. It is a labeled reference experiment, never PCC ownership evidence.
"""
import os
import platform
import shutil
import subprocess
import sys

import pytest

TARGET = 'arm64-apple-darwin'
UNAVAILABLE = (None if sys.platform == 'darwin' and platform.machine() == 'arm64'
               else 'requires an arm64 Darwin host for owned Mach-O execution')
REFERENCE = os.environ.get('PCC_CPP_REFERENCE') or shutil.which('clang++')
REFERENCE_UNAVAILABLE = None if REFERENCE else 'select a development C++17 compiler with PCC_CPP_REFERENCE or PATH'

OBSERVER = '''.section __TEXT,__text,regular,pure_instructions
.globl _main
.p2align 2
_main:
  stp x29, x30, [sp, #-32]!
  mov x29, sp
  bl _{symbol}
  str w0, [sp, #16]
  add x1, sp, #16
  mov x2, #4
  mov x0, #1
  bl _write
  ldr w0, [sp, #16]
  ldp x29, x30, [sp], #32
  ret
'''
# No library headers: the observer declares the POSIX write it calls.
REFERENCE_MAIN = '''extern "C" long write(int, const void*, unsigned long);
int cpp_test_entry();
int main() {
  int value = cpp_test_entry();
  write(1, &value, 4);
  return value & 255;
}
'''


def run_owned_cpp(units, tmp_path, monkeypatch):
    """Compile every unit, run the observer, return cpp_test_entry()'s int."""
    from pcc.backend import macho_exec, macho_obj
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
    from pcc.frontends.c.passes.context import PassContext
    from pcc.frontends.cpp import CppParser
    from tests.cpp.owned_execution import entry_definition
    objects, entry = [], None
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned compile/link invoked a tool'))
        for filename, source in units:
            parser = CppParser(target_triple=TARGET)
            ast = parser.parse(source, filename)
            selected = entry_definition(parser.scope.lookup('cpp_test_entry'))
            if selected is not None:
                assert entry is None, 'cpp_test_entry is defined more than once'
                entry = selected
            generator = CCodeGenerator(translation_unit_name=filename,
                                       pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
            generator.generate_code(ast)
            objects.append(emit_owned_object(postprocess_ir_text(str(generator.module)), TARGET))
        assert entry is not None, 'no unit defines int cpp_test_entry()'
        sections, undefined = assemble_file(OBSERVER.format(symbol=entry))
        image = macho_exec.link_executable([*objects, macho_obj.emit_object(sections, undefined=undefined)])
    binary = tmp_path / 'owned-cpp'
    binary.write_bytes(image)
    binary.chmod(0o700)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert run.stderr == b'' and len(run.stdout) == 4, run
    value = int.from_bytes(run.stdout, 'little', signed=True)
    assert run.returncode == value & 255, run
    return value


def run_reference_cpp(units, tmp_path):
    """The same unchanged units built by the reference compiler."""
    directory = tmp_path / 'reference'
    directory.mkdir()
    paths = []
    for name, text in (*units, ('observer_main.cpp', REFERENCE_MAIN)):
        (directory / name).write_text(text)
        paths.append(str(directory / name))
    subprocess.run([REFERENCE, '-std=c++17', '-w', '-o', str(directory / 'reference'), *paths],
                   check=True, capture_output=True, timeout=120)
    run = subprocess.run([str(directory / 'reference')], capture_output=True, timeout=10)
    assert len(run.stdout) == 4, run
    return int.from_bytes(run.stdout, 'little', signed=True)


def reference_rejects(units, tmp_path):
    """Whether the reference compiler rejects the units (compile-only)."""
    directory = tmp_path / 'reference-reject'
    directory.mkdir()
    for name, text in units:
        (directory / name).write_text(text)
    # ISO C++17 rules: extensions such as string-literal to char* are errors.
    run = subprocess.run([REFERENCE, '-std=c++17', '-pedantic-errors', '-fsyntax-only',
                          *[str(directory / name) for name, _ in units]],
                         capture_output=True, timeout=120)
    return run.returncode != 0, run.stderr.decode('utf-8', 'replace')
