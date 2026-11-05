"""Use the real freestanding source scanner for whole-owner raw imports.

These are source/IR admission contracts, not a runtime archive build or
native collector test. The freestanding verifier remains fail-closed.
"""
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline_freestanding
from pcc.frontends.python.codegen.runtime_abi import (
    is_freestanding_gc_cross_object_runtime_import,
)
from pcc.frontends.python.pipeline_modes import PyPipelineError


ROOT = Path(__file__).resolve().parents[2]
SLICE = "pcc_gc_visit_object_slots_slice"
SLICE_PARAMETERS = "(c_ptr,c_int64,c_int64,c_ptr,c_ptr,c_ptr)"
SOURCE_IMPORTS = (
    ("freestanding_gc_common_mark_cycle.py", SLICE, SLICE_PARAMETERS),
    ("freestanding_gc_incremental_concurrent_scheduler.py",
     "pcc_gc_trace_referents_work", "(c_ptr,)"),
    ("freestanding_gc_incremental_concurrent_scheduler.py",
     "pcc_gc_trace_stack_drain_work", "(c_int64,c_ptr)"),
)


@pytest.mark.parametrize("filename,symbol,parameters", SOURCE_IMPORTS)
def test_whole_owner_raw_imports_use_actual_source_scanner(filename, symbol, parameters):
    source = (ROOT / "pcc/runtime/py" / filename).read_text()
    bindings = pipeline_freestanding.freestanding_module_scope_extern_bindings(source)
    assert bindings.count((symbol, parameters, "c_int64")) == 1
    assert is_freestanding_gc_cross_object_runtime_import(symbol, parameters, "c_int64")
    assert symbol in pipeline_freestanding.freestanding_gc_cross_object_runtime_imports(source)
    assert symbol in pipeline_freestanding.freestanding_allowed_external_symbols(source)


def test_slice_ir_needs_the_real_verified_source_boundary():
    source = (ROOT / "pcc/runtime/py/freestanding_gc_common_mark_cycle.py").read_text()
    ir = '''\
define i64 @owner(ptr %obj, ptr %state) {
entry:
  %work = call i64 @pcc_gc_visit_object_slots_slice(ptr %obj, i64 0, i64 9223372036854775807, ptr null, ptr null, ptr %state)
  ret i64 %work
}
'''
    with pytest.raises(PyPipelineError, match="managed-runtime reference"):
        pipeline_freestanding.validate_freestanding_ir(ir, set())
    allowed = pipeline_freestanding.freestanding_allowed_external_symbols(source)
    pipeline_freestanding.validate_freestanding_ir(ir, allowed)


@pytest.mark.parametrize("symbol,parameters,returns", (
    (SLICE, "(c_ptr,c_int64,c_ptr,c_ptr,c_ptr)", "c_int64"),
    (SLICE, "(c_ptr,c_int32,c_int64,c_ptr,c_ptr,c_ptr)", "c_int64"),
    (SLICE, SLICE_PARAMETERS, "c_ptr"),
    ("pcc_gc_trace_referents_work", "(c_ptr,)", "c_void"),
    ("pcc_gc_trace_stack_drain_work", "(c_ptr,c_int64)", "c_int64"),
    ("pcc_gc_unregistered_whole_owner_probe", SLICE_PARAMETERS, "c_int64"),
))
def test_wrong_raw_abi_is_still_rejected(symbol, parameters, returns):
    source = f'probe = extern("{symbol}", {parameters}, {returns})\n'
    assert not is_freestanding_gc_cross_object_runtime_import(symbol, parameters, returns)
    assert symbol not in pipeline_freestanding.freestanding_gc_cross_object_runtime_imports(source)
    assert symbol not in pipeline_freestanding.freestanding_allowed_external_symbols(source)
