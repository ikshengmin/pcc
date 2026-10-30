"""Physical frame registration groups; each cell keeps its ownership lifetime."""
from pcc.ir.compat import ir
from pcc.frontends.python.codegen.errors import L1CodegenError

_CSTR = ir.IntType(8).as_pointer()
_I32 = ir.IntType(32)
_OWNING_GROUP_SIZE = 8
_BORROWED_GROUP_LIMIT = 16


def begin_function_root_groups(host, parameter_count):
    """Enable groups only in an ordinary function body, never an ABI adapter."""
    fn = host.current_function
    if (fn is None or host.current_func_def is None
            or host._freestanding_module or host._runtime_port_module
            or getattr(host, "_suppress_implicit_gc_roots", False)
            or getattr(host, "_generator_ctx_stack", ())
            or getattr(host, "_async_body_depth", 0)
            or host._is_valueclass_payload_type(host.current_func_def.return_ty)):
        return
    host._function_root_groups[fn.name] = {
        "function": fn, "members": {}, "owned": [], "borrowed": [],
        "borrowed_capacity": min(_BORROWED_GROUP_LIMIT, parameter_count),
    }


def _function_root_context(host):
    fn = host.current_function
    if fn is None:
        return None
    context = getattr(host, "_function_root_groups", {}).get(fn.name)
    if context is None or context["function"] is not fn:
        return None
    return context


def frame_root_group(host, slot):
    from pcc.frontends.python.codegen.generator_lowering import (
        generator_operand_root_group,
    )
    group = generator_operand_root_group(host, slot)
    if group is not None:
        return group
    context = _function_root_context(host)
    if context is None:
        return None
    record = context["members"].get(id(slot))
    if record is None:
        return None
    if record[0] is not slot:
        raise L1CodegenError("function root group identity mismatch")
    return record[1]


def allocate_function_root(host, name, borrowed):
    """Return a grouped cell or None when this owner cannot use entry groups.

    Only entry-to-exit registrations use this storage. Values are still pinned,
    retained, cleared and released independently at their original points.
    Positive and negative count maps preserve owning and borrowed roles.
    Fixed maps and NULL padding keep streamed IR and late exits consistent.
    """
    context = _function_root_context(host)
    if context is None:
        return None
    capacity = context["borrowed_capacity"] if borrowed else _OWNING_GROUP_SIZE
    if capacity < 2:
        return None
    kind = "borrowed" if borrowed else "owned"
    groups = context[kind]
    group = groups[-1] if groups else None
    if group is not None and not 0 <= group["used"] <= capacity:
        raise L1CodegenError("function root group index out of range")
    if group is None or group["used"] == capacity:
        base = host._alloca_in_entry(
            ir.ArrayType(_CSTR, capacity), name=host._fresh("fn.roots." + kind),
        )
        frame_map = ir.GlobalVariable(
            host.module, _I32, name="." + host._fresh("pcc.gc.frame.map.fn." + kind),
        )
        frame_map.linkage = "internal"
        frame_map.global_constant = True
        frame_map.initializer = ir.Constant(_I32, -capacity if borrowed else capacity)
        saved_block = host.builder._block
        host._position_at_entry_hoist_point()
        for index in range(capacity):
            cell = host.builder.gep(
                base, [ir.Constant(_I32, 0), ir.Constant(_I32, index)],
                name=host._fresh("fn.roots.empty"),
            )
            host.builder.store(ir.Constant(_CSTR, None), cell)
        host.builder.position_at_end(saved_block)
        group = {"base": base, "map": frame_map, "used": 0,
                 "registered": False, "borrowed": borrowed}
        groups.append(group)
    index = group["used"]
    saved_block = host.builder._block
    host._position_at_entry_hoist_point()
    slot = host.builder.gep(
        group["base"], [ir.Constant(_I32, 0), ir.Constant(_I32, index)], name=name,
    )
    host.builder.position_at_end(saved_block)
    group["used"] = index + 1
    context["members"][id(slot)] = (slot, group)
    return slot
