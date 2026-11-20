"""Owned jump threading and block merging (``jump-thread``).

Reference: LLVM ``JumpThreading.cpp`` (``ThreadEdge``: an edge whose branch
condition is known on that edge goes straight to the chosen successor) and
``BasicBlockUtils.cpp`` (``MergeBlockIntoPredecessor``). LLVM LangRef,
"phi instruction": one incoming entry per predecessor edge, each value
dominating the end of its predecessor.

A short-circuit ``and``/``or`` lowers to a phi of constants that an
inlined predicate returns through a zext and a single-entry phi before its
caller compares it with zero; the self backend materialized each such
boolean and branched on it again. The pass decides those branches on the
edges that supply constants and merges the straight-line remains.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.compiled_owned_passes import OWNED_PASS_NAMES, run_owned_passes
from pcc.ir.optimization.ir_mutator import MutableModule
from pcc.ir.optimization.jump_thread import jump_thread_text

_LABEL_RE = re.compile(r"label\s+%([\w.$-]+)")
_PHI_LABEL_RE = re.compile(r"\[\s*[^,\]]+?\s*,\s*%([\w.$-]+)\s*\]")


def _verify(text: str) -> None:
    """The self backend's IR verifier (SSA dominance, phi edges, types)."""
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module

    if "target triple" not in text:
        text = 'target triple = "arm64-apple-darwin"\n' + text
    verify_parsed_module(parse_self_backend_module(text))


def _check_phi_entries(text: str) -> None:
    """Every phi has exactly one entry per predecessor edge, and the
    module passes the self backend's verifier."""
    _verify(text)
    for fn in MutableModule.parse(text).functions:
        preds: dict[str, list[str]] = {block.name: [] for block in fn.blocks}
        for block in fn.blocks:
            for target in _LABEL_RE.findall(block.terminator.text):
                preds[target].append(block.name)
        for block in fn.blocks:
            for inst in block.instructions:
                if inst.opcode != "phi":
                    continue
                labels = _PHI_LABEL_RE.findall(inst.text)
                assert sorted(labels) == sorted(preds[block.name]), (fn.name, block.name, inst.text)


def _function(text: str, name: str) -> str:
    match = re.search(r"^define[^\n]*@" + re.escape(name) + r"\(.*?^}", text, re.M | re.S)
    assert match is not None, name
    return match.group(0)


SHORT_CIRCUIT = """define i64 @f(i64 %a, i64 %b) {
entry:
  %c1 = icmp ne i64 %a, 0
  br i1 %c1, label %rhs, label %bool.short
rhs:
  %c2 = icmp sgt i64 %b, 5
  br label %bool.end
bool.short:
  br label %bool.end
bool.end:
  %and = phi i1 [ %c2, %rhs ], [ 0, %bool.short ]
  %w = zext i1 %and to i64
  br label %cont
cont:
  %ret = phi i64 [ %w, %bool.end ]
  %t = icmp ne i64 %ret, 0
  br i1 %t, label %yes, label %no
yes:
  ret i64 1
no:
  %r = phi i64 [ 0, %cont ]
  ret i64 %r
}
"""


def test_short_circuit_constant_edge_branches_to_its_decision():
    out, changed = jump_thread_text(SHORT_CIRCUIT)
    assert changed
    _check_phi_entries(out)
    # The left operand's false edge decides the whole condition.
    assert "br i1 %c1, label %rhs, label %no" in out
    assert "phi i1" not in out
    # The forwarders and the boolean merge are gone.
    for gone in ("bool.short:", "bool.end:", "cont:"):
        assert gone not in out
    assert "%r = phi i64 [0, %rhs], [0, %entry]" in out


def test_nested_short_circuit_threads_through_the_inner_merge():
    text = """define i64 @g(i64 %a, i64 %b, i64 %c) {
entry:
  %c1 = icmp ne i64 %a, 0
  br i1 %c1, label %rhs1, label %short1
rhs1:
  %c2 = icmp ne i64 %b, 0
  br i1 %c2, label %rhs2, label %short2
rhs2:
  %c3 = icmp ne i64 %c, 0
  br label %end2
short2:
  br label %end2
end2:
  %in = phi i1 [ %c3, %rhs2 ], [ 0, %short2 ]
  br label %end1
short1:
  br label %end1
end1:
  %out = phi i1 [ %in, %end2 ], [ 0, %short1 ]
  br i1 %out, label %yes, label %no
yes:
  ret i64 7
no:
  ret i64 3
}
"""
    out, changed = jump_thread_text(text)
    assert changed
    _check_phi_entries(out)
    assert "br i1 %c1, label %rhs1, label %no" in out
    assert "br i1 %c2, label %rhs2, label %no" in out
    assert "br i1 %c3, label %yes, label %no" in out
    assert "phi i1" not in out


