"""Restricted native class-variable initialization, not constant-array interning.

JLS 8.3.2, 8.3.3, 12.4.1 and 12.4.2. Only the supplied ordinary-static
class domain participates. Runtime fields are initialized in textual order,
never at link time. The bounded first lane has no Java callbacks during class
initialization. It admits constant primitive array initializers, immutable
aliases to prior initialized arrays, and pure reads of prior own class fields.
It does not infer a superclass initializer, a null length, or an exception ABI.
"""
from dataclasses import dataclass

from pcc.ir.compat import ir

from .integer_semantics import wrap_integer
from .source_types import ArrayType


@dataclass(eq=False)
class StaticBinding:
    field: object
    mode: str
    length: int | None = None

    @property
    def dtype(self):
        return self.field.dtype

    @property
    def name(self):
        return self.field.token.text


def gap(token, reason):
    token.error(reason, code="java.unsupported.static-initialization")


def ungroup(node):
    while node.kind == "group":
        node = node.children[0]
    return node


def array_length(method, binding):
    return binding.length if isinstance(binding, StaticBinding) else method.array_lengths[binding]


def proven_index(node, method):
    """A bounds proof, never a Java constant variable or an evaluation skip."""
    node = ungroup(node)
    if node.constant is not None and node.dtype in {"int", "byte", "short", "char"}:
        return int(node.constant)
    if node.kind == "array_length":
        return array_length(method, node.symbol)
    if node.kind == "binary" and node.dtype == "int" and node.value in {"+", "-"}:
        a = proven_index(node.children[0], method)
        b = proven_index(node.children[1], method)
        if a is not None and b is not None:
            return wrap_integer(a + b if node.value == "+" else a - b, 32)
    return None


def before_runtime_field(analyzer, field, node):
    current = analyzer.current_field
    if current is not None and (current.owner != field.owner or field.ordinal >= current.ordinal):
        # Qualified forward references can legally observe default values,
        # and other class initializers can execute user code. Neither proves
        # that this field already contains its final, non-null binding.
        gap(node.token, "runtime field initializer requires a previously initialized field of the same class")


def read_only_initializer(node):
    """Closed tree: evaluating the expression can't call back into a class or store."""
    node = ungroup(node)
    if node.constant is not None:
        return True
    if node.kind == "name":
        return isinstance(node.symbol, StaticBinding)
    if node.kind in {"array_access", "array_length"}:
        return isinstance(node.symbol, StaticBinding) and all(read_only_initializer(n) for n in node.children)
    if node.kind == "binary" and node.value in {"+", "-", "*", "&", "|", "^", "<<", ">>", ">>>"}:
        return all(read_only_initializer(n) for n in node.children)
    return False


def analyze_array_field(analyzer, field, maximum):
    term = ungroup(field.initializer)
    dtype = field.dtype
    if term.kind not in {"array_initializer", "array_new"}:
        analyzer.expression(field.initializer, set())
        analyzer.assignment_value(field.initializer, dtype)
        if term.kind != "name" or not isinstance(term.symbol, StaticBinding) or term.symbol.length is None:
            gap(term.token, "static array alias requires an earlier, non-null initialized array field")
        field.native_storage = StaticBinding(field, "alias", term.symbol.length)
        return
    if term.value != dtype:
        term.token.error("incompatible primitive array component type")
    if term.kind == "array_new":
        dim, = term.children
        analyzer.expression(dim, set())
        if dim.dtype not in {"byte", "short", "char", "int"}:
            dim.token.error("array dimension must promote to int")
        length = proven_index(dim, None)
        if length is None or not read_only_initializer(dim) or not 0 <= length <= maximum:
            gap(dim.token, "static array dimension requires a bounded proven nonnegative length")
    else:
        length = len(term.children)
        if length > maximum:
            gap(term.token, "static array initializer exceeds the bounded component count")
        for component in term.children:
            analyzer.expression(component, set())
            analyzer.assignment_value(component, dtype.component)
            if not read_only_initializer(component):
                gap(component.token, "static array initializer with user effects requires managed exception/initialization propagation")
    term.dtype = dtype
    field.native_storage = StaticBinding(field, "array", length)


def start_node(analyzer, original):
    """Recognize field.length only after name resolution establishes an array.

    Do not steal Class.length or a qualifier shadowed by a scalar local.
    The regular expression resolver still enforces access on the selected field.
    """
    prefix, dot, suffix = original.value.rpartition(".")
    if not dot or suffix != "length" or not analyzer.project:
        return None
    parts = prefix.split(".")
    local = next((scope[parts[0]] for scope in reversed(analyzer.scopes) if parts[0] in scope), None)
    if local is not None:
        if len(parts) != 1 or not isinstance(local.dtype, ArrayType):
            return None
    elif len(parts) == 1:
        field = analyzer.project.simple_field(analyzer, prefix, original.token)
        if field is None or not isinstance(field.dtype, ArrayType):
            return None
    elif len(parts) == 2:
        cls = analyzer.project.visible_simple_type(analyzer.program, parts[0], original.token)
        if cls is None or cls.name not in analyzer.project.analyzers:
            return None
        field = analyzer.project.analyzers[cls.name].fields.get(parts[1])
        if field is None or not isinstance(field.dtype, ArrayType):
            return None
    else:
        return None
    token = type(original.token)(prefix, original.token.filename, original.token.line, original.token.column)
    return type(original)("name", token, prefix)


