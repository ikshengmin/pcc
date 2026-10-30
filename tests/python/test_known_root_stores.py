"""Compiler-proven root owners keep the checked ABI at foreign boundaries."""

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_generator_frame_default_bulk import PROGRAM


def test_reused_codegen_rechecks_current_module_and_switch(monkeypatch):
    from pcc.frontends.python import type_infer
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.codegen.layer1 import L1CodeGen

    body = "def replace(value):\n    local = [value]\n    local = [local, value]\n    return local\n"
    ordinary = type_infer.infer_module(parse_and_lift(body, "ordinary.py", "ordinary"))
    port = type_infer.infer_module(parse_and_lift("__pcc_runtime_port__ = True\n" + body, "port.py", "port"))
    host = L1CodeGen(ordinary, ir_scaffold_mode="on")
    host._strict_no_libpython = True
    for module, enabled, expected in ((ordinary, "1", True), (port, "1", False), (ordinary, "0", False), (ordinary, "1", True)):
        monkeypatch.setenv("PCC_KNOWN_ROOT_STORES", enabled)
        text = str(host.generate(module))
        calls = [line for line in text.splitlines() if "call " in line]
        assert any("@pcc_gc_store_root_known(" in line for line in calls) == expected
        assert any("@pcc_gc_store_root" in line for line in calls)


def test_known_root_effects_preserve_the_generic_contract():
    from pcc.diagnostics.contracts.runtime_effects import runtime_arrow

    for name in ("pcc_gc_store_root", "pcc_gc_store_root_take"):
        original = runtime_arrow(name)
        known = runtime_arrow(name + "_known")
        assert (known.inputs, known.outputs, known.effects) == (original.inputs, original.outputs, original.effects)


@pytest.mark.parametrize("enabled,raw_import,expected", (
    ("0", "", False),
    ("1", "", True),
    ("1", "    from pcc import i64\n", False),
))
def test_root_store_selection_preserves_raw_scopes(tmp_path, monkeypatch, enabled, raw_import, expected):
    monkeypatch.setenv("PCC_KNOWN_ROOT_STORES", enabled)
    source = tmp_path / "root_owner.py"
    source.write_text("def replace(value):\n" + raw_import +
                      "    local = [value]\n    local = [local, value]\n    return local\n")
    output = tmp_path / "root_owner.ll"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    calls = [line for line in output.read_text().splitlines() if "call " in line]
    assert any("@pcc_gc_store_root_known(" in line for line in calls) == expected


@pytest.mark.integration
def test_known_roots_keep_native_lifetimes_all_collectors(tmp_path, monkeypatch, pcc_runtime_archive):
    monkeypatch.setenv("PCC_KNOWN_ROOT_STORES", "1")
    source = tmp_path / "lifetime.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "lifetime"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (run.returncode, run.stdout, run.stderr) == (0, "BULK_FRAME_DEFAULT_OK\n", ""), backend


@pytest.mark.integration
def test_known_root_copy_take_alias_and_verification(tmp_path, pcc_runtime_archive):
    source = tmp_path / "roots.c"
    source.write_text(r'''#include "py_runtime.h"
#include <stdlib.h>
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map[1] = {2};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_list_new(0);
    for (int checks = 0; checks < 2; checks++) {
        pcc_gc_set_known_ref_checks(checks);
        int64_t before = ((PyObjectHeader *)roots[0])->refcount;
        pcc_gc_store_root_known(&roots[1], roots[0]);
        if (((PyObjectHeader *)roots[0])->refcount != before + 1) return 2;
        pcc_gc_store_root_known(&roots[1], roots[0]);
        if (((PyObjectHeader *)roots[0])->refcount != before + 1) return 3;
        py_incref(roots[0]);
        pcc_gc_store_root_take_known(&roots[1], roots[0]);
        if (((PyObjectHeader *)roots[0])->refcount != before + 1) return 4;
        py_gc_collect();
        if (roots[0] != roots[1] || py_list_len(roots[1]) != 0) return 5;
        pcc_gc_store_root_take_known(&roots[1], NULL);
        if (((PyObjectHeader *)roots[0])->refcount != before) return 6;
        pcc_gc_store_root_take_known(&roots[1], py_int_from_i64(17));
        pcc_gc_store_root_known(&roots[1], NULL);
    }
    pcc_gc_store_root_take_known(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    puts("KNOWN_ROOTS_OK");
    return 0;
}
''')
    binary = tmp_path / "roots"
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive)
    for backend in range(5):
        run = subprocess.run([str(binary), str(backend)], capture_output=True, text=True, timeout=20)
        assert (run.returncode, run.stdout, run.stderr) == (0, "KNOWN_ROOTS_OK\n", ""), backend
