"""x86-64 reads a copied register's original and drops moves nothing reads.

``mov r10, rbx`` / ``cmp r10, 0`` compares rbx directly, after which the
move is dead and dropped. Instructions outside the modeled set are barriers,
labels end forwarding, and rsp/rbp are never forwarded or dropped.
"""
from __future__ import annotations

import pytest

from itertools import product
from random import Random

from pcc.backend.x86_64_register_copies import forward_register_copies
from pcc.backend import x86_64_register_copies as copies
from tests.x86_64_dead_moves_reference import _dead_moves as reference_dead_moves


def test_operands_read_the_original_and_dead_copies_go():
    assert forward_register_copies([
        "  mov r10, rbx",
        "  cmp r10, 0",
        "  mov r11, r10",
        "  mov rdi, r11",
        "  mov rsi, QWORD PTR [r10 + 8]",
        "  call g",
        "  mov r10, rax",
        "  mov r12, r10",
        "  ret",
    ]) == [
        "  cmp rbx, 0",
        "  mov rdi, rbx",
        "  mov rsi, QWORD PTR [rbx + 8]",
        "  call g",
        "  mov r12, rax",
        "  ret",
    ]


def test_narrow_copies_forward_only_at_their_width():
    assert forward_register_copies([
        "  mov r10b, bl",
        "  test r10b, r10b",
        "  mov r11d, r12d",
        "  mov rdi, r11",
        "  mov ecx, r11d",
        "  call g",
        "  ret",
    ]) == [
        "  test bl, bl",
        "  mov r11d, r12d",
        "  mov rdi, r11",
        "  mov ecx, r12d",
        "  call g",
        "  ret",
    ]


def test_flags_results_nothing_reads_go():
    assert forward_register_copies([
        "  cmp rax, 0", "  setne r11b", "  je .Lf_x", "  mov rax, 1", ".Lf_x:", "  ret",
    ]) == ["  cmp rax, 0", "  je .Lf_x", "  mov rax, 1", ".Lf_x:", "  ret"]
    # al is part of the returned rax.
    lines = ["  cmp rdi, 0", "  setne al", "  je .Lf_x", "  mov rax, 1", ".Lf_x:", "  ret"]
    assert forward_register_copies(lines) == lines


def test_moves_that_only_feed_themselves_around_a_loop_go():
    # Each byte write merges into rax, which only the next iteration's byte
    # write reads; nothing reads al or rax before the exit writes eax. (A
    # call would read al: it may be variadic.)
    assert forward_register_copies([
        ".Lf_loop:",
        "  mov al, r13b",
        "  test r13b, r13b",
        "  je .Lf_done",
        "  add r12d, 1",
        "  jmp .Lf_loop",
        ".Lf_done:",
        "  mov eax, r12d",
        "  ret",
    ]) == [
        ".Lf_loop:",
        "  test r13b, r13b",
        "  je .Lf_done",
        "  add r12d, 1",
        "  jmp .Lf_loop",
        ".Lf_done:",
        "  mov eax, r12d",
        "  ret",
    ]


def test_self_moves_go_except_the_zero_extending_one():
    assert forward_register_copies([
        "  mov r10, rbx", "  mov rbx, r10", "  mov r13d, r13d", "  mov rdi, r13", "  call g", "  ret",
    ]) == ["  mov r13d, r13d", "  mov rdi, r13", "  call g", "  ret"]


def test_a_call_reads_rax_only_as_a_variadic_count():
    # A byte result in al that no call or later read uses is dropped; the
    # variadic vector count stays even with nothing else reading al.
    assert forward_register_copies([
        "  cmp rdi, 0", "  setne al", "  call g", "  mov rdi, rax", "  mov al, 1",
        "  mov r11, rbx", "  call r11", "  call h", "  ret",
    ]) == [
        "  cmp rdi, 0", "  call g", "  mov rdi, rax", "  mov al, 1", "  mov r11, rbx", "  call r11",
        "  call h", "  ret",
    ]


def test_the_win64_stack_probe_reads_its_size_from_rax():
    lines = ["  push rbp", "  mov rbp, rsp", "  mov eax, 8192", "  call __pcc_chkstk",
             "  sub rsp, rax", "  .seh_stackalloc 8192", "  mov rax, rbx", "  ret"]
    assert forward_register_copies(lines) == lines


def test_scratch_registers_are_dead_at_a_return():
    assert forward_register_copies([
        "  mov r10, rax", "  mov rcx, 1", "  mov rdx, r13", "  ret",
    ]) == ["  mov rdx, r13", "  ret"]


def test_writes_end_a_copy():
    lines = [
        "  mov r10, rbx",
        "  add rbx, 1",
        "  mov rdi, r10",
        "  mov r11, r13",
        "  add r11, 2",
        "  mov rsi, r11",
        "  call g",
        "  ret",
    ]
    assert forward_register_copies(lines) == lines


