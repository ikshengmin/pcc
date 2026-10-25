"""Actual eager-dict materialization bodies against traced ownership memory."""
import ast
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_dict_super_slot_roots import DictMemory
from tests.python.test_set_call_slot_roots import Block, Object


PORT = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_dict.py'


class MaterializeMemory(DictMemory):
    def __init__(self, phase='register', **faults):
        super().__init__(phase, **faults)
        self.fail_stage = None
        self.runtime_active = False
        self.after_append = None
        self.retired_tables = set()
        self.frames = []
        self.ns.update(
            pcc_gc_frame_enter=self.frame_enter,
            pcc_gc_frame_leave=self.frame_leave,
            pcc_gc_pin=self.pin,
            pcc_gc_take_pinned_slot=self.take,
            global_addr=lambda name: name,
            py_list_new=self.new_list,
            py_tuple_new=self.new_tuple,
            py_list_append=self.append_list,
            py_tuple_set_item=self.set_tuple,
            py_tuple_from_owned_roots=self.tuple_from_owned_roots,
            pcc_gc_store_root=self.store_root,
        )
        tree = ast.parse(PORT.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id.startswith('_DICT_MATERIALIZE_'):
                    self.ns[node.targets[0].id] = ast.literal_eval(node.value)
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and (node.name.startswith('_dict_materialize_') or node.name in ('py_dict_keys','py_dict_values','py_dict_items'))]
        for node in selected:
            node.decorator_list = []
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(PORT),'exec'),self.ns)

    def read(self, pointer, offset):
        if isinstance(pointer, str):
            assert pointer == "pcc_gc_read_barrier_enabled" and offset == 0
            return 0
        block, _ = self.location(pointer, offset)
        assert block not in getattr(self, 'retired_tables', ()), 'stale dictionary table'
        return super().read(pointer, offset)

    def fail(self, stage):
        if self.runtime_active and self.fail_stage == stage:
            self.error = (19, stage)
            return True
        return False

    def new_list(self, size):
        if self.fail('list'):
            return None
        return super().new_list(size)

    def new_tuple(self, size):
        if self.fail('pair'):
            return None
        return super().new_tuple(size)

    def set_tuple(self, obj, index, value):
        assert obj.leases > 0
        if isinstance(value, Object):
            assert value.leases > 0
        if self.fail('pair'+str(index)):
            return
        super().set_tuple(obj,index,value)

    def tuple_from_owned_roots(self, sources, count, output):
        pair = self.new_tuple(count)
        if pair is None:
            return -1
        self.write(output, 0, pair)
        for index in range(count):
            if self.fail('pair' + str(index)):
                return -1
            source = self.read(sources, index * abi.C_POINTER_SIZE)
            value = self.read(source, 0)
            if isinstance(value, Object):
                assert value.leases == 0
            pair.fields[abi.PYTUPLEOBJECT_ITEMS_OFFSET + index * abi.C_POINTER_SIZE] = value
            self.write(source, 0, None)
        return 0

    def append_list(self, obj, value):
        self.collect('callback')
        if self.fail('append'):
            return
        super().append_list(obj,value)
        if self.after_append:
            callback,self.after_append=self.after_append,None
            callback()

    def drop(self, obj):
        obj.refs -= 1
        assert obj.refs >= 0
        if obj.refs:
            return
        if obj.tag == abi.PY_TYPE_LIST:
            count=obj.fields[abi.PYLISTOBJECT_LENGTH_OFFSET]
            children=[self.read(obj.fields[abi.PYLISTOBJECT_ITEMS_OFFSET],index*8) for index in range(count)]
        elif obj.tag == abi.PY_TYPE_TUPLE:
            children=[self.read(obj,abi.PYTUPLEOBJECT_ITEMS_OFFSET+index*8) for index in range(obj.fields[abi.PYTUPLEOBJECT_LEN_OFFSET])]
        else:
            children=[]
        for child in children:
            if isinstance(child,Object):
                self.drop(child)

    def store_root(self, slot, value):
        self.collect('drop')
        old=self.read(slot,0)
        self.write(slot,0,value)
        if isinstance(old,Object):
            self.drop(old)
        if isinstance(value,Object):
            value.refs+=1

    def frame_enter(self, name, slots):
        count=1 if name.endswith('borrowed_map') else 2
        handles=[]
        for index in range(count):
            handle=object();self.roots[handle]=self.add(slots,index*8);handles.append(handle)
        self.frames.append((slots,handles))
        self.collect('frame')

    def frame_leave(self, slots):
        actual,handles=self.frames.pop()
        assert actual is slots
        for handle in handles:
            del self.roots[handle]
        self.collect('frame')

    def pin(self,obj):
        assert obj.leases>0
        obj.fields[abi.PYOBJECTHEADER_FLAGS_OFFSET] |= abi.PY_FLAG_GC_PINNED
        obj.leases+=1

    def take(self,slot,prior):
        value=self.read(slot,0)
        if isinstance(value,Object):
            assert value.fields[abi.PYOBJECTHEADER_FLAGS_OFFSET] & abi.PY_FLAG_GC_PINNED
            value.fields[abi.PYOBJECTHEADER_FLAGS_OFFSET]=prior
            value.leases-=1
        self.write(slot,0,None)
        return value

    def materialize(self, data, kind, raw=False):
        receiver=self.wrap(data)
        caller=self.input_roots([receiver,None])
        source,result=caller,self.add(caller,8)
        expected_handles=set(self.roots)
        self.runtime_active=True
        if raw:
            token=self.acquire(source)
            value=self.ns['py_dict_'+kind](self.read(source,0))
            self.release(source,token)
        else:
            status=self.ns['_dict_materialize_bound'](source,result,{'keys':0,'values':1,'items':2}[kind])
            value=self.read(result,0)
            assert status == (-1 if self.error is not None else 0)
        assert set(self.roots)==expected_handles and not self.frames
        assert all(obj.leases==0 for obj in self.objects if obj is not self.none)
        return value,source