def test_unknown_incoming_values_keep_the_branch():
    text = """define i64 @h(i1 %x, i1 %y, i1 %s) {
entry:
  br i1 %s, label %left, label %right
left:
  br label %join
right:
  br label %join
join:
  %p = phi i1 [ %x, %left ], [ %y, %right ]
  br i1 %p, label %yes, label %no
yes:
  ret i64 1
no:
  ret i64 0
}
"""
    out, _changed = jump_thread_text(text)
    _check_phi_entries(out)
    assert "%p = phi i1" in out
    assert "br i1 %p, label %yes, label %no" in out


def test_side_effects_in_the_decided_block_stop_threading():
    for effect in ("  call void @effect()\n", "  %l = load i64, ptr %q\n",
                   "  store i64 1, ptr %q\n"):
        text = ("declare void @effect()\n"
                "define i64 @k(i1 %x, ptr %q) {\n"
                "entry:\n"
                "  br i1 %x, label %one, label %two\n"
                "one:\n"
                "  %u = add i64 0, 0\n"
                "  br label %join\n"
                "two:\n"
                "  %v = add i64 0, 0\n"
                "  br label %join\n"
                "join:\n"
                "  %p = phi i1 [ 1, %one ], [ 0, %two ]\n"
                + effect +
                "  br i1 %p, label %yes, label %no\n"
                "yes:\n"
                "  ret i64 1\n"
                "no:\n"
                "  ret i64 0\n"
                "}\n")
        out, _changed = jump_thread_text(text)
        _check_phi_entries(out)
        assert "br i1 %p, label %yes, label %no" in _function(out, "k"), effect


def test_a_value_used_past_the_successor_phis_stops_threading():
    text = """define i64 @m(i1 %x, i64 %n) {
entry:
  br i1 %x, label %one, label %two
one:
  %u = add i64 %n, 1
  br label %join
two:
  %v = add i64 %n, 2
  br label %join
join:
  %p = phi i1 [ 1, %one ], [ 0, %two ]
  %wide = zext i1 %p to i64
  br i1 %p, label %yes, label %no
yes:
  ret i64 %wide
no:
  ret i64 0
}
"""
    out, _changed = jump_thread_text(text)
    _check_phi_entries(out)
    assert "br i1 %p, label %yes, label %no" in out
    assert "ret i64 %wide" in out


SUCCESSOR_PHI = """define i64 @n(i1 %x, i64 %a) {
entry:
  br i1 %x, label %one, label %two
one:
  %u = add i64 %a, 1
  br label %join
two:
  %v = add i64 %a, 2
  br label %join
join:
  %p = phi i1 [ 1, %one ], [ 0, %two ]
  %k = phi i64 [ 10, %one ], [ 20, %two ]
  %k2 = add i64 %k, 5
  br i1 %p, label %yes, label %no
yes:
  %r = phi i64 [ %k2, %join ]
  ret i64 %r
no:
  %q = phi i64 [ %k, %join ]
  ret i64 %q
}
"""


def _check_threaded_successor_phis(text):
    _check_phi_entries(text)
    function = MutableModule.parse(text).function("n")
    # The join and its successor PHIs merge into the two predecessor paths.
    assert {block.name: block.terminator.text.strip() for block in function.blocks} == {
        "entry": "br i1 %x, label %one, label %two",
        "one": "ret i64 15",  # 10 + 5 on the true edge
        "two": "ret i64 20",  # 20 on the false edge
    }


def test_successor_phis_take_the_value_of_the_threaded_path():
    out, changed = jump_thread_text(SUCCESSOR_PHI)
    assert changed
    _check_threaded_successor_phis(out)


def _successor_phi_driver():
    return f'''from pcc.ir.optimization.jump_thread import jump_thread_text


def main() -> None:
    rewritten, changed = jump_thread_text("""{SUCCESSOR_PHI}""")
    assert changed
    print(rewritten)


main()
'''


