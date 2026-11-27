"""Opt-in typed x64 MIR admission and machine-register interference."""
from dataclasses import replace

from pcc.backend import self_backend_x86_64_linux as x64
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_prepare import prepare_parsed_module_for_target
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_precise_stackmaps import (
    PlannedManagedReload, PlannedRootLocation, PlannedSafepoint,
    build_function_stack_map_plan,
)

TRIPLE = 'target triple = "x86_64-unknown-linux-gnu"\n'
CALL_LOOP = TRIPLE + """declare i64 @callee(i64)
define i64 @work(i64 %a, i64 %n) {
entry:
  %x = add i64 %a, 3
  %r = call i64 @callee(i64 %x)
  %y = xor i64 %r, %x
  br label %loop
loop:
  %v = phi i64 [ %y, %entry ], [ %w, %body ]
  %i = phi i64 [ 0, %entry ], [ %j, %body ]
  %done = icmp eq i64 %i, %n
  br i1 %done, label %exit, label %body
body:
  %w = add i64 %v, %x
  %j = add i64 %i, 1
  br label %loop
exit:
  ret i64 %v
}
"""


def _prepare(text):
    module = prepare_parsed_module_for_target(
        parse_self_backend_module(text),
        aggregate_returned_indirect=x64._aggregate_returned_indirect,
    )
    func = module.functions[0]
    func.platform_frame_extra = 16 + x64.CALLEE_SAVED_AREA_SIZE
    plan = build_function_stack_map_plan(func, module.globals_, target="x86_64-linux")
    return func, plan


def test_mir_call_constraints_and_phi_edges():
    from pcc.backend.self_backend_x86_mir import build_x86_mir, allocate_x86_mir, CALLER_REGISTERS
    func, plan = _prepare(CALL_LOOP)
    kernel = get_indexed_function_kernel(func)
    mir = build_x86_mir(func, plan)
    assert mir is not None
    allocation = allocate_x86_mir(mir)
    x = kernel.value_id("x")
    n = kernel.value_id("n")
    r = kernel.value_id("r")
    assert allocation.registers[x] not in CALLER_REGISTERS
    assert allocation.registers[n] not in CALLER_REGISTERS
    assert allocation.registers[x] != allocation.registers[n]
    assert allocation.registers[r] in CALLER_REGISTERS
    loop = kernel.block_id("loop")
    body = kernel.block_id("body")
    assert kernel.value_id("w") in mir.edge_uses[(body, loop)]
    assert kernel.value_id("j") in mir.edge_uses[(body, loop)]
    assert kernel.value_id("v") in mir.blocks[loop].phi_defs


def test_mir_pins_every_mapped_and_reload_home():
    from pcc.backend.self_backend_x86_mir import build_x86_mir, allocate_x86_mir
    text = TRIPLE + """define ptr @f(ptr %p, ptr %q) {
entry:
  %x = getelementptr i8, ptr %p, i64 8
  %y = load ptr, ptr %x
  %z = select i1 true, ptr %q, ptr %y
  ret ptr %z
}
"""
    func, plan = _prepare(text)
    kernel = get_indexed_function_kernel(func)
    x = kernel.value_id("x")
    y = kernel.value_id("y")
    z = kernel.value_id("z")
    # Exact root/reload cells may be mutated by their explicit protocol;
    # neither sources nor destinations can be silently register-only.
    record = PlannedSafepoint(0, ".Ltest", 0,
        (PlannedRootLocation(-kernel.value_slot_offset(x), True),),
        reloads=(PlannedManagedReload(-kernel.value_slot_offset(y), -kernel.value_slot_offset(z)),))
    root_plan = replace(plan, records=(record,))
    mir = build_x86_mir(func, root_plan)
    assert mir is not None
    assert {x, y, z} <= set(mir.pinned)
    allocation = allocate_x86_mir(mir)
    assert all(allocation.registers[value] == -1 for value in (x, y, z))


def test_mir_unsupported_function_falls_back_whole():
    from pcc.backend.self_backend_x86_mir import build_x86_mir
    text = TRIPLE + """define i64 @f(i64 %a, ...) {
entry:
  ret i64 %a
}
"""
    func, plan = _prepare(text)
    assert build_x86_mir(func, plan) is None


