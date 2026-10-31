"""Lower typed Go scalar AST directly into PCC's public, owned IR.

No C/Python source translation, external compiler, distinct GC or scheduler.
Each symbol has a semantic Go type; int and int64 share an i64 representation
without becoming assignable to one another. Loads/stores and control edges are
ordinary public IR, handled by the existing optimizer and self backend.
"""

from pcc.ir.compat import ir
from pcc.frontends.go.types import ArrayType, integer_bits, integer_signed


def function_symbol(name, package_path=None):
    """Collision-free ASCII ABI-internal name, including Unicode identifiers."""
    prefix = "__pcc_go_main_" if package_path is None else "__pcc_go_pkg_" + package_path.encode("utf-8").hex() + "_"
    return prefix + name.encode("utf-8").hex()


GO_MAIN_SYMBOL = function_symbol("main")


def ir_type(dtype):
    if isinstance(dtype, ArrayType):
        return ir.ArrayType(ir_type(dtype.element), dtype.count)
    if dtype == "void":
        return ir.VoidType()
    if dtype in ("bool", "untyped bool"):
        return ir.IntType(1)
    return ir.IntType(integer_bits(dtype))


class Lowerer:
    def __init__(self, program, target_triple, module_name):
        self.program = program
        self.module = ir.Module(name="go_" + module_name.encode("utf-8", "surrogatepass").hex())
        self.module.triple = target_triple
        self.functions = {}
        self.slots = {}
        self.globals = {}
        self.temps = {}
        self.saved_values = {}
        self.loops = []
        self.builder = None
        self.function = None

    def lower(self):
        for declaration in self.program.variables:
            for symbol in declaration.symbols:
                storage = ir.GlobalVariable(self.module, ir_type(symbol.type), symbol.abi_symbol)
                value = symbol.initializer
                if value is None and not isinstance(symbol.type, ArrayType):
                    value = 0
                if isinstance(symbol.type, ArrayType) and value is not None:
                    value = [ir.Constant(ir_type(symbol.type.element), int(item)) for item in value]
                storage.initializer = ir.Constant(ir_type(symbol.type), value)
                self.globals[symbol] = storage
        for source in self.program.functions:
            ftype = ir.FunctionType(ir_type(source.result), [ir_type(dtype) for _, dtype in source.params])
            self.functions[source.abi_symbol] = ir.Function(self.module, ftype, source.abi_symbol)
        for source in self.program.functions:
            self.function = self.functions[source.abi_symbol]
            entry = self.function.append_basic_block("entry")
            self.builder = ir.IRBuilder(entry)
            self.slots = dict(self.globals)
            self.temps = {}
            for index, expr in enumerate(source.temporaries):
                if id(expr) not in self.temps:
                    self.temps[id(expr)] = self.builder.alloca(ir_type(expr.type), name="array_temp_" + str(index))
            for index, symbol in enumerate(source.locals):
                self.slots[symbol] = self.builder.alloca(ir_type(symbol.type), name="local_" + str(index))
            for symbol, argument in zip(source.parameters, self.function.args):
                self.builder.store(argument, self.slots[symbol])
            self.statements(source.body)
            if not self.builder.block.is_terminated:
                if source.result == "void":
                    self.builder.ret_void()
                else:
                    # Sema proved the source ends in a terminating statement;
                    # only a CFG merge without incoming edges can remain here.
                    self.builder.unreachable()
        return self.module

    def statements(self, statements):
        for statement in statements:
            if self.builder.block.is_terminated:
                break
            self.statement(statement)

    def statement(self, statement):
        kind = statement.kind
        if kind == "const":
            return
        if kind == "=":
            # Phase one evaluates LHS index operands and every RHS, left to
            # right; phase two commits stores left to right. Array SSA loads
            # are value snapshots, never an implicit pointer/decay alias.
            targets = [None if target.kind == "name" and target.value == "_" else self.address(target)
                       for target in statement.targets]
            values = [self.expression(expr) for expr in statement.exprs]
            for target, value in zip(targets, values):
                if target is not None:
                    self.builder.store(value, target)
            return
        if kind in ("++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "&^=", "<<=", ">>="):
            target = statement.targets[0]
            address = self.address(target)
            self.saved_values[id(target)] = self.builder.load(address)
            value = self.expression(statement.exprs[0])
            del self.saved_values[id(target)]
            self.builder.store(value, address)
            return
        if kind in ("var", ":="):
            values = [self.expression(expr) for expr in statement.exprs]
            for index, symbol in enumerate(statement.symbols):
                if symbol is not None and symbol.token.text != "_":
                    value = values[index] if values else ir.Constant(ir_type(symbol.type), None if isinstance(symbol.type, ArrayType) else 0)
                    self.builder.store(value, self.slots[symbol])
            return
        if kind == "expression":
            self.expression(statement.exprs[0])
            return
        if kind == "return":
            if statement.exprs:
                self.builder.ret(self.expression(statement.exprs[0]))
            else:
                self.builder.ret_void()
            return
        if kind == "block":
            self.statements(statement.body)
            return
        if kind == "break":
            self.builder.branch(self.loops[-1][1])
            return
        if kind == "continue":
            self.builder.branch(self.loops[-1][0])
            return
        if kind == "if":
            if statement.init is not None:
                self.statement(statement.init)
            condition = self.expression(statement.exprs[0])
            yes = self.function.append_basic_block("if_yes")
            no = self.function.append_basic_block("if_no")
            done = self.function.append_basic_block("if_done")
            self.builder.cbranch(condition, yes, no)
            self.builder.position_at_end(yes)
            self.statements(statement.body)
            if not self.builder.block.is_terminated:
                self.builder.branch(done)
            self.builder.position_at_end(no)
            self.statements(statement.otherwise or [])
            if not self.builder.block.is_terminated:
                self.builder.branch(done)
            self.builder.position_at_end(done)
            if statement.terminates:
                self.builder.unreachable()
            return
        if kind == "for":
            if statement.init is not None:
                self.statement(statement.init)
            check = self.function.append_basic_block("for_check")
            body = self.function.append_basic_block("for_body")
            post = self.function.append_basic_block("for_post")
            done = self.function.append_basic_block("for_done")
            self.builder.branch(check)
            self.builder.position_at_end(check)
            if statement.exprs:
                self.builder.cbranch(self.expression(statement.exprs[0]), body, done)
            else:
                self.builder.branch(body)
            self.builder.position_at_end(body)
            self.loops.append((post, done))
            self.statements(statement.body)
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(post)
            self.builder.position_at_end(post)
            if statement.post is not None:
                self.statement(statement.post)
            if not self.builder.block.is_terminated:
                self.builder.branch(check)
            self.builder.position_at_end(done)
            if statement.terminates:
                self.builder.unreachable()
            return
        raise AssertionError("unlowered typed statement: " + kind)

    def address(self, expr):
        if expr.kind in ("name", "selector"):
            return self.slots[expr.symbol]
        if expr.kind == "index":
            base, index = expr.args
            if base.addressable:
                storage = self.address(base)
            else:
                value = self.expression(base)
                storage = self.temps[id(base)]
                self.builder.store(value, storage)
            # Sema either checked a constant or proved a type/range bound;
            # unchecked runtime indices never reach this GEP.
            offset = self.expression(index)
            if offset.type.width < 64:
                extend = self.builder.sext if integer_signed(index.type) else self.builder.zext
                offset = extend(offset, ir.IntType(64))
            return self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), offset])
        raise AssertionError("unlowered address: " + expr.kind)

    def expression(self, expr):
        if id(expr) in self.saved_values:
            return self.saved_values[id(expr)]
        if expr.kind == "array":
            values = [self.expression(item) for item in expr.args]
            storage = self.temps[id(expr)]
            self.builder.store(ir.Constant(ir_type(expr.type), None), storage)
            for index, value in zip(expr.indices, values):
                element = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(64), index)])
                self.builder.store(value, element)
            return self.builder.load(storage)
        if expr.constant is not None:
            return ir.Constant(ir_type(expr.type), int(expr.constant))
        if expr.kind in ("name", "selector", "index"):
            return self.builder.load(self.address(expr))
        if expr.kind == "call":
            arguments = [self.expression(arg) for arg in expr.args]
            if expr.symbol is None:
                value = arguments[0]
                target = ir_type(expr.type)
                if value.type.width > target.width:
                    return self.builder.trunc(value, target)
                if value.type.width < target.width:
                    extend = self.builder.sext if integer_signed(expr.args[0].type) else self.builder.zext
                    return extend(value, target)
                return value
            return self.builder.call(self.functions[expr.symbol.abi_symbol], arguments)
        if expr.kind == "unary":
            value = self.expression(expr.args[0])
            if expr.value == "+":
                return value
            if expr.value == "-":
                return self.builder.neg(value)
            return self.builder.not_(value)
        if expr.kind == "binary":
            op = expr.value
            if op in ("&&", "||"):
                return self.short_circuit(expr)
            left = self.expression(expr.args[0])
            if op in ("<<", ">>"):
                return self.shift(expr, left)
            right = self.expression(expr.args[1])
            if op in ("==", "!=", "<", "<=", ">", ">="):
                compare = self.builder.icmp_signed if expr.args[0].type in ("bool", "untyped bool") or integer_signed(expr.args[0].type) else self.builder.icmp_unsigned
                return compare(op, left, right)
            if op in ("/", "%") and expr.args[1].constant == -1:
                # Go defines MIN_INT / -1 == MIN_INT, unlike LLVM sdiv.
                return self.builder.neg(left) if op == "/" else ir.Constant(left.type, 0)
            if op == "&^":
                return self.builder.and_(left, self.builder.not_(right))
            # The shared machine emitter's scalar multiply/divide lane is at
            # least i32. Widen narrow operands according to their Go type, then
            # truncate the result; this preserves Go's narrow wrap/remainder
            # semantics without changing the semantic type or shared backend.
            narrow = op in ("*", "/", "%") and integer_bits(expr.type) < 32
            if narrow:
                extend = self.builder.sext if integer_signed(expr.type) else self.builder.zext
                left = extend(left, ir.IntType(32))
                right = extend(right, ir.IntType(32))
            methods = {
                "+": self.builder.add, "-": self.builder.sub, "*": self.builder.mul,
                "/": self.builder.sdiv if integer_signed(expr.type) else self.builder.udiv,
                "%": self.builder.srem if integer_signed(expr.type) else self.builder.urem,
                "&": self.builder.and_, "|": self.builder.or_, "^": self.builder.xor,
            }
            value = methods[op](left, right)
            return self.builder.trunc(value, ir_type(expr.type)) if narrow else value
        raise AssertionError("unlowered typed expression: " + expr.kind)

    def shift(self, expr, left):
        width = integer_bits(expr.type)
        signed = integer_signed(expr.type)
        emitter = self.builder.shl if expr.value == "<<" else self.builder.ashr if signed else self.builder.lshr
        overflow = (self.builder.ashr(left, ir.Constant(left.type, width - 1))
                    if expr.value == ">>" and signed else ir.Constant(left.type, 0))
        count = expr.args[1].constant
        if count is not None:
            return overflow if count >= width else emitter(left, ir.Constant(left.type, count))
        # A dynamic unsigned count cannot panic. Clamp *before* emitting a
        # shift so neither the in-range nor out-of-range path creates poison.
        amount = self.expression(expr.args[1])
        in_range = self.builder.icmp_unsigned("<", amount, ir.Constant(amount.type, width))
        safe = self.builder.select(in_range, amount, ir.Constant(amount.type, 0))
        if safe.type.width < width:
            safe = self.builder.zext(safe, left.type)
        elif safe.type.width > width:
            safe = self.builder.trunc(safe, left.type)
        value = emitter(left, safe)
        return self.builder.select(in_range, value, overflow)

    def short_circuit(self, expr):
        left = self.expression(expr.args[0])
        left_block = self.builder.block
        right_block = self.function.append_basic_block("logical_rhs")
        done = self.function.append_basic_block("logical_done")
        is_and = expr.value == "&&"
        self.builder.cbranch(left, right_block if is_and else done, done if is_and else right_block)
        self.builder.position_at_end(right_block)
        right = self.expression(expr.args[1])
        right_end = self.builder.block
        self.builder.branch(done)
        self.builder.position_at_end(done)
        result = self.builder.phi(ir.IntType(1))
        result.add_incoming(ir.Constant(ir.IntType(1), 0 if is_and else 1), left_block)
        result.add_incoming(right, right_end)
        return result
