"""The known-object check the store barriers run before reading a header.

A source check that its lookup closure only reads. It does not establish
native execution, concurrent collector progress or throughput.
"""
from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"


def test_known_lookup_closure_is_read_only():
    sources = {
        RUNTIME / "freestanding_gc_root_operations.py": (
            "pcc_gc_object_is_known_no_lock",
        ),
        RUNTIME / "freestanding_gc_index_table.py": (
            "pcc_gc_object_index_find", "pcc_gc_index_py_find_published",
            "pcc_gc_object_index_find_unpublished",
            "pcc_gc_index_py_find_slot", "pcc_gc_index_py_hash_ptr",
            "_object_index_find_once", "_object_index_record_find",
        ),
        # An object cell's node is its slab record: finding it walks the
        # allocator's span radix without filling the span cache.
        RUNTIME / "freestanding_allocator.py": (
            "pcc_allocator_object_node_record_find", "_object_cell_number",
            "_granule_radix_span_probe", "_granule_radix_walk",
        ),
    }
    allowed = {"ptr_is_null", "is_tagged_int", "null", "load_i32", "load_i64",
               "load_ptr", "global_addr", "atomic_load_i64", "int_to_ptr",
               "ptr_eq", "ptr_diff", "logical_shift_right_i64", "wrapping_mul_i64",
               "atomic_fence", "pcc_platform_abort"}
    # The OOM fallback can fence reads or fail closed on corrupt generation;
    # it still cannot allocate, park, call back, lock, or mutate graph state.
    allowed.update(name for names in sources.values() for name in names)
    # A miss asks the allocation staging rings, which only count-free
    # GC1/GC2 enable: for GC4 (and GC0/GC3) the lookup reads the one enable
    # word and keeps its miss without a call, and the hook itself returns
    # on the same word before anything else.
    allowed.add("pcc_gc_alloc_stage_miss_drain")
    index_find = next(
        node for node in ast.parse(
            (RUNTIME / "freestanding_gc_index_table.py").read_text()
        ).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "pcc_gc_object_index_find"
    )
    gate, hook = (
        position for position, statement in enumerate(index_find.body)
        if "pcc_gc_alloc_stage" in ast.unparse(statement)
    )
    assert ast.unparse(index_find.body[gate]) == (
        "if load_i32(global_addr('pcc_gc_alloc_stage_enabled'), 0) == 0:\n"
        "    return node"
    )
    assert "pcc_gc_alloc_stage_miss_drain()" in ast.unparse(index_find.body[hook])
    stage = ast.parse(
        (RUNTIME / "freestanding_gc_alloc_stage.py").read_text()
    )
    miss_drain = next(
        node for node in stage.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "pcc_gc_alloc_stage_miss_drain"
    )
    first = next(
        statement for statement in miss_drain.body
        if not isinstance(statement, ast.Expr)
    )
    assert ast.unparse(first) == (
        "if load_i32(global_addr('pcc_gc_alloc_stage_enabled'), 0) == 0:\n"
        "    return 0"
    )
    for path, names in sources.items():
        functions = [node for node in ast.parse(path.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in functions} == set(names)
        for node in functions:
            node.decorator_list = []
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    assert isinstance(call.func, ast.Name) and call.func.id in allowed