@pytest.mark.integration
@pytest.mark.pcc_gate(
    platforms=("linux",), machines=("x86_64",),
    probe=lambda: not os.environ.get("PCC_TEST_COMPILER"),
)
def test_named_jump_thread_executes_owned_optimizer(tmp_path, pcc_runtime_archive):
    """pcc0 builds the actual optimizer and siblings as one native executable."""
    from pcc.backend.elf_x86_64 import parse_static_executable
    from pcc.frontends.python.pipeline import compile_python_multi

    root = Path(__file__).resolve().parents[2]
    driver = tmp_path / "jump_thread_driver.py"
    driver.write_text(_successor_phi_driver(), encoding="utf-8")
    reference = subprocess.run(
        [sys.executable, "-B", str(driver)],
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, timeout=10,
    )
    assert reference.returncode == 0 and not reference.stderr, reference.stderr

    modules = ["pcc.ir.optimization." + name for name in ("jump_thread", "ir_mutator", "text_tokens")]
    paths = [str(driver), *(str(root / (name.replace(".", "/") + ".py")) for name in modules)]
    binary = tmp_path / "jump_thread_driver"
    target = "x86_64-unknown-linux-gnu"
    profile = {}
    compile_python_multi(
        paths, str(binary), module_names=[driver.stem, *modules], entry_module=driver.stem,
        target_triple=target, runtime_archive=str(pcc_runtime_archive), profile=profile,
    )
    assert profile["counters"]["multi_files"] == 4
    parse_static_executable(binary.read_bytes())
    actual = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, reference.stdout, b"")
    _check_threaded_successor_phis(f'target triple = "{target}"\n' + actual.stdout.decode("utf-8"))


def test_a_predecessor_already_reaching_the_decision_is_not_threaded():
    text = """define i64 @d(i1 %x, i1 %y) {
entry:
  br i1 %x, label %join, label %yes
join:
  %p = phi i1 [ 1, %entry ], [ 0, %other ]
  br i1 %p, label %yes, label %no
other:
  br label %join
yes:
  %r = phi i64 [ 1, %entry ], [ 2, %join ]
  ret i64 %r
no:
  ret i64 0
}
"""
    out, _changed = jump_thread_text(text)
    _check_phi_entries(out)
    # entry already branches to yes: threading entry->join->yes would give
    # yes two entries for entry.
    assert "br i1 %x, label %join, label %yes" in out


def test_a_loop_header_carrying_its_own_value_is_not_threaded():
    # The runtime's py_gc_track shape: the header's phi keeps its value on
    # the latch edge. Threading entry (0) and the body (1) past the header
    # would leave the latch's use of %acq without a dominating definition.
    text = """define i64 @spin(ptr %lock) {
entry:
  br label %cond
cond:
  %acq = phi i64 [ 0, %entry ], [ %acq, %retry ], [ 1, %body ]
  %done = icmp ne i64 %acq, 0
  br i1 %done, label %out, label %body
body:
  %v = load i64, ptr %lock
  %got = icmp eq i64 %v, 0
  br i1 %got, label %cond, label %retry
retry:
  br label %cond
out:
  ret i64 %acq
}
"""
    _verify(text)
    out, _changed = jump_thread_text(text)
    _check_phi_entries(out)
    assert "%acq = phi i64" in out


def test_an_entry_decided_never_to_enter_a_loop_drops_the_loop():
    text = """define i64 @never(ptr %p, i64 %n) {
entry:
  br label %head
head:
  %go = phi i1 [ 0, %entry ], [ %again, %body ]
  br i1 %go, label %body, label %out
body:
  %v = load i64, ptr %p
  %again = icmp slt i64 %v, %n
  br label %head
out:
  ret i64 7
}
"""
    _verify(text)
    out, changed = jump_thread_text(text)
    assert changed
    _check_phi_entries(out)
    # entry branches past the loop; the loop is left unreachable and goes,
    # and the exit merges into entry.
    assert "body:" not in out and "head:" not in out and "out:" not in out
    assert "entry:\n  ret i64 7\n" in out


def test_a_phi_it_cannot_parse_leaves_the_function_unchanged():
    # A constant expression with commas is not split into entries.
    text = SHORT_CIRCUIT.replace(
        "  %r = phi i64 [ 0, %cont ]\n",
        "  %r = phi i64 [ ptrtoint (ptr getelementptr (i8, ptr null, i64 8) to i64), %cont ]\n",
    )
    out, changed = jump_thread_text(text)
    assert not changed and out == text


