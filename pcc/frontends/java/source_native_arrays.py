"""Non-throwing primitive arrays for the native static source domain.

JLS 10.3-10.7, 14.14.2, 15.10.3 and 15.26.1, lowered onto the shared GC0 managed
envelope (pcc/runtime/py/py_shared_managed.py). Only operations which cannot
complete abruptly are admitted, as with the nonzero-constant division domain:

- a one-dimensional boolean/integral local declared once at the method's top
  level with an array initializer, never reassigned and never escaping;
- ``a.length``; reads and simple stores of a constant in-range component;
- enhanced ``for`` over such a local, which is non-null by construction.

Every other array use needs shared NullPointerException,
ArrayIndexOutOfBoundsException or NegativeArraySizeException support, class
initialization, or a managed root/ownership ABI across calls; each stays an
explicit capability boundary. Allocation failure (OutOfMemoryError) is an
explicit fatal runtime boundary with a labeled diagnostic, not an exception.

Each allocation is owned by a stable root handle in the method's frame and is
dropped on every method exit. Component storage is the envelope's payload with
the component's PCC ABI width. Only the Linux x86-64 GC0 envelope is admitted:
GC0 never moves an object, so a payload address is stable while its handle
roots it. Programs using it need the owned runtime archive and the opt-in
shared managed object at link time; the tiny runtime-free Java executable
cannot host them.
"""
from pcc.ir.compat import ir

from .diagnostics import UnsupportedJava
from .source_types import ArrayType, widening

# Component storage widths, the PCC ABI types of the native scalar lowering.
COMPONENT_BYTES = {"boolean": 1, "byte": 1, "short": 2, "char": 2, "int": 4, "long": 8}
# Bounded initializer length (IR size), far below the envelope's own limits.
MAX_INITIALIZER_COMPONENTS = 65536
PRIMITIVE_VARIABLES = frozenset(COMPONENT_BYTES) | {"float", "double"}
MANAGED_TARGET = "x86_64-unknown-linux-gnu"
# py_shared_managed descriptor layout, version 1: six i64 words.
DESCRIPTOR_VERSION = 1
ALLOCATION_FAILURE = b"pcc: Java array allocation failed (OutOfMemoryError requires shared Java exception support)\n\0"


def array_binding(binding):
    return binding is not None and isinstance(binding.dtype, ArrayType)


