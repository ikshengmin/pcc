"""Production-body proofs of conservative idle-barrier publication.

The model does not establish CPU memory-ordering or native relocation. Native
fixtures below execute the forwarding install/load/retirement boundary too.
"""
import ast
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(os.environ.get('PCC_MOVING_BARRIER_SOURCE_ROOT', str(Path(__file__).resolve().parents[2])))
FLAG = 'pcc_gc_read_barrier_enabled'
POP = 'pcc_gc_forwarding_population'
FWD = 'pcc_gc_forwarding_head'
SELECTED = 'pcc_gc_relocation_set_head'


def functions(module, names, namespace, optional=()):
    p = ROOT / 'pcc/runtime/py' / module
    nodes = [n for n in ast.parse(p.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in set(names) | set(optional)]
    assert set(names) <= {n.name for n in nodes}
    for n in nodes: n.decorator_list = []
    tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            namespace.setdefault(n.func.id, lambda *args: 0)
    exec(compile(ast.fix_missing_locations(tree), str(p), 'exec'), namespace)


class Moving:
    def __init__(self, backend=4):
        self.backend = backend
        self.memory = {FLAG: 1, POP: 0, FWD: 0, SELECTED: 0,
                       'pcc_gc_backend_selected': backend, 'pcc_gc_config_initialized': 1}
        self.forward = {}
        self.target = {}
        self.identities = {}
        self.objects = set()
        self.next = 4096
        self.depth = 0
        self.lock_calls = 0
        self.events = []
        self.roots = []
        self.reads = []
        self.fail_insert = False
        self.ns = {}
        for node in ast.parse((ROOT / 'pcc/runtime/py/py_abi_constants.py').read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name): self.ns[target.id] = node.value.value
        self.ns.update(
            null=lambda: 0, ptr_is_null=lambda x: int(x == 0),
            is_tagged_int=lambda x: x & 1, ptr_eq=lambda a,b: int(a == b),
            ptr_add=lambda a,b: a+b, ptr_to_int=int, int_to_ptr=int,
            global_addr=lambda name: name,
            global_load_ptr=lambda name: self.load(name, 0),
            global_store_ptr=lambda name,value: self.store(name, 0, value),
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            atomic_load_i64=self.load, store_i32=self.store, store_i64=self.store,
            store_ptr=self.store, atomic_store_i32=self.store, atomic_store_i64=self.store,
            atomic_rmw_i32=self.rmw, malloc=self.alloc, stack_alloc=self.alloc,
            free=lambda obj: self.events.append(('free', obj)), cstr=str,
            pcc_py_gc_minor_graph_lock=self.lock,
            pcc_py_gc_minor_graph_unlock=self.unlock,
            pcc_gc_config_ensure=lambda: backend, _init_config=lambda: backend,
            pcc_gc_backend=lambda: backend, pcc_current_thread_id=lambda: 7,
            pcc_thread_safepoint=lambda: self.events.append(('poll',)),
            pcc_gc_object_is_known_no_lock=lambda obj: int(obj in self.objects),
            # This finite predicate supplies stable-known admission only;
            # native index-publication ordering is outside this model.
            pcc_gc_object_is_known_stable=lambda obj: int(obj in self.objects),
            pcc_gc_object_is_address_pinned=lambda obj: int(bool(self.load(obj, 12) & 64)),
            pcc_gc_forwarding_head=lambda: self.load(FWD, 0),
            pcc_gc_forwarding_list_head=lambda: self.load(FWD, 0),
            pcc_gc_forwarding_find=lambda obj: self.forward.get(obj, 0),
            pcc_gc_forwarding_index_find=lambda obj: self.forward.get(obj, 0),
            pcc_gc_forwarding_index_remove=lambda obj: self.forward.pop(obj, 0),
            pcc_gc_forwarding_target_index_remove=lambda obj: self.target.pop(obj, 0),
            pcc_gc_forwarding_index_clear=self.forward.clear,
            pcc_gc_forwarding_target_index_clear=self.target.clear,
            pcc_gc_forwarding_index_insert=lambda obj,node: self.index_insert(0,obj,node),
            pcc_gc_forwarding_plan_index_insert=self.index_insert,
            pcc_gc_forwarding_plan_index_commit=lambda *args: 1,
            pcc_gc_identity_ensure=self.identity, _identity_find=self.identity,
            _identity_assign=lambda obj,ident: 1,
            _forwarding_target_find=lambda obj: self.target.get(obj, 0),
            _forwarding_target_prepare=lambda obj,node: node,
            _forwarding_target_attach_prepared=self.attach,
            pcc_gc_forwarding_target_unlink=self.detach_target,
            _forwarding_target_exists=lambda obj: int(obj in self.target),
            pcc_gc_forwarding_target_exists=lambda obj: int(obj in self.target),
            py_incref=lambda obj: None, py_decref=lambda obj: None,
            _zpage_node_for_owner=lambda obj: 0,
            pcc_gc_generational_oldify_supported_tag=lambda tag: 1,
            abi_constant=lambda key: -999,
            pcc_gc_relocation_member_maybe=lambda obj: 1,
            pcc_gc_relocation_member_note=lambda obj: self.events.append(('member-add',obj)),
            pcc_gc_relocation_member_forget=lambda obj: self.events.append(('member-remove',obj)),
            pcc_gc_relocation_member_clear=lambda: self.events.append(('members-clear',)),
            _forwarding_find=lambda obj: self.forward.get(obj, 0),
            _object_head=lambda: 0,
            _relocation_reset_finish=lambda *args: None,
            _backend4_evacuation_page_finish_detached=lambda *args: None,
            _backend4_note_page_candidate=lambda *args: None,
            pcc_gc_backend4_zpage_note_forwarding_removed=lambda obj: None,
            pcc_gc_backend4_note_forwarding_removed_on_page=lambda page: None,
            pcc_gc_relocation_retire_source_payload_for_target_death_into_finish=lambda *args: 1,
            pcc_gc_relocation_retire_source_payload_into_finish=lambda *args: 1,
            _retire_forwarded_source_into_finish=lambda *args: self.events.append(('retire',)),
            pcc_gc_object_list_head=lambda: 0,
            _release_retained_pages=lambda: 0,
            pcc_gc_backend4_drain_parked_pages=lambda: None,
            pcc_gc_visit_registered_root_slots=self.rewrite_roots,
            pcc_gc_note_load=lambda: self.events.append(('slow-load',)),
            _gc_relocation_candidate=lambda obj: int(bool(self.load(obj,12)&2048)),
            pcc_gc_backend4_slot_needs_resolve=lambda obj: int(obj in self.forward),
            pcc_gc_note_relocation_read=self.resolve,
            _heal_forwarded_field=lambda slot,old,new: self.store(slot,0,new),
            pcc_gc_slot_is_runtime_root=lambda slot: int(slot in self.roots),
        )
        functions('freestanding_gc_forwarding_identity.py', {
            '_graph_lock','_graph_unlock','_set_forwarding_head',
            'pcc_gc_forwarding_unlink_main','pcc_gc_forwarding_clear_all',
            'pcc_gc_install_forwarding_unlocked','pcc_gc_install_forwarding_preallocated_unlocked',
            'pcc_gc_install_forwarding'}, self.ns, ('pcc_gc_refresh_read_barrier',))
        functions('freestanding_gc_forwarding_retirement.py', {
            'pcc_gc_forwarding_detach','pcc_gc_forwarding_remove_target',
            'pcc_gc_backend4_remap_and_retire_unlocked'}, self.ns)
        functions('py_gc_backend.py', {'_object_graph_lock','_object_graph_unlock',
            '_relocation_set_head','_set_relocation_set_head','_relocation_set_find',
            '_relocation_set_add','_relocation_set_remove','pcc_gc_reset_relocation_set',
            '_evacuation_page_head','_set_evacuation_page_head'}, self.ns)
        functions('freestanding_gc_relocation_selector.py', {'_backend4_add_candidate_node'}, self.ns)
        functions('py_obj.py', {'pcc_gc_load_ptr','pcc_gc_load_borrowed_ptr',
            '_gc_backend_fast','_gc_forwarding_population','_gc_backend4_should_check_slot'}, self.ns)

    def key(self, base, offset): return (base,offset) if isinstance(base,str) and offset else base if isinstance(base,str) else base+offset
    def load(self, base, offset=0, *order): return self.memory.get(self.key(base,offset),0)
    def store(self, base, offset, value, *order):
        self.memory[self.key(base,offset)] = value
        if base in (FLAG, POP, FWD, SELECTED): self.events.append(('store',base,value,self.load(FLAG)))
    def rmw(self, op,base,offset,value,*order):
        old=self.load(base,offset);self.store(base,offset,old|value if op=='or' else old+value);return old
    def lock(self): self.depth+=1;self.lock_calls+=1
    def unlock(self): self.depth-=1;assert self.depth>=0
    def alloc(self,size): self.next+=max(size,128);return self.next
    def object(self, tag=6):
        obj=self.alloc(128);self.objects.add(obj);self.store(obj,8,tag);return obj
    def identity(self,obj):
        if obj not in self.identities:
            self.identities[obj]=self.alloc(32);self.store(self.identities[obj],8,41)
        return self.identities[obj]
    def index_insert(self,kind,obj,node):
        if kind==0:
            self.events.append(('publish-forward',self.load(FLAG)))
            if self.fail_insert:return -1
            self.forward[obj]=node
        elif kind==1:self.target[obj]=node
        return 1
    def attach(self,node,head): self.target[self.load(node,8)]=node
    def detach_target(self,node): self.target.pop(self.load(node,8),None)
    def resolve(self,obj):
        node=self.forward.get(obj,0);return self.load(node,8) if node else obj
    def rewrite_roots(self,*args):
        self.events.append(('rewrite',))
        for slot in self.roots:self.store(slot,0,self.resolve(self.load(slot)))
    def cas(self,base,offset,expected,value,*order):
        old=self.load(base,offset)
        if old==expected:self.store(base,offset,value)
        return old
    def refresh(self):self.ns['pcc_gc_refresh_read_barrier']();assert self.depth==0
    def install(self,old,new,kind='public'):
        if kind=='public':return self.ns['pcc_gc_install_forwarding'](old,new)
        self.lock()
        try:
            if kind=='preallocated':
                plan=self.alloc(72);self.store(plan,16,self.alloc(56))
                return self.ns['pcc_gc_install_forwarding_preallocated_unlocked'](old,new,plan)
            return self.ns['pcc_gc_install_forwarding_unlocked'](old,new)
        finally:self.unlock()


@pytest.mark.parametrize('backend',range(5))
@pytest.mark.parametrize('initialized',(0,1))
@pytest.mark.parametrize('population,head,selected',((0,0,0),(1,0,0),(0,99,0),(0,0,99),(-1,0,0)))
def test_refresh_never_disables_a_window_with_possible_moving_work(backend,initialized,population,head,selected):
    m=Moving(backend);m.memory.update({'pcc_gc_config_initialized':initialized,POP:population,FWD:head,SELECTED:selected})
    m.refresh()
    expected=1 if not initialized else int(backend in (3,4) and (population!=0 or head!=0 or backend==4 and selected!=0))
    assert m.load(FLAG)==expected


@pytest.mark.parametrize('backend',(3,4))
@pytest.mark.parametrize('kind',('public','unlocked','preallocated'))
@pytest.mark.parametrize('entry',('pcc_gc_load_ptr','pcc_gc_load_borrowed_ptr'))
def test_first_forwarding_is_enabled_before_publication_and_last_detach_retires_it(backend,kind,entry):
    m=Moving(backend);old=m.object();new=m.object();slot=m.alloc(8);m.store(slot,0,old)
    m.refresh();assert m.load(FLAG)==0
    assert m.ns[entry](0,slot)==old
    assert m.install(old,new,kind)==0
    assert ('publish-forward',1) in m.events and m.load(FLAG)==1
    assert m.ns[entry](0,slot)==new and m.load(slot)==new
    m.lock();m.ns['pcc_gc_forwarding_detach'](old);m.unlock()
    assert m.load(POP)==0 and m.load(FLAG)==0


@pytest.mark.parametrize('writer',('ordinary','selector'))
def test_relocation_set_without_forwarding_keeps_slow_path_until_reset(writer):
    m=Moving();old=m.object();m.refresh()
    m.lock()
    if writer=='ordinary':assert m.ns['_relocation_set_add'](old)==1
    else:
        node=m.alloc(40);page=m.alloc(128);plan=m.alloc(16);m.store(node,0,old);m.store(node,8,page);m.store(node,32,32);m.store(page,108,1);m.store(plan,0,m.alloc(16))
        assert m.ns['_backend4_add_candidate_node'](node,0,plan)==1
    m.unlock()
    assert m.load(POP)==0 and m.load(FLAG)==1 and m.load(SELECTED)!=0
    first=next(e for e in m.events if e[:2]==('store',SELECTED))
    assert first[3]==1
    m.ns['pcc_gc_reset_relocation_set']()
    assert m.load(SELECTED)==0 and m.load(FLAG)==0 and m.depth==0


def test_last_forwarding_does_not_disable_a_nonempty_relocation_set():
    m=Moving();a=m.object();b=m.object();pending=m.object();m.refresh();m.install(a,b)
    m.lock();m.ns['_relocation_set_add'](pending);m.ns['pcc_gc_forwarding_detach'](a);m.unlock()
    assert m.load(POP)==0 and m.load(FLAG)==1
    m.lock();m.ns['_relocation_set_remove'](pending);m.unlock()
    assert m.load(FLAG)==0


@pytest.mark.parametrize('retirement',('clear','target-death','remap'))
def test_other_population_retirement_paths_refresh_only_after_unlink(retirement):
    m=Moving();old=m.object();new=m.object();m.refresh();m.install(old,new)
    slot=m.alloc(8);m.store(slot,0,old);m.roots=[slot]
    m.lock()
    if retirement=='clear':m.ns['pcc_gc_forwarding_clear_all']()
    elif retirement=='target-death':m.ns['pcc_gc_forwarding_remove_target'](new,m.alloc(48))
    else:
        finish=m.alloc(48);m.ns['pcc_gc_backend4_remap_and_retire_unlocked'](finish)
        assert m.load(FLAG)==1
        m.ns['pcc_gc_backend4_remap_and_retire_unlocked'](finish)
        assert m.load(slot)==new
    m.unlock()
    assert not m.forward and m.load(FWD)==m.load(POP)==m.load(FLAG)==0


def test_failed_install_can_keep_conservative_slow_path_without_publishing_an_edge():
    m=Moving();a=m.object();b=m.object();m.refresh();m.fail_insert=True
    assert m.install(a,b)==-1
    assert not m.forward and m.load(POP)==0 and m.load(FLAG)==1


@pytest.mark.parametrize('backend',(0,3,4))
def test_tuple_hash_rechecks_later_slots_after_user_hash_starts_forwarding(backend):
    m=Moving(backend);outer=m.object(m.ns['PY_TYPE_TUPLE']);trigger=m.object(m.ns['PY_TYPE_USER_CLASS_START']);old=m.object(m.ns['PY_TYPE_USER_CLASS_START']);new=m.object(m.ns['PY_TYPE_USER_CLASS_START'])
    m.store(outer,16,2);m.store(outer,24,trigger);m.store(outer,32,old);m.store(FLAG,0,0)
    seen=[]
    def user_hash(obj,handled):
        seen.append(obj)
        if obj==trigger and backend in (3,4):
            # Model the proposed publisher with the old/new tuple consumer:
            # movement becomes visible during the first user callback.
            m.store(FLAG,0,1)
            assert m.install(old,new)==0
        return 7
    m.ns.update(py_user_hash_dispatch=user_hash,py_err_occurred=lambda:0,
        logical_shift_left_i64=lambda value,bits:(value<<bits)&((1<<64)-1),
        logical_shift_right_i64=lambda value,bits:(value&((1<<64)-1))>>bits,
        untag_int=lambda value:value>>1,_hash_i64=lambda value:value)
    functions('py_obj_ops_compare.py',{'py_obj_hash'},m.ns)
    m.ns['py_obj_hash'](outer)
    assert seen==[trigger,new if backend in (3,4) else old]
    if backend==0:assert ('slow-load',) not in m.events


@pytest.mark.parametrize('backend', range(5))
def test_actual_configuration_initializes_the_idle_gate(backend):
    m=Moving(backend)
    m.store('pcc_gc_config_initialized',0,0)
    m.ns.update(pcc_platform_getenv=lambda name:0, pcc_threads_enabled=lambda:1,
                pcc_gc_maybe_start_cms_worker=lambda:None)
    functions('freestanding_gc_public_collection.py', {
        'pcc_gc_config_ensure','pcc_gc_config_parse_env_i32',
        '_pcc_gc_config_parse_backend','_pcc_gc_config_abort_bad_backend'},m.ns)
    assert m.ns['pcc_gc_config_ensure']()==backend
    assert m.load(FLAG)==0 and m.depth==0


@pytest.mark.parametrize('backend',(3,4))
@pytest.mark.parametrize('population,selected',((0,0),(1,0),(0,9000)))
def test_actual_same_backend_reset_republishes_current_work_state(backend,population,selected):
    m=Moving(backend);m.store(POP,0,population);m.store(SELECTED,0,selected)
    m.ns.update(atomic_load_i32=m.load,atomic_cas_i64=m.cas,
                _tracing_cycle_epoch_advance_unlocked=lambda:1,
                _backend_uses_forwarding=lambda:1,
                pcc_gc_reset_relocation_set=lambda:None)
    functions('py_gc_backend.py', {'pcc_gc_set_backend'},m.ns)
    assert m.ns['pcc_gc_set_backend'](backend)==0
    assert m.load(FLAG)==int(population>0 or backend==4 and selected!=0)
    assert m.depth==0


def test_all_authoritative_population_and_selected_head_writers_are_enumerated():
    expected={
        ('freestanding_gc_forwarding_identity.py','pcc_gc_forwarding_clear_all',POP),
        ('freestanding_gc_forwarding_identity.py','pcc_gc_install_forwarding_preallocated_unlocked',POP),
        ('freestanding_gc_forwarding_identity.py','pcc_gc_install_forwarding_unlocked',POP),
        ('freestanding_gc_forwarding_retirement.py','pcc_gc_forwarding_detach',POP),
        ('freestanding_gc_forwarding_retirement.py','pcc_gc_forwarding_remove_target',POP),
        ('freestanding_gc_forwarding_retirement.py','pcc_gc_backend4_remap_and_retire_unlocked',POP),
        ('py_gc_backend.py','_set_relocation_set_head',SELECTED),
        ('freestanding_gc_relocation_selector.py','_backend4_add_candidate_node',SELECTED),
        ('freestanding_gc_relocation_copy.py','pcc_gc_backend4_relocate_copy_preallocated_unlocked',SELECTED),
    }
    actual=set()
    for file in {entry[0] for entry in expected}:
        tree=ast.parse((ROOT/'pcc/runtime/py'/file).read_text())
        for fn in tree.body:
            if not isinstance(fn,ast.FunctionDef):continue
            for call in ast.walk(fn):
                if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or not call.args:continue
                target=call.args[0]
                if call.func.id=='global_store_ptr' and isinstance(target,ast.Constant) and target.value==SELECTED:
                    actual.add((file,fn.name,SELECTED))
                if call.func.id=='store_i32' and isinstance(target,ast.Call) and isinstance(target.func,ast.Name) and target.func.id=='global_addr' and target.args and isinstance(target.args[0],ast.Constant) and target.args[0].value==POP:
                    actual.add((file,fn.name,POP))
    assert actual==expected
    source=(ROOT/'pcc/runtime/py/freestanding_gc_relocation_copy.py').read_text()
    tree=ast.parse(source);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='pcc_gc_backend4_relocate_copy_preallocated_unlocked')
    text=ast.get_source_segment(source,fn)
    # A successful forwarding install is mandatory before copy removes the
    # selected node. Its publication already enabled the gate; keep it on.
    assert text.index('pcc_gc_install_forwarding_preallocated_unlocked(') < text.index('global_store_ptr("pcc_gc_relocation_set_head", nxt)')


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('operation', ('empty-detach', 'empty-target', 'unmatched-target'))
def test_noop_forwarding_removal_does_not_refresh_or_reenter_graph_lock(backend, operation):
    m = Moving(backend)
    m.refresh()
    old, target, unmatched = m.object(), m.object(), m.object()
    if operation == 'unmatched-target':
        assert m.install(old, target) == 0
    # A failed publication may conservatively leave 1 with no edges. No-op
    # frees must not pay to recover this optional optimization.
    m.store(FLAG, 0, 1)
    finish = m.alloc(48)
    m.lock()
    before = m.lock_calls
    events = len(m.events)
    if operation == 'empty-detach':
        assert m.ns['pcc_gc_forwarding_detach'](unmatched) == 0
    else:
        m.ns['pcc_gc_forwarding_remove_target'](unmatched, finish)
    assert m.lock_calls == before
    assert not any(event[:2] == ('store', FLAG) for event in m.events[events:])
    m.unlock()
    assert m.load(FLAG) == 1


