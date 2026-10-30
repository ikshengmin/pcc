"""Exact stack-slot placement and lifetime regressions using the real backend IR.

These host tests import the production parser, kernel and allocator. Target
triples exercise parsing and layout only; no emitted native program is built
or executed here. Expected IDs and offsets are independent of allocator helpers.
"""

from __future__ import annotations

import unittest

from pcc.backend import self_backend_stackprep
from pcc.backend.self_backend_ir import (
    ArgInfo,
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    PhiIncoming,
    PhiInstr,
    TypeDesc,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module


VOID = TypeDesc("void")
I32 = TypeDesc("int", 32)
I64 = TypeDesc("int", 64)
I128 = TypeDesc("int", 128)
F64 = TypeDesc("fp", 64)
PTR = TypeDesc("ptr", pointee=VOID)


def _define(name, ty=I64):
    if ty.is_fp:
        return ParsedInstr("fbinop", ("fadd", name, ty, "1.0", "2.0"))
    if ty.is_ptr:
        return ParsedInstr("freeze", (name, ty, "null"))
    return ParsedInstr("binop", ("add", name, ty, "1", "2"))


def _sink(values):
    args = tuple((ty, name) for name, ty in values)
    return ParsedInstr(
        "call", (None, VOID, "@sink", False, args, len(args), False, (0,) * len(args))
    )


def _function(instructions, *, blocks=None, args=None, ret_type=VOID):
    if blocks is None:
        blocks = [ParsedBlock(
            "entry", instructions=instructions,
            terminator=ParsedInstr("ret_void", ()),
        )]
    return ParsedFunction("queue_probe", ret_type, args or [], True, False, blocks)


def _wave_function(widths, *, repeated_arguments=False):
    instructions = []
    for wave, width in enumerate(widths):
        values = [(f"wave{wave}.v{i}", I64) for i in range(width)]
        instructions.extend(_define(name, ty) for name, ty in values)
        instructions.append(_sink(values * (3 if repeated_arguments else 1)))
    return _function(instructions)


def _allocate(fn, *, legacy=True, indirect=False):
    self_backend_stackprep.assign_stack_slots(
        fn,
        aggregate_returned_indirect=lambda ty: indirect and ty.is_struct,
        aggregate_returned_indirect_indexed=(
            lambda kernel, type_id: indirect and kernel.types[type_id].is_struct
        ),
        materialize_legacy_slots=legacy,
    )


class StackprepLayoutTests(unittest.TestCase):
    def assert_slots(self, fn, names, expected_ids, expected_offsets):
        kernel = get_indexed_function_kernel(fn)
        values = [kernel.value_id(name) for name in names]
        self.assertTrue(all(value_id >= 0 for value_id in values))
        self.assertEqual(expected_ids, [kernel.value_slot_id(value_id) for value_id in values])
        self.assertEqual(expected_offsets, [kernel.value_slot_offset(value_id) for value_id in values])

    def assert_wave(self, fn, wave, ids):
        self.assert_slots(
            fn, [f"wave{wave}.v{i}" for i in range(len(ids))],
            ids, [8 * (slot_id + 1) for slot_id in ids],
        )

    def test_single_slot_reuse_and_projection_contract(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                fn = _function([], blocks=[ParsedBlock(
                    "entry", instructions=[
                        _define("first"),
                        ParsedInstr("binop", ("add", "discard", I64, "first", "3")),
                        _define("second"),
                    ], terminator=ParsedInstr("ret", (I64, "second")),
                )], ret_type=I64)
                _allocate(fn, legacy=legacy)
                kernel = get_indexed_function_kernel(fn)
                self.assert_slots(fn, ["first", "second", "discard"], [0, 0, -1], [8, 8, -1])
                self.assertEqual(2, len(kernel.slot_scalars))
                self.assertEqual(16, fn.frame_size)
                self.assertEqual(not legacy, fn.indexed_slot_projection)
                if legacy:
                    self.assertIs(fn.value_slots["first"], fn.value_slots["second"])
                    self.assertEqual({"first", "second"}, set(fn.value_slots))
                else:
                    self.assertEqual({}, fn.value_slots)

    def test_real_parser_fifo_layout_for_four_target_triples(self):
        width = 65
        signature = ", ".join("i64" for _ in range(width))
        body = []
        for wave in range(2):
            body.extend(f"  %wave{wave}.v{i} = add i64 {i}, 1" for i in range(width))
            args = ", ".join(f"i64 %wave{wave}.v{i}" for i in range(width))
            body.append(f"  call void @sink({args})")
        for target in (
            "arm64-apple-darwin", "aarch64-unknown-linux-gnu",
            "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc",
        ):
            text = (f'target triple = "{target}"\n'
                    f"declare void @sink({signature})\n"
                    "define void @queue_probe() {\nentry:\n"
                    + "\n".join(body) + "\n  ret void\n}\n")
            for legacy in (True, False):
                with self.subTest(target=target, legacy=legacy):
                    module = parse_self_backend_module(text)
                    self.assertEqual(target, module.triple)
                    self.assertEqual(1, len(module.functions))
                    fn = module.functions[0]
                    _allocate(fn, legacy=legacy)
                    for wave in range(2):
                        self.assert_wave(fn, wave, list(range(width)))
                    self.assertEqual(2 * width, len(get_indexed_function_kernel(fn).slot_scalars))
                    self.assertEqual(528, fn.frame_size)

    def test_fifo_bulk_free_and_reallocation_exact_offsets(self):
        for width in (0, 1, 2, 63, 64, 65, 127, 128, 129, 257):
            for legacy in (True, False):
                with self.subTest(width=width, legacy=legacy):
                    fn = _wave_function((width, width, width))
                    _allocate(fn, legacy=legacy)
                    for wave in range(3):
                        self.assert_wave(fn, wave, list(range(width)))
                    self.assertEqual(2 * width, len(get_indexed_function_kernel(fn).slot_scalars))
                    self.assertEqual(((width * 8 + 15) // 16) * 16, fn.frame_size)
                    if legacy:
                        for i in range(width):
                            self.assertIs(fn.value_slots[f"wave0.v{i}"], fn.value_slots[f"wave2.v{i}"])
                    else:
                        self.assertEqual({}, fn.value_slots)

    def test_partial_drain_preserves_fifo_before_newly_freed_slots(self):
        for width, prefix in ((5, 2), (65, 64), (129, 65), (257, 130)):
            for legacy in (True, False):
                with self.subTest(width=width, prefix=prefix, legacy=legacy):
                    fn = _wave_function((width, prefix, width))
                    _allocate(fn, legacy=legacy)
                    self.assert_wave(fn, 0, list(range(width)))
                    self.assert_wave(fn, 1, list(range(prefix)))
                    self.assert_wave(fn, 2, list(range(prefix, width)) + list(range(prefix)))
                    self.assertEqual(2 * width, len(get_indexed_function_kernel(fn).slot_scalars))

    def test_type_identity_not_just_slot_size_controls_reuse(self):
        types = (I32, I64, F64, PTR, I128)
        width = 135
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                instructions = []
                for wave, order in enumerate((types, types[::-1], types)):
                    values = [(f"wave{wave}.v{i}", order[i % 5]) for i in range(width)]
                    instructions.extend(_define(name, ty) for name, ty in values)
                    instructions.append(_sink(values))
                fn = _function(instructions)
                _allocate(fn, legacy=legacy)
                kernel = get_indexed_function_kernel(fn)
                # One five-type group occupies 48 bytes, including alignment.
                first_offsets = [48 * (i // 5) + (4, 16, 24, 32, 48)[i % 5] for i in range(width)]
                for wave in range(3):
                    ids = [(i // 5) * 5 + (4 - i % 5 if wave == 1 else i % 5) for i in range(width)]
                    names = [f"wave{wave}.v{i}" for i in range(width)]
                    self.assert_slots(fn, names, ids, [first_offsets[slot_id] for slot_id in ids])
                    for name, slot_id in zip(names, ids):
                        type_id = kernel.value_slot_type_id(kernel.value_id(name))
                        self.assertEqual(types[slot_id % 5], kernel.types[type_id])
                        if legacy:
                            self.assertIs(fn.value_slots[f"wave0.v{slot_id}"], fn.value_slots[name])
                self.assertEqual(2 * width, len(kernel.slot_scalars))
                self.assertEqual(1296, fn.frame_size)

    def test_duplicate_uses_never_double_free(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                fn = _wave_function((129, 129), repeated_arguments=True)
                _allocate(fn, legacy=legacy)
                for wave in range(2):
                    self.assert_wave(fn, wave, list(range(129)))
                self.assertEqual(258, len(get_indexed_function_kernel(fn).slot_scalars))

    def test_cross_block_phi_alloca_and_unused_values_keep_lifetimes(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                entry = ParsedBlock("entry", instructions=[
                    ParsedInstr("alloca", ("buffer", TypeDesc("array", count=9, elem=TypeDesc("int", 8)))),
                    _define("cross"), _define("local"), _define("unused"),
                    _sink([("local", I64), ("buffer", PTR)]),
                ], terminator=ParsedInstr("br", ("body",)))
                body = ParsedBlock("body", phis=(
                    PhiInstr("merged", I64, (PhiIncoming("arg", "entry"),)),
                ), instructions=[
                    ParsedInstr("binop", ("add", "first", I64, "cross", "merged")),
                    _sink([("first", I64)]), _define("second"),
                ], terminator=ParsedInstr("ret", (I64, "second")))
                fn = _function([], blocks=[entry, body], args=[ArgInfo("arg", I64)], ret_type=I64)
                _allocate(fn, legacy=legacy)
                kernel = get_indexed_function_kernel(fn)
                self.assert_slots(
                    fn, ["arg", "cross", "local", "merged", "first", "second", "unused", "buffer"],
                    [0, 1, 2, 2, 3, 2, -1, -1], [8, 32, 40, 40, 48, 40, -1, -1],
                )
                self.assertIsNone(kernel.last_use(0, kernel.value_id("cross")))
                self.assertEqual(24, kernel.alloca_offset(kernel.value_id("buffer")))
                self.assertEqual(8, len(kernel.slot_scalars))
                self.assertEqual(48, fn.frame_size)
                if legacy:
                    self.assertIs(fn.value_slots["local"], fn.value_slots["merged"])
                    self.assertIs(fn.value_slots["local"], fn.value_slots["second"])
                    self.assertEqual(24, fn.alloca_slots["buffer"].offset)
                else:
                    self.assertEqual({}, fn.value_slots)
                    self.assertEqual({}, fn.alloca_slots)

    def test_indirect_return_and_discarded_aggregate_slots(self):
        structure = TypeDesc("struct", fields=(I64, I64, I64))
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                instructions = [ParsedInstr(
                    "call", ("discarded", structure, "@make", False, (), 0, False, ())
                )]
                fn = _function([], blocks=[ParsedBlock(
                    "entry", instructions=instructions,
                    terminator=ParsedInstr("ret", (structure, "zeroinitializer")),
                )], ret_type=structure)
                _allocate(fn, legacy=legacy, indirect=True)
                kernel = get_indexed_function_kernel(fn)
                self.assertEqual(0, kernel.hidden_sret_slot_id)
                self.assertEqual(8, kernel.slot_offset(kernel.hidden_sret_slot_id))
                self.assertEqual(PTR, kernel.types[kernel.slot_type_id(kernel.hidden_sret_slot_id)])
                self.assert_slots(fn, ["discarded"], [1], [32])
                self.assertFalse(kernel.value_is_used(kernel.value_id("discarded")))
                self.assertEqual(4, len(kernel.slot_scalars))
                self.assertEqual(32, fn.frame_size)
                if legacy:
                    self.assertEqual(8, fn.hidden_sret_slot.offset)
                    self.assertEqual(structure, fn.value_slots["discarded"].type)
                else:
                    self.assertIsNone(fn.hidden_sret_slot)
                    self.assertEqual({}, fn.value_slots)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
