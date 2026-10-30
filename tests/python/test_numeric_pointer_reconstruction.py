"""Numeric metadata addresses use pointer casts and survive optimization."""

from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple


ROOT = Path(__file__).resolve().parents[2]
ALLOCATOR = ROOT / "pcc/runtime/py/freestanding_allocator.py"


@pytest.mark.integration
@pytest.mark.parametrize("emitter", ("self", "llvm_o2_oracle"))
def test_cold_granule_lookup_preserves_numeric_pointer_provenance(tmp_path, emitter):
    """LLVM is a labeled development oracle, never the production emitter."""
    if emitter != "self" and shutil.which("clang") is None:
        pytest.skip("external LLVM optimization oracle is unavailable")
    if sys.platform == "win32":
        pytest.skip("standalone POSIX platform-ABI driver")
    target = host_target_triple()
    ir = tmp_path / "allocator.ll"
    compile_python(str(ALLOCATOR), str(ir), emit_llvm_only=True,
                   python_library=True, backend="self", libpython_mode="off",
                   target_triple=target)
    assert "getelementptr i8, ptr null, i64" not in ir.read_text()
    obj = tmp_path / "allocator.o"
    if emitter == "self":
        with patch("subprocess.Popen", side_effect=AssertionError("self emission spawned an external tool")):
            obj.write_bytes(emit_owned_object(ir.read_text(), target))
    else:
        built = subprocess.run(["clang", "-target", target, "-O2", "-c", str(ir), "-o", str(obj)],
                               capture_output=True, text=True, timeout=45)
        assert built.returncode == 0, built.stdout + built.stderr
    source = tmp_path / "cold.c"
    source.write_text(r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int32_t pcc_gc_refcount_provenance_probe = 0;
void pcc_platform_abort(void) { abort(); }
int64_t pcc_platform_write(int64_t fd, const void *data, int64_t size) {
    return (int64_t)write((int)fd, data, (size_t)size);
}
extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_gc_granule_kind(void *);
extern int64_t pcc_gc_granule_object_publish(void *);
extern int64_t pcc_gc_granule_object_retire(void *);
extern int64_t pcc_gc_granule_is_object_start(void *);
extern int64_t pcc_allocator_granule_span_cache_set_fill(int64_t);
int main(void) {
    int64_t sizes[5] = {16, 32, 64, 128, 4096};
    pcc_allocator_granule_span_cache_set_fill(0);
    for (int i = 0; i < 5; i++) {
        void *object = pcc_allocator_alloc_object(sizes[i]);
        if (!object || pcc_gc_granule_kind(object) != 1) return 1;
        *(int64_t *)object = 1;
        *(int32_t *)((char *)object + 8) = 5;
        *(int32_t *)((char *)object + 12) = 0;
        if (pcc_gc_granule_object_publish(object) != 1) return 2;
        pcc_allocator_granule_span_cache_set_fill(0);
        if (pcc_gc_granule_kind(object) != 1 || pcc_gc_granule_is_object_start(object) != 1) return 3;
        if (pcc_gc_granule_kind((char *)object + 1) != 1
            || pcc_gc_granule_is_object_start((char *)object + 1) != -1) return 4;
        if (pcc_gc_granule_object_retire(object) != 1) return 5;
        if (pcc_gc_granule_is_object_start(object) != -1) return 6;
        free(object);
    }
    puts("NUMERIC_POINTER_OK");
    return 0;
}
''')
    evaluator = CEvaluator(backend="self", target_triple=target)
    binary = tmp_path / "cold"
    with patch("subprocess.Popen", side_effect=AssertionError("owned C driver spawned an external tool")):
        units = evaluator.compile_translation_units(
            [TranslationUnit(str(source), str(source), source.read_text())],
            use_system_cpp=False, use_compile_cache=False,
            include_dirs=[str(ROOT / "utils/fake_libc_include")],
        )
        evaluator.emit_executable(units, str(binary), link_args=[str(obj)])
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    assert (run.returncode, run.stdout, run.stderr) == (0, "NUMERIC_POINTER_OK\n", "")