def test_mir_fixed_shift_and_division_clobbers():
    from pcc.backend.self_backend_x86_mir import build_x86_mir, allocate_x86_mir
    text = TRIPLE + """define i64 @f(i64 %a, i64 %b, i64 %c) {
entry:
  %shift = shl i64 %a, %b
  %q = udiv i64 %shift, %b
  %r = add i64 %q, %c
  ret i64 %r
}
"""
    func, plan = _prepare(text)
    kernel = get_indexed_function_kernel(func)
    mir = build_x86_mir(func, plan)
    assert mir is not None
    allocation = allocate_x86_mir(mir)
    # c is live through CL and RAX/RDX fixed-register operations.
    assert allocation.registers[kernel.value_id("c")] not in (0, 1, 2)


def test_mir_owned_native_call_phi_and_flag_branch(monkeypatch, tmp_path):
    import platform
    import subprocess
    import sys
    import pytest
    if sys.platform!="linux" or platform.machine() not in ("x86_64","AMD64"):
        pytest.skip("native Linux x86-64 required")
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    start=""" .intel_syntax noprefix
.text
.globl _start
_start:
  mov r12, 101
  mov r13, 102
  mov r14, 103
  mov r15, 104
  mov rbx, 105
  mov rdi, 9
  mov rsi, 3
  call work
  cmp rax, 36
  jne .Lbad
  cmp r12, 101
  jne .Lbad
  cmp r13, 102
  jne .Lbad
  cmp r14, 103
  jne .Lbad
  cmp r15, 104
  jne .Lbad
  cmp rbx, 105
  jne .Lbad
  xor edi, edi
  jmp .Lexit
.Lbad:
  mov edi, 1
.Lexit:
  mov eax, 60
  syscall
.globl callee
callee:
  mov rax, rdi
  mov rcx, 7
  mov rdx, 8
  mov r8, 9
  mov r9, 10
  ret
"""
    outputs=[]
    for flag,compact in (("0","0"),("1","0"),("1","1")):
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR",flag)
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT",compact)
        assembly=x64.emit_x86_64_linux_asm(CALL_LOOP)
        outputs.append(assembly)
        exe=tmp_path/("mir-"+flag+compact)
        exe.write_bytes(link_static_executable([assemble_file(start),assemble_file(assembly)]))
        exe.chmod(0o755)
        result=subprocess.run([str(exe)],capture_output=True,timeout=10)
        assert (result.returncode,result.stdout,result.stderr)==(0,b"",b"")
    assert outputs[0]!=outputs[1]
    assert outputs[1]!=outputs[2]
    assert "  sete " not in outputs[1]


def test_mir_unsupported_fallback_default_exact(monkeypatch):
    text=TRIPLE+"""define i64 @f(i64 %a, ...) {
entry:
  ret i64 %a
}
"""
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR","0")
    baseline=x64.emit_x86_64_linux_asm(text)
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR","1")
    assert x64.emit_x86_64_linux_asm(text)==baseline


def test_mir_owned_native_parallel_swap_stack_args_and_fixed_regs(monkeypatch,tmp_path):
    import platform
    import subprocess
    import sys
    import pytest
    if sys.platform!="linux" or platform.machine() not in ("x86_64","AMD64"):
        pytest.skip("native Linux x86-64 required")
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    source=TRIPLE+"""declare i64 @sum8(i64,i64,i64,i64,i64,i64,i64,i64)
define i64 @swap(i64 %n) {
entry:
  br label %loop
loop:
  %a = phi i64 [ 1, %entry ], [ %b, %body ]
  %b = phi i64 [ 2, %entry ], [ %a, %body ]
  %i = phi i64 [ 0, %entry ], [ %j, %body ]
  %done = icmp eq i64 %i, %n
  br i1 %done, label %exit, label %body
body:
  %j = add i64 %i, 1
  br label %loop
exit:
  %x = sub i64 %a, %b
  ret i64 %x
}
define i64 @fixed(i64 %a,i64 %b,i64 %c) {
entry:
  %x = shl i64 %a,%b
  %q = udiv i64 %x,%b
  %r = add i64 %q,%c
  ret i64 %r
}
define i64 @stackargs(i64 %a,i64 %b,i64 %c,i64 %d,i64 %e,i64 %f,i64 %g,i64 %h) {
entry:
  %x = call i64 @sum8(i64 %h,i64 %g,i64 %f,i64 %e,i64 %d,i64 %c,i64 %b,i64 %a)
  %r = sub i64 %x,%a
  ret i64 %r
}
"""
    start=""" .intel_syntax noprefix
.text
.globl _start
_start:
  mov rdi,3
  call swap
  cmp rax,1
  jne .Lbad
  mov rdi,4
  call swap
  cmp rax,-1
  jne .Lbad
  mov rdi,10
  mov rsi,3
  mov rdx,7
  call fixed
  cmp rax,33
  jne .Lbad
  mov rdi,1
  mov rsi,2
  mov rdx,3
  mov rcx,4
  mov r8,5
  mov r9,6
  sub rsp,16
  mov QWORD PTR [rsp],7
  mov QWORD PTR [rsp + 8],8
  call stackargs
  add rsp,16
  cmp rax,35
  jne .Lbad
  xor edi,edi
  jmp .Lexit
.Lbad:
  mov edi,1
.Lexit:
  mov eax,60
  syscall
.globl sum8
sum8:
  mov rax,rdi
  add rax,rsi
  add rax,rdx
  add rax,rcx
  add rax,r8
  add rax,r9
  mov r10,QWORD PTR [rsp + 8]
  add rax,r10
  mov r10,QWORD PTR [rsp + 16]
  add rax,r10
  ret
"""
    assemblies=[]
    for flag,compact in (("0","0"),("1","0"),("1","1")):
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR",flag)
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT",compact)
        assembly=x64.emit_x86_64_linux_asm(source);assemblies.append(assembly)
        exe=tmp_path/("mir-fixed-"+flag+compact)
        exe.write_bytes(link_static_executable([assemble_file(start),assemble_file(assembly)]))
        exe.chmod(0o755)
        result=subprocess.run([str(exe)],capture_output=True,timeout=10)
        assert (result.returncode,result.stdout,result.stderr)==(0,b"",b"")
    assert assemblies[0]!=assemblies[1]


