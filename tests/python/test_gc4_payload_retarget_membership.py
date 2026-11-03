"""Payload growth preserves buffered slots without scanning unrelated owners."""

import ast
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / 'pcc/runtime/py'


def install(namespace, filename, names):
    definitions = [node for node in ast.parse((RUNTIME / filename).read_text()).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in definitions} == set(names)
    for node in definitions:
        node.decorator_list = []
    exec(compile(ast.Module(body=definitions, type_ignores=[]), filename, 'exec'), namespace)


class Retarget:
    OWNER = 0x1000
    OTHER_OWNER = 0x1100
    OWNER_NODE = 0x2000
    PAGE = 0x3000
    OLD_BASE = 0x4000
    NEW_BASE = 0x5000
    SPAN = 0x6000
    REMEMBERED = 0x7000
    PAIRS = 0x8000

    def __init__(self, *, member=False, incomplete=False, span=True, remembered=False):
        self.words = {
            self.OWNER_NODE + 8: self.PAGE,
            self.OWNER_NODE + 64: self.SPAN if span else 0,
            self.SPAN + 8: self.OLD_BASE,
            self.SPAN + 16: 64,
            self.SPAN + 24: -1,  # external payload, no page reservation
            self.SPAN + 32: self.PAGE,
            self.REMEMBERED: self.OWNER,
            self.REMEMBERED + 8: self.OLD_BASE + 24,
        }
        self.globals = {
            'pcc_gc_backend_selected': 4,
            'pcc_py_gc_store_buffer_member_incomplete': int(incomplete),
            'pcc_py_gc_store_buffer_member_slots': 0x9000,
            'pcc_py_gc_store_buffer_member_cap': 256,
        }
        self.heads = {'medium': 0, 'global': 0}
        self.buffer_nodes = set()
        self.buffer_reads = 0
        self.slot_moves = []
        self.remembered_updates = []
        self.namespace = dict(
            c_ptr=int, i64=int, null=lambda: 0,
            ptr_is_null=lambda p: p == 0, is_tagged_int=lambda p: p & 1,
            ptr_eq=lambda a, b: a == b, ptr_diff=lambda a, b: a - b,
            ptr_add=lambda a, b: a + b,
            global_addr=lambda name: name,
            global_load_ptr=lambda name: self.globals.get(name, 0),
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i64=self.store, store_ptr=self.store,
            pcc_gc_index_py_find_slot=lambda slots, capacity, owner: 0 if member else -1,
            _backend4_zpage_find=lambda owner: self.OWNER_NODE,
            _store_buffer_medium_head=lambda: self.heads['medium'],
            _store_buffer_head=lambda: self.heads['global'],
            pcc_gc_store_buffer_slot_move=self.move,
            pcc_gc_remembered_member_maybe=lambda owner: int(remembered),
            pcc_gc_remembered_member_exact=lambda: 1,
            pcc_gc_remembered_member_head=lambda owner: self.REMEMBERED,
            _backend4_remembered_page_remove_slot=lambda slot: self.remembered_updates.append(('remove', slot)),
            _backend4_remembered_page_add=lambda slot: self.remembered_updates.append(('add', slot)),
            _backend4_zpage_note_remembered_slot=lambda owner, change: None,
            _backend4_zpage_note_remembered_card=lambda owner, slot, change: None,
        )
        install(self.namespace, 'freestanding_gc_index_table.py',
                {'pcc_gc_store_buffer_member_maybe'})
        install(self.namespace, 'py_gc_backend.py',
                {'_backend4_map_mutator_payload_slot', 'pcc_gc_backend4_retarget_mutator_payload_locked'})

    def load(self, address, offset):
        if isinstance(address, str):
            return self.globals.get(address, 0)
        if address in self.buffer_nodes:
            self.buffer_reads += 1
        return self.words.get(address + offset, 0)

    def store(self, address, offset, value):
        self.words[address + offset] = value

    def move(self, old, new):
        # The slot index is retargeted before publishing the new entry slot.
        assert any(self.words[node + 8] == old for node in self.buffer_nodes)
        self.slot_moves.append((old, new))

    def entry(self, queue, owner, slot):
        node = 0x10000 + len(self.buffer_nodes) * 64
        self.buffer_nodes.add(node)
        self.words.update({node: owner, node + 8: slot,
                           node + 16: 0x1200, node + 24: self.heads[queue],
                           node + 32: 37})  # medium thread id must not become a link
        self.heads[queue] = node
        return node

    def run(self, pairs=()):
        for index, (old, new) in enumerate(pairs):
            self.words[self.PAIRS + index * 16] = old
            self.words[self.PAIRS + index * 16 + 8] = new
        return self.namespace['pcc_gc_backend4_retarget_mutator_payload_locked'](
            self.OWNER, self.OLD_BASE, 64, self.NEW_BASE, 128,
            self.PAIRS if pairs else 0, len(pairs),
        )


