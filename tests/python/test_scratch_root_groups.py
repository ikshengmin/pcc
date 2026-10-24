"""Execute scratch-frame owners across movement, OOM and exception cleanup."""

import ast
from pathlib import Path

import pytest

from tests.python.test_dict_super_slot_roots import DictMemory
from tests.python.test_set_call_slot_roots import Block, Object


def frame_functions(memory, kind):
    if kind == "special":
        path = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_class.py"
        names = {"_special_open", "_special_close", "_special_copy", "_special_drop"}
        body = [node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), memory.ns)
        return (lambda slots, tokens, state, width: memory.ns["_special_open"](slots, tokens, state),
                lambda slots, tokens, state, count, suspended, width:
                    memory.ns["_special_close"](slots, tokens, state, count, suspended),
                lambda slots, tokens, source: memory.ns["_special_copy"](slots, tokens, 1, source, 0))
    return (memory.ns["_dict_slot_open"], memory.ns["_dict_slot_close"],
            lambda slots, tokens, source: memory.ns["_dict_slot_copy"](slots, tokens, 1, source))


@pytest.mark.parametrize("kind,width", (("special", 14), ("dict", 7), ("dict", 16)))
@pytest.mark.parametrize("phase", ("register", "copy", "acquire", "release", "drop", "unregister"))
@pytest.mark.parametrize("failed_operation", (False, True))
def test_group_preserves_owners_and_pending_exception(kind, width, phase, failed_operation):
    memory = DictMemory(phase)
    opened, closed, copied = frame_functions(memory, kind)
    source = memory.input_roots((memory.wrap("owned input"),))
    initial_roots = set(memory.roots)
    slots, tokens, state = Block(), Block(), Block()
    entry_error = (2, "entry")
    memory.error = entry_error
    count = opened(slots, tokens, state, width)
    assert count == width
    assert memory.registrations == 1
    assert len(memory.roots) == len(initial_roots) + width
    assert len(memory.root_groups) == 1
    memory.swap_error(slots)
    assert copied(slots, tokens, source) == 0
    memory.error = (3, "operation") if failed_operation else None
    closed(slots, tokens, state, count, 1, width)
    assert memory.error == ((3, "operation") if failed_operation else entry_error)
    assert set(memory.roots) == initial_roots
    assert not memory.root_groups
    value = memory.read(source, 0)
    assert isinstance(value, Object) and value.alive and value.refs == 1 and value.leases == 0
    assert memory.unwrap(value) == "owned input"
    assert all(memory.read(slots, index * 8) is None for index in range(width))


@pytest.mark.parametrize("kind,width", (("special", 14), ("dict", 7), ("dict", 16)))
def test_failed_group_registration_keeps_inputs_and_tls(kind, width):
    memory = DictMemory("register", fail_register=1)
    opened, closed, _ = frame_functions(memory, kind)
    source = memory.input_roots((memory.wrap("still owned"),))
    initial_roots = set(memory.roots)
    slots, tokens, state = Block(), Block(), Block()
    memory.error = (2, "entry")
    count = opened(slots, tokens, state, width)
    assert count == 0 and not memory.root_groups
    closed(slots, tokens, state, count, 0, width)
    assert set(memory.roots) == initial_roots and memory.error == (2, "entry")
    value = memory.read(source, 0)
    assert value.alive and value.refs == 1 and value.leases == 0
    assert memory.unwrap(value) == "still owned"