def test_jump_thread_is_an_owned_pass_name():
    assert "jump-thread" in OWNED_PASS_NAMES
    out = run_owned_passes(SHORT_CIRCUIT, ["jump-thread", "instsimplify", "instcombine", "dce"])
    _check_phi_entries(out)
    assert "phi i1" not in out


def _predicate_module() -> str:
    # A nested and/or predicate returned as i64, compared with zero by the
    # caller (the inlined runtime-helper shape), evaluated over a grid.
    return """target triple = "arm64-apple-darwin"
define internal i64 @pred(i64 %a, i64 %b, i64 %c) {
entry:
  %c1 = icmp sgt i64 %a, 1
  br i1 %c1, label %rhs1, label %short1
rhs1:
  %c2 = icmp slt i64 %b, 3
  br i1 %c2, label %short2, label %rhs2
rhs2:
  %c3 = icmp eq i64 %c, 2
  br label %end2
short2:
  br label %end2
end2:
  %or = phi i1 [ %c3, %rhs2 ], [ 1, %short2 ]
  br label %end1
short1:
  br label %end1
end1:
  %and = phi i1 [ %or, %end2 ], [ 0, %short1 ]
  %w = zext i1 %and to i64
  ret i64 %w
}
define i32 @main() {
entry:
  br label %la
la:
  %a = phi i64 [ 0, %entry ], [ %a1, %la.next ]
  %acc = phi i64 [ 0, %entry ], [ %acc3, %la.next ]
  br label %lb
lb:
  %b = phi i64 [ 0, %la ], [ %b1, %lb.next ]
  %acc1 = phi i64 [ %acc, %la ], [ %acc2, %lb.next ]
  br label %lc
lc:
  %c = phi i64 [ 0, %lb ], [ %c1, %lc.join ]
  %accc = phi i64 [ %acc1, %lb ], [ %accn, %lc.join ]
  %r = call i64 @pred(i64 %a, i64 %b, i64 %c)
  %t = icmp ne i64 %r, 0
  br i1 %t, label %lc.hit, label %lc.miss
lc.hit:
  %mix = mul i64 %a, 9
  %mix2 = add i64 %mix, %b
  %mix3 = mul i64 %mix2, 5
  %mix4 = add i64 %mix3, %c
  %hit = add i64 %accc, %mix4
  br label %lc.join
lc.miss:
  %miss = add i64 %accc, 1
  br label %lc.join
lc.join:
  %accn = phi i64 [ %hit, %lc.hit ], [ %miss, %lc.miss ]
  %c1 = add i64 %c, 1
  %cdone = icmp eq i64 %c1, 5
  br i1 %cdone, label %lb.next, label %lc
lb.next:
  %acc2 = phi i64 [ %accn, %lc.join ]
  %b1 = add i64 %b, 1
  %bdone = icmp eq i64 %b1, 5
  br i1 %bdone, label %la.next, label %lb
la.next:
  %acc3 = phi i64 [ %acc2, %lb.next ]
  %a1 = add i64 %a, 1
  %adone = icmp eq i64 %a1, 5
  br i1 %adone, label %out, label %la
out:
  %res = phi i64 [ %acc3, %la.next ]
  %tr = trunc i64 %res to i32
  %m = and i32 %tr, 255
  ret i32 %m
}
"""


def _expected_predicate_checksum() -> int:
    acc = 0
    for a in range(5):
        for b in range(5):
            for c in range(5):
                if a > 1 and (b < 3 or c == 2):
                    acc += (a * 9 + b) * 5 + c
                else:
                    acc += 1
    return acc & 255


def test_inlined_predicate_loses_its_boolean_materialization():
    passes = ["inline-defined-wide", "jump-thread", "instsimplify", "instcombine", "dce"]
    out = run_owned_passes(_predicate_module(), passes)
    _check_phi_entries(out)
    main = _function(out, "main")
    assert "call i64 @pred" not in main
    assert "phi i1" not in main
    assert "zext i1" not in main


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("passes", [
    ["inline-defined-wide", "instsimplify", "instcombine", "dce"],
    ["inline-defined-wide", "jump-thread", "instsimplify", "instcombine", "dce"],
], ids=["without", "with"])
def test_threaded_predicate_module_runs(tmp_path, passes):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.owned_object_emit import emit_owned_object

    text = run_owned_passes(_predicate_module(), passes)
    executable = tmp_path / "threaded"
    executable.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                           entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, timeout=30)
    assert ran.returncode == _expected_predicate_checksum()
