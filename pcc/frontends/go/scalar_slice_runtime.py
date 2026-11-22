"""Opt-in Go scalar backing calls to the shared managed envelope.

This does not wire up Go values, mutation, roots, or result lowering. Callers
must consume allocation status and adopt an owning handle before exposing
payload. The default Go compiler and default runtime build never select it.
"""
from pcc.ir.compat import ir
from pcc.support.managed_objects import ManagedContractError, pointer_map
from .managed_objects import scalar_slice_backing_plan


# Existing pcc_shared_managed.h status, not a Go panic or error value.
_OVERFLOW = 2
_BAD_LAYOUT = 3


class ScalarSliceRuntime:
    """One module's immutable runtime-v1 descriptors and exact allocation ABI."""

    def __init__(self, module, *, gc_backend):
        if (module.triple != "x86_64-unknown-linux-gnu" or
                type(gc_backend) is not int or gc_backend != 0):
            raise ManagedContractError("Go scalar slice envelope requires Linux x86-64 GC0")
        self.module = module
        self.descriptors = {}
        self.first_appends = {}
        self.full_appends = {}
        self.in_capacity_appends = {}

    def descriptor(self, element):
        layout = scalar_slice_backing_plan(element).layout
        if element not in self.descriptors:
            refs = pointer_map(layout, pointer_size=8)
            if (refs.element is None or refs.owned or refs.borrowed or refs.update_only
                    or refs.element.owned or refs.element.borrowed or refs.element.update_only):
                raise ManagedContractError("scalar slice descriptor cannot contain reference slots")
            i64 = ir.IntType(64)
            storage = ir.GlobalVariable(self.module, ir.ArrayType(i64, 6),
                                        "__pcc_go_scalar_backing_" + element)
            storage.linkage = "private"
            storage.global_constant = True
            # ABI v1: version, fixed/element payload, alignment, fixed/element refs.
            values = (1, refs.fixed_size, refs.element_stride, refs.alignment, 0, 0)
            storage.initializer = ir.Constant(storage.value_type, [ir.Constant(i64, n) for n in values])
            self.descriptors[element] = storage
        return self.descriptors[element]

    def _matching_function(self, name, signature):
        existing = self.module.globals.get(name)
        if existing is not None and (not isinstance(existing, ir.Function)
                                     or existing.function_type != signature):
            raise ManagedContractError("incompatible shared managed ABI: " + name)
        return existing

    def allocate(self, builder, element, count, output):
        """Emit status = alloc(descriptor, i64 count, handle **out)."""
        pointer = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)
        if (builder.block.function.module is not self.module or
                count.type != i64 or output.type != pointer.as_pointer()):
            raise ManagedContractError("scalar slice allocation requires its module, i64 count and handle output")
        signature = ir.FunctionType(ir.IntType(32), [pointer, i64, pointer.as_pointer()])
        name = "pcc_rt_managed_alloc"
        existing = self._matching_function(name, signature)
        desc = self.descriptor(element)
        alloc = existing if existing is not None else ir.Function(self.module, signature, name)
        return builder.call(alloc, [builder.bitcast(desc, pointer), count, output])

    def first_append_function(self, element):
        """Nil plus one scalar, returning status and an owning result handle.

        The caller adopts this new handle and uses an offset/length/capacity
        of 0/1/1. Output must not alias a prior live handle or managed payload.
        Failure leaves output untouched. No safepoint follows data-address
        resolution, and the helper never leaks its temporary handle slot.
        """
        layout = scalar_slice_backing_plan(element).layout
        if element in self.first_appends:
            return self.first_appends[element]
        pointer = ir.IntType(8).as_pointer()
        handle_out = pointer.as_pointer()
        i32 = ir.IntType(32)
        i64 = ir.IntType(64)
        word = ir.IntType(8 * layout.element_stride)
        self._matching_function("pcc_rt_managed_alloc", ir.FunctionType(i32, [pointer, i64, handle_out]))
        data_type = ir.FunctionType(pointer, [pointer])
        data_name = "pcc_rt_managed_data"
        data = self._matching_function(data_name, data_type)
        fn = ir.Function(self.module, ir.FunctionType(i32, [word, handle_out]),
                         "__pcc_go_scalar_append_first_" + element)
        fn.linkage = "internal"
        value, output = fn.args
        builder = ir.IRBuilder(fn.append_basic_block("entry"))
        allocate = fn.append_basic_block("scalar_append_allocate")
        invalid = fn.append_basic_block("scalar_append_invalid")
        ready = fn.append_basic_block("scalar_append_ready")
        done = fn.append_basic_block("scalar_append_done")
        builder.cbranch(builder.icmp_unsigned("==", output, ir.Constant(handle_out, None)), invalid, allocate)
        builder.position_at_end(invalid)
        builder.ret(ir.Constant(i32, _BAD_LAYOUT))
        builder.position_at_end(allocate)
        temporary = builder.alloca(pointer)
        status = self.allocate(builder, element, ir.Constant(i64, 1), temporary)
        builder.cbranch(builder.icmp_unsigned("==", status, ir.Constant(i32, 0)), ready, done)
        builder.position_at_end(ready)
        owner = builder.load(temporary)
        if data is None:
            data = ir.Function(self.module, data_type, data_name)
        payload = builder.call(data, [owner])
        builder.store(value, builder.bitcast(payload, word.as_pointer()))
        builder.store(owner, output)
        builder.branch(done)
        builder.position_at_end(done)
        builder.ret(status)
        self.first_appends[element] = fn
        return fn

    def full_append_function(self, element):
        """Grow only a base view whose logical length equals its allocation.

        The caller keeps the source handle alive and passes a separate output
        slot. Success returns a newly owned backing; the old owner and its
        aliases remain unchanged. Logical result length is old count + 1,
        irrespective of spare capacity. No source payload address is held
        across the managed allocation.
        """
        layout = scalar_slice_backing_plan(element).layout
        if element in self.full_appends:
            return self.full_appends[element]
        pointer = ir.IntType(8).as_pointer()
        handle_out = pointer.as_pointer()
        i32 = ir.IntType(32)
        i64 = ir.IntType(64)
        stride = layout.element_stride
        word = ir.IntType(8 * stride)
        self._matching_function("pcc_rt_managed_alloc", ir.FunctionType(i32, [pointer, i64, handle_out]))
        externals = {
            "pcc_rt_managed_descriptor": ir.FunctionType(pointer, [pointer]),
            "pcc_rt_managed_element_count": ir.FunctionType(i64, [pointer]),
            "pcc_rt_managed_data": ir.FunctionType(pointer, [pointer]),
            "memcpy": ir.FunctionType(pointer, [pointer, pointer, i64]),
        }
        bindings = {name: self._matching_function(name, signature) for name, signature in externals.items()}
        for name, signature in externals.items():
            if bindings[name] is None:
                bindings[name] = ir.Function(self.module, signature, name)
        descriptor = self.descriptor(element)
        fn = ir.Function(self.module, ir.FunctionType(i32, [pointer, word, handle_out]),
                         "__pcc_go_scalar_append_full_" + element)
        fn.linkage = "internal"
        source, value, output = fn.args
        builder = ir.IRBuilder(fn.append_basic_block("entry"))
        classify = fn.append_basic_block("scalar_grow_classify")
        count_block = fn.append_basic_block("scalar_grow_count")
        invalid = fn.append_basic_block("scalar_grow_invalid")
        overflow = fn.append_basic_block("scalar_grow_overflow")
        allocate = fn.append_basic_block("scalar_grow_allocate")
        ready = fn.append_basic_block("scalar_grow_ready")
        done = fn.append_basic_block("scalar_grow_done")
        bad = builder.or_(builder.icmp_unsigned("==", source, ir.Constant(pointer, None)),
                          builder.icmp_unsigned("==", output, ir.Constant(handle_out, None)))
        builder.cbranch(bad, invalid, classify)
        builder.position_at_end(classify)
        identity = builder.call(bindings["pcc_rt_managed_descriptor"], [source])
        matches = builder.icmp_unsigned("==", identity, builder.bitcast(descriptor, pointer))
        builder.cbranch(matches, count_block, invalid)
        builder.position_at_end(invalid)
        builder.ret(ir.Constant(i32, _BAD_LAYOUT))
        builder.position_at_end(count_block)
        count = builder.call(bindings["pcc_rt_managed_element_count"], [source])
        # This bounds compiler-side scalar indexing and multiplication. The
        # allocator separately checks descriptor/header alignment overhead.
        maximum = ((1 << 63) - 1) // stride
        builder.cbranch(builder.icmp_unsigned("<", count, ir.Constant(i64, maximum)), allocate, overflow)
        builder.position_at_end(overflow)
        builder.ret(ir.Constant(i32, _OVERFLOW))
        builder.position_at_end(allocate)
        next_length = builder.add(count, ir.Constant(i64, 1))
        can_double = builder.icmp_unsigned("<=", count, ir.Constant(i64, maximum // 2))
        nonempty = builder.icmp_unsigned("!=", count, ir.Constant(i64, 0))
        capacity = builder.select(builder.and_(can_double, nonempty),
                                  builder.mul(count, ir.Constant(i64, 2)), next_length)
        temporary = builder.alloca(pointer)
        status = self.allocate(builder, element, capacity, temporary)
        builder.cbranch(builder.icmp_unsigned("==", status, ir.Constant(i32, 0)), ready, done)
        builder.position_at_end(ready)
        owner = builder.load(temporary)
        payload = builder.call(bindings["pcc_rt_managed_data"], [owner])
        old_payload = builder.call(bindings["pcc_rt_managed_data"], [source])
        size = builder.mul(count, ir.Constant(i64, stride))
        builder.call(bindings["memcpy"], [payload, old_payload, size])
        slot = builder.gep(builder.bitcast(payload, word.as_pointer()), [count])
        builder.store(value, slot)
        builder.store(owner, output)
        builder.branch(done)
        builder.position_at_end(done)
        builder.ret(status)
        self.full_appends[element] = fn
        return fn

    def in_capacity_append_function(self, element):
        """Write a scalar in a zero-offset view without changing its owner.

        The supplied logical length is the first unused element in this view,
        not stored as shared backing metadata. Advance that view's length only
        after success. This helper neither allocates nor grows a full backing.
        """
        layout = scalar_slice_backing_plan(element).layout
        if element in self.in_capacity_appends:
            return self.in_capacity_appends[element]
        pointer = ir.IntType(8).as_pointer()
        i32 = ir.IntType(32)
        i64 = ir.IntType(64)
        word = ir.IntType(8 * layout.element_stride)
        externals = {
            "pcc_rt_managed_descriptor": ir.FunctionType(pointer, [pointer]),
            "pcc_rt_managed_element_count": ir.FunctionType(i64, [pointer]),
            "pcc_rt_managed_data": ir.FunctionType(pointer, [pointer]),
        }
        bindings = {name: self._matching_function(name, signature) for name, signature in externals.items()}
        for name, signature in externals.items():
            if bindings[name] is None:
                bindings[name] = ir.Function(self.module, signature, name)
        descriptor = self.descriptor(element)
        fn = ir.Function(self.module, ir.FunctionType(i32, [pointer, i64, word]),
                         "__pcc_go_scalar_append_in_capacity_" + element)
        fn.linkage = "internal"
        owner, length, value = fn.args
        builder = ir.IRBuilder(fn.append_basic_block("entry"))
        classify = fn.append_basic_block("scalar_in_capacity_classify")
        bounds = fn.append_basic_block("scalar_in_capacity_bounds")
        invalid = fn.append_basic_block("scalar_in_capacity_invalid")
        write = fn.append_basic_block("scalar_in_capacity_write")
        builder.cbranch(builder.icmp_unsigned("==", owner, ir.Constant(pointer, None)), invalid, classify)
        builder.position_at_end(classify)
        identity = builder.call(bindings["pcc_rt_managed_descriptor"], [owner])
        matches = builder.icmp_unsigned("==", identity, builder.bitcast(descriptor, pointer))
        builder.cbranch(matches, bounds, invalid)
        builder.position_at_end(bounds)
        capacity = builder.call(bindings["pcc_rt_managed_element_count"], [owner])
        builder.cbranch(builder.icmp_unsigned("<", length, capacity), write, invalid)
        builder.position_at_end(invalid)
        builder.ret(ir.Constant(i32, _BAD_LAYOUT))
        builder.position_at_end(write)
        payload = builder.call(bindings["pcc_rt_managed_data"], [owner])
        slot = builder.gep(builder.bitcast(payload, word.as_pointer()), [length])
        builder.store(value, slot)
        builder.ret(ir.Constant(i32, 0))
        self.in_capacity_appends[element] = fn
        return fn


def compile_scalar_first_append_entry(units, *, function_name, target_triple, gc_backend):
    """Lower a checked complete producer through the explicit foreign entry.

    The generated symbol uses the Go linkage encoder, while its temporary
    ingress ABI is the shared result handle (status, out handle), not a Go
    slice return ABI. Regular package lowering does not call this function.
    """
    from .diagnostics import UnsupportedGo
    from .lowering import function_symbol
    from .packages import parse_package
    from .slice_proof import closed_scalar_first_append

    program = parse_package(units, require_main=False)
    proof = closed_scalar_first_append(program, function_name)
    if proof is None:
        token = (program.functions[0].token if len(program.functions) == 1 else program.token)
        raise UnsupportedGo(token, "go.unsupported.slice-storage",
                            "closed scalar first append requires a nil slice local and one scalar builtin append")
    module = ir.Module(name="go_scalar_slice_entry")
    module.triple = target_triple
    helper = ScalarSliceRuntime(module, gc_backend=gc_backend).first_append_function(proof.element)
    symbol = function_symbol(proof.token.text)
    fn = ir.Function(module, helper.function_type, symbol)
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    builder.ret(builder.call(helper, list(fn.args)))
    return module, symbol
