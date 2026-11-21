"""Private M10 proof of a fresh owner's use as a nonescaping numeric operand.

The generator map by itself does not establish lifetime. Only the enclosing
binary emitter can issue a proof, for one expression by identity, consumed
and retired as one operand of a *physically emitted* audited binary ABI.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.py_ast import BinOp, Call, FloatLit, FloatType, Name
from pcc.ir.compat import ir


# Exact FLOAT/FLOAT takes these runtimes' built-in numeric arms, not
# reflected dispatch, an extension, or a sequence/user callback. No change
# to their calls, input leases or to transfer/publication of their outputs.
CONSUMERS = frozenset(("py_obj_add", "py_obj_sub", "py_obj_mul_slots"))


def exact_float_literal(expr):
    return (isinstance(expr, FloatLit) and isinstance(expr.ty, FloatType)
            and expr.ty.name == "float" and expr.ty.width == 64)


def provider_candidate(expr):
    # Syntax is an exclusion, not admission. Only an actual live-binding
    # guarded native direct call with a physical FP return can prove this.
    return isinstance(expr, Call) and not expr.args and not expr.kwargs


def numeric_scope(host, expr, runtime_name):
    if (runtime_name not in CONSUMERS
            or os.environ.get("PCC_EXPERIMENT_GC3_MAPPED_FLOAT") != "1"
            or not getattr(host, "_strict_no_libpython", False)
            or getattr(host, "_freestanding_module", False)
            or getattr(host, "_runtime_port_module", False)):
        return None
    from pcc.frontends.python.codegen.generator_lowering import (
        _spill_function, generator_expr_suspends,
    )
    owner = _spill_function(host)
    if owner is None or generator_expr_suspends(host, expr, owner[0], owner[1]):
        return None
    return host._generator_ctx_stack[-1]


def exact_local_source(host, ctx, expr):
    """A plain existing owning local; its *value* will be checked only at use."""
    if not isinstance(expr, Name):
        return False
    from pcc.frontends.python.codegen.cpy_import_state import live_import_name_slot
    from pcc.frontends.python.codegen.generator_lowering import _spill_plain_local
    fd = host.current_func_def
    if (any(arg.name == expr.ident for arg in fd.args)
            or not _spill_plain_local(host, fd, expr.ident)
            or live_import_name_slot(host, expr.ident) is not None
            or host._cpy_env_flags.get(expr.ident, False)
            or expr.ident in host._borrowed_gc_rooted_local_names):
        return False
    entry = host.env.get(expr.ident)
    frame = ctx["frame_slots"].get(expr.ident)
    if entry is None or frame is None or entry[0] is not frame[1]:
        return False
    slot = entry[0]
    if (host._slot_call_root_is_registered(slot)
            or not host._ir_type_matches(entry[1], ir.IntType(8).as_pointer())
            or host._owned_local_flag_for(expr.ident, slot) is None):
        return False
    return any(r[0] == expr.ident and r[1] is slot for r in
               host._fn_gc_root_slot_registry.get(host.current_function.name, ()))


@dataclass
class OperandProof:
    """Codegen-only obligation; never materialized in Python/runtime state."""
    function: object
    context: dict
    continuation: int
    expression: object
    publish: bool
    witness_direct: bool
    placement: object = None
    exact_sub_inputs: object = None
    direct_native_arm: object = None
    helper_slot: object = None
    provider_slot: object = None

    def current(self, host):
        contexts = getattr(host, "_generator_ctx_stack", ())
        return (host.current_function is self.function and bool(contexts)
                and contexts[-1] is self.context
                and self.context["next_state"] == self.continuation)

    def group_of(self, host, slot):
        if (not self.current(host) or not host._slot_call_root_is_registered(slot)
                or getattr(host, "_slot_call_borrowed_operands", {}).get(id(slot)) is slot
                or getattr(host, "_exp2_opaque_result_roots", {}).get(id(slot)) is slot):
            return None
        owned, flag, lifo = host._slot_call_root_record(slot)
        member = self.context.get("operand_root_group_members", {}).get(id(slot))
        if (owned is not slot or flag is None or lifo or member is None or member[0] is not slot
                or not any(r[1] is slot for r in
                           host._fn_gc_root_slot_registry.get(self.function.name, ()))):
            return None
        _, group, index = member
        count = group["map"].initializer
        if (not group["registered"] or count.type != ir.IntType(32)
                or not isinstance(count.value, int)
                or not 0 <= index < group["used"] <= count.value or count.value < 2):
            return None
        return group


def current_proof(host, expr):
    p = getattr(host, "_exp2_mapped_float_operand", None)
    return p if p is not None and p.expression is expr and p.current(host) else None


def emit_operand(host, expr, label, ctx, publish, witness_direct, placement=None):
    """Emit exactly one staged operand through the ordinary owning binder."""
    proof = OperandProof(host.current_function, ctx, ctx["next_state"], expr, publish, witness_direct, placement)
    previous = getattr(host, "_exp2_mapped_float_operand", None)
    host._exp2_mapped_float_operand = proof
    try:
        slot = host._emit_slot_call_operand(expr, label)
    finally:
        host._exp2_mapped_float_operand = previous
    for used in (proof.helper_slot, proof.provider_slot):
        if used is not None and (used is not slot or not proof.current(host)):
            raise L1CodegenError("M10 float operand escaped its emitted owning scope")
    return slot, proof


@dataclass
class TerminalFloatPlacement:
    """Two placements or neither, issued only by the real generator return."""
    function: object
    context: dict
    next_state: int
    expression: object
    subtraction: object
    producer: object
    box_slot: object = None
    output_slot: object = None

    def current(self, host):
        contexts = getattr(host, "_generator_ctx_stack", ())
        return (host.current_function is self.function and bool(contexts)
                and contexts[-1] is self.context
                and self.context["next_state"] == self.next_state)


def emit_generator_terminal_object(host, expr):
    """Only a direct terminal result of this activation is a proven escape."""
    if (os.environ.get("PCC_EXPERIMENT_GC3_FLOAT_PLACEMENT") != "1"
            or os.environ.get("PCC_EXPERIMENT_GC3_LEASE_SITES") != "1"
            or host._finally_stack or not isinstance(expr, BinOp) or expr.op != "*"
            or not exact_float_literal(expr.rhs)
            or not isinstance(expr.lhs, BinOp)
            or host._slot_call_binary_runtime(expr) != "py_obj_mul_slots"
            or host._slot_call_binary_runtime(expr.lhs, object_boundary=True) != "py_obj_sub"
            or not provider_candidate(expr.lhs.lhs)):
        return host._emit_as_object(expr)
    ctx = numeric_scope(host, expr, "py_obj_mul_slots")
    if ctx is None or not (exact_float_literal(expr.lhs.rhs)
                           or exact_local_source(host, ctx, expr.lhs.rhs)):
        return host._emit_as_object(expr)
    placement = TerminalFloatPlacement(
        host.current_function, ctx, ctx["next_state"], expr, expr.lhs, expr.lhs.lhs,
    )
    previous = getattr(host, "_exp2_terminal_float_placement", None)
    host._exp2_terminal_float_placement = placement
    try:
        result = host._emit_as_object(expr)
    finally:
        host._exp2_terminal_float_placement = previous
    if ((placement.box_slot is None) != (placement.output_slot is None)
            or (placement.box_slot is not None and not placement.current(host))):
        raise L1CodegenError("M11 FLOAT placement is not a complete terminal lifetime")
    return result


def terminal_placement(host, expr):
    p = getattr(host, "_exp2_terminal_float_placement", None)
    return p if p is not None and p.expression is expr and p.current(host) else None


def emit_old_multiplication(host, args, exact_inputs):
    """Only the known exact branch selects old placement, never a lease bypass."""
    fn = host.current_function
    old_bb = fn.append_basic_block(host._fresh("exp2.place.mul.old"))
    stock_bb = fn.append_basic_block(host._fresh("exp2.place.mul.stock"))
    done_bb = fn.append_basic_block(host._fresh("exp2.place.mul.done"))
    host.builder.cbranch(exact_inputs, old_bb, stock_bb)
    host.builder.position_at_end(old_bb)
    ptr = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    old_mul = host._declare_external_function(
        "pcc_exp2_obj_mul_slots_old", i64, [ptr, ptr, ptr],
    )
    old_status = host.builder.call(old_mul, args, name=host._fresh("exp2.place.mul.status"))
    host.builder.branch(done_bb)
    host.builder.position_at_end(stock_bb)
    stock_status = host.builder.call(
        host.runtime["py_obj_mul_slots"], args, name=host._fresh("binary.slot.invoke"),
    )
    host.builder.branch(done_bb)
    host.builder.position_at_end(done_bb)
    result = host.builder.phi(i64, name=host._fresh("exp2.place.mul.result"))
    result.add_incoming(old_status, old_bb)
    result.add_incoming(stock_status, stock_bb)
    return result