@pytest.mark.parametrize('kind', ['keys','values','items'])
@pytest.mark.parametrize('phase', ['register','copy','acquire','release','drop','callback'])
def test_materialized_dict_children_survive_every_boundary(kind,phase):
    model=MaterializeMemory(phase)
    value,source=model.materialize({'a':'first','b':'second'},kind)
    expected={'keys':['a','b'],'values':['first','second'],'items':[('a','first'),('b','second')]}[kind]
    assert model.unwrap(value)==expected
    assert value.refs==1
    assert model.unwrap(model.read(source,0))=={'a':'first','b':'second'}
    assert model.error is None


@pytest.mark.parametrize('stage', ['list','pair','pair0','pair1','append'])
def test_allocation_and_append_errors_release_partial_outputs(stage):
    model=MaterializeMemory('release');model.fail_stage=stage
    value,source=model.materialize({'a':'first'},'items')
    assert value is None and model.error==(19,stage)
    assert model.unwrap(model.read(source,0))=={'a':'first'}
    assert all(obj.refs==0 for obj in model.objects if obj.alive and obj.tag in (abi.PY_TYPE_LIST,abi.PY_TYPE_TUPLE))


@pytest.mark.parametrize('failed_copy',[1,2,3])
def test_failed_source_or_pair_copy_does_not_leak_a_lease(failed_copy):
    model=MaterializeMemory('release',fail_copy=failed_copy)
    result,_=model.materialize({'a':'first'},'items')
    assert result is None and model.error


@pytest.mark.parametrize('failed', [False, True])
def test_materialization_root_group_registration_is_balanced(failed):
    model=MaterializeMemory('register',fail_register=1 if failed else 0)
    result,_=model.materialize({'a':'first'},'items')
    assert model.registrations == 1
    assert not model.root_groups
    if failed:
        assert result is None and model.error
    else:
        assert model.unwrap(result) == [('a', 'first')]
        assert model.error is None


@pytest.mark.parametrize('kind',['keys','values','items'])
def test_existing_raw_abi_keeps_result_owned_through_terminal_return(kind):
    model=MaterializeMemory('frame')
    result,_=model.materialize({'a':'first'},kind,raw=True)
    assert model.unwrap(result)=={'keys':['a'],'values':['first'],'items':[('a','first')]}[kind]
    assert result.refs==1


