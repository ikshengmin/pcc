"""An already-cleared list must free its storage without re-releasing a child."""
from pathlib import Path

import pytest

from pcc.runtime.py.py_abi_constants import (
    PYLISTOBJECT_CAPACITY_OFFSET,
    PYLISTOBJECT_ITEMS_OFFSET,
    PYLISTOBJECT_LENGTH_OFFSET,
)
from tests.python.runtime_body_model import load_functions


@pytest.mark.parametrize("capacity", (4, 17, 1024))
def test_cleared_list_frees_items_before_returning_header(capacity):
    obj, items = 4096, 8192
    fields = {PYLISTOBJECT_LENGTH_OFFSET: 0,
              PYLISTOBJECT_CAPACITY_OFFSET: capacity,
              PYLISTOBJECT_ITEMS_OFFSET: items}
    freed = []

    def read(owner, offset):
        assert owner == obj
        return fields[offset]

    def write(owner, offset, value):
        assert owner == obj
        fields[offset] = value

    def no_children(*args):
        pytest.fail("already-cleared list must not release a child")

    def release_storage(value):
        assert value == items and not any(fields.values())
        freed.append(value)

    ns = dict(
        load_i64=read, load_ptr=read, store_i64=write, store_ptr=write,
        ptr_is_null=lambda ptr: int(ptr == 0), null=lambda: 0, free=release_storage,
        pcc_gc_free_object_memory=freed.append, py_decref=no_children,
        pcc_gc_release_nonterminal_tail=no_children, pcc_gc_load_ptr=no_children,
        PYLISTOBJECT_LENGTH_OFFSET=PYLISTOBJECT_LENGTH_OFFSET,
        PYLISTOBJECT_CAPACITY_OFFSET=PYLISTOBJECT_CAPACITY_OFFSET,
        PYLISTOBJECT_ITEMS_OFFSET=PYLISTOBJECT_ITEMS_OFFSET,
    )
    module = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj_dealloc.py"
    load_functions(module, {"py_dealloc_list"}, ns)
    ns["py_dealloc_list"](obj)
    assert freed == [items, obj]
