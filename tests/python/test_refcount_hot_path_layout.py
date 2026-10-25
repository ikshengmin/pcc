"""The common refcount entry must not inherit checked-path scratch frames."""
from pathlib import Path
import re

from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.owned_runtime_build import runtime_ir_passes
from pcc.ir.optimization.driver import optimize_ir


def test_hot_refcount_entries_keep_atomic_updates_outside_preparation_frames(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / 'py_obj.ll'
    compile_python(str(root / 'pcc/runtime/py/py_obj.py'), str(output),
                   emit_llvm_only=True, python_library=True, libpython_mode='off')
    text = optimize_ir(output.read_text(), runtime_ir_passes(str(root / 'pcc/runtime')))
    def body(name):
        found = re.search(r'^define[^\n]*@' + re.escape(name) + r'\([^\n]*\n(.*?)^}', text, re.M | re.S)
        assert found is not None, name
        return found.group(1)
    for name in ('py_incref', 'py_decref'):
        hot = body(name)
        cold_name = 'user_py_obj__' + name + '_checked_tail'
        assert '@' + cold_name + '(' in hot
        assert 'alloca ' not in hot
        assert 'alloca ' in body(cold_name)
        assert 'pcc_gc_refcount_atomic' in hot
        assert 'pcc_diagnostics_runtime_log_fast_state' in hot
    decrement = body('py_decref')
    assert 'cmpxchg ' in decrement and 'release monotonic' in decrement
    assert '_decref_above_one_atomic' not in decrement