@pytest.mark.parametrize('kind',['keys','values','items'])
def test_table_replacement_does_not_leave_a_borrowed_payload_address(kind):
    model=MaterializeMemory('none')
    receiver=model.wrap({'a':'first','b':'second'})
    caller=model.input_roots([receiver,None]);result=model.add(caller,8)
    def replace_table():
        current=model.read(caller,0)
        old=current.fields[abi.PYDICTOBJECT_ENTRIES_OFFSET]
        replacement=Block();replacement.fields=old.fields.copy()
        offset=abi.DICTENTRY_SIZE+abi.DICTENTRY_VALUE_OFFSET
        prior=replacement.fields[offset]
        replacement.fields[offset]=model.wrap('changed')
        replacement.fields[offset].refs+=1
        prior.refs-=1
        current.fields[abi.PYDICTOBJECT_ENTRIES_OFFSET]=replacement
        model.retired_tables.add(old)
    model.after_append=replace_table
    model.runtime_active=True
    assert model.ns['_dict_materialize_bound'](caller,result,{'keys':0,'values':1,'items':2}[kind])==0
    assert model.unwrap(model.read(result,0))=={
        'keys':['a','b'],'values':['first','changed'],'items':[('a','first'),('b','changed')]}[kind]
    assert len(model.roots)==2 and model.depth==0


@pytest.mark.parametrize('failed',[False,True])
@pytest.mark.parametrize('kind', ['keys', 'values', 'items'])
def test_output_cleanup_preserves_selected_or_entry_exception(failed, kind):
    model=MaterializeMemory('release')
    receiver=model.wrap({'a':'first'})
    caller=model.input_roots([receiver,None]);result=model.add(caller,8)
    previous=(2,'entry');cleanup=(7,'cleanup')
    model.error=previous
    model.fail_stage='append' if failed else None
    model.runtime_active=True
    drop=model.ns['pcc_gc_store_root']
    def drop_with_error(slot,value):
        old=model.read(slot,0)
        drop(slot,value)
        if isinstance(old,Object) and old.tag==abi.PY_TYPE_DICT and value is None:
            model.error=cleanup
    model.ns['pcc_gc_store_root']=drop_with_error
    assert model.ns['_dict_materialize_bound'](caller,result,{'keys':0,'values':1,'items':2}[kind])==(-1 if failed else 0)
    assert model.error==((19,'append') if failed else previous)
    assert len(model.roots)==2