def test_mir_cas_pair_admission_and_escape_fallback():
    from pcc.backend.self_backend_x86_mir import build_x86_mir, allocate_x86_mir
    text=TRIPLE+"""define i64 @cas(ptr %p,i64 %old,i64 %new) {
entry:
  %pair = cmpxchg ptr %p, i64 %old, i64 %new acq_rel acquire
  %value = extractvalue { i64, i1 } %pair, 0
  %success = extractvalue { i64, i1 } %pair, 1
  %flag = zext i1 %success to i64
  %result = add i64 %value, %flag
  ret i64 %result
}
"""
    func,plan=_prepare(text)
    mir=build_x86_mir(func,plan)
    assert mir is not None
    kernel=get_indexed_function_kernel(func)
    pair=kernel.value_id("pair")
    assert pair in mir.pinned
    assert set(mir.cas_extracts.values())=={(pair,0),(pair,1)}
    allocation=allocate_x86_mir(mir)
    assert allocation.registers[pair]==-1
    # A pair moved through a phi cannot use the ephemeral RAX/FLAGS result.
    escape=TRIPLE+"""define i64 @escape(ptr %p,i64 %old,i64 %new) {
entry:
  %pair = cmpxchg ptr %p, i64 %old, i64 %new acq_rel acquire
  br label %next
next:
  %value = extractvalue { i64, i1 } %pair, 0
  ret i64 %value
}
"""
    f,p=_prepare(escape)
    assert build_x86_mir(f,p) is None


def test_mir_owned_native_cas_old_success_and_memset(monkeypatch,tmp_path):
    import platform,subprocess,sys
    import pytest
    if sys.platform!="linux" or platform.machine() not in ("x86_64","AMD64"):
        pytest.skip("native Linux x86-64 required")
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    source=TRIPLE+"""declare void @llvm.memset.p0.i64(ptr,i8,i64,i1)
define i64 @cas(ptr %p,i64 %old,i64 %new) {
entry:
  %pair = cmpxchg ptr %p, i64 %old, i64 %new acq_rel acquire
  %value = extractvalue { i64, i1 } %pair, 0
  %success = extractvalue { i64, i1 } %pair, 1
  %flag = zext i1 %success to i64
  %result = add i64 %value, %flag
  ret i64 %result
}
define void @fill(ptr %p,i64 %n,i8 %v) {
entry:
  call void @llvm.memset.p0.i64(ptr %p,i8 %v,i64 %n,i1 0)
  ret void
}
define void @clear(ptr %p) {
entry:
  call void @llvm.memset.p0.i64(ptr %p,i8 0,i64 16,i1 0)
  ret void
}
"""
    start=""" .intel_syntax noprefix
.text
.globl _start
_start:
  lea rdi,cell[rip]
  mov rsi,10
  mov rdx,20
  call cas
  cmp rax,11
  jne .Lbad
  lea rdi,cell[rip]
  mov rsi,10
  mov rdx,30
  call cas
  cmp rax,20
  jne .Lbad
  mov rax,QWORD PTR cell[rip]
  cmp rax,20
  jne .Lbad
  lea rdi,buffer[rip]
  mov rsi,5
  mov edx,7
  call fill
  lea r11,buffer[rip]
  movzx eax,BYTE PTR [r11 + 4]
  cmp eax,7
  jne .Lbad
  movzx eax,BYTE PTR [r11 + 5]
  cmp eax,9
  jne .Lbad
  lea rdi,buffer[rip]
  call clear
  lea r11,buffer[rip]
  mov rax,QWORD PTR [r11]
  or rax,QWORD PTR [r11 + 8]
  cmp rax,0
  jne .Lbad
  xor edi,edi
  jmp .Lexit
.Lbad:
  mov edi,1
.Lexit:
  mov eax,60
  syscall
.data
.p2align 3
cell:
  .quad 10
buffer:
  .byte 9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9
"""
    outputs=[]
    for flag,compact in (("0","0"),("1","0"),("1","1")):
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR",flag)
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT",compact)
        assembly=x64.emit_x86_64_linux_asm(source);outputs.append(assembly)
        exe=tmp_path/("mir-cas-"+flag+compact)
        exe.write_bytes(link_static_executable([assemble_file(start),assemble_file(assembly)]))
        exe.chmod(0o755)
        result=subprocess.run([str(exe)],capture_output=True,timeout=10)
        assert (result.returncode,result.stdout,result.stderr)==(0,b"",b"")
    assert outputs[0]!=outputs[1]