@pytest.mark.parametrize('nonempty', (False, True))
def test_noop_selected_removal_does_not_refresh_or_reenter_graph_lock(nonempty):
    m = Moving()
    m.refresh()
    pending, absent = m.object(), m.object()
    m.lock()
    if nonempty:
        assert m.ns['_relocation_set_add'](pending) == 1
    m.store(FLAG, 0, 1)
    before = m.lock_calls
    events = len(m.events)
    m.ns['_relocation_set_remove'](absent)
    assert m.lock_calls == before
    assert not any(event[:2] == ('store', FLAG) for event in m.events[events:])
    m.unlock()
    assert m.load(FLAG) == 1


@pytest.mark.parametrize('backend', (3, 4))
@pytest.mark.parametrize('operation', ('detach', 'target'))
def test_nonlast_forwarding_removal_keeps_gate_without_refresh(backend, operation):
    m = Moving(backend)
    old, target, other, other_target = (m.object() for _ in range(4))
    m.refresh()
    assert m.install(old, target) == 0
    assert m.install(other, other_target) == 0
    m.lock()
    before = m.lock_calls
    if operation == 'detach':
        m.ns['pcc_gc_forwarding_detach'](old)
    else:
        m.ns['pcc_gc_forwarding_remove_target'](target, m.alloc(48))
    assert m.lock_calls == before
    assert m.load(POP) == 1 and m.load(FLAG) == 1
    m.unlock()


