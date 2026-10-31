"""Lower typed Go scalar AST directly into PCC's public, owned IR.

No C/Python source translation, external compiler, distinct GC or scheduler.
Each symbol has a semantic Go type; int and int64 share an i64 representation
without becoming assignable to one another. Loads/stores and control edges are
ordinary public IR, handled by the existing optimizer and self backend.
"""

from pcc.ir.compat import ir
from pcc.frontends.go.records import StructType, underlying
from pcc.frontends.go.types import ArrayType, Results, result_types, integer_bits, integer_signed, is_string


def function_symbol(name, package_path=None):
    """Collision-free ASCII ABI-internal name, including Unicode identifiers."""
    prefix = "__pcc_go_main_" if package_path is None else "__pcc_go_pkg_" + package_path.encode("utf-8").hex() + "_"
    return prefix + name.encode("utf-8").hex()


GO_MAIN_SYMBOL = function_symbol("main")


def ir_type(dtype):
    dtype = underlying(dtype)
    if isinstance(dtype, StructType):
        return ir.LiteralStructType([ir_type(field.type) for field in dtype.fields])
    if isinstance(dtype, Results):
        return ir.LiteralStructType([ir_type(item) for item in dtype.elements])
    if isinstance(dtype, ArrayType):
        return ir.ArrayType(ir_type(dtype.element), dtype.count)
    if is_string(dtype):
        return ir.LiteralStructType([ir.IntType(8).as_pointer(), ir.IntType(64)])
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
        self.breaks = []
        self.fallthrough = None
        self.builder = None
        self.function = None
        self.string_literals = {}
        self.string_descriptors = {}

    def string_constant(self, data):
        if not data:
            pointer = ir.Constant(ir.IntType(8).as_pointer(), None)
        else:
            if data not in self.string_literals:
                storage = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), len(data)),
                                            "__pcc_go_string_" + str(len(self.string_literals)))
                storage.linkage = "private"
                storage.global_constant = True
                storage.initializer = ir.Constant(storage.value_type, bytearray(data))
                self.string_literals[data] = storage
            pointer = self.string_literals[data].gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        return ir.Constant(ir_type("string"), [pointer, ir.Constant(ir.IntType(64), len(data))])

    def string_value(self, data):
        # The shared backend supports pointer relocations in static aggregate
        # initializers, but not inline aggregate literals in instructions.
        # Load the immutable descriptor as a value; its pointer still denotes
        # the same private readonly backing, with no runtime allocation/init.
        if data not in self.string_descriptors:
            descriptor = ir.GlobalVariable(self.module, ir_type("string"),
                                           "__pcc_go_string_value_" + str(len(self.string_descriptors)))
            descriptor.linkage = "private"
            descriptor.global_constant = True
            descriptor.initializer = self.string_constant(data)
            self.string_descriptors[data] = descriptor
        return self.builder.load(self.string_descriptors[data])

    def zero(self, dtype):
        aggregate = isinstance(underlying(dtype), (ArrayType, StructType))
        return self.string_constant(b"") if is_string(dtype) else ir.Constant(ir_type(dtype), None if aggregate else 0)

    def static_initializer(self, dtype, value):
        if value is None:
            return self.zero(dtype)
        base = underlying(dtype)
        if is_string(dtype):
            return self.string_constant(value)
        if isinstance(base, ArrayType):
            return ir.Constant(ir_type(dtype), [self.static_initializer(base.element, item) for item in value])
        if isinstance(base, StructType):
            return ir.Constant(ir_type(dtype), [self.static_initializer(field.type, item)
                                                for field, item in zip(base.fields, value)])
        return ir.Constant(ir_type(dtype), int(value))

    def lower(self):
        for declaration in self.program.variables:
            for symbol in declaration.symbols:
                storage = ir.GlobalVariable(self.module, ir_type(symbol.type), symbol.abi_symbol)
                storage.initializer = self.static_initializer(symbol.type, symbol.initializer)
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
            self.source_function = source
            self.result_storage = self.builder.alloca(ir_type(source.result), name="multiple_results") if isinstance(source.result, Results) else None
            for symbol in source.result_symbols:
                self.builder.store(self.zero(symbol.type), self.slots[symbol])
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
            values = self.values(statement.exprs)
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
            values = self.values(statement.exprs)
            for index, symbol in enumerate(statement.symbols):
                if symbol is not None and symbol.token.text != "_":
                    value = values[index] if values else self.zero(symbol.type)
                    self.builder.store(value, self.slots[symbol])
            return
        if kind == "expression":
            self.expression(statement.exprs[0])
            return
        if kind == "return":
            source = self.source_function
            if statement.exprs:
                values = self.values(statement.exprs)
                for symbol, value in zip(source.result_symbols, values):
                    self.builder.store(value, self.slots[symbol])
            else:
                values = [self.builder.load(self.slots[symbol]) for symbol in source.result_symbols]
            if not values:
                self.builder.ret_void()
            elif len(values) == 1:
                self.builder.ret(values[0])
            else:
                for index, value in enumerate(values):
                    field = self.builder.gep(self.result_storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), index)])
                    self.builder.store(value, field)
                self.builder.ret(self.builder.load(self.result_storage))
            return
        if kind == "block":
            self.statements(statement.body)
            return
        if kind == "break":
            self.builder.branch(self.breaks[-1])
            return
        if kind == "continue":
            self.builder.branch(self.loops[-1][0])
            return
        if kind == "fallthrough":
            self.builder.branch(self.fallthrough)
            return
        if kind == "switch":
            if statement.init is not None:
                self.statement(statement.init)
            selector = statement.exprs[0]
            self.saved_values[id(selector)] = self.expression(selector)
            bodies = [self.function.append_basic_block("switch_case") for _ in statement.cases]
            done = self.function.append_basic_block("switch_done")
            default = done
            # Only dispatch evaluates case expressions. Fallthrough goes
            # directly to the next clause body, including a middle default.
            for clause, body in zip(statement.cases, bodies):
                if clause.kind == "default":
                    default = body
                for comparison in clause.exprs:
                    next_test = self.function.append_basic_block("switch_test")
                    self.builder.cbranch(self.expression(comparison), body, next_test)
                    self.builder.position_at_end(next_test)
            self.builder.branch(default)
            del self.saved_values[id(selector)]
            self.breaks.append(done)
            previous = self.fallthrough
            for index, (clause, body) in enumerate(zip(statement.cases, bodies)):
                self.builder.position_at_end(body)
                self.fallthrough = bodies[index + 1] if index + 1 < len(bodies) else None
                self.statements(clause.body)
                if not self.builder.block.is_terminated:
                    self.builder.branch(done)
            self.fallthrough = previous
            self.breaks.pop()
            self.builder.position_at_end(done)
            if statement.terminates:
                self.builder.unreachable()
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
        if kind == "range":
            ranged = statement.exprs[0]
            dtype = underlying(ranged.type)
            uses_element = len(statement.targets) == 2 and not (
                statement.targets[-1].kind == "name" and statement.targets[-1].value == "_")
            if statement.evaluate_range:
                value = self.expression(ranged)
                if uses_element:
                    # An array range snapshots the entire value once, not a
                    # pointer to the original variable or caller's storage.
                    self.builder.store(value, self.temps[id(ranged)])
            index_slot = self.temps[id(statement.range_index)]
            index_type = ir.IntType(64)
            self.builder.store(ir.Constant(index_type, 0), index_slot)
            check = self.function.append_basic_block("range_check")
            body = self.function.append_basic_block("range_body")
            post = self.function.append_basic_block("range_post")
            done = self.function.append_basic_block("range_done")
            self.builder.branch(check)
            self.builder.position_at_end(check)
            index = self.builder.load(index_slot)
            self.builder.cbranch(self.builder.icmp_unsigned("<", index, ir.Constant(index_type, dtype.count)), body, done)
            self.builder.position_at_end(body)
            if statement.init is not None:
                values = [index]
                if len(statement.targets) == 2:
                    if uses_element:
                        # The hidden induction value was checked above and is
                        # never exposed for user mutation. No panic shortcut.
                        address = self.builder.gep(self.temps[id(ranged)], [ir.Constant(ir.IntType(32), 0), index])
                        values.append(self.builder.load(address))
                    else:
                        values.append(self.zero(dtype.element))
                for expr, value in zip(statement.init.exprs, values):
                    self.saved_values[id(expr)] = value
                self.statement(statement.init)
                for expr in statement.init.exprs:
                    del self.saved_values[id(expr)]
            self.loops.append((post, done))
            self.breaks.append(done)
            self.statements(statement.body)
            self.breaks.pop()
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(post)
            self.builder.position_at_end(post)
            self.builder.store(self.builder.add(index, ir.Constant(index_type, 1)), index_slot)
            self.builder.branch(check)
            self.builder.position_at_end(done)
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
            self.breaks.append(done)
            self.statements(statement.body)
            self.breaks.pop()
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

    def values(self, expressions):
        if len(expressions) == 1 and isinstance(expressions[0].type, Results):
            call = expressions[0]
            result = self.expression(call)
            return [self.builder.extract_value(result, index) for index in range(len(call.type.elements))]
        return [self.expression(expr) for expr in expressions]

    def address(self, expr):
        if expr.kind in ("name", "selector"):
            return self.slots[expr.symbol]
        if expr.kind == "field":
            return self.builder.gep(self.address(expr.args[0]),
                                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), expr.indices[0])])
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
        if expr.kind in ("array", "struct"):
            values = [self.expression(item) for item in expr.args]
            storage = self.temps[id(expr)]
            self.builder.store(ir.Constant(ir_type(expr.type), None), storage)
            for index, value in zip(expr.indices, values):
                index_type = ir.IntType(32 if expr.kind == "struct" else 64)
                element = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(index_type, index)])
                self.builder.store(value, element)
            return self.builder.load(storage)
        if expr.constant is not None:
            return self.string_value(expr.constant) if is_string(expr.type) else ir.Constant(ir_type(expr.type), int(expr.constant))
        if expr.kind == "string_compare":
            return self.string_compare(expr)
        if expr.kind == "string_len":
            return self.builder.extract_value(self.expression(expr.args[0]), 1)
        if expr.kind == "array_len":
            self.expression(expr.args[0])
            return ir.Constant(ir_type(expr.type), underlying(expr.args[0].type).count)
        if expr.kind == "index" and is_string(expr.args[0].type):
            base, index = expr.args
            value = self.expression(base)
            pointer = self.builder.extract_value(value, 0)
            offset = self.expression(index)
            if offset.type.width < 64:
                extend = self.builder.sext if integer_signed(index.type) else self.builder.zext
                offset = extend(offset, ir.IntType(64))
            return self.builder.load(self.builder.gep(pointer, [offset]))
        if expr.kind == "field":
            if expr.addressable:
                return self.builder.load(self.address(expr))
            return self.builder.extract_value(self.expression(expr.args[0]), expr.indices[0])
        if expr.kind in ("name", "selector", "index"):
            return self.builder.load(self.address(expr))
        if expr.kind == "call":
            receiver = [self.expression(expr.receiver)] if expr.receiver is not None else []
            arguments = receiver + self.values(expr.args)
            if expr.symbol is None:
                value = arguments[0]
                target = ir_type(expr.type)
                if value.type == target:
                    return value
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
                compare = self.builder.icmp_signed if underlying(expr.args[0].type) in ("bool", "untyped bool") or integer_signed(expr.args[0].type) else self.builder.icmp_unsigned
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


    def string_compare(self, expr):
        """Lexicographic unsigned bytes, bounded by both live descriptor lengths.

        This ordinary public-IR loop needs no allocator, runtime call, or panic:
        every byte load is dominated by the two explicit length comparisons.
        """
        left, right = [self.expression(item) for item in expr.args]
        left_pointer = self.builder.extract_value(left, 0)
        right_pointer = self.builder.extract_value(right, 0)
        left_length = self.builder.extract_value(left, 1)
        right_length = self.builder.extract_value(right, 1)
        origin = self.builder.block
        check = self.function.append_basic_block("string.check")
        body = self.function.append_basic_block("string.byte")
        advance = self.function.append_basic_block("string.advance")
        difference = self.function.append_basic_block("string.difference")
        length = self.function.append_basic_block("string.length")
        done = self.function.append_basic_block("string.done")
        self.builder.branch(check)
        self.builder.position_at_end(check)
        index = self.builder.phi(ir.IntType(64))
        index.add_incoming(ir.Constant(ir.IntType(64), 0), origin)
        available = self.builder.and_(self.builder.icmp_unsigned("<", index, left_length),
                                      self.builder.icmp_unsigned("<", index, right_length))
        self.builder.cbranch(available, body, length)
        self.builder.position_at_end(body)
        a = self.builder.load(self.builder.gep(left_pointer, [index]))
        b = self.builder.load(self.builder.gep(right_pointer, [index]))
        self.builder.cbranch(self.builder.icmp_unsigned("!=", a, b), difference, advance)
        self.builder.position_at_end(advance)
        following = self.builder.add(index, ir.Constant(ir.IntType(64), 1))
        index.add_incoming(following, advance)
        self.builder.branch(check)
        self.builder.position_at_end(difference)
        byte_result = self.builder.icmp_unsigned(expr.value, a, b)
        self.builder.branch(done)
        self.builder.position_at_end(length)
        length_result = self.builder.icmp_unsigned(expr.value, left_length, right_length)
        self.builder.branch(done)
        self.builder.position_at_end(done)
        result = self.builder.phi(ir.IntType(1))
        result.add_incoming(byte_result, difference)
        result.add_incoming(length_result, length)
        return result
