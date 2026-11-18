"""PCC Java static-call ABI for references on the serialized Linux GC0 carrier.

A reference is an opaque managed handle, not a payload or an identity address.
A call borrows its reference arguments until it returns. A reference result is
one owning handle (possibly null); the recipient must adopt or drop it. A
return of a local/parameter clones that root *before* retiring frame locals.
A call returned directly transfers its result without another clone. No
source constructor, class initializer, virtual dispatch or erasure is inferred.
"""
from pcc.ir.compat import ir

from .source_types import ArrayType, ClassType, NamedType


def reference(dtype):
    return isinstance(dtype, (ArrayType, ClassType)) or dtype == "null"


def resolve_named(project, program, dtype):
    if not isinstance(dtype, NamedType):
        return dtype
    token = dtype.tokens[0]
    if len(dtype.tokens) != 1:
        token.error("qualified reference declarations require complete implicit-type metadata; "
                    "use an accessible supplied class and a single-type import",
                    code="java.unsupported.implicit-type-metadata")
    target = project.visible_simple_type(program, token.text, token)
    if target is None:
        token.error("reference declaration requires supplied source for " + token.text,
                    code="java.unsupported.source-dependency")
    project.require_class_access(program, target, token)
    # Native source headers reject any generic declaration before this project
    # exists. Every resolved supplied class here is thus provably nongeneric;
    # type arguments on it are a JLS 4.5 source error, not an unimplemented ABI.
    if dtype.arguments or dtype.diamond:
        token.error("non-generic supplied class " + target.name + " cannot take type arguments")
    return ClassType(target.name)


class NativeReferenceLowering:
    def __init__(self, lowerer):
        self.lowerer = lowerer
        self.pointer = ir.IntType(8).as_pointer()
        self.message = None

    def select(self, token):
        self.lowerer.arrays.require_target(token)
        self.lowerer.arrays.used = True

    def external(self, name, result, params):
        return self.lowerer.arrays.external(name, result, params)

    def begin_frame(self):
        self.copy_output = None

    def borrowed_copy(self, value):
        """Use one entry alloca per method, never allocate stack slots in a loop.
        Clone returns a NEW owner or null; failed clone never supplies a value.
        The scratch cell is only an out parameter, never a second owning root.
        """
        lower = self.lowerer
        builder = lower.builder
        if self.copy_output is None:
            entry = ir.IRBuilder(lower.function.entry_basic_block)
            entry.position_at_start(lower.function.entry_basic_block)
            self.copy_output = entry.alloca(self.pointer, name="java_root_copy_out")
            # The new instruction was inserted by another builder. Restore the
            # active builder's insertion point before emitting this expression.
            builder.position_at_end(builder.block)
        clone = self.external("pcc_rt_managed_clone", lower.i32,
                              [self.pointer, self.pointer.as_pointer()])
        result = builder.call(clone, [value, self.copy_output])
        ok = lower.function.append_basic_block("java_root_cloned")
        bad = lower.function.append_basic_block("java_root_clone_failed")
        builder.cbranch(builder.icmp_unsigned("==", result, ir.Constant(lower.i32, 0)), ok, bad)
        builder.position_at_end(bad)
        if lower.statics.initializing is not None:
            lower.statics.publish(False)
        if self.message is None:
            text = b"pcc: Java reference root acquisition failed (shared Java exceptions unavailable)\n\0"
            self.message = ir.GlobalVariable(lower.module, ir.ArrayType(ir.IntType(8), len(text)),
                                             "__pcc_java_root_acquisition_failure")
            self.message.linkage = "private"
            self.message.global_constant = True
            self.message.initializer = ir.Constant(self.message.value_type, bytearray(text))
        fatal = self.external("pcc_debug_fatal_py", ir.VoidType(), [self.pointer])
        zero = ir.Constant(lower.i32, 0)
        builder.call(fatal, [builder.gep(self.message, [zero, zero])])
        builder.unreachable()
        builder.position_at_end(ok)
        return builder.load(self.copy_output)

    def drop(self, value):
        drop = self.external("pcc_rt_managed_drop", ir.VoidType(), [self.pointer])
        self.lowerer.builder.call(drop, [value])

    def call(self, node):
        """Left-to-right evaluation, preserving each owning temporary through
        invocation. The callee cannot transfer any of these borrowed roots."""
        lower = self.lowerer
        arguments, temporaries = [], []
        for expression, dtype in zip(node.children, node.symbol.parameter_types):
            if reference(dtype):
                value, owner = self.value(expression)
                if owner:
                    temporaries.append(value)
            else:
                value = lower.to_abi(lower.expression(expression), dtype)
            arguments.append(value)
        key = (node.symbol.owner, node.symbol.token.text, node.symbol.descriptor)
        result = lower.builder.call(lower.functions[key], arguments)
        for value in reversed(temporaries):
            self.drop(value)
        return result

    def value(self, node):
        """Return (handle, owns) for one evaluated expression."""
        self.select(node.token)
        lower = self.lowerer
        while node.kind == "group":
            node = node.children[0]
        if node.kind == "null":
            return ir.Constant(self.pointer, None), False
        if node.kind == "name":
            return lower.builder.load(lower.statics.slot(node.symbol)), False
        if node.kind == "call":
            return self.call(node), True
        if node.kind == "conditional":
            condition, yes, no = node.children
            builder = lower.builder
            value = lower.expression(condition)
            yes_block = lower.function.append_basic_block("java_ref_yes")
            no_block = lower.function.append_basic_block("java_ref_no")
            done = lower.function.append_basic_block("java_ref_join")
            builder.cbranch(value, yes_block, no_block)
            incoming = []
            for block, arm in ((yes_block, yes), (no_block, no)):
                builder.position_at_end(block)
                incoming.append((self.own(arm), builder.block))
                builder.branch(done)
            builder.position_at_end(done)
            result = builder.phi(self.pointer)
            for incoming_value, block in incoming:
                result.add_incoming(incoming_value, block)
            return result, True
        raise AssertionError("unsupported managed reference expression " + node.kind)

    def own(self, node):
        value, owns = self.value(node)
        return value if owns or node.dtype == "null" else self.borrowed_copy(value)

    def identity(self, node):
        lower = self.lowerer
        left, own_left = self.value(node.children[0])
        right, own_right = self.value(node.children[1])
        compare = self.external("pcc_rt_managed_identity_equal", lower.i32, [self.pointer, self.pointer])
        same = lower.builder.call(compare, [left, right])
        if own_right:
            self.drop(right)
        if own_left:
            self.drop(left)
        return lower.builder.icmp_unsigned("!=" if node.value == "==" else "==", same,
                                           ir.Constant(lower.i32, 0))
