"""Name resolution, monomorphic scalar inference, and Rust type checking."""
from __future__ import annotations

from contextlib import contextmanager

import re
from dataclasses import dataclass

from .syntax import ConstItem, Enumeration, Expr, Function, Record, RustError, StaticItem, Token
from .types import (
    INTEGER_TYPES, RUST_CHAR, RUST_U8, STATIC_STR, STR_POINTEE, FixedArrayType, SharedReference, TupleType, TypeApplication, TypeParameter, integer_bits,
    is_borrowed_str, is_static_str,
)

INTEGERS = INTEGER_TYPES
SHIFTS = frozenset({"<<", ">>"})
STANDARD_MACRO_NODES = frozenset({"debug_assert", "panic", "assert_compare", "format_args", "format",
                                  "write_fmt", "vec_macro"})
FFI_INTEGERS = frozenset({"i32", "i64", "u32", "u64"})
NEVER = "!"
# The value of a borrow node for a two-phase mutable borrow (truthy: mutable).
TWO_PHASE = "two_phase"


@dataclass(eq=False)
class InferredInteger:
    bound: object = None


@dataclass(eq=False)
class InferredType:
    token: Token
    bound: object = None
    description: str = "empty array element"


@dataclass
class Binding:
    name: str
    ty: object
    mutable: bool
    serial: int
    parameter: bool = False


def resolve(ty: object) -> object:
    if isinstance(ty, FixedArrayType):
        return FixedArrayType(resolve(ty.element), ty.length)
    if isinstance(ty, InferredType):
        if ty.bound is None:
            raise ty.token.error(f'cannot infer {ty.description} type; provide a type annotation')
        ty.bound = resolve(ty.bound)
        return ty.bound
    if isinstance(ty, SharedReference):
        return SharedReference(resolve(ty.referent), ty.mutable, ty.lifetime)
    if isinstance(ty, TypeApplication):
        return TypeApplication(ty.name, tuple(resolve(item) for item in ty.arguments))
    if isinstance(ty, TypeParameter):
        return ty
    if isinstance(ty, TupleType):
        return TupleType(tuple(resolve(item) for item in ty.elements))
    if isinstance(ty, InferredInteger):
        if ty.bound is None:
            ty.bound = "i32"
        ty.bound = resolve(ty.bound)
        return ty.bound
    return str(ty)


def _root(ty: object) -> object:
    while isinstance(ty, (InferredInteger, InferredType)) and ty.bound is not None:
        ty = ty.bound
    return ty


def unify(left: object, right: object, token: Token) -> object:
    left, right = _root(left), _root(right)
    if left == NEVER:
        return right
    if right == NEVER:
        return left
    if left is right or left == right:
        return left
    if isinstance(left, InferredType):
        from .generics import children
        pending, seen = [right], set()
        while pending:
            item = _root(pending.pop())
            if item is left:
                raise token.error(f"infinite inferred {left.description} type")
            if id(item) in seen:
                continue
            seen.add(id(item))
            pending.extend(children(item))
        left.bound = right
        return right
    if isinstance(right, InferredType):
        return unify(right, left, token)
    if isinstance(left, FixedArrayType) and isinstance(right, FixedArrayType) and left.length == right.length:
        return FixedArrayType(unify(left.element, right.element, token), left.length)
    if isinstance(left, TupleType) and isinstance(right, TupleType) and len(left.elements) == len(right.elements):
        return TupleType(tuple(unify(a, b, token) for a, b in zip(left.elements, right.elements)))
    if isinstance(left, TypeApplication) and isinstance(right, TypeApplication) and left.name == right.name and len(left.arguments) == len(right.arguments):
        return TypeApplication(left.name, tuple(unify(a, b, token) for a, b in zip(left.arguments, right.arguments)))
    if isinstance(left, SharedReference) and isinstance(right, SharedReference) and left.mutable == right.mutable:
        return SharedReference(unify(left.referent, right.referent, token), left.mutable)
    if isinstance(left, InferredInteger):
        if isinstance(right, InferredInteger) or isinstance(right, str) and right in INTEGERS:
            left.bound = right
            return right
    elif isinstance(right, InferredInteger):
        return unify(right, left, token)
    raise token.error(f"type mismatch: expected {left}, found {right}")


def integer_literal(expr: Expr) -> tuple[int, object]:
    match = re.fullmatch(r"(0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*)(i32|i64|u8|u32|u64|isize|usize)?", str(expr.value))
    if match is None:
        raise expr.token.error("invalid or unsupported integer literal (supported suffixes: i32, i64, u8, u32, u64, isize, usize)")
    raw = match.group(1).replace("_", "")
    try:
        value = int(raw, 0 if raw.startswith(("0x", "0o", "0b")) else 10)
    except ValueError as exc:
        raise expr.token.error("invalid integer literal") from exc
    suffix = match.group(2)
    return value, RUST_U8 if suffix == "u8" else (suffix or InferredInteger())


_TRAIT_SPELLING = {"Display": "", "Debug": "?", "LowerHex": "x", "UpperHex": "X", "Octal": "o", "Binary": "b",
                   "LowerExp": "e", "UpperExp": "E", "Pointer": "p"}


def local_key(name: str, token=None) -> str:
    """A local's scope key: its name, qualified by the macro_rules! expansion
    mark of the token naming it (mixed-site hygiene; items ignore marks)."""
    mark = getattr(token, 'hygiene', 0) if token is not None else 0
    return name if not mark else f"{name}\x00{mark}"


def bounds(ty: str, pointer_bits=None) -> tuple[int, int]:
    bits = integer_bits(ty, pointer_bits)
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if ty[0] == "i" else (0, (1 << bits) - 1)


def cast_constant(value: int, ty: str, pointer_bits=None) -> int:
    """Rust integer casts truncate modulo 2**width, then interpret signedness."""
    bits = integer_bits(ty, pointer_bits)
    value &= (1 << bits) - 1
    if ty.startswith("i") and value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


def shift_overflows(amount: int, amount_ty: str, ty: str, pointer_bits=None) -> bool:
    """rustc's KnownPanicsLint (and const evaluation): a shift overflows when
    the amount's bit pattern, read unsigned in its own type, is at least the
    shifted type's width. A negative amount is therefore always an overflow."""
    pattern = amount & ((1 << integer_bits(amount_ty, pointer_bits)) - 1)
    return pattern >= integer_bits(ty, pointer_bits)


def shift_value(op: str, value: int, amount: int, ty: str, pointer_bits=None) -> int:
    """`value << amount` / `value >> amount` in `ty` for an in-range amount:
    bits shifted out are lost (not an overflow), `>>` is arithmetic for a
    signed type and logical for an unsigned one (its value is nonnegative)."""
    return cast_constant(value << amount if op == "<<" else value >> amount, ty, pointer_bits)


