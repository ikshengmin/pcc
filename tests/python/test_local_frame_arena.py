"""Activation-private frame storage: actual raw bodies, modeled memory/kernel.

Specification: docs/project-intent.md shared slot/root and native handle
contract; docs/compiler-contract.md runtime dependency ownership.
"""
from __future__ import annotations
import ast
from pathlib import Path
import pytest

from tests.python.runtime_body_model import load_functions

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "pcc/runtime/py"


class Arena:
    def __init__(self):
        self.memory = {}
        self.globals = {}
        self.next = 4096
        self.allocations = 0
        self.fail_at = 0
        self.freed = []
        self.depth = 0
        self.stopped = 0
        self.thread = 1
        self.records = {1: self.allocate(216), 2: self.allocate(216)}
        self.write(self.records[1], 0, self.records[2])
        for owner, record in self.records.items():
            self.write(record, 8, owner)
        self.globals['pcc_thread_records_head_py'] = self.records[1]
        self.globals.update(pcc_gc_config_initialized=1, pcc_gc_backend_selected=4,
                            pcc_gc_backend0_frame_roots_enabled=0)
        self.ns = dict(i64=int, c_ptr=int, null=lambda: 0,
            ptr_is_null=lambda value: int(value == 0), ptr_eq=lambda a,b: int(a == b),
            ptr_to_int=lambda value: value, ptr_add=lambda value,offset: value+offset,
            load_i32=self.read, load_i64=self.read, load_ptr=self.read,
            store_i32=self.write, store_i64=self.write, store_ptr=self.write,
            memset=self.zero, malloc=self.allocate, free=self.freed.append,
            global_addr=lambda name: name, global_load_ptr=self.get_global,
            global_store_ptr=lambda name,value: self.globals.__setitem__(name,value),
            pcc_current_thread_id=lambda: self.thread,
            pcc_thread_is_running=lambda: int(self.read(self.records[self.thread],16)==0),
            pcc_thread_no_park_depth=lambda: self.depth,
            pcc_thread_no_park_enter=self.enter_guard, pcc_thread_no_park_exit=self.exit_guard,
            pcc_thread_owns_stopped_world=lambda: self.stopped,
            pcc_platform_abort=self.abort, gc_backend_current=lambda: self.globals['pcc_gc_backend_selected'],
            call_void_ptr2=lambda fn,a,b: fn(a,b),
        )
        self.load('freestanding_gc_root_registry.py', {'pcc_gc_root_slot_count_from_map','pcc_gc_root_map_is_borrowed'})
        self.load('freestanding_gc_frame_registry.py', prefix='pcc_gc_local_frame')
        self.load('freestanding_gc_frame_registry.py', prefix='pcc_gc_frame_', names={
            'pcc_gc_frame_roots_disabled_fast','pcc_gc_should_track_frame_roots',
            'pcc_gc_note_frame_enter_local_checked'}, only_local=True)
        self.load('freestanding_thread_kernel_pthread.py', {
            'pcc_thread_local_root_arena_get','pcc_thread_local_root_arena_set',
            'pcc_thread_visit_native_root_frames','pcc_thread_native_root_contains',
            'pcc_thread_local_root_slots_stopped','_thread_record_unlink'})
        self.ns['pcc_gc_frame_slots_walked'] = lambda slots: 0
        self.allocations = 0

    def load(self, filename, names=(), prefix='', only_local=False):
        path = PY / filename
        chosen = set(names)
        if prefix:
            tree = ast.parse(path.read_text())
            chosen.update(
                node.name for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name.startswith(prefix)
                and (not only_local or 'local' in node.name)
            )
        load_functions(path, chosen, self.ns)

    def get_global(self, name):
        if name == 'pcc_tls_thread_record_py':
            return self.globals.get(name, self.records[self.thread])
        return self.globals.get(name,0)

    def read(self, base, offset=0):
        if isinstance(base,str): return self.globals.get(base,0)
        return self.memory.get(base+offset,0)

    def write(self, base, offset, value):
        if isinstance(base,str): self.globals[base]=value
        else: self.memory[base+offset]=value

    def allocate(self, size):
        self.allocations += 1
        if self.fail_at and self.allocations == self.fail_at: return 0
        result=self.next; self.next += size + 32
        return result

    def zero(self, base, value, size):
        for offset in range(0,size,8): self.write(base,offset,value)

    def abort(self): raise RuntimeError('abort')
    def enter_guard(self): self.depth += 1
    def exit_guard(self):
        assert self.depth > 0
        self.depth -= 1

    def call(self, name, *args): return self.ns[name](*args)
    def arena(self, owner=None): return self.read(self.records[owner or self.thread],72)
    def map(self, count):
        result=self.next;self.next+=32;self.write(result,0,count);return result
    def slots(self, *values):
        result=self.next;self.next+=len(values)*8+32
        for index,value in enumerate(values):self.write(result,index*8,value)
        return result
    def enter(self, slots, count=1, kind=0):
        self.call('pcc_gc_frame_enter_local_mode',self.map(count),slots,kind)
    def visit(self, visitor):
        self.stopped=1
        try:return self.call('pcc_thread_visit_native_root_frames',visitor,0)
        finally:self.stopped=0
    def active(self):
        result=[];node=self.read(self.arena(),16)
        while node:
            result.append((node,self.read(node,40),self.read(node,48),self.read(node,64)))
            node=self.read(node)
        return result


