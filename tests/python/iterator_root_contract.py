"""Test-only exact physical iterator ownership and runtime ABI checks."""

import ast
from dataclasses import replace
from pathlib import Path

from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from tests.python.root_slot_contract import RootSlotContract


def parsed_root_contract(module, function):
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    return RootSlotContract(blocks, module.globals_)


def _reachable(roots, initial, *, omit=()):
    by_name = {block.name: block for block in roots.blocks}
    seen, pending = set(), [initial]
    while pending:
        name = pending.pop()
        if name in seen or name in omit:
            continue
        seen.add(name)
        term = by_name[name].terminator
        if term.kind == "br":
            pending.append(term.data[0])
        elif term.kind == "br_cond":
            pending.extend(term.data[1:])
        elif term.kind == "switch":
            pending.append(term.data[2])
            pending.extend(target for _, target in term.data[3])
    return seen


def _same(roots, value, identity):
    return roots.slot(value) == identity


def _dominated(roots, before, after):
    a, ai, _ = before
    b, bi, _ = after
    entry = roots.blocks[0].name
    reachable = _reachable(roots, entry)
    if a.name not in reachable or b.name not in reachable:
        return False
    if a.name == b.name:
        return ai < bi
    return b.name not in _reachable(roots, entry, omit=(a.name,))


def _owner_frame_ledger(roots, identity):
    # Module initializers also register process-lifetime global frames. Keep
    # the complete CFG and every non-frame instruction, but check the exact
    # enter/leave ABI of this one activation-owned physical iterator slot.
    def keep(ins):
        abi = roots.frame_abi(ins)
        if abi is None:
            return True
        name = abi[0]
        argument = roots.args(ins)[1 if "frame_enter" in name else 0]
        return _same(roots, argument, identity)

    blocks = [replace(block, instructions=tuple(ins for ins in block.instructions if keep(ins)))
              for block in roots.blocks]
    ledger = RootSlotContract(blocks, roots.globals.values())
    return ledger.assert_frame_exits(exact_abi=True)


def _retirement_paths(roots, identity, *, allow_guarded=False):
    calls = []
    for block, index, ins in roots.rows:
        if not roots.called(ins, "pcc_gc_store_root") or roots.args(ins)[1] != "null":
            continue
        destination = roots.args(ins)[0]
        if _same(roots, destination, identity):
            calls.append((block, index))
            continue
        if allow_guarded:
            # Error cleanup uses a flag-selected root or an initialized NULL
            # sentinel. Such a call must still address this exact owner.
            select = roots.definitions.get(destination)
            if select is None or select.kind != "select":
                continue
            if not _same(roots, select.data[3], identity):
                continue
            empty = roots.slot(select.data[4])
            assert empty != identity
            assert any(prev.kind == "store" and prev.data[0].is_ptr and prev.data[1] == "null"
                       and roots.slot(prev.data[3]) == empty and prev_block.name == "entry"
                       for prev_block, _, prev in roots.rows)
            calls.append((block, index))
    assert len(calls) >= 2, "normal and error retirement must clear the owned cell"
    returns = _owner_frame_ledger(roots, identity)
    rooted_returns = {name for name, entered in returns.items() if entered == {True}}
    assert rooted_returns
    paths = [rooted_returns & _reachable(roots, block.name) for block, _ in calls]
    assert all(paths)
    assert len({frozenset(path) for path in paths}) >= 2, (
        "normal and error owner clears must reach distinct rooted exits"
    )
    # Check each clear is reached while its exact frame is still active,
    # not merely textually before a leave in some other CFG block.
    clear_sites = {(block.name, index) for block, index in calls}
    by_name = {block.name: block for block in roots.blocks}
    pending, visited, visited_clears = [(roots.blocks[0].name, False)], set(), set()
    while pending:
        name, active = pending.pop()
        state = (name, active)
        if state in visited:
            continue
        visited.add(state)
        block = by_name[name]
        for index, ins in enumerate(block.instructions):
            if (name, index) in clear_sites:
                assert active, "owner clear occurs after frame retirement or before entry"
                visited_clears.add((name, index))
            abi = roots.frame_abi(ins)
            if abi is None:
                continue
            enter = "frame_enter" in abi[0]
            if _same(roots, roots.args(ins)[1 if enter else 0], identity):
                active = enter
        term = block.terminator
        if term.kind == "br":
            pending.append((term.data[0], active))
        elif term.kind == "br_cond":
            pending.extend((target, active) for target in term.data[1:])
        elif term.kind == "switch":
            pending.append((term.data[2], active))
            pending.extend((target, active) for _, target in term.data[3])
    assert visited_clears == clear_sites


def _function_source(file, name):
    root = Path(__file__).resolve().parents[2]
    source = (root / "pcc/runtime/py" / file).read_text()
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    return function, source


