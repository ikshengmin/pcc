"""Private retry storage shared by sequential socket operations in a resume frame."""

from pcc.ir.compat import ir
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS


TCP_ARGUMENTS = (".tcp.arg0", ".tcp.arg1", ".tcp.arg2")
TCP_FIELDS = ("deadline", "progress", "generation")
# The list-backed frame has its argument tuple at index zero.
TCP_DEADLINE = 1
TCP_PROGRESS = 2
TCP_GENERATION = 3
_I64 = ir.IntType(64)


class TCPRetryFrame:
    def __init__(self, arguments, fields):
        self.arguments = arguments
        self.fields = fields


def tcp_retry_frame(host, argument_count):
    ctx = host._generator_ctx_stack[-1]
    fields = ctx.get("tcp_scalar_fields")
    if fields is None:
        # Each entry (including a resume on a new carrier) computes its own
        # addresses. The GC traces exactly the reference prefix of this frame.
        saved = host.builder._block
        host._position_at_entry_hoist_point()
        frame = host.current_function.args[1]
        length_slot = host.builder.gep(frame, [
            ir.Constant(_I64, ABI_CONSTANTS["object.gen.inline_length_offset"]),
        ])
        length = host.builder.load(host.builder.bitcast(length_slot, _I64.as_pointer()))
        size = host.builder.mul(length, ir.Constant(_I64, ABI_CONSTANTS["object.pointer.size"]))
        offset = host.builder.add(size, ir.Constant(_I64, ABI_CONSTANTS["object.gen.inline_items_offset"]))
        tail = host.builder.bitcast(host.builder.gep(frame, [offset]), _I64.as_pointer())
        fields = {}
        for index, field in enumerate(TCP_FIELDS):
            fields[field] = host.builder.gep(tail, [ir.Constant(_I64, index)], name="tcp." + field)
        ctx["tcp_scalar_fields"] = fields
        host.builder.position_at_end(saved)
    arguments = tuple(ctx["frame_slots"][name][1] for name in TCP_ARGUMENTS[:argument_count])
    return TCPRetryFrame(arguments, fields)


def prepare_tcp_retry_frame(host, args, span, timeout_index, initial_progress, lexical):
    """Keep evaluated operands alive for exactly one socket operation."""
    ctx = host._generator_ctx_stack[-1]
    frame = tcp_retry_frame(host, len(args))
    # Evaluate the entire argument list before claiming the shared
    # cells. A nested socket operation can park in a later argument.
    previous = host._current_try_err_block()
    target = previous if previous is not None else host._ensure_fn_err_exit()
    saved_cpy = host._cpy_operand_cleanup_block
    previous_lexical = ctx.get("lexical_slot_operands", False)
    initial_state = ctx["next_state"]
    ctx["lexical_slot_operands"] = lexical
    roots = []
    try:
        for arg in args:
            roots.append(host._emit_slot_call_operand(arg, "tcp.argument"))
            host._try_err_block = host._slot_call_cleanup_block(tuple(roots), target)
            host._cpy_operand_cleanup_block = host._try_err_block
        timeout = ir.Constant(_I64, -1)
        if timeout_index < len(roots):
            timeout = host.builder.call(
                host.runtime["py_int_value_i64"], [host.builder.load(roots[timeout_index])],
            )
            host._emit_post_call_err_check(span)
        deadline = host.builder.call(host.runtime["py_virtual_thread_tcp_deadline"], [timeout])
        host._emit_post_call_err_check(span)
        for slot, root in zip(frame.arguments, roots):
            host.builder.call(host.runtime["pcc_gc_store_root"], [slot, host.builder.load(root)])
        host._release_slot_call_roots(tuple(roots))
    finally:
        host._try_err_block = previous
        host._cpy_operand_cleanup_block = saved_cpy
        ctx["lexical_slot_operands"] = previous_lexical
        if lexical and ctx["next_state"] != initial_state:
            raise L1CodegenError("TCP operand evaluation introduced an unexpected resume edge")
    host.builder.store(deadline, frame.fields["deadline"])
    host.builder.store(ir.Constant(_I64, initial_progress), frame.fields["progress"])
    host.builder.store(ir.Constant(_I64, -1), frame.fields["generation"])
    return frame