def test_local_duplicates_and_non_lifo_removal_preserve_kinds():
    memory=Arena(); left=memory.slots(11,12); right=memory.slots(13)
    memory.enter(left,1); memory.enter(right,-1); memory.enter(left,2)
    memory.enter(left,-1,2)
    assert [row[2:] for row in memory.active()]==[(-1,2),(2,0),(-1,0),(1,0)]
    memory.call('pcc_gc_frame_leave_local',left)
    assert [row[2:] for row in memory.active()]==[(-1,2),(-1,0),(1,0)]
    memory.call('pcc_gc_frame_leave_local',right)
    memory.call('pcc_gc_frame_leave_lifo_local',left)
    assert memory.active()[0][2:]==(1,0)
    memory.call('pcc_gc_frame_leave_local',left)
    assert memory.active()==[]


def test_chunks_reuse_and_checked_duplicate_failure_are_local():
    memory=Arena(); cells=[memory.slots(index) for index in range(257)]
    for cell in cells:memory.enter(cell)
    assert memory.allocations==3  # arena plus two256-entry chunks
    memory.depth=1
    assert memory.call('pcc_gc_note_frame_enter_local_checked',memory.map(1),cells[0])==0
    assert len(memory.active())==257
    memory.depth=0
    for cell in cells:memory.call('pcc_gc_frame_leave_local',cell)
    allocations=memory.allocations
    for cell in cells:memory.enter(cell)
    assert memory.allocations==allocations


@pytest.mark.parametrize('failure',(1,2))
def test_first_arena_allocation_failure_publishes_nothing(failure):
    memory=Arena();memory.depth=1;memory.fail_at=failure
    assert memory.call('pcc_gc_note_frame_enter_local_checked',memory.map(1),memory.slots(11))==0
    assert memory.arena()==0 and memory.depth==1
    assert len(memory.freed)==(1 if failure==2 else 0)


def test_gc0_disabled_entry_stays_allocation_and_guard_free():
    memory=Arena();memory.globals['pcc_gc_backend_selected']=0
    slots=memory.slots(11)
    for kind in (0,2):
        memory.enter(slots,1,kind)
        memory.call('pcc_gc_frame_leave_local_mode',slots,kind)
    assert memory.arena()==memory.allocations==memory.depth==0


def test_stopped_visitor_rewrites_other_parked_owner_exact_cells():
    memory=Arena();first=memory.slots(11);memory.enter(first)
    memory.thread=2;second=memory.slots(12,13);memory.enter(second,-2)
    memory.write(memory.records[2],16,2);memory.thread=1
    seen=[]
    def rewrite(frame, context):
        ranges=memory.read(frame,8);slots=memory.read(ranges);count=abs(memory.read(ranges,8))
        for index in range(count):
            seen.append(slots+8*index);memory.write(slots,index*8,memory.read(slots,index*8)+100)
    assert memory.visit(rewrite)==2
    assert set(seen)=={first,second,second+8}
    assert [memory.read(slot) for slot in (first,second,second+8)]==[111,112,113]


