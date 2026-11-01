"""Typed bootstrap output and terminal uncaught panic for the closed Go subset.

This is frontend lowering to existing public IR/syscall machinery, not a Go
allocator or runtime. No defer, recover, goroutine, interface-payload or foreign
callback route is admitted. Full Go panicking/unwind remains unimplemented.
"""
from pcc.ir.compat import ir
from pcc.frontends.go.diagnostics import UnsupportedGo
from pcc.frontends.go.types import Results, integer_signed, is_string, result_types
from pcc.frontends.go.records import underlying


I8, I64 = ir.IntType(8), ir.IntType(64)
SIGPIPE = 13
SIGPIPE_MASK = 1 << (SIGPIPE - 1)
KERNEL_SIGNAL_MASK_BYTES = 8
SIG_BLOCK, SIG_SETMASK = 0, 2


def number(n):
    return ir.Constant(I64, n)


class BuiltinIO:
    def __init__(self, owner):
        self.owner = owner
        self.write_function = None
        self.integer_function = None
        machine = owner.module.triple.split('-', 1)[0]
        self.write_number = 64 if machine in ('aarch64', 'arm64') else 1
        self.exit_number = 94 if machine in ('aarch64', 'arm64') else 231
        self.mask_number, self.pending_number, self.wait_number = (
            (135, 136, 137) if machine in ('aarch64', 'arm64') else (14, 127, 128))

    def syscall(self, builder, operation, *arguments):
        args = [*arguments, *[number(0) for _ in range(6-len(arguments))]]
        return builder.syscall6(number(operation), *args)

    def write_helper(self):
        if self.write_function is not None:
            return self.write_function
        f = ir.Function(self.owner.module, ir.FunctionType(ir.VoidType(), [I8.as_pointer(), I64]),
                        '__pcc_go_builtin_write_all')
        f.linkage = 'internal'
        self.write_function = f
        blocks = {name: f.append_basic_block(name) for name in (
            'entry', 'block_signal', 'pending', 'condition', 'write', 'advance',
            'retry', 'epipe', 'drain', 'drain_result', 'restore', 'done', 'signal_failure')}
        b = ir.IRBuilder(blocks['entry'])
        pointer, remaining = b.alloca(I8.as_pointer()), b.alloca(I64)
        signal_set, old_mask, pending = b.alloca(I64), b.alloca(I64), b.alloca(I64)
        timeout = b.alloca(ir.ArrayType(I64, 2))
        b.store(f.args[0], pointer)
        b.store(f.args[1], remaining)
        b.store(number(SIGPIPE_MASK), signal_set)
        for index in (0, 1):
            b.store(number(0), b.gep(timeout, [number(0), number(index)]))
        b.cbranch(b.icmp_unsigned('>', f.args[1], number(0)), blocks['block_signal'], blocks['done'])
        b.position_at_end(blocks['block_signal'])
        # Block only this calling thread. Never replace the host process's
        # signal disposition, including when an exported Go function is called.
        result = self.syscall(b, self.mask_number, number(SIG_BLOCK),
                              b.ptrtoint(signal_set, I64), b.ptrtoint(old_mask, I64),
                              number(KERNEL_SIGNAL_MASK_BYTES))
        b.cbranch(b.icmp_signed('==', result, number(0)), blocks['pending'], blocks['signal_failure'])
        b.position_at_end(blocks['pending'])
        result = self.syscall(b, self.pending_number, b.ptrtoint(pending, I64),
                              number(KERNEL_SIGNAL_MASK_BYTES))
        b.cbranch(b.icmp_signed('==', result, number(0)), blocks['condition'], blocks['restore'])
        b.position_at_end(blocks['condition'])
        count = b.load(remaining)
        b.cbranch(b.icmp_unsigned('>', count, number(0)), blocks['write'], blocks['restore'])
        b.position_at_end(blocks['write'])
        address = b.load(pointer)
        result = self.syscall(b, self.write_number, number(2), b.ptrtoint(address, I64), count)
        b.cbranch(b.icmp_signed('>', result, number(0)), blocks['advance'], blocks['retry'])
        b.position_at_end(blocks['advance'])
        b.store(b.gep(address, [result]), pointer)
        b.store(b.sub(count, result), remaining)
        b.branch(blocks['condition'])
        b.position_at_end(blocks['retry'])
        b.cbranch(b.icmp_signed('==', result, number(-4)), blocks['condition'], blocks['epipe'])
        b.position_at_end(blocks['epipe'])
        newly_pending = b.icmp_unsigned('==', b.and_(b.load(pending), number(SIGPIPE_MASK)), number(0))
        needs_drain = b.and_(b.icmp_signed('==', result, number(-32)), newly_pending)
        b.cbranch(needs_drain, blocks['drain'], blocks['restore'])
        b.position_at_end(blocks['drain'])
        # Preserve any signal pending before this write. Consume only our new
        # EPIPE notification, with a zero timeout (SIG_IGN produces none).
        waited = self.syscall(b, self.wait_number, b.ptrtoint(signal_set, I64), number(0),
                              b.ptrtoint(timeout, I64), number(KERNEL_SIGNAL_MASK_BYTES))
        b.cbranch(b.icmp_signed('==', waited, number(-4)), blocks['drain'], blocks['drain_result'])
        b.position_at_end(blocks['drain_result'])
        drained = b.or_(b.icmp_signed('==', waited, number(SIGPIPE)),
                        b.icmp_signed('==', waited, number(-11)))
        b.cbranch(drained, blocks['restore'], blocks['signal_failure'])
        b.position_at_end(blocks['restore'])
        restored = self.syscall(b, self.mask_number, number(SIG_SETMASK), b.ptrtoint(old_mask, I64),
                                number(0), number(KERNEL_SIGNAL_MASK_BYTES))
        b.cbranch(b.icmp_signed('==', restored, number(0)), blocks['done'], blocks['signal_failure'])
        b.position_at_end(blocks['signal_failure'])
        # A kernel-policy failure must not return to a caller with a changed
        # signal mask or permit an unsafe write. This fail-closed OS boundary
        # is not an emulation of a Go source panic.
        self.syscall(b, self.exit_number, number(2))
        b.unreachable()
        b.position_at_end(blocks['done'])
        b.ret_void()
        return f

    def integer_helper(self):
        if self.integer_function is not None:
            return self.integer_function
        f = ir.Function(self.owner.module, ir.FunctionType(ir.VoidType(), [I64, ir.IntType(1)]),
                        '__pcc_go_builtin_integer')
        f.linkage = 'internal'
        self.integer_function = f
        entry, digit, sign, minus, output = [f.append_basic_block(n) for n in
                                            ('entry', 'digit', 'sign', 'minus', 'output')]
        b = ir.IRBuilder(entry)
        buffer = b.alloca(ir.ArrayType(I8, 21))
        cursor, magnitude = b.alloca(I64), b.alloca(I64)
        negative = b.and_(f.args[1], b.icmp_signed('<', f.args[0], number(0)))
        # Unsigned two's-complement magnitude also handles min(int64), without
        # signed overflow flags, host arithmetic or allocation.
        b.store(b.select(negative, b.sub(number(0), f.args[0]), f.args[0]), magnitude)
        b.store(number(21), cursor)
        b.branch(digit)
        b.position_at_end(digit)
        value = b.load(magnitude)
        index = b.sub(b.load(cursor), number(1))
        b.store(index, cursor)
        byte = b.add(b.trunc(b.urem(value, number(10)), I8), ir.Constant(I8, 48))
        b.store(byte, b.gep(buffer, [number(0), index]))
        quotient = b.udiv(value, number(10))
        b.store(quotient, magnitude)
        b.cbranch(b.icmp_unsigned('!=', quotient, number(0)), digit, sign)
        b.position_at_end(sign)
        b.cbranch(negative, minus, output)
        b.position_at_end(minus)
        index = b.sub(b.load(cursor), number(1))
        b.store(index, cursor)
        b.store(ir.Constant(I8, 45), b.gep(buffer, [number(0), index]))
        b.branch(output)
        b.position_at_end(output)
        index = b.load(cursor)
        b.call(self.write_helper(), [b.gep(buffer, [number(0), index]), b.sub(number(21), index)])
        b.ret_void()
        return f

    def write(self, value):
        b = self.owner.builder
        b.call(self.write_helper(), [b.extract_value(value, 0), b.extract_value(value, 1)])

    def text(self, value):
        self.write(self.owner.string_value(value))

    def value(self, value, dtype):
        b = self.owner.builder
        if is_string(dtype):
            self.write(value)
        elif underlying(dtype) == 'bool':
            true = self.owner.string_value(b'true')
            false = self.owner.string_value(b'false')
            pointer = b.select(value, b.extract_value(true, 0), b.extract_value(false, 0))
            length = b.select(value, b.extract_value(true, 1), b.extract_value(false, 1))
            b.call(self.write_helper(), [pointer, length])
        else:
            signed = integer_signed(dtype)
            if value.type.width < 64:
                value = (b.sext if signed else b.zext)(value, I64)
            b.call(self.integer_helper(), [value, ir.Constant(ir.IntType(1), int(signed))])

    def emit(self, expr):
        if 'linux' not in self.owner.module.triple.split('-'):
            raise UnsupportedGo(expr.token, 'go.unsupported.builtin-io',
                                'bootstrap output and terminal panic currently require owned Linux I/O')
        # Evaluate and capture every argument before producing any output.
        values = self.owner.values(expr.args)
        types = (result_types(expr.args[0].type) if len(expr.args) == 1 and
                 isinstance(expr.args[0].type, Results) else [arg.type for arg in expr.args])
        if expr.value == 'panic':
            self.text(b'panic: ')
        for index, (value, dtype) in enumerate(zip(values, types)):
            if expr.value == 'println' and index:
                self.text(b' ')
            self.value(value, dtype)
        if expr.value in ('println', 'panic'):
            self.text(b'\n')
        if expr.value == 'panic':
            # No admitted source can install deferred cleanup/recovery or
            # spawn another goroutine. This is the actual uncaught terminal
            # case, not a claim of general panic/unwind or a returned error.
            self.syscall(self.owner.builder, self.exit_number, number(2))
            self.owner.builder.unreachable()
        return None