class NativeArrayAnalyzer:
    """Mixin for the native SourceAnalyzer; its scalar rules are unchanged.

    The nonlowering class model subclasses SourceAnalyzer and owns its own
    array semantics; ArrayModelAnalyzer disables this native domain there.
    """
    native_array_domain = True

    def reset_native_arrays(self, method):
        method.array_lengths = {}
        self.array_operands = set()

    def local_array_binding(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name] if array_binding(scope[name]) else None
        return None

    def array_operand(self, node, assigned):
        """Type a local array reference in an admitted, non-escaping position."""
        while node.kind == "group":
            self.array_operands.add(id(node))
            node = node.children[0]
        if node.kind == "name" and "." not in node.value:
            binding = self.local_array_binding(node.value)
            if binding is not None:
                self.array_operands.add(id(node))
                self.expression(node, assigned)
                return binding
        if node.kind == "name":
            node.token.error("static array fields require shared Java class initialization",
                             code="java.unsupported.static-initialization")
        node.token.error("array expressions other than a top-level initialized local require shared "
                         "NullPointerException support and managed root ownership", code="java.unsupported.array-storage")

    def array_reference_read(self, node, binding):
        if id(node) not in self.array_operands:
            node.token.error("array references escaping length, constant component access and enhanced for "
                             "require the shared managed root/ownership ABI", code="java.unsupported.array-storage")

    def native_array_local(self, node, assigned):
        dtype = node.value
        if node.inference_token is not None:
            node.token.error("inferred array locals require the next array inference domain",
                             code="java.unsupported.local-inference-domain")
        if len(self.scopes) != 2 or self.controls:
            node.token.error("array locals inside nested blocks, loops or switches require scoped root "
                             "retirement", code="java.unsupported.array-storage")
        if not node.children or node.children[0].kind != "array_initializer":
            node.token.error("array locals without an array initializer require shared "
                             "NullPointerException/NegativeArraySizeException support", code="java.unsupported.array-storage")
        initializer = node.children[0]
        if len(initializer.children) > MAX_INITIALIZER_COMPONENTS:
            initializer.token.error("array initializer exceeds the bounded component limit (65536)", code="java.unsupported.array-storage")
        node.symbol = self.declare(node.token, dtype, node.is_final)
        # JLS 15.10.3: components are evaluated left to right, each converted
        # by assignment conversion before the variable becomes assigned.
        for value in initializer.children:
            self.expression(value, assigned)
            self.assignment_value(value, dtype.component)
        initializer.dtype = dtype
        self.method.array_lengths[node.symbol] = len(initializer.children)
        assigned.add(node.symbol)
        return assigned

    def array_index(self, node, binding):
        if node.dtype not in {"byte", "short", "char", "int"}:
            node.token.error("array index or dimension must promote to int")
        if node.constant is None or not 0 <= node.constant < self.method.array_lengths[binding]:
            node.token.error("nonconstant or out-of-range array index requires shared "
                             "ArrayIndexOutOfBoundsException support", code="java.unsupported.array-index-exception")

    def native_array_expression(self, node, assigned):
        """Type admitted array expressions; False leaves scalar typing alone."""
        if node.kind == "name" and "." in node.value:
            root, suffix = node.value.split(".", 1)
            if self.local_array_binding(root) is None:
                return False
            if suffix != "length":
                node.token.error("array members other than length require the next reference domain",
                                 code="java.unsupported.array-model-shape")
            token = type(node.token)(root, node.token.filename, node.token.line, node.token.column)
            node.kind, node.value = "array_length", None
            node.children = [type(node)("name", token, root)]
        if node.kind == "array_length":
            node.symbol = self.array_operand(node.children[0], assigned)
            # JLS 15.28: an array length is never a constant expression.
            node.dtype = "int"
            return True
        if node.kind == "array_access":
            array, index = node.children
            node.symbol = self.array_operand(array, assigned)
            self.expression(index, assigned)
            self.array_index(index, node.symbol)
            node.dtype = node.symbol.dtype.component
            return True
        if node.kind == "array_initializer":
            node.token.error("array initializers are admitted only in top-level array local declarations",
                             code="java.unsupported.array-storage")
        return False

    def native_array_store(self, node, assigned):
        """`a[k] = v` for a constant in-range k: no abrupt completion."""
        target = node.children[0]
        while target.kind == "group":
            target = target.children[0]
        if node.value != "=":
            node.token.error("array component compound assignment and increment require the next "
                             "variable-access domain", code="java.unsupported.assignment-expression-target")
        array, index = target.children
        binding = self.array_operand(array, assigned)
        self.expression(index, assigned)
        self.array_index(index, binding)
        rhs = node.children[1]
        self.expression(rhs, assigned)
        component = binding.dtype.component
        self.assignment_value(rhs, component)
        target.kind, target.symbol, target.dtype = "array_place", binding, component
        node.symbol, node.dtype = binding, component
        return assigned

    def native_enhanced_for(self, node, assigned):
        variable, expression, body = node.children
        binding = self.array_operand(expression, assigned)
        component = binding.dtype.component
        if variable.inference_token is not None:
            variable.value = component
        if not isinstance(variable.value, str) or variable.value not in PRIMITIVE_VARIABLES:
            variable.token.error("enhanced for variables require a primitive component conversion",
                                 code="java.unsupported.enhanced-for-boxing")
        if not widening(component, variable.value):
            variable.token.error("incompatible enhanced for element assignment conversion from " + component
                                 + " to " + str(variable.value))
        node.symbol = binding
        self.scopes.append({})
        loop = {"node": node, "break": [], "continue": []}
        self.controls.append(loop)
        try:
            variable.symbol = self.declare(variable.token, variable.value, variable.is_final)
            self.statement(body, set(assigned) | {variable.symbol})
        finally:
            self.controls.pop()
            self.scopes.pop()
        # JLS 16.2.12: the length test may fail before the body runs.
        return set.intersection(set(assigned), *loop["break"])

    def reject_array_reassignment(self, node):
        if array_binding(node.symbol):
            node.token.error("reassigning an array local requires the shared managed root/ownership ABI",
                             code="java.unsupported.array-storage")