def test_mir_compact_preserves_pinned_cells_and_restores_input():
    from pcc.backend.self_backend_x86_mir import build_x86_mir,allocate_x86_mir
    from pcc.backend.self_backend_x86_mir_frame import compact_x86_mir_frame
    text=TRIPLE+"""define ptr @f(ptr %p,i64 %a) {
entry:
  %x = add i64 %a, 4
  %y = xor i64 %x, %a
  %q = getelementptr i8, ptr %p, i64 %y
  %r = load ptr, ptr %q
  ret ptr %r
}
"""
    func,plan=_prepare(text)
    kernel=get_indexed_function_kernel(func)
    q=kernel.value_id("q");r=kernel.value_id("r")
    q_offset=kernel.value_slot_offset(q);r_offset=kernel.value_slot_offset(r)
    record=PlannedSafepoint(0,".Lroot",0,
        (PlannedRootLocation(-q_offset,True),),
        reloads=(PlannedManagedReload(-q_offset,-r_offset),))
    rooted=replace(plan,records=(record,))
    mir=build_x86_mir(func,rooted)
    assert mir is not None
    allocation=allocate_x86_mir(mir)
    assert allocation.registers[q]==allocation.registers[r]==-1
    old_size=func.frame_size
    old_slots={name:slot.offset for name,slot in func.value_slots.items()}
    layout=compact_x86_mir_frame(func,rooted,mir,allocation)
    try:
        assert func.frame_size<old_size
        assert kernel.value_slot_offset(q)==layout.remap(q_offset)
        assert kernel.value_slot_offset(r)==layout.remap(r_offset)
        assert 8<=kernel.value_slot_offset(q)<=func.frame_size
        assert 8<=kernel.value_slot_offset(r)<=func.frame_size
    finally:
        layout.restore(func)
    assert func.frame_size==old_size
    assert {name:slot.offset for name,slot in func.value_slots.items()}==old_slots


