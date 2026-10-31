"""Typed Rust AST to the public PCC IR builder, shared by owned backends."""
from __future__ import annotations

from pcc.ir.compat import ir

from .semantics import Binding, Checker, NEVER
from .syntax import Expr, Function


def ir_type(ty: str):
    if ty == "()":
        return ir.VoidType()
    if ty == "bool":
        return ir.IntType(1)
    return ir.IntType(int(ty[1:]))


class Lowerer:
    def __init__(self, functions: list[Function], checker: Checker, module_name: str):
        self.module = ir.Module(name=module_name)
        self.functions = functions
        self.checker = checker
        self.declarations: dict[str, object] = {}
        self.symbols: dict[str, str] = {}
        self.slots: dict[int, object] = {}
        self.loops: list[tuple[object, object]] = []
        self.builder = None
        self.function = None

    def lower(self):
        for fn in self.functions:
            name = fn.token.text
            symbol = "__pcc_rust_" + name
            self.symbols[name] = symbol
            # Unit has no payload and is erased at this private calling boundary.
            signature = ir.FunctionType(ir_type(fn.result), [ir_type(ty) for _, ty, _ in fn.params if ty != "()"])
            self.declarations[name] = ir.Function(self.module, signature, name=symbol)
        for fn in self.functions:
            self.function = self.declarations[fn.token.text]
            self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
            self.slots = {}
            args = iter(self.function.args)
            for binding in self.checker.parameters[fn.token.text]:
                self.allocate(binding)
                if binding.ty != "()":
                    self.builder.store(next(args), self.slots[binding.serial])
            self.allocate_locals(fn.body)
            value = self.expression(fn.body)
            if not self.builder.block.is_terminated:
                if fn.result == "()":
                    self.builder.ret_void()
                else:
                    self.builder.ret(value)
        return self.module

    def allocate(self, binding: Binding) -> None:
        if binding.ty != "()":
            self.slots[binding.serial] = self.builder.alloca(ir_type(binding.ty), name=binding.name)

    def allocate_locals(self, expr: Expr) -> None:
        if expr.kind == "let":
            self.allocate(expr.binding)
        for child in expr.children:
            self.allocate_locals(child)

    def expression(self, expr: Expr):
        if self.builder.block.is_terminated:
            return None
        kind, children = expr.kind, expr.children
        if kind == "group":
            return self.expression(children[0])
        if kind == "integer":
            return ir.Constant(ir_type(expr.ty), expr.value)
        if kind == "bool":
            return ir.Constant(ir.IntType(1), int(expr.value))
        if kind == "unit":
            return None
        if kind == "name":
            if expr.ty == "()":
                return None
            return self.builder.load(self.slots[expr.binding.serial])
        if kind == "block":
            result = None
            for child in children:
                result = self.expression(child)
            return result if len(children) > expr.value else None
        if kind in {"discard", "statement"}:
            self.expression(children[0])
            return None
        if kind == "let":
            value = self.expression(children[0])
            if not self.builder.block.is_terminated and expr.binding.ty != "()":
                self.builder.store(value, self.slots[expr.binding.serial])
            return None
        if kind == "call":
            args = []
            for child in children[1:]:
                value = self.expression(child)
                if child.ty != "()":
                    args.append(value)
            if self.builder.block.is_terminated:
                return None
            return self.builder.call(self.declarations[expr.binding.token.text], args)
        if kind == "unary":
            value = self.expression(children[0])
            if self.builder.block.is_terminated:
                return None
            return self.builder.neg(value) if expr.value == "-" else self.builder.not_(value)
        if kind == "binary":
            if expr.value in {"&&", "||"}:
                return self.lazy_boolean(expr)
            left = self.expression(children[0])
            right = self.expression(children[1])
            if self.builder.block.is_terminated:
                return None
            return self.binary(expr.value, left, right, children[0].ty)
        if kind == "assign":
            # Rust assignment evaluates RHS before the assignee; only scalar
            # names are admitted, so compound place evaluation has no side effects.
            right = self.expression(children[1])
            if self.builder.block.is_terminated:
                return None
            slot = self.slots.get(children[0].binding.serial)
            if slot is not None:
                if expr.value != "=":
                    right = self.binary(expr.value[0], self.builder.load(slot), right, children[0].ty)
                self.builder.store(right, slot)
            return None
        if kind == "cast":
            value = self.expression(children[0])
            before, after = value.type.width, ir_type(expr.ty).width
            if before == after:
                return value
            if before > after:
                return self.builder.trunc(value, ir_type(expr.ty))
            return (self.builder.sext if children[0].ty.startswith("i") else self.builder.zext)(value, ir_type(expr.ty))
        if kind == "if":
            return self.conditional(expr)
        if kind == "while":
            condition_block = self.function.append_basic_block("while.cond")
            body_block = self.function.append_basic_block("while.body")
            done = self.function.append_basic_block("while.end")
            self.builder.branch(condition_block)
            self.builder.position_at_end(condition_block)
            condition = self.expression(children[0])
            if not self.builder.block.is_terminated:
                self.builder.cbranch(condition, body_block, done)
            self.builder.position_at_end(body_block)
            self.loops.append((condition_block, done))
            self.expression(children[1])
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(condition_block)
            self.builder.position_at_end(done)
            return None
        if kind == "return":
            value = self.expression(children[0]) if children else None
            if not self.builder.block.is_terminated:
                if children and children[0].ty != "()":
                    self.builder.ret(value)
                else:
                    self.builder.ret_void()
            return None
        if kind in {"break", "continue"}:
            self.builder.branch(self.loops[-1][1 if kind == "break" else 0])
            return None
        raise expr.token.error(f"unsupported lowering node {kind}")

    def binary(self, op: str, left, right, ty: str):
        operations = {"+": self.builder.add, "-": self.builder.sub, "*": self.builder.mul, "&": self.builder.and_, "|": self.builder.or_, "^": self.builder.xor}
        if op in operations:
            return operations[op](left, right)
        if op in {"/", "%"}:
            if op == "/":
                operation = self.builder.sdiv if ty.startswith("i") else self.builder.udiv
            else:
                operation = self.builder.srem if ty.startswith("i") else self.builder.urem
            return operation(left, right)
        comparison = self.builder.icmp_signed if ty.startswith("i") else self.builder.icmp_unsigned
        return comparison(op, left, right)

    def lazy_boolean(self, expr: Expr):
        left = self.expression(expr.children[0])
        if self.builder.block.is_terminated:
            return None
        origin = self.builder.block
        rhs = self.function.append_basic_block("bool.rhs")
        done = self.function.append_basic_block("bool.end")
        is_and = expr.value == "&&"
        self.builder.cbranch(left, rhs if is_and else done, done if is_and else rhs)
        self.builder.position_at_end(rhs)
        right = self.expression(expr.children[1])
        rhs_end = self.builder.block
        rhs_live = not rhs_end.is_terminated
        if rhs_live:
            self.builder.branch(done)
        self.builder.position_at_end(done)
        result = self.builder.phi(ir.IntType(1))
        result.add_incoming(ir.Constant(ir.IntType(1), 0 if is_and else 1), origin)
        if rhs_live:
            result.add_incoming(right, rhs_end)
        return result

    def conditional(self, expr: Expr):
        condition = self.expression(expr.children[0])
        if self.builder.block.is_terminated:
            return None
        yes = self.function.append_basic_block("if.then")
        no = self.function.append_basic_block("if.else")
        done = self.function.append_basic_block("if.end")
        self.builder.cbranch(condition, yes, no)
        incoming = []
        for block, branch in ((yes, expr.children[1]), (no, expr.children[2] if len(expr.children) == 3 else None)):
            self.builder.position_at_end(block)
            value = self.expression(branch) if branch is not None else None
            if not self.builder.block.is_terminated:
                incoming.append((value, self.builder.block))
                self.builder.branch(done)
        self.builder.position_at_end(done)
        if not incoming:
            self.builder.unreachable()
            return None
        if expr.ty in {"()", NEVER}:
            return None
        result = self.builder.phi(ir_type(expr.ty))
        for value, block in incoming:
            result.add_incoming(value, block)
        return result
