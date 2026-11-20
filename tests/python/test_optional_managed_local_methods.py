"""Lookup on an optional local must use the actual managed receiver, not NoneType."""

import pytest

from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_verify import verify_parsed_module
from pcc.frontends.python.codegen.builtin_exceptions import BUILTIN_EXC_TAG
from pcc.frontends.python.pipeline import compile_python


_SOURCE = """\
def key(trace: list[str]) -> str:
    trace.append("argument")
    return "hit"


def item(trace: list[str]) -> int:
    trace.append("argument")
    return 9


def local_get(stop: bool, trace: list[str]):
    receiver: dict[str, int] | None = {"hit": 7}
    if stop:
        receiver = None
    return receiver.get(key(trace))


def local_append(stop: bool, trace: list[str]):
    receiver: list[int] | None = [2]
    if stop:
        receiver = None
    receiver.append(item(trace))
    return receiver


def dict_slot_control(stop: bool):
    receiver: dict[str, int] | None = {"hit": 7}
    if stop:
        receiver = None
    return receiver


def list_slot_control(stop: bool):
    receiver: list[int] | None = [2]
    if stop:
        receiver = None
    return receiver
"""


@pytest.mark.parametrize("method,result", [("get", 7), ("append", [2, 9])])
def test_optional_local_method_matches_cpython_lookup_order(method, result):
    scope = {}
    exec(_SOURCE, scope)
    invoke = scope["local_" + method]
    trace = []
    assert invoke(False, trace) == result
    assert trace == ["argument"]
    trace = []
    with pytest.raises(AttributeError) as caught:
        invoke(True, trace)
    assert caught.value.obj is None
    assert caught.value.name == method
    assert trace == []


def _reachable(core, start, exclude=-1):
    seen = set()
    pending = [start]
    while pending:
        block = pending.pop()
        if block == exclude or block in seen:
            continue
        seen.add(block)
        pending.extend(
            core.cfg_successor_id(block, index)
            for index in range(core.cfg_successor_count(block))
        )
    return seen


def _assert_lookup_before_argument(module, method, argument):
    function = next(fn for fn in module.functions if fn.name == "user_optional_local_local_" + method)
    core = get_indexed_function_kernel(function)
    assert core.block_names[0] != "strict.nolib.stub", method
    blocks = [core.diagnostic_block(i) for i in range(len(core.block_names))]
    calls = [
        (block_id, index, instruction.data)
        for block_id, block in enumerate(blocks)
        for index, instruction in enumerate(block.instructions)
        if instruction.kind == "call" and not instruction.data[3]
    ]

    def call_site(symbol):
        site, = [(block, index) for block, index, call in calls if call[2] == symbol]
        return site

    lookup_block, lookup_index = call_site("py_obj_load_method")
    argument_block, argument_index = call_site("user_optional_local_" + argument)
    invoke_block, invoke_index = call_site("py_obj_call_loaded_method")
    error_guard, = [
        (block_id, index, instruction.data)
        for block_id, block in enumerate(blocks)
        for index, instruction in enumerate(block.instructions)
        if instruction.kind == "icmp"
        and instruction.data[1].startswith("attr." + method + ".missing.")
    ]
    guard_block, guard_index, comparison = error_guard
    assert comparison[0] == "eq" and comparison[-1] == "null"
    term = blocks[guard_block].terminator
    assert term.kind == "br_cond"
    cond, miss, found = term.data
    assert cond == comparison[1]
    missed = _reachable(core, core.block_names.index(miss))
    ready = _reachable(core, core.block_names.index(found))
    assert argument_block in ready and invoke_block in ready
    assert argument_block not in missed and invoke_block not in missed
    attribute_error = str(BUILTIN_EXC_TAG["AttributeError"])
    assert any(
        block in missed and call[2] == "py_exc_new" and call[4][0][1] == attribute_error
        for block, _index, call in calls
    )

    def precedes(first_block, first_index, second_block, second_index):
        if first_block == second_block:
            return first_index < second_index
        return second_block not in _reachable(core, 0, exclude=first_block)

    assert guard_block in _reachable(core, 0)
    assert precedes(lookup_block, lookup_index, guard_block, guard_index)
    assert precedes(guard_block, guard_index, argument_block, argument_index)
    assert precedes(argument_block, argument_index, invoke_block, invoke_index)
    assert all(not call[2].startswith("py_cpy_") for _, _, call in calls)
    assert any(call[2] == "pcc_gc_frame_enter_local"
               and call[4][1][1].startswith("receiver.addr.") for _, _, call in calls)


def test_optional_managed_locals_emit_native_lookup_before_arguments(tmp_path):
    source = tmp_path / "optional_local.py"
    source.write_text(_SOURCE, encoding="utf-8")
    out = tmp_path / "optional_local.ll"
    compile_python(
        str(source), str(out), emit_llvm_only=True, python_library=True,
        backend="self", ir_scaffold_mode="on", target_triple="x86_64-unknown-linux-gnu",
    )
    module = parse_self_backend_module(out.read_text(encoding="utf-8"))
    verify_parsed_module(module)
    _assert_lookup_before_argument(module, "get", "key")
    _assert_lookup_before_argument(module, "append", "item")
