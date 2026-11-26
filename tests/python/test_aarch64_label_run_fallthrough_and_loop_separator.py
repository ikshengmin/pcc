"""Two late AArch64 cleanups that remove executed instructions.

1. ``b L_x`` is dropped when ``L_x`` labels the next instruction, also when
   other plain local labels stand between the branch and ``L_x``. Labels emit
   no code, so the branch jumps to the next instruction. Stack-map labels,
   function labels, directives and instructions still block the rewrite.
2. The stack-map planner puts a ``nop`` before every loop safepoint label so
   that the label never shares a PC with an earlier record (a call's return
   address). When an ordinary instruction precedes that ``nop``, the label
   already has its own PC, and the ``nop`` only costs one instruction on every
   backedge; it is dropped. After a label, a directive or a call it stays.

The text passes, the typed record passes and the native fragment rewrite
must agree; the native emission must equal the assembled text; a loop runs.
P0 exact ranking (docs/status.md): executed ``nop`` was 4.9% and
unconditional branches 5.4% of the self backend's excess over clang.
"""
import subprocess

import pytest

from pcc.backend import self_backend_aarch64_darwin as reference
from pcc.backend import self_backend_aarch64_fragments as fragments
from pcc.backend import self_backend_aarch64_typed_cfg as typed
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_asm,
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_aarch64_semantics import TOKEN_SYMBOL
from pcc.backend.self_backend_parse import parse_self_backend_module


class Events:
    """Typed-pass fixture: provenance supplied directly, never parsed."""
    # The CFG helpers cache decoded records below this ID; the model has none.
    decoded_limit = 0

    def __init__(self):
        self.names = []
        self.events = []

    def intern(self, name):
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name)

    def symbol(self, symbol):
        return self.names[symbol]

    def add(self, text, opcode="", operands=(), label=None, directive=False, empty=False):
        self.events.append((text, opcode, list(operands), label, directive, empty))
        return len(self.events) - 1

    def branch(self, name):
        return self.add("  b " + name, "b", [(TOKEN_SYMBOL, self.intern(name), 0, 0)])

    def insn(self, text):
        return self.add(text, text.split()[0])

    def mark(self, name):
        return self.add(name + ":", label=self.intern(name))

    def opcode(self, record):
        return self.events[record][1]

    def operands(self, record):
        return list(self.events[record][2])

    def label(self, record):
        return self.events[record][3]

    def is_directive(self, record):
        return self.events[record][4]

    def is_empty(self, record):
        return self.events[record][5]

    def text(self, records):
        return [self.events[record][0] for record in records]


def _events(ctx, lines):
    records = []
    for line in lines:
        if not line:
            records.append(ctx.add("", empty=True))
        elif line.endswith(":"):
            records.append(ctx.mark(line[:-1]))
        elif line.startswith(".") or line.startswith("  ."):
            records.append(ctx.add(line, directive=True))
        elif line.startswith("  b ") and not line.startswith("  b."):
            records.append(ctx.branch(line[4:]))
        else:
            records.append(ctx.insn(line))
    return records


FALLTHROUGH = [
    # (lines, branch dropped)
    (["  b L_target", "L_target:", "  ret"], True),
    (["  b L_target", "L_other:", "L_target:", "  ret"], True),
    (["  b L_target", "", "L_a:", "", "L_b:", "L_target:", "  ret"], True),
    (["  b L_target", "L_pcc_smap_1_2:", "L_target:", "  ret"], False),
    (["  b L_target", "_global:", "L_target:", "  ret"], False),
    (["  b L_target", ".p2align 2", "L_target:", "  ret"], False),
    (["  b L_target", "L_a:", "  nop", "L_target:", "  ret"], False),
    (["  b L_target", "L_a:", "L_b:", "  ret", "L_target:"], False),
    (["  b _external", "L_a:", "_external:", "  ret"], False),
]


@pytest.mark.parametrize("lines,dropped", FALLTHROUGH)
def test_fallthrough_crosses_plain_local_labels_only(lines, dropped):
    expected = lines[1:] if dropped else lines
    assert reference._drop_fallthrough_uncond_branches(list(lines)) == expected
    ctx = Events()
    records = _events(ctx, lines)
    assert ctx.text(typed.drop_fallthrough_uncond_branches_records(ctx, records)) == expected


def _native(owner, span, lines):
    for line in lines:
        if not line:
            continue
        if line.endswith(":"):
            owner.append_label(span, line[:-1])
        elif line == "  nop":
            owner.append_nop(span)
        elif line.startswith("  b "):
            owner.append_branch(span, "b", line[4:])
        elif line == "  ret":
            owner.append_word(span, 0xD65F03C0, fragments.EMITTED_INSTRUCTION_SCALAR)
        else:
            raise AssertionError(line)