def test_mir_compact_native_root_reload_callback_exception(monkeypatch,tmp_path,precise_stackmap_tables):
    import platform,subprocess,sys
    import pytest
    if sys.platform!="linux" or platform.machine() not in ("x86_64","AMD64"):
        pytest.skip("native Linux x86-64 required")
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.backend.target_objects import emit_indexed_assembly,encode_assembly_object
    from tests.python.test_x86_64_managed_phi_liveness import ROOTED_LOOP
    start=""" .intel_syntax noprefix
.text
.globl _start
_start:
  lea rdi,original[rip]
  mov esi,1
  xor edx,edx
  xor ecx,ecx
  call rooted_phi
  cmp rax,1
  jne .Lbad
  lea rdi,original[rip]
  xor esi,esi
  xor edx,edx
  xor ecx,ecx
  call rooted_phi
  cmp rax,2
  jne .Lbad
  mov QWORD PTR error_mode[rip],1
  lea rdi,original[rip]
  mov esi,1
  xor edx,edx
  xor ecx,ecx
  call rooted_phi
  cmp rax,-1
  jne .Lbad
  mov rax,QWORD PTR bad[rip]
  or rax,QWORD PTR current_root[rip]
  cmp rax,0
  jne .Lbad
  xor edi,edi
  jmp .Lexit
.Lbad:
  mov edi,1
.Lexit:
  mov eax,60
  syscall
.globl pcc_gc_frame_enter
pcc_gc_frame_enter:
  mov QWORD PTR current_root[rip],rsi
  ret
.globl pcc_thread_safepoint
pcc_thread_safepoint:
  mov r10,QWORD PTR current_root[rip]
  lea r11,target[rip]
  mov QWORD PTR [r10],r11
  mov rax,42
  mov rcx,43
  mov rdx,44
  mov rsi,45
  mov rdi,46
  mov r8,47
  mov r9,48
  ret
.globl py_err_occurred
py_err_occurred:
  mov rax,QWORD PTR error_mode[rip]
  ret
.globl opaque_call
opaque_call:
  lea r10,target[rip]
  add r10,24
  cmp rdi,r10
  je .Lopaque_good
  mov QWORD PTR bad[rip],1
.Lopaque_good:
  ret
.globl pcc_gc_frame_leave
pcc_gc_frame_leave:
  cmp rdi,QWORD PTR current_root[rip]
  je .Lleave_good
  mov QWORD PTR bad[rip],1
.Lleave_good:
  mov QWORD PTR current_root[rip],0
  ret
.data
.p2align 3
current_root:
  .quad 0
error_mode:
  .quad 0
bad:
  .quad 0
original:
  .zero 64
target:
  .zero 64
"""
    sizes=[];assemblies=[];record_counts=[]
    for flag,compact in (("0","0"),("1","0"),("1","1")):
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR",flag)
        monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT",compact)
        plans=[]
        assembly=emit_indexed_assembly(parse_self_backend_module(ROOTED_LOOP),stack_map_plans_out=plans)
        assemblies.append(assembly)
        assert len(plans)==1
        sizes.append(plans[0].frame_size)
        records=plans[0].diagnostic_records()
        record_counts.append((len(records),sum(len(record.reloads) for record in records)))
        assert record_counts[-1][1]>0
        for record in records:
            for reload in record.reloads:
                assert -sizes[-1]<=reload.source_offset<0
                assert -sizes[-1]<=reload.destination_offset<0
        obj=encode_assembly_object(assembly,"x86_64-unknown-linux-gnu",stack_map_plans=plans)
        exe=tmp_path/("mir-root-"+flag+compact)
        exe.write_bytes(link_static_executable([assemble_file(start),parse_relocatable(obj)]))
        exe.chmod(0o755)
        result=subprocess.run([str(exe)],capture_output=True,timeout=10)
        assert (result.returncode,result.stdout,result.stderr)==(0,b"",b"")
    assert record_counts[0]==record_counts[1]==record_counts[2]
    assert assemblies[0]!=assemblies[1]!=assemblies[2]
    assert sizes[2]<sizes[1]==sizes[0]


def test_mir_pure_frame_plan_matches_mutating_stackmap_rebuild():
    from pcc.backend.self_backend_x86_mir import build_x86_mir,allocate_x86_mir
    from pcc.backend.self_backend_x86_mir_frame import (
        plan_x86_mir_frame,compact_x86_mir_frame,remap_plain_stack_map,
        stack_map_remap_is_exact,
    )
    from tests.python.test_x86_64_managed_phi_liveness import ROOTED_LOOP
    module=prepare_parsed_module_for_target(
        parse_self_backend_module(ROOTED_LOOP),
        aggregate_returned_indirect=x64._aggregate_returned_indirect,
    )
    func=module.functions[0];func.platform_frame_extra=16+x64.CALLEE_SAVED_AREA_SIZE
    old=build_function_stack_map_plan(func,module.globals_,target="x86_64-linux",identity_name=x64._asm_symbol(func.name))
    mir=build_x86_mir(func,old);assert mir is not None
    allocation=allocate_x86_mir(mir)
    original_size=func.frame_size
    original_slots={name:slot.offset for name,slot in func.value_slots.items()}
    layout=plan_x86_mir_frame(func,old,mir,allocation)
    direct=remap_plain_stack_map(old,layout)
    assert direct is not None and layout.new_size<original_size
    assert func.frame_size==original_size
    assert {name:slot.offset for name,slot in func.value_slots.items()}==original_slots
    oracle=compact_x86_mir_frame(func,old,mir,allocation)
    try:
        rebuilt=build_function_stack_map_plan(func,module.globals_,target="x86_64-linux",identity_name=x64._asm_symbol(func.name))
        assert direct==rebuilt
        assert stack_map_remap_is_exact(old,direct,oracle)
        assert sum(len(record.reloads) for record in direct.records)>0
    finally:
        oracle.restore(func)
    assert func.frame_size==original_size
    assert {name:slot.offset for name,slot in func.value_slots.items()}==original_slots


