"""Root readers preserve polling, slot healing, and all five collectors."""
import ast
import os
from pathlib import Path
import re
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("backend,barrier,slot,value,forwarding", (
    (0, 0, 0, 0, 0),
    (0, 0, 100, 42, 0),
    (1, 1, 100, 42, 0),
    (3, 1, 100, 42, 0),
    (3, 1, 100, 42, 1),
    (4, 1, 100, 0, 1),
    (4, 1, 100, 1, 1),
    (4, 1, 100, 42, 1),
))
def test_root_reads_preserve_null_immediates_and_relocation(
    name, backend, barrier, slot, value, forwarding,
):
    events = []
    memory = {
        "pcc_gc_config_initialized": 1,
        "pcc_gc_backend_selected": backend,
        "pcc_gc_read_barrier_enabled": barrier,
        100: value,
    }

    def read(source, offset):
        events.append(("read", source, offset))
        return memory[source]

    def write(source, offset, resolved):
        events.append(("write", source, offset, resolved))
        memory[source] = resolved

    def resolve(old):
        events.append(("resolve", old))
        return 242

    def swap(source, offset, expected, desired, success, failure):
        events.append(("swap", source, offset, expected, desired))
        observed = memory[source]
        if observed == expected:
            memory[source] = desired
        return observed

    ns = {
        "global_addr": lambda name: name,
        "load_i32": lambda address, offset: memory[address],
        "load_ptr": read,
        "store_ptr": write,
        "ptr_is_null": lambda pointer: pointer == 0,
        "ptr_eq": lambda left, right: left == right,
        "null": lambda: 0,
        "is_tagged_int": lambda pointer: pointer == 1,
        "_gc_backend_fast": lambda: backend,
        "_gc_forwarding_population": lambda: forwarding,
        "_gc_backend4_should_check_slot": lambda source: 1,
        "_gc_relocation_candidate": lambda pointer: pointer == 42,
        "pcc_gc_backend4_slot_needs_resolve": lambda pointer: pointer == 42,
        "pcc_gc_note_load": lambda: events.append(("note",)),
        "pcc_gc_note_relocation_read": resolve,
        "atomic_cas_i64": swap,
        "ptr_to_int": lambda pointer: pointer,
        "py_incref": lambda pointer: events.append(("incref", pointer)),
        "py_decref": lambda pointer: events.append(("decref", pointer)),
    }
    path = ROOT / "pcc/runtime/py/py_obj.py"
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in (name, "_heal_forwarded_field")]
    assert len(nodes) == 2
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(nodes, type_ignores=[]), str(path), "exec"), ns)
    result = ns[name](200, slot)
    if not slot:
        assert result == 0 and events == []
    elif barrier and forwarding and value == 42 and backend == 3:
        # A GC3 field (owner 200) moves its reference from the shell to the
        # copy as collector promotion does (test_gc3_forwarded_field_heal.py).
        assert result == 242 and memory[slot] == 242
        assert events == [
            ("read", slot, 0), ("note",), ("resolve", value),
            ("incref", 242), ("swap", slot, 0, value, 242), ("decref", value),
        ]
    elif barrier and forwarding and value == 42:
        assert result == 242 and memory[slot] == 242
        assert events == [
            ("read", slot, 0), ("note",), ("resolve", value),
            ("write", slot, 0, 242),
        ]
    else:
        assert result == value and events == [("read", slot, 0)]


PROBE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1]))) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(4);
    roots[1] = py_int_from_i64(23);
    if (pcc_gc_load_ptr(NULL, &roots[0]) != roots[0]) return 2;
    if (pcc_gc_load_borrowed_ptr(NULL, &roots[0]) != roots[0]) return 3;
    if (pcc_gc_load_ptr(NULL, &roots[1]) != roots[1]) return 4;
    if (pcc_gc_load_borrowed_ptr(NULL, &roots[1]) != roots[1]) return 5;
    if (pcc_gc_load_ptr(NULL, NULL) != NULL) return 6;
    if (pcc_gc_load_borrowed_ptr(NULL, NULL) != NULL) return 7;
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    puts("ROOT_READ_OK");
    return 0;
}
'''


def test_native_root_reads_execute_on_five_gc(tmp_path, pcc_runtime_archive):
    source=tmp_path/"read.c";source.write_text(PROBE)
    output=tmp_path/"read"
    compile_owned_c_with_runtime(source,output,runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        ran=subprocess.run([str(output),str(gc)],capture_output=True,text=True,timeout=20,
                           env=dict(os.environ,PATH="/nonexistent"))
        assert (ran.returncode,ran.stdout,ran.stderr)==(0,"ROOT_READ_OK\n",""),gc


def _reader_module_ir(pcc_runtime_archive):
    """Keep the real compiled readers and turn unrelated bodies into externs."""
    path = Path(str(pcc_runtime_archive) + ".objects") / "py_obj.ll"
    text = path.read_text()

    def keep_reader(match):
        body = match.group(0)
        name = body.split("@", 1)[1].split("(", 1)[0]
        if name in NAMES:
            return body
        header = body.splitlines()[0].removesuffix("{").rstrip()
        return re.sub(r"^define (?:external |internal )?", "declare ", header)

    return re.sub(r"^define [^\n]*\{\n.*?^}", keep_reader, text, flags=re.M | re.S)


def test_compiled_read_entries_preserve_stop_poll_and_protection(pcc_runtime_archive):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm

    text = _reader_module_ir(pcc_runtime_archive)
    assembly = emit_aarch64_darwin_asm(text, optimize=True)
    for name in NAMES:
        ir_body = re.search(r"^define [^\n]*@" + name + r"\([^\n]*\{\n.*?^}",
                            text, re.M | re.S).group(0)
        assert ir_body.index("@pcc_thread_stop_requested") < ir_body.index("load ptr")
        assert "@pcc_thread_safepoint()" in ir_body
        body = re.search(r"_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                         assembly, re.S).group(1)
        assert "paciasp" in body and "  bl _pcc_thread_safepoint" in body, body


@pytest.mark.parametrize("name", NAMES)
def test_withdrawn_checked_read_names_are_not_raw_abi(name):
    from pcc.frontends.python.codegen.runtime_abi import is_freestanding_gc_cross_object_runtime_import

    for parameters, result in (("(c_ptr,c_ptr)", "c_ptr"), ("(c_ptr,)", "c_ptr"),
                               ("(c_ptr,c_ptr)", "c_obj")):
        assert not is_freestanding_gc_cross_object_runtime_import(
            name + "_checked", parameters, result,
        )


@pytest.mark.parametrize("target,magic,machine", (
    ("x86_64-unknown-linux-gnu", b"\x7fELF", 62),
    ("aarch64-unknown-linux-gnu", b"\x7fELF", 183),
    ("x86_64-pc-windows-msvc", b"\x64\x86", None),
))
def test_root_read_ir_reaches_owned_non_darwin_objects(pcc_runtime_archive, target, magic, machine):
    import struct
    from pcc.backend.owned_object_emit import emit_owned_object

    source = re.sub(r'^target triple = "[^"\n]*"', 'target triple = "' + target + '"',
                    _reader_module_ir(pcc_runtime_archive), flags=re.M)
    data = emit_owned_object(source, target)
    assert data.startswith(magic)
    if machine is not None:
        assert struct.unpack_from("<H", data, 18)[0] == machine