def test_visitor_defers_reuse_of_non_lifo_retired_next_entry():
    memory=Arena();old=memory.slots(11);top=memory.slots(12);fresh=memory.slots(13)
    memory.enter(old); old_entry=memory.active()[0][0];memory.enter(top)
    seen=[]
    def mutate(frame, context):
        seen.append(memory.read(frame,40))
        if memory.read(frame,40)==top:
            memory.call('pcc_gc_frame_leave_local',old)
            memory.enter(fresh)
            assert memory.active()[0][0]!=old_entry
            memory.call('pcc_gc_frame_leave_local',fresh)
    memory.visit(mutate)
    assert seen==[top]
    assert memory.read(memory.arena(),32)==0 and memory.read(memory.arena(),40)==0


def test_gc3_same_cell_owned_role_precedes_borrowed_healing():
    memory=Arena()
    source=memory.slots(2,0,0); target=memory.slots(1,0,0)
    memory.write(source,12,128); memory.write(target,12,256)
    cell=memory.slots(source)
    memory.enter(cell,1);memory.enter(cell,-1)
    memory.ns.update({
        'pcc_gc_generational_pointer_can_have_header':lambda value:int(value in (source,target)),
        'pcc_gc_object_is_known_no_lock':lambda value:int(value in (source,target)),
        'pcc_gc_forwarding_find':lambda value:0,
        'pcc_gc_generational_oldify_copy':lambda value:target if value==source else 0,
        'pcc_gc_trace_referents_for_promotion':lambda value:None,
        'py_incref':lambda value:memory.write(value,0,memory.read(value)+1),
        'py_decref':lambda value:memory.write(value,0,memory.read(value)-1),
    })
    memory.load('freestanding_gc_generational_promotion.py',{'pcc_gc_generational_promote_owned_slot_mode','pcc_gc_generational_promote_borrowed_slot_mode','pcc_gc_promote_cached_frame_slot'})
    memory.load('freestanding_gc_mapped_roots.py',{'pcc_gc_visit_mapped_root_slot','pcc_gc_visit_mapped_root_slots','pcc_gc_visit_one_native_root_frame'})
    # Constants for the same40-byte descriptor and24-byte visit context.
    memory.ns.update(_NATIVE_FRAME_RANGES=8,_NATIVE_FRAME_USED=16,_NATIVE_RANGE_BYTES=16,
                     _NATIVE_VISIT_MODE=0,_NATIVE_VISIT_RESOLVE=8,_NATIVE_VISIT_RESULT=16)
    context=memory.slots(2,0,0)
    memory.visit(lambda frame,_context:memory.call('pcc_gc_visit_one_native_root_frame',frame,context))
    assert memory.read(cell)==target and memory.read(source)==1 and memory.read(target)==2


def test_thread_exit_rejects_live_private_roots_and_frees_empty_arena():
    memory=Arena();cell=memory.slots(11);memory.enter(cell)
    with pytest.raises(RuntimeError,match='abort'):memory.call('_thread_record_unlink')
    assert memory.arena()!=0
    memory.call('pcc_gc_frame_leave_local',cell)
    arena=memory.arena();chunk=memory.read(arena)
    memory.call('_thread_record_unlink')
    assert {arena,chunk,memory.records[1]}<=set(memory.freed)


def test_unchecked_legacy_bodies_keep_their_global_index_contract():
    source=ast.parse((PY/'freestanding_gc_frame_registry.py').read_text())
    functions={node.name:node for node in source.body if isinstance(node,ast.FunctionDef)}
    for name in ('pcc_gc_note_frame_enter','pcc_gc_note_frame_enter_lifo','pcc_gc_note_frame_leave','pcc_gc_note_frame_leave_lifo','pcc_gc_note_frame_enter_checked'):
        calls={node.func.id for node in ast.walk(functions[name]) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)}
        assert not any('local_frame' in symbol or 'arena' in symbol for symbol in calls)
        assert 'pcc_py_gc_minor_graph_lock' in calls