def _read(owner, span):
    result = []
    owner.start_cursor(span)
    record_id = owner.next_record_id()
    while record_id >= 0:
        result.append(record_id)
        record_id = owner.next_record_id()
    return result


@pytest.mark.parametrize("lines,dropped", [
    case for case in FALLTHROUGH
    if not any(line.startswith(".") or line.startswith("_") for line in case[0])
])
def test_native_fallthrough_rewrite_agrees(lines, dropped):
    owner = fragments.AArch64EmissionFragments()
    try:
        span = owner.new_fragment()
        _native(owner, span, [line for line in lines if line])
        before = _read(owner, span)
        after = _read(owner, owner.drop_fallthrough_branches(span))
        assert after == (before[1:] if dropped else before)
        assert _read(owner, span) == before
    finally:
        owner.close()


SEPARATOR = [
    # (lines, separator dropped)
    (["  add x4, x4, #8", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], True),
    (["  cmp x2, x0", "", "  nop", "", "L_pcc_smap_ab_1:", "  b.eq L_exit"], True),
    (["  bl _f", "L_pcc_smap_ab_3:", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], False),
    (["  bl _f", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], False),
    (["  blr x9", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], False),
    (["L_block:", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], False),
    (["  .loc 1 2 3", "  nop", "L_pcc_smap_ab_4:", "  b L_loop"], False),
    (["  add x4, x4, #8", "  nop", "L_block:", "  b L_loop"], False),
    (["  add x4, x4, #8", "  nop", "L_pcc_smap_end_ab:"], False),
    (["  add x4, x4, #8", "  nop", "  b L_loop"], False),
]


@pytest.mark.parametrize("lines,dropped", SEPARATOR)
def test_loop_separator_nop_drops_only_after_an_ordinary_instruction(lines, dropped):
    expected = [line for line in lines if line != "  nop"] if dropped else lines
    assert reference._drop_loop_separator_nops(list(lines)) == expected
    ctx = Events()
    records = _events(ctx, lines)
    assert ctx.text(typed.drop_loop_separator_nops_records(ctx, records)) == expected


LOOPS = '''target triple = "arm64-apple-darwin"
@ticks = global i64 0, align 8
define void @tick() {
entry:
  %t = load i64, ptr @ticks, align 8
  %u = add i64 %t, 1
  store i64 %u, ptr @ticks, align 8
  ret void
}
define i64 @sum(i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %next, %loop ]
  %acc = phi i64 [ 0, %entry ], [ %acc2, %loop ]
  %acc2 = add i64 %acc, %i
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, %n
  br i1 %done, label %exit, label %loop
exit:
  ret i64 %acc2
}
define i64 @calls(i64 %n) {
entry:
  br label %loop
loop:
  %i = phi i64 [ 0, %entry ], [ %next, %latch ]
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, %n
  br i1 %done, label %exit, label %latch
latch:
  call void @tick()
  br label %loop
exit:
  ret i64 %next
}
define i32 @main() {
entry:
  %s = call i64 @sum(i64 10)
  %c = call i64 @calls(i64 6)
  %t = load i64, ptr @ticks, align 8
  %ok_s = icmp eq i64 %s, 45
  %ok_c = icmp eq i64 %c, 6
  %ok_t = icmp eq i64 %t, 5
  %ab = and i1 %ok_s, %ok_c
  %all = and i1 %ab, %ok_t
  %status = select i1 %all, i32 0, i32 41
  ret i32 %status
}
'''


def _body(assembly, name):
    lines = assembly.split("\n")
    start = lines.index("_" + name + ":")
    end = start + 1
    while end < len(lines) and not lines[end].startswith(".p2align") and not lines[end].startswith(".section"):
        end += 1
    return lines[start:end]


def test_simple_latch_loses_its_separator_and_call_latch_keeps_it():
    assembly = emit_aarch64_darwin_asm(LOOPS, optimize=True)
    sum_body = _body(assembly, "sum")
    assert any(line.startswith("L_pcc_smap_") for line in sum_body[2:]), sum_body
    assert "  nop" not in sum_body, sum_body
    calls_body = _body(assembly, "calls")
    position = calls_body.index("  nop")
    assert calls_body[position - 1].startswith("L_pcc_smap_"), calls_body
    assert calls_body[position + 1].startswith("L_pcc_smap_"), calls_body


@pytest.mark.parametrize("target", ("arm64-apple-darwin", "aarch64-unknown-linux-gnu"))
def test_native_sections_equal_the_assembled_text(target):
    source = LOOPS.replace('"arm64-apple-darwin"', '"' + target + '"')
    assembly = emit_aarch64_darwin_indexed_module(parse_self_backend_module(source), optimize=True)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(source), optimize=True, structured_instructions=True,
    )
    assert transport.fallback_instruction_count == 0
    assert transport.assemble_sections() == assemble_file(assembly)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_loops_execute(tmp_path):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(LOOPS), optimize=True, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "loops"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
