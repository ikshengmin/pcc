"""Typed Rust AST to the public PCC IR builder, shared by owned backends."""
from __future__ import annotations

from dataclasses import dataclass

from pcc.ir.compat import ir

from .iteration import OwnedArrayIteration, StandardIterator
from .semantics import Binding, Checker, NEVER
from .syntax import Expr, Function
from .targets import RustTarget
from .types import STATIC_STR, FixedArrayType, SharedReference, TupleType, integer_bits


def ir_type(ty: str, pointer_bits=None):
    if ty == "()":
        return ir.VoidType()
    if ty == "bool":
        return ir.IntType(1)
    return ir.IntType(integer_bits(ty, pointer_bits))


@dataclass(frozen=True)
class LoopTargets:
    continue_block: object
    break_block: object
    continue_depth: int
    break_depth: int


@dataclass(frozen=True)
class ArrayIterationScope:
    plan: OwnedArrayIteration
    # A cursor denotes the iterator-owned remaining initialized range. None
    # denotes the per-iteration binding/temporary scope with empty destruction.
    cursor: object = None


class Lowerer:
    def __init__(self, functions: list[Function], checker: Checker, module_name: str, target: RustTarget):
        # ModuleID is textual IR, not an arbitrary-string transport. Encoding
        # labels prevents quotes/newlines from adding IR directives or globals.
        self.module = ir.Module(name="rust_" + module_name.encode("utf-8", "surrogatepass").hex())
        self.target = target
        self.module.triple = target.triple
        self.module.data_layout = target.data_layout
        self.functions = [fn for fn in functions if not fn.type_parameters]
        self.checker = checker
        self.declarations: dict[str, object] = {}
        self.symbols: dict[str, str] = {}
        self.slots: dict[int, object] = {}
        self.temps: dict[int, object] = {}
        self.loops: list[LoopTargets] = []
        self.iteration_scopes: list[ArrayIterationScope] = []
        self.builder = None
        self.function = None

    def type(self, ty):
        if ty == STATIC_STR:
            # (data pointer, byte length): two integer words, as rustc passes
            # and returns a `&str` scalar pair on these targets.
            return ir.LiteralStructType([ir.IntType(8).as_pointer(), ir.IntType(self.target.pointer_bits)])
        if isinstance(ty, FixedArrayType):
            element = ir.LiteralStructType([]) if ty.element == "()" else self.type(ty.element)
            return ir.ArrayType(element, ty.length)
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
        return ir_type(ty, self.target.pointer_bits)

    def static_global(self, item):
        """The one global place of a static item, initialized with its value."""
        cache = self.__dict__.setdefault('static_globals', {})
        if id(item) not in cache:
            ty = self.type(item.ty)
            storage = ir.GlobalVariable(self.module, ty, f"__pcc_rust_static_{len(cache)}")
            storage.linkage = "internal"
            storage.global_constant = not item.mutable
            if item.ty == STATIC_STR:
                self.static_str(item.value, load=False)
                storage.initializer = self.static_str_constant(item.value)
            else:
                storage.initializer = ir.Constant(ty, int(item.value))
            cache[id(item)] = storage
        return cache[id(item)]

    def static_str_constant(self, text):
        """The (pointer, length) aggregate constant of literal text."""
        return self.static_strings_descriptor_value[text.encode('utf-8')]

    def static_str(self, text, *, load=True):
        """A `&'static str` value: private readonly UTF-8 bytes and a
        private (pointer, length) descriptor, loaded as one value. The
        backend relocates pointers in static aggregate initializers, not in
        inline aggregate constants. An empty string still points at a byte:
        a `&str` pointer is never null."""
        data = text.encode('utf-8')
        cache = self.__dict__.setdefault('static_strings', {})
        if data not in cache:
            serial = len(cache)
            storage = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), max(1, len(data))),
                                        f"__pcc_rust_str_{serial}")
            storage.linkage = "private"
            storage.global_constant = True
            storage.initializer = ir.Constant(storage.value_type, bytearray(data or b"\0"))
            pointer = storage.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
            descriptor = ir.GlobalVariable(self.module, self.type(STATIC_STR), f"__pcc_rust_str_value_{serial}")
            descriptor.linkage = "private"
            descriptor.global_constant = True
            descriptor.initializer = ir.Constant(self.type(STATIC_STR), [
                pointer, ir.Constant(ir.IntType(self.target.pointer_bits), len(data))])
            cache[data] = descriptor
            self.__dict__.setdefault('static_strings_descriptor_value', {})[data] = descriptor.initializer
        return self.builder.load(cache[data]) if load else cache[data]

    def place_pointer(self, expr):
        if expr.kind == "group":
            return self.place_pointer(expr.children[0])
        if expr.kind == "deref":
            return self.expression(expr.children[0])
        if expr.kind == "name":
            return self.slots.get(expr.binding.serial)
        if expr.kind == "static":
            return self.static_global(expr.binding)
        if expr.kind == "index":
            base = expr.children[0]
            if isinstance(base.ty, SharedReference):
                pointer = self.expression(base)
            elif self.checker.place(base) is not None:
                pointer = self.place_pointer(base)
            else:
                value = self.expression(base)
                if self.builder.block.is_terminated:
                    return None
                pointer = self.temps[("index", id(expr))]
                self.builder.store(value, pointer)
            index = self.expression(expr.children[1])
            if self.builder.block.is_terminated:
                return None
            return self.builder.gep(pointer, [ir.Constant(ir.IntType(32), 0), index])
        if expr.kind == "field":
            base = expr.children[0]
            pointer = self.expression(base) if isinstance(base.ty, SharedReference) else self.place_pointer(base)
            return self.builder.gep(pointer, [
                ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), expr.value)
            ])
        raise expr.token.error("unsupported place")

    def lower(self):
        for fn in self.functions:
            name = fn.identity
            symbol = name if fn.body is None or fn.no_mangle else "__pcc_rust_" + name
            self.symbols[fn.export_name] = symbol
            # Unit has no payload and is erased at this private calling boundary.
            signature = ir.FunctionType(self.type(fn.result), [self.type(ty) for _, ty, _ in fn.params if ty != "()"])
            self.declarations[name] = ir.Function(self.module, signature, name=symbol)
        for fn in self.functions:
            if fn.body is None:
                continue
            self.function = self.declarations[fn.identity]
            self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
            self.slots = {}
            self.temps = {}
            self.loops = []
            self.iteration_scopes = []
            args = iter(self.function.args)
            for binding in self.checker.parameters[fn.identity]:
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
        if expr.kind == "for":
            if expr.binding is not None:
                self.allocate(expr.binding)
            self.temps[("for_array", id(expr))] = self.builder.alloca(self.type(expr.value.array_type), name="for.array")
            self.temps[("for_cursor", id(expr))] = self.builder.alloca(self.type("usize"), name="for.cursor")
        if expr.kind in {"for_range", "for_range_inclusive"}:
            if expr.binding is not None:
                self.allocate(expr.binding)
            bound = self.type(expr.children[0].ty)
            self.temps[("range_cursor", id(expr))] = self.builder.alloca(bound, name="range.cursor")
            self.temps[("range_end", id(expr))] = self.builder.alloca(bound, name="range.end")
            if expr.kind == "for_range_inclusive":
                self.temps[("range_exhausted", id(expr))] = self.builder.alloca(ir.IntType(1), name="range.exhausted")
        if expr.kind in {"match", "while_let", "pattern_let"}:
            from .patterns import pattern_bindings
            patterns = (expr.value if expr.kind == "match" else (expr.value[0],) if expr.kind == "pattern_let"
                        else (expr.value,))
            for pattern in patterns:
                for binding in pattern_bindings(pattern):
                    self.allocate(binding)
        if (expr.kind in {"tuple", "construct", "enum_construct", "array", "array_repeat"}
                or expr.kind == "variant" and self.checker.enums[expr.ty].payloads):
            self.temps[id(expr)] = self.builder.alloca(self.type(expr.ty), name="aggregate")
        if expr.kind == "index" and not isinstance(expr.children[0].ty, SharedReference) and self.checker.place(expr.children[0]) is None:
            self.temps[("index", id(expr))] = self.builder.alloca(self.type(expr.children[0].ty), name="indexed_array")
        if expr.kind == "derived_eq":
            # Array elements are compared through entry-block slots: the IR
            # builder types `extractvalue` only for struct aggregates.
            for array_type in self.array_types(expr.children[0].ty):
                key = ("eq_array", str(array_type))
                if key not in self.temps:
                    self.temps[key] = (self.builder.alloca(self.type(array_type), name="eq.left"),
                                       self.builder.alloca(self.type(array_type), name="eq.right"))
        # Direct-call callee syntax is not a value expression. A qualified
        # method path may share the parser's enum-path shape but has no value
        # type/storage. Method receiver values are already the first actual
        # operand, so visiting the callee would also traverse them twice.
        operands = expr.children[1:] if expr.kind == "call" else expr.children
        for child in operands:
            self.allocate_locals(child)

    def expression(self, expr: Expr):
        if self.builder.block.is_terminated:
            return None
        kind, children = expr.kind, expr.children
        if kind in {"group", "unsafe", "reference_coerce"}:
            return self.expression(children[0])
        if kind in {"array", "array_repeat"}:
            values = [self.expression(child) for child in children]
            if self.builder.block.is_terminated:
                return None
            storage = self.temps[id(expr)]
            if expr.ty.length == 0 or expr.ty.element == "()":
                self.builder.store(ir.Constant(self.type(expr.ty), None), storage)
            else:
                if kind == "array_repeat":
                    values = values * expr.ty.length
                for index, value in enumerate(values):
                    address = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(self.target.pointer_bits), index)])
                    self.builder.store(value, address)
            return self.builder.load(storage)
        if kind == "clone":
            # A derived or primitive Clone of heap-free data is the value itself.
            return self.expression(children[0])
        if kind == "derived_eq":
            left = self.expression(children[0])
            right = self.expression(children[1])
            if self.builder.block.is_terminated:
                return None
            equal = self.equal_values(left, right, children[0].ty)
            return equal if expr.value == "==" else self.builder.xor(equal, ir.Constant(ir.IntType(1), 1))
        if kind == "array_len":
            base = children[0]
            if self.checker.place(base) is not None:
                self.place_pointer(base)
            else:
                self.expression(base)
            if self.builder.block.is_terminated:
                return None
            return ir.Constant(self.type("usize"), expr.value)
        if kind == "index":
            pointer = self.place_pointer(expr)
            if self.builder.block.is_terminated or expr.ty == "()":
                return None
            return self.builder.load(pointer)
        if kind == "borrow":
            return self.place_pointer(children[0])
        if kind == "deref":
            return self.builder.load(self.expression(children[0]))
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
                # A struct-like variant literal keeps source evaluation order
                # and stores each value at its declaration position.
                positions = expr.binding if isinstance(expr.binding, tuple) else range(len(children))
                for i, value, child in zip(positions, values, children):
                    if child.ty != "()":
                        field = self.builder.gep(payload, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)])
                        self.builder.store(value, field)
            return self.builder.load(storage)
        if kind in {"integer", "variant"}:
            return ir.Constant(self.type(expr.ty), expr.value)
        if kind == "bool":
            return ir.Constant(ir.IntType(1), int(expr.value))
        if kind == "static":
            return self.builder.load(self.static_global(expr.binding))
        if kind == "string" or kind == "const" and expr.ty == STATIC_STR:
            return self.static_str(expr.value if kind == "string" else expr.binding.value)
        if kind == "const":
            # A const item denotes its evaluated value at each use.
            value = expr.binding.value
            return ir.Constant(ir.IntType(1) if expr.ty == "bool" else self.type(expr.ty), int(value))
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
            if expr.ty == "()":
                if self.checker.place(expr) is None or isinstance(children[0].ty, SharedReference):
                    self.expression(children[0])
                return None
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
        if kind == "wildcard_let":
            # Do not read a place merely to discard it. Address computation
            # still evaluates a certified reference operand where required.
            if self.checker.place(children[0]) is not None:
                self.place_pointer(children[0])
            else:
                self.expression(children[0])
            return None
        if kind == "let":
            value = self.expression(children[0])
            if not self.builder.block.is_terminated and expr.binding.ty != "()":
                self.builder.store(value, self.slots[expr.binding.serial])
            return None
        if kind == "pattern_let":
            value = self.expression(children[0])
            if not self.builder.block.is_terminated:
                self.bind_pattern(expr.value[0], value)
            return None
        if kind == "call":
            args = []
            for child in children[1:]:
                value = self.expression(child)
                if child.ty != "()":
                    args.append(value)
            if self.builder.block.is_terminated:
                return None
            return self.builder.call(self.declarations[expr.binding.identity], args)
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
            # Rust assignment evaluates RHS before the assignee, including a
            # dereference whose reference slot may be rebound by that RHS.
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
        if kind == "for":
            return self.array_iteration(expr)
        if kind in {"for_range", "for_range_inclusive"}:
            return self.range_iteration(expr)
        if kind == "loop":
            body_block = self.function.append_basic_block("loop.body")
            done = self.function.append_basic_block("loop.end")
            self.builder.branch(body_block)
            self.builder.position_at_end(body_block)
            depth = len(self.iteration_scopes)
            self.loops.append(LoopTargets(body_block, done, depth, depth))
            self.expression(children[0])
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(body_block)
            self.builder.position_at_end(done)
            if expr.ty == NEVER:
                self.builder.unreachable()
            return None
        if kind == "while_let":
            condition_block = self.function.append_basic_block("while.let.condition")
            body_block = self.function.append_basic_block("while.let.body")
            done = self.function.append_basic_block("while.let.end")
            self.builder.branch(condition_block)
            self.builder.position_at_end(condition_block)
            value = self.expression(children[0])
            if self.builder.block.is_terminated:
                # Semantic admission rejects a directly diverging scrutinee.
                # Keep composed divergent paths closed rather than extracting
                # a payload from a value which was never produced.
                self.builder.position_at_end(body_block)
                self.builder.unreachable()
                self.builder.position_at_end(done)
                self.builder.unreachable()
                return None
            self.builder.cbranch(self.pattern_test(expr.value, value), body_block, done)
            self.builder.position_at_end(body_block)
            self.bind_pattern(expr.value, value)
            depth = len(self.iteration_scopes)
            self.loops.append(LoopTargets(condition_block, done, depth, depth))
            self.expression(children[1])
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(condition_block)
            self.builder.position_at_end(done)
            return None
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
            depth = len(self.iteration_scopes)
            self.loops.append(LoopTargets(condition_block, done, depth, depth))
            self.expression(children[1])
            self.loops.pop()
            if not self.builder.block.is_terminated:
                self.builder.branch(condition_block)
            self.builder.position_at_end(done)
            return None
        if kind == "return":
            value = self.expression(children[0]) if children else None
            if not self.builder.block.is_terminated:
                self.finish_iteration_scopes(0)
                if children and children[0].ty != "()":
                    self.builder.ret(value)
                else:
                    self.builder.ret_void()
            return None
        if kind in {"break", "continue"}:
            target = self.loops[-1]
            self.finish_iteration_scopes(target.break_depth if kind == "break" else target.continue_depth)
            self.builder.branch(target.break_block if kind == "break" else target.continue_block)
            return None
        raise expr.token.error(f"unsupported lowering node {kind}")

    def finish_iteration_scopes(self, depth):
        """Retire only the scopes actually crossed by this control-flow edge.

        No destructor call is omitted by guessing: semantic admission proved
        the full concrete element graph has empty destruction. Unknown/Drop
        types cannot create these scopes. The binding scope has no remaining
        side effects; the iterator's live suffix is retired by its cursor.
        """
        for scope in reversed(self.iteration_scopes[depth:]):
            assert scope.plan.identity is StandardIterator.OWNED_ARRAY
            if scope.cursor is not None:
                self.builder.store(ir.Constant(self.type("usize"), scope.plan.array_type.length), scope.cursor)

    def range_iteration(self, expr):
        """Range::next / RangeInclusive::next over one integer type.

        `a..b` yields a, a+1, ..., b-1; the increment follows `cursor < end`,
        so it cannot overflow. `a..=b` yields b last through an exhausted flag
        instead of incrementing past it (no overflow at the type maximum).
        """
        start = self.expression(expr.children[0])
        if self.builder.block.is_terminated:
            return None
        end = self.expression(expr.children[1])
        if self.builder.block.is_terminated:
            return None
        ty = expr.children[0].ty
        compare = self.builder.icmp_signed if ty.startswith("i") else self.builder.icmp_unsigned
        cursor = self.temps[("range_cursor", id(expr))]
        bound = self.temps[("range_end", id(expr))]
        self.builder.store(start, cursor)
        self.builder.store(end, bound)
        inclusive = expr.kind == "for_range_inclusive"
        if inclusive:
            exhausted = self.temps[("range_exhausted", id(expr))]
            self.builder.store(ir.Constant(ir.IntType(1), 0), exhausted)
        condition = self.function.append_basic_block("range.next")
        body = self.function.append_basic_block("range.body")
        done = self.function.append_basic_block("range.end")
        self.builder.branch(condition)
        self.builder.position_at_end(condition)
        current = self.builder.load(cursor)
        limit = self.builder.load(bound)
        if inclusive:
            has_next = self.builder.and_(self.builder.not_(self.builder.load(exhausted)),
                                         compare("<=", current, limit))
        else:
            has_next = compare("<", current, limit)
        self.builder.cbranch(has_next, body, done)
        self.builder.position_at_end(body)
        # next() yields the current value and advances before the body runs.
        if inclusive:
            self.builder.store(compare("==", current, limit), exhausted)
            below = compare("<", current, limit)
            one = ir.Constant(current.type, 1)
            self.builder.store(self.builder.select(below, self.builder.add(current, one), current), cursor)
        else:
            self.builder.store(self.builder.add(current, ir.Constant(current.type, 1)), cursor)
        if expr.binding is not None and expr.binding.ty != "()":
            self.builder.store(current, self.slots[expr.binding.serial])
        depth = len(self.iteration_scopes)
        self.loops.append(LoopTargets(condition, done, depth, depth))
        self.expression(expr.children[2])
        self.loops.pop()
        if not self.builder.block.is_terminated:
            self.builder.branch(condition)
        self.builder.position_at_end(done)
        return None

    def array_iteration(self, expr):
        plan = expr.value
        assert isinstance(plan, OwnedArrayIteration) and plan.identity is StandardIterator.OWNED_ARRAY
        value = self.expression(expr.children[0])
        if self.builder.block.is_terminated:
            return None
        # Even an empty iterator must evaluate its source, including repeat
        # operands and calls. Its proven-empty destruction then has no work.
        if plan.array_type.length == 0:
            return None
        storage = self.temps[("for_array", id(expr))]
        cursor = self.temps[("for_cursor", id(expr))]
        self.builder.store(value, storage)  # Owned value snapshot, never alias source storage.
        index_type = self.type("usize")
        self.builder.store(ir.Constant(index_type, 0), cursor)
        depth = len(self.iteration_scopes)
        self.iteration_scopes.append(ArrayIterationScope(plan, cursor))
        condition = self.function.append_basic_block("for.next")
        body = self.function.append_basic_block("for.body")
        done = self.function.append_basic_block("for.end")
        self.builder.branch(condition)
        self.builder.position_at_end(condition)
        index = self.builder.load(cursor)
        has_next = self.builder.icmp_unsigned("<", index, ir.Constant(index_type, plan.array_type.length))
        self.builder.cbranch(has_next, body, done)
        self.builder.position_at_end(body)
        # The iterator consumes an element before the body. This internal
        # index is proven in bounds by the dominating next test; no general
        # source indexing or panic check is elided. N <= 4096 prevents overflow.
        self.builder.store(self.builder.add(index, ir.Constant(index_type, 1)), cursor)
        if expr.binding is not None and expr.binding.ty != "()":
            pointer = self.builder.gep(storage, [ir.Constant(ir.IntType(32), 0), index])
            self.builder.store(self.builder.load(pointer), self.slots[expr.binding.serial])
        self.iteration_scopes.append(ArrayIterationScope(plan))
        self.loops.append(LoopTargets(condition, done, depth + 1, depth))
        self.expression(expr.children[1])
        self.loops.pop()
        if not self.builder.block.is_terminated:
            self.finish_iteration_scopes(depth + 1)
            self.builder.branch(condition)
        self.iteration_scopes.pop()
        self.builder.position_at_end(done)
        self.finish_iteration_scopes(depth)
        self.iteration_scopes.pop()
        return None

    def array_types(self, ty, found=None):
        """Every array type reachable inside a value of type `ty`."""
        found = [] if found is None else found
        if isinstance(ty, FixedArrayType):
            if ty not in found:
                found.append(ty)
            self.array_types(ty.element, found)
        elif isinstance(ty, TupleType) or ty in self.checker.records:
            for item in self.checker.fields(ty):
                self.array_types(item, found)
        elif ty in self.checker.enums:
            for fields in self.checker.enums[ty].payloads.values():
                for item in fields:
                    self.array_types(item, found)
        return found

    def equal_values(self, left, right, ty):
        """Derived PartialEq: fields in declaration order; for an enum, the
        variant first and then only the active payload, never an inactive
        one (the same rule as pattern tests)."""
        true = ir.Constant(ir.IntType(1), 1)
        if ty == "()" or left is None:
            return true
        if isinstance(ty, FixedArrayType):
            left_slot, right_slot = self.temps[("eq_array", str(ty))]
            self.builder.store(left, left_slot)
            self.builder.store(right, right_slot)
            result = true
            for index in range(ty.length):
                position = [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), index)]
                element_equal = self.equal_values(self.builder.load(self.builder.gep(left_slot, position)),
                                                  self.builder.load(self.builder.gep(right_slot, position)),
                                                  ty.element)
                result = self.builder.and_(result, element_equal)
            return result
        if isinstance(ty, TupleType) or ty in self.checker.records:
            result = true
            for index, item in enumerate(self.checker.fields(ty)):
                if item == "()":
                    continue
                field_equal = self.equal_values(self.builder.extract_value(left, index),
                                                self.builder.extract_value(right, index), item)
                result = self.builder.and_(result, field_equal)
            return result
        if ty in self.checker.enums:
            enum = self.checker.enums[ty]
            if not enum.payloads:
                return self.builder.icmp_unsigned("==", left, right)
            tag = self.builder.extract_value(left, 0)
            same = self.builder.icmp_unsigned("==", tag, self.builder.extract_value(right, 0))
            origin = self.builder.block
            done = self.function.append_basic_block("eq.end")
            check = self.function.append_basic_block("eq.payload")
            self.builder.cbranch(same, check, done)
            incoming = [(ir.Constant(ir.IntType(1), 0), origin)]
            self.builder.position_at_end(check)
            for index, (variant, fields) in enumerate(enum.payloads.items(), start=1):
                active = self.function.append_basic_block("eq.variant")
                following = self.function.append_basic_block("eq.next")
                self.builder.cbranch(self.builder.icmp_unsigned(
                    "==", tag, ir.Constant(tag.type, enum.discriminants[variant])), active, following)
                self.builder.position_at_end(active)
                result = self.equal_values(self.builder.extract_value(left, index),
                                           self.builder.extract_value(right, index), TupleType(fields))
                incoming.append((result, self.builder.block))
                self.builder.branch(done)
                self.builder.position_at_end(following)
            incoming.append((true, self.builder.block))  # equal unit variants
            self.builder.branch(done)
            self.builder.position_at_end(done)
            phi = self.builder.phi(ir.IntType(1))
            for value, block in incoming:
                phi.add_incoming(value, block)
            return phi
        return self.builder.icmp_unsigned("==", left, right)

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
        if pattern.kind == "at":
            return self.pattern_test(pattern.children[0], value)
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
        if pattern.kind in {"binding", "at"}:
            if pattern.ty != "()":
                self.builder.store(value, self.slots[pattern.binding.serial])
            if pattern.kind == "at":
                # The subpattern binds parts of the same (unchanged) value.
                self.bind_pattern(pattern.children[0], value)
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
