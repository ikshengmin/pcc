"""Global pointer access width is independent of prior address declarations."""

import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple


@pytest.mark.parametrize("storage", ["external", "word", "tls"])
def test_global_pointer_roundtrip_after_address_or_word_declaration(tmp_path, storage):
    declaration = {
        "external": "",
        "word": 'define_global_i64("unsafe_pointer_cell", 0)',
        "tls": 'define_thread_local_ptr_null("unsafe_pointer_cell")',
    }[storage]
    source = tmp_path / "pointer_probe.py"
    source.write_text('''from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    define_global_i64, define_thread_local_ptr_null,
    global_addr, global_load_ptr, global_store_ptr,
    stack_alloc, store_ptr, load_ptr, ptr_eq, ptr_is_null, null,
)
__pcc_freestanding__ = True
''' + declaration + '''
@c_abi_export("unsafe_pointer_probe")
def probe() -> i64:
    cell = global_addr("unsafe_pointer_cell")
    first = stack_alloc(8)
    second = stack_alloc(8)
    store_ptr(cell, 0, first)
    first_ok: i64 = ptr_eq(global_load_ptr("unsafe_pointer_cell"), first)
    global_store_ptr("unsafe_pointer_cell", second)
    second_ok: i64 = ptr_eq(load_ptr(cell, 0), second)
    global_store_ptr("unsafe_pointer_cell", null())
    empty: i64 = ptr_is_null(global_load_ptr("unsafe_pointer_cell"))
    return first_ok + second_ok + empty
''')
    triple = host_target_triple()
    llvm_ir = tmp_path / "pointer_probe.ll"
    compile_python(str(source), str(llvm_ir), emit_llvm_only=True,
                   python_library=True, libpython_mode="off", backend="self",
                   target_triple=triple)
    text = llvm_ir.read_text()
    body = text.split("define external i64 @unsafe_pointer_probe", 1)[1].split("\n}", 1)[0]
    assert "@py_int_from_i64(" not in body
    queries = [line for line in body.splitlines()
               if " call " in line and "@llvm.threadlocal.address.p0(" in line]
    assert len(queries) == (1 if storage == "tls" else 0)
    obj = tmp_path / "pointer_probe.o"
    obj.write_bytes(emit_owned_object(text, triple))
    global_definition = "void *unsafe_pointer_cell;" if storage == "external" else ""
    if storage == "tls":
        body = '''
extern int pthread_create(void **, void *, void *(*)(void *), void *);
extern int pthread_join(void *, void **);
static void *worker(void *arg) {
    (void)arg;
    for (int i = 0; i < 1000; ++i) if (unsafe_pointer_probe() != 3) return (void *)1;
    return 0;
}
int main(void) {
    void *threads[2] = {0};
    for (int i = 0; i < 2; ++i) if (pthread_create(&threads[i], 0, worker, 0)) return 2;
    for (int i = 0; i < 2; ++i) {
        void *result = 0;
        if (pthread_join(threads[i], &result) || result) return 3;
    }
    return 0;
}
'''
    else:
        body = "int main(void) { return unsafe_pointer_probe() == 3 ? 0 : 1; }"
    csource = "extern long long unsafe_pointer_probe(void);\n" + global_definition + "\n" + body
    cpath = tmp_path / "main.c"
    cpath.write_text(csource)
    evaluator = CEvaluator(backend="self", target_triple=triple)
    units = evaluator.compile_translation_units(
        [TranslationUnit(str(cpath), str(cpath), csource)],
        use_system_cpp=False, use_compile_cache=False,
    )
    binary = tmp_path / "pointer_probe"
    evaluator.emit_executable(units, str(binary), link_args=[str(obj)])
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
