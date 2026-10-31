"""Typed Java source AST directly to PCC public IR, with owned target output.

No class-file construction, JVM instruction list, bytecode verifier, Java
interpreter or external compiler is on this lowering route.
"""
from pcc.ir.compat import ir

from .artifact import JavaCompilation, method_symbol
from .integer_semantics import emit_binary, emit_narrow


OPERATIONS = {"+": "add", "-": "sub", "*": "mul", "&": "and", "|": "or", "^": "xor",
              "<<": "shl", ">>": "ashr", ">>>": "lshr"}


class SourceLowerer:
    def __init__(self, program, target):
        self.program = program
        self.module = ir.Module(name="java_" + program.name.encode("utf-8").hex())
        self.module.triple = target
        self.functions = {}
        self.i32 = ir.IntType(32)
        self.loops = []

    def lower(self, entry):
        for method in self.program.methods:
            key = (method.token.text, method.descriptor)
            signature = ir.FunctionType(self.i32 if method.result == "int" else ir.VoidType(),
                                        [self.i32 for _ in method.parameters])
            self.functions[key] = ir.Function(self.module, signature, method_symbol(self.program.name, *key))
        for method in self.program.methods:
            self.function = self.functions[method.token.text, method.descriptor]
            self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
            self.slots = {binding: self.builder.alloca(self.i32, name="local_" + str(binding.slot))
                          for binding in method.locals}
            for argument, binding in zip(self.function.args, method.locals):
                self.builder.store(argument, self.slots[binding])
            self.statement(method.body)
            if not self.builder.block.is_terminated:
                if method.result == "void":
                    self.builder.ret_void()
                else:
                    raise AssertionError("source sema admitted a missing return")
        return JavaCompilation(self.module, self.program.name, entry, tuple(self.functions))

    def expression(self, node):
        if node.constant is not None:
            return ir.Constant(ir.IntType(1) if node.dtype == "boolean" else self.i32, int(node.constant))
        kind = node.kind
        if kind == "name":
            return self.builder.load(self.slots[node.symbol])
        if kind == "call":
            arguments = [self.expression(argument) for argument in node.children]
            return self.builder.call(self.functions[node.symbol.token.text, node.symbol.descriptor], arguments)
        if kind == "group":
            return self.expression(node.children[0])
        if kind == "cast":
            value = self.expression(node.children[0])
            if node.value == "int":
                return value
            return emit_narrow(self.builder, {"byte": "i2b", "short": "i2s", "char": "i2c"}[node.value], value)
        if kind == "unary":
            value = self.expression(node.children[0])
            if node.value == "+":
                return value
            if node.value == "-":
                return self.builder.sub(ir.Constant(self.i32, 0), value)
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
            if node.value in ("==", "!=", "<", "<=", ">", ">="):
                return self.builder.icmp_signed(node.value, left, right)
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
                self.builder.store(self.expression(node.children[0]), self.slots[node.symbol])
            return
        if kind == "assign":
            slot = self.slots[node.symbol]
            if node.value == "=":
                value = self.expression(node.children[0])
            else:
                left = self.builder.load(slot)
                if node.value in ("++", "--"):
                    right, operation = ir.Constant(self.i32, 1), "add" if node.value == "++" else "sub"
                else:
                    right, operation = self.expression(node.children[0]), OPERATIONS[node.value[:-1]]
                value = emit_binary(self.builder, operation, left, right)
            self.builder.store(value, slot)
            return
        if kind == "expression":
            self.expression(node.children[0])
            return
        if kind == "return":
            if node.children:
                self.builder.ret(self.expression(node.children[0]))
            else:
                self.builder.ret_void()
            return
        if kind in ("break", "continue"):
            self.builder.branch(self.loops[-1][0 if kind == "continue" else 1])
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
            self.loops.append((update_block, done))
            self.statement(body)
            self.loops.pop()
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
