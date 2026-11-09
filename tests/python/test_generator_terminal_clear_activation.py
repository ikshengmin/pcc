"""Conditional terminal activation: frontend/optimized IR and native/reference.

This file does not by itself qualify activation. An accepted-result observation
from an actual terminal caller and a real pending-stop run remain mandatory;
calling the primitive directly or seeing a symbol is not that observation.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

from tests.python.root_slot_contract import RootSlotContract
from tests.python.test_generator_operand_root_groups import (
    _parsed_function, source_generator_group_ir,
)
from tests.python.test_generator_moving_frame_take import _native, _run
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/fixtures/native/constructor/generator_terminal_clear.py"
BACKEND_ORDER = (3, 4, 0, 1, 2)


def _assert_terminal_index_progress(roots, definitions, index, count, length,
                                    test, attempt, accepted, scalar_test, clear, setter):
    """Tie updates to the real loop-carried memory cell or exact PHI inputs."""
    scalar_check = definitions[scalar_test.terminator.data[0]]
    assert scalar_check.kind == "icmp" and scalar_check.data[0] == "slt"
    scalar_index = scalar_check.data[3]
    assert scalar_check.data[4] == length
    assert roots.args(setter)[1] == scalar_index, "scalar setter must use the tested index"

    def increment(block, left, right):
        matches = [ins for ins in block.instructions
                   if ins.kind == "binop" and ins.data[0] == "add"
                   and ins.data[3:] == (left, right)]
        assert len(matches) == 1, "loop needs its exact index increment"
        return matches[0].data[1]

    next_group = increment(accepted, index, count)
    next_scalar = increment(clear, scalar_index, "1")
    phis = {phi.dest: (block.name, phi) for block in roots.blocks for phi in block.phis}
    if index in phis:
        # Fail closed on mixed or unfamiliar promotion, rather than accepting
        # an unrelated add that never feeds the loop's actual index.
        assert scalar_index in phis, "unsupported mixed index representation"
        group_block, group_phi = phis[index]
        scalar_block, scalar_phi = phis[scalar_index]
        assert group_block == test.name and scalar_block == scalar_test.name
        group_edges = [(edge.label, edge.value) for edge in group_phi.incoming]
        assert len(group_edges) == 2
        assert (accepted.name, next_group) in group_edges, "accepted increment must feed group PHI"
        initial = [edge for edge in group_edges if edge[0] != accepted.name]
        assert len(initial) == 1 and initial[0][1] == "0"
        scalar_edges = [(edge.label, edge.value) for edge in scalar_phi.incoming]
        assert len(scalar_edges) == 3 and set(scalar_edges) == {
            (test.name, index), (attempt.name, index), (clear.name, next_scalar),
        }, "scalar PHI must preserve rejected index and consume +1 backedge"
    else:
        group_load, scalar_load = definitions[index], definitions[scalar_index]
        assert group_load.kind == scalar_load.kind == "load", "unsupported index representation"
        index_cell = roots.slot(group_load.data[3])
        assert roots.slot(scalar_load.data[3]) == index_cell, "both loops must use the same index cell"
        # The emitter's i64 index is a direct entry alloca, never a GEP.
        assert index_cell[1] == 0 and index_cell[0] in roots.allocas
        stores = [(block.name, ins.data[1]) for block, _, ins in roots.rows
                  if ins.kind == "store" and roots.symbol(ins.data[3]) == index_cell[0]]
        assert len(stores) == 3, "index needs only initialization and its two backedge stores"
        assert (accepted.name, next_group) in stores, "accepted increment must reach index store"
        assert (clear.name, next_scalar) in stores, "scalar increment must reach index store"
        initial = [item for item in stores if item[0] not in (accepted.name, clear.name)]
        assert len(initial) == 1 and initial[0][1] == "0", "terminal index must initialize to zero"


def assert_terminal_clear_contract(blocks, globals_):
    """Check count/index dataflow and receiver-cell identity.

    require_owning checks registration coverage/initialization only. This
    checker does NOT prove that the keeper stays live/pinned at every call or
    that activation cleanup follows terminal clearing; those remain separate
    lifetime/cleanup regressions and qualification obligations.
    """
    roots = RootSlotContract(blocks, globals_)
    by_name = {block.name: block for block in blocks}
    definitions = {}
    for _, _, ins in roots.rows:
        if ins.kind in ("select", "call", "load"):
            definitions[ins.data[0]] = ins
        elif ins.kind in ("icmp", "binop"):
            definitions[ins.data[1]] = ins
    groups = []
    for block, position, call in roots.rows:
        if not roots.called(call, "py_gen_frame_clear_group"):
            continue
        receiver, index, count = roots.args(call)
        reload = block.instructions[position - 1]
        assert roots.called(reload, "pcc_gc_load_ptr") and reload.data[0] == receiver, "batch receiver must freshly reload"
        keeper = roots.slot(roots.args(reload)[1])
        assert roots.args(reload)[0] == "null"
        roots.require_owning(keeper, allow_lifo=True)
        select = definitions[count]
        assert select.kind == "select" and select.data[4] == "16", "bounded count must be min(remaining,16)"
        remaining = select.data[3]
        comparison = definitions[select.data[2]]
        assert comparison.kind == "icmp" and comparison.data[0] == "slt"
        assert comparison.data[3:] == (remaining, "16")
        difference = definitions[remaining]
        assert difference.kind == "binop" and difference.data[0] == "sub" and difference.data[4] == index
        length = difference.data[3]
        assert roots.called(definitions[length], "py_gen_frame_len") or roots.called(definitions[length], "py_list_len")
        test = next(candidate for candidate in blocks
                    if candidate.terminator.kind == "br_cond"
                    and candidate.terminator.data[1] == block.name)
        gate = definitions[test.terminator.data[0]]
        assert gate.kind == "icmp" and gate.data[0] == "sge" and gate.data[3:] == (remaining, "2")
        term = block.terminator
        assert term.kind == "br_cond"
        success = definitions[term.data[0]]
        assert success.kind == "icmp" and success.data[0] == "eq" and success.data[3:] == (call.data[0], "1")
        accepted, scalar_test = by_name[term.data[1]], by_name[term.data[2]]
        assert test.terminator.data[2] == scalar_test.name, "singleton must enter original scalar loop"
        assert accepted.terminator.kind == "br" and accepted.terminator.data[0] == test.name
        assert not any(ins.kind == "call" for ins in accepted.instructions), "accepted cells must not be cleared twice"
        scalar_term = scalar_test.terminator
        assert scalar_term.kind == "br_cond"
        clear = by_name[scalar_term.data[1]]
        setters = [(i, ins) for i, ins in enumerate(clear.instructions)
                   if roots.called(ins, "py_gen_frame_set")]
        assert len(setters) == 1
        at, setter = setters[0]
        current = clear.instructions[at - 1]
        assert roots.called(current, "pcc_gc_load_ptr"), "fallback receiver must reload after a declined/parking call"
        assert current.data[0] == roots.args(setter)[0]
        assert roots.slot(roots.args(current)[1]) == keeper
        assert clear.terminator.kind == "br" and clear.terminator.data[0] == scalar_test.name, "decline must retain original serial remainder"
        _assert_terminal_index_progress(
            roots, definitions, index, count, length,
            test, block, accepted, scalar_test, clear, setter,
        )
        groups.append((block.name, keeper))
    assert groups, "no reachable terminal group shape"
    return groups


def test_frontend_terminal_groups_keep_scalar_remainder_and_current_owner(source_generator_group_ir):
    _, module, blocks = source_generator_group_ir
    assert_terminal_clear_contract(blocks, module.globals_)


_INDEX_MEMORY = '''
target triple = "x86_64-unknown-linux-gnu"
declare void @py_gen_frame_set(ptr, i64, ptr)
define void @index_model(i64 %length, i64 %count, i1 %probe, i1 %admitted) {
entry:
  %index.slot = alloca i64
  store i64 0, ptr %index.slot
  br label %group.test
group.test:
  %index = load i64, ptr %index.slot
  br i1 %probe, label %attempt, label %scalar.test
attempt:
  br i1 %admitted, label %accepted, label %scalar.test
accepted:
  %group.next = add i64 %index, %count
  store i64 %group.next, ptr %index.slot
  br label %group.test
scalar.test:
  %scalar.index = load i64, ptr %index.slot
  %scalar.more = icmp slt i64 %scalar.index, %length
  br i1 %scalar.more, label %clear, label %done
clear:
  call void @py_gen_frame_set(ptr null, i64 %scalar.index, ptr null)
  %scalar.next = add i64 %scalar.index, 1
  store i64 %scalar.next, ptr %index.slot
  br label %scalar.test
done:
  ret void
}
'''
_INDEX_PHI = (_INDEX_MEMORY
    .replace("  %index.slot = alloca i64\n", "")
    .replace("  store i64 0, ptr %index.slot\n", "")
    .replace("  %index = load i64, ptr %index.slot",
             "  %index = phi i64 [ 0, %entry ], [ %group.next, %accepted ]")
    .replace("  store i64 %group.next, ptr %index.slot\n", "")
    .replace("  %scalar.index = load i64, ptr %index.slot",
             "  %scalar.index = phi i64 [ %index, %group.test ], "
             "[ %index, %attempt ], [ %scalar.next, %clear ]")
    .replace("  store i64 %scalar.next, ptr %index.slot\n", ""))


def _check_index_model(text):
    module, blocks = _parsed_function(text, "index_model")
    roots = RootSlotContract(blocks, module.globals_)
    by_name = {block.name: block for block in blocks}
    definitions = {}
    for _, _, ins in roots.rows:
        if ins.kind == "load":
            definitions[ins.data[0]] = ins
        elif ins.kind in ("icmp", "binop"):
            definitions[ins.data[1]] = ins
    setter = next(ins for ins in by_name["clear"].instructions
                  if roots.called(ins, "py_gen_frame_set"))
    _assert_terminal_index_progress(
        roots, definitions, "index", "count", "length",
        by_name["group.test"], by_name["attempt"], by_name["accepted"],
        by_name["scalar.test"], by_name["clear"], setter,
    )


@pytest.mark.parametrize("promoted,before,after,diagnostic", (
    (False, "i64 %scalar.index, ptr null", "i64 0, ptr null", "scalar setter"),
    (False, "store i64 %group.next", "store i64 %index", "accepted increment"),
    (False, "store i64 %scalar.next", "store i64 %scalar.index", "scalar increment"),
    (True, "i64 %scalar.index, ptr null", "i64 0, ptr null", "scalar setter"),
    (True, "[ %group.next, %accepted ]", "[ %index, %accepted ]", "group PHI"),
    (True, "[ %index, %attempt ]", "[ 0, %attempt ]", "scalar PHI"),
    (True, "[ %scalar.next, %clear ]", "[ %scalar.index, %clear ]", "scalar PHI"),
))
def test_index_checker_rejects_disconnected_updates(promoted, before, after, diagnostic):
    text = _INDEX_PHI if promoted else _INDEX_MEMORY
    _check_index_model(text)
    assert text.count(before) == 1
    with pytest.raises(AssertionError, match=diagnostic):
        _check_index_model(text.replace(before, after))


@pytest.mark.integration
def test_terminal_native_reference_and_actual_optimized_linker_input(tmp_path, monkeypatch, pcc_runtime_archive):
    from pcc.frontends.python import pipeline
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from pcc.backend.self_backend_verify import verify_parsed_module

    cases = ("primitive", "return", "error", "close")
    statuses = {case: {"reference": "UNRUN", "native": {
        str(backend): "UNRUN" for backend in BACKEND_ORDER}} for case in cases}
    status_path = tmp_path / "terminal-execution-status.json"
    def save_status():
        status_path.write_text(json.dumps(statuses, indent=2) + "\n")
    save_status()
    monkeypatch.setenv("PCC_INLINE_GENERATOR_FRAMES", "1")
    monkeypatch.setenv("PCC_LOG", "")
    monkeypatch.setenv("PCC_GC_REFCOUNT_PROVENANCE_PROBE", "0")
    original = pipeline._link_with_self_backend_ir_texts
    saved = []
    def observe(texts, *args, **kwargs):
        for i, text in enumerate(texts):
            if "primitive_frame__gen_resume" not in text and "heap_frame__gen_resume" not in text:
                continue
            path = tmp_path / ("actual-terminal-link-" + str(len(saved)) + "-" + str(i) + ".ll")
            data = text.encode()
            path.write_bytes(data)
            saved.append((path, sha256(data).hexdigest()))
        return original(texts, *args, **kwargs)
    monkeypatch.setattr(pipeline, "_link_with_self_backend_ir_texts", observe)
    binary = tmp_path / "terminal-clear"
    pipeline.compile_python(str(SOURCE), str(binary), backend="self", ir_scaffold_mode="on",
                            runtime_archive=str(pcc_runtime_archive))
    checked = set()
    for path, digest in saved:
        assert sha256(path.read_bytes()).hexdigest() == digest
        module = parse_self_backend_module(path.read_text())
        verify_parsed_module(module)
        for function in module.functions:
            if not function.name.endswith(("_primitive_frame__gen_resume", "_heap_frame__gen_resume")):
                continue
            blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
            assert_terminal_clear_contract(blocks, module.globals_)
            checked.add(function.name.rsplit("_frame__", 1)[0].rsplit("_", 1)[-1])
    assert checked == {"primitive", "heap"}
    references = {}
    # References are prerequisites. Native backends run outermost so a known
    # GC1/2 failure cannot prevent another selected GC3/4 case from running.
    for case in cases:
        statuses[case]["reference"] = "RUNNING"
        save_status()
        try:
            reference = run_process_group_timeout(
                [sys.executable, "-E", "-B", str(SOURCE), case], timeout=20,
            )
            (tmp_path / (case + "-reference.stdout")).write_text(reference.stdout)
            (tmp_path / (case + "-reference.stderr")).write_text(reference.stderr)
            assert reference.returncode == 0 and not reference.stderr
            references[case] = reference.stdout
        except BaseException:
            statuses[case]["reference"] = "FAILED"
            save_status()
            raise
        statuses[case]["reference"] = "PASS"
        save_status()
    for backend in BACKEND_ORDER:
        for case in cases:
            statuses[case]["native"][str(backend)] = "RUNNING"
            save_status()
            try:
                env = dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="",
                           PCC_GC_REFCOUNT_PROVENANCE_PROBE="0")
                result = run_process_group_timeout([str(binary), case], env=env, timeout=20)
                (tmp_path / (case + "-gc" + str(backend) + ".stdout")).write_text(result.stdout)
                (tmp_path / (case + "-gc" + str(backend) + ".stderr")).write_text(result.stderr)
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, references[case], ""), (case, backend)
            except BaseException:
                statuses[case]["native"][str(backend)] = "FAILED"
                save_status()
                raise
            statuses[case]["native"][str(backend)] = "PASS"
            save_status()


NATIVE_TRANSITIVE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
static int finalizers, weak_callbacks;
static PyObject *finalize(PyObject *captures, PyObject *args) {
    (void)captures; (void)args; finalizers++; return py_None;
}
static PyObject *weak_gone(PyObject *captures, PyObject *args) {
    (void)captures; (void)args; weak_callbacks++; return py_None;
}
int main(void) {
    pcc_gc_backend();
    py_gc_disable();
    PyObject *roots[9] = {0};
    int32_t map[1] = {9};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(16);
    roots[1] = (PyObject *)py_class_new("ClearTransitive", 0, 0, 0, 0);
    roots[2] = py_tuple_new(0);
    roots[3] = py_func_new((void *)(uintptr_t)finalize, roots[2]);
    roots[4] = py_func_new((void *)(uintptr_t)weak_gone, roots[2]);
    if (!roots[0] || !roots[1] || !roots[2] || !roots[3] || !roots[4]) return 1;
    py_class_add_method((PyClassObject *)roots[1], "__del__", roots[3]);
    roots[5] = py_instance_new((PyClassObject *)roots[1]);
    roots[6] = py_list_new(0);
    roots[7] = py_weakref_new(roots[5], roots[4]);
    if (!roots[5] || !roots[6] || !roots[7]) return 2;
    py_list_append(roots[6], roots[5]);
    PyObject *prefix[15] = {0};
    for (int i = 0; i < 15; i++) {
        prefix[i] = i % 2 == 0 ? NULL : py_int_from_i64(i + 41);
        if (i % 2 != 0 && !py_mem_ptr_is_tagged_int(prefix[i])) return 8;
        py_gen_frame_set(roots[0], i, prefix[i]);
    }
    py_gen_frame_set(roots[0], 15, roots[6]);
    /* The old cell is a plain list, not the callback-bearing descendant. */
    pcc_gc_store_root(&roots[5], NULL);
    pcc_gc_store_root(&roots[6], NULL);
    if (py_gen_frame_clear_group(roots[0], 0, 16) != 0) return 3;
    for (int i = 0; i < 15; i++)
        if (((PyListObject *)roots[0])->items[i] != prefix[i]) return 4;
    if (((PyListObject *)roots[0])->items[15] == py_None) return 5;
    roots[8] = py_weakref_call(roots[7]);
    if (!roots[8] || roots[8] == py_None || finalizers || weak_callbacks) return 6;
    pcc_gc_store_root(&roots[8], NULL);
    /* Preserve the original scalar fallback. Collection/finalization timing
       after fallback belongs to the Python native/reference terminal fixture. */
    for (int i = 0; i < 16; i++)
        py_gen_frame_set(pcc_gc_load_ptr(0, &roots[0]), i, py_None);
    for (int i = 0; i < 16; i++)
        if (((PyListObject *)roots[0])->items[i] != py_None) return 7;
    for (int i = 8; i >= 0; i--) pcc_gc_store_root(&roots[i], NULL);
    pcc_gc_frame_leave(roots);
    puts("FRAME_CLEAR_TRANSITIVE_REJECT_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_transitive_finalizer_and_weakref_range_is_rejected(tmp_path, pcc_runtime_archive):
    statuses = {str(backend): "UNRUN" for backend in BACKEND_ORDER}
    status_path = tmp_path / "transitive-execution-status.json"
    status_path.write_text(json.dumps(statuses, indent=2) + "\n")
    executable = _native(tmp_path, pcc_runtime_archive, "frame_clear_transitive", NATIVE_TRANSITIVE)
    for backend in BACKEND_ORDER:
        statuses[str(backend)] = "RUNNING"
        status_path.write_text(json.dumps(statuses, indent=2) + "\n")
        try:
            result = _run(executable, backend)
            (tmp_path / ("transitive-gc" + str(backend) + ".stdout")).write_text(result.stdout)
            (tmp_path / ("transitive-gc" + str(backend) + ".stderr")).write_text(result.stderr)
            assert (result.returncode, result.stdout, result.stderr) == (
                0, "FRAME_CLEAR_TRANSITIVE_REJECT_OK\n", ""), backend
        except BaseException:
            statuses[str(backend)] = "FAILED"
            status_path.write_text(json.dumps(statuses, indent=2) + "\n")
            raise
        statuses[str(backend)] = "PASS"
        status_path.write_text(json.dumps(statuses, indent=2) + "\n")