def test_calls_labels_and_barriers_end_copies():
    lines = [
        # The call clobbers r10, the copy's source.
        "  mov r13, r10",
        "  call g",
        "  mov rdi, r13",
        "  mov r14, rbx",
        # Another path may enter at a label.
        ".Lf_x:",
        "  mov rsi, r14",
        "  mov r10, r12",
        "  lock xadd QWORD PTR [r13], r10",
        "  mov rdx, r10",
        "  call h",
        "  ret",
    ]
    assert forward_register_copies(lines) == lines


def test_copies_of_callee_saved_registers_survive_calls():
    assert forward_register_copies([
        "  mov r13, rbx", "  call g", "  mov rdi, r13", "  call h", "  ret",
    ]) == ["  mov r13, rbx", "  call g", "  mov rdi, rbx", "  call h", "  ret"]


def test_live_registers_across_blocks_keep_their_moves():
    lines = [
        "  mov r10, rbx",
        "  cmp r12, 0",
        "  je .Lf_b",
        "  mov rax, 1",
        "  ret",
        ".Lf_b:",
        "  mov rdi, r10",
        "  call g",
        "  ret",
    ]
    assert forward_register_copies(lines) == lines


@pytest.mark.parametrize("lines", [
    # A load may fault.
    ["  mov r10, QWORD PTR [rbx]", "  ud2"],
    # rsp and rbp are never forwarded or dropped.
    ["  mov r11, rsp", "  mov rdi, QWORD PTR [r11 + 8]", "  mov rbp, rsp", "  ud2"],
    # r12 cannot be an SIB index.
    ["  mov r10, r12", "  mov rax, QWORD PTR [rbx + r10*8]", "  ret"],
    # The divisor keeps rax/rdx live.
    ["  mov rax, r12", "  cqo", "  idiv rbx", "  ret"],
    # A jump out of the function text stops the dead-move pass.
    ["  mov r10, 5", "  jmp g"],
])
def test_shapes_that_stay(lines):
    assert forward_register_copies(lines) == lines


def test_constants_become_immediates():
    assert forward_register_copies([
        "  mov r10b, 1",
        "  mov BYTE PTR [rbp - 64], r10b",
        "  xor r10, r10",
        "  mov QWORD PTR [rbp - 56], r10",
        "  mov r11, 5",
        "  mov rsi, r11",
        "  cmp rbx, r11",
        "  mov r10, 4294967296",
        "  mov QWORD PTR [rbp - 72], r10",
        "  call g",
        "  ret",
    ]) == [
        "  mov BYTE PTR [rbp - 64], 1",
        "  mov QWORD PTR [rbp - 56], 0",
        "  mov rsi, 5",
        "  cmp rbx, 5",
        # A 64-bit store takes only a sign-extended imm32.
        "  mov r10, 4294967296",
        "  mov QWORD PTR [rbp - 72], r10",
        "  call g",
        "  ret",
    ]


def test_constants_stay_registers_where_the_encoder_takes_no_immediate():
    lines = [
        "  mov r10, 5", "  test rbx, r10", "  cmp QWORD PTR [rbx], r10", "  setne al",
        "  movzx eax, al", "  ret",
    ]
    assert forward_register_copies(lines) == lines


def test_zero_idiom_stays_while_its_flags_are_read():
    lines = ["  xor r10, r10", "  mov QWORD PTR [rbp - 8], r10", "  jne .Lf_x", "  ud2", ".Lf_x:", "  ret"]
    assert forward_register_copies(lines) == [
        "  xor r10, r10", "  mov QWORD PTR [rbp - 8], 0", "  jne .Lf_x", "  ud2", ".Lf_x:", "  ret",
    ]
    # A variadic call's vector count stays though the model has calls read
    # no rax.
    lines = ["  xor eax, eax", "  call printf", "  ret"]
    assert forward_register_copies(lines) == lines


class _CountReads(list):
    """Count host test input accesses without changing their values."""

    def __init__(self, values):
        super().__init__(values)
        self.accesses = 0

    def __getitem__(self, index):
        self.accesses += 1
        return super().__getitem__(index)


def test_dead_moves_stop_after_out_only_change_in_cycle():
    # Block 0 reads rax; block 1 defines it and jumps back. In reverse order,
    # sweep 2 changes only block 1's live_out: its definition kills live_in.
    # That updated live_out must still keep the definition in the final pass.
    # Counts include the final dead-marking scan and are not timings.
    tail = ([0, copies._RAX], [0, 1], {"read": 0, "write": 1},
            [0, 1], ["write", "read"], [0, 0])
    reads = _CountReads([copies._RAX, 0])
    old_reads = _CountReads(reads)
    assert copies._dead_moves(2, reads, *tail) == [0, 0]
    assert reference_dead_moves(2, old_reads, *tail) == [0, 0]
    assert reads.accesses == 5
    assert old_reads.accesses == 7


