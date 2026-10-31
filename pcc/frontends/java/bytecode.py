"""Fail-closed int-stack decoder and worklist verifier for selected methods.

No JVM interpreter is involved. Every admitted instruction has a lowering;
unsupported instructions fail even in an unreachable part of a selected method.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .classfile import ClassFile, JavaError, Method, Reader, UnsupportedJava, descriptor


@dataclass(frozen=True)
class Instruction:
    offset: int
    operation: str
    operands: tuple[int, ...]
    next_offset: int
    targets: tuple[int, ...] = ()


@dataclass(frozen=True)
class Frame:
    locals: tuple[str | None, ...]
    stack: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedMethod:
    method: Method
    instructions: tuple[Instruction, ...]
    frames: dict[int, Frame]
    calls: tuple[tuple[str, str], ...]


_BINARY = {0x60: "add", 0x64: "sub", 0x68: "mul", 0x78: "shl", 0x7A: "ashr",
           0x7C: "lshr", 0x7E: "and", 0x80: "or", 0x82: "xor"}
_CONDITIONS = ("eq", "ne", "lt", "ge", "gt", "le")
_UNSUPPORTED = {0x6C: "idiv (ArithmeticException runtime)", 0x70: "irem (ArithmeticException runtime)",
                0xBA: "invokedynamic", 0xBB: "new/heap allocation", 0xBC: "newarray/heap allocation",
                0xBD: "anewarray/heap allocation", 0xC2: "monitorenter", 0xC3: "monitorexit",
                0xBF: "athrow/exception unwind", 0xB2: "getstatic", 0xB3: "putstatic",
                0xB4: "getfield", 0xB5: "putfield", 0xB6: "invokevirtual", 0xB7: "invokespecial",
                0xB9: "invokeinterface", 0xA8: "jsr", 0xA9: "ret", 0xC9: "jsr_w"}


def decode(code: bytes) -> tuple[Instruction, ...]:
    reader = Reader(code, "bytecode")
    result = []
    while reader.pos < len(code):
        offset = reader.pos
        opcode = reader.u1()
        args = ()
        targets = ()
        if opcode == 0:
            operation = "nop"
        elif 2 <= opcode <= 8:
            operation, args = "const", (opcode - 3,)
        elif opcode in (0x10, 0x11):
            operation, args = "const", (int.from_bytes(reader.take(1 if opcode == 0x10 else 2), "big", signed=True),)
        elif opcode in (0x12, 0x13):
            operation, args = "ldc", (reader.uint(1 if opcode == 0x12 else 2),)
        elif opcode in (0x15, 0x36):
            operation, args = ("load" if opcode == 0x15 else "store"), (reader.u1(),)
        elif 0x1A <= opcode <= 0x1D:
            operation, args = "load", (opcode - 0x1A,)
        elif 0x3B <= opcode <= 0x3E:
            operation, args = "store", (opcode - 0x3B,)
        elif opcode == 0x84:
            operation, args = "iinc", (reader.u1(), int.from_bytes(reader.take(1), "big", signed=True))
        elif opcode == 0xC4:
            wide = reader.u1()
            if wide not in (0x15, 0x36, 0x84):
                raise UnsupportedJava(f"unsupported wide opcode 0x{wide:02x} at byte {offset}")
            operation = {0x15: "load", 0x36: "store", 0x84: "iinc"}[wide]
            args = (reader.u2(),)
            if wide == 0x84:
                args += (int.from_bytes(reader.take(2), "big", signed=True),)
        elif opcode in _BINARY:
            operation = _BINARY[opcode]
        elif opcode == 0x74:
            operation = "neg"
        elif opcode in (0x91, 0x92, 0x93):
            operation = {0x91: "i2b", 0x92: "i2c", 0x93: "i2s"}[opcode]
        elif 0x57 <= opcode <= 0x5F:
            operation = ("pop", "pop2", "dup", "dup_x1", "dup_x2", "dup2", "dup2_x1", "dup2_x2", "swap")[opcode - 0x57]
        elif 0x99 <= opcode <= 0xA4:
            operation = ("if_" if opcode <= 0x9E else "if2_") + _CONDITIONS[(opcode - 0x99) % 6]
            targets = (offset + int.from_bytes(reader.take(2), "big", signed=True),)
        elif opcode in (0xA7, 0xC8):
            operation = "goto"
            targets = (offset + int.from_bytes(reader.take(2 if opcode == 0xA7 else 4), "big", signed=True),)
        elif opcode in (0xAA, 0xAB):
            operation = "switch"
            while reader.pos % 4:
                if reader.u1() != 0:
                    raise JavaError(f"nonzero switch padding at byte {offset}")
            default = offset + int.from_bytes(reader.take(4), "big", signed=True)
            first = int.from_bytes(reader.take(4), "big", signed=True)
            if opcode == 0xAA:
                last = int.from_bytes(reader.take(4), "big", signed=True)
                count = last - first + 1
                if count <= 0 or count > (len(code) - reader.pos) // 4:
                    raise JavaError(f"invalid tableswitch range at byte {offset}")
                args = tuple(range(first, last + 1))
                targets = (default,) + tuple(offset + int.from_bytes(reader.take(4), "big", signed=True) for _ in args)
            else:
                count = first
                if count < 0 or count > (len(code) - reader.pos) // 8:
                    raise JavaError(f"invalid lookupswitch count at byte {offset}")
                pairs = tuple((int.from_bytes(reader.take(4), "big", signed=True),
                               offset + int.from_bytes(reader.take(4), "big", signed=True)) for _ in range(count))
                args = tuple(pair[0] for pair in pairs)
                targets = (default,) + tuple(pair[1] for pair in pairs)
                if any(a >= b for a, b in zip(args, args[1:])):
                    raise JavaError(f"lookupswitch keys must increase at byte {offset}")
        elif opcode == 0xB8:
            operation, args = "call", (reader.u2(),)
        elif opcode in (0xAC, 0xB1):
            operation = "ireturn" if opcode == 0xAC else "return"
        else:
            feature = _UNSUPPORTED.get(opcode, f"opcode 0x{opcode:02x}")
            raise UnsupportedJava(f"unsupported Java bytecode {feature} at byte {offset}")
        result.append(Instruction(offset, operation, args, reader.pos, targets))
    boundaries = {i.offset for i in result}
    for instruction in result:
        if any(target not in boundaries for target in instruction.targets):
            raise JavaError(f"branch target is not an instruction boundary at byte {instruction.offset}")
    return tuple(result)


# All admitted values have JVM category 1. These permutations are expressed
# oldest-to-newest over the affected suffix, not target-machine registers.
_STACK = {"pop": (1, ()), "pop2": (2, ()), "dup": (1, (0, 0)),
          "dup_x1": (2, (1, 0, 1)), "dup_x2": (3, (2, 0, 1, 2)),
          "dup2": (2, (0, 1, 0, 1)), "dup2_x1": (3, (1, 2, 0, 1, 2)),
          "dup2_x2": (4, (2, 3, 0, 1, 2, 3)), "swap": (2, (1, 0))}


def int_signature(method_descriptor: str) -> tuple[tuple[str, ...], str]:
    args, result = descriptor(method_descriptor)
    if any(a != "I" for a in args) or result not in ("I", "V"):
        raise UnsupportedJava(f"only int/void static method descriptors are supported: {method_descriptor}")
    return args, result


def _stackmaps(cls: ClassFile, method: Method, boundaries: set[int]) -> dict[int, Frame]:
    code = method.code
    assert code is not None
    attrs = [a for a in code.attributes if a.name == "StackMapTable"]
    if not attrs:
        return {}
    reader = Reader(attrs[0].data, "StackMapTable")
    locals_ = ["I"] * len(int_signature(method.descriptor)[0])
    result = {}
    offset = -1

    def verification_type():
        tag = reader.u1()
        if tag == 0:
            return None
        if tag == 1:
            return "I"
        if tag == 7:
            cls.constant(reader.u2(), 7)
        elif tag == 8:
            reader.u2()
        elif tag not in (2, 3, 4, 5, 6):
            raise JavaError("invalid stack-map verification type")
        raise UnsupportedJava("non-int StackMapTable verification type is unsupported")

    for _ in range(reader.u2()):
        kind = reader.u1()
        stack = []
        if kind <= 63:
            delta = kind
        elif kind <= 127:
            delta = kind - 64
            stack = [verification_type()]
        elif kind == 247:
            delta = reader.u2()
            stack = [verification_type()]
        elif 248 <= kind <= 250:
            delta = reader.u2()
            count = 251 - kind
            if len(locals_) < count:
                raise JavaError("stack-map chop underflow")
            locals_ = locals_[:-count]
        elif kind == 251:
            delta = reader.u2()
        elif 252 <= kind <= 254:
            delta = reader.u2()
            locals_ += [verification_type() for _ in range(kind - 251)]
        elif kind == 255:
            delta = reader.u2()
            locals_ = [verification_type() for _ in range(reader.u2())]
            stack = [verification_type() for _ in range(reader.u2())]
        else:
            raise JavaError("reserved stack-map frame type")
        offset += delta + 1
        if offset not in boundaries or len(locals_) > code.max_locals or len(stack) > code.max_stack:
            raise JavaError("invalid stack-map offset or frame size")
        if None in stack:
            raise JavaError("TOP on int operand stack")
        result[offset] = Frame(tuple(locals_) + (None,) * (code.max_locals - len(locals_)), tuple(stack))
    reader.end()
    return result


def verify_method(cls: ClassFile, method: Method) -> VerifiedMethod:
    if not method.flags & 8:
        raise UnsupportedJava(f"instance method {method.name} is unsupported")
    if method.flags & (0x0020 | 0x0100 | 0x0400):
        raise UnsupportedJava(f"synchronized/native/abstract method {method.name} is unsupported")
    args, return_type = int_signature(method.descriptor)
    code = method.code
    if code is None:
        raise JavaError("selected method has no Code")
    if code.handlers:
        raise UnsupportedJava("Java exception handlers require the shared exception/unwind runtime")
    if code.max_locals < len(args):
        raise JavaError("max_locals is smaller than the parameter count")
    instructions = decode(code.bytecode)
    if len(instructions) * (code.max_locals + code.max_stack + 1) > 1_000_000:
        raise UnsupportedJava("method exceeds the bounded AOT verifier frame budget")
    by_offset = {i.offset: i for i in instructions}
    declared = _stackmaps(cls, method, set(by_offset))
    calls = []
    signatures = {}
    methods = {(m.name, m.descriptor): m for m in cls.methods}
    for instruction in instructions:
        op = instruction.operation
        if op in ("load", "store", "iinc") and instruction.operands[0] >= code.max_locals:
            raise JavaError(f"local index out of bounds at byte {instruction.offset}")
        if op == "ldc":
            entry = cls.constant(instruction.operands[0])
            if entry.tag != 3:
                raise UnsupportedJava("ldc supports only CONSTANT_Integer, not objects/floating point/dynamic constants")
        if op == "call":
            owner, name, desc = cls.method_ref(instruction.operands[0])
            if owner != cls.name:
                raise UnsupportedJava(f"closed-world class dependency {owner}.{name}{desc} is not available")
            if (name, desc) not in methods:
                raise JavaError(f"unresolved static method {name}{desc}")
            target = methods[name, desc]
            if not target.flags & 8 or name.startswith("<"):
                raise JavaError("invokestatic target is not an ordinary static method")
            signatures[instruction.offset] = int_signature(desc)
            calls.append((name, desc))
        if cls.major >= 51:
            if any(target != 0 and target not in declared for target in instruction.targets):
                raise JavaError("missing StackMapTable frame at branch target")
            # JVMS 4.10.1.6: a return/unconditional transfer yields afterGoto.
            # Any subsequent instruction starts a block with an explicit frame,
            # even if no ordinary CFG edge reaches that block.
            if (op in ("goto", "switch", "ireturn", "return")
                    and instruction.next_offset < len(code.bytecode)
                    and instruction.next_offset not in declared):
                raise JavaError("missing StackMapTable frame after unconditional control transfer")
    initial = Frame(("I",) * len(args) + (None,) * (code.max_locals - len(args)), ())
    frames = {}
    queue = deque()

    def merge(offset: int, incoming: Frame):
        if offset not in by_offset:
            raise JavaError("reachable bytecode falls off the end of Code")
        if offset in declared:
            required = declared[offset]
            if incoming.stack != required.stack or any(want == "I" and have != "I" for have, want in zip(incoming.locals, required.locals)):
                raise JavaError(f"incompatible StackMapTable frame at byte {offset}")
            incoming = required
        old = frames.get(offset)
        if old is not None:
            if old.stack != incoming.stack:
                raise JavaError(f"incompatible operand stacks at byte {offset}")
            incoming = Frame(tuple(a if a == b else None for a, b in zip(old.locals, incoming.locals)), old.stack)
        if old != incoming:
            frames[offset] = incoming
            queue.append(offset)

    merge(0, initial)
    # First exhaust actual entry edges so incompatible declared frames retain
    # the primary merge diagnostic. Then verify each disconnected declared
    # block; a later predecessor still has to match that block's frame.
    pending_frames = deque(declared)
    while queue or pending_frames:
        if not queue:
            while pending_frames:
                root = pending_frames.popleft()
                if root not in frames:
                    merge(root, declared[root])
                    break
            if not queue:
                break
        offset = queue.popleft()
        instruction = by_offset[offset]
        op, operands = instruction.operation, instruction.operands
        locals_, stack = list(frames[offset].locals), list(frames[offset].stack)

        def pop(count: int):
            if len(stack) < count:
                raise JavaError(f"operand-stack underflow at byte {offset}")
            if count:
                del stack[-count:]

        if op in ("const", "ldc"):
            stack.append("I")
        elif op == "load":
            if locals_[operands[0]] != "I":
                raise JavaError(f"uninitialized int local at byte {offset}")
            stack.append("I")
        elif op == "store":
            pop(1)
            locals_[operands[0]] = "I"
        elif op == "iinc":
            if locals_[operands[0]] != "I":
                raise JavaError(f"uninitialized int local at byte {offset}")
        elif op in _BINARY.values():
            pop(2)
            stack.append("I")
        elif op in ("neg", "i2b", "i2c", "i2s"):
            pop(1)
            stack.append("I")
        elif op in _STACK:
            count, permutation = _STACK[op]
            pop(count)
            stack.extend("I" for _ in permutation)
        elif op.startswith("if"):
            pop(2 if op.startswith("if2_") else 1)
        elif op == "switch":
            pop(1)
        elif op == "call":
            params, result = signatures[offset]
            pop(len(params))
            if result == "I":
                stack.append("I")
        elif op == "ireturn":
            if return_type != "I":
                raise JavaError("ireturn in a void method")
            pop(1)
        elif op == "return" and return_type != "V":
            raise JavaError("void return in an int method")
        if len(stack) > code.max_stack:
            raise JavaError(f"operand stack exceeds max_stack at byte {offset}")
        outgoing = Frame(tuple(locals_), tuple(stack))
        for target in instruction.targets:
            merge(target, outgoing)
        if op not in ("goto", "switch", "ireturn", "return"):
            merge(instruction.next_offset, outgoing)
    return VerifiedMethod(method, instructions, frames, tuple(dict.fromkeys(calls)))
