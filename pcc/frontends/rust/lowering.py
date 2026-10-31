"""Typed Rust AST to the public PCC IR builder, shared by owned backends."""
from __future__ import annotations

from pcc.ir.compat import ir

from .semantics import Binding, Checker, NEVER
from .syntax import Expr, Function
from .targets import RustTarget
from .types import SharedReference, TupleType


def ir_type(ty: str):
    if ty == "()":
        return ir.VoidType()
    if ty == "bool":
        return ir.IntType(1)
    return ir.IntType(int(ty[1:]))


class Lowerer:
    def __init__(self, functions: list[Function], checker: Checker, module_name: str, target: RustTarget):
        # ModuleID is textual IR, not an arbitrary-string transport. Encoding
        # labels prevents quotes/newlines from adding IR directives or globals.
        self.module = ir.Module(name="rust_" + module_name.encode("utf-8", "surrogatepass").hex())
        self.module.triple = target.triple
        self.module.data_layout = target.data_layout
        self.functions = functions
        self.checker = checker
        self.declarations: dict[str, object] = {}
        self.symbols: dict[str, str] = {}
        self.slots: dict[int, object] = {}
        self.temps: dict[int, object] = {}
        self.loops: list[tuple[object, object]] = []
        self.builder = None
        self.function = None

    def type(self, ty):
        if isinstance(ty, SharedReference):
            return self.type(ty.referent).as_pointer()
        if self.checker.aggregate(ty):
            # An implementation-private Rust storage choice, not rustc layout
            # or a promise that repr(Rust) has the C ABI. Foreign aggregates
            # remain rejected by the semantic boundary until qualified.
            return ir.LiteralStructType([
                ir.LiteralStructType([]) if item == "()" else self.type(item)
                for item in self.checker.fields(ty)
            ])
        if ty in self.checker.enums:
            enum = self.checker.enums[ty]
            tag = ir_type(enum.representation or "i64")
            if enum.payloads:
                # Private tagged storage, not a C/rustc enum layout. Every
                # field offset and alignment comes from the shared target IR
                # data layout. No host sizeof, union punning or pointer casts.
                return ir.LiteralStructType([tag] + [self.type(TupleType(fields)) for fields in enum.payloads.values()])
            return tag
        return ir_type(ty)

    def place_pointer(self, expr):
        if expr.kind == "group":
            return self.place_pointer(expr.children[0])
        if expr.kind == "name":
            return self.slots.get(expr.binding.serial)
        if expr.kind == "field":
            base = expr.children[0]
            pointer = self.expression(base) if isinstance(base.ty, SharedReference) else self.place_pointer(base)
            return self.builder.gep(pointer, [
                ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), expr.value)
            ])
        raise expr.token.error("unsupported place")

    def lower(self):
        for fn in self.functions:
            name = fn.token.text
            symbol = name if fn.body is None or fn.no_mangle else "__pcc_rust_" + name
            self.symbols[name] = symbol
            # Unit has no payload and is erased at this private calling boundary.
            signature = ir.FunctionType(self.type(fn.result), [self.type(ty) for _, ty, _ in fn.params if ty != "()"])
            self.declarations[name] = ir.Function(self.module, signature, name=symbol)
        for fn in self.functions:
            if fn.body is None:
                continue
            self.function = self.declarations[fn.token.text]
            self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
            self.slots = {}
            self.temps = {}
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
            self.slots[binding.serial] = self.builder.alloca(self.type(binding.ty), name=binding.name)

    def allocate_locals(self, expr: Expr) -> None:
        if expr.kind == "let":
            self.allocate(expr.binding)
        if expr.kind == "match":
            from .patterns import pattern_bindings
            for pattern in expr.value:
                for binding in pattern_bindings(pattern):
                    self.allocate(binding)
        if (expr.kind in {"tuple", "construct", "enum_construct"}
                or expr.kind == "variant" and self.checker.enums[expr.ty].payloads):
            self.temps[id(expr)] = self.builder.alloca(self.type(expr.ty), name="aggregate")
        for child in expr.children:
            self.allocate_locals(child)

    def expression(self, expr: Expr):
        if self.builder.block.is_terminated:
            return None
        kind, children = expr.kind, expr.children
        if kind in {"group", "unsafe"}:
            return self.expression(children[0])
        if kind == "enum_construct" or kind == "variant" and self.checker.enums[expr.ty].payloads:
            enum = self.checker.enums[expr.ty]
            variant = expr.value if kind == "enum_construct" else expr.binding
            values = [self.expression(child) for child in children]
            if self.builder.block.is_terminated:
                return None
            storage = self.temps[id(expr)]
            # Unused physical storage is defined zero bits, not initialized
            # Rust values. Only the tagged active payload may be inspected.
            self.builder.store(ir.Constant(self.type(expr.ty), None), storage)
            tag = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
            self.builder.store(ir.Constant(ir_type(enum.representation or "i64"), enum.discriminants[variant]), tag)
            if kind == "enum_construct":
                index = list(enum.payloads).index(variant) + 1
                payload = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), index)])
                for i, (value, child) in enumerate(zip(values, children)):
                    if child.ty != "()":
                        field = self.builder.gep(payload, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)])
                        self.builder.store(value, field)
            return self.builder.load(storage)
        if kind in {"integer", "variant"}:
            return ir.Constant(self.type(expr.ty), expr.value)
        if kind == "bool":
            return ir.Constant(ir.IntType(1), int(expr.value))
        if kind == "unit":
            return None
        if kind == "name":
            if expr.ty == "()":
                return None
            return self.builder.load(self.slots[expr.binding.serial])
        if kind in {"tuple", "construct"}:
            # Evaluate in SOURCE order, then place values at declaration-order
            # offsets. Reordered named initializers must not reorder effects.
            values = [self.expression(child) for child in children]
            if self.builder.block.is_terminated:
                return None
            pointer = self.temps[id(expr)]
            indices = expr.value if kind == "construct" else range(len(children))
            for index, value, child in zip(indices, values, children):
                if child.ty != "()":
                    field = self.builder.gep(pointer, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), index)])
                    self.builder.store(value, field)
            return self.builder.load(pointer)
        if kind == "field":
            if isinstance(children[0].ty, SharedReference):
                pointer = self.builder.gep(self.expression(children[0]), [
                    ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), expr.value)
                ])
                return self.builder.load(pointer)
            if self.checker.place(expr) is not None:
                return None if expr.ty == "()" else self.builder.load(self.place_pointer(expr))
            aggregate = self.expression(children[0])
            if expr.ty == "()" or self.builder.block.is_terminated:
                return None
            return self.builder.extract_value(aggregate, expr.value)
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
            # Rust assignment evaluates RHS before the assignee. Supported
            # field paths contain no effectful index/dereference operations.
            right = self.expression(children[1])
            if self.builder.block.is_terminated:
                return None
            slot = None if children[0].ty == "()" else self.place_pointer(children[0])
            if slot is not None:
                if expr.value != "=":
                    right = self.binary(expr.value[0], self.builder.load(slot), right, children[0].ty)
                self.builder.store(right, slot)
            return None
        if kind == "cast":
            value = self.expression(children[0])
            before, after = value.type.width, self.type(expr.ty).width
            if before == after:
                return value
            if before > after:
                return self.builder.trunc(value, self.type(expr.ty))
            source_ty = children[0].ty
            if source_ty in self.checker.enums:
                source_ty = self.checker.enums[source_ty].representation or "i64"
            return (self.builder.sext if source_ty.startswith("i") else self.builder.zext)(value, self.type(expr.ty))
        if kind == "match":
            return self.match(expr)
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
        result = self.builder.phi(self.type(expr.ty))
        for value, block in incoming:
            result.add_incoming(value, block)
        return result

    def payload_value(self, pattern, value):
        enum = self.checker.enums[pattern.ty]
        index = list(enum.payloads).index(pattern.variant) + 1
        return self.builder.extract_value(value, index)

    def pattern_test(self, pattern, value):
        true = ir.Constant(ir.IntType(1), 1)
        if pattern.kind in {"wildcard", "unit", "binding"}:
            return true
        if pattern.kind == "literal":
            return self.builder.icmp_unsigned("==", value, ir.Constant(self.type(pattern.ty), int(pattern.value)))
        if pattern.kind in {"variant", "payload"}:
            enum = self.checker.enums[pattern.ty]
            tag = self.builder.extract_value(value, 0) if enum.payloads else value
            test = self.builder.icmp_unsigned("==", tag, ir.Constant(tag.type, pattern.value))
            if pattern.kind == "variant":
                return test
            # Do not inspect any inactive payload, including a nested enum's
            # tag. Branch before extraction, then merge a plain bool predicate.
            origin = self.builder.block
            active = self.function.append_basic_block("pattern.payload")
            done = self.function.append_basic_block("pattern.end")
            self.builder.cbranch(test, active, done)
            self.builder.position_at_end(active)
            result = self.pattern_fields_test(pattern.children, self.payload_value(pattern, value))
            end = self.builder.block
            self.builder.branch(done)
            self.builder.position_at_end(done)
            predicate = self.builder.phi(ir.IntType(1))
            predicate.add_incoming(ir.Constant(ir.IntType(1), 0), origin)
            predicate.add_incoming(result, end)
            return predicate
        if pattern.kind == "or":
            result = ir.Constant(ir.IntType(1), 0)
            for option in pattern.children:
                result = self.builder.or_(result, self.pattern_test(option, value))
            return result
        return self.pattern_fields_test(pattern.children, value)

    def pattern_fields_test(self, children, value):
        result = ir.Constant(ir.IntType(1), 1)
        for index, child in enumerate(children):
            if child.kind in {"wildcard", "unit", "binding"}:
                continue
            field = self.builder.extract_value(value, index)
            result = self.builder.and_(result, self.pattern_test(child, field))
        return result

    def bind_pattern(self, pattern, value):
        from .patterns import contains_binding

        if not contains_binding(pattern):
            return
        if pattern.kind == "binding":
            if pattern.ty != "()":
                self.builder.store(value, self.slots[pattern.binding.serial])
        elif pattern.kind in {"tuple", "payload"}:
            if pattern.kind == "payload":
                value = self.payload_value(pattern, value)
            for index, child in enumerate(pattern.children):
                if contains_binding(child):
                    field = None if child.ty == "()" else self.builder.extract_value(value, index)
                    self.bind_pattern(child, field)

    def match(self, expr):
        # Exactly one scrutinee evaluation; test arms in source order. Patterns
        # consume only the bound active payload fields. Value-expression
        # operands retain ordinary Rust moves, checked before lowering.
        value = self.expression(expr.children[0])
        if self.builder.block.is_terminated:
            return None
        done = self.function.append_basic_block("match.end")
        incoming = []
        for pattern, arm in zip(expr.value, expr.children[1:]):
            selected = self.function.append_basic_block("match.arm")
            following = self.function.append_basic_block("match.next")
            self.builder.cbranch(self.pattern_test(pattern, value), selected, following)
            self.builder.position_at_end(selected)
            self.bind_pattern(pattern, value)
            result = self.expression(arm)
            if not self.builder.block.is_terminated:
                incoming.append((result, self.builder.block))
                self.builder.branch(done)
            self.builder.position_at_end(following)
        # Coverage is established over the typed constructor matrix; no panic
        # or external runtime is smuggled in for the impossible residual edge.
        self.builder.unreachable()
        self.builder.position_at_end(done)
        if not incoming:
            self.builder.unreachable()
            return None
        if expr.ty in {"()", NEVER}:
            return None
        result = self.builder.phi(self.type(expr.ty))
        for item, block in incoming:
            result.add_incoming(item, block)
        return result