def test_dead_moves_match_reference_for_all_two_block_masks():
    # Every pair of two-bit read/write masks and all three drop kinds,
    # across all 12 CFGs expressible by two blocks with one terminator each.
    # Includes self loops, backedges, kills, branches and disconnected blocks.
    masks = (0, copies._RAX, copies._FLAGS, copies._RAX | copies._FLAGS)
    effects = tuple(product(masks, masks, (0, 1, 2)))
    first_edges = (("", 0), ("a", 0), ("b", 0), ("a", 1))
    second_edges = (("", 0), ("a", 0), ("b", 0))
    cases = 0
    for first, second, edge_a, edge_b in product(
        effects, effects, first_edges, second_edges
    ):
        reads = [first[0], second[0]]
        writes = [first[1], second[1]]
        kinds = [first[2], second[2]]
        args = (2, reads, writes, kinds, {"a": 0, "b": 1}, [0, 1],
                [edge_a[0], edge_b[0]], [edge_a[1], edge_b[1]])
        original = (reads[:], writes[:], kinds[:])
        assert copies._dead_moves(*args) == reference_dead_moves(*args), args
        assert (reads, writes, kinds) == original
        cases += 1
    assert cases == 27648


def test_dead_moves_match_reference_for_multiline_cyclic_cfgs():
    # Fixed finite corpus: 1-8 blocks, 1-4 lines per block, all 17 bits,
    # conditional fallthrough, backwards/forwards edges and real barriers.
    rng = Random(0x1738A85)
    masks = (0, copies._RAX, copies._RCX, copies._R10, copies._FLAGS,
             copies._FRAME, copies._RET_READS, copies._EVERYTHING)
    for case in range(512):
        blocks = 1 + rng.randrange(8)
        reads, writes, kinds, labels, ends, targets, falls = [], [], [], {}, [], [], []
        for block in range(blocks):
            labels[str(block)] = len(reads)
            for _ in range(1 + rng.randrange(4)):
                reads.append(rng.choice(masks))
                writes.append(rng.choice(masks))
                kinds.append(rng.randrange(3))
            ends.append(len(reads) - 1)
            targets.append("" if rng.randrange(4) == 0 else str(rng.randrange(blocks)))
            falls.append(rng.randrange(2))
        args = (len(reads), reads, writes, kinds, labels, ends, targets, falls)
        assert copies._dead_moves(*args) == reference_dead_moves(*args), (case, args)


def test_dead_moves_unknown_target_and_empty_input_keep_fallback():
    for args in (
        (0, [], [], [], {}, [], [], []),
        (2, [0, 0], [copies._R10, 0], [1, 0], {"here": 0},
         [1], ["missing"], [0]),
    ):
        assert copies._dead_moves(*args) is None
        assert reference_dead_moves(*args) is None
    lines = ["  mov r10, 5", "  jmp .Lmissing"]
    assert forward_register_copies(lines) is lines


def test_dead_move_cycle_retains_latest_out_and_line_metadata():
    class Line(str):
        def __new__(cls, text, origin):
            line = super().__new__(cls, text)
            line.origin = origin
            return line

    text = [".Lread:", "  test r10, r10", "  jmp .Lwrite", ".Lwrite:",
            "  mov r10, 7", "  mov r11, r11", "  jmp .Lread"]
    lines = [Line(line, index) for index, line in enumerate(text)]
    result = forward_register_copies(lines)
    kept = (0, 1, 2, 3, 4, 6)
    assert result == [text[index] for index in kept]
    for line, index in zip(result, kept, strict=True):
        assert line is lines[index]
        assert line.origin == index
    # With nothing to remove or forward, the original list is returned too.
    assert forward_register_copies(result) is result


def test_cyclic_forwarding_matches_reference_with_flags_and_barriers(monkeypatch):
    fixtures = [
        # Strong liveness must not resurrect a byte-copy-only cycle.
        [".Lloop:", "  mov al, bl", "  jmp .Lloop"],
        # The flags and a partially written return register remain live.
        [".Lloop:", "  xor r10, r10", "  mov al, bl", "  jne .Lloop", "  ret"],
        # A backward read retains a later write across a kill.
        [".La:", "  test r10, r10", "  jmp .Lb", ".Lb:", "  mov r10, 7", "  jmp .La"],
        # An unmodeled instruction is an all-register/flags barrier.
        [".Lloop:", "  mov r10, rbx", "  lock xadd QWORD PTR [r13], r10",
         "  jne .Lloop", "  ret"],
        # Forwarding clears at a label while callee-preserved copies survive.
        [".Lloop:", "  mov r13, rbx", "  call g", "  mov rdi, r13",
         "  test rdi, rdi", "  jne .Lloop", "  ret"],
    ]
    for lines in fixtures:
        actual = forward_register_copies(lines)
        with monkeypatch.context() as patch:
            patch.setattr(copies, "_dead_moves", reference_dead_moves)
            expected = forward_register_copies(lines)
        assert actual == expected
    assert forward_register_copies(fixtures[0]) == [".Lloop:", "  jmp .Lloop"]