@pytest.mark.parametrize('span', [False, True])
def test_absent_owner_reads_no_unrelated_buffer_nodes_but_keeps_other_updates(span):
    model = Retarget(span=span, remembered=True)
    for index in range(4096):
        model.entry('medium' if index & 1 else 'global', model.OTHER_OWNER,
                    model.OLD_BASE + 8)
    before = {node: dict((offset, model.words[node + offset])
                        for offset in (0, 8, 16, 24, 32)) for node in model.buffer_nodes}
    assert model.run() == (1 if span else 2)
    assert model.buffer_reads == 0
    assert not model.slot_moves
    assert model.remembered_updates == [
        ('remove', model.OLD_BASE + 24), ('add', model.NEW_BASE + 24),
    ]
    for node, fields in before.items():
        assert {offset: model.words[node + offset] for offset in fields} == fields
    if span:
        assert model.words[model.SPAN + 8] == model.NEW_BASE
        assert model.words[model.SPAN + 16] == 128


@pytest.mark.parametrize('queues', [('medium',), ('global',), ('medium', 'global')])
@pytest.mark.parametrize('incomplete', [False, True])
def test_buffered_owner_is_retargeted_in_both_queues_with_incomplete_index_fallback(
    queues, incomplete,
):
    model = Retarget(member=not incomplete, incomplete=incomplete)
    own = []
    unrelated = []
    for queue in queues:
        unrelated.append(model.entry(queue, model.OTHER_OWNER, model.OLD_BASE + 8))
        own.append(model.entry(queue, model.OWNER, model.OLD_BASE + 16))
        unrelated.append(model.entry(queue, model.OTHER_OWNER, model.OLD_BASE + 32))
    assert model.run() == 1
    assert model.slot_moves == [(model.OLD_BASE + 16, model.NEW_BASE + 16)] * len(queues)
    assert model.buffer_reads > 0
    for node in own:
        assert model.words[node + 8] == model.NEW_BASE + 16
        assert model.words[node + 32] == 37
    for node in unrelated:
        assert model.words[node + 8] in (model.OLD_BASE + 8, model.OLD_BASE + 32)


@pytest.mark.parametrize('slot', [0, 0xA000, 0x4001])
def test_existing_null_external_and_invalid_slot_mapping_is_preserved(slot):
    model = Retarget(member=True)
    node = model.entry('medium', model.OWNER, slot)
    assert model.run() == 1
    expected = 0 if slot == model.OLD_BASE + 1 else slot
    assert model.slot_moves == [(slot, expected)]
    assert model.words[node + 8] == expected


def test_reordered_slot_pairs_preserve_explicit_mapping_and_index_update_order():
    model = Retarget(member=True)
    first = model.entry('medium', model.OWNER, model.OLD_BASE + 8)
    second = model.entry('global', model.OWNER, model.OLD_BASE + 32)
    assert model.run(((model.OLD_BASE + 32, model.NEW_BASE + 8),
                      (model.OLD_BASE + 8, model.NEW_BASE + 40))) == 1
    assert model.words[first + 8] == model.NEW_BASE + 40
    assert model.words[second + 8] == model.NEW_BASE + 8


def test_incomplete_membership_retains_scan_even_when_the_owner_is_absent():
    model = Retarget(incomplete=True)
    model.entry('global', model.OTHER_OWNER, model.OLD_BASE + 8)
    assert model.run() == 1
    assert model.buffer_reads > 0 and not model.slot_moves