def test_compiler_private_scope_reuses_exact_allocation_and_alias_identity():
    from tests.python.test_private_frame_scopes import host, rooted, frame_map, PTR
    from pcc.frontends.python.codegen.private_frame_scopes import private_frame_scope
    from pcc.frontends.python.codegen.ownership_lowering import OwnershipLoweringMixin
    from pcc.ir.compat import ir
    h=host();slot,alias=rooted(h)
    h._precise_stack_frames_mapped=False
    assert not private_frame_scope(h,'pcc_gc_frame_leave',alias)
    assert OwnershipLoweringMixin._gc_frame_entry(h,'pcc_gc_frame_enter',alias,frame_map(h,1))=='pcc_gc_frame_enter_local'
    assert OwnershipLoweringMixin._gc_frame_entry(h,'pcc_gc_frame_leave',slot)=='pcc_gc_frame_leave_local'
    assert not private_frame_scope(h,'pcc_gc_frame_leave_lifo',slot)
    global_slot=ir.GlobalVariable(h.module,PTR,'persistent')
    assert not private_frame_scope(h,'pcc_gc_frame_enter',global_slot)
    assert not private_frame_scope(h,'pcc_gc_frame_enter',h.current_function.args[0])
    h._generator_ctx_stack=[{'resume_function':h.current_function}]
    assert not private_frame_scope(h,'pcc_gc_frame_enter',slot)  # no explicit map evidence


def test_unmapped_local_protocol_is_not_elided_by_walked_frame_marker():
    from pcc.backend.self_backend_aarch64_gc_calls import _WALKED_FRAME_CALLS
    from pcc.backend.self_backend_call_flags import classify_call_flags, CALL_FLAG_FRAME_ENTER, CALL_FLAG_FRAME_LEAVE
    for kind in ('','_lifo'):
        enter='pcc_gc_frame_enter'+kind+'_local'
        leave='pcc_gc_frame_leave'+kind+'_local'
        assert enter not in _WALKED_FRAME_CALLS and leave not in _WALKED_FRAME_CALLS
        assert enter+'_mapped' in _WALKED_FRAME_CALLS and leave+'_mapped' in _WALKED_FRAME_CALLS
        assert classify_call_flags(enter,False,False)&CALL_FLAG_FRAME_ENTER
        assert classify_call_flags(leave,False,False)&CALL_FLAG_FRAME_LEAVE


def test_private_frame_protocol_has_real_stackmap_locations_and_reload():
    from tests.python.test_precise_stackmap_abi import _stale_managed_ssa_ir
    from pcc.backend.self_backend_prepare import prepare_module_for_target
    from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
    source=_stale_managed_ssa_ir('arm64-apple-darwin').replace('pcc_gc_frame_enter(', 'pcc_gc_frame_enter_local(').replace('pcc_gc_frame_leave(', 'pcc_gc_frame_leave_local(')
    prepared=prepare_module_for_target(source,aggregate_returned_indirect=lambda ty:False)
    plans=build_stack_map_plans(prepared.functions,prepared.globals_,target='aarch64-darwin')
    try:
        records=plans[0].diagnostic_records()
        assert any(record.locations and record.reloads for record in records)
        assert all(reload.derived_offset==24 for record in records for reload in record.reloads)
    finally:
        for plan in plans:
            if plan.packed_records is not None:plan.packed_records.close()



def test_checked_raw_adapter_pair_is_not_unconditional_stackmap_protocol():
    from pcc.backend.self_backend_call_flags import classify_call_flags, CALL_FLAG_FRAME_PROTOCOL
    for symbol in ('pcc_gc_note_frame_enter_local_checked','pcc_gc_note_frame_leave_local'):
        assert classify_call_flags(symbol,False,False)&CALL_FLAG_FRAME_PROTOCOL==0


@pytest.mark.parametrize(('target_triple', 'suffix'), [
    ('x86_64-unknown-linux-gnu', '_local'),
    ('arm64-apple-darwin', '_local_mapped'),
], ids=['linux-x64', 'darwin-arm64'])
def test_real_library_frontend_selects_local_frame_scope(
    tmp_path, monkeypatch, target_triple, suffix,
):
    from pcc.frontends.python.pipeline import compile_python

    # Test the target's default protocol, independent of the test host and
    # environment override. This is IR cross-generation, not Darwin execution.
    monkeypatch.delenv('PCC_PRECISE_STACKMAP_TABLES', raising=False)
    source = tmp_path / 'private_scope.py'
    source.write_text('def keep(value: list) -> list:\n    result = [value]\n    return result\n')
    output = source.with_suffix('.ll')
    compile_python(
        str(source), str(output), backend='self', python_library=True,
        emit_llvm_only=True, ir_scaffold_mode='on', target_triple=target_triple,
    )
    text = output.read_text()
    definitions = text[text.index('define '):]
    assert '@pcc_gc_frame_enter' + suffix + '(' in definitions
    assert '@pcc_gc_frame_leave' + suffix + '(' in definitions