def test_nonlast_selected_removal_keeps_gate_without_refresh():
    m = Moving()
    first, last = m.object(), m.object()
    m.refresh()
    m.lock()
    assert m.ns['_relocation_set_add'](first) == 1
    assert m.ns['_relocation_set_add'](last) == 1
    before = m.lock_calls
    m.ns['_relocation_set_remove'](last)
    assert m.lock_calls == before
    assert m.load(SELECTED) != 0 and m.load(FLAG) == 1
    m.ns['_relocation_set_remove'](first)
    assert m.lock_calls == before + 1
    assert m.load(SELECTED) == 0 and m.load(FLAG) == 0
    m.unlock()


def test_remap_first_epoch_does_not_refresh_without_retiring_an_edge():
    m = Moving()
    old, target = m.object(), m.object()
    m.refresh()
    assert m.install(old, target) == 0
    m.lock()
    before = m.lock_calls
    m.ns['pcc_gc_backend4_remap_and_retire_unlocked'](m.alloc(48))
    assert m.lock_calls == before
    assert m.load(POP) == 1 and m.load(FLAG) == 1
    m.unlock()


def test_remap_invalid_source_last_edge_still_refreshes_after_unlink():
    m = Moving()
    old, target = m.object(), m.object()
    m.refresh()
    assert m.install(old, target) == 0
    node = m.forward[old]
    # Exercise the production remap cleanup arm for null/invalid source.
    m.store(node, 0, 0)
    m.lock()
    before = m.lock_calls
    m.ns['pcc_gc_backend4_remap_and_retire_unlocked'](m.alloc(48))
    assert m.lock_calls == before + 1
    assert m.load(FWD) == 0 and m.load(POP) == 0 and m.load(FLAG) == 0
    m.unlock()