class NativeArrayLowering:
    """Envelope calls for the admitted array domain inside SourceLowerer."""

    def __init__(self, lowerer):
        self.lowerer = lowerer
        self.module = lowerer.module
        self.descriptors = {}
        self.functions = {}
        self.message = None
        self.used = False

    def require_target(self, token):
        if self.module.triple != MANAGED_TARGET:
            raise UnsupportedJava(str(token.filename) + ":" + str(token.line) + ":" + str(token.column)
                                  + ": Java arrays use the shared managed runtime, admitted only for "
                                  + MANAGED_TARGET + " GC0", code="java.unsupported.array-storage")

    def external(self, name, result, arguments):
        if name not in self.functions:
            self.functions[name] = ir.Function(self.module, ir.FunctionType(result, arguments), name)
        return self.functions[name]

    @staticmethod
    def storage_type(component):
        return ir.IntType(8 * COMPONENT_BYTES[component])

    def descriptor(self, component):
        if component not in self.descriptors:
            size = COMPONENT_BYTES[component]
            word = ir.IntType(64)
            storage = ir.GlobalVariable(self.module, ir.ArrayType(word, 6),
                                        "__pcc_java_array_descriptor_" + component)
            storage.linkage = "private"
            storage.global_constant = True
            # version, fixed bytes, element bytes, alignment, fixed refs, element refs
            storage.initializer = ir.Constant(storage.value_type, [
                ir.Constant(word, value) for value in (DESCRIPTOR_VERSION, 0, size, size, 0, 0)])
            self.descriptors[component] = storage
        return self.descriptors[component]

    def failure_message(self):
        if self.message is None:
            self.message = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), len(ALLOCATION_FAILURE)),
                                             "__pcc_java_array_allocation_failure")
            self.message.linkage = "private"
            self.message.global_constant = True
            self.message.initializer = ir.Constant(self.message.value_type, bytearray(ALLOCATION_FAILURE))
        return self.message

    def handle_slot(self, builder):
        slot = builder.alloca(ir.IntType(8).as_pointer(), name="java_array_handle")
        builder.store(ir.Constant(ir.IntType(8).as_pointer(), None), slot)
        return slot

    def allocate(self, node, slot, initializers):
        """JLS 10.6: create a zeroed array of n components, then evaluate the
        initializers left to right, storing each before evaluating the next."""
        self.require_target(node.token)
        self.used = True
        lowerer = self.lowerer
        builder = lowerer.builder
        pointer = ir.IntType(8).as_pointer()
        component = node.value.component
        allocate = self.external("pcc_rt_managed_alloc", ir.IntType(32),
                                 [pointer, ir.IntType(64), pointer.as_pointer()])
        fatal = self.external("pcc_debug_fatal_py", ir.VoidType(), [pointer])
        descriptor = builder.bitcast(self.descriptor(component), pointer)
        status = builder.call(allocate, [descriptor, ir.Constant(ir.IntType(64), len(initializers)), slot])
        failed = lowerer.function.append_basic_block("java_array_allocation_failed")
        ready = lowerer.function.append_basic_block("java_array_allocated")
        builder.cbranch(builder.icmp_unsigned("!=", status, ir.Constant(ir.IntType(32), 0)), failed, ready)
        builder.position_at_end(failed)
        message = self.failure_message()
        builder.call(fatal, [builder.gep(message, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])])
        builder.unreachable()
        builder.position_at_end(ready)
        for index, initializer in enumerate(initializers):
            self.store(slot, component, ir.Constant(ir.IntType(64), index), lowerer.expression(initializer))

    def data(self, slot, component):
        builder = self.lowerer.builder
        pointer = ir.IntType(8).as_pointer()
        payload = self.external("pcc_rt_managed_data", pointer, [pointer])
        address = builder.call(payload, [builder.load(slot)])
        return builder.bitcast(address, self.storage_type(component).as_pointer())

    def component_address(self, slot, component, index):
        """`index` is an i64 IR value already proven within [0, length)."""
        return self.lowerer.builder.gep(self.data(slot, component), [index])

    def load(self, slot, component, index):
        builder = self.lowerer.builder
        value = builder.load(self.component_address(slot, component, index))
        if component == "boolean":
            return builder.icmp_unsigned("!=", value, ir.Constant(value.type, 0))
        return self.lowerer.from_abi(value, component)

    def store(self, slot, component, index, value):
        builder = self.lowerer.builder
        value = self.lowerer.to_abi(value, component)
        if component == "boolean":
            value = builder.zext(value, ir.IntType(8))
        builder.store(value, self.component_address(slot, component, index))

    def drop_all(self, slots):
        """Retire every frame root before any method exit."""
        if not slots:
            return
        builder = self.lowerer.builder
        pointer = ir.IntType(8).as_pointer()
        drop = self.external("pcc_rt_managed_drop", ir.VoidType(), [pointer])
        for slot in slots:
            builder.call(drop, [builder.load(slot)])
