"""The two live ranges around a user iterator retire their entry decisions."""
import ast
import os
from pathlib import Path

import pytest
from tests.python.owned_regression_support import (
    _record_execution,
    assert_reference_program,
    explicit_owned_runtime,
)


def test_extend_retires_the_two_admissions_issued_before_iteration():
    # This is a source/data-flow obligation, not a claim that the existing
    # fixed-scan runtime loses a live root on a public input.
    path = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_list.py"
    scope = next(node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "_list_extend_iterable")
    tickets = []
    for stmt in scope.body:
        if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "_list_extend_bind_range"):
            tickets.append((stmt.value.args[1].id, stmt.targets[0].id))
    assert [name for name, _ in tickets] == ["borrowed", "slots"], (
        "borrowed/owned range admission discarded", tickets,
    )
    retirements = [stmt.value for stmt in scope.body if isinstance(stmt, ast.Expr)
                   and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name)
                   and stmt.value.func.id == "pcc_gc_native_root_unbind"]
    assert [call.args[0].id for call in retirements] == [ticket for _, ticket in reversed(tickets)]
    assert not any(isinstance(n, ast.Name) and n.id == "pcc_gc_native_frame_unbind_map"
                   for n in ast.walk(scope))


def test_extend_bind_preserves_the_existing_map_admissions():
    # Execute both real admission functions with ordinary Python objects as
    # opaque identities. No machine addresses, allocator or stack simulator.
    root = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
    signed_map, slots, ticket = object(), object(), object()
    values, writes = {}, []

    def bind_range(received, size):
        assert received is slots
        writes.append(size)
        return ticket

    ns = dict(i64=int, c_ptr=object, global_addr=lambda name: name, null=lambda: None,
              ptr_is_null=lambda value: value is None,
              load_i32=lambda address, offset: values[address],
              pcc_gc_native_root_bind_range=bind_range)
    for filename, name in (
        ("freestanding_gc_frame_registry.py", "pcc_gc_frame_roots_disabled_fast"),
        ("freestanding_thread_kernel_pthread.py", "pcc_gc_native_frame_bind_map"),
        ("py_list.py", "_list_extend_bind_range"),
    ):
        fn = next(node for node in ast.parse((root / filename).read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
        fn.decorator_list = []
        exec(compile(ast.Module(body=[fn], type_ignores=[]), filename, "exec"), ns)
    for backend in (0,):
        for initialized in (0, 1):
            for tracking in (0, 1):
                for nonmoving in (0, 1):
                    for count in (-2, 0, 5):
                        values.update(pcc_gc_backend_selected=backend,
                                      pcc_gc_config_initialized=initialized,
                                      pcc_gc_backend0_frame_roots_enabled=tracking,
                                      pcc_gc_nonmoving_count_free=nonmoving)
                        values[signed_map] = count
                        writes.clear()
                        ns["pcc_gc_native_frame_bind_map"](signed_map, slots)
                        expected = list(writes)
                        writes.clear()
                        result = ns["_list_extend_bind_range"](signed_map, slots)
                        assert writes == expected
                        assert result is (ticket if expected else None)


PROGRAM = '''import gc
import weakref

class Item:
    pass

class Finished(Exception):
    pass

events = []
observed = []

def observe(phase, info):
    if observed[0]() is not None:
        events.append(phase)

class Source:
    def __init__(self, item, error):
        self.item = item
        self.error = error
        self.calls = 0
    def __iter__(self):
        return self
    def __next__(self):
        self.calls += 1
        gc.collect()
        if self.calls == 1:
            return self.item
        if self.error is not None:
            raise self.error
        raise StopIteration

def main():
    gc.disable()
    item = Item()
    observed.append(weakref.ref(item))
    result = []
    gc.callbacks.append(observe)
    try:
        result.extend(Source(item, None))
        marker = Finished("iterator")
        try:
            result.extend(Source(item, marker))
        except Finished as caught:
            assert caught is marker
        else:
            raise AssertionError("lost iterator exception")
    finally:
        gc.callbacks.remove(observe)
    assert events == ["start", "stop"] * 4, events
    assert len(result) == 2 and result[0] is item and result[1] is item
    print("ITERABLE_ROOT_ADMISSION_OK")

main()
'''
EXPECTED = "ITERABLE_ROOT_ADMISSION_OK\n"


def test_list_extend_public_collection_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_list_extend_public_collection_native(tmp_path, explicit_owned_runtime):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "extend_callbacks.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "extend_callbacks"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(explicit_owned_runtime))
    for backend in (0,):
        result = _record_execution(
            tmp_path, f"gc{backend}", [str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, EXPECTED, "",
        ), (backend, result)
