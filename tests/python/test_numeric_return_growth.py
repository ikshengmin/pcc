"""Alternative binary returns share owners; pending nested returns do not."""
from __future__ import annotations

import re

import pytest

from ir_pointer_aliases import (
    canonical_pointer,
    function_bodies,
    pointer_bitcast_aliases,
)
from pcc.frontends.python.codegen.layer1 import (
    L1CodeGen,
)
from pcc.frontends.python.py_lift import (
    parse_and_lift,
)
from pcc.frontends.python.type_infer import (
    infer_module,
)
from tests.python.test_numeric_return_owners import (
    check_return_cfg,
)


class ObservedReturns(L1CodeGen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.return_slots = {}

    def _emit_owned_return_through_finally(self, value, stmt, source_slot=None):
        super()._emit_owned_return_through_finally(value, stmt, source_slot)
        for name, entry in self.env.items():
            if name.startswith('.pcc.return.cleanup.owner.'):
                slot = entry[0]
                flag = self._owned_local_flag_for(name, slot)
                self.return_slots[str(slot)] = str(flag)


def emit(source):
    module = infer_module(parse_and_lift(source, 'growth.py', 'growth'))
    codegen = ObservedReturns(module, ir_scaffold_mode='on')
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    body = next(body for name, body in function_bodies(text) if name == 'user_growth_probe')
    return body, codegen.return_slots


def metrics(body):
    blocks = re.split(r'^([\w.$]+):\n', body, flags=re.M)
    instructions = sum(len([line for line in block.splitlines() if line.strip() and line.strip() != '}']) for block in blocks[2::2])
    return {
        'permanent_roots': body.count('@pcc_gc_frame_enter('),
        'frame_leaves': body.count('@pcc_gc_frame_leave('),
        'root_stores': body.count('@pcc_gc_store_root('),
        'blocks': (len(blocks) - 1) // 2,
        'instructions': instructions,
    }


def assert_each_acquisition_sets_its_own_cleanup_flag(body, slots):
    aliases = pointer_bitcast_aliases(body)
    canonical = lambda value: canonical_pointer(value, aliases)
    flags = {canonical(slot): canonical(flag) for slot, flag in slots.items()}
    blocks = {}
    current = None
    for line in body.splitlines():
        label = re.match(r'^([\w.$]+):', line)
        if label:
            current = label[1]
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line.strip())
    assert blocks
    edges = {name: re.findall(r'label %([\w.$]+)', '\n'.join(lines))
             for name, lines in blocks.items()}
    entry = next(iter(blocks))
    reachable = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(edges[name])
    predecessors = {name: set() for name in reachable}
    for name in reachable:
        for successor in edges[name]:
            predecessors[successor].add(name)

    def update(state, line):
        stored = re.search(r'store i1 ([^ ,]+), ptr (%[^ ,]+)', line)
        if stored:
            flag = canonical(stored[2])
            if flag in flags.values():
                if stored[1] == '1':
                    state.add(flag)
                else:
                    state.discard(flag)

    # Must analysis: a flag is ready only if every incoming path sets it.
    # Intersect predecessor states, including clears, rather than requiring
    # acquisition and publication to happen in the same basic block.
    outgoing = {name: set(flags.values()) for name in reachable}
    incoming = {}
    changed = True
    while changed:
        changed = False
        for name in blocks:
            if name not in reachable:
                continue
            incoming[name] = (set() if name == entry else
                              set.intersection(*(outgoing[p] for p in predecessors[name])))
            state = incoming[name].copy()
            for line in blocks[name]:
                update(state, line)
            if state != outgoing[name]:
                outgoing[name] = state
                changed = True
    acquisitions = 0
    for name in blocks:
        if name not in reachable:
            continue
        state = incoming[name].copy()
        for line in blocks[name]:
            update(state, line)
            move = re.search(r'@pcc_gc_root_move\(ptr (%[^ ,]+), ptr (%[^ ,)]+)\)', line)
            if move and canonical(move[1]) in flags:
                target = canonical(move[1])
                assert flags[target] in state, ('unowned runtime move', target, name)
                acquisitions += 1
            stored = re.search(r'store ptr ([^ ,]+), ptr (%[^ ,]+)', line)
            if stored and stored[1] != 'null' and canonical(stored[2]) in flags:
                target = canonical(stored[2])
                assert flags[target] in state, ('unowned scalar move', target, name)
    assert acquisitions
    return acquisitions


def cleanup_flag_diamond(left='store i1 1, ptr %ready', right='store i1 1, ptr %ready'):
    return ('define ptr @probe() {\nentry:\n'
            '  br i1 %condition, label %left, label %right\n'
            'left:\n  ' + left + '\n  br label %merge\n'
            'right:\n  ' + right + '\n  br label %merge\n'
            'merge:\n  %status = call i64 (ptr, ptr) @pcc_gc_root_move(ptr %owner, ptr %source)\n'
            '  ret ptr null\n}\n')


def test_cleanup_flag_checker_accepts_publication_on_every_incoming_path():
    text = cleanup_flag_diamond(left='store i1 1, ptr %ready\n  store ptr %value, ptr %owner')
    assert assert_each_acquisition_sets_its_own_cleanup_flag(text, {'%owner': '%ready'}) == 1


@pytest.mark.parametrize('left,right', (
    ('store i1 1, ptr %ready', ''),
    ('store i1 1, ptr %ready\n  store i1 0, ptr %ready', 'store i1 1, ptr %ready'),
    ('store i1 1, ptr %ready', 'store i1 %unknown, ptr %ready'),
    ('store ptr %value, ptr %owner\n  store i1 1, ptr %ready', 'store i1 1, ptr %ready'),
))
def test_cleanup_flag_checker_rejects_unowned_heap_and_scalar_paths(left, right):
    with pytest.raises(AssertionError, match='unowned'):
        assert_each_acquisition_sets_its_own_cleanup_flag(
            cleanup_flag_diamond(left, right), {'%owner': '%ready'},
        )


@pytest.mark.parametrize('shape', ('flat', 'nested-finally'))
def test_return_owner_and_cleanup_structure_grows_linearly(shape):
    rows = []
    for count in (10, 20, 40):
        body = ''.join('    if selector == ' + str(index) + ':\n        return left + right\n' for index in range(count - 1))
        body += '    return left + right\n'
        if shape == 'nested-finally':
            body = '    try:\n        try:\n' + ''.join('        ' + line + '\n' for line in body.splitlines()) + '        finally:\n            cleanup()\n    finally:\n        cleanup()\n'
        source = 'def cleanup():\n    pass\ndef probe(selector: int, left, right):\n' + body
        text, slots = emit(source)
        assert len(slots) == 1
        assert assert_each_acquisition_sets_its_own_cleanup_flag(text, slots) == count
        check_return_cfg(text)
        rows.append(metrics(text))
    assert rows[0]['permanent_roots'] == rows[1]['permanent_roots'] == rows[2]['permanent_roots']
    for name in ('frame_leaves', 'root_stores', 'blocks', 'instructions'):
        assert rows[2][name] - rows[1][name] == 2 * (rows[1][name] - rows[0][name]), (name, rows)


def test_overriding_return_uses_an_independent_pending_depth_owner():
    text, slots = emit('''def probe(left, right, replacement):
    try:
        return left % right
    finally:
        return replacement // right
''')
    assert len(slots) == 2
    assert len(set(slots.values())) == 2
    assert assert_each_acquisition_sets_its_own_cleanup_flag(text, slots) >= 2
    check_return_cfg(text)
