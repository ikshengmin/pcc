"""Source-mode witness for the existing stopped-world owner's refresh.

The register and stack observations below are supplied machine-state fixtures,
not claims about native x86-64 or Darwin register capture.
"""

import ast
from pathlib import Path

import pytest

KERNEL = (Path(__file__).resolve().parents[3]
          / "pcc/runtime/py/freestanding_thread_kernel_pthread.py")
RECORD, BUFFER = 0x2000, 0x3000
CURRENT_SP, ENTRY_SP, STACK_END = 0x1000, 0x1020, 0x1040
STALE_STACK, STALE_REGISTER = 0x8000, 0x8100
CALLER_STACK, CALLER_REGISTER = 0x9000, 0x9100


def _owner_refresh(*, stop_epoch, current_register, collector_temporary):
    tree = ast.parse(KERNEL.read_text(encoding="utf-8"))
    names = {"_THREAD_ROOT_REGISTERS", "_THREAD_ROOT_CAPACITY",
             "_THREAD_ROOT_DATA", "_THREAD_ROOT_BYTES"}
    functions = {"_thread_snapshot_roots", "pcc_thread_snapshot_stopped_world_owner"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            node.decorator_list = []
            body.append(node)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            body.append(ast.Assign([node.target], node.value))
    assert len(body) == len(names) + len(functions)

    words = {CURRENT_SP + 8: collector_temporary, ENTRY_SP + 8: CALLER_STACK}
    state = {}
    events = []

    def capture(registers):
        events.append("capture")
        state[registers] = current_register
        return CURRENT_SP

    def copy(dst, src, size):
        events.append("copy")
        for offset in range(0, size, 8):
            state[dst + offset] = words.get(src + offset, 0)

    bindings = {
        "capture_gc_registers": capture,
        "ptr_add": lambda base, offset: base + offset,
        "ptr_to_int": int,
        "ptr_is_null": lambda value: int(not value),
        "pcc_thread_world_stop_epoch_held": lambda: stop_epoch,
        "global_addr": lambda name: name,
        "global_load_ptr": lambda name: RECORD if name == "pcc_tls_thread_record_py" else 1,
        "atomic_load_i32": lambda name, offset, order: 1,
        "pthread_mutex_lock": lambda _: events.append("lock"),
        "pthread_mutex_unlock": lambda _: events.append("unlock"),
        "load_i64": lambda ptr, offset: state[ptr + offset],
        "load_ptr": lambda ptr, offset: state[ptr + offset],
        "store_i64": lambda ptr, offset, val: state.__setitem__(ptr + offset, val),
        "memcpy": copy,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body, type_ignores=[])),
                 str(KERNEL), "exec"), bindings)
    state.update({
        RECORD + 32: STACK_END - 16,
        RECORD + bindings["_THREAD_ROOT_CAPACITY"]: STACK_END - CURRENT_SP,
        RECORD + bindings["_THREAD_ROOT_DATA"]: BUFFER,
        RECORD + bindings["_THREAD_ROOT_BYTES"]: 0,
    })
    result = bindings["pcc_thread_snapshot_stopped_world_owner"]()
    size = state[RECORD + bindings["_THREAD_ROOT_BYTES"]]
    frozen = {state[BUFFER + offset] for offset in range(0, size, 8)} - {0}
    regs = state.get(RECORD + bindings["_THREAD_ROOT_REGISTERS"])
    if regs:
        frozen.add(regs)
    return result, frozen, events


@pytest.mark.parametrize("collector_word", [STALE_STACK, STALE_STACK + 128])
def test_current_refresh_copies_collector_only_words_but_entry_oracle_does_not(collector_word):
    result, observed, events = _owner_refresh(
        stop_epoch=7, current_register=STALE_REGISTER, collector_temporary=collector_word,
    )
    assert result == 1
    assert events == ["lock", "capture", "copy", "unlock"]
    assert observed == {collector_word, STALE_REGISTER, CALLER_STACK}
    caller_entry_oracle = {CALLER_STACK, CALLER_REGISTER}
    assert observed - caller_entry_oracle == {collector_word, STALE_REGISTER}
    assert caller_entry_oracle - observed == {CALLER_REGISTER}


def test_current_refresh_does_not_capture_when_caller_does_not_own_stop():
    assert _owner_refresh(stop_epoch=-1, current_register=STALE_REGISTER,
                          collector_temporary=STALE_STACK) == (-1, set(), [])
