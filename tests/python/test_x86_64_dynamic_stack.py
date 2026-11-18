"""Linux SysV dynamic stack storage must survive real frame and ABI boundaries."""
import platform
import signal
import subprocess
import sys

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.x86_64_asm_driver import assemble_file

TARGET = "x86_64-unknown-linux-gnu"
NATIVE = pytest.mark.pcc_gate(probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64")
START = """.intel_syntax noprefix
.text
.globl _start
_start:
  mov rbx, 111
  mov r12, 112
  mov r13, 113
  mov r14, 114
  mov r15, 115
  call main
  cmp rbx, 111
  jne .Lbroken_callee
  cmp r12, 112
  jne .Lbroken_callee
  cmp r13, 113
  jne .Lbroken_callee
  cmp r14, 114
  jne .Lbroken_callee
  cmp r15, 115
  jne .Lbroken_callee
  mov edi, eax
  jmp .Lexit
.Lbroken_callee:
  mov edi, 99
.Lexit:
  mov eax, 60
  syscall
"""


def _module(body, target=TARGET):
    # Only an explicitly marked allocation gets this test promise; raw and
    # stripped C IR are tested separately and must remain inadmissible.
    metadata = '\n!701 = !{ !"pcc.alloca.function_lifetime.v1" }\n' if "!701" in body else ""
    return 'target triple = "' + target + '"\n' + body + metadata


def _run(tmp_path, monkeypatch, body, expected, extra="", stack_limit=0):
    def forbidden(*args, **kwargs):
        raise AssertionError("owned stack lowering invoked an external build tool")
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        text = _module(body)
        obj = emit_owned_object(text, TARGET)
        assert obj == emit_owned_object(text, TARGET)
        raw = link_static_executable([assemble_file(START + extra), parse_relocatable(obj)], entry="_start")
    elf = tmp_path / "owned"
    elf.write_bytes(raw)
    elf.chmod(0o700)
    def limit():
        import resource
        resource.setrlimit(resource.RLIMIT_STACK, (stack_limit, stack_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    run = subprocess.run([str(elf)], capture_output=True, timeout=10,
                         preexec_fn=limit if stack_limit else None)
    assert (run.returncode, run.stdout, run.stderr) == (expected, b"", b"")


def _one(element="i8", count="i64", suffix="", call="17"):
    return (f"define i32 @body({count} %count) {{\nentry:\n"
            f"  %p = alloca {element}, {count} %count{suffix}, !pcc.alloca.function_lifetime !701\n"
            "  store i8 42, ptr %p\n  %result = load i8, ptr %p\n"
            "  %wide = zext i8 %result to i32\n  ret i32 %wide\n}\n"
            "define i32 @main() {\nentry:\n"
            f"  %r = call i32 @body({count} {call})\n  ret i32 %r\n}}\n")


@NATIVE
def test_multiple_arrays_indirect_stack_call_and_narrow_unsigned_count(tmp_path, monkeypatch):
    body = """
declare i64 @stack_observer(i64,i64,i64,i64,i64,i64,ptr,ptr)
define i32 @probe(i8 %tiny, i64 %wide, ptr %observer) {
entry:
  %a = alloca i8, i8 %tiny, align 64, !pcc.alloca.function_lifetime !701
  %aend = getelementptr i8, ptr %a, i64 254
  store i8 31, ptr %aend
  %b = alloca i8, i64 %wide, align 32, !pcc.alloca.function_lifetime !701
  %bend = getelementptr i8, ptr %b, i64 8999
  store i8 11, ptr %bend
  %v = call i64 %observer(i64 1,i64 2,i64 3,i64 4,i64 5,i64 6,ptr %a,ptr %b)
  %v32 = trunc i64 %v to i32
  ret i32 %v32
}
define i32 @main() {
entry:
  %v = call i32 @probe(i8 255,i64 9000,ptr @stack_observer)
  ret i32 %v
}
"""
    extra = """
.globl stack_observer
stack_observer:
  mov r10, rsp
  and r10, 15
  cmp r10, 8
  jne .Lbad_stack
  mov r10, QWORD PTR [rsp + 8]
  mov r11, QWORD PTR [rsp + 16]
  mov rax, r10
  and rax, 63
  jne .Lbad_stack
  mov rax, r11
  and rax, 31
  jne .Lbad_stack
  movzx eax, BYTE PTR [r10 + 254]
  movzx ecx, BYTE PTR [r11 + 8999]
  add eax, ecx
  ret
.Lbad_stack:
  mov eax, 98
  ret
"""
    _run(tmp_path, monkeypatch, body, 42, extra)


@NATIVE
def test_variadic_after_dynamic_stack_uses_fixed_gp_fp_and_overflow_area(tmp_path, monkeypatch):
    reads = "\n".join(f"  %g{i} = va_arg ptr %ap, i64" for i in range(7))
    # One named GP leaves five anonymous register slots, then two stack
    # arguments. The floating argument occupies the distinct SSE save bank.
    body = """
declare void @llvm.va_start(ptr)
define i32 @var(i64 %n, ...) {
entry:
  %p = alloca i8, i64 %n, !pcc.alloca.function_lifetime !701
  store i8 10, ptr %p
  %ap = alloca [24 x i8]
  call void @llvm.va_start(ptr %ap)
""" + reads + """
  %f = va_arg ptr %ap, double
  %fi = fptosi double %f to i64
  %x = add i64 %g0, %g6
  %y = add i64 %x, %fi
  %tag = load i8, ptr %p
  %w = zext i8 %tag to i64
  %z = add i64 %y, %w
  %r = trunc i64 %z to i32
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 (i64, ...) @var(i64 5000,i64 12,i64 2,i64 3,i64 4,i64 5,i64 6,i64 13,double 7.0)
  ret i32 %r
}
"""
    _run(tmp_path, monkeypatch, body, 42)


@NATIVE
def test_nearbyint_keeps_mxcsr_and_does_not_overwrite_array(tmp_path, monkeypatch):
    body = """
declare double @llvm.nearbyint.f64(double)
declare void @set_control(i32)
declare i32 @read_control()
define i32 @probe(i64 %n) {
entry:
  %p = alloca i8, i64 %n, !pcc.alloca.function_lifetime !701
  %canary = getelementptr i8, ptr %p, i64 8
  store i64 123456789, ptr %canary
  %f = call double @llvm.nearbyint.f64(double 41.8)
  %got = load i64, ptr %canary
  %right = icmp eq i64 %got, 123456789
  %fv = fptosi double %f to i32
  %out = select i1 %right, i32 %fv, i32 96
  ret i32 %out
}
define i32 @main() {
entry:
  call void @set_control(i32 16256)
  %v0 = call i32 @probe(i64 64)
  %control0 = call i32 @read_control()
  call void @set_control(i32 16288)
  %v1 = call i32 @probe(i64 64)
  %control1 = call i32 @read_control()
  %a = icmp eq i32 %v0, 42
  %b = icmp eq i32 %v1, 42
  %c = icmp eq i32 %control0, 16256
  %d = icmp eq i32 %control1, 16288
  %ab = and i1 %a, %b
  %cd = and i1 %c, %d
  %ok = and i1 %ab, %cd
  %out = select i1 %ok, i32 42, i32 96
  ret i32 %out
}
"""
    # Exercise the call ABI and state restoration, not libm arithmetic.
    # Both trials use downward rounding. The stand-in toggles inexact,
    # and independent assembly observers must see the caller's old bit.
    extra = """
.globl set_control
set_control:
  sub rsp, 8
  mov DWORD PTR [rsp], edi
  ldmxcsr DWORD PTR [rsp]
  add rsp, 8
  ret
.globl read_control
read_control:
  sub rsp, 8
  stmxcsr DWORD PTR [rsp]
  mov eax, DWORD PTR [rsp]
  add rsp, 8
  ret
.globl rint
rint:
  sub rsp, 8
  stmxcsr DWORD PTR [rsp]
  mov ecx, DWORD PTR [rsp]
  xor ecx, 32
  mov DWORD PTR [rsp], ecx
  ldmxcsr DWORD PTR [rsp]
  add rsp, 8
  mov eax, 42
  cvtsi2sd xmm0, eax
  ret
"""
    _run(tmp_path, monkeypatch, body, 42, extra)


@NATIVE
@pytest.mark.parametrize("element,count", (("i64", "2305843009213693953"), ("i8", "-1")))
def test_size_overflow_or_pointer_underflow_traps_before_moving_rsp(tmp_path, monkeypatch, element, count):
    _run(tmp_path, monkeypatch, _one(element, call=count), -signal.SIGILL, stack_limit=512 * 1024)


@NATIVE
def test_allocation_probes_every_page_before_returning_even_without_a_user_store(tmp_path, monkeypatch):
    # Without probing, merely subtracting a large size then restoring RSP at
    # return could silently succeed under the operating system's stack limit.
    body = """define i32 @body(i64 %n) {
entry:
  %p = alloca i8, i64 %n, !pcc.alloca.function_lifetime !701
  ret i32 91
}
define i32 @main() {
entry:
  %r = call i32 @body(i64 4194304)
  ret i32 %r
}
"""
    _run(tmp_path, monkeypatch, body, -signal.SIGSEGV, stack_limit=512 * 1024)


@pytest.mark.parametrize("element,count,suffix,match", (
    ("i8", "i24", "", "count width"), ("i8", "i64", ", align 3", "power-of-two"),
    ("i8", "i64", ", align 0", "power-of-two"), ("i8", "i64", ", addrspace(2)", "address space"),
    ("[3 x i64]", "i64", "", "explicit alignment"),
))
def test_unverified_count_alignment_or_storage_shape_is_not_lowered(element,count,suffix,match):
    with pytest.raises(BackendUnavailable, match=match):
        emit_x86_64_linux_asm(_module(_one(element,count,suffix)))


def test_existing_win64_and_byte_only_intrinsic_stay_rejected():
    windows = "x86_64-pc-windows-msvc"
    with pytest.raises(BackendUnavailable, match="does not support run-time sized stack"):
        emit_owned_object(_module(_one(),windows), windows)
    body = """
declare ptr @llvm.pcc.dynamic.alloca(i64,i64)
define ptr @old(i64 %n) {
entry:
  %p = call ptr @llvm.pcc.dynamic.alloca(i64 %n,i64 16)
  ret ptr %p
}
"""
    with pytest.raises(BackendUnavailable, match="does not support run-time sized stack"):
        emit_x86_64_linux_asm(_module(body))


@pytest.mark.parametrize("suffix", ("", "_lifo", "_mapped", "_local_mapped", "_lifo_local_mapped"))
@pytest.mark.parametrize("first", (True, False))
def test_managed_registration_rejected_with_dynamic_frame_before_or_after(suffix, first):
    enter, leave = "pcc_gc_frame_enter" + suffix, "pcc_gc_frame_leave" + suffix
    allocation = "  %v = alloca i8, i64 %n, !pcc.alloca.function_lifetime !701\n"
    start = f"  call void @{enter}(ptr %map, ptr %roots)\n"
    body = (f"declare void @{enter}(ptr,ptr)\ndeclare void @{leave}(ptr)\n"
            "define void @body(i64 %n, ptr %map, ptr %roots) {\nentry:\n"
            + (allocation + start if first else start + allocation)
            + f"  call void @{leave}(ptr %roots)\n  ret void\n}}\n")
    with pytest.raises(BackendUnavailable, match="managed frame registration"):
        emit_x86_64_linux_asm(_module(body))


@pytest.mark.parametrize("dtype,attrs", (
    ("i64", "i64 2,i64 4,i64 16"), ("ptr", "i32 2,i64 4,i64 16"),
    ("ptr", "i64 2,i64 4"), ("ptr", "i64 2,i64 4,i64 %n"),
    ("ptr", "i64 2,i64 4,i64 24"), ("ptr", "i64 2,i64 4,i64 8589934592"),
))
def test_malformed_checked_intrinsic_never_changes_rsp(dtype, attrs):
    body = (f"define {dtype} @f(i64 %n) {{\nentry:\n"
            f"  %p = call {dtype} @llvm.pcc.x86.dynamic.array({attrs})\n  ret {dtype} %p\n}}\n")
    with pytest.raises(BackendUnavailable, match="dynamic alloca"):
        emit_x86_64_linux_asm(_module(body))


@pytest.mark.parametrize("storage", ("inalloca i8", "swifterror ptr", "ptr addrspace(5)"))
def test_special_counted_storage_cannot_fall_through_to_one_fixed_slot(storage):
    with pytest.raises(BackendUnavailable, match="default address space"):
        emit_x86_64_linux_asm(_module(_one(storage)))


@pytest.mark.parametrize("layout", ("e-A5", "e-p:32:32", "e-i64:256"))
def test_custom_layout_never_guesses_array_stride_or_alloca_address_space(layout):
    body = 'target datalayout = "' + layout + '"\n' + _one()
    with pytest.raises(BackendUnavailable, match="SysV data layout"):
        emit_x86_64_linux_asm(_module(body))


@pytest.mark.parametrize("call", ("  %saved = call ptr @llvm.stacksave()",
                                  "  call void @llvm.stackrestore(ptr %p)"))
def test_scoped_llvm_stack_intrinsics_are_not_treated_as_noops(call):
    body = _one().replace("  store i8 42, ptr %p", call + "\n  store i8 42, ptr %p")
    with pytest.raises(BackendUnavailable, match=r"llvm\.stack(save|restore)"):
        emit_x86_64_linux_asm(_module(body))


def test_overalignment_mask_is_full_width_and_win64_rejects_checked_form():
    asm = emit_x86_64_linux_asm(_module(_one(suffix=", align 4294967296")))
    assert "mov r11, -4294967296" in asm and "and r10, r11" in asm
    body = """define ptr @f(i64 %n) {
entry:
  %p = call ptr @llvm.pcc.x86.dynamic.array(i64 %n,i64 1,i64 16)
  ret ptr %p
}
"""
    windows = "x86_64-pc-windows-msvc"
    with pytest.raises(BackendUnavailable, match="no native lowering"):
        emit_owned_object(_module(body, windows), windows)


@NATIVE
def test_explicit_aggregate_stride_and_padding_match_real_allocation(tmp_path, monkeypatch):
    # {[i8,padding],i64} has a 16-byte allocation stride, not 9 bytes.
    # The address observer is independent owned assembly, not another GEP.
    body = """
%Pair = type { i8, i64 }
declare i32 @check_span(ptr,ptr)
define i32 @body(i64 %n) {
entry:
  %first = alloca %Pair, i64 %n, align 16, !pcc.alloca.function_lifetime !701
  %next = alloca %Pair, i64 %n, align 16, !pcc.alloca.function_lifetime !701
  %r = call i32 @check_span(ptr %first,ptr %next)
  ret i32 %r
}
define i32 @main() {
entry:
  %r = call i32 @body(i64 5)
  ret i32 %r
}
"""
    extra = """
.globl check_span
check_span:
  sub rdi, rsi
  cmp rdi, 80
  jne .Lwrong_span
  mov eax, 42
  ret
.Lwrong_span:
  mov eax, 93
  ret
"""
    _run(tmp_path, monkeypatch, body, 42, extra)
# Proposed additions to test_x86_64_dynamic_stack.py.
# Authored by independent reviewer; NOT EXECUTED. Reuse _run/_module/NATIVE.
# The assembly assertions intentionally fail if promotion is not exercised.

@NATIVE
@pytest.mark.parametrize("with_scratch", (False, True))
def test_five_live_call_results_and_both_dynamic_frame_exits(tmp_path, monkeypatch, with_scratch):
    import re
    body = '''
@frame_double_tag = global double 1.0
declare i64 @frame_identity(i64)
declare void @frame_clobber()
define i32 @frame_probe(i64 %n, i1 %choose) {
entry:
  %p = alloca i8, i64 %n, align 16, !pcc.alloca.function_lifetime !701
  %a = call i64 @frame_identity(i64 1)
  %b = call i64 @frame_identity(i64 2)
  %c = call i64 @frame_identity(i64 3)
  %d = call i64 @frame_identity(i64 4)
  %e = call i64 @frame_identity(i64 10)
  call void @frame_clobber()
  %fp = load double, ptr @frame_double_tag
  %one = fptoui double %fp to i64
  %tag = add i64 %e, %one
  br i1 %choose, label %left, label %right
left:
  %l0 = add i64 %a, %b
  %l1 = add i64 %l0, %c
  %l2 = add i64 %l1, %d
  %l3 = add i64 %l2, %tag
  %lv = trunc i64 %l3 to i32
  ret i32 %lv
right:
  %r0 = add i64 %tag, %d
  %r1 = add i64 %r0, %c
  %r2 = add i64 %r1, %b
  %r3 = add i64 %r2, %a
  %rv = trunc i64 %r3 to i32
  ret i32 %rv
}
define i32 @main() {
entry:
  %a = call i32 @frame_probe(i64 8193, i1 0)
  %b = call i32 @frame_probe(i64 0, i1 1)
  %r = add i32 %a, %b
  ret i32 %r
}
'''
    if not with_scratch:
        body = body.replace("  %fp = load double, ptr @frame_double_tag\n"
                            "  %one = fptoui double %fp to i64\n"
                            "  %tag = add i64 %e, %one\n", "")
        body = body.replace("%tag", "%e").replace("@frame_identity(i64 10)", "@frame_identity(i64 11)")
    asm = emit_x86_64_linux_asm(_module(body))
    # Only this function, not main or an unrelated prologue, can satisfy it.
    section = asm.split('frame_probe:', 1)[1].split('.size frame_probe', 1)[0]
    assert len(re.findall(r'^  ret$', section, re.M)) == 2, section
    for reg in ('rbx', 'r12', 'r13', 'r14', 'r15'):
        assert re.search(r'mov QWORD PTR \[rsp \+ \d+\], ' + reg + r'\b', section), section
        assert len(re.findall(r'mov ' + reg + r', QWORD PTR \[rsp \+ \d+\]', section)) == 2, section
    # Track each of the five live values across the actual call. The
    # unmodified scalar case really promotes four and spills the fifth.
    # The conversion case also dirties RBX, which is scratch, not a fifth
    # promotion register; its changed live ranges may choose different homes.
    before, after = section.split("  call frame_clobber", 1)
    segments = before.split("  call frame_identity")
    assert len(segments) == 6, before
    homes = []
    for result in segments[1:]:
        store = re.search(r'mov (r1[2-5]|QWORD PTR \[rbp - \d+\]), rax\b', result)
        assert store is not None, result
        home = store.group(1)
        assert home not in homes and ", " + home + "\n" in after, (home, after)
        homes.append(home)
    if not with_scratch:
        assert set(homes[:4]) == {"r12", "r13", "r14", "r15"}
        assert homes[4].startswith("QWORD PTR [rbp - ")
    else:
        assert "  mov rbx, rax\n  sar rbx, 63" in after
        assert "  and r10, rbx" in after
        for reg in ("r12", "r13", "r14", "r15"):
            assert re.search(r'mov ' + reg + r', (rax|rdi)\b', before), before
            assert re.search(r', ' + reg + r'\n', section.split(".Lframe_probe_entry:", 1)[1]), section
    extra = '''
.globl frame_identity
frame_identity:
  mov rax, rdi
  ret
.globl frame_clobber
frame_clobber:
  mov rax, -1
  mov rcx, -2
  mov rdx, -3
  mov rsi, -4
  mov rdi, -5
  mov r8, -6
  mov r9, -7
  mov r10, -8
  mov r11, -9
  ret
'''
    _run(tmp_path, monkeypatch, body, 42, extra)


@NATIVE
def test_zero_count_loop_does_not_move_rsp_or_overwrite_live_storage(tmp_path, monkeypatch):
    body = '''
declare i64 @frame_read_rsp()
define i32 @zero_loop(i64 %zero) {
entry:
  %length = add i64 %zero, 64
  %outer = alloca i8, i64 %length, !pcc.alloca.function_lifetime !701
  store i64 42, ptr %outer
  %before = call i64 @frame_read_rsp()
  br label %loop
loop:
  %i = phi i64 [0, %entry], [%next, %again]
  %z = alloca i8, i64 %zero, align 16, !pcc.alloca.function_lifetime !701
  %after = call i64 @frame_read_rsp()
  %same = icmp eq i64 %before, %after
  br i1 %same, label %again, label %bad
again:
  %next = add i64 %i, 1
  %more = icmp ult i64 %next, 64
  br i1 %more, label %loop, label %done
done:
  %tag = load i64, ptr %outer
  %r = trunc i64 %tag to i32
  ret i32 %r
bad:
  ret i32 97
}
define i32 @main() {
entry:
  %r = call i32 @zero_loop(i64 0)
  ret i32 %r
}
'''
    extra = '''
.globl frame_read_rsp
frame_read_rsp:
  mov rax, rsp
  ret
'''
    _run(tmp_path, monkeypatch, body, 42, extra)


def _c_source_ir(source):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.parse import make_c_parser
    from pcc.frontends.c.passes.context import PassContext
    codegen = CCodeGenerator(translation_unit_name="lifetime.c",
                             pass_ctx=PassContext(opt_level=0, target_triple=TARGET))
    codegen.generate_code(make_c_parser().parse(source))
    return str(codegen.module)


@pytest.mark.parametrize("source", (
    "int f(int n){int s=0;for(int i=0;i<2;i++){int a[n];a[0]=21;s+=a[0];}return s;}",
    "int f(int n){int s=0;{int a[n];a[0]=42;s=a[0];}return s;}",
    "int f(int n){int i=0;again:;int a[n];a[0]=42;if(++i<2)goto again;return a[0];}",
    "int f(int n){int a[n];switch(n){case 1: inside: return 1;}return 42;}",
    "int f(int n,int(*op)(void)){op();int a[n];a[0]=42;return a[0];}",
    "int f(int n,int(*op)(void)){int a[op()+n];a[0]=42;return a[0];}",
))
def test_c_block_lifetime_or_possible_reentry_is_not_promised(source):
    text = _c_source_ir(source)
    assert " = alloca i32, i64 " in text
    assert "!pcc.alloca.function_lifetime" not in text
    with pytest.raises(BackendUnavailable, match="verified function-lifetime proof"):
        emit_x86_64_linux_asm(text)


def test_outer_declaration_does_not_authorize_vla_nested_in_its_bound():
    text = _c_source_ir(
        "int f(int n){int a[({int b[n];b[0]=1;b[0]+n;})];a[0]=42;return a[0];}"
    )
    allocations = [line for line in text.splitlines() if " = alloca i32, i64 " in line]
    assert len(allocations) == 2, allocations
    assert ["!pcc.alloca.function_lifetime" in line for line in allocations] == [False, True]
    with pytest.raises(BackendUnavailable, match="verified function-lifetime proof"):
        emit_x86_64_linux_asm(text)


@pytest.mark.parametrize("alter", ("removed", "renumbered", "unknown", "duplicate",
                                   "duplicate-definition", "neighbor", "comment"))
def test_positive_lifetime_proof_belongs_to_this_instruction_and_module(alter):
    text = _module(_one())
    mark = ", !pcc.alloca.function_lifetime !701"
    if alter == "removed":
        text = text.replace(mark, "")
    elif alter == "renumbered":
        text = text.replace(mark, mark.replace("701","702"))
    elif alter == "unknown":
        text = text.replace("lifetime.v1", "lifetime.v2")
    elif alter == "duplicate":
        text = text.replace(mark, mark + mark)
    elif alter == "duplicate-definition":
        text += '\n!701 = !{ !"pcc.alloca.function_lifetime.v1" }\n'
    elif alter == "neighbor":
        text = text.replace(mark, "").replace("store i8 42, ptr %p","store i8 42, ptr %p" + mark)
    else:
        text = text.replace(mark, " ; comments are not instruction attachments" + mark)
    with pytest.raises(BackendUnavailable, match="lifetime proof"):
        emit_x86_64_linux_asm(text)


def test_c_direct_function_lifetime_vla_survives_loop_and_later_call():
    text = _c_source_ir("int twice(int x){return x*2;}"
        "int f(int n){int a[n];int i;for(i=0;i<n;i++)a[i]=i+20;return twice(a[1]);}")
    rows = [line for line in text.splitlines() if " = alloca i32, i64 " in line]
    assert len(rows) == 1 and "!pcc.alloca.function_lifetime" in rows[0]
    assert "  ud2" in emit_x86_64_linux_asm(text)


def test_raw_checked_intrinsic_cannot_bypass_layout_validation():
    body = """target datalayout = "e-p:32:32"
define ptr @direct(i64 %n) {
entry:
  %out = call ptr @llvm.pcc.x86.dynamic.array(i64 %n,i64 8,i64 16)
  ret ptr %out
}
"""
    with pytest.raises(BackendUnavailable, match="SysV data layout"):
        emit_x86_64_linux_asm(_module(body))


def test_direct_unit_count_contract_does_not_coerce_a_textual_one():
    from pcc.ir import ir
    check = ir.IRBuilder._require_direct_unit_alloca_count
    i64 = ir.IntType(64)
    check(ir.Constant(i64, 1))
    wrong = ir.Constant(i64, 7)
    wrong.value = 1
    for no_unit in (ir.Constant(i64, 0), ir.Constant(i64, 2), ir.Constant(i64, ir.Value(i64, "1")), wrong,
                    ir.Constant(i64, True),
                    ir.Constant(ir.FloatType(), 1), ir.Value(i64, "1")):
        with pytest.raises(NotImplementedError, match="does not represent counted alloca"):
            check(no_unit)


@pytest.mark.parametrize("assignment", ("%p=alloca ", "%p =  alloca ", "%p\t=\talloca\t"))
def test_noncanonical_alloca_whitespace_is_still_a_counted_instruction(assignment):
    original = _module(_one())
    spaced = original.replace("%p = alloca ", assignment)
    assert spaced != original
    assert emit_owned_object(spaced, TARGET) == emit_owned_object(original, TARGET)
    stripped = spaced.replace(", !pcc.alloca.function_lifetime !701", "")
    with pytest.raises(BackendUnavailable, match="verified function-lifetime proof"):
        emit_owned_object(stripped, TARGET)


@pytest.mark.parametrize("line", ("%p =  alloca i8, i64 %n", "%p=alloca i8, i64 %n",
                                  "%p = alloca { i32, i32 }, i32 2"))
def test_neither_fixed_instruction_parser_discards_a_remaining_count(line):
    from pcc.backend.self_backend_kernel import IndexedFunctionSeed
    from pcc.backend.self_backend_parse import _parse_indexed_hot_instruction, _parse_instruction
    with pytest.raises(BackendUnavailable, match="cannot discard an unlegalized alloca count"):
        _parse_instruction("remaining", "entry", line)
    seed = IndexedFunctionSeed()
    seed.define_value("p", 0)
    with pytest.raises(BackendUnavailable, match="cannot discard an unlegalized alloca count"):
        _parse_indexed_hot_instruction("remaining", "entry", line, seed)
    assert len(seed.instruction_kind_ids) == 0


@pytest.mark.parametrize("symbol", ("@llvm.pcc.x86.dynamic.array", '@"llvm.pcc.x86.dynamic.array"'))
def test_layout_validation_uses_the_decoded_callee(symbol):
    body = ('declare ptr ' + symbol + '(i64,i64,i64)\n'
            'define ptr @direct(i64 %n) {\nentry:\n'
            '  %out = call ptr ' + symbol + '(i64 %n,i64 8,i64 16)\n  ret ptr %out\n}\n')
    accepted = _module(body)
    assert "  ud2" in emit_x86_64_linux_asm(accepted)
    for layout in ('target datalayout = "e-p:32:32"\n',
                   'target\tdatalayout="e-p:64:64-A1" ; different allocation space\n'):
        with pytest.raises(BackendUnavailable, match="SysV data layout"):
            emit_owned_object(_module(layout + body), TARGET)
        # A declaration is not a use. Neither it nor unrelated debug text
        # makes an otherwise unchanged fixed function require this contract.
        unused = body.split("define ptr")[0] + 'define i32 @ordinary() {\nentry:\n  ret i32 42\n}\n'
        assert "  ud2" not in emit_x86_64_linux_asm(_module(layout + unused))


def test_parameter_evaluation_cannot_assert_one_time_vla_entry():
    text = _c_source_ir("int extent(void);int f(int n,int a[extent()])"
                        "{int b[n];b[0]=42;return b[0];}")
    assert " = alloca i32, i64 " in text
    assert "!pcc.alloca.function_lifetime" not in text
    with pytest.raises(BackendUnavailable, match="verified function-lifetime proof"):
        emit_owned_object(text, TARGET)