@pytest.mark.parametrize('kind', ['keys', 'values', 'items'])
@pytest.mark.parametrize('phase', ['register', 'copy', 'acquire', 'release', 'drop', 'callback', 'lock', 'move', 'unregister'])
def test_materialization_only_retains_and_drops_requested_children(kind, phase):
    model = MaterializeMemory(phase)
    prepared = []
    finished = []
    dropped = []
    prepare = model.ns['pcc_gc_root_copy_lease_prepare_locked']
    finish = model.ns['pcc_gc_root_copy_lease_finish']
    drop = model.ns['_dict_slot_drop']

    def prepare_selected(destination, source, borrowed, plan):
        prepared.append((model.location(destination, 0)[1] // abi.C_POINTER_SIZE, plan))
        return prepare(destination, source, borrowed, plan)

    def finish_selected(plan):
        finished.append(plan)
        finish(plan)

    def drop_selected(slots, tokens, index):
        dropped.append(index)
        drop(slots, tokens, index)

    model.ns.update(
        pcc_gc_root_copy_lease_prepare_locked=prepare_selected,
        pcc_gc_root_copy_lease_finish=finish_selected,
        _dict_slot_drop=drop_selected,
    )
    result, source = model.materialize({'a': 'first', 'b': 'second'}, kind)
    roles = {'keys': [3], 'values': [4], 'items': [3, 4]}[kind]
    assert [role for role, _ in prepared] == roles * 2
    assert finished == [plan for _, plan in prepared]
    assert model.copies == 1 + len(roles) * 2  # receiver plus selected children
    # close() still visits its fixed scratch frame exactly once; the per-item
    # cleanup must not visit the unused sibling or a nonexistent pair.
    for role in (3, 4, 5):
        per_item = (role in roles if kind != 'items' else role == 5)
        assert dropped.count(role) == 1 + (2 if per_item else 0)
    assert model.unwrap(result) == {
        'keys': ['a', 'b'], 'values': ['first', 'second'],
        'items': [('a', 'first'), ('b', 'second')],
    }[kind]
    assert model.unwrap(model.read(source, 0)) == {'a': 'first', 'b': 'second'}
    assert result.refs == 1 and model.error is None


@pytest.mark.parametrize('kind', ['keys', 'values'])
@pytest.mark.parametrize('failed_copy', [2, 3])
def test_selected_child_transfer_failure_cleans_partial_materialization(kind, failed_copy):
    model = MaterializeMemory('release', fail_copy=failed_copy)
    result, source = model.materialize({'a': 'first', 'b': 'second'}, kind)
    assert result is None and model.error
    assert model.unwrap(model.read(source, 0)) == {'a': 'first', 'b': 'second'}
    assert all(obj.refs == 0 for obj in model.objects
               if obj.alive and obj.tag in (abi.PY_TYPE_LIST, abi.PY_TYPE_TUPLE))


@pytest.mark.parametrize('kind', ['keys', 'values', 'items'])
def test_entry_replacement_can_retire_unused_sibling_after_unlock(kind):
    model = MaterializeMemory('release')
    receiver = model.wrap({'a': 'first', 'b': 'second'})
    # wrap() keeps its fixture-construction references in addition to the
    # dictionary owners. Retire those so replacing an unselected child really
    # destroys its final owner, rather than merely decrementing a live object.
    entries = receiver.fields[abi.PYDICTOBJECT_ENTRIES_OFFSET]
    for index in range(2):
        for offset in (abi.DICTENTRY_KEY_OFFSET, abi.DICTENTRY_VALUE_OFFSET):
            model.drop(entries.fields[index * abi.DICTENTRY_SIZE + offset])
    caller = model.input_roots([receiver, None])
    result = model.add(caller, abi.C_POINTER_SIZE)
    finish = model.ns['pcc_gc_root_copy_lease_finish']
    replaced = []
    retired = []

    def replace_after_selection(plan):
        finish(plan)
        if replaced:
            return
        replaced.append(True)
        assert model.depth == 0
        current = model.read(caller, 0)
        old = current.fields[abi.PYDICTOBJECT_ENTRIES_OFFSET]
        replacement = Block()
        replacement.fields = old.fields.copy()
        for offset, value in ((abi.DICTENTRY_KEY_OFFSET, 'new-key'),
                              (abi.DICTENTRY_VALUE_OFFSET, 'new-value')):
            previous = replacement.fields[offset]
            # Transfer the new object's construction reference to the table.
            replacement.fields[offset] = model.wrap(value)
            model.drop(previous)
            if previous.refs == 0:
                assert previous.leases == 0
                previous.alive = False
                retired.append(previous.value)
        current.fields[abi.PYDICTOBJECT_ENTRIES_OFFSET] = replacement
        model.retired_tables.add(old)

    model.ns['pcc_gc_root_copy_lease_finish'] = replace_after_selection
    model.runtime_active = True
    assert model.ns['_dict_materialize_bound'](
        caller, result, {'keys': 0, 'values': 1, 'items': 2}[kind]) == 0
    assert retired == {'keys': ['first'], 'values': ['a'], 'items': []}[kind]
    assert model.unwrap(model.read(result, 0)) == {
        'keys': ['a', 'b'], 'values': ['first', 'second'],
        'items': [('a', 'first'), ('b', 'second')],
    }[kind]
    assert model.unwrap(model.read(caller, 0)) == {'new-key': 'new-value', 'b': 'second'}
    assert len(model.roots) == 2 and not model.root_groups and model.depth == 0
    assert all(obj.leases == 0 for obj in model.objects if obj is not model.none)
    assert model.error is None


def test_terminal_return_lease_failure_preserves_error_while_dropping_output():
    model=MaterializeMemory('none')
    acquire=model.ns['pcc_gc_foreign_lease_acquire']
    def acquire_final(slot):
        value=model.read(slot,0)
        if isinstance(value,Object) and value.tag==abi.PY_TYPE_LIST and len(model.roots)==5:
            return -2
        return acquire(slot)
    model.ns['pcc_gc_foreign_lease_acquire']=acquire_final
    drop=model.ns['pcc_gc_store_root']
    def drop_with_error(slot,value):
        old=model.read(slot,0)
        drop(slot,value)
        if isinstance(old,Object) and old.tag==abi.PY_TYPE_LIST and value is None:
            model.error=(7,'output finalizer')
    model.ns['pcc_gc_store_root']=drop_with_error
    result,_=model.materialize({'a':'first'},'items',raw=True)
    assert result is None
    assert model.error==(15,'dictionary result return lease failed')
