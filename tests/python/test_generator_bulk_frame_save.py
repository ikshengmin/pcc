"""Batch saving keeps source/frame owners and the tracing-collector fallback."""

import re
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime


def test_rejected_bulk_save_stays_out_of_application_codegen(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_MOVE_GENERATOR_FRAME_OWNERS", "0")

    source = tmp_path / "suspended.py"
    source.write_text('''def suspended(seed):
    first = [seed]
    second = first
    yield first
    second.append(seed)
    yield second
iterator = suspended(42)
print(next(iterator))
print(next(iterator))
''')
    calls = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_BULK_GENERATOR_FRAME_SAVE", enabled)
        output = tmp_path / ("save_" + enabled + ".ll")
        compile_python(str(source), str(output), backend="self",
                       ir_scaffold_mode="on", emit_llvm_only=True)
        text = output.read_text()
        body = re.search(r"define[^\n]*suspended__gen_resume[^\n]*\{\n(.*?)\n\}", text, re.S)
        assert body is not None
        calls.append(len(re.findall(r"call[^\n]*@py_gen_frame_save\(", body.group(1))))
        # The current private-frame path moves the local owner into its
        # matching frame slot at each yield. Terminal frame_set(None) calls
        # are not save operations and cannot satisfy this control.
        saved = re.findall(
            r"call[^\n]*@py_gen_frame_move\(ptr [^,]+, i64 ([0-9]+), "
            r"ptr %(seed|first|second)\.addr\.[^,]+, i64 (%gen\.save\.owned\.i64\.[^)]+)\)",
            body.group(1),
        )
        assert [(slot, local) for slot, local, _ in saved] == [
            ("0", "seed"), ("1", "first"), ("2", "second"),
        ] * 2
        owners = [owner for _, _, owner in saved]
        assert len(set(owners)) == 6
        for owner in owners:
            assert re.search(
                r"^\s*" + re.escape(owner) + r" = zext i1 1 to i64$",
                body.group(1), re.M,
            ), "private frame must consume exactly the owned local"
        assert len(re.findall(r"call[^\n]*@py_gen_frame_move\(", body.group(1))) == 6
    assert calls == [0, 0], "the rejected experiment must not alter application codegen"


def test_bulk_save_keeps_aliases_and_falls_back_without_mutation(tmp_path, pcc_runtime_archive):
    source = tmp_path / "bulk_save.c"
    source.write_text('''#include "py_runtime.h"
#include <stdio.h>
extern int64_t py_gen_frame_save(PyObject *, void *, int64_t);
int main(void) {
    PyObject *roots[3] = {py_gen_frame_new(2), py_list_new(0), py_list_new(0)};
    int32_t map[1] = {3};
    pcc_gc_frame_enter(map, roots);
    PyObject *value = py_int_from_i64(42);
    py_list_append(roots[1], value);
    py_decref(value);
    PyObject **slots[2] = {&roots[1], &roots[2]};
    if (py_gen_frame_save(roots[0], slots, 1) != 0) return 2;
    if (py_gen_frame_save(roots[0], NULL, 2) != 0) return 3;
    value = py_list_get(roots[0], 0);
    if (value != py_None) return 4;
    py_decref(value);
    py_gc_collect();
    int64_t saved = py_gen_frame_save(roots[0], slots, 2);
    if (saved != 1) return 5;
    value = py_list_get(roots[0], 0);
    if (value != roots[1]) return 6;
    py_decref(value);
    value = py_list_get(roots[0], 1);
    if (value != roots[2]) return 6;
    py_decref(value);
    /* Both saved slots may alias the same independently owned source. */
    pcc_gc_store_root(&roots[2], roots[1]);
    saved = py_gen_frame_save(roots[0], slots, 2);
    if (saved != 1) return 9;
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_store_root(&roots[2], NULL);
    py_gc_collect();
    PyObject *left = py_list_get(roots[0], 0);
    PyObject *right = py_list_get(roots[0], 1);
    if (left != right || py_list_len(left) != 1) return 7;
    value = py_list_get(left, 0);
    int overflow = 0;
    if (py_int_to_i64(value, &overflow) != 42 || overflow) return 8;
    py_decref(value);
    py_decref(left);
    py_decref(right);
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("bulk-frame-save-ok");
    return 0;
}
''')
    executable = tmp_path / "bulk_save"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == "bulk-frame-save-ok"
