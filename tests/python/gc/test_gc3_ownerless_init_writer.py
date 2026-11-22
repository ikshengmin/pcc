"""Run existing source branches against a hypothetical GC3 nursery model."""

import ast
from pathlib import Path

from pcc.runtime.py import py_abi_constants as abi
from tests.python.gc.gc3_sparse_remembered_model import Model

RUNTIME = Path(__file__).resolve().parents[3] / "pcc/runtime/py"


def _source_function(file, name, bindings):
    path = RUNTIME / file
    body = ast.parse(path.read_text(encoding="utf-8")).body
    function, = (node for node in body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    exec(compile(ast.Module([function], type_ignores=[]), str(path), "exec"), bindings)
    return bindings[name]


def test_gc3_dict_admission_and_fast_init_discard_remembered_owner():
    model = Model()
    model.add_old(1000, "dict-entry-card")
    model.add_young(2000)
    backend_3_no_major = {
        "pcc_gc_backend_selected": 3,
        "pcc_gc_nonmoving_count_free": 1,
        "pcc_gc_foreign_lease_active": 0,
        "pcc_diagnostics_runtime_log_fast_state": 0,
    }
    slot, supplied = 3200, 3300
    memory = {slot: 0, supplied: 2000}
    shades = []

    def store(pointer, offset, value):
        memory[pointer + offset] = value
        assert pointer == slot and offset == 0
        # Mirror the raw store, not a barrier for the old dictionary's card.
        model.cards["dict-entry-card"][1000] = (value,)

    bindings = {
        "c_ptr": int,
        "i64": int,
        "global_addr": lambda name: name,
        "load_i32": lambda ptr, offset=0: (
            backend_3_no_major[ptr] if isinstance(ptr, str) else abi.PY_TYPE_LIST
        ),
        "atomic_load_i64": lambda name, offset, order: backend_3_no_major[name],
        "ptr_is_null": lambda ptr: int(ptr == 0),
        "ptr_eq": lambda a, b: int(a == b),
        "is_tagged_int": lambda _: 0,
        "load_ptr": lambda ptr, offset: memory[ptr + offset],
        "store_ptr": store,
        "pcc_gc_count_free_store_shade": shades.append,
    }
    for name in ("PYOBJECTHEADER_TYPE_TAG_OFFSET", "PY_TYPE_NONE", "PY_TYPE_CONTINUATION",
                 "PY_TYPE_CPY_HANDLE", "PY_TYPE_USER", "PY_TYPE_CEXT_TAG_BASE"):
        bindings[name] = getattr(abi, name)

    admit = _source_function("py_dict.py", "_dict_gc0_admitted_locked", bindings)
    initialize = _source_function("freestanding_gc_root_operations.py", "pcc_gc_try_init_ptr_locked", bindings)
    assert admit() == 1
    assert initialize(slot, supplied) == 1
    assert memory[slot] == 2000 and shades == [2000]

    # The major shade receives only the value. It cannot remember the card.
    assert model.full_reachable_young((1000,)) == {2000}
    assert model.minor((1000,)).live_young == set()


def test_gc3_dict_pair_publication_has_one_owner_boundary_and_no_rollback_edge():
    from tests.python.test_dict_gc0_locked_get_set import Heap, SET_NAMES, _entry, _functions

    heap = Heap()
    heap.flags.update({
        "pcc_gc_backend_selected": 3,
        "pcc_gc_nonmoving_count_free": 1,
        "pcc_gc_refcount_atomic": 0,
    })
    key, rejected, value = heap.obj(), heap.obj(), heap.obj()
    owner = heap.dict([])
    heap.rejected.add(rejected)
    indices = heap.load(owner, abi.PYDICTOBJECT_INDICES_OFFSET)
    target_index = 5 * 8  # Heap.probe uses bucket 5 for an absent key.
    heap.store(indices, target_index, -1)
    model = Model()
    model.add_old(owner, "dictionary")
    for young in (key, value, rejected):
        model.add_young(young)
    publications = []
    dictionary = _functions(heap, SET_NAMES)

    def observe_commit(address, offset, incoming):
        heap.store(address, offset, incoming)
        if (address, offset) != (owner, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET):
            return
        assert heap.locked()
        assert incoming == 1
        assert heap.load(owner, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET) == 1
        assert heap.load(indices, target_index) == 0
        edges = _entry(heap, owner, 0)
        assert edges == (key, value)
        publications.append((owner, edges))
        # Hypothetical remembered write; the current source has no such hook.
        model.store_old("dictionary", owner, edges)

    dictionary["store_i64"] = observe_commit
    set_fast = dictionary["_dict_set_gc0_locked"]
    assert set_fast(owner, key, rejected, 2) == 0
    assert _entry(heap, owner, 0) == (0, 0)
    assert heap.load(owner, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET) == 0
    assert heap.load(indices, target_index) == -1
    assert publications == [] and model.dirty == {}

    assert set_fast(owner, key, value, 2) == 1
    assert publications == [(owner, (key, value))]
    assert model.full_reachable_young((owner,)) == {key, value}
    assert model.minor((owner,)).live_young == {key, value}
    assert model.minor(()).live_young == {key, value}