class Checker:
    def __init__(self, functions: list[Function], records: list[Record] = (), enums: list[Enumeration] = (), implementations=(), traits=(), *, externally_checked=False, pointer_bits=None, module_resolution=None, crate_policy=None, consts=()):
        if pointer_bits is None:
            from .targets import DEFAULT_TARGET, resolve_target
            pointer_bits = resolve_target(DEFAULT_TARGET).pointer_bits
        if pointer_bits != 64:
            raise RustError('Rust pointer data model is not yet qualified')
        self.module_resolution = module_resolution
        self.current_module = module_resolution.root if module_resolution is not None else None
        self.replaying_instance = False
        self.pointer_bits = pointer_bits
        self.externally_checked = externally_checked
        self.signature_lifetimes = None  # lifetimes declared on the signature being validated
        self.functions = functions
        self.records: dict[str, Record] = {}
        for record in records:
            if record.declaration_name in self.records or record.token.text in INTEGERS | {"bool"}:
                raise record.token.error("duplicate or reserved struct type")
            self.records[record.declaration_name] = record
        self.enums: dict[str, Enumeration] = {}
        for enum in enums:
            name = enum.declaration_name
            if name in self.records or name in self.enums or enum.token.text in INTEGERS | {"bool"}:
                raise enum.token.error("duplicate or reserved enum type")
            self.enums[name] = enum
        self.names: dict[str, Function] = {}
        # Free const items. A single-module crate looks them up by name; a
        # module crate resolves them through its module namespaces.
        self.consts = tuple(consts)
        self.const_names = {}
        if module_resolution is None:
            function_names = {fn.token.text for fn in functions if fn.implementation is None}
            for const in self.consts:
                if const.token.text in self.const_names or const.token.text in function_names:
                    raise const.token.error(f'the name {const.token.text!r} is defined multiple times in the value namespace')
                self.const_names[const.token.text] = const
        self.evaluating_consts = []
        # Nesting of mandatory constant evaluation (array lengths, enum
        # discriminants; const and static initializers use evaluating_consts).
        self.const_evaluation = 0
        # Nesting of code that is checked but never emitted (a disabled
        # debug_assert!): runtime models it would need are not required.
        self.suppressed_runtime = 0
        self.declared_derives = {}
        self.scopes: list[dict[str, Binding]] = []
        self.serial = 0
        self.loops = []
        self.result = "()"
        self.parameters: dict[str, list[Binding]] = {}
        self.unsafe_depth = 0
        self.type_scope = {}
        self.abstract_function = False
        self.inferred_calls = []
        self.current_function = None
        from .generics import GenericDeclarations
        from .function_generics import GenericFunctions
        self.generics = GenericDeclarations(self)
        self.function_generics = GenericFunctions(self)
        from .methods import InherentMethods
        self.methods = InherentMethods(self, implementations)
        for implementation in implementations:
            # Owned regions (stage 6): `impl<'a> W<'a>` for a data type with
            # one lifetime and no type parameters, its methods' signatures
            # naming 'a. Other impl lifetimes keep the actual-check gate.
            owner = implementation.owner
            if (implementation.lifetime_parameters and not externally_checked
                    and not (len(implementation.lifetime_parameters) == 1 and not implementation.type_parameters
                             and implementation.trait is None and isinstance(owner, TypeApplication)
                             and not owner.arguments
                             and owner.lifetimes == (implementation.lifetime_parameters[0].text,))):
                raise implementation.lifetime_parameters[0].error(
                    "explicit impl lifetimes require a matching external rustc check record")
        from .traits import StaticTraits
        self.traits = StaticTraits(self, traits, implementations)
        from .standard_markers import StandardMarkers
        self.standard_markers = StandardMarkers(self, crate_policy)

    @staticmethod
    def function_module(fn):
        """The module whose scope fn's signature and body resolve in, if any:
        its own item module, or its impl's."""
        if fn.item_identity is not None:
            return fn.item_identity.module
        if fn.scope_module is not None:
            return fn.scope_module
        if fn.implementation is not None:
            return fn.implementation.module
        return None

    def source_type_access(self, ty, token):
        # Privacy of source-written types/expressions is checked in the unused
        # abstract body before any concrete instance is scheduled. A caller's
        # private nominal type can legally flow through an opaque T in that
        # body. Replaying T=b::Private in a must not reclassify it as the source
        # spelling a::b::Private. Lexical path/field lookup still uses a.
        if self.module_resolution is not None and not self.replaying_instance:
            self.module_resolution.type_access(ty, self.current_module, token)

    def bind(self, token: Token, ty: object, mutable: bool, *, parameter=False) -> Binding:
        # An identifier pattern naming a const is a constant pattern in Rust,
        # never a fresh binding; keep that form an explicit gap.
        item = self.constant_named(token.text, token)
        if isinstance(item, StaticItem):
            raise token.error(f'let bindings cannot shadow statics: {token.text!r} (rustc E0530)')
        if item is not None:
            raise token.error(f'{token.text!r} names a constant: constant patterns and bindings that '
                              'would shadow a constant are not implemented')
        self.source_type_access(ty, token)
        self.serial += 1
        binding = Binding(token.text, ty, mutable, self.serial, parameter)
        self.scopes[-1][local_key(token.text, token)] = binding
        return binding

    def struct_variant(self, path, token):
        """(enum owner, enum, variant) when `path` names a struct-like variant."""
        if not isinstance(path, str) or "::" not in path:
            return None
        owner, variant = path.rsplit("::", 1)
        if all(part in {"crate", "self", "super"} for part in owner.split("::")):
            return None  # `super::S { .. }`: a module path, never an enum
        if owner == "Self":
            resolved = self.generics.enum_constructor_type("Self", token)
            enum = self.generics.definitions.get(resolved.name if isinstance(resolved, TypeApplication) else resolved)
        elif self.module_resolution is not None:
            declaration = self.module_resolution.resolve(owner, "type", self.current_module, token, _optional_final=True)
            enum = declaration.syntax if declaration is not None else None
        else:
            enum = self.generics.definitions.get(owner)
        if not isinstance(enum, Enumeration) or variant not in enum.discriminants:
            return None
        if variant not in enum.named_payloads:
            raise token.error("struct literal syntax for tuple or unit variants is not implemented")
        return owner, enum, variant

    def pattern_let(self, expr, init):
        """`let PATTERN (: T)? = init;` for tuple, binding and wildcard patterns.

        These are irrefutable for every type they check against. The bindings
        go into the enclosing block scope (and may shadow earlier locals), but
        a name may occur once in the pattern (E0416)."""
        from .patterns import check_pattern
        from .syntax import IRREFUTABLE_PATTERNS
        pattern, annotation = expr.value
        pending = [pattern]
        while pending:
            item = pending.pop()
            if item.kind not in IRREFUTABLE_PATTERNS:
                raise item.token.error("let patterns other than tuple, reference, binding and wildcard patterns "
                                       "require irrefutability checking, which is not implemented")
            pending.extend(item.children)
        # A place initializer is matched in place, as a match scrutinee is:
        # only the parts bound by value move (statements.html#let-statements).
        place = self.place(init) is not None
        init_ty = self.expression(init, place_context=place)
        if init_ty == NEVER:
            raise expr.token.error("diverging let initializers are not implemented")
        if annotation is not None:
            annotation = self.generics.normalize(annotation, expr.token, placeholder_context="local")
            self.valid_type(annotation, expr.token)
            unify(annotation, init_ty, expr.token)
            from .call_inference import partial
            init_ty = partial(annotation)
        self.scopes.append({})
        try:
            check_pattern(self, pattern, init_ty)
            if place:
                self.binding_moves(init, pattern)
        finally:
            bound = self.scopes.pop()
        self.scopes[-1].update(bound)

    def tuple_struct_construction(self, expr, callee, record, path, children):
        """`S(a, b)` / `S::<T>(a)`: the tuple struct's constructor, checked and
        lowered as the struct expression `S { 0: a, 1: b }` it is equivalent to
        (Rust Reference expressions/struct-expr.html#tuple-struct-expression)."""
        name = record.token.text
        if not record.tuple_fields:
            raise callee.token.error(f"expected function, tuple struct or tuple variant, found struct `{name}` (rustc E0423)")
        arguments = children[1:]
        if len(arguments) != len(record.fields):
            raise expr.token.error(f"this struct takes {len(record.fields)} field(s) but {len(arguments)} "
                                   "were supplied (rustc E0061)")
        from dataclasses import replace
        binding = record.declaration_name
        if isinstance(path, TypeApplication):
            binding = TypeApplication(binding, path.arguments)
        expr.kind, expr.binding, expr.children = "construct", binding, arguments
        expr.value = [replace(argument.token, kind="integer", text=str(index))
                      for index, argument in enumerate(arguments)]
        return self.expression(expr)

    def construct_struct_variant(self, expr, children):
        """`E::V { a: x, b }`: fields evaluate in source order and are stored
        at their declaration positions (an `enum_construct` with a mapping)."""
        owner, enum, variant = self.struct_variant(expr.binding, expr.token)
        enum_name = self.generics.enum_constructor_type(owner, expr.token)
        self.valid_type(enum_name, expr.token)
        field_types = self.generics.specialized_payload(enum_name, variant)
        names = [token.text for token in enum.named_payloads[variant]]
        provided, indices = set(), []
        for field, child in zip(expr.value, children):
            if field.text not in names:
                raise field.error(f"variant `{owner}::{variant}` has no field named `{field.text}` (rustc E0559)")
            if field.text in provided:
                raise field.error(f"field `{field.text}` specified more than once (rustc E0062)")
            provided.add(field.text)
            index = names.index(field.text)
            indices.append(index)
            unify(field_types[index], self.expression(child), field)
        missing = [name for name in names if name not in provided]
        if missing:
            raise expr.token.error(f"missing field `{missing[0]}` in initializer of `{owner}::{variant}` (rustc E0063)")
        expr.kind, expr.value, expr.binding = "enum_construct", variant, tuple(indices)
        return enum_name

    def builtin_clone(self, ty):
        """Clone without a user method: derived or primitive Clone types."""
        from .call_inference import partial
        from .derives import implements
        ty = partial(ty)
        if isinstance(ty, TypeApplication) or isinstance(ty, (InferredInteger, InferredType)):
            return False
        base = ty
        if (base, "clone") in self.methods.declarations or self.traits.candidates(ty, "clone"):
            return False
        return implements(self, ty, "Clone")

    def constant_named(self, name, token):
        """The const item a bare value name denotes in the current module."""
        if name == "self":
            return None  # the receiver keyword, never an item path
        if self.module_resolution is not None:
            return self.module_resolution.constant(name, self.current_module, token)
        return self.const_names.get(name)

    def constant_path(self, owner, name, token):
        """The const item `owner::name` denotes when `owner` is a module path."""
        if isinstance(owner, TypeApplication):
            return None
        if self.module_resolution is None:
            return self.const_names.get(name) if owner in {'crate', 'self'} else None
        if not self.module_resolution.module_path(owner, self.current_module, token):
            return None
        const = self.module_resolution.constant(owner + '::' + name, self.current_module, token)
        if const is None:
            raise token.error(f'cannot find value {name!r} in module {owner!r}')
        return const

    def evaluate_const(self, const, token):
        """Type and evaluate one const initializer, once, at its declaration.

        Rust Reference const_eval.html: integer and bool const items whose
        initializers use literals, unary/binary operators, casts and other
        const items. Overflow and out-of-range literals are errors (rustc
        E0080), as is a cycle between initializers (E0391).
        """
        if const.evaluated:
            return const
        if const in self.evaluating_consts:
            raise token.error(f'cycle detected when evaluating constant {const.token.text!r} (rustc E0391)')
        if len(self.evaluating_consts) >= 64:
            raise token.error('const item dependency chain exceeds implementation limit (64)')
        saved = (self.scopes, self.current_module, self.type_scope, self.abstract_function)
        self.evaluating_consts.append(const)
        try:
            self.scopes, self.type_scope, self.abstract_function = [{}], {}, False
            if const.item_identity is not None:
                self.current_module = const.item_identity.module
            # In a const item an elided `&str` lifetime is 'static.
            ty = (STATIC_STR if is_static_str(const.ty, elided_is_static=True) and self.generics.primitive_str(const.token)
                  else self.generics.normalize(const.ty, const.token))
            label = 'static' if isinstance(const, StaticItem) else 'const'
            if ty not in INTEGERS | {"bool", STATIC_STR, RUST_CHAR}:
                raise const.token.error(f'{label} items of type {ty} are not implemented; integer, bool and &str {label} items are')
            pending, count = [const.expression], 0
            while pending:
                node = pending.pop()
                count += 1
                if count > 1024:
                    raise node.token.error('const initializer exceeds implementation limit (1024 nodes)')
                if node.kind not in {'integer', 'bool', 'character', 'string', 'group', 'unary', 'binary', 'cast', 'name', 'variant', 'const', 'static'}:
                    raise node.token.error('const initializers support literals, operators, casts and other const '
                                           'items; calls, blocks and other expressions are not implemented')
                pending.extend(node.children)
            unify(ty, self.expression(const.expression), const.expression.token)
            value = self.validate(const.expression)
            if ty == STATIC_STR:
                value = self.static_str_value(const.expression)
            if value is None:
                raise const.expression.token.error(f'{label} initializer is not a closed constant expression')
            const.ty, const.value, const.evaluated = ty, value, True
            return const
        finally:
            self.evaluating_consts.pop()
            self.scopes, self.current_module, self.type_scope, self.abstract_function = saved

    def item_value_kind(self, item, token):
        """A const use folds to its value; a static use reads its place.
        Any use of a `static mut` needs an unsafe context (rustc E0133)."""
        if not isinstance(item, StaticItem):
            return "const"
        if item.mutable and not self.unsafe_depth:
            raise token.error(f"use of mutable static `{item.token.text}` is unsafe and requires an unsafe block (rustc E0133)")
        return "static"

    def assigns_static(self, lhs):
        """Type an assignment target; True when it is a static item."""
        if lhs.kind not in {"name", "variant", "static"}:
            return False
        if lhs.kind == "name" and self.local(str(lhs.value), lhs.token) is not None:
            return False
        if lhs.kind != "static":
            item = (self.constant_named(str(lhs.value), lhs.token) if lhs.kind == "name"
                    else self.constant_path(lhs.value[0], lhs.value[1], lhs.token))
            if not isinstance(item, StaticItem):
                return False
            self.expression(lhs, place_context=True)
        return lhs.kind == "static"

    def static_str_value(self, expr):
        """The literal text a `&'static str` const initializer denotes."""
        while expr.kind == "group":
            expr = expr.children[0]
        if expr.kind == "string":
            return expr.value
        if expr.kind == "const" or expr.kind == "static" and not expr.binding.mutable:
            return expr.binding.value
        return None

    def local(self, name: str, token: Token | None = None) -> Binding | None:
        """The local `name` written as `token` denotes: macro_rules! hygiene
        keys locals by the token's expansion mark (local_key)."""
        key = local_key(name, token)
        for scope in reversed(self.scopes):
            if key in scope:
                return scope[key]
        return None

    def fields(self, ty: object) -> tuple[object, ...]:
        if isinstance(ty, FixedArrayType):
            return (ty.element,) * ty.length
        if isinstance(ty, TupleType):
            return ty.elements
        if ty in self.records:
            return tuple(field_ty for _, field_ty in self.records[ty].fields)
        return ()

    def aggregate(self, ty: object) -> bool:
        return isinstance(ty, (TupleType, FixedArrayType)) or ty in self.records

    def copy_type(self, ty: object) -> bool:
        from .call_inference import partial
        ty = partial(ty)
        if isinstance(ty, InferredInteger):
            return True
        if isinstance(ty, TypeParameter):
            return self.standard_markers.copy_parameter(ty)
        if isinstance(ty, TypeApplication):
            return False
        if isinstance(ty, SharedReference):
            return not ty.mutable
        if isinstance(ty, FixedArrayType):
            return self.copy_type(ty.element)
        if isinstance(ty, TupleType):
            return all(self.copy_type(item) for item in ty.elements)
        # Named Rust structs do NOT become Copy just because their fields are;
        # only a validated `#[derive(Copy)]` makes one Copy.
        if ty in self.records or ty in self.enums:
            return "Copy" in self.declared_derives.get(ty, ())
        return ty in INTEGERS | {"bool", "()", STATIC_STR, RUST_CHAR}

    def valid_type(self, ty: object, token: Token, stack: tuple[str, ...] = (), *, allow_reference=True, require_sized=True) -> None:
        if isinstance(ty, (InferredInteger, InferredType)):
            return  # Local borrow inference resolves after all call constraints.
        if isinstance(ty, TypeParameter):
            if self.abstract_function and ty in self.type_scope.values():
                if require_sized:
                    self.standard_markers.require_sized(ty, token)
                return
            raise token.error("unbound generic type parameter")
        if isinstance(ty, TypeApplication):
            from .call_inference import partial, pending_integer
            ty = partial(ty)
            if pending_integer(ty):
                for argument in ty.arguments:
                    self.valid_type(argument, token, allow_reference=False)
                return  # Declaration layout was already checked; storage waits for body constraints.
            self.generics.instantiate(ty, token)
        if isinstance(ty, SharedReference):
            if ty.lifetime and not self.externally_checked:
                self.require_lifetime(ty.lifetime, token)
            if not allow_reference:
                raise token.error("reference returns and aggregate reference storage require lifetime analysis, which is not implemented")
            if is_borrowed_str(ty):
                return  # Sized descriptor; the pointee never has standalone storage.
            if ty.referent == "()" or isinstance(ty.referent, SharedReference):
                raise token.error("unit and nested references require additional reference layout/lifetime support")
            self.valid_type(ty.referent, token, stack, allow_reference=False, require_sized=False)
            return
        if isinstance(ty, FixedArrayType):
            from .arrays import length_value, storage_units
            length_value(self, ty.length, token)
            self.valid_type(ty.element, token, stack, allow_reference=False)
            storage_units(self, ty, token)
            return
        if isinstance(ty, TupleType):
            for item in ty.elements:
                self.valid_type(item, token, stack, allow_reference=False)
            return
        if ty in INTEGERS | {"bool", "()", STATIC_STR, RUST_CHAR}:
            return
        if ty in self.enums:
            if ty in stack:
                raise token.error("recursive aggregate has infinite size")
            enum = self.enums[ty]
            with self.declaration_lifetimes(enum):
                for fields in enum.payloads.values():
                    for field_ty in fields:
                        self.valid_type(field_ty, token, stack + (ty,), allow_reference=bool(enum.lifetime_parameters))
            return
        if ty not in self.records:
            raise token.error(f"unsupported Rust type {ty!r}")
        if ty in stack:
            raise token.error("recursive aggregate has infinite size")
        record = self.records[ty]
        if not record.fields:
            raise record.token.error("zero-field structs are not implemented")
        names = set()
        with self.declaration_lifetimes(record):
            for field, field_ty in record.fields:
                if field.text in names:
                    raise field.error("duplicate struct field")
                names.add(field.text)
                self.valid_type(field_ty, field, stack + (ty,), allow_reference=bool(record.lifetime_parameters))

    def place(self, expr: Expr):
        if expr.kind == "group":
            return self.place(expr.children[0])
        if expr.kind == "deref":
            return self.place(expr.children[0])
        if expr.kind == "name":
            return expr.binding, ()
        if expr.kind == "index":
            from pcc.analysis.ownership import Index
            base = self.place(expr.children[0])
            if base is not None:
                return base[0], base[1] + (Index(expr.value),)
        if expr.kind == "field":
            base = self.place(expr.children[0])
            if base is not None:
                return base[0], base[1] + (expr.value,)
        return None

    def reference_temporary(self, expr):
        """The reference-valued temporary (e.g. a call result) an assignee is
        reached through, as in `*s.slot() = v`, or None."""
        if expr.kind == "group":
            return self.reference_temporary(expr.children[0])
        if expr.kind in {"deref", "field", "index"}:
            base = expr.children[0]
            if isinstance(base.ty, SharedReference) and self.place(base) is None:
                return base
            return self.reference_temporary(base) if expr.kind != "deref" else None
        return None

    def through_reference(self, expr: Expr) -> bool:
        if expr.kind == "deref":
            return True
        if expr.kind == "group":
            return self.through_reference(expr.children[0])
        if expr.kind in {"field", "index"}:
            return isinstance(expr.children[0].ty, SharedReference) or self.through_reference(expr.children[0])
        return False

    def coercible(self, ty, target, token):
        """`&mut T` where `&T` is expected: a reference coercion."""
        from .call_inference import partial
        ty, target = partial(ty), partial(target)
        if ty == STATIC_STR and is_borrowed_str(target):
            return True  # 'static outlives the destination, never the reverse.
        if (isinstance(ty, SharedReference) and isinstance(target, SharedReference)
                and ty.mutable and not target.mutable):
            unify(target.referent, ty.referent, token)
            return True
        return False

    def result_coercion(self, body, body_ty):
        """Coerce a block's `&mut T` tail to the `&T` result it returns."""
        if (body.kind != "block" or len(body.children) <= body.value
                or not self.coercible(body_ty, self.result, body.children[-1].token)):
            return False
        tail = body.children[-1]
        body.children[-1] = self.implicit_reborrow(tail, self.result) or Expr(
            "reference_coerce", tail.token, children=[tail], ty=self.result)
        return True

    def implicit_reborrow(self, expr, target, *, two_phase=False):
        """A `&mut` place used where a reference is expected (a coercion site:
        an argument, an annotated let, an assignment) is reborrowed, `&mut *r`
        or `&*r`, rather than moved (rustc's implicit reborrow). None when the
        operand is not such a place."""
        from .call_inference import partial
        ty, target = partial(expr.ty), partial(target)
        if not (isinstance(target, SharedReference) and isinstance(ty, SharedReference) and ty.mutable):
            return None
        if expr.kind == "borrow" or self.place(expr) is None:
            return None
        deref = Expr("deref", expr.token, children=[expr], ty=ty.referent)
        mutable = target.mutable
        return Expr("borrow", expr.token, (TWO_PHASE if two_phase else True) if mutable else False,
                    children=[deref], ty=SharedReference(ty.referent, mutable))

    @contextmanager
    def declaration_lifetimes(self, declaration):
        """Validate inside a data declaration's own lifetime scope."""
        previous = self.signature_lifetimes
        self.signature_lifetimes = {token.text for token in declaration.lifetime_parameters}
        try:
            yield
        finally:
            self.signature_lifetimes = previous

    def require_lifetime(self, lifetime, token):
        """A written lifetime outside an actual check: 'static, '_ or one the
        enclosing signature, impl or data declaration declares."""
        if lifetime in ("'_", "'static"):
            return
        if self.signature_lifetimes is None:
            raise token.error("explicit reference lifetimes outside a function signature need region "
                              "inference, which is not implemented")
        if lifetime not in self.signature_lifetimes:
            raise token.error(f"use of undeclared lifetime name `{lifetime}` (rustc E0261)")

    def reference_field(self, declaration, ty, aggregate):
        """A reference field of a data declaration with one lifetime parameter
        (stage 6): `&'a T` or `&'static T` with a region-free T."""
        from .reference_admission import has_regions
        token = declaration.token
        if self.externally_checked:
            return
        if aggregate:
            raise token.error("references inside tuple or array fields need region inference, which is not "
                              "implemented")
        if not declaration.lifetime_parameters:
            raise token.error("missing lifetime specifier: a reference field needs the declaration's "
                              "lifetime parameter (E0106)")
        if ty.lifetime is None or ty.lifetime == "'_":
            raise token.error("missing lifetime specifier: a reference field must name a lifetime (E0106)")
        if ty.lifetime not in ("'static", declaration.lifetime_parameters[0].text):
            raise token.error(f"use of undeclared lifetime name `{ty.lifetime}` (rustc E0261)")
        if has_regions(ty.referent, self):
            raise token.error("a reference field to data with regions needs region inference, which is not "
                              "implemented")

    def region_field(self, declaration, nested, aggregate):
        """A field of a data type with a lifetime shares the declaration's region."""
        if self.externally_checked:
            return
        if not declaration.lifetime_parameters:
            raise declaration.token.error(f"missing lifetime specifier: a field of type "
                                          f"{nested.token.text} needs a lifetime parameter (E0106)")
        if aggregate:
            raise declaration.token.error("data with regions inside tuple or array fields needs region "
                                          "inference, which is not implemented")

    def binding_moves(self, scrutinee, pattern):
        """By-value bindings of a place scrutinee move out of it: not through
        a shared reference (rustc E0507) or an array index (E0508)."""
        if self.place(scrutinee) is None:
            return
        from .patterns import pattern_bindings
        for binding in pattern_bindings(pattern):
            if self.copy_type(binding.ty):
                continue
            if self.through_reference(scrutinee):
                raise pattern.token.error("cannot move a non-Copy value out of a shared reference")
            if self.through_array_index(scrutinee):
                raise pattern.token.error("cannot move a non-Copy value out of an array index")

    def through_array_index(self, expr):
        return expr.kind == "index" or expr.kind in {"group", "field"} and self.through_array_index(expr.children[0])

    def mutable_place(self, expr: Expr) -> bool:
        """Mutability/addressability only; shared loans are the move checker's, &mut aliasing external."""
        if expr.kind == "group":
            return self.mutable_place(expr.children[0])
        if expr.kind == "deref":
            pointer = expr.children[0].ty
            return isinstance(pointer, SharedReference) and pointer.mutable
        if expr.kind in {"field", "index"}:
            base = expr.children[0]
            if isinstance(base.ty, SharedReference):
                return base.ty.mutable
            return self.mutable_place(base)
        return expr.kind == "name" and expr.binding.mutable

    def check(self) -> None:
        from .patterns import check_enums

        self.generics.prepare()
        check_enums(self, self.generics.enum_definitions)
        self.generics.check_layouts()
        for name, enum in list(self.enums.items()):
            self.valid_type(name, enum.token)
        for name, record in list(self.records.items()):
            self.valid_type(name, record.token)
        from .derives import validate_derives
        validate_derives(self)
        self.traits.prepare()
        self.methods.prepare()
        # rustc evaluates every free const item, used or not.
        for const in self.consts:
            self.evaluate_const(const, const.token)
        declarations = tuple(self.functions)
        for fn in declarations:
            module = self.function_module(fn)
            if module is not None:
                self.current_module = module
            self.type_scope = self.function_generics.prepare(fn)
            self.abstract_function = bool(fn.type_parameters)
            # Written types keep the lifetimes normalization erases (regions.py).
            if fn.written_signature is None:
                fn.written_signature = (tuple(ty for _, ty, _ in fn.params), fn.result)
            # A top-level reference result is owned (regions.py); references
            # nested in result aggregates still need region parameters.
            impl_lifetimes = fn.implementation.lifetime_parameters if fn.implementation is not None else ()
            self.signature_lifetimes = {token.text for token in (*fn.lifetime_parameters, *impl_lifetimes)}
            try:
                fn.result = self.generics.normalize(fn.result, fn.token)
                fn.params = [(token, self.generics.normalize(ty, token), mutable)
                             for token, ty, mutable in fn.params]
                self.valid_type(fn.result, fn.token)
                parameter_names = set()
                for token, ty, _ in fn.params:
                    if token.text in parameter_names:
                        raise token.error(f"duplicate parameter {token.text!r}")
                    parameter_names.add(token.text)
                    self.valid_type(ty, token)
            finally:
                self.signature_lifetimes = None
            from .regions import signature_regions
            signature_regions(fn, self)  # rustc E0106 for an underivable elided output
            name = fn.declaration_name
            if name in self.names:
                raise fn.token.error(f"duplicate function {name!r}")
            if fn.token.text.startswith("__pcc_rust_"):
                raise fn.token.error("reserved PCC Rust symbol prefix")
            if fn.no_mangle and fn.abi != "C":
                raise fn.token.error("no_mangle is supported only on extern C function definitions")
            self.names[name] = fn
            if fn.abi == "C" and (any(ty not in FFI_INTEGERS and not isinstance(ty, SharedReference) for _, ty, _ in fn.params) or fn.result not in FFI_INTEGERS | {"()"}):
                raise fn.token.error("extern C currently supports integer/shared-reference parameters and integer/unit returns; aggregate ABI requires a separately qualified repr(C) shape")
            if fn.abi == "C":
                for token, ty, _ in fn.params:
                    if isinstance(ty, SharedReference):
                        record = self.records.get(ty.referent)
                        if (ty.mutable or isinstance(ty.referent, TypeApplication) or record is None or not record.repr_c
                                or not record.fields or any(t not in FFI_INTEGERS for _, t in record.fields)):
                            raise token.error("foreign shared references require the separately qualified non-generic repr(C) integer record shape")
        self.traits.validate_signatures()
        self.type_scope = {}
        self.abstract_function = False
        self.inferred_calls = []
        # Defaults have their own declaration environment, including abstract
        # Self and its declared trait identities. Check even overridden/unused
        # bodies, then pin inherited calls before concrete body replay.
        self.traits.defaults.check()
        # Rust checks declarations, not only the bodies selected by concrete
        # calls. Generic parameters stay non-Copy even if every current call
        # happens to substitute a primitive Copy type.
        from .moves import MoveChecker
        for fn in declarations:
            if fn.type_parameters:
                self.check_body(fn, self.function_generics.scope(fn), abstract=True)
                from .reference_admission import contains_references
                if not self.externally_checked or not contains_references(fn):
                    MoveChecker(self).check_function(fn)
        for fn in declarations:
            if not fn.type_parameters:
                self.check_body(fn, self.function_generics.scope(fn))
        self.function_generics.materialize()

    def check_body(self, fn, type_scope, *, abstract=False):
        if fn.body is None:
            return
        previous_scope, previous_abstract = self.type_scope, self.abstract_function
        previous_function, self.current_function = self.current_function, fn
        previous_module, previous_replay = self.current_module, self.replaying_instance
        self.replaying_instance = fn.instance_arguments is not None
        module = self.function_module(fn)
        if module is not None:
            self.current_module = module
        previous_calls, self.inferred_calls = self.inferred_calls, []
        self.type_scope, self.abstract_function = type_scope, abstract
        try:
            self.scopes = [{}]
            self.loops = []
            self.result = fn.result
            self.unsafe_depth = int(fn.unsafe)
            params: list[Binding] = []
            for name, ty, mutable in fn.params:
                if name.text in self.scopes[0]:
                    raise name.error(f"duplicate parameter {name.text!r}")
                params.append(self.bind(name, ty, mutable, parameter=True))
            self.parameters[fn.identity] = params
            body_ty = self.expression(fn.body)
            if self.result_coercion(fn.body, body_ty):
                # The result is a coercion site: `&mut T` becomes `&T` there.
                body_ty = fn.body.ty = fn.result
            unify(fn.result, body_ty, fn.body.token)
            from .call_inference import finish
            finish(self, self.inferred_calls)
            self.validate(fn.body)
        finally:
            self.type_scope, self.abstract_function = previous_scope, previous_abstract
            self.current_module, self.replaying_instance = previous_module, previous_replay
            self.inferred_calls = previous_calls
            self.current_function = previous_function

    def expression(self, expr: Expr, *, allow_borrow=False, place_context=False) -> object:
        kind, children = expr.kind, expr.children
        if kind == "integer":
            expr.value, ty = integer_literal(expr)
        elif kind == "group":
            ty = self.expression(children[0], allow_borrow=allow_borrow, place_context=place_context)
        elif kind == "borrow":
            # Shared and mutable loans, their liveness and index overlap are
            # owned by the move checker (moves.py); escapes need regions.
            referent = self.expression(children[0], place_context=True)
            if self.place(children[0]) is None:
                raise expr.token.error("shared borrowing of temporaries requires lifetime extension, which is not implemented")
            if expr.value and not self.mutable_place(children[0]):
                raise expr.token.error("cannot borrow an immutable place as mutable")
            ty = SharedReference(referent, bool(expr.value))
            self.valid_type(ty, expr.token)
        elif kind == "deref":
            # Dereferencing a place reads the pointer in place; it never moves
            # a `&mut` field out of what it is reached through.
            pointer = self.expression(children[0], place_context=self.place(children[0]) is not None)
            if not isinstance(pointer, SharedReference):
                raise expr.token.error("dereference requires an admitted shared reference; raw pointers are not implemented")
            ty = pointer.referent
            if ty == STR_POINTEE:
                raise expr.token.error("dereferencing an unsized str place is not implemented")
            if not place_context and not self.copy_type(ty):
                raise expr.token.error("cannot move a non-Copy value out of a shared reference")
        elif kind == "std_output":
            # Covers semantic entry points operating on module syntax directly.
            raise expr.value.missing_runtime()
        elif kind == "character":
            ty = RUST_CHAR
        elif kind == "bool":
            ty = "bool"
        elif kind == "variant" and self.constant_path(expr.value[0], expr.value[1], expr.token) is not None:
            const = self.evaluate_const(self.constant_path(expr.value[0], expr.value[1], expr.token), expr.token)
            expr.kind, expr.binding, expr.value = self.item_value_kind(const, expr.token), const, None
            ty = const.ty
        elif kind in {"const", "static"}:
            ty = expr.binding.ty
        elif kind == "string":
            ty = STATIC_STR
        elif kind == "variant":
            enum_name, variant = expr.value
            if isinstance(enum_name, TypeApplication) or enum_name in self.generics.definitions or enum_name == "Self" or self.module_resolution is not None:
                enum_name = self.generics.enum_constructor_type(enum_name, expr.token)
                self.valid_type(enum_name, expr.token)
            enum = self.generics.definitions.get(
                enum_name.name if isinstance(enum_name, TypeApplication) else enum_name)
            if not isinstance(enum, Enumeration) or variant not in enum.discriminants:
                raise expr.token.error("unknown enum variant")
            if variant in enum.payloads:
                raise expr.token.error("payload variant requires constructor arguments")
            expr.value = enum.discriminants[variant]
            expr.binding = variant
            ty = enum_name
        elif kind == "pattern_let":
            ty = "()"
            self.pattern_let(expr, children[0])
        elif kind in {"unit", "let", "wildcard_let", "discard", "statement"}:
            ty = "()"
            if kind in {"let", "wildcard_let"}:
                annotation, mutable = expr.value
                init_ty = self.expression(children[0], place_context=kind == "wildcard_let")
                if init_ty == NEVER:
                    raise expr.token.error("diverging let initializers are not implemented")
                if annotation is not None:
                    # Each annotation hole belongs to this local declaration and
                    # body replay. Share it with the initializer and later uses;
                    # resolving here would default integers before call/result
                    # constraints have reached the same inference variables.
                    annotation = self.generics.normalize(
                        annotation, expr.token, placeholder_context="local")
                    self.valid_type(annotation, expr.token)
                    if init_ty == STATIC_STR and is_borrowed_str(annotation):
                        children[0] = Expr("reference_coerce", children[0].token, children=[children[0]], ty=annotation)
                    else:
                        unify(annotation, init_ty, expr.token)
                    from .call_inference import partial
                    init_ty = partial(annotation)
                    children[0] = self.implicit_reborrow(children[0], init_ty) or children[0]
                if kind == "let":
                    expr.binding = self.bind(expr.token, init_ty, mutable)
            elif children:
                child_ty = self.expression(children[0])
                if kind == "statement":
                    unify("()", child_ty, expr.token)
                if child_ty == NEVER:
                    ty = NEVER
        elif kind in {"array", "array_repeat"}:
            from .arrays import MAX_ARRAY_ELEMENTS, length_value
            element = InferredType(expr.token)
            for child in children:
                child_type = self.expression(child)
                if child_type == NEVER:
                    raise child.token.error("diverging array operands require additional never-type inference, which is not implemented")
                element = unify(element, child_type, child.token)
            count = length_value(self, expr.value, expr.token) if kind == "array_repeat" else len(children)
            if count > MAX_ARRAY_ELEMENTS:
                raise expr.token.error("array length exceeds implementation limit (4096 elements)")
            expr.value = count
            ty = FixedArrayType(element, count)
        elif kind == "index":
            from .arrays import closed_constant
            from .call_inference import partial
            base_type = partial(self.expression(children[0], place_context=True))
            if isinstance(base_type, SharedReference):
                base_type = base_type.referent
            if not isinstance(base_type, FixedArrayType):
                raise expr.token.error("indexing requires an array; slices and Index traits are not implemented")
            unify("usize", self.expression(children[1]), children[1].token)
            closed_constant(children[1], children[1].token)
            ty = base_type.element
            if not place_context and not self.copy_type(ty):
                raise expr.token.error("cannot move a non-Copy value out of an array index")
        elif kind == "tuple":
            elements = tuple(self.expression(child) for child in children)
            if NEVER in elements:
                raise expr.token.error("diverging tuple operands require never-type layout support, which is not implemented")
            if any(isinstance(item, SharedReference) for item in elements):
                raise expr.token.error("aggregate reference storage requires lifetime analysis, which is not implemented")
            ty = TupleType(elements)
        elif kind == "construct" and self.struct_variant(expr.binding, expr.token) is not None:
            ty = self.construct_struct_variant(expr, children)
        elif kind == "construct":
            # Only a struct literal may omit its known owner's arguments.
            # Type annotations and abstract parameters keep their own rules.
            constructor = expr.binding
            if isinstance(constructor, str):
                constructor = self.generics.canonical_name(constructor, expr.token)
                declaration = self.generics.definitions.get(constructor)
                if (constructor not in self.type_scope and isinstance(declaration, Record)
                        and declaration.type_parameters):
                    constructor = TypeApplication(
                        constructor, ("_",) * len(declaration.type_parameters))
            # Fresh literal holes share their field/result constraints.
            # Do not put unresolved arguments into the data-instance registry;
            # valid_type materializes storage after whole-body resolution.
            record_name = self.generics.normalize(
                constructor, expr.token, placeholder_context="constructor")
            self.valid_type(record_name, expr.token)
            record = (self.generics.definitions.get(record_name.name)
                      if isinstance(record_name, TypeApplication) else self.records.get(record_name))
            if not isinstance(record, Record):
                raise expr.token.error(f"unknown struct {expr.token.text!r}")
            field_types = self.generics.specialized_fields(record_name)
            provided = set()
            indices = []
            names = [field.text for field, _ in record.fields]
            for field, child in zip(expr.value, children):
                if field.text not in names:
                    raise field.error("unknown struct field")
                if field.text in provided:
                    raise field.error("duplicate field initializer")
                provided.add(field.text)
                index = names.index(field.text)
                if self.module_resolution is not None:
                    self.module_resolution.field_access(record, index, self.current_module, field)
                indices.append(index)
                unify(field_types[index], self.expression(child), field)
            if provided != set(names):
                raise expr.token.error("missing struct field initializer")
            expr.value = indices
            ty = record_name
        elif kind == "field":
            from .call_inference import partial
            base_ty = partial(self.expression(children[0], place_context=True))
            if isinstance(base_ty, SharedReference):
                base_ty = base_ty.referent
            if isinstance(base_ty, TupleType):
                if not str(expr.value).isdecimal() or int(expr.value) >= len(base_ty.elements):
                    raise expr.token.error("unknown tuple field")
                index = int(expr.value)
                ty = base_ty.elements[index]
            elif isinstance(base_ty, TypeApplication) or isinstance(base_ty, str) and base_ty in self.records:
                if isinstance(base_ty, TypeApplication):
                    declaration = self.generics.definitions[base_ty.name]
                    if not isinstance(declaration, Record):
                        raise expr.token.error("field access requires a struct or tuple")
                    fields = list(zip((f for f, _ in declaration.fields), self.generics.specialized_fields(base_ty)))
                else:
                    fields = self.records[base_ty].fields
                names = [field.text for field, _ in fields]
                if expr.value not in names:
                    raise expr.token.error("unknown struct field")
                index = names.index(expr.value)
                if self.module_resolution is not None:
                    record = declaration if isinstance(base_ty, TypeApplication) else self.records[base_ty]
                    self.module_resolution.field_access(record, index, self.current_module, expr.token)
                ty = fields[index][1]
                self.source_type_access(ty, expr.token)
            else:
                raise expr.token.error("field access requires a struct or tuple")
            expr.value = index
            if self.through_array_index(expr) and not place_context and not self.copy_type(ty):
                raise expr.token.error("cannot move a non-Copy field out of an array index")
            if self.through_reference(expr) and not place_context and not self.copy_type(ty):
                raise expr.token.error("cannot move a non-Copy field out of a shared reference")
        elif kind == "name":
            binding = self.local(str(expr.value), expr.token)
            const = self.constant_named(str(expr.value), expr.token) if binding is None else None
            if const is not None:
                self.evaluate_const(const, expr.token)
                expr.kind, expr.binding, expr.value = self.item_value_kind(const, expr.token), const, None
                ty = const.ty
            else:
                if binding is None:
                    raise expr.token.error(f"unknown local {expr.value!r}; function values are not implemented")
                expr.binding = binding
                ty = binding.ty
        elif kind == "block":
            self.scopes.append({})
            diverges = False
            for statement in children[:expr.value]:
                if self.expression(statement) == NEVER:
                    diverges = True
            if len(children) > expr.value:
                ty = self.expression(children[-1])
            else:
                ty = NEVER if diverges else "()"
            self.scopes.pop()
        elif kind == "unsafe":
            self.unsafe_depth += 1
            ty = self.expression(children[0])
            self.unsafe_depth -= 1
        elif kind == "call":
            callee = children[0]
            while callee.kind == "group":
                callee = callee.children[0]
            fn = None
            fixed_arguments = ()
            pinned_method = self.function_generics.replay_bound_call(expr, callee.token)
            explicit_arguments = callee.type_arguments
            receiver_checked = False
            arguments_checked = False
            if callee.kind == "field":
                receiver = callee.children[0]
                from .call_inference import partial
                receiver_type = partial(self.expression(receiver, place_context=True))
                array_type = receiver_type.referent if isinstance(receiver_type, SharedReference) else receiver_type
                if (callee.value == "clone" and len(children) == 1 and not explicit_arguments
                        and not isinstance(receiver_type, SharedReference) and self.builtin_clone(receiver_type)):
                    # Derived (or primitive) Clone: a non-moving copy of the
                    # receiver, exact for PCC's heap-free admitted types.
                    expr.kind, expr.value, expr.children, expr.ty = "clone", None, [receiver], receiver_type
                    return receiver_type
                if (receiver_type == STATIC_STR or is_borrowed_str(receiver_type)) and callee.value in {"len", "is_empty"}:
                    if explicit_arguments is not None or len(children) != 1:
                        raise callee.token.error("str::" + callee.value + " needs no generic or value arguments")
                    result = "usize" if callee.value == "len" else "bool"
                    expr.kind, expr.value, expr.children, expr.ty = "str_length", callee.value, [receiver], result
                    return result
                if isinstance(array_type, FixedArrayType) and callee.value == "len":
                    if explicit_arguments:
                        raise callee.token.error("array len is not a generic method")
                    if len(children) != 1:
                        raise callee.token.error("array len expects no arguments")
                    expr.kind, expr.value, expr.children, expr.ty = "array_len", array_type.length, [receiver], "usize"
                    return "usize"
                fn, fixed_arguments = pinned_method or self.methods.lookup(receiver_type, callee.value, callee.token, receiver=True)
                if isinstance(fn.params[0][1], SharedReference) and not isinstance(receiver_type, SharedReference):
                    if self.place(receiver) is None:
                        raise receiver.token.error("shared receiver borrowing of temporaries requires lifetime extension, which is not implemented")
                    if fn.params[0][1].mutable and not self.mutable_place(receiver):
                        raise receiver.token.error("cannot borrow an immutable receiver as mutable")
                    # A mutable autoref is a two-phase borrow: reserved while
                    # the arguments are evaluated, activated by the call.
                    receiver = Expr("borrow", receiver.token, TWO_PHASE if fn.params[0][1].mutable else False,
                                    children=[receiver], ty=SharedReference(receiver_type, fn.params[0][1].mutable))
                elif self.through_array_index(receiver) and not self.copy_type(receiver_type):
                    raise receiver.token.error("cannot move a non-Copy value receiver out of an array index")
                elif self.through_reference(receiver) and not self.copy_type(receiver_type):
                    raise receiver.token.error("cannot move a value receiver out of a shared reference")
                # Canonical typed direct-call operands: receiver is first and
                # appears once. Its already-checked AST must not be rechecked.
                expr.children = children = [callee, receiver] + children[1:]
                receiver_checked = True
            elif callee.kind == "trait_path":
                owner, trait, associated_name = callee.value
                fn, fixed_arguments = pinned_method or self.traits.qualified(owner, trait, associated_name, callee.token)
            elif callee.kind == "variant":
                owner, associated_name = callee.value
                if (self.module_resolution is not None and isinstance(owner, str)
                        and owner.split('::', 1)[0] in self.type_scope):
                    raise callee.token.error('associated items on generic parameters are not implemented')
                if self.module_resolution is not None:
                    fn = self.module_resolution.associated_function(owner, associated_name, self.current_module, callee.token)
                if fn is None:
                    selected = pinned_method or self.methods.lookup(owner, associated_name, callee.token)
                    if selected is not None:
                        fn, fixed_arguments = selected
            if isinstance(fn, Record):
                # `module::S(..)`: a tuple struct constructor through a module path.
                return self.tuple_struct_construction(expr, callee, fn, None, children)
            if fn is None and callee.kind == "variant":
                if explicit_arguments is not None:
                    raise callee.token.error("type arguments on enum constructor segments are not implemented; supply them on the enum owner")
                enum_name, variant = callee.value
                if isinstance(enum_name, TypeApplication) or enum_name in self.generics.definitions or enum_name == "Self" or self.module_resolution is not None:
                    enum_name = self.generics.enum_constructor_type(enum_name, callee.token)
                    self.valid_type(enum_name, callee.token)
                enum = self.generics.definitions.get(
                    enum_name.name if isinstance(enum_name, TypeApplication) else enum_name)
                if not isinstance(enum, Enumeration) or variant not in enum.discriminants:
                    raise callee.token.error("unknown enum constructor")
                fields = self.generics.specialized_payload(enum_name, variant)
                if variant in enum.named_payloads:
                    raise callee.token.error(f"expected function, tuple struct or tuple variant, found struct variant `{variant}` (rustc E0533)")
                if fields is None:
                    raise callee.token.error("unit variant does not take constructor arguments")
                if len(children) - 1 != len(fields):
                    raise expr.token.error("enum constructor argument count mismatch")
                for arg, field_ty in zip(children[1:], fields):
                    unify(field_ty, self.expression(arg), arg.token)
                expr.kind, expr.value, expr.children = "enum_construct", variant, children[1:]
                expr.ty = enum_name
                return enum_name
            if fn is None:
                path = callee.value
                name = path.name if isinstance(path, TypeApplication) else str(path)
                if callee.kind != "name" or self.local(name, callee.token) is not None:
                    raise callee.token.error("only unshadowed direct function calls are supported")
                fn = (self.module_resolution.function(name, self.current_module, callee.token)
                      if self.module_resolution is not None else self.names.get(name))
                if fn is None and self.module_resolution is None:
                    record = self.records.get(name) or self.generics.definitions.get(name)
                    fn = record if isinstance(record, Record) else None
                if isinstance(fn, Record):
                    return self.tuple_struct_construction(expr, callee, fn, path, children)
                if fn is None and name in self.const_names:
                    kind_name = 'static' if isinstance(self.const_names[name], StaticItem) else 'constant'
                    raise callee.token.error(f'expected function, found {kind_name} {name!r} (rustc E0618)')
                if fn is None:
                    raise callee.token.error(f"unknown function {callee.value!r}")
                explicit_arguments = path.arguments if isinstance(path, TypeApplication) else None
            # An empty call turbofish supplies no fixed method/function slots.
            # Resolve the callable first so it cannot authorize empty nominal
            # owner arguments or enum constructor-segment arguments. The same
            # omitted-argument path handles inference and nongeneric calls.
            if explicit_arguments == ():
                explicit_arguments = None
            self.function_generics.remember_bound_call(expr, fn)
            self.source_type_access(fn.result, callee.token)
            for _, parameter_type, _ in fn.params:
                self.source_type_access(parameter_type, callee.token)
            if fn.unsafe and not self.unsafe_depth:
                raise expr.token.error(f"call to unsafe function {fn.token.text!r} requires an unsafe context")
            if len(children) - 1 != len(fn.params):
                raise expr.token.error(f"function {fn.token.text!r} expects {len(fn.params)} arguments")
            assert len(fixed_arguments) == fn.owner_parameter_count
            inferable_count = len(fn.type_parameters) - fn.owner_parameter_count
            if explicit_arguments is not None and fn.implementation is not None:
                if len(explicit_arguments) != inferable_count:
                    raise callee.token.error("generic method argument count mismatch")
            if explicit_arguments is not None:
                if not fn.type_parameters:
                    raise callee.token.error('type arguments supplied to a nongeneric function')
                if len(explicit_arguments) != inferable_count:
                    raise callee.token.error('generic function argument count mismatch')
                # Each source `_` is fresh for this call (and for every replay
                # of a generic body). Concrete slots remain fixed constraints.
                # Nested placeholders retain their tuple/array/nominal shape.
                explicit_arguments = tuple(
                    self.generics.normalize(ty, callee.token, placeholder_context="call")
                    for ty in explicit_arguments)
            from .call_inference import infer, pending_integer
            fixed = fixed_arguments + (explicit_arguments or ())
            pending_arguments = any(pending_integer(argument) for argument in fixed)
            if (inferable_count and explicit_arguments is None) or pending_arguments:
                # Known owner slots and explicit placeholders may be constrained
                # by operands/results or later body context. Do not register an
                # unresolved instance or default its integer variables before
                # those constraints land.
                types = [arg.ty if receiver_checked and index == 0 else
                         self.expression(arg, allow_borrow=isinstance(param_ty, SharedReference))
                         for index, (arg, (_, param_ty, _)) in enumerate(zip(children[1:], fn.params))]
                declaration = fn
                fn, arguments = infer(self, declaration, types, callee.token,
                                      fixed_arguments=fixed)
                self.inferred_calls.append((expr, declaration, arguments, callee.token))
                arguments_checked = True
            else:
                arguments = fixed if fn.type_parameters else None
                fn = self.function_generics.select(fn, arguments, callee.token,
                                                   normalized=True)
            expr.binding = fn
            for index, (arg, (_, param_ty, _)) in enumerate(zip(children[1:], fn.params)):
                arg_ty = arg.ty if arguments_checked or receiver_checked and index == 0 else self.expression(arg, allow_borrow=isinstance(param_ty, SharedReference))
                if arg_ty == STATIC_STR and is_borrowed_str(param_ty):
                    children[index + 1] = Expr("reference_coerce", arg.token, children=[arg], ty=param_ty)
                elif (isinstance(param_ty, SharedReference) and isinstance(arg_ty, SharedReference)
                        and not param_ty.mutable and arg_ty.mutable):
                    unify(param_ty.referent, arg_ty.referent, arg.token)
                    children[index + 1] = self.implicit_reborrow(arg, param_ty) or Expr(
                        "reference_coerce", arg.token, children=[arg], ty=param_ty)
                else:
                    unify(param_ty, arg_ty, arg.token)
                    # Arguments are coercion sites with two-phase reborrows.
                    children[index + 1] = self.implicit_reborrow(arg, param_ty, two_phase=True) or arg
            ty = fn.result
        elif kind == "unary":
            ty = self.expression(children[0])
            literal = children[0]
            while literal.kind == "group":
                literal = literal.children[0]
            if expr.value == "-" and literal.kind == "integer":
                literal.binding = "negative-literal"
        elif kind == "binary" and expr.value in SHIFTS:
            # rustc enforce_builtin_binop_types: a shift's operands are any
            # two integer types, never unified; the result is the LHS type.
            ty = self.expression(children[0])
            self.expression(children[1])
        elif kind == "binary":
            left = self.expression(children[0])
            right = self.expression(children[1])
            common = unify(left, right, expr.token)
            if expr.value in {"&&", "||"}:
                unify("bool", common, expr.token)
                ty = "bool"
            elif expr.value in {"==", "!=", "<", "<=", ">", ">="}:
                ty = "bool"
                from .call_inference import partial
                operand = partial(common)
                if expr.value in {"==", "!="} and not (operand in INTEGERS or operand in {"bool", RUST_CHAR}
                                                    or isinstance(operand, (InferredInteger, InferredType))):
                    from .derives import equality_gap
                    gap = equality_gap(self, operand)
                    if gap is not None:
                        raise expr.token.error(f"operator {expr.value!r} does not accept {operand}: {gap}")
                    # Structural equality of derived PartialEq data: both
                    # operands are compared in place, never moved.
                    expr.kind = "derived_eq"
            else:
                ty = common
        elif kind == "assign" and self.assigns_static(children[0]):
            lhs = children[0]
            static = lhs.binding
            if not static.mutable:
                raise lhs.token.error(f"cannot assign to immutable static item `{static.token.text}` (rustc E0594)")
            value = self.expression(children[1])
            if expr.value[:-1] not in SHIFTS:
                unify(static.ty, value, expr.token)
            ty = "()"
        elif kind == "assign":
            lhs = children[0]
            target = self.expression(lhs, place_context=True)
            place = self.place(lhs)
            if place is None and self.reference_temporary(lhs) is None:
                raise lhs.token.error("assignment target must be a local binding or field")
            if not self.mutable_place(lhs):
                raise lhs.token.error("cannot assign through a shared reference" if self.through_reference(lhs) else f"cannot assign to immutable binding {place[0].name!r}")
            value = self.expression(children[1])
            if expr.value == "=" and value == STATIC_STR and is_borrowed_str(target):
                children[1] = Expr("reference_coerce", children[1].token, children=[children[1]], ty=target)
            elif expr.value[:-1] not in SHIFTS:
                unify(target, value, expr.token)
            if expr.value == "=":
                children[1] = self.implicit_reborrow(children[1], target) or children[1]
            ty = "()"
        elif kind == "cast":
            self.expression(children[0])
            ty = self.generics.normalize(expr.value, expr.token)
        elif kind == "if":
            unify("bool", self.expression(children[0]), children[0].token)
            yes = self.expression(children[1])
            no = self.expression(children[2]) if len(children) == 3 else "()"
            ty = unify(yes, no, expr.token)
        elif kind in {"match", "if_let"}:
            from .patterns import check_pattern

            if kind == "if_let":
                from .conditional_patterns import normalize_if_let
                normalize_if_let(expr)
            from .call_inference import partial, pending_integer
            # A place scrutinee is inspected in place; only its by-value
            # bindings move (checked per arm below).
            scrutinee_ty = partial(self.expression(children[0], place_context=self.place(children[0]) is not None))
            if kind == "if_let" and scrutinee_ty == NEVER:
                raise expr.token.error("diverging if let scrutinees require additional never-type analysis, which is not implemented")
            # Ordinary bindings/wildcards can also carry unresolved struct
            # slots into arm field access, result constraints and later uses.
            # Keep conditional inference behind its existing capability gate.
            if (isinstance(scrutinee_ty, TypeApplication) and pending_integer(scrutinee_ty)
                    and kind != "match"):
                raise expr.token.error("matching an unresolved inferred generic type requires later pattern constraints, which is not implemented")
            ty = NEVER
            for pattern, arm in zip(expr.value, children[1:]):
                self.scopes.append({})
                check_pattern(self, pattern, scrutinee_ty, allow_inferred=kind == "match")
                self.binding_moves(children[0], pattern)
                if arm.kind == "match_guard":
                    # The guard sees the arm's bindings and must be bool. Its
                    # borrow rules are owned by the move checker (GuardScope).
                    guard, body = arm.children
                    unify("bool", self.expression(guard), guard.token)
                    arm.ty = self.expression(body)
                    ty = unify(ty, arm.ty, body.token)
                else:
                    ty = unify(ty, self.expression(arm), arm.token)
                self.scopes.pop()
        elif kind == "for":
            # Moves of the array and across iterations, and shared loans in
            # the body, are owned by the move checker's loop fixed point.
            from .call_inference import partial
            source = partial(self.expression(children[0]))
            if not isinstance(source, FixedArrayType):
                raise expr.token.error("for supports concrete owned arrays only; borrowed/custom iterators and traits are not implemented")
            pattern = expr.value
            self.scopes.append({})
            if pattern.kind == "binding":
                expr.binding = self.bind(pattern.token, source.element, pattern.value)
            from .loops import LoopContext
            self.loops.append(LoopContext('for'))
            try:
                unify("()", self.expression(children[1]), children[1].token)
            finally:
                self.loops.pop()
                self.scopes.pop()
            ty = "()"
        elif kind in {"for_range", "for_range_inclusive"}:
            # core::ops::Range / RangeInclusive iteration over one integer type
            # (Step). Bounds are evaluated once, start first; the binding takes
            # the unified type. Validation requires a resolved integer type.
            bound = unify(self.expression(children[0]), self.expression(children[1]), expr.token)
            pattern = expr.value
            self.scopes.append({})
            if pattern.kind == "binding":
                expr.binding = self.bind(pattern.token, bound, pattern.value)
            from .loops import LoopContext
            self.loops.append(LoopContext('for'))
            try:
                unify("()", self.expression(children[2]), children[2].token)
            finally:
                self.loops.pop()
                self.scopes.pop()
            ty = "()"
        elif kind == "while":
            unify("bool", self.expression(children[0]), children[0].token)
            from .loops import LoopContext
            self.loops.append(LoopContext('while'))
            try:
                body = self.expression(children[1])
            finally:
                self.loops.pop()
            unify("()", body, children[1].token)
            ty = "()"
        elif kind == "while_let":
            from .call_inference import partial, pending_integer
            from .loops import LoopContext
            from .patterns import check_pattern

            scrutinee = partial(self.expression(children[0], place_context=self.place(children[0]) is not None))
            if scrutinee == NEVER:
                raise expr.token.error("diverging while let scrutinees require additional never-type analysis, which is not implemented")
            if isinstance(scrutinee, TypeApplication) and pending_integer(scrutinee):
                raise expr.token.error("matching an unresolved inferred generic type requires later pattern constraints, which is not implemented")
            # The scrutinee is outside the pattern's binding scope. Bindings
            # are visible only in the body, including across a continue edge.
            self.scopes.append({})
            self.loops.append(LoopContext('while_let'))
            try:
                check_pattern(self, expr.value, scrutinee)
                self.binding_moves(children[0], expr.value)
                unify("()", self.expression(children[1]), children[1].token)
            finally:
                self.loops.pop()
                self.scopes.pop()
            ty = "()"
        elif kind == "loop":
            from .loops import LoopContext
            context = LoopContext('loop')
            self.loops.append(context)
            try:
                unify("()", self.expression(children[0]), children[0].token)
            finally:
                self.loops.pop()
            # A nested loop/while/for owns its own breaks. Only a break
            # associated with this loop turns its never type into unit.
            ty = "()" if context.has_break else NEVER
        elif kind == "return":
            value_ty = self.expression(children[0]) if children else "()"
            if children and self.coercible(value_ty, self.result, children[0].token):
                children[0] = self.implicit_reborrow(children[0], self.result) or Expr(
                    "reference_coerce", children[0].token, children=[children[0]], ty=self.result)
                value_ty = self.result
            unify(self.result, value_ty, expr.token)
            ty = NEVER
        elif kind in {"break", "continue"}:
            if not self.loops:
                raise expr.token.error(f"{kind} outside a loop")
            if kind == "break":
                self.loops[-1].has_break = True
            ty = NEVER
        elif kind == "try":
            # Its operand is a core Try implementor (Result, Option, ...); the
            # early return converts errors through From. No core model exists.
            raise expr.token.error("the ? operator requires owned core Try/Result/Option and From lowering, "
                                   "which is not implemented")
        elif kind in STANDARD_MACRO_NODES:
            ty = self.standard_macro(expr)
        elif kind == "closure":
            # A closure has a unique anonymous type implementing Fn/FnMut/FnOnce
            # from its captures and body; none of that model exists yet.
            raise expr.token.error("closure expressions require closure types, captures and Fn-family "
                                   "closure traits, which are not implemented")
        else:
            raise expr.token.error(f"unsupported semantic node {kind}")
        expr.ty = ty
        return ty

    @contextmanager
    def constant_evaluation(self):
        """Evaluate a value rustc's const evaluator must produce."""
        self.const_evaluation += 1
        try:
            yield
        finally:
            self.const_evaluation -= 1

    def shift_overflow(self, expr: Expr, amount, amount_ty, ty) -> bool:
        """Whether a shift by a folded amount overflows. Mandatory constant
        evaluation rejects it (rustc E0080). In a body the value is left
        unfolded: KnownPanics judges it there, only where it is reachable."""
        if amount is None or not shift_overflows(amount, amount_ty, ty, self.pointer_bits):
            return False
        if self.evaluating_consts or self.const_evaluation:
            raise expr.token.error("evaluation of constant value failed: attempt to shift with overflow "
                                   "(rustc E0080)")
        return True

    def runtime_gap(self, expr: Expr, message: str) -> None:
        """A runtime model a reachable expansion needs; code that is never
        emitted (a disabled debug_assert!) is checked without it."""
        if not self.suppressed_runtime:
            raise expr.token.error(message)

    def format_arguments(self, expr: Expr) -> None:
        """A format_args node's arguments are borrowed (place context); their
        formatting traits are checked once types are resolved (validate)."""
        for child in expr.children:
            self.expression(child, place_context=self.place(child) is not None)
        for piece in expr.value.pieces:
            if isinstance(piece, str):
                continue
            for count in (piece.width, piece.precision):
                if count is not None and count.argument:
                    unify("usize", expr.children[count.value].ty, expr.children[count.value].token)

    def standard_macro(self, expr: Expr) -> object:
        """The core/std macro expansions of std_macros.py."""
        kind, children = expr.kind, expr.children
        if kind == "debug_assert":
            if expr.value:
                self.expression(children[0])
            else:
                self.suppressed_runtime += 1
                try:
                    self.expression(children[0])
                finally:
                    self.suppressed_runtime -= 1
            return "()"
        if kind == "panic":
            self.format_arguments(children[0])
            self.runtime_gap(expr, f"{expr.value}! needs owned panic support, which is not implemented "
                                   "(plan step 5)")
            return NEVER
        if kind == "assert_compare":
            left = self.expression(children[0], place_context=self.place(children[0]) is not None)
            right = self.expression(children[1], place_context=self.place(children[1]) is not None)
            unify(left, right, expr.token)
            if len(children) == 3:
                self.format_arguments(children[2])
            self.runtime_gap(expr, "assert_eq!/assert_ne! need owned panic support and core::fmt::Debug "
                                   "messages, which are not implemented (plan steps 5 and 9)")
            return "()"
        if kind == "format_args":
            self.format_arguments(expr)
            raise expr.token.error("format_args! values are core::fmt::Arguments, which is not implemented "
                                   "(plan step 9)")
        if kind == "format":
            self.format_arguments(children[0])
            raise expr.token.error("format! needs alloc::string::String and core::fmt, which are not "
                                   "implemented (plan steps 7 and 9)")
        if kind == "write_fmt":
            self.expression(children[0], place_context=self.place(children[0]) is not None)
            self.format_arguments(children[1])
            raise expr.token.error(f"{expr.value}! needs core::fmt::Write or std::io::Write formatting, which "
                                   "is not implemented (plan step 9)")
        # vec_macro
        for child in children:
            self.expression(child)
        raise expr.token.error("vec! needs alloc::vec::Vec, which is not implemented (plan step 7)")

    def require_static_divisor(self, expr: Expr, divisor, ty) -> None:
        """Until owned panics exist, `/` and `%` (and `/=`, `%=`) need a known
        divisor that cannot panic: nonzero, and not -1 for a signed type."""
        if divisor is None or divisor == 0 or (ty.startswith("i") and divisor == -1):
            raise expr.token.error("panic-capable division/remainder is unsupported; divisor must be a nonzero "
                                   "constant, not signed -1")

    def formatting_traits(self, expr: Expr) -> None:
        """Each placeholder's core::fmt trait for its resolved argument type:
        primitives as core implements them, references through them; other
        types need the owned fmt trait model."""
        for piece in expr.value.pieces:
            if isinstance(piece, str):
                continue
            argument = expr.children[piece.argument]
            ty = argument.ty
            while isinstance(ty, SharedReference) and piece.trait in ("Display", "Debug"):
                ty = ty.referent
            if ty in INTEGERS:
                continue
            if piece.trait in ("Display", "Debug") and (ty == "bool" or ty == STATIC_STR or ty == "str"):
                continue
            if piece.trait == "Debug" and ty == "()":
                continue
            raise argument.token.error(f"formatting {ty} with {{:{_TRAIT_SPELLING[piece.trait]}}} needs the "
                                       "owned core::fmt trait model, which is not implemented (plan step 9)")

    def shift_operands(self, expr: Expr, op: str) -> None:
        """Both shift operands must be integers: core implements Shl/Shr for
        every pair of integer types; bool has none (rustc E0369). User
        operator-trait implementations are not modeled."""
        for operand in expr.children:
            if operand.ty not in INTEGERS:
                reason = ("(rustc E0369)" if operand.ty == "bool"
                          else "user operator trait implementations are not implemented")
                raise expr.token.error(f"operator {op!r} does not accept {operand.ty}: no implementation "
                                       f"for {expr.children[0].ty} {op} {expr.children[1].ty} {reason}")

    def validate(self, expr: Expr) -> int | None:
        # Resolve only after the complete function has contributed constraints:
        # `let x = 1; takes_i64(x)` must infer i64, not prematurely default i32.
        expr.ty = resolve(expr.ty)
        if isinstance(expr.ty, (TypeApplication, FixedArrayType)):
            self.valid_type(expr.ty, expr.token)
        values = [self.validate(child) for child in expr.children if not (expr.kind == "call" and child is expr.children[0])]
        if isinstance(expr.binding, Binding):
            expr.binding.ty = resolve(expr.binding.ty)
            if expr.kind == "let":
                self.standard_markers.require_sized(expr.binding.ty, expr.token)
        if expr.kind == "pattern_let":
            from .patterns import validate_pattern
            validate_pattern(self, expr.value[0])
        kind, op = expr.kind, expr.value
        if kind == "call":
            # Bodyless required signatures may mention unsized Self or an
            # associated projection by value. An actual call still needs
            # storage, even when its result is discarded. Do not impose this
            # on a borrowed place's referent, which need not be Sized.
            self.standard_markers.require_sized(expr.ty, expr.token)
            for operand in expr.children[1:]:
                self.standard_markers.require_sized(operand.ty, operand.token)
        if kind == "for":
            from .iteration import validate_iteration
            validate_iteration(self, expr)
        if kind in {"for_range", "for_range_inclusive"} and expr.children[0].ty not in INTEGERS:
            raise expr.token.error("range iteration over " + str(expr.children[0].ty)
                                   + " requires a Step implementation; only integer ranges are implemented")
        if kind == "match":
            from .patterns import validate_match

            validate_match(self, expr)
        if kind == "while_let":
            from .patterns import validate_pattern, validate_pattern_scrutinee

            validate_pattern_scrutinee(self, expr.children[0].ty, expr.token)
            validate_pattern(self, expr.value)
        if kind == "array_repeat" and expr.value > 1 and not self.copy_type(expr.ty.element):
            raise expr.token.error("array repeat operands of length greater than one require Copy; const-item/block repetition and trait bounds are not implemented")
        if kind == "index":
            base = expr.children[0].ty
            array = base.referent if isinstance(base, SharedReference) else base
            index = values[1]
            if type(index) is not int:
                raise expr.token.error("dynamic array bounds/panic support is not implemented")
            if not 0 <= index < array.length:
                raise expr.token.error("constant array index is out of bounds")
            expr.value = index
        if kind == "variant":
            return expr.value
        if kind == "const":
            return expr.binding.value
        if kind == "static":
            # A static is a place. Only while evaluating a const/static
            # initializer may an immutable static's value be read (rustc
            # accepts it); PCC's static types have no interior mutability.
            if self.evaluating_consts and not expr.binding.mutable:
                return expr.binding.value
            return None
        if kind == "group":
            return values[0]
        if kind in {"bool", "character"}:
            return expr.value
        if kind == "block" and expr.value == 0 and values:
            return values[0]
        if kind == "integer":
            low, high = bounds(expr.ty, self.pointer_bits)
            if expr.binding == "negative-literal" and expr.ty.startswith("i"):
                high += 1
            if not low <= op <= high:
                raise expr.token.error(f"integer literal out of range for {expr.ty}")
            return op
        if kind == "unary":
            operand_ty = expr.children[0].ty
            if op == "-" and operand_ty not in {"i32", "i64", "isize", NEVER}:
                raise expr.token.error("unary '-' requires a signed integer")
            if op == "!" and operand_ty not in INTEGERS | {"bool", NEVER}:
                raise expr.token.error("unary '!' requires bool or integer")
            if op == "-" and values[0] is not None:
                value = -values[0]
                if not bounds(expr.ty, self.pointer_bits)[0] <= value <= bounds(expr.ty, self.pointer_bits)[1]:
                    raise expr.token.error("constant integer overflow")
                return value
            if op == "!" and values[0] is not None:
                return not values[0] if expr.ty == "bool" else cast_constant(~values[0], expr.ty, self.pointer_bits)
        if kind == "cast":
            if expr.ty == RUST_CHAR and expr.children[0].ty in {RUST_CHAR, RUST_U8}:
                return values[0]
            if expr.ty not in INTEGERS or (expr.children[0].ty not in INTEGERS | {"bool", RUST_CHAR} and expr.children[0].ty not in self.enums):
                raise expr.token.error("supported casts are integer-to-integer, bool-to-integer and unit-only-enum-to-integer")
            if expr.children[0].ty in self.enums and self.enums[expr.children[0].ty].payloads:
                raise expr.token.error("an enum with payload fields cannot be cast to an integer")
            if values[0] is not None:
                return cast_constant(int(values[0]), expr.ty, self.pointer_bits)
        if kind == "format_args":
            self.formatting_traits(expr)
        if kind == "binary" and op in SHIFTS:
            self.shift_operands(expr, op)
            left, right = values
            if self.shift_overflow(expr, right, expr.children[1].ty, expr.ty) or None in values:
                return None
            return shift_value(op, left, right, expr.ty, self.pointer_bits)
        if kind == "assign" and op[:-1] in SHIFTS:
            self.shift_operands(expr, op[:-1])
            return None
        if kind == "binary":
            operand_ty = expr.children[0].ty
            allowed = INTEGERS | {"bool"} if op in {"&", "|", "^", "==", "!=", "<", "<=", ">", ">=", "&&", "||"} else INTEGERS
            if op in {"==", "!=", "<", "<=", ">", ">="}:
                allowed = allowed | {RUST_CHAR}
            if operand_ty not in allowed:
                raise expr.token.error(f"operator {op!r} does not accept {operand_ty}")
            if op in {"/", "%"}:
                self.require_static_divisor(expr, values[1], operand_ty)
            if op in {"+", "-", "*"} and all(value is not None for value in values):
                left, right = values
                value = left + right if op == "+" else left - right if op == "-" else left * right
                if not bounds(operand_ty, self.pointer_bits)[0] <= value <= bounds(operand_ty, self.pointer_bits)[1]:
                    raise expr.token.error("constant integer overflow")
                return value
            if all(value is not None for value in values):
                left, right = values
                if op in {"/", "%"}:
                    quotient = abs(left) // abs(right)
                    if (left < 0) != (right < 0):
                        quotient = -quotient
                    return quotient if op == "/" else left - quotient * right
                if op in {"&", "|", "^"}:
                    return left & right if op == "&" else left | right if op == "|" else left ^ right
                if op in {"&&", "||"}:
                    return left and right if op == "&&" else left or right
                if op in {"==", "!=", "<", "<=", ">", ">="}:
                    return {"==": left == right, "!=": left != right, "<": left < right,
                            "<=": left <= right, ">": left > right, ">=": left >= right}[op]
        if kind == "assign" and op != "=":
            operand_ty = expr.children[0].ty
            # core implements BitAndAssign/BitOrAssign/BitXorAssign for bool too.
            if operand_ty not in INTEGERS and not (operand_ty == "bool" and op in {"&=", "|=", "^="}):
                raise expr.token.error(f"operator {op!r} requires integer operands")
            if op in {"/=", "%="}:
                self.require_static_divisor(expr, values[1], operand_ty)
        return None
