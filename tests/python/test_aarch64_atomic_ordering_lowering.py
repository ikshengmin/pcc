"""AArch64 LL/SC loops carry exactly the IR-requested memory ordering.

The mapping is the C/C++11 -> ARMv8 one LLVM's AtomicExpand uses without LSE:
acquire-or-stronger orderings take LDAXR, release-or-stronger ones take STLXR,
and monotonic needs only the exclusive monitor. cmpxchg uses one loop for both
outcomes, so its load also meets the failure ordering (getMergedOrdering).
The threaded program is emitted and linked by pcc's owned object writer and
linker; it proves the weaker forms still update atomically under contention.
"""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


_RMW_PAIRS = {
    "monotonic": ("ldxr", "stxr"),
    "acquire": ("ldaxr", "stxr"),
    "release": ("ldxr", "stlxr"),
    "acq_rel": ("ldaxr", "stlxr"),
    "seq_cst": ("ldaxr", "stlxr"),
}

# (success, failure) -> merged ordering of the single LL/SC loop.
_CMPXCHG_MERGED = {
    ("monotonic", "monotonic"): "monotonic",
    ("monotonic", "acquire"): "acquire",
    ("monotonic", "seq_cst"): "seq_cst",
    ("acquire", "monotonic"): "acquire",
    ("acquire", "acquire"): "acquire",
    ("release", "monotonic"): "release",
    ("release", "acquire"): "acq_rel",
    ("acq_rel", "monotonic"): "acq_rel",
    ("acq_rel", "acquire"): "acq_rel",
    ("seq_cst", "monotonic"): "seq_cst",
    ("seq_cst", "seq_cst"): "seq_cst",
}


def _body(text, name):
    return text.split("_" + name + ":", 1)[1].split(".section", 1)[0]


@pytest.mark.parametrize("width, register, suffix", [
    (8, "w", "b"), (16, "w", "h"), (32, "w", ""), (64, "x", ""),
])
@pytest.mark.parametrize("ordering", sorted(_RMW_PAIRS))
def test_rmw_exclusive_pair_follows_ordering(width, register, suffix, ordering):
    asm = emit_aarch64_darwin_asm(f'''target triple = "arm64-apple-darwin"
define i{width} @probe(ptr %address) {{
entry:
  %prior = atomicrmw add ptr %address, i{width} 3 {ordering}
  ret i{width} %prior
}}
''', optimize=False)
    body = _body(asm, "probe")
    load_op, store_op = _RMW_PAIRS[ordering]
    assert re.search(rf"^  {load_op}{suffix} {register}11, \[x\d+\]$", body, re.M), body
    assert re.search(rf"^  {store_op}{suffix} w13, {register}12, \[x\d+\]$", body, re.M), body
    exclusives = re.findall(r"^  (?:ldx|ldax|stx|stlx)r[bh]? ", body, re.M)
    assert len(exclusives) == 2, body


@pytest.mark.parametrize("success, failure", sorted(_CMPXCHG_MERGED))
def test_cmpxchg_exclusive_pair_uses_merged_ordering(success, failure):
    asm = emit_aarch64_darwin_asm(f'''target triple = "arm64-apple-darwin"
define {{i64, i1}} @probe(ptr %address) {{
entry:
  %pair = cmpxchg ptr %address, i64 5, i64 9 {success} {failure}
  ret {{i64, i1}} %pair
}}
''', optimize=False)
    body = _body(asm, "probe")
    load_op, store_op = _RMW_PAIRS[_CMPXCHG_MERGED[(success, failure)]]
    assert re.search(rf"^  {load_op} x12, \[x\d+\]$", body, re.M), body
    assert re.search(rf"^  {store_op} w13, x11, \[x\d+\]$", body, re.M), body
    assert "  clrex" in body


_ITERATIONS = 1000000
_THREADS = 4
_DOWN_START = 1 << 40


def _contended_program():
    total = _ITERATIONS * _THREADS
    return f'''target triple = "arm64-apple-darwin"
@counter = global i64 0, align 8
@acquired = global i64 0, align 8
@narrow8 = global i8 0, align 1
@narrow16 = global i16 0, align 2
@down = global i64 {_DOWN_START}, align 8
@up = global i64 0, align 8
declare i32 @pthread_create(ptr, ptr, ptr, ptr)
declare i32 @pthread_join(ptr, ptr)

define ptr @worker(ptr %arg) {{
entry:
  br label %loop
loop:
  %i = phi i64 [0, %entry], [%next, %step]
  %a = atomicrmw add ptr @counter, i64 1 monotonic
  %b = atomicrmw add ptr @acquired, i64 1 acquire
  %c = atomicrmw add ptr @narrow8, i8 1 monotonic
  %d = atomicrmw add ptr @narrow16, i16 1 release
  %seen = load atomic i64, ptr @down monotonic, align 8
  br label %down_cas
down_cas:
  %expected = phi i64 [%seen, %loop], [%observed, %down_cas]
  %desired = sub i64 %expected, 1
  %pair = cmpxchg ptr @down, i64 %expected, i64 %desired release monotonic
  %observed = extractvalue {{i64, i1}} %pair, 0
  %ok = extractvalue {{i64, i1}} %pair, 1
  br i1 %ok, label %up_start, label %down_cas
up_start:
  %up_seen = load atomic i64, ptr @up monotonic, align 8
  br label %up_cas
up_cas:
  %up_expected = phi i64 [%up_seen, %up_start], [%up_observed, %up_cas]
  %up_desired = add i64 %up_expected, 1
  %up_pair = cmpxchg ptr @up, i64 %up_expected, i64 %up_desired monotonic acquire
  %up_observed = extractvalue {{i64, i1}} %up_pair, 0
  %up_ok = extractvalue {{i64, i1}} %up_pair, 1
  br i1 %up_ok, label %step, label %up_cas
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
  %counter_v = load atomic i64, ptr @counter acquire, align 8
  %acquired_v = load atomic i64, ptr @acquired acquire, align 8
  %narrow8_v = load atomic i8, ptr @narrow8 acquire, align 1
  %narrow16_v = load atomic i16, ptr @narrow16 acquire, align 2
  %down_v = load atomic i64, ptr @down acquire, align 8
  %up_v = load atomic i64, ptr @up acquire, align 8
  %ok1 = icmp eq i64 %counter_v, {total}
  %ok2 = icmp eq i64 %acquired_v, {total}
  %ok3 = icmp eq i8 %narrow8_v, {total % 256}
  %ok4 = icmp eq i16 %narrow16_v, {total % 65536}
  %ok5 = icmp eq i64 %down_v, {_DOWN_START - total}
  %ok6 = icmp eq i64 %up_v, {total}
  %ok12 = and i1 %ok1, %ok2
  %ok34 = and i1 %ok3, %ok4
  %ok56 = and i1 %ok5, %ok6
  %ok14 = and i1 %ok12, %ok34
  %all = and i1 %ok14, %ok56
  br i1 %all, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 3
}}
'''


def test_contended_program_emits_the_weaker_exclusive_forms():
    body = _body(emit_aarch64_darwin_asm(_contended_program(), optimize=False), "worker")
    for needle in ("  ldxr x", "  stxr w13, x", "  ldaxr x", "  stlxr w13, x",
                   "  ldxrb w", "  stxrb w13, w", "  ldxrh w", "  stlxrh w13, w"):
        assert needle in body, needle


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
def test_weaker_exclusive_forms_stay_atomic_across_threads(tmp_path):
    data = emit_owned_object(_contended_program(), "arm64-apple-darwin")
    executable = tmp_path / "contended-atomics"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