class NativeStatics:
    def __init__(self, lower):
        self.lower = lower
        self.slots = {}
        self.guards = {}
        self.states = {}
        self.initializing = None
        self.failure_text = None

    def prepare(self):
        lower = self.lower
        for program in lower.programs:
            fields = [f for f in program.fields if f.native_storage is not None]
            if not fields:
                continue
            lower.arrays.require_target(fields[0].token)
            lower.arrays.used = True
            owner = program.name
            name = "__pcc_java_static_" + owner.encode().hex()
            state = ir.GlobalVariable(lower.module, ir.IntType(64), name + "_state")
            state.linkage = "private"
            state.initializer = ir.Constant(state.value_type, 0)
            state.align = 8
            fn = ir.Function(lower.module, ir.FunctionType(ir.VoidType(), []), name + "_initialize")
            fn.linkage = "internal"
            self.guards[owner], self.states[owner] = fn, state
            for f in fields:
                dtype = lower.references.pointer if isinstance(f.dtype, ArrayType) else lower.scalar_type(f.dtype)
                slot = ir.GlobalVariable(lower.module, dtype, name + "_field_" + f.token.text.encode().hex())
                slot.linkage = "private"
                slot.initializer = ir.Constant(dtype, None)
                self.slots[f.native_storage] = slot
        for program in lower.programs:
            if program.name in self.guards:
                self.emit_initializer(program)

    def external(self, name, result, args):
        return self.lower.arrays.external(name, result, args)

    def fatal(self):
        lower = self.lower
        pointer = lower.references.pointer
        if self.failure_text is None:
            data = b"pcc: Java class initialization failed (shared Java exception delivery unavailable)\n\0"
            self.failure_text = ir.GlobalVariable(lower.module, ir.ArrayType(ir.IntType(8), len(data)), "__pcc_java_static_failure")
            self.failure_text.linkage = "private"
            self.failure_text.global_constant = True
            self.failure_text.initializer = ir.Constant(self.failure_text.value_type, bytearray(data))
        zero = ir.Constant(lower.i32, 0)
        fatal = self.external("pcc_debug_fatal_py", ir.VoidType(), [pointer])
        lower.builder.call(fatal, [lower.builder.gep(self.failure_text, [zero, zero])])
        lower.builder.unreachable()

    def publish(self, ok):
        lower = self.lower
        fn = self.external("pcc_rt_java_class_publish", lower.i32, [lower.references.pointer, lower.i32])
        state = lower.builder.bitcast(self.states[self.initializing], lower.references.pointer)
        return lower.builder.call(fn, [state, ir.Constant(lower.i32, int(ok))])

    def emit_initializer(self, program):
        lower = self.lower
        owner = self.initializing = program.name
        fn = lower.function = self.guards[owner]
        lower.method = None
        lower.slots = {}
        lower.array_slots = []
        lower.builder = b = ir.IRBuilder(fn.append_basic_block("entry"))
        lower.references.begin_frame()
        acquire = self.external("pcc_rt_java_class_enter", lower.i32, [lower.references.pointer])
        state = b.bitcast(self.states[owner], lower.references.pointer)
        status = b.call(acquire, [state])
        todo = fn.append_basic_block("initialize")
        notnew = fn.append_basic_block("already_or_error")
        done = fn.append_basic_block("done")
        failed = fn.append_basic_block("failed")
        b.cbranch(b.icmp_signed("==", status, ir.Constant(lower.i32, 1)), todo, notnew)
        b.position_at_end(notnew)
        b.cbranch(b.icmp_signed("==", status, ir.Constant(lower.i32, 0)), done, failed)
        b.position_at_end(todo)
        for f in program.fields:
            binding = f.native_storage
            if binding is None:
                continue
            slot = self.slots[binding]
            if binding.mode == "array":
                # JLS 10.6/8.3.2: root the fresh allocation while filling it,
                # publish to the Java field only after the initializer ends.
                temporary = lower.arrays.handle_slot(b)
                lower.arrays.allocate_initializer(f.token, f.dtype, ungroup(f.initializer), temporary)
                b.store(b.load(temporary), slot)
                b.store(ir.Constant(lower.references.pointer, None), temporary)
            elif binding.mode == "alias":
                b.store(lower.references.own(f.initializer), slot)
            else:
                b.store(lower.coerce(lower.expression(f.initializer), f.dtype), slot)
        completed = self.publish(True)
        b.cbranch(b.icmp_signed("==", completed, ir.Constant(lower.i32, 0)), done, failed)
        b.position_at_end(failed)
        self.fatal()
        b.position_at_end(done)
        b.ret_void()
        self.initializing = None

    def trigger(self, owner):
        if owner in self.guards and owner != self.initializing:
            self.lower.builder.call(self.guards[owner], [])

    def slot(self, binding):
        if not isinstance(binding, StaticBinding):
            return self.lower.slots[binding]
        self.trigger(binding.field.owner)
        return self.slots[binding]