def test_mir_windows_and_raw_syscall_fallback_byte_exact(monkeypatch):
    from pcc.backend.self_backend_x86_64_windows import emit_x86_64_windows_asm
    from tests.python.test_unsafe_syscall6 import _build_syscall6_module
    linux=_build_syscall6_module()
    windows=TRIPLE.replace("x86_64-unknown-linux-gnu","x86_64-pc-windows-msvc")+"""define i64 @f(i64 %a,i64 %b) {
entry:
  %r = add i64 %a,%b
  ret i64 %r
}
"""
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR","0")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT","0")
    sys_base=x64.emit_x86_64_linux_asm(linux)
    win_base=emit_x86_64_windows_asm(windows)
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR","1")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT","1")
    assert x64.emit_x86_64_linux_asm(linux)==sys_base
    assert emit_x86_64_windows_asm(windows)==win_base

def test_mir_packed_adjacency_and_sparse_deaths_match_dense_oracle():
    from pcc.backend.self_backend_x86_mir import build_x86_mir, allocate_x86_mir
    from tests.python.test_x86_64_managed_phi_liveness import ROOTED_LOOP
    pointer_loop = TRIPLE + """declare ptr @next(ptr)
define ptr @pointers(ptr %p, ptr %q, i64 %n) {
entry:
  br label %loop
loop:
  %x = phi ptr [ %p, %entry ], [ %y, %body ]
  %i = phi i64 [ 0, %entry ], [ %j, %body ]
  %done = icmp eq i64 %i, %n
  br i1 %done, label %exit, label %body
body:
  %r = call ptr @next(ptr %x)
  %y = select i1 true, ptr %r, ptr %q
  %j = add i64 %i, 1
  br label %loop
exit:
  ret ptr %x
}
    """
    death_sites = 0
    no_death_sites = 0
    for text in (CALL_LOOP, ROOTED_LOOP, pointer_loop):
        func, plan = _prepare(text)
        mir = build_x86_mir(func, plan)
        assert mir is not None
        dense = allocate_x86_mir(mir)
        packed = allocate_x86_mir(mir, force_packed=True)
        assert packed.registers == dense.registers
        assert packed.interference == dense.interference
        assert packed.allowed == dense.allowed
        assert packed.saved == dense.saved
        assert packed.live_in == dense.live_in and packed.live_out == dense.live_out
        sparse = allocate_x86_mir(mir, keep_diagnostics=False, force_packed=True)
        assert sparse.registers == dense.registers and sparse.saved == dense.saved
        assert sparse.block_body_live == dense.block_body_live
        pinned = set(mir.pinned)
        for block in mir.blocks:
            for position, op in enumerate(block.instructions):
                live = dense.instruction_live_after[block.block_id][position]
                expected = any(v >= 0 and v not in pinned and mir.values[v].pointer
                               and not live & (1 << v) for v in op.inputs)
                actual = sparse.instruction_live_after[block.block_id][position]
                if expected:
                    assert actual == live
                    death_sites += 1
                else:
                    assert actual is None
                    no_death_sites += 1
    assert death_sites > 0 and no_death_sites > 0


def test_mir_packed_budget_falls_back_entire_function(monkeypatch):
    from pcc.backend import self_backend_x86_mir as planner
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "0")
    base = x64.emit_x86_64_linux_asm(CALL_LOOP)
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "1")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "1")
    monkeypatch.setattr(planner, "_PACKED_GRAPH_MIN_VALUES", 0)
    monkeypatch.setattr(planner, "_PACKED_GRAPH_MAX_BYTES", 0)
    assert x64.emit_x86_64_linux_asm(CALL_LOOP) == base

def test_worker_object_cache_binds_opt_in_mir_and_compaction(monkeypatch):
    import json
    from pcc.frontends.python.pipeline_self_backend_cache import _effective_backend_configuration
    monkeypatch.delenv("PCC_SELF_X86_TYPED_MIR", raising=False)
    monkeypatch.delenv("PCC_SELF_X86_TYPED_MIR_COMPACT", raising=False)
    default = _effective_backend_configuration()
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "off")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "1")
    assert _effective_backend_configuration() == default
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "1")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "0")
    mir = _effective_backend_configuration()
    assert json.loads(mir) == json.loads(default) + [["x86-typed-mir", True]]
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", " yes ")
    assert _effective_backend_configuration() == mir
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "on")
    compact = _effective_backend_configuration()
    assert json.loads(compact) == json.loads(mir) + [["x86-typed-mir-compact", True]]
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "true")
    assert _effective_backend_configuration() == compact
    assert len({default, mir, compact}) == 3

