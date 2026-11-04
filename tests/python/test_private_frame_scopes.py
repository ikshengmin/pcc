"""Compiler-owned cell origin, exact maps, and paired private lifetimes.

Specification: docs/compiler-contract.md; shared GC slot/root ownership.
"""
from types import SimpleNamespace
import pytest
from pcc.ir.compat import ir
from pcc.frontends.python.codegen.runtime_abi import declare_runtime
from pcc.frontends.python.codegen.ownership_lowering import OwnershipLoweringMixin
from pcc.frontends.python.codegen.private_frame_scopes import (
    private_frame_scope, record_private_root_address, record_root_pointer_alias,
)
PTR=ir.IntType(8).as_pointer()

def host():
    module=ir.Module('frame_proof');fn=ir.Function(module,ir.FunctionType(ir.VoidType(),[PTR]),'probe')
    builder=ir.IRBuilder(fn.append_basic_block('entry'))
    h=SimpleNamespace(module=module,current_function=fn,builder=builder,runtime=declare_runtime(module),
        _freestanding_module=False,_private_root_addresses={},_private_root_pointer_aliases={},
        _private_frame_scopes={},_precise_stack_frames_mapped=True,_python_library=True,
        _runtime_port_module=False,_generator_ctx_stack=[],_function_root_groups={},
        _fn_gc_root_slot_registry={fn.name:[]})
    h._mark_walked_frames=lambda:None
    return h

def rooted(h, capacity=1):
    ty=PTR if capacity==1 else ir.ArrayType(PTR,capacity)
    slot=h.builder.alloca(ty,name='owner')
    record_private_root_address(h,slot,capacity)
    h._fn_gc_root_slot_registry[h.current_function.name].append(('owner',slot))
    alias=h.builder.bitcast(slot,PTR);record_root_pointer_alias(h,alias,slot)
    return slot,alias

def frame_map(h,count,constant=True,width=32):
    ty=ir.IntType(width);name='map'+str(len(h.module.globals))
    value=ir.GlobalVariable(h.module,ty,name);value.global_constant=constant
    value.initializer=ir.Constant(ty,count)
    return value

def route(h,name,slot,map_value=None):
    return OwnershipLoweringMixin._gc_frame_entry(h,name,slot,map_value)

@pytest.mark.parametrize('lifo',(False,True))
@pytest.mark.parametrize('count',(1,-1,4,-4))
def test_resume_scalar_lexical_and_group_cells_use_proven_map(lifo,count):
    h=host();slot,alias=rooted(h,abs(count))
    h._generator_ctx_stack=[{'resume_function':h.current_function,'operand_root_group_members':{}}]
    h._async_body_depth=1
    middle='_lifo' if lifo else ''
    assert route(h,'pcc_gc_frame_enter'+middle,alias,frame_map(h,count))=='pcc_gc_frame_enter'+middle+'_local_mapped'
    h._generator_ctx_stack=[];h._async_body_depth=0
    assert route(h,'pcc_gc_frame_leave'+middle,slot)=='pcc_gc_frame_leave'+middle+'_local_mapped'

@pytest.mark.parametrize('case',('missing','zero','overflow','oversized','mutable','width','foreign_map','raw_capacity','heap','global','foreign_function'))
def test_unproven_or_out_of_bounds_registration_stays_legacy(case):
    h=host();slot,_=rooted(h);mapping=frame_map(h,1)
    if case=='missing':mapping=None
    elif case=='zero':mapping=frame_map(h,0)
    elif case=='overflow':mapping=frame_map(h,-2147483648)
    elif case=='oversized':mapping=frame_map(h,2)
    elif case=='mutable':mapping=frame_map(h,1,False)
    elif case=='width':mapping=frame_map(h,1,True,64)
    elif case=='foreign_map':mapping=frame_map(host(),1)
    elif case=='raw_capacity':record_private_root_address(h,slot,0)
    elif case=='heap':slot=h.current_function.args[0]
    elif case=='global':slot=ir.GlobalVariable(h.module,PTR,'shared')
    elif case=='foreign_function':h.current_function=ir.Function(h.module,ir.FunctionType(ir.VoidType(),[]),'other')
    assert not private_frame_scope(h,'pcc_gc_frame_enter',slot,mapping)
    assert not private_frame_scope(h,'pcc_gc_frame_leave',slot)

def test_hoisted_entry_does_not_change_route_after_resume_context_push():
    h=host();slot,_=rooted(h)
    assert route(h,'pcc_gc_frame_enter',slot,frame_map(h,-1))=='pcc_gc_frame_enter_local_mapped'
    h._generator_ctx_stack=[{'resume_function':h.current_function}]
    assert route(h,'pcc_gc_frame_leave',slot)=='pcc_gc_frame_leave_local_mapped'
    assert route(h,'pcc_gc_frame_leave_lifo',slot)=='pcc_gc_frame_leave_lifo'
