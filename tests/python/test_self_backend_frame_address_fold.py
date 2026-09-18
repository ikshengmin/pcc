"""Frame-address folding rewrites ``x29`` slot addressing to ``sp`` offsets.

The AArch64 emitter materialises ``sub xD, x29, #imm`` whenever a frame slot
falls outside ``ldur``/``stur``'s signed 9-bit displacement.  The scaled
``ldr``/``str`` form reaches the same slot from ``sp`` once the prologue has
fixed the frame, so the address register becomes dead and the ``sub`` goes
away.  These cases pin the proof obligations: a modelled frame, a dead address
register, an encodable displacement, and ``sp`` unchanged since the prologue.
"""

from pcc.backend.self_backend_target_passes import (
    FoldFrameAddressPass,
    SelfTargetPassContext,
    fold_aarch64_frame_addresses,
)


def _fold(body: str) -> list[str]:
    return fold_aarch64_frame_addresses(body.strip("\n").splitlines())


_PROLOGUE = """_f:
  paciasp
  stp x29, x30, [sp, #-16]!
  mov x29, sp
  sub sp, sp, #1088
"""


def test_folds_load_through_materialised_frame_address():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1032
  ldur x9, [x15]
  b L_end
""")
    assert "  ldr x9, [sp, #56]" in out
    assert not any("x29, #1032" in line for line in out)


def test_folds_store_when_address_register_is_dead():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1024
  stur x0, [x15]
  ret
""")
    assert "  str x0, [sp, #64]" in out


def test_keeps_sub_when_address_register_is_read_again():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1024
  stur x0, [x15]
  add x1, x15, #8
  ret
""")
    assert "  sub x15, x29, #1024" in out
    assert "  stur x0, [x15]" in out


def test_keeps_sub_without_a_modelled_frame():
    out = _fold("""_f:
  sub x15, x29, #1024
  ldur x9, [x15]
  ret
""")
    assert "  sub x15, x29, #1024" in out


def test_stops_folding_after_an_unmodelled_stack_adjustment():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1024
  ldur x9, [x15]
  sub sp, sp, x11
  sub x16, x29, #1024
  ldur x10, [x16]
  ret
""")
    assert "  ldr x9, [sp, #64]" in out
    assert "  sub x16, x29, #1024" in out


def test_ordinary_epilogue_keeps_the_frame_modelled_for_later_blocks():
    out = _fold(_PROLOGUE + """  add sp, sp, #1088
  ldp x29, x30, [sp], #16
  autiasp
  ret
L_more:
  sub x15, x29, #1024
  ldur x9, [x15]
  ret
""")
    assert "  ldr x9, [sp, #64]" in out


def test_byte_access_uses_the_unscaled_immediate_form():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1025
  ldurb w9, [x15]
  ret
""")
    assert "  ldrb w9, [sp, #63]" in out


def test_misaligned_word_slot_falls_back_to_unscaled_sp_addressing():
    out = _fold(_PROLOGUE + """  sub x15, x29, #1028
  ldur x9, [x15]
  ret
""")
    assert "  ldur x9, [sp, #60]" in out


def test_slot_above_the_frame_pointer_is_not_folded():
    out = _fold(_PROLOGUE + """  sub x15, x29, #4096
  ldur x9, [x15]
  ret
""")
    assert "  sub x15, x29, #4096" in out


def test_pass_is_a_no_op_for_other_targets():
    # The registry keys targets by identity, e.g. "self-aarch64-darwin-v0".
    source = _PROLOGUE + "  sub x15, x29, #1024\n  ldur x9, [x15]\n  ret\n"
    ctx = SelfTargetPassContext(target_id="self-x86-64-linux-v0")
    assert FoldFrameAddressPass().run(source, ctx) == source


def test_pass_preserves_trailing_newline():
    source = _PROLOGUE + "  sub x15, x29, #1024\n  ldur x9, [x15]\n  ret\n"
    ctx = SelfTargetPassContext(target_id="self-aarch64-darwin-v0")
    assert FoldFrameAddressPass().run(source, ctx).endswith("\n")


# ---------------------------------------------------------------------------
# Immediate folding
# ---------------------------------------------------------------------------

from pcc.backend.self_backend_target_passes import (  # noqa: E402
    FoldImmediatePass,
    fold_aarch64_immediates,
)


def _immediates(body: str) -> list[str]:
    return fold_aarch64_immediates(body.strip("\n").splitlines())


def test_folds_a_materialised_constant_into_a_compare():
    out = _immediates("""  movz x10, #1, lsl #0
  cmp x9, x10
  b.eq L_x
""")
    assert out == ["  cmp x9, #1", "  b.eq L_x"]


def test_folds_into_a_three_operand_add():
    out = _immediates("""  movz x10, #24
  add x11, x9, x10
  ret
""")
    assert "  add x11, x9, #24" in out


def test_keeps_the_constant_when_the_register_is_read_again():
    out = _immediates("""  movz x10, #1
  cmp x9, x10
  add x12, x11, x10
  ret
""")
    assert "  movz x10, #1" in out
    assert "  cmp x9, x10" in out


def test_refuses_an_immediate_wider_than_the_add_sub_field():
    out = _immediates("""  movz x10, #65536
  cmp x9, x10
  ret
""")
    assert "  movz x10, #65536" in out


def test_leaves_a_shifted_operand_form_alone():
    out = _immediates("""  movz x10, #3
  add x11, x9, x10, lsl #2
  ret
""")
    assert "  add x11, x9, x10, lsl #2" in out


def test_drops_a_register_copy_that_is_immediately_undone():
    out = _immediates("""  mov x1, x9
  mov x9, x1
  cmp x9, #0
  ret
""")
    assert out == ["  mov x1, x9", "  cmp x9, #0", "  ret"]


def test_keeps_a_copy_pair_when_the_scratch_is_read_later():
    out = _immediates("""  mov x1, x9
  mov x9, x1
  add x2, x1, #8
  ret
""")
    assert "  mov x9, x1" in out


def test_immediate_pass_is_a_no_op_for_other_targets():
    source = "  movz x10, #1\n  cmp x9, x10\n"
    ctx = SelfTargetPassContext(target_id="self-x86-64-linux-v0")
    assert FoldImmediatePass().run(source, ctx) == source
