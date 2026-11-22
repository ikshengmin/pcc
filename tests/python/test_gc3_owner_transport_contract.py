"""Executable contract for GC3's proposed header-only store classification.

The probe is not linked into the runtime. Its arguments follow the managed
heap-store ABI: non-immediate pointers name live objects. Roots pass no
owner; an allocated old object need not have the legacy OLD bit.
"""
import os
import platform
import re
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py.py_abi_constants import PYOBJECTHEADER_FLAGS_OFFSET
from tests.python.test_owned_external_tls import _c_ir


# pcc/runtime/src/py_internal.h names bit 7 PY_FLAG_GC_YOUNG.
YOUNG_BIT = 1 << 7
TARGET = "x86_64-unknown-linux-gnu"
ENTRY = """
.intel_syntax noprefix
.text
.globl _start
.type _start, @function
_start:
    and rsp, -16
    call main
    mov edi, eax
    mov eax, 60
    syscall
"""


def _probe_ir(tmp_path):
    source = tmp_path / "owner_transport.py"
    source.write_text(f"""from pcc.extern import c_abi_export, c_ptr
from pcc import i64
from pcc.unsafe import atomic_load_i32, is_tagged_int, ptr_is_null
__pcc_freestanding__ = True
YOUNG_BIT = {YOUNG_BIT}
FLAGS_OFFSET = {PYOBJECTHEADER_FLAGS_OFFSET}

@c_abi_export("gc3_edge_needs_card")
def gc3_edge_needs_card(backend: i64, owner: c_ptr, value: c_ptr) -> i64:
    if backend != 3:
        return 0
    if ptr_is_null(owner) or is_tagged_int(owner):
        return 0
    if ptr_is_null(value) or is_tagged_int(value):
        return 0
    if atomic_load_i32(owner, FLAGS_OFFSET, "acquire") & YOUNG_BIT:
        return 0
    if atomic_load_i32(value, FLAGS_OFFSET, "acquire") & YOUNG_BIT:
        return 1
    return 0
""")
    ir = tmp_path / "owner_transport.ll"
    compile_python(str(source), str(ir), emit_llvm_only=True, python_library=True,
                   backend="self", target_triple=TARGET)
    return ir.read_text()


def test_gc3_edge_candidate_uses_only_two_header_reads(tmp_path):
    ir = _probe_ir(tmp_path)
    body = next(section.split("\n}", 1)[0] for section in ir.split("\ndefine ")
                if "@gc3_edge_needs_card(" in section.splitlines()[0])
    assert len(re.findall(r"\bload atomic i32\b", body)) == 2
    assert not re.search(r"\b(call|invoke|store)\b", body)
    for forbidden in ("index", "radix", "mark_active"):
        assert forbidden not in body


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: platform.system() == "Linux" and platform.machine() == "x86_64")
def test_owned_gc3_classifier_takes_owner_without_consulting_an_index(tmp_path):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    driver = _c_ir(f"""
typedef struct {{ long reference_count; unsigned int tag; unsigned int flags; }} Header;
extern long gc3_edge_needs_card(long backend, void *owner, void *value);
int main(void) {{
    Header old = {{0, 1, 0}}, young = {{0, 1, {YOUNG_BIT}}};
    for (int gc = 0; gc <= 4; ++gc) {{
        if (gc == 3) continue;
        /* Non-GC3 must return before any header access. */
        if (gc3_edge_needs_card(gc, (void *)2, (void *)4)) return 1;
    }}
    if (!gc3_edge_needs_card(3, &old, &young)) return 2;
    if (gc3_edge_needs_card(3, &young, &young)) return 3;
    if (gc3_edge_needs_card(3, &old, &old)) return 4;
    if (gc3_edge_needs_card(3, (void *)0, &young)) return 5;
    if (gc3_edge_needs_card(3, &old, (void *)0)) return 6;
    if (gc3_edge_needs_card(3, (void *)1, &young)) return 7;
    if (gc3_edge_needs_card(3, &old, (void *)1)) return 8;
    return 0;
}}
""", TARGET)
    objects = [parse_relocatable(emit_owned_object(ir, TARGET))
               for ir in (_probe_ir(tmp_path), driver)]
    program = tmp_path / "owner_transport"
    program.write_bytes(link_static_executable([assemble_file(ENTRY), *objects], entry="_start"))
    program.chmod(0o755)
    run = subprocess.run([str(program)], capture_output=True, timeout=15,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b"")