def test_unqualified_native_compiler_rejects_explicit_mir_before_cache(monkeypatch):
    import pytest
    from pcc.backend import BackendUnavailable
    from pcc.frontends.python.owned_runtime_build import runtime_build_config
    from pcc.frontends.python.pipeline_self_backend_cache import _effective_backend_configuration
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "0")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "0")
    base = x64.emit_x86_64_linux_asm(CALL_LOOP)
    config = runtime_build_config()
    key = _effective_backend_configuration()
    monkeypatch.setattr(x64, "_x86_typed_mir_compiler_name", lambda: "pcc")
    assert x64.emit_x86_64_linux_asm(CALL_LOOP) == base
    assert runtime_build_config() == config
    assert _effective_backend_configuration() == key
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "1")
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR_COMPACT", "1")
    for operation in (lambda: x64.emit_x86_64_linux_asm(CALL_LOOP),
                      runtime_build_config, _effective_backend_configuration):
        with pytest.raises(BackendUnavailable, match="host compiler only"):
            operation()
    # Rejected explicit native mode cannot poison module-global emitter state.
    monkeypatch.setenv("PCC_SELF_X86_TYPED_MIR", "0")
    assert x64.emit_x86_64_linux_asm(CALL_LOOP) == base

def test_mir_plain_stackmap_remap_preserves_original_tuple_sharing():
    from pcc.backend.self_backend_x86_mir_frame import X86MIRFrame, remap_plain_stack_map
    _, plan = _prepare(CALL_LOOP)
    # One physical span moves by eight bytes; the later root does not move.
    layout = X86MIRFrame(64, [], [], [(0, 32, 8)], [], 48, True)
    shared_locations = (PlannedRootLocation(-24, True), PlannedRootLocation(-40, False))
    shared_reloads = (PlannedManagedReload(-24, -32, 8),)
    distinct_locations = tuple([PlannedRootLocation(-24, True), PlannedRootLocation(-40, False)])
    distinct_reloads = tuple([PlannedManagedReload(-24, -32, 8)])
    assert distinct_locations == shared_locations and distinct_locations is not shared_locations
    assert distinct_reloads == shared_reloads and distinct_reloads is not shared_reloads
    a = PlannedSafepoint(1, ".La", 0, shared_locations, 3, "", 11, shared_reloads)
    b = PlannedSafepoint(2, ".Lb", 1, shared_locations, 4, "err", 12, shared_reloads)
    c = PlannedSafepoint(3, ".Lc", 0, distinct_locations, 5, "", 13, distinct_reloads)
    d = PlannedSafepoint(4, ".Ld", 0, (shared_locations[1],), 6, "", 14, ())
    # Different source tuple identities can still share one exact element.
    e = PlannedSafepoint(5, ".Le", 0, tuple([shared_locations[0]]), 7, "", 15,
                         tuple([shared_reloads[0]]))
    original = replace(plan, frame_size=80, records=(a, b, c, d, e))
    remapped = remap_plain_stack_map(original, layout)
    assert remapped is not None and remapped.frame_size == 64
    aa, bb, cc, dd, ee = remapped.records
    expected_locations = (PlannedRootLocation(-16, True), PlannedRootLocation(-40, False))
    expected_reloads = (PlannedManagedReload(-16, -24, 8),)
    assert aa == replace(a, locations=expected_locations, reloads=expected_reloads)
    assert bb == replace(b, locations=expected_locations, reloads=expected_reloads)
    assert cc == replace(c, locations=expected_locations, reloads=expected_reloads)
    # Preserve only the input plan's exact immutable sharing, never merge
    # distinct-but-equal tuples or mutate their old offsets.
    assert aa.locations is bb.locations and aa.reloads is bb.reloads
    assert aa.locations is not cc.locations and aa.reloads is not cc.reloads
    assert aa.locations[0] is not cc.locations[0] and aa.reloads[0] is not cc.reloads[0]
    assert ee.locations is not aa.locations and ee.reloads is not aa.reloads
    assert ee.locations[0] is aa.locations[0] and ee.reloads[0] is aa.reloads[0]
    assert aa.locations[1] is shared_locations[1]
    assert dd is d
    assert original.records == (a, b, c, d, e)
    assert a.locations[0].offset == -24 and a.reloads[0].destination_offset == -32
    malformed = replace(c, locations=(object(),))
    assert remap_plain_stack_map(replace(original, records=(a, b, malformed)), layout) is None

