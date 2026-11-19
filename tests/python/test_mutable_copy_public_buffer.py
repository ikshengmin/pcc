"""Public values and live C Py_buffer metadata of copies from mutable leaves."""
import json
import os
from pathlib import Path
import subprocess
import sys


PROGRAM = r'''import gc
def main():
    src = bytearray(b'A\x00B')
    immutable = bytes(src)
    mutable = bytearray(src)
    src.clear()
    gc.collect()
    print(signature(src), signature(immutable), signature(mutable))
    mutable[2] = 90
    print(signature(immutable), signature(mutable))
    live = bytearray(b'C\x00D')
    a = bytes(memoryview(memoryview(live)))
    b = bytearray(memoryview(live))
    live[0] = 88
    gc.collect()
    print(signature(live), signature(a), signature(b))
main()
'''

REFERENCE = r'''def signature(obj):
    with memoryview(obj) as view:
        assert view.itemsize == 1
        return len(view) * 1000000 + int(view.readonly) * 100000 + sum((i + 1) * b for i, b in enumerate(view))
'''
NATIVE = '''from pcc.extern import c_int64, c_obj, extern
signature = extern("fixture_buffer_copy_signature", (c_obj,), c_int64)
'''


def test_mutable_leaf_copy_public_buffer(tmp_path, python_program_compiler, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.python.pipeline_targets import host_target_triple
    root = Path(__file__).resolve().parents[2]
    c_file = Path(__file__).with_name('fixtures') / 'gc_mutable_copy_public_buffer.c'
    reference = tmp_path / 'reference.py'
    reference.write_text(REFERENCE + PROGRAM)
    oracle = subprocess.run([sys.executable, '-I', str(reference)], capture_output=True,
                            text=True, timeout=8, check=True)
    assert len(oracle.stdout.splitlines()) == 3 and not oracle.stderr
    target = host_target_triple()
    evaluator = CEvaluator(backend='self', target_triple=target)
    units = evaluator.compile_translation_units(
        [TranslationUnit(str(c_file), str(c_file), c_file.read_text())],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(root/'pcc/runtime/include'), str(root/'utils/fake_libc_include')],
    )
    assert len(units) == 1
    helper = tmp_path/'signature.o'
    helper.write_bytes(emit_owned_object(units[0][1], target))
    src = tmp_path/'copy_probe.py'
    src.write_text(NATIVE + PROGRAM)
    exe = tmp_path/'copy_probe'
    python_program_compiler(str(src), str(exe), backend='self',
                            runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    rows = {'cpython': oracle.stdout}
    for mode in range(5):
        env = dict(os.environ, PCC_GC_BACKEND=str(mode))
        env.pop('PCC_GC_TRACING_ONLY', None)
        env.pop('PCC_GC4_TRACING_ONLY', None)
        out = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=12)
        rows[str(mode)] = [out.returncode, out.stdout, out.stderr]
        (tmp_path/'copy_rows.json').write_text(json.dumps(rows, indent=2))
        assert (out.returncode, out.stdout, out.stderr) == (0, oracle.stdout, ''), (mode, out)
