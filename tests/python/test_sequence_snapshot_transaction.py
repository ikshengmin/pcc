"""A prepared repetition snapshot copies one list version into owning fields."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def load_functions(path, names, environment):
    selected = []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.startswith('_SEQUENCE_SNAPSHOT_'):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected if isinstance(node, ast.FunctionDef)} == set(names)
    exec(compile(ast.fix_missing_locations(ast.Module(selected, [])), str(path), 'exec'), environment)


class SnapshotModel:
    def __init__(self, values, *, failure=None, mutate=False):
        self.values = tuple(values)
        self.failure = failure
        self.mutate = mutate
        self.memory = {}
        self.refs = {}
        self.leases = {}
        self.plans = {}
        self.events = []
        self.locked = False
        self.source = 1000
        self.output = 2000
        self.items = 10000
        self.source_root = 20000
        self.output_root = 20100
        self.plan_base = 30000
        self.tokens = 40000
        self.env = self.environment()
        c = self.env
        self.store(self.source_root, 0, self.source)
        self.store(self.output_root, 0, self.output)
        self.store(self.source, c['PYOBJECTHEADER_TYPE_TAG_OFFSET'], c['PY_TYPE_LIST'])
        self.store(self.output, c['PYOBJECTHEADER_TYPE_TAG_OFFSET'], c['PY_TYPE_TUPLE'])
        self.store(self.source, c['PYLISTOBJECT_LENGTH_OFFSET'], len(values))
        self.store(self.source, c['PYLISTOBJECT_ITEMS_OFFSET'], self.items)
        self.store(self.output, c['PYTUPLEOBJECT_LEN_OFFSET'], len(values))
        for index, value in enumerate(values):
            self.store(self.items, index * 8, value)
            if value and value % 2 == 0:
                self.refs[value] = self.refs.get(value, 0) + 1
            self.plans[self.plan_base + index * 128] = {'attempted': False, 'committed': False}
        load_functions(ROOT / 'pcc/runtime/py/py_obj.py', ('pcc_gc_copy_ptr_lease_commit_locked',), self.env)
        load_functions(ROOT / 'pcc/runtime/py/freestanding_sequence_snapshot.py', ('pcc_list_snapshot_commit_slots',), self.env)

    def load(self, base, offset=0):
        return self.memory.get(base + offset, 0)

    def store(self, base, offset, value):
        self.memory[base + offset] = value

    def destination(self, index):
        return self.output + self.env['PYTUPLEOBJECT_ITEMS_OFFSET'] + index * 8

    def environment(self):
        constants = {}
        for node in ast.parse((ROOT / 'pcc/runtime/py/py_abi_constants.py').read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value

        def lock():
            assert not self.locked
            self.locked = True
            self.events.append('lock')

        def unlock():
            assert self.locked
            self.locked = False
            self.events.append('unlock')
            if self.mutate:
                # Simulate a writer obtaining the lock immediately afterward.
                # All successful snapshot fields already own their objects.
                for index in range(len(self.values)):
                    value = self.load(self.items, index * 8)
                    if value and value % 2 == 0:
                        self.refs[value] -= 1
                        assert self.refs[value] >= 0
                    self.store(self.items, index * 8, 11)
                self.events.append('source-replaced')

        def acquire(source):
            assert self.locked
            value = self.load(source)
            if self.failure == 'lease' and source == self.items + 8:
                return -1
            if not value or value % 2:
                return 0
            assert self.refs[value] > 0
            self.leases[value] = self.leases.get(value, 0) + 1
            self.events.append(('acquire', value))
            return 1

        def release(slot, token):
            if token:
                value = self.load(slot)
                assert self.leases.get(value, 0) > 0
                self.leases[value] -= 1
                self.events.append(('release', value))
            return 0

        def commit(plan, owner, destination, value):
            assert self.locked and owner == self.output
            assert plan in self.plans
            assert destination in [self.destination(index) for index in range(len(self.values))]
            assert self.load(destination) == 0
            assert not self.plans[plan]['attempted']
            self.plans[plan]['attempted'] = True
            if self.failure == 'commit' and plan == self.plan_base + 128:
                return 0
            if value and value % 2 == 0:
                assert self.leases.get(value, 0) > 0
                self.refs[value] += 1
            self.store(destination, 0, value)
            self.plans[plan]['committed'] = True
            # An owner-aware barrier is part of this primitive's contract.
            self.events.append(('barrier', owner, destination, value))
            return 1

        abi_names = {'object.pointer.size': 'C_POINTER_SIZE', 'object.list.items_offset': 'PYLISTOBJECT_ITEMS_OFFSET', 'object.list.length_offset': 'PYLISTOBJECT_LENGTH_OFFSET', 'object.header.type_tag_offset': 'PYOBJECTHEADER_TYPE_TAG_OFFSET', 'object.tuple.items_offset': 'PYTUPLEOBJECT_ITEMS_OFFSET', 'object.tuple.length_offset': 'PYTUPLEOBJECT_LEN_OFFSET', 'object.type.list': 'PY_TYPE_LIST', 'object.type.tuple': 'PY_TYPE_TUPLE'}
        constants["abi_constant"] = lambda name: constants[abi_names[name]]
        constants.update(dict(
            c_ptr=int, i64=int, ptr_is_null=lambda value: int(value == 0),
            ptr_eq=lambda a,b: int(a == b), ptr_add=lambda p,o: p+o,
            is_tagged_int=lambda value: int(value % 2 != 0),
            load_ptr=self.load, load_i32=self.load, load_i64=self.load,
            store_i64=self.store,
            pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=unlock,
            pcc_gc_foreign_lease_acquire=acquire, pcc_gc_foreign_lease_release=release,
            pcc_gc_store_ptr_plan_commit_locked=commit,
        ))
        return constants

    def run(self, length=None):
        if length is None:
            length = len(self.values)
        result = self.env['pcc_list_snapshot_commit_slots'](
            self.source_root, self.output_root, length, self.plan_base, self.tokens,
        )
        assert not self.locked
        for index in range(len(self.values)):
            # Every initialized plan is finished outside the transaction,
            # including the attempted failing plan and untouched no-op plans.
            self.events.append(('finish', index))
            plan = self.plans[self.plan_base + index * 128]
            if plan['committed']:
                value = self.load(self.destination(index))
                if value and value % 2 == 0:
                    assert self.refs[value] > 0 and self.leases[value] > 0
            token = self.load(self.tokens, index * 8)
            self.env['pcc_gc_foreign_lease_release'](self.destination(index), token)
            self.store(self.tokens, index * 8, 0)
        assert all(count == 0 for count in self.leases.values())
        return result

    def drop_snapshot(self):
        assert not self.locked and all(count == 0 for count in self.leases.values())
        for index in range(len(self.values)):
            value = self.load(self.destination(index))
            self.store(self.destination(index), 0, 0)
            if value and value % 2 == 0:
                self.refs[value] -= 1
                assert self.refs[value] >= 0


@pytest.mark.parametrize('values', ((), (3000,), (3000,4000), (3000,3000), (11,3000,11)))
@pytest.mark.parametrize('mutate', (False, True))
def test_snapshot_owns_exact_original_items_after_source_replacement(values, mutate):
    model = SnapshotModel(values, mutate=mutate)
    assert model.run() == 0
    assert tuple(model.load(model.destination(i)) for i in range(len(values))) == values
    model.drop_snapshot()
    expected = {} if mutate else {value: values.count(value) for value in values if value and value % 2 == 0}
    assert {value: count for value,count in model.refs.items() if count} == expected


@pytest.mark.parametrize('failure', ('lease', 'commit'))
@pytest.mark.parametrize('mutate', (False, True))
def test_snapshot_partial_failure_keeps_published_owners_until_cleanup(failure, mutate):
    model = SnapshotModel((3000,4000,5000), failure=failure, mutate=mutate)
    assert model.run() == -1
    assert model.load(model.destination(0)) == 3000
    assert model.load(model.destination(1)) == model.load(model.destination(2)) == 0
    model.drop_snapshot()
    expected = {} if mutate else {3000:1,4000:1,5000:1}
    assert {value: count for value,count in model.refs.items() if count} == expected


@pytest.mark.parametrize('change', ('size', 'source_type', 'tuple_type', 'tuple_size', 'null_item'))
def test_snapshot_revalidates_shape_before_copy(change):
    model = SnapshotModel((3000,4000))
    c = model.env
    if change == 'size': model.store(model.source,c['PYLISTOBJECT_LENGTH_OFFSET'],3)
    elif change == 'source_type': model.store(model.source,c['PYOBJECTHEADER_TYPE_TAG_OFFSET'],c['PY_TYPE_TUPLE'])
    elif change == 'tuple_type': model.store(model.output,c['PYOBJECTHEADER_TYPE_TAG_OFFSET'],c['PY_TYPE_LIST'])
    elif change == 'tuple_size': model.store(model.output,c['PYTUPLEOBJECT_LEN_OFFSET'],3)
    else: model.store(model.items,0,0)
    assert model.run() == (-2 if change == 'size' else -1)
    assert all(model.load(model.destination(i)) == 0 for i in range(2))
    assert not any(plan['committed'] for plan in model.plans.values())


def test_heap_copy_rejects_a_nonempty_destination_without_touching_owners():
    model = SnapshotModel((3000,))
    model.store(model.destination(0),0,4000)
    before = dict(model.memory)
    model.env['pcc_py_gc_minor_graph_lock']()
    result = model.env['pcc_gc_copy_ptr_lease_commit_locked'](
        model.plan_base, model.output, model.destination(0), model.items,
    )
    model.env['pcc_py_gc_minor_graph_unlock']()
    assert result == -4 and model.memory == before
    assert not model.leases
