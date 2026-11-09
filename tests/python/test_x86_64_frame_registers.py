"""x86-64 keeps scalar frame slots in callee-saved registers.

The instruction selector stores every SSA value to its frame slot and reloads
it at each use. ``keep_frame_slots_in_registers`` rewrites a finished function
so a slot that only plain ``mov``s touch, whose address is never taken and
that no stack map lists, lives in rbx/r12-r15 (plus rsi/rdi under Win64)
instead; the prologue saves and each epilogue restores the r12-r15 it uses.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import random
from types import SimpleNamespace

import pytest

from pcc.backend.self_backend_ir import SlotInfo, TypeDesc
from pcc.backend.self_backend_precise_stackmaps import (
    PackedPlannedSafepoints,
    PlannedManagedReload,
    PlannedRootLocation,
    PlannedSafepoint,
)
from pcc.backend.x86_64_frame_registers import (
    expand_pointer_retirements, keep_frame_slots_in_registers,
)

I64 = TypeDesc("int", 64)
I8 = TypeDesc("int", 8)
PTR = TypeDesc("ptr", pointee=TypeDesc("void"))
F64 = TypeDesc("fp", 64)
PROLOGUE = ["", ".text", ".p2align 4, 0x90", ".globl f", ".type f, @function", "f:",
            "  push rbp", "  mov rbp, rsp", "  sub rsp, 80", "  mov QWORD PTR [rsp + 0], rbx"]
EPILOGUE = ["  mov rbx, QWORD PTR [rsp + 0]", "  add rsp, 80", "  pop rbp", "  ret"]


def _func(*slots, windows=False):
    value_slots = {f"%v{index}": SlotInfo(offset, ty) for index, (offset, ty) in enumerate(slots)}
    return SimpleNamespace(value_slots=value_slots, is_vararg=False,
                           platform_frame_extra=(32 + 112 if windows else 48),
                           alloca_slots={}, hidden_sret_slot=None)


def _plan(*root_offsets):
    locations = tuple(PlannedRootLocation(-offset, True) for offset in root_offsets)
    record = PlannedSafepoint(1, ".Lsmap_1", 0, locations)
    return SimpleNamespace(packed_records=None, records=(record,))


def _promote(body, *slots, plan=None, windows=False):
    lines = PROLOGUE + body + EPILOGUE
    return expand_pointer_retirements(keep_frame_slots_in_registers(
        lines, _func(*slots), plan if plan is not None else _plan(), windows=windows,
    ))


def test_values_across_calls_live_in_callee_saved_registers():
    body = [
        "  mov QWORD PTR [rbp - 8], rdi",
        "  call g",
        ".Lsmap_1:",
        "  mov QWORD PTR [rbp - 16], rax",
        "  mov rdi, QWORD PTR [rbp - 8]",
        "  mov rsi, QWORD PTR [rbp - 16]",
        "  call h",
        "  mov rax, QWORD PTR [rbp - 16]",
    ]
    out = _promote(body, (8, I64), (16, PTR))
    # The heavier slot (three accesses) takes rbx, which every frame already
    # saves; the slot live across its store takes r12, saved at
    # platform_frame_extra - 32 and restored before each return.
    assert out == PROLOGUE[:-1] + ["  mov QWORD PTR [rbp - 16], 0", PROLOGUE[-1],
                                 "  mov QWORD PTR [rsp + 16], r12"] + [
        "  mov r12, rdi",
        "  call g",
        ".Lsmap_1:",
        "  mov rbx, rax",
        "  mov rdi, r12",
        "  mov rsi, rbx",
        "  call h",
        "  mov rax, rbx",
        "  mov ebx, 0",
    ] + EPILOGUE[:1] + ["  mov r12, QWORD PTR [rsp + 16]"] + EPILOGUE[1:]


def test_disjoint_values_share_one_register_and_widths_keep_their_alias():
    body = [
        "  mov BYTE PTR [rbp - 1], al",
        "  mov r10b, BYTE PTR [rbp - 1]",
        "  mov DWORD PTR [rbp - 12], -7",
        "  mov r10d, DWORD PTR [rbp - 12]",
        "  mov QWORD PTR [rbp - 24], 5",
        "  mov rax, QWORD PTR [rbp - 24]",
    ]
    out = _promote(body, (1, I8), (12, TypeDesc("int", 32)), (24, I64))
    assert out == PROLOGUE + [
        "  mov bl, al", "  mov r10b, bl",
        "  mov ebx, -7", "  mov r10d, ebx",
        "  mov rbx, 5", "  mov rax, rbx",
    ] + EPILOGUE


@pytest.mark.parametrize("body, slots, plan", [
    # The address escapes, so a pointer may read or write the slot.
    (["  mov QWORD PTR [rbp - 8], rax", "  lea rdi, [rbp - 8]", "  call g",
      "  mov rax, QWORD PTR [rbp - 8]"], ((8, I64),), None),
    # An address inside the slot escapes.
    (["  mov QWORD PTR [rbp - 8], rax", "  lea rdi, [rbp - 4]", "  call g",
      "  mov rax, QWORD PTR [rbp - 8]"], ((8, I64),), None),
    # A stack map lists it: the collector reads and relocates it in memory.
    (["  mov QWORD PTR [rbp - 8], rax", "  call g", "  mov rax, QWORD PTR [rbp - 8]"],
     ((8, PTR),), _plan(8)),
    # Two widths touch it.
    (["  mov QWORD PTR [rbp - 8], rax", "  mov eax, DWORD PTR [rbp - 8]"], ((8, I64),), None),
    # Something other than a general mov reads it.
    (["  mov QWORD PTR [rbp - 8], rax", "  cmp QWORD PTR [rbp - 8], 0"], ((8, I64),), None),
    (["  movsd QWORD PTR [rbp - 8], xmm0", "  movsd xmm1, QWORD PTR [rbp - 8]"], ((8, F64),), None),
    # It is read before any store on some path into the function.
    (["  mov rax, QWORD PTR [rbp - 8]", "  mov QWORD PTR [rbp - 8], rdi",
      "  mov rdx, QWORD PTR [rbp - 8]"], ((8, I64),), None),
    # A wider access elsewhere covers its bytes.
    (["  mov QWORD PTR [rbp - 24], rax", "  movdqu XMMWORD PTR [rbp - 32], xmm0",
      "  mov rax, QWORD PTR [rbp - 24]"], ((24, I64),), None),
    # It is not a scalar SSA value slot.
    (["  mov QWORD PTR [rbp - 8], rax", "  mov rax, QWORD PTR [rbp - 8]"], (), None),
])
def test_slots_that_stay_in_memory(body, slots, plan):
    assert _promote(body, *slots, plan=plan) == PROLOGUE + body + EPILOGUE


def test_stores_nothing_reads_are_dropped_in_registers_and_in_memory():
    body = [
        "  mov QWORD PTR [rbp - 8], rax",
        "  mov QWORD PTR [rbp - 8], rdi",
        "  mov rax, QWORD PTR [rbp - 8]",
        # Read before its stores, so it stays in memory; the first of its
        # two stores is still dead.
        "  mov rcx, QWORD PTR [rbp - 16]",
        "  mov QWORD PTR [rbp - 16], rdi",
        "  mov QWORD PTR [rbp - 16], rsi",
        "  mov rdx, QWORD PTR [rbp - 16]",
        "  mov BYTE PTR [rbp - 17], al",
    ]
    out = _promote(body, (8, I64), (16, I64), (17, I8))
    assert out == PROLOGUE + ["  mov rbx, rdi", "  mov rax, rbx", body[3], body[5], body[6]] + EPILOGUE


def test_packed_stack_map_roots_and_reload_sources_stay_in_memory():
    packed = PackedPlannedSafepoints(1)
    packed.append(1, ".Lsmap_1", 0, (PlannedRootLocation(-8, True),), 0, "", 0,
                  (PlannedManagedReload(-16, -24),))
    plan = SimpleNamespace(packed_records=packed, records=())
    body = [
        "  mov QWORD PTR [rbp - 8], rax", "  mov QWORD PTR [rbp - 16], rax",
        "  call g", ".Lsmap_1:",
        "  mov r11, QWORD PTR [rbp - 16]", "  mov QWORD PTR [rbp - 24], r11",
        "  mov rax, QWORD PTR [rbp - 8]", "  mov rdx, QWORD PTR [rbp - 24]",
    ]
    out = _promote(body, (8, PTR), (16, PTR), (24, PTR), plan=plan)
    # The reload destination is an ordinary value slot.
    assert out == PROLOGUE + body[:5] + ["  mov rbx, r11", body[6], "  mov rdx, rbx"] + EPILOGUE


def test_registers_the_body_names_are_not_used():
    body = [
        "  mov QWORD PTR [rbp - 8], rdi",
        "  mov rbx, rax", "  sar rbx, 63",
        "  call g",
        "  mov rax, QWORD PTR [rbp - 8]",
    ]
    out = _promote(body, (8, I64))
    assert out == PROLOGUE + ["  mov QWORD PTR [rsp + 16], r12", "  mov r12, rdi",
                              *body[1:4], "  mov rax, r12"] + EPILOGUE[:1] + [
        "  mov r12, QWORD PTR [rsp + 16]"] + EPILOGUE[1:]


def test_loop_carried_value_interferes_across_the_back_edge():
    # [rbp - 8] carries the sum around the loop, so it is live where each
    # iteration stores [rbp - 16]; they need two registers.
    body = [
        "  mov QWORD PTR [rbp - 8], 0",
        ".Lf_head:",
        "  call g",
        "  mov QWORD PTR [rbp - 16], rax",
        "  mov r10, QWORD PTR [rbp - 8]",
        "  mov r11, QWORD PTR [rbp - 16]",
        "  add r10, r11",
        "  mov QWORD PTR [rbp - 8], r10",
        "  cmp r10, 100",
        "  jl .Lf_head",
        "  mov rax, QWORD PTR [rbp - 8]",
    ]
    out = _promote(body, (8, I64), (16, I64))
    assert out == PROLOGUE + ["  mov QWORD PTR [rsp + 16], r12"] + [
        "  mov rbx, 0",
        ".Lf_head:",
        "  call g",
        "  mov r12, rax",
        "  mov r10, rbx",
        "  mov r11, r12",
        "  add r10, r11",
        "  mov rbx, r10",
        "  cmp r10, 100",
        "  jl .Lf_head",
        "  mov rax, rbx",
    ] + EPILOGUE[:1] + ["  mov r12, QWORD PTR [rsp + 16]"] + EPILOGUE[1:]


def test_value_dead_between_its_load_and_next_store_shares_its_register():
    # The loop reads [rbp - 8] and stores it again only after [rbp - 16]'s
    # last use, so the two never hold values at the same point.
    body = [
        "  mov QWORD PTR [rbp - 8], 0",
        ".Lf_head:",
        "  mov r10, QWORD PTR [rbp - 8]",
        "  mov QWORD PTR [rbp - 16], r10",
        "  call g",
        "  mov r10, QWORD PTR [rbp - 16]",
        "  add r10, 1",
        "  mov QWORD PTR [rbp - 8], r10",
        "  cmp r10, 10",
        "  jl .Lf_head",
        "  mov rax, QWORD PTR [rbp - 8]",
    ]
    out = _promote(body, (8, I64), (16, I64))
    assert out == PROLOGUE + [line.replace("QWORD PTR [rbp - 8]", "rbx").replace(
        "QWORD PTR [rbp - 16]", "rbx") for line in body] + EPILOGUE


def test_windows_saves_are_described_for_unwinding():
    outgoing = 32
    prologue = ["", ".text", ".p2align 4, 0x90", ".globl f", ".type f, @function", ".seh_proc f", "f:",
                "  push rbp", "  .seh_pushreg rbp", "  mov rbp, rsp", "  sub rsp, 176",
                "  .seh_stackalloc 176",
                f"  mov QWORD PTR [rsp + {outgoing}], rbx", f"  .seh_savereg rbx, {outgoing}",
                f"  mov QWORD PTR [rsp + {outgoing + 8}], rsi", f"  .seh_savereg rsi, {outgoing + 8}",
                f"  mov QWORD PTR [rsp + {outgoing + 16}], rdi", f"  .seh_savereg rdi, {outgoing + 16}",
                "  .seh_endprologue"]
    epilogue = [f"  mov rbx, QWORD PTR [rsp + {outgoing}]", f"  mov rsi, QWORD PTR [rsp + {outgoing + 8}]",
                f"  mov rdi, QWORD PTR [rsp + {outgoing + 16}]", "  add rsp, 176", "  pop rbp", "  ret"]
    # Four values live across the call: rbx, rsi and rdi are already saved;
    # the fourth takes r12, saved at the top of the 112-byte platform area.
    body = [f"  mov QWORD PTR [rbp - {8 * index}], rcx" for index in range(1, 5)]
    body += ["  call g"]
    body += [f"  mov rdx, QWORD PTR [rbp - {8 * index}]" for index in range(1, 5)]
    slots = [(8 * index, I64) for index in range(1, 5)]
    out = keep_frame_slots_in_registers(prologue + body + epilogue, _func(*slots, windows=True),
                                        _plan(), windows=True)
    area = outgoing + 80
    registers = ("rbx", "rsi", "rdi", "r12")
    assert out == prologue[:-1] + [
        f"  mov QWORD PTR [rsp + {area}], r12", f"  .seh_savereg r12, {area}", "  .seh_endprologue",
    ] + [f"  mov {register}, rcx" for register in registers] + ["  call g"] + [
        f"  mov rdx, {register}" for register in registers
    ] + epilogue[:3] + [f"  mov r12, QWORD PTR [rsp + {area}]"] + epilogue[3:]


@pytest.mark.parametrize("body", [
    # Control leaves the function text other than through ret.
    ["  mov QWORD PTR [rbp - 8], rax", "  jmp g", "  mov rax, QWORD PTR [rbp - 8]"],
    ["  mov QWORD PTR [rbp - 8], rax", "  jmp rax"],
    # A frame address form the pass does not model.
    ["  mov QWORD PTR [rbp - 8], rax", "  mov rax, QWORD PTR [rbp - 8 + r10]"],
])
def test_text_the_pass_cannot_follow_is_left_alone(body):
    assert _promote(body, (8, I64)) == PROLOGUE + body + EPILOGUE


def test_return_without_the_frame_restore_is_left_alone():
    body = ["  mov QWORD PTR [rbp - 8], rdi", "  call g", "  mov QWORD PTR [rbp - 16], rax",
            "  mov rdi, QWORD PTR [rbp - 8]", "  mov rsi, QWORD PTR [rbp - 16]", "  call h",
            "  mov rax, QWORD PTR [rbp - 16]", "  ret"]
    lines = PROLOGUE + body + EPILOGUE
    assert keep_frame_slots_in_registers(
        lines, _func((8, I64), (16, I64)), _plan(), windows=False) == lines


class _CountedSuccessors(list):
    """Count block visits, not elapsed time or native instructions."""

    def __init__(self, successors):
        super().__init__(successors)
        self.visits = 0

    def __getitem__(self, index):
        self.visits += 1
        return super().__getitem__(index)


@pytest.fixture(scope="module")
def _production_frame_liveness():
    # Execute the production recurrence without constructing thousands of
    # unrelated frame/assembly fixtures. Keep the original AST unmodified;
    # the full-pass tests above and below also exercise its actual caller.
    tree = ast.parse(inspect.getsource(keep_frame_slots_in_registers))
    function = tree.body[0]
    starts = [
        index for index, statement in enumerate(function.body)
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "live_in"
    ]
    assert len(starts) == 1
    statements = function.body[starts[0]:starts[0] + 4]
    assert [statement.targets[0].id for statement in statements[:3]] == [
        "live_in", "live_out", "changed",
    ]
    assert isinstance(statements[3], ast.While)
    assert isinstance(statements[3].test, ast.Name)
    assert statements[3].test.id == "changed"
    code = compile(ast.Module(body=statements, type_ignores=[]),
                   "<production frame-register liveness>", "exec")

    def solve(successors, uses, kills):
        counted = _CountedSuccessors(successors)
        context = dict(block_count=len(successors), successors=counted,
                       uses=list(uses), kills=list(kills))
        exec(code, {"__builtins__": {}}, context)
        return context["live_in"], context["live_out"], counted.visits

    return solve


def _full_sweep_liveness_reference(successors, uses, kills):
    # The original conservative scheduler, independent of the extracted
    # production code. Do not change this alongside solver optimizations.
    live_in = [0] * len(successors)
    live_out = [0] * len(successors)
    changed = True
    visits = 0
    while changed:
        changed = False
        for block in range(len(successors) - 1, -1, -1):
            visits += 1
            out = 0
            for successor in successors[block]:
                out |= live_in[successor]
            entering = uses[block] | (out & ~kills[block])
            if out != live_out[block] or entering != live_in[block]:
                live_out[block] = out
                live_in[block] = entering
                changed = True
    return live_in, live_out, visits


def test_frame_liveness_matches_full_sweep_for_all_two_block_two_bit_graphs(
    _production_frame_liveness,
):
    # 16 graphs * 4**4 use/kill choices = 4096 cases. Includes empty edges,
    # unreachable blocks, self loops, cycles and overlapping use/kill bits.
    edge_choices = [[], [0], [1], [0, 1]]
    for successors in itertools.product(edge_choices, repeat=2):
        for use0, use1, kill0, kill1 in itertools.product(range(4), repeat=4):
            uses, kills = [use0, use1], [kill0, kill1]
            actual = _production_frame_liveness(successors, uses, kills)
            reference = _full_sweep_liveness_reference(successors, uses, kills)
            assert actual[:2] == reference[:2], (successors, uses, kills)
            assert reference[2] - actual[2] in (0, len(successors))


def test_frame_liveness_matches_full_sweep_for_seeded_multiblock_graphs(
    _production_frame_liveness,
):
    randomizer = random.Random(0xF12A)
    for _ in range(512):
        blocks = randomizer.randrange(1, 13)
        successors = [
            [randomizer.randrange(blocks) for _ in range(randomizer.randrange(4))]
            for _ in range(blocks)
        ]
        uses = [randomizer.getrandbits(8) for _ in range(blocks)]
        kills = [randomizer.getrandbits(8) for _ in range(blocks)]
        actual = _production_frame_liveness(successors, uses, kills)
        reference = _full_sweep_liveness_reference(successors, uses, kills)
        assert actual[:2] == reference[:2], (successors, uses, kills)
        assert reference[2] - actual[2] in (0, blocks)


@pytest.mark.parametrize("successors, uses, kills, expected_in, expected_out", [
    ([[0]], [1], [1], [1], [1]),
    ([[1], [0]], [1, 0], [0, 1], [1, 0], [0, 1]),
    ([[1, 1], [0, 0]], [1, 0], [0, 1], [1, 0], [0, 1]),
    ([[1], [0], [2]], [1, 0, 0], [0, 1, 0], [1, 0, 0], [0, 1, 0]),
])
def test_out_only_changes_keep_fresh_live_out_without_an_extra_sweep(
    _production_frame_liveness, successors, uses, kills, expected_in, expected_out,
):
    actual = _production_frame_liveness(successors, uses, kills)
    reference = _full_sweep_liveness_reference(successors, uses, kills)
    assert actual == (expected_in, expected_out, 2 * len(successors))
    assert reference == (expected_in, expected_out, 3 * len(successors))


def test_out_only_cycle_preserves_a_store_read_on_the_back_edge():
    body = [
        ".Lread:",
        "  mov rax, QWORD PTR [rbp - 8]",
        "  jmp .Lwrite",
        ".Lwrite:",
        "  mov QWORD PTR [rbp - 8], rdi",
        "  jmp .Lread",
    ]
    # The slot is read at entry, so remains in memory. The write block kills
    # its incoming bit, but its outgoing bit must still preserve the store.
    assert _promote(body, (8, I64)) == PROLOGUE + body + EPILOGUE


def test_pointer_last_read_retires_storage_after_copy_not_before_call():
    body = ["  mov QWORD PTR [rbp - 8], rdi", "  call before",
            "  mov rsi, QWORD PTR [rbp - 8]", "  call consume", "  call after"]
    out = _promote(body, (8, PTR))
    assert out.index("  mov rsi, rbx") < out.index("  mov ebx, 0") < out.index("  call consume")
    assert "  mov ebx, 0" not in out[:out.index("  call before")]
    assert out.index("  mov QWORD PTR [rbp - 8], 0") < out.index("  mov rbx, rdi")


def test_pointer_retirement_survives_final_copy_peepholes_without_changing_flags():
    from pcc.backend.x86_64_register_copies import forward_register_copies, retarget_scratch_definitions
    body = ["  mov QWORD PTR [rbp - 8], rdi", "  cmp rsi, 7",
            "  mov r10, QWORD PTR [rbp - 8]", "  sete sil", "  mov rdi, r10", "  call consume"]
    marked = keep_frame_slots_in_registers(PROLOGUE + body + EPILOGUE,
                                           _func((8, PTR)), _plan(), windows=False)
    assert "  pcc_retire_pointer_register rbx" in marked
    final = expand_pointer_retirements(forward_register_copies(retarget_scratch_definitions(marked)))
    assert "  mov ebx, 0" in final
    assert not any("pcc_retire_" in line for line in final)
    compare, flag_use = final.index("  cmp rsi, 7"), final.index("  sete sil")
    assert compare < final.index("  mov ebx, 0") < flag_use
    assert all(not line.strip().startswith(("xor ", "sub ", "add "))
               for line in final[compare + 1:flag_use])


def test_spilled_pointer_last_read_is_cleared_when_all_pool_registers_are_reserved():
    body = ["  mov QWORD PTR [rbp - 8], rdi"]
    body += [f"  mov {register}, 7" for register in ("rbx", "r12", "r13", "r14", "r15")]
    body += ["  mov rdi, QWORD PTR [rbp - 8]", "  call consume"]
    out = _promote(body, (8, PTR))
    read = out.index("  mov rdi, QWORD PTR [rbp - 8]")
    assert out[read + 1] == "  mov QWORD PTR [rbp - 8], 0"
    assert out[read + 2] == "  call consume"


def test_pointer_live_on_loop_backedge_is_not_retired_early():
    body = ["  mov QWORD PTR [rbp - 8], rdi", ".Lagain:",
            "  mov rdi, QWORD PTR [rbp - 8]", "  call consume",
            "  cmp rax, 0", "  jne .Lagain", "  mov rax, QWORD PTR [rbp - 8]"]
    out = _promote(body, (8, PTR))
    assert out.count("  mov ebx, 0") == 1
    assert out.index("  mov rax, rbx") < out.index("  mov ebx, 0")


def test_pointer_read_live_on_one_successor_is_not_retired_before_branch():
    body = ["  mov QWORD PTR [rbp - 8], rdi", "  mov rdi, QWORD PTR [rbp - 8]",
            "  call consume", "  cmp rax, 0", "  je .Ldone",
            "  mov rdi, QWORD PTR [rbp - 8]", "  call consume", ".Ldone:"]
    out = _promote(body, (8, PTR))
    assert "  mov ebx, 0" not in out[:out.index("  je .Ldone")]
    assert out.count("  mov ebx, 0") == 1


def test_pointer_retirement_preserves_same_color_later_integer_and_partial_width_value():
    body = ["  mov QWORD PTR [rbp - 8], rdi", "  mov rdi, QWORD PTR [rbp - 8]",
            "  call consume", "  mov BYTE PTR [rbp - 9], al",
            "  mov dil, BYTE PTR [rbp - 9]", "  call integer_consumer"]
    out = _promote(body, (8, PTR), (9, I8))
    assert out.index("  mov ebx, 0") < out.index("  mov bl, al")
    assert "  mov ebx, 0" not in out[out.index("  mov bl, al"):]


def test_pointer_retirement_excludes_complete_alloca_and_hidden_result_spans():
    from pcc.backend.self_backend_ir import AllocaInfo
    body = ["  mov QWORD PTR [rbp - 16], rax", "  mov rdi, QWORD PTR [rbp - 16]"]
    for allocation, hidden in ((AllocaInfo(24, TypeDesc("array", elem=I8, count=16)), None),
                               (None, SlotInfo(16, PTR))):
        function = _func((16, PTR))
        function.alloca_slots = {"buffer": allocation} if allocation else {}
        function.hidden_sret_slot = hidden
        out = keep_frame_slots_in_registers(PROLOGUE + body + EPILOGUE,
                                            function, _plan(), windows=False)
        assert not any("pcc_retire_" in line for line in out)
        assert "  mov QWORD PTR [rbp - 16], 0" not in out


def test_pointer_retirement_excludes_reload_destination_metadata():
    record = PlannedSafepoint(1, ".Lsmap_1", 0, (), reloads=(PlannedManagedReload(-8, -16),))
    plan = SimpleNamespace(packed_records=None, records=(record,))
    body = ["  mov QWORD PTR [rbp - 16], rax", "  mov rdi, QWORD PTR [rbp - 16]"]
    out = keep_frame_slots_in_registers(PROLOGUE + body + EPILOGUE,
                                       _func((16, PTR)), plan, windows=False)
    assert not any("pcc_retire_" in line for line in out)
    assert "  mov QWORD PTR [rbp - 16], 0" not in out


def test_final_emitter_retires_pointer_but_keeps_integer_pointer_roundtrip():
    from pcc.backend.self_backend_dispatch import emit_self_asm
    source = '''target triple = "x86_64-unknown-linux-gnu"
declare void @consume(ptr)
define ptr @roundtrip(ptr %raw) {
entry:
  %bits = ptrtoint ptr %raw to i64
  call void @consume(ptr %raw)
  %again = inttoptr i64 %bits to ptr
  ret ptr %again
}
'''
    assembly = emit_self_asm(source, "x86_64-unknown-linux-gnu")
    assert "pcc_retire_" not in assembly
    # The final emitter runs scratch retargeting and register-copy DCE. This
    # check is intentionally on that output, not on the allocator alone.
    assert any(line.strip() in ("mov ebx, 0", "mov r12d, 0", "mov r13d, 0")
               for line in assembly.splitlines())


def test_many_windows_pointer_cells_initialize_outside_unwind_prologue():
    from pcc.backend.coff_x86_64 import assemble_object, parse_object
    count, frame_size, outgoing = 48, 560, 32
    prologue = [".intel_syntax noprefix", ".text", ".globl f", ".seh_proc f", "f:",
                "  push rbp", "  .seh_pushreg rbp", "  mov rbp, rsp",
                f"  sub rsp, {frame_size}", f"  .seh_stackalloc {frame_size}",
                f"  mov QWORD PTR [rsp + {outgoing}], rbx", f"  .seh_savereg rbx, {outgoing}",
                f"  mov QWORD PTR [rsp + {outgoing + 8}], rsi", f"  .seh_savereg rsi, {outgoing + 8}",
                f"  mov QWORD PTR [rsp + {outgoing + 16}], rdi", f"  .seh_savereg rdi, {outgoing + 16}",
                "  .seh_endprologue"]
    body = []
    for index in range(1, count + 1):
        body += [f"  mov QWORD PTR [rbp - {index * 8}], rcx",
                 f"  mov rdx, QWORD PTR [rbp - {index * 8}]", "  call consume"]
    epilogue = [f"  mov rbx, QWORD PTR [rsp + {outgoing}]",
                f"  mov rsi, QWORD PTR [rsp + {outgoing + 8}]",
                f"  mov rdi, QWORD PTR [rsp + {outgoing + 16}]",
                f"  add rsp, {frame_size}", "  pop rbp", "  ret", "  .seh_endproc"]
    function = _func(*[(index * 8, PTR) for index in range(1, count + 1)], windows=True)
    out = expand_pointer_retirements(keep_frame_slots_in_registers(
        prologue + body + epilogue, function, _plan(), windows=True,
    ))
    boundary = out.index("  .seh_endprologue")
    for index in range(1, count + 1):
        assert out.index(f"  mov QWORD PTR [rbp - {index * 8}], 0") > boundary
    # The owned COFF writer rejects prologue_size > 255. Initializers for
    # many disjoint nominal slots must not count toward that one-byte limit.
    obj = parse_object(assemble_object("\n".join(out) + "\n"))
    assert any(section.name == ".xdata" for section in obj.sections)


def test_narrow_reused_slot_metadata_is_not_cleared_as_a_pointer_cell():
    # An eliminated pointer SSA can share an offset with the only remaining
    # byte-valued machine accesses. Do not turn that byte into an 8-byte write.
    body = ["  mov BYTE PTR [rbp - 8], al", "  mov dil, BYTE PTR [rbp - 8]", "  call consume"]
    out = keep_frame_slots_in_registers(PROLOGUE + body + EPILOGUE,
                                       _func((8, PTR), (8, I8)), _plan(), windows=False)
    assert not any("pcc_retire_" in line for line in out)
    assert "  mov QWORD PTR [rbp - 8], 0" not in out
