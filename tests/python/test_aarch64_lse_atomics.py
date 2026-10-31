"""arm64 macOS atomics lower to ARMv8.1 LSE instructions; Linux keeps LL/SC.

Arm ARM C6.2 (LDADD, LDCLR, LDEOR, LDSET, SWP, CAS and their A/L/B/H forms):
one instruction performs the read-modify-write with the ordering's
acquire/release bits. The lowering is LLVM's with +lse: ``sub`` adds the
negation, ``and`` clears the complement, ``cmpxchg`` seeds CAS's Rs with the
expected value and compares after it. Every arm64 Mac implements LSE; Linux
AArch64 does not guarantee it (ARMv8.0 cores), so that triple keeps the
exclusive-monitor loops. Expected words were cross-checked once against
``llvm-mc -triple=arm64-apple-macos -mattr=+lse -show-encoding``; no LLVM
tool runs here. The programs are emitted by the production object path,
linked by pcc's owned linker and executed.
"""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.arm64_encode import EncodeError, assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


_NATIVE = pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")


@pytest.mark.parametrize("instruction, encoding", [
    ("ldadd x1, x2, [x3]", "620021f8"),
    ("ldaddal x10, x11, [x9]", "2b01eaf8"),
    ("ldadda w1, w2, [sp]", "e203a1b8"),
    ("ldaddl x4, x5, [x6]", "c50064f8"),
    ("ldclral x10, x11, [x9]", "2b11eaf8"),
    ("ldsetal w10, w11, [x9]", "2b31eab8"),
    ("ldeoral x1, x2, [x3]", "6220e1f8"),
    ("swpal x10, x11, [x9]", "2b81eaf8"),
    ("ldaddalb w10, w11, [x9]", "2b01ea38"),
    ("ldaddalh w10, w11, [x9]", "2b01ea78"),
    ("ldsetb w1, w2, [x3]", "62302138"),
    ("swplh w1, w2, [x3]", "62806178"),
    ("ldadd x1, xzr, [x3]", "7f0021f8"),
    ("cas x0, x1, [x2]", "417ca0c8"),
    ("casal x12, x11, [x9]", "2bfdecc8"),
    ("casa w12, w11, [x9]", "2b7dec88"),
    ("casl x12, x11, [x9]", "2bfdacc8"),
    ("casalb w12, w11, [x9]", "2bfdec08"),
    ("casalh w12, w11, [x9]", "2bfdec48"),
    ("mvn x10, x11", "ea032baa"),
    ("mvn w10, w11", "ea032b2a"),
])
def test_lse_words_match_reference(instruction, encoding):
    assembled = assemble_text(instruction)
    assert assembled.code == bytes.fromhex(encoding)
    assert assembled.relocations == []


@pytest.mark.parametrize("instruction", [
    "ldaddb x1, x2, [x3]", "ldadd w1, x2, [x3]", "ldadd x1, x2, [x3, #8]",
    "casal x1, x2, [x3]!", "ldaddxl x1, x2, [x3]", "mvn x1, w2",
])
def test_lse_operands_fail_closed(instruction):
    with pytest.raises(EncodeError):
        assemble_text(instruction)


_SUFFIX = {"monotonic": "", "acquire": "a", "release": "l", "acq_rel": "al", "seq_cst": "al"}
_RMW = {"add": "ldadd", "sub": "ldadd", "and": "ldclr", "or": "ldset", "xchg": "swp"}


def _body(text, name):
    return text.split("_" + name + ":", 1)[1].split(".section", 1)[0]


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize("width, register, size", [(8, "w", "b"), (16, "w", "h"), (32, "w", ""), (64, "x", "")])
@pytest.mark.parametrize("ordering", sorted(_SUFFIX))
@pytest.mark.parametrize("op", sorted(_RMW))
def test_darwin_rmw_is_one_lse_instruction(op, ordering, width, register, size, optimize):
    asm = emit_aarch64_darwin_asm(f'''target triple = "arm64-apple-darwin"
define i{width} @probe(ptr %address, i{width} %operand) {{
entry:
  %prior = atomicrmw {op} ptr %address, i{width} %operand {ordering}
  ret i{width} %prior
}}
''', optimize=optimize)
    body = _body(asm, "probe")
    mnemonic = _RMW[op] + _SUFFIX[ordering] + size
    assert re.search(rf"^  {mnemonic} {register}\w+, {register}\w+, \[x\w+\]$", body, re.M), body
    if op == "sub":
        assert re.search(rf"^  neg {register}10, {register}\w+$", body, re.M), body
    if op == "and":
        assert re.search(rf"^  mvn {register}10, {register}\w+$", body, re.M), body
    assert not re.search(r"^  (?:ldx|ldax|stx|stlx)r", body, re.M), body


