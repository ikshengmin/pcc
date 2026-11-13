"""CI-only exact event-sequence tests for the final typed CFG passes."""
import pytest

from pcc.backend import self_backend_aarch64_typed_cfg as typed
from pcc.backend import self_backend_aarch64_darwin as reference
from pcc.backend.self_backend_aarch64_semantics import TOKEN_REGISTER, TOKEN_SYMBOL


class Events:
    """Fixture provenance supplied before encoding, never decoded from text."""
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
        record = len(self.events)
        self.events.append((text, opcode, list(operands), label, directive, empty))
        return record

    def emit(self, opcode, operands):
        target = self.symbol(operands[-1][1])
        if opcode in ("cbz", "cbnz"):
            reg = operands[0][1]
            spelling = ("w" + str(reg - 32)) if reg >= 32 else ("x" + str(reg))
            text = "  " + opcode + " " + spelling + ", " + target
        else:
            text = "  " + opcode + " " + target
        return self.add(text, opcode, operands)

    def branch(self, opcode, name, reg=9):
        tokens = [(TOKEN_SYMBOL, self.intern(name), 0, 0)]
        if opcode in ("cbz", "cbnz"):
            tokens.insert(0, (TOKEN_REGISTER, reg, 0, 0))
        return self.emit(opcode, tokens)

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


@pytest.mark.parametrize("padding", [0, 5995, 5996, 5997])
@pytest.mark.parametrize("opcode", ["b", "cbz", "cbnz", "b.ne"])
def test_threading_uses_all_event_distances(opcode, padding):
    ctx = Events()
    records = [ctx.mark("_first"), ctx.branch(opcode, "L_trampoline"), ctx.branch("b", "L_finish"),
               ctx.mark("L_trampoline"), ctx.branch("b", "L_target")]
    records += [ctx.add("", empty=True) for _ in range(padding)]
    records += [ctx.mark("L_target"), ctx.add("  ret", "ret"), ctx.mark("L_finish")]
    expected = reference._thread_trampoline_branches(ctx.text(records))
    assert ctx.text(typed.thread_trampoline_branches_records(ctx, records)) == expected


@pytest.mark.parametrize("protect", [False, True])
@pytest.mark.parametrize("cycle", [False, True])
def test_module_threading_cycles_metadata_and_fallthrough(protect, cycle):
    ctx = Events()
    records = [ctx.mark("_f"), ctx.branch("b", "L_a"), ctx.mark("L_a"), ctx.branch("b", "L_b"),
               ctx.mark("L_b"), ctx.branch("b", "L_a" if cycle else "L_end"),
               ctx.mark("L_pcc_smap_1"), ctx.branch("b", "L_end"), ctx.mark("_g"),
               ctx.branch("b", "L_a"), ctx.mark("L_end"), ctx.add("  ret", "ret")]
    metadata = {"L_a"} if protect else set()
    expected = reference._thread_trampoline_branches(ctx.text(records), metadata)
    actual = typed.thread_trampoline_branches_records(ctx, records, [ctx.intern(name) for name in metadata])
    assert ctx.text(actual) == expected


@pytest.mark.parametrize("opcode", ["cbz", "cbnz", "b.eq", "b.cs", "b.hs", "b.al"])
@pytest.mark.parametrize("proof", [False, True])
def test_cold_fallthrough_requires_exact_proof_and_condition_alias(opcode, proof):
    ctx = Events()
    records = [ctx.mark("_f"), ctx.mark("L_source"), ctx.mark("L_pcc_smap_1"),
               ctx.branch(opcode, "L_success"), ctx.branch("b", "L_error"),
               ctx.add("", empty=True), ctx.mark("L_success"), ctx.add("  ret", "ret"),
               ctx.mark("L_error"), ctx.add("  ret", "ret")]
    edges = [("L_source", "L_error", "L_success")] if proof else []
    expected = reference._fold_cond_branch_to_fallthrough(ctx.text(records), edges)
    ids = [tuple(ctx.intern(name) for name in edge) for edge in edges]
    assert ctx.text(typed.fold_cond_branch_to_fallthrough_records(ctx, records, ids)) == expected


@pytest.mark.parametrize("middle", ["empty", "directive", "nop", "label"])
def test_fallthrough_deletion_crosses_only_empty_events(middle):
    ctx = Events()
    event = {"empty": ctx.add("", empty=True), "directive": ctx.add(".p2align 2", directive=True),
             "nop": ctx.add("  nop", "nop"), "label": ctx.mark("L_alias")}[middle]
    records = [ctx.branch("b", "L_target"), event, ctx.mark("L_target")]
    expected = reference._drop_fallthrough_uncond_branches(ctx.text(records))
    assert ctx.text(typed.drop_fallthrough_uncond_branches_records(ctx, records)) == expected


def test_empty_labels_keep_stackmap_metadata_nop_and_module_eof():
    ctx = Events()
    records = [ctx.mark("L_dead"), ctx.add("", empty=True), ctx.mark("L_metadata"),
               ctx.mark("L_pcc_smap_1"), ctx.mark("L_nop"), ctx.add("  nop", "nop"),
               ctx.mark("L_end"), ctx.mark("_next"), ctx.mark("L_eof")]
    expected = reference._drop_unreferenced_empty_local_labels(ctx.text(records), {"L_metadata"})
    actual = typed.drop_unreferenced_empty_local_labels_records(ctx, records, [ctx.intern("L_metadata")])
    assert ctx.text(actual) == expected


@pytest.mark.parametrize("padding", [199995, 199996, 199997])
def test_conditional_threading_exact_two_hundred_thousand_event_guard(padding):
    ctx = Events()
    blank = ctx.add("", empty=True)
    records = [ctx.mark("_f"), ctx.branch("b.eq", "L_trampoline"), ctx.branch("b", "L_end"),
               ctx.mark("L_trampoline"), ctx.branch("b", "L_target")]
    records += [blank] * padding
    records += [ctx.mark("L_target"), ctx.add("  ret", "ret"), ctx.mark("L_end")]
    expected = reference._thread_trampoline_branches(ctx.text(records))
    assert ctx.text(typed.thread_trampoline_branches_records(ctx, records)) == expected
