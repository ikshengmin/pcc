"""Name resolution, monomorphic scalar inference, and Rust type checking."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .syntax import ConstItem, Enumeration, Expr, Function, Record, RustError, Token
from .types import (
    INTEGER_TYPES, FixedArrayType, SharedReference, TupleType, TypeApplication, TypeParameter, integer_bits,
)

INTEGERS = INTEGER_TYPES
FFI_INTEGERS = frozenset({"i32", "i64", "u32", "u64"})
NEVER = "!"


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
    match = re.fullmatch(r"(0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*)(i32|i64|u32|u64|isize|usize)?", str(expr.value))
    if match is None:
        raise expr.token.error("invalid or unsupported integer literal (supported suffixes: i32, i64, u32, u64, isize, usize)")
    raw = match.group(1).replace("_", "")
    try:
        value = int(raw, 0 if raw.startswith(("0x", "0o", "0b")) else 10)
    except ValueError as exc:
        raise expr.token.error("invalid integer literal") from exc
    return value, match.group(2) or InferredInteger()


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
            # Lifetimes are erased; only the matching actual rustc check
            # establishes that the impl's region constraints hold.
            if implementation.lifetime_parameters and not externally_checked:
                raise implementation.lifetime_parameters[0].error(
                    "explicit impl lifetimes require a matching external rustc check record")
        from .traits import StaticTraits
        self.traits = StaticTraits(self, traits, implementations)
        from .standard_markers import StandardMarkers
        self.standard_markers = StandardMarkers(self, crate_policy)

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
        if self.constant_named(token.text, token) is not None:
            raise token.error(f'{token.text!r} names a constant: constant patterns and bindings that '
                              'would shadow a constant are not implemented')
        self.source_type_access(ty, token)
        self.serial += 1
        binding = Binding(token.text, ty, mutable, self.serial, parameter)
        self.scopes[-1][token.text] = binding
        return binding

    def constant_named(self, name, token):
        """The const item a bare value name denotes in the current module."""
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
            ty = self.generics.normalize(const.ty, const.token)
            if ty not in INTEGERS | {"bool"}:
                raise const.token.error(f'const items of type {ty} are not implemented; integer and bool const items are')
            pending, count = [const.expression], 0
            while pending:
                node = pending.pop()
                count += 1
                if count > 1024:
                    raise node.token.error('const initializer exceeds implementation limit (1024 nodes)')
                if node.kind not in {'integer', 'bool', 'group', 'unary', 'binary', 'cast', 'name', 'variant', 'const'}:
                    raise node.token.error('const initializers support literals, operators, casts and other const '
                                           'items; calls, blocks and other expressions are not implemented')
                pending.extend(node.children)
            unify(ty, self.expression(const.expression), const.expression.token)
            value = self.validate(const.expression)
            if value is None:
                raise const.expression.token.error('const initializer is not a closed constant expression')
            const.ty, const.value, const.evaluated = ty, value, True
            return const
        finally:
            self.evaluating_consts.pop()
            self.scopes, self.current_module, self.type_scope, self.abstract_function = saved

    def local(self, name: str) -> Binding | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
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
        # Named Rust structs do NOT become Copy just because their fields are.
        return ty in INTEGERS | {"bool", "()"}

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
                raise token.error("explicit reference lifetimes require a matching external rustc check record")
            if ty.mutable and not self.externally_checked:
                raise token.error("mutable references require a matching external rustc check record")
            if not allow_reference:
                raise token.error("reference returns and aggregate reference storage require lifetime analysis, which is not implemented")
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
        if ty in INTEGERS | {"bool", "()"}:
            return
        if ty in self.enums:
            if ty in stack:
                raise token.error("recursive aggregate has infinite size")
            for fields in self.enums[ty].payloads.values():
                for field_ty in fields:
                    self.valid_type(field_ty, token, stack + (ty,), allow_reference=False)
            return
        if ty not in self.records:
            raise token.error(f"unsupported Rust type {ty!r}")
        if ty in stack:
            raise token.error("recursive aggregate has infinite size")
        record = self.records[ty]
        if not record.fields:
            raise record.token.error("zero-field structs are not implemented")
        names = set()
        for field, field_ty in record.fields:
            if field.text in names:
                raise field.error("duplicate struct field")
            names.add(field.text)
            self.valid_type(field_ty, field, stack + (ty,), allow_reference=False)

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

    def through_reference(self, expr: Expr) -> bool:
        if expr.kind == "deref":
            return True
        if expr.kind == "group":
            return self.through_reference(expr.children[0])
        if expr.kind in {"field", "index"}:
            return isinstance(expr.children[0].ty, SharedReference) or self.through_reference(expr.children[0])
        return False

    def through_array_index(self, expr):
        return expr.kind == "index" or expr.kind in {"group", "field"} and self.through_array_index(expr.children[0])

    def mutable_place(self, expr: Expr) -> bool:
        """Mutability/addressability only; lifetime and alias checking is external."""
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
        self.traits.prepare()
        self.methods.prepare()
        # rustc evaluates every free const item, used or not.
        for const in self.consts:
            self.evaluate_const(const, const.token)
        declarations = tuple(self.functions)
        for fn in declarations:
            if fn.item_identity is not None:
                self.current_module = fn.item_identity.module
            if fn.explicit_receiver and not self.externally_checked:
                raise fn.token.error("explicit receiver types require a matching external rustc check record")
            if fn.lifetime_parameters and not self.externally_checked:
                raise fn.token.error("explicit function lifetimes require a matching external rustc check record")
            self.type_scope = self.function_generics.prepare(fn)
            self.abstract_function = bool(fn.type_parameters)
            fn.result = self.generics.normalize(fn.result, fn.token)
            fn.params = [(token, self.generics.normalize(ty, token), mutable)
                         for token, ty, mutable in fn.params]
            self.valid_type(fn.result, fn.token, allow_reference=False)
            parameter_names = set()
            for token, ty, _ in fn.params:
                if token.text in parameter_names:
                    raise token.error(f"duplicate parameter {token.text!r}")
                parameter_names.add(token.text)
                self.valid_type(ty, token)
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
                if not self.externally_checked:
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
        if fn.item_identity is not None:
            self.current_module = fn.item_identity.module
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
            unify(fn.result, self.expression(fn.body), fn.body.token)
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
            if expr.value and not self.externally_checked:
                raise expr.token.error("mutable borrowing requires a matching external rustc check record")
            if not allow_borrow and not self.externally_checked:
                raise expr.token.error("local borrowing is supported only as a direct shared-reference call argument; reference storage/escape lifetime analysis is not implemented")
            referent = self.expression(children[0], place_context=True)
            if self.through_array_index(children[0]) and not self.externally_checked:
                raise expr.token.error("indexed borrowing requires an actual external rustc check; legacy partial loans do not prove array disjointness")
            if self.place(children[0]) is None:
                raise expr.token.error("shared borrowing of temporaries requires lifetime extension, which is not implemented")
            if expr.value and not self.mutable_place(children[0]):
                raise expr.token.error("cannot borrow an immutable place as mutable")
            ty = SharedReference(referent, bool(expr.value))
            self.valid_type(ty, expr.token)
        elif kind == "deref":
            pointer = self.expression(children[0])
            if not isinstance(pointer, SharedReference):
                raise expr.token.error("dereference requires an admitted shared reference; raw pointers are not implemented")
            ty = pointer.referent
            if not place_context and not self.copy_type(ty):
                raise expr.token.error("cannot move a non-Copy value out of a shared reference")
        elif kind == "bool":
            ty = "bool"
        elif kind == "variant" and self.constant_path(expr.value[0], expr.value[1], expr.token) is not None:
            const = self.evaluate_const(self.constant_path(expr.value[0], expr.value[1], expr.token), expr.token)
            expr.kind, expr.binding, expr.value = "const", const, None
            ty = const.ty
        elif kind == "const":
            ty = expr.binding.ty
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
                    unify(annotation, init_ty, expr.token)
                    from .call_inference import partial
                    init_ty = partial(annotation)
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
            binding = self.local(str(expr.value))
            const = self.constant_named(str(expr.value), expr.token) if binding is None else None
            if const is not None:
                self.evaluate_const(const, expr.token)
                expr.kind, expr.binding, expr.value = "const", const, None
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
                if isinstance(array_type, FixedArrayType) and callee.value == "len":
                    if explicit_arguments:
                        raise callee.token.error("array len is not a generic method")
                    if len(children) != 1:
                        raise callee.token.error("array len expects no arguments")
                    expr.kind, expr.value, expr.children, expr.ty = "array_len", array_type.length, [receiver], "usize"
                    return "usize"
                fn, fixed_arguments = pinned_method or self.methods.lookup(receiver_type, callee.value, callee.token, receiver=True)
                if isinstance(fn.params[0][1], SharedReference) and not isinstance(receiver_type, SharedReference):
                    if self.through_array_index(receiver) and not self.externally_checked:
                        raise receiver.token.error("indexed receiver borrowing requires an actual external rustc check; legacy partial loans do not prove array disjointness")
                    if self.place(receiver) is None:
                        raise receiver.token.error("shared receiver borrowing of temporaries requires lifetime extension, which is not implemented")
                    if fn.params[0][1].mutable and not self.mutable_place(receiver):
                        raise receiver.token.error("cannot borrow an immutable receiver as mutable")
                    receiver = Expr("borrow", receiver.token, fn.params[0][1].mutable, children=[receiver], ty=SharedReference(receiver_type, fn.params[0][1].mutable))
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
                if callee.kind != "name" or self.local(name) is not None:
                    raise callee.token.error("only unshadowed direct function calls are supported")
                fn = (self.module_resolution.function(name, self.current_module, callee.token)
                      if self.module_resolution is not None else self.names.get(name))
                if fn is None and name in self.const_names:
                    raise callee.token.error(f'expected function, found constant {name!r} (rustc E0618)')
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
                if (isinstance(param_ty, SharedReference) and isinstance(arg_ty, SharedReference)
                        and not param_ty.mutable and arg_ty.mutable):
                    unify(param_ty.referent, arg_ty.referent, arg.token)
                    children[index + 1] = Expr("reference_coerce", arg.token, children=[arg], ty=param_ty)
                else:
                    unify(param_ty, arg_ty, arg.token)
            ty = fn.result
        elif kind == "unary":
            ty = self.expression(children[0])
            literal = children[0]
            while literal.kind == "group":
                literal = literal.children[0]
            if expr.value == "-" and literal.kind == "integer":
                literal.binding = "negative-literal"
        elif kind == "binary":
            left = self.expression(children[0])
            right = self.expression(children[1])
            common = unify(left, right, expr.token)
            if expr.value in {"&&", "||"}:
                unify("bool", common, expr.token)
                ty = "bool"
            elif expr.value in {"==", "!=", "<", "<=", ">", ">="}:
                ty = "bool"
            else:
                ty = common
        elif kind == "assign":
            lhs = children[0]
            target = self.expression(lhs, place_context=True)
            place = self.place(lhs)
            if place is None:
                raise lhs.token.error("assignment target must be a local binding or field")
            if not self.mutable_place(lhs):
                raise lhs.token.error("cannot assign through a shared reference" if self.through_reference(lhs) else f"cannot assign to immutable binding {place[0].name!r}")
            if not self.externally_checked and place[0].parameter and isinstance(target, SharedReference):
                raise lhs.token.error("reference parameter reassignment requires lifetime constraints, which are not implemented")
            unify(target, self.expression(children[1]), expr.token)
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
            scrutinee_ty = partial(self.expression(children[0]))
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
                ty = unify(ty, self.expression(arm), arm.token)
                self.scopes.pop()
        elif kind == "for":
            if not self.externally_checked:
                raise expr.token.error("for ownership/borrowing admission requires an actual external rustc check")
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

            scrutinee = partial(self.expression(children[0]))
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
            unify(self.result, self.expression(children[0]) if children else "()", expr.token)
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
        elif kind == "closure":
            # A closure has a unique anonymous type implementing Fn/FnMut/FnOnce
            # from its captures and body; none of that model exists yet.
            raise expr.token.error("closure expressions require closure types, captures and Fn-family "
                                   "closure traits, which are not implemented")
        else:
            raise expr.token.error(f"unsupported semantic node {kind}")
        expr.ty = ty
        return ty

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
        if kind == "group":
            return values[0]
        if kind == "bool":
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
            if expr.ty not in INTEGERS or (expr.children[0].ty not in INTEGERS | {"bool"} and expr.children[0].ty not in self.enums):
                raise expr.token.error("supported casts are integer-to-integer, bool-to-integer and unit-only-enum-to-integer")
            if expr.children[0].ty in self.enums and self.enums[expr.children[0].ty].payloads:
                raise expr.token.error("an enum with payload fields cannot be cast to an integer")
            if values[0] is not None:
                return cast_constant(int(values[0]), expr.ty, self.pointer_bits)
        if kind == "binary":
            operand_ty = expr.children[0].ty
            allowed = INTEGERS | {"bool"} if op in {"&", "|", "^", "==", "!=", "<", "<=", ">", ">=", "&&", "||"} else INTEGERS
            if operand_ty not in allowed:
                raise expr.token.error(f"operator {op!r} does not accept {operand_ty}")
            if op in {"/", "%"}:
                divisor = values[1]
                if divisor is None or divisor == 0 or (operand_ty.startswith("i") and divisor == -1):
                    raise expr.token.error("panic-capable division/remainder is unsupported; divisor must be a nonzero constant, not signed -1")
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
        if kind == "assign" and op != "=" and expr.children[0].ty not in INTEGERS:
            raise expr.token.error(f"operator {op!r} requires integer operands")
        return None