def _private_next_reads_owner_slot():
    function, _ = _function_source("py_iter.py", "py_obj_private_for_next")
    assert [arg.arg for arg in function.args.args] == ["owner_slot", "cursor"]
    reads = sorted(node.lineno for node in ast.walk(function)
                   if isinstance(node, ast.Call)
                   and ast.unparse(node) == "pcc_gc_load_ptr(null(), owner_slot)")
    assert len(reads) >= 3
    # First read is unconditional. Fallback after materialization and the
    # exact-list item read after the length helper both use the live cell.
    assert ast.unparse(function.body[2]) == "current = pcc_gc_load_ptr(null(), owner_slot)"
    texts = [ast.unparse(node) for node in function.body]
    assert any("return py_obj_iter_next(pcc_gc_load_ptr(null(), owner_slot))" in text
               for text in texts)
    length = next(i for i, text in enumerate(texts) if "length: int = py_list_len(current)" == text)
    assert texts[length + 2:length + 4] == [
        "current = pcc_gc_load_ptr(null(), owner_slot)",
        "item = py_list_get(current, index)",
    ]


def _root_take_releases_previous_owner():
    function, _ = _function_source("py_obj.py", "pcc_gc_store_root_take")
    texts = [ast.unparse(node) for node in function.body]
    old = texts.index("old = load_ptr(slot, 0)")
    store = texts.index("store_ptr(slot, 0, value)")
    release = next(i for i, text in enumerate(texts) if "py_decref(old)" in text)
    assert old < store < release


def assert_iterator_owner(roots, owner, *, private):
    identity = roots.slot(owner)
    roots.require_owning(identity)
    entries = [row for row in roots.rows
               if roots.called(row[2], "pcc_gc_frame_enter")
               and _same(roots, roots.args(row[2])[1], identity)]
    assert len(entries) == 1
    abi = roots.frame_abi(entries[0][2])
    assert abi is not None and abi[1], "iterator slot must use activation-local frame ABI"
    publications = [row for row in roots.rows
                    if roots.called(row[2], "pcc_gc_store_root_take")
                    and _same(roots, roots.args(row[2])[0], identity)]
    assert len(publications) == 1
    publication = publications[0]
    assert _dominated(roots, entries[0], publication)
    produced = roots.args(publication[2])[1]
    begin = next(row for row in roots.rows if row[2].kind == "call" and row[2].data[0] == produced)
    assert _dominated(roots, begin, publication)
    if private:
        assert roots.called(begin[2], "py_obj_private_for_begin")
        cursor = roots.args(begin[2])[1]
        cursor_base, cursor_offset = roots.slot(cursor)
        assert (cursor_offset == 0 and roots.allocas[cursor_base][0].describe() == "i64"
                and roots.allocas[cursor_base][1] == "entry")
        nexts = [row for row in roots.rows
                 if roots.called(row[2], "py_obj_private_for_next")
                 and _same(roots, roots.args(row[2])[0], identity)]
        assert len(nexts) == 1 and roots.slot(roots.args(nexts[0][2])[1]) == roots.slot(cursor)
        assert not any(roots.called(ins, "py_obj_iter_next") for _, _, ins in roots.rows)
        assert not any(roots.frame_abi(ins) is not None
                       and "frame_enter" in roots.frame_abi(ins)[0]
                       and roots.slot(roots.args(ins)[1]) == roots.slot(cursor)
                       for _, _, ins in roots.rows), "scalar cursor is not an object root"
        _private_next_reads_owner_slot()
    else:
        assert roots.called(begin[2], "py_obj_iter")
        reads = [row for row in roots.rows
                 if (address := roots.root_read(row[2])) is not None
                 and _same(roots, address, identity)]
        nexts = [row for row in roots.rows
                 if roots.called(row[2], "py_obj_iter_next")
                 and any(roots.args(row[2])[0] == read[2].data[0]
                         and _dominated(roots, read, row) for read in reads)]
        assert len(nexts) == 1
    assert _dominated(roots, publication, nexts[0])
    _retirement_paths(roots, identity, allow_guarded=True)
    return nexts[0][2].data[0]


def assert_iterator_target(roots, item):
    # The exact successful next result, not a different slot sharing its
    # source-name prefix, replaces the prior owning loop-target binding.
    transfers = [row for row in roots.rows
                 if roots.called(row[2], "pcc_gc_store_root_take")
                 and roots.args(row[2])[1] == item]
    assert len(transfers) == 1
    target = roots.slot(roots.args(transfers[0][2])[0])
    assert "value" in target[0]
    roots.require_owning(target)
    previous = [row for row in roots.rows
                if roots.called(row[2], "pcc_gc_store_root_take")
                and _same(roots, roots.args(row[2])[0], target)
                and roots.args(row[2])[1] != item]
    assert len(previous) == 1 and _dominated(roots, previous[0], transfers[0])
    assert any(roots.called(ins, "pcc_gc_root_copy_local")
               and _same(roots, roots.args(ins)[1], target)
               for _, _, ins in roots.rows), "target use must read its current owning cell"
    _root_take_releases_previous_owner()
    _retirement_paths(roots, target, allow_guarded=True)
