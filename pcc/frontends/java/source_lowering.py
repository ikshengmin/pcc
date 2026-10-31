"""Typed Java source AST directly to PCC public IR, with owned target output.

No class-file construction, JVM instruction list, bytecode verifier, Java
interpreter or external compiler is on this lowering route.
"""
from pcc.ir.compat import ir

from .artifact import JavaCompilation, method_symbol
from .integer_semantics import emit_binary, emit_narrow, emit_nonzero_division


OPERATIONS = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem", "&": "and", "|": "or", "^": "xor",
              "<<": "shl", ">>": "ashr", ">>>": "lshr"}


class SourceLowerer:
    def __init__(self, program, target, programs=None):
        self.program = program
        self.programs = tuple(sorted(programs or (program,), key=lambda item: item.name))
        self.module = ir.Module(name="java_" + program.name.encode("utf-8").hex())
        self.module.triple = target
        self.functions = {}
        self.i32 = ir.IntType(32)
        self.controls = []

    @staticmethod
    def scalar_type(dtype):
        if dtype == "void":
            return ir.VoidType()
        return ir.IntType(1 if dtype == "boolean" else 64 if dtype == "long" else 32)

    def coerce(self, value, dtype):
        target = self.scalar_type(dtype)
        if value.type == target:
            return value
        if value.type.width > target.width:
            return self.builder.trunc(value, target)
        return self.builder.sext(value, target)

    def lower(self, entry):
        methods = [method for program in self.programs for method in program.methods]
        for method in methods:
            key = (method.owner, method.token.text, method.descriptor)
            signature = ir.FunctionType(self.scalar_type(method.result),
                                        [self.scalar_type(dtype) for dtype in method.parameter_types])
            self.functions[key] = ir.Function(self.module, signature, method_symbol(*key))
        for method in methods:
            self.function = self.functions[method.owner, method.token.text, method.descriptor]
            self.result_type = method.result
            self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
            self.slots = {binding: self.builder.alloca(self.scalar_type(binding.dtype), name="local_" + str(binding.slot))
                          for binding in method.locals}
            for argument, binding in zip(self.function.args, method.locals):
                self.builder.store(argument, self.slots[binding])
            self.statement(method.body)
            if not self.builder.block.is_terminated:
                if method.result == "void":
                    self.builder.ret_void()
                else:
                    raise AssertionError("source sema admitted a missing return")
        return JavaCompilation(self.module, self.program.name, entry,
                               tuple((method.token.text, method.descriptor) for method in self.program.methods),
                               tuple(self.functions))

    def expression(self, node):
        if node.constant is not None:
            return ir.Constant(self.scalar_type(node.dtype), int(node.constant))
        kind = node.kind
        if kind == "name":
            return self.builder.load(self.slots[node.symbol])
        if kind == "call":
            arguments = [self.coerce(self.expression(argument), dtype)
                         for argument, dtype in zip(node.children, node.symbol.parameter_types)]
            return self.builder.call(self.functions[node.symbol.owner, node.symbol.token.text, node.symbol.descriptor], arguments)
        if kind == "group":
            return self.expression(node.children[0])
        if kind == "conditional":
            condition, yes, no = node.children
            value = self.expression(condition)
            yes_block = self.function.append_basic_block("conditional_yes")
            no_block = self.function.append_basic_block("conditional_no")
            done = self.function.append_basic_block("conditional_done")
            self.builder.cbranch(value, yes_block, no_block)
            incoming = []
            for block, arm in ((yes_block, yes), (no_block, no)):
                self.builder.position_at_end(block)
                result = self.coerce(self.expression(arm), node.dtype)
                incoming.append((result, self.builder.block))
                self.builder.branch(done)
            self.builder.position_at_end(done)
            result = self.builder.phi(self.scalar_type(node.dtype))
            for value, block in incoming:
                result.add_incoming(value, block)
            return result
        if kind == "cast":
            value = self.expression(node.children[0])
            if node.value in ("int", "long", "boolean"):
                return self.coerce(value, node.value)
            return emit_narrow(self.builder, {"byte": "i2b", "short": "i2s", "char": "i2c"}[node.value], value)
        if kind == "unary":
            value = self.expression(node.children[0])
            if node.value == "+":
                return value
            if node.value == "-":
                return self.builder.sub(ir.Constant(value.type, 0), value)
            return self.builder.not_(value)
        if kind == "binary":
            left = self.expression(node.children[0])
            if node.value in ("&&", "||"):
                origin = self.builder.block
                right_block = self.function.append_basic_block("logical_rhs")
                done = self.function.append_basic_block("logical_done")
                is_and = node.value == "&&"
                self.builder.cbranch(left, right_block if is_and else done, done if is_and else right_block)
                self.builder.position_at_end(right_block)
                right = self.expression(node.children[1])
                right_end = self.builder.block
                self.builder.branch(done)
                self.builder.position_at_end(done)
                result = self.builder.phi(ir.IntType(1))
                result.add_incoming(ir.Constant(ir.IntType(1), 0 if is_and else 1), origin)
                result.add_incoming(right, right_end)
                return result
            right = self.expression(node.children[1])
            dtype = node.operation_type or "boolean"
            left = self.coerce(left, dtype)
            if node.value not in ("<<", ">>", ">>>"):
                right = self.coerce(right, dtype)
            if node.value in ("==", "!=", "<", "<=", ">", ">="):
                return self.builder.icmp_signed(node.value, left, right)
            if node.value in ("/", "%"):
                return emit_nonzero_division(self.builder, OPERATIONS[node.value], left, node.children[1].constant)
            return emit_binary(self.builder, OPERATIONS[node.value], left, right)
        raise AssertionError("unlowered typed source expression " + kind)

    def statement(self, node):
        kind = node.kind
        if kind == "block":
            for child in node.children:
                self.statement(child)
            return
        if kind == "empty":
            return
        if kind == "var":
            if node.children:
                self.builder.store(self.coerce(self.expression(node.children[0]), node.symbol.dtype), self.slots[node.symbol])
            return
        if kind == "assign":
            slot = self.slots[node.symbol]
            if node.value == "=":
                value = self.coerce(self.expression(node.children[0]), node.symbol.dtype)
            else:
                left = self.coerce(self.builder.load(slot), node.operation_type)
                if node.value in ("++", "--"):
                    right, operation = ir.Constant(left.type, 1), "add" if node.value == "++" else "sub"
                else:
                    right, operation = self.expression(node.children[0]), OPERATIONS[node.value[:-1]]
                if operation not in ("shl", "ashr", "lshr"):
                    right = self.coerce(right, node.operation_type)
                value = (emit_nonzero_division(self.builder, operation, left, node.children[0].constant)
                         if operation in ("sdiv", "srem") else emit_binary(self.builder, operation, left, right))
                value = self.coerce(value, node.symbol.dtype)
            self.builder.store(value, slot)
            return
        if kind == "expression":
            self.expression(node.children[0])
            return
        if kind == "return":
            if node.children:
                self.builder.ret(self.coerce(self.expression(node.children[0]), self.result_type))
            else:
                self.builder.ret_void()
            return
        if kind in ("break", "continue"):
            target = (self.controls[-1][1] if kind == "break" else
                      next(target[0] for target in reversed(self.controls) if target[0] is not None))
            self.builder.branch(target)
            return
        if kind == "if":
            condition, yes, no = node.children
            value = self.expression(condition)
            yes_block = self.function.append_basic_block("if_yes")
            no_block = self.function.append_basic_block("if_no")
            done = self.function.append_basic_block("if_done")
            self.builder.cbranch(value, yes_block, no_block)
            self.builder.position_at_end(yes_block)
            self.statement(yes)
            if not self.builder.block.is_terminated:
                self.builder.branch(done)
            self.builder.position_at_end(no_block)
            self.statement(no)
            if not self.builder.block.is_terminated:
                self.builder.branch(done)
            self.builder.position_at_end(done)
            if not node.completes:
                self.builder.unreachable()
            return
        if kind == "switch":
            selector, *groups = node.children
            value = self.expression(selector)
            done = self.function.append_basic_block("switch_done")
            blocks = [self.function.append_basic_block("switch_group") for group in groups]
            default = next((block for group, block in zip(groups, blocks)
                            if any(label.kind == "default" for label in group.value)), done)
            dispatch = self.builder.switch(value, default)
            for group, block in zip(groups, blocks):
                for label in group.value:
                    if label.kind == "case":
                        dispatch.add_case(ir.Constant(value.type, label.constant), block)
            self.controls.append((None, done))
            for index, (group, block) in enumerate(zip(groups, blocks)):
                self.builder.position_at_end(block)
                for child in group.children:
                    self.statement(child)
                if not self.builder.block.is_terminated:
                    target = blocks[index + 1] if node.value == "colon" and index + 1 < len(blocks) else done
                    self.builder.branch(target)
            self.controls.pop()
            self.builder.position_at_end(done)
            if not node.completes:
                self.builder.unreachable()
            return
        if kind == "do":
            condition, body = node.children
            body_block = self.function.append_basic_block("do_body")
            check = self.function.append_basic_block("do_check")
            done = self.function.append_basic_block("do_done")
            self.builder.branch(body_block)
            self.builder.position_at_end(body_block)
            self.controls.append((check, done))
            self.statement(body)
            self.controls.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(check)
            self.builder.position_at_end(check)
            self.builder.cbranch(self.expression(condition), body_block, done)
            self.builder.position_at_end(done)
            if not node.completes:
                self.builder.unreachable()
            return
        if kind in ("while", "for"):
            if kind == "for":
                initial, condition, update, body = node.children
                self.statement(initial)
            else:
                condition, body = node.children
                update = None
            check = self.function.append_basic_block("loop_check")
            body_block = self.function.append_basic_block("loop_body")
            update_block = self.function.append_basic_block("loop_update") if update is not None else check
            done = self.function.append_basic_block("loop_done")
            self.builder.branch(check)
            self.builder.position_at_end(check)
            self.builder.cbranch(self.expression(condition), body_block, done)
            self.builder.position_at_end(body_block)
            self.controls.append((update_block, done))
            self.statement(body)
            self.controls.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(update_block)
            if update is not None:
                self.builder.position_at_end(update_block)
                self.statement(update)
                self.builder.branch(check)
            self.builder.position_at_end(done)
            if not node.completes:
                self.builder.unreachable()
            return
        raise AssertionError("unlowered source statement " + kind)