_CMPXCHG_MERGED = {
    ("monotonic", "monotonic"): "", ("monotonic", "acquire"): "a",
    ("acquire", "monotonic"): "a", ("release", "monotonic"): "l",
    ("release", "acquire"): "al", ("acq_rel", "acquire"): "al",
    ("seq_cst", "seq_cst"): "al",
}


@pytest.mark.parametrize("success, failure", sorted(_CMPXCHG_MERGED))
@pytest.mark.parametrize("width, register, size", [(8, "w", "b"), (16, "w", "h"), (64, "x", "")])
def test_darwin_cmpxchg_is_one_cas(success, failure, width, register, size):
    asm = emit_aarch64_darwin_asm(f'''target triple = "arm64-apple-darwin"
define {{i{width}, i1}} @probe(ptr %address) {{
entry:
  %pair = cmpxchg ptr %address, i{width} 5, i{width} 9 {success} {failure}
  ret {{i{width}, i1}} %pair
}}
''', optimize=False)
    body = _body(asm, "probe")
    mnemonic = "cas" + _CMPXCHG_MERGED[(success, failure)] + size
    assert re.search(rf"^  {mnemonic} {register}\w+, {register}11, \[x\w+\]$", body, re.M), body
    assert "clrex" not in body
    assert not re.search(r"^  (?:ldx|ldax|stx|stlx)r", body, re.M), body
    if width < 32:
        mask = "0xff" if width == 8 else "0xffff"
        assert re.search(rf"^  and w10, w10, #{mask}$", body, re.M), body


@pytest.mark.parametrize("op", sorted(_RMW))
def test_linux_keeps_exclusive_loops(op):
    asm = emit_aarch64_darwin_asm(f'''target triple = "aarch64-unknown-linux-gnu"
define i64 @probe(ptr %address, i64 %operand) {{
entry:
  %prior = atomicrmw {op} ptr %address, i64 %operand seq_cst
  %pair = cmpxchg ptr %address, i64 5, i64 9 seq_cst seq_cst
  ret i64 %prior
}}
''', optimize=False)
    body = asm.split("probe:", 1)[1]
    assert re.search(r"^  ldaxr x11, \[x\w+\]$", body, re.M), body
    assert re.search(r"^  stlxr w13, x12, \[x\w+\]$", body, re.M), body
    assert not re.search(r"^  (?:ldadd|ldclr|ldset|swp|cas)", body, re.M), body


def _single_thread_program() -> str:
    checks = []
    lines = [
        'target triple = "arm64-apple-darwin"',
        "@w64 = global i64 100, align 8",
        "@w32 = global i32 100, align 4",
        "@w16 = global i16 100, align 2",
        "@w8 = global i8 100, align 1",
        "define i32 @main() {",
        "entry:",
    ]
    # Each step: (name, op, operand, expected prior, expected stored after).
    for width, name in ((64, "w64"), (32, "w32"), (16, "w16"), (8, "w8")):
        mask = (1 << width) - 1
        value = 100
        steps = [("add", 7), ("sub", 9), ("and", 0x5A), ("or", 0x81), ("xchg", 33), ("sub", 40)]
        for index, (op, operand) in enumerate(steps):
            prior = value
            if op == "add":
                value = (value + operand) & mask
            elif op == "sub":
                value = (value - operand) & mask
            elif op == "and":
                value = value & operand
            elif op == "or":
                value = value | operand
            else:
                value = operand
            tag = f"{name}_{index}"
            lines.append(f"  %{tag} = atomicrmw {op} ptr @{name}, i{width} {operand} seq_cst")
            checks.append(f"  %ok_{tag} = icmp eq i{width} %{tag}, {prior if prior < (1 << (width - 1)) else prior - (1 << width)}")
        # A failing then a succeeding compare-exchange on the final value.
        final = value if value < (1 << (width - 1)) else value - (1 << width)
        lines.append(f"  %{name}_fail = cmpxchg ptr @{name}, i{width} 1, i{width} 2 seq_cst seq_cst")
        lines.append(f"  %{name}_fail_ok = extractvalue {{i{width}, i1}} %{name}_fail, 1")
        lines.append(f"  %{name}_fail_seen = extractvalue {{i{width}, i1}} %{name}_fail, 0")
        lines.append(f"  %{name}_hit = cmpxchg ptr @{name}, i{width} {final}, i{width} 3 acq_rel acquire")
        lines.append(f"  %{name}_hit_ok = extractvalue {{i{width}, i1}} %{name}_hit, 1")
        lines.append(f"  %{name}_after = load i{width}, ptr @{name}, align 1")
        checks.append(f"  %ok_{name}_fail = xor i1 %{name}_fail_ok, true")
        checks.append(f"  %ok_{name}_fail_seen = icmp eq i{width} %{name}_fail_seen, {final}")
        checks.append(f"  %ok_{name}_hit = and i1 %{name}_hit_ok, true")
        checks.append(f"  %ok_{name}_after = icmp eq i{width} %{name}_after, 3")
    lines.extend(checks)
    names = [line.split(" = ", 1)[0].strip() for line in checks]
    accumulator = names[0]
    for index, name in enumerate(names[1:]):
        lines.append(f"  %all{index} = and i1 {accumulator}, {name}")
        accumulator = f"%all{index}"
    lines.extend([
        f"  br i1 {accumulator}, label %good, label %bad",
        "good:",
        "  ret i32 0",
        "bad:",
        "  ret i32 3",
        "}",
    ])
    return "\n".join(lines) + "\n"


