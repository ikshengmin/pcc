"""Java .class -> PCC public IR -> owned self backend, without javac/JVM.

Stage: host-Python compiler, closed-world static int/void methods in one class.
This is not a Java source compiler, native pcc1 frontend, or Java runtime.
Heap, classes with initialization/state, monitors, exceptions and external
classes remain explicit capability errors, not alternate execution owners.
"""
from __future__ import annotations


from pcc.ir.compat import ir

from .artifact import JavaCompilation, method_symbol
from .integer_semantics import emit_binary, emit_narrow
from .bytecode import _BINARY, _STACK, VerifiedMethod, int_signature, verify_method
from .classfile import ClassFile, JavaError, UnsupportedJava, parse_class


def _lower(cls: ClassFile, verified: VerifiedMethod, function, functions) -> None:
    method = verified.method
    code = method.code
    assert code is not None
    i32 = ir.IntType(32)
    constant = lambda value: ir.Constant(i32, value)
    entry = function.append_basic_block("entry")
    blocks = {instruction.offset: function.append_basic_block(f"bc_{instruction.offset}")
              for instruction in verified.instructions if instruction.offset in verified.frames}
    builder = ir.IRBuilder(entry)
    locals_ = [builder.alloca(i32, name=f"local_{i}") for i in range(code.max_locals)]
    stack_slots = [builder.alloca(i32, name=f"stack_{i}") for i in range(code.max_stack)]
    for arg, slot in zip(function.args, locals_):
        builder.store(arg, slot)
    builder.branch(blocks[0])
    for instruction in verified.instructions:
        if instruction.offset not in blocks:
            continue
        builder = ir.IRBuilder(blocks[instruction.offset])
        depth = len(verified.frames[instruction.offset].stack)
        # Stack values live in explicit i32 slots across every CFG edge. No
        # predecessor-dependent SSA alias or uninitialized merge is invented.
        values = [builder.load(slot) for slot in stack_slots[:depth]]
        operation, operands = instruction.operation, instruction.operands
        terminated = False
        if operation == "const":
            values.append(constant(operands[0]))
        elif operation == "ldc":
            values.append(constant(cls.constant(operands[0], 3).values[0]))
        elif operation == "load":
            values.append(builder.load(locals_[operands[0]]))
        elif operation == "store":
            builder.store(values.pop(), locals_[operands[0]])
        elif operation == "iinc":
            slot = locals_[operands[0]]
            builder.store(builder.add(builder.load(slot), constant(operands[1])), slot)
        elif operation in _BINARY.values():
            right, left = values.pop(), values.pop()
            values.append(emit_binary(builder, operation, left, right))
        elif operation == "neg":
            values.append(builder.sub(constant(0), values.pop()))
        elif operation in ("i2b", "i2c", "i2s"):
            values.append(emit_narrow(builder, operation, values.pop()))
        elif operation in _STACK:
            count, permutation = _STACK[operation]
            suffix = values[-count:]
            del values[-count:]
            values.extend(suffix[i] for i in permutation)
        elif operation == "call":
            owner, name, desc = cls.method_ref(operands[0])
            args, result = int_signature(desc)
            call_args = values[-len(args):] if args else []
            if args:
                del values[-len(args):]
            value = builder.call(functions[name, desc], call_args)
            if result == "I":
                values.append(value)
        elif operation.startswith("if"):
            right = values.pop() if operation.startswith("if2_") else constant(0)
            left = values.pop()
            predicate = {"eq": "==", "ne": "!=", "lt": "<", "ge": ">=", "gt": ">", "le": "<="}[operation.split("_")[1]]
            condition = builder.icmp_signed(predicate, left, right)
            for value, slot in zip(values, stack_slots):
                builder.store(value, slot)
            builder.cbranch(condition, blocks[instruction.targets[0]], blocks[instruction.next_offset])
            terminated = True
        elif operation == "switch":
            value = values.pop()
            for pending, slot in zip(values, stack_slots):
                builder.store(pending, slot)
            switch = builder.switch(value, blocks[instruction.targets[0]])
            for key, target in zip(operands, instruction.targets[1:]):
                switch.add_case(constant(key), blocks[target])
            terminated = True
        elif operation == "ireturn":
            builder.ret(values.pop())
            terminated = True
        elif operation == "return":
            builder.ret_void()
            terminated = True
        if not terminated:
            for value, slot in zip(values, stack_slots):
                builder.store(value, slot)
            target = instruction.targets[0] if operation == "goto" else instruction.next_offset
            builder.branch(blocks[target])


def compile_class(data: bytes, *, entry: str = "run", descriptor: str = "()I",
                  target: str = "x86_64-unknown-linux-gnu") -> JavaCompilation:
    """Compile the selected static method and same-class static call closure.

    Unsupported unselected methods (e.g. javac's constructor) are not emitted.
    All selected methods are decoded and verified before any IR is produced.
    There is no dynamic loading, foreign Java classpath, or runtime fallback.
    """
    cls = parse_class(data)
    if cls.flags & ~0x1031 or cls.interfaces or cls.super_name != "java/lang/Object":
        raise UnsupportedJava("only ordinary classes directly extending java/lang/Object are supported")
    if cls.field_count or any(method.name == "<clinit>" for method in cls.methods):
        raise UnsupportedJava("Java fields/class initialization require the shared Java runtime")
    methods = {(method.name, method.descriptor): method for method in cls.methods}
    root = (entry, descriptor)
    if root not in methods:
        raise JavaError(f"entry method not found: {entry}{descriptor}")
    if entry.startswith("<"):
        raise UnsupportedJava("special methods cannot be AOT entrypoints")
    queue = [root]
    verified = {}
    while queue:
        key = queue.pop(0)
        if key in verified:
            continue
        result = verify_method(cls, methods[key])
        verified[key] = result
        queue.extend(call for call in result.calls if call not in verified)
    # Class-file names are not Java source identifiers: quotes/newlines can
    # occur. The text-first builder places ModuleID in a comment without
    # escaping, so encode this label just like native symbols.
    module = ir.Module(name="java_" + cls.name.encode("utf-8", "surrogatepass").hex())
    module.triple = target
    functions = {}
    for key in verified:
        args, result = int_signature(key[1])
        signature = ir.FunctionType(ir.IntType(32) if result == "I" else ir.VoidType(),
                                    [ir.IntType(32) for _ in args])
        functions[key] = ir.Function(module, signature, name=method_symbol(cls.name, *key))
    for key, method in verified.items():
        _lower(cls, method, functions[key], functions)
    return JavaCompilation(module, cls.name, root, tuple(verified))
