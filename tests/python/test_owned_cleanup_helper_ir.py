"""Actual helper ABI, caller cleanup shape and precise root-state verification."""
from pathlib import Path
import re

import pytest

from pcc.backend.self_backend_call_flags import classify_call_flags
from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect
from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_modules
from tests.owned_ir_validation import verify_ir_text
from tests.python.test_slot_call_cleanup_reuse import setup_roots, IdentityOnly
from tests.python.test_slot_call_lexical_roots import _emit

ROOT = Path(__file__).resolve().parents[2]
ROOT_HELPER = 'py_cleanup_one_root_preserving_exception'
LEASE_HELPER = 'py_cleanup_one_lease_preserving_exception'


def verify_stackmaps(text):
    verify_ir_text(text)
    prepared = prepare_module_for_target(text, aggregate_returned_indirect=_aggregate_returned_indirect)
    plans = build_stack_map_plans(prepared.functions, prepared.globals_, target='x86_64-linux')
    assert len(plans) == len(prepared.functions)


@pytest.mark.parametrize('threads', ['0', '1'])
def test_actual_runtime_helper_abi_and_synchronous_body(tmp_path, monkeypatch, threads):
    monkeypatch.setenv('PCC_WITH_THREADS', threads)
    output = tmp_path / 'cleanup.ll'
    _compile_runtime_module('py_cleanup_runtime', str(ROOT / 'pcc/runtime/py/py_cleanup_runtime.py'),
                            str(output), 'x86_64-unknown-linux-gnu')
    text = output.read_text()
    verify_stackmaps(text)
    for name, signature, disposal in (
        (ROOT_HELPER, r'ptr %slot', 'pcc_gc_store_root'),
        (LEASE_HELPER, r'ptr %slot, i64 %acquired', 'pcc_gc_foreign_lease_release'),
    ):
        match = re.search(r'^define[^\n]*void @' + name + r'\(' + signature + r'\)[^\n]*\{\n(.*?)^}', text, re.M | re.S)
        assert match
        body = match.group(1)
        calls = re.findall(r'\bcall\b[^\n]*?@([\w.]+)\(', body)
        assert calls == ['pcc_gc_frame_enter_lifo', 'py_tls_exc_swap_slot', disposal,
                         'py_clear_exception', 'py_tls_exc_swap_slot', 'pcc_gc_frame_leave_lifo']
        assert body.index('store ptr null') < body.index('@pcc_gc_frame_enter_lifo')
        assert 'alloca [8 x i8]' in body
        assert 'br ' not in body and 'Py_' not in body
        assert 'pcc_thread_' not in body
        # Ordinary safepoint-capable call; never a hidden caller-frame transition.
        assert classify_call_flags(name, False, False) == 0
    assert 'i64 %acquired)' in text
    assert 'py_cleanup_runtime' in runtime_modules(str(ROOT / 'pcc/runtime'), 'x86_64-unknown-linux-gnu', threads == '1')
    assert re.search(r'py_cleanup_runtime.o:.*\n\t[^\n]*PCC_WITH_THREADS=0', (ROOT / 'pcc/runtime/Makefile').read_text())
    assert __import__('os').environ['PCC_WITH_THREADS'] == threads


def test_exact_single_shapes_have_no_caller_exception_alloca():
    model, first, second, target = setup_roots()
    model._slot_call_cleanup_block((first,), target)
    assert model.events == [(ROOT_HELPER, (first,)), ('leave', first), ('branch', target)]
    model.events.clear()
    token = IdentityOnly()
    model._slot_call_cleanup_block((), target, ((second, token),))
    assert model.events == [(LEASE_HELPER, (second, token)), ('branch', target)]


@pytest.mark.parametrize('shape', ['multiple-roots', 'multiple-leases', 'mixed', 'flagged', 'non-lifo'])
def test_ineligible_shapes_keep_inline_tls_protocol(shape):
    model, first, second, target = setup_roots()
    roots, leases = (first,), ()
    if shape == 'multiple-roots': roots = (first, second)
    if shape == 'multiple-leases': roots, leases = (), ((first, IdentityOnly()), (second, IdentityOnly()))
    if shape == 'mixed': leases = ((second, IdentityOnly()),)
    if shape == 'flagged': model._slot_call_root_records[0] = (first, IdentityOnly(), True)
    if shape == 'non-lifo': model._slot_call_root_records[0] = (first, None, False)
    model._slot_call_cleanup_block(roots, target, leases)
    calls = [event[0] for event in model.events]
    assert calls.count('swap') == 2
    assert ROOT_HELPER not in calls and LEASE_HELPER not in calls


@pytest.mark.parametrize('source', [
    'def probe(f, x):\n    if x:\n        return f(x)\n    return f(None)\n',
    'def probe(f, values):\n    for x in values:\n        try:\n            try:\n                f(x)\n            except TypeError:\n                f(None)\n        except ValueError:\n            pass\n',
    'def probe(f, x):\n    try:\n        yield f(x)\n    except ValueError:\n        yield None\n',
])
def test_real_caller_cleanup_root_provenance_and_stackmaps(source):
    _, text = _emit(source)
    verify_stackmaps(text)
    blocks = re.findall(r'^call.slot.cleanup[^:]*:\n(.*?)(?=^[\w.]+:|^})', text, re.M | re.S)
    assert blocks
    for block in blocks:
        if '@' + ROOT_HELPER + '(' in block:
            assert '@pcc_gc_frame_leave_lifo(' in block
            assert block.index('@' + ROOT_HELPER) < block.index('@pcc_gc_frame_leave_lifo')
            assert '@py_tls_exc_swap_slot(' not in block
        if '@' + LEASE_HELPER + '(' in block:
            assert '@pcc_gc_frame_leave_lifo(' not in block
            assert '@pcc_gc_store_root(' not in block