@_NATIVE
def test_lse_results_and_memory_match_the_operations(tmp_path):
    data = emit_owned_object(_single_thread_program(), "arm64-apple-darwin")
    executable = tmp_path / "lse-single"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")


_ITERATIONS = 500000
_THREADS = 4


def _contended_program() -> str:
    total = _ITERATIONS * _THREADS
    return f'''target triple = "arm64-apple-darwin"
@sum = global i64 0, align 8
@down = global i64 {total * 3}, align 8
@bits = global i64 0, align 8
@cas = global i64 0, align 8
declare i32 @pthread_create(ptr, ptr, ptr, ptr)
declare i32 @pthread_join(ptr, ptr)

define ptr @worker(ptr %arg) {{
entry:
  br label %loop
loop:
  %i = phi i64 [0, %entry], [%next, %step]
  %a = atomicrmw add ptr @sum, i64 1 monotonic
  %b = atomicrmw sub ptr @down, i64 3 acq_rel
  %bit = and i64 %i, 63
  %mask = shl i64 1, %bit
  %c = atomicrmw or ptr @bits, i64 %mask release
  %seen = load atomic i64, ptr @cas monotonic, align 8
  br label %cas_loop
cas_loop:
  %expected = phi i64 [%seen, %loop], [%observed, %cas_loop]
  %desired = add i64 %expected, 1
  %pair = cmpxchg ptr @cas, i64 %expected, i64 %desired seq_cst seq_cst
  %observed = extractvalue {{i64, i1}} %pair, 0
  %ok = extractvalue {{i64, i1}} %pair, 1
  br i1 %ok, label %step, label %cas_loop
step:
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, {_ITERATIONS}
  br i1 %done, label %exit, label %loop
exit:
  ret ptr null
}}

define i32 @main() {{
entry:
  %threads = alloca [{_THREADS} x ptr], align 8
  br label %spawn
spawn:
  %s = phi i64 [0, %entry], [%s_next, %spawned]
  %slot = getelementptr [{_THREADS} x ptr], ptr %threads, i64 0, i64 %s
  %created = call i32 @pthread_create(ptr %slot, ptr null, ptr @worker, ptr null)
  %create_ok = icmp eq i32 %created, 0
  br i1 %create_ok, label %spawned, label %bad
spawned:
  %s_next = add i64 %s, 1
  %spawn_done = icmp eq i64 %s_next, {_THREADS}
  br i1 %spawn_done, label %join, label %spawn
join:
  %j = phi i64 [0, %spawned], [%j_next, %joined]
  %join_slot = getelementptr [{_THREADS} x ptr], ptr %threads, i64 0, i64 %j
  %handle = load ptr, ptr %join_slot, align 8
  %join_status = call i32 @pthread_join(ptr %handle, ptr null)
  %join_ok = icmp eq i32 %join_status, 0
  br i1 %join_ok, label %joined, label %bad
joined:
  %j_next = add i64 %j, 1
  %join_done = icmp eq i64 %j_next, {_THREADS}
  br i1 %join_done, label %check, label %join
check:
  %sum_v = load atomic i64, ptr @sum acquire, align 8
  %down_v = load atomic i64, ptr @down acquire, align 8
  %bits_v = load atomic i64, ptr @bits acquire, align 8
  %cas_v = load atomic i64, ptr @cas acquire, align 8
  %ok1 = icmp eq i64 %sum_v, {total}
  %ok2 = icmp eq i64 %down_v, 0
  %ok3 = icmp eq i64 %bits_v, -1
  %ok4 = icmp eq i64 %cas_v, {total}
  %ok12 = and i1 %ok1, %ok2
  %ok34 = and i1 %ok3, %ok4
  %all = and i1 %ok12, %ok34
  br i1 %all, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 3
}}
'''


@_NATIVE
def test_lse_atomics_stay_atomic_across_threads(tmp_path):
    asm = emit_aarch64_darwin_asm(_contended_program(), optimize=True)
    worker = _body(asm, "worker")
    for needle in ("  ldadd x", "  ldaddal x", "  ldsetl x", "  casal x"):
        assert needle in worker, needle
    data = emit_owned_object(_contended_program(), "arm64-apple-darwin")
    executable = tmp_path / "lse-contended"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