def test_mir_plain_stackmap_custom_layout_observes_uncached_remaps(monkeypatch):
    from pcc.backend.self_backend_x86_mir_frame import X86MIRFrame, remap_plain_stack_map
    class ObservedLayout(X86MIRFrame):
        def __init__(self):
            super().__init__(64, [], [], [(0, 32, 8)], [], 48, True)
            self.calls = []
        def remap(self, offset):
            self.calls.append(offset)
            return super().remap(offset)
    _, plan = _prepare(CALL_LOOP)
    locations = (PlannedRootLocation(-24, True),)
    reloads = (PlannedManagedReload(-24, -32, 8),)
    a = PlannedSafepoint(1, ".La", 0, locations, reloads=reloads)
    b = PlannedSafepoint(2, ".Lb", 0, locations, reloads=reloads)
    layout = ObservedLayout()
    remapped = remap_plain_stack_map(replace(plan, frame_size=80, records=(a, b)), layout)
    assert remapped is not None
    assert layout.calls == [24, 24, 32, 24, 24, 32]
    assert remapped.records[0].locations is not remapped.records[1].locations
    assert remapped.records[0].reloads is not remapped.records[1].reloads

    original_remap = X86MIRFrame.remap
    patched_calls = []
    def patched_remap(self, offset):
        patched_calls.append(offset)
        return original_remap(self, offset)
    monkeypatch.setattr(X86MIRFrame, "remap", patched_remap)
    exact_layout = X86MIRFrame(64, [], [], [(0, 32, 8)], [], 48, True)
    patched = remap_plain_stack_map(replace(plan, frame_size=80, records=(a, b)), exact_layout)
    assert patched is not None and patched_calls == [24, 24, 32, 24, 24, 32]
    assert patched.records[0].locations is not patched.records[1].locations

def test_mir_packed_clobber_union_deduplicates_repeated_allowed_updates(monkeypatch):
    from pcc.backend import self_backend_x86_mir as planner
    source = TRIPLE + "declare void @callee(i64)\ndefine i64 @f(i64 %a, i64 %b) {\nentry:\n"
    source += "  call void @callee(i64 %b)\n" * 24
    source += "  %x = add i64 %a, %b\n  ret i64 %x\n}\n"
    func, plan = _prepare(source)
    kernel = get_indexed_function_kernel(func)
    mir = planner.build_x86_mir(func, plan)
    assert mir is not None
    # Both integer arguments stay live across each identical call. Every
    # clobber event used to enumerate this same mask even after its bits
    # had already been removed from each allowed-register mask.
    affected = (1 << kernel.value_id("a")) | (1 << kernel.value_id("b"))
    original = planner._bits
    visits = []
    def counted(mask):
        if mask == affected:
            visits.append(mask)
        yield from original(mask)
    monkeypatch.setattr(planner, "_bits", counted)
    packed = planner.allocate_x86_mir(mir, force_packed=True)
    # One visit for the unioned call mask and one for the final add inputs.
    assert len(visits) == 2
    monkeypatch.setattr(planner, "_bits", original)
    dense = planner.allocate_x86_mir(mir)
    assert packed.allowed == dense.allowed
    assert packed.interference == dense.interference
    assert packed.registers == dense.registers and packed.saved == dense.saved
    for name in ("a", "b"):
        assert packed.allowed[kernel.value_id(name)] & planner.CALLER_MASK == 0


def test_mir_packed_clobber_union_matches_fixed_operand_and_call_masks():
    from pcc.backend import self_backend_x86_mir as planner
    source = TRIPLE + """declare void @callee(i64)
define i64 @f(i64 %a, i64 %b, i64 %c) {
entry:
  call void @callee(i64 %a)
  %shift = shl i64 %a, %b
  %q = udiv i64 %shift, %b
  call void @callee(i64 %q)
  %r = add i64 %q, %c
  ret i64 %r
}
"""
    func, plan = _prepare(source)
    mir = planner.build_x86_mir(func, plan)
    assert mir is not None
    dense = planner.allocate_x86_mir(mir)
    packed = planner.allocate_x86_mir(mir, force_packed=True)
    assert packed.allowed == dense.allowed
    assert packed.interference == dense.interference
    assert packed.registers == dense.registers and packed.saved == dense.saved
    assert packed.instruction_live_after == dense.instruction_live_after
