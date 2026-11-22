"""Opt-in Go scalar backing calls to the shared managed envelope.

This does not wire up Go values, mutation, roots, or result lowering. Callers
must consume allocation status and adopt an owning handle before exposing
payload. The default Go compiler and default runtime build never select it.
"""
from pcc.ir.compat import ir
from pcc.support.managed_objects import ManagedContractError, pointer_map
from .managed_objects import scalar_slice_backing_plan


class ScalarSliceRuntime:
    """One module's immutable runtime-v1 descriptors and exact allocation ABI."""

    def __init__(self, module, *, gc_backend):
        if (module.triple != "x86_64-unknown-linux-gnu" or
                type(gc_backend) is not int or gc_backend != 0):
            raise ManagedContractError("Go scalar slice envelope requires Linux x86-64 GC0")
        self.module = module
        self.descriptors = {}

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

    def allocate(self, builder, element, count, output):
        """Emit status = alloc(descriptor, i64 count, handle **out)."""
        pointer = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)
        if (builder.block.function.module is not self.module or
                count.type != i64 or output.type != pointer.as_pointer()):
            raise ManagedContractError("scalar slice allocation requires its module, i64 count and handle output")
        signature = ir.FunctionType(ir.IntType(32), [pointer, i64, pointer.as_pointer()])
        name = "pcc_rt_managed_alloc"
        existing = self.module.globals.get(name)
        if existing is not None and (not isinstance(existing, ir.Function)
                                     or existing.function_type != signature):
            raise ManagedContractError("incompatible shared managed allocation ABI")
        desc = self.descriptor(element)
        alloc = existing if existing is not None else ir.Function(self.module, signature, name)
        return builder.call(alloc, [builder.bitcast(desc, pointer), count, output])
