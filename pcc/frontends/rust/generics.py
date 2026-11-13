"""Declaration-checked generic data, not deferred generic function bodies.

Every declaration is checked symbolically, even if never instantiated. Concrete
instances keep nominal identity and reuse ordinary fields, moves, patterns and
target IR layout. Instance caches belong to one Checker, never a global runtime
or a cross-target cache. Trait obligations, lifetimes and Drop remain gates.
"""
from dataclasses import replace

from pcc.support.instantiation import InstanceRegistry

from .syntax import Enumeration
from .types import (
    INTEGER_TYPES, SLICE_GAP, STATIC_STR, FixedArrayType, SharedReference, SliceType, TupleType, TypeApplication,
    TypeParameter, is_static_str,
)

PRIMITIVES = INTEGER_TYPES | {"bool", "()"}
MAX_TYPE_NODES = 128
MAX_LAYOUT_STEPS = 4096
MAX_INSTANCES = 128


def children(ty):
    if isinstance(ty, FixedArrayType):
        return (ty.element,)
    if isinstance(ty, TupleType):
        return ty.elements
    if isinstance(ty, TypeApplication):
        return ty.arguments
    if isinstance(ty, SharedReference):
        return (ty.referent,)
    if isinstance(ty, SliceType):
        return (ty.element,)
    return ()


def bounded_type(ty, token):
    # Bound logical structure before hashing/equality of recursive dataclasses.
    # Shared subtrees still count each occurrence; expanding A<(T,T)> cannot
    # silently create an exponential hash/substitution workload.
    pending, count = [ty], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_TYPE_NODES:
            raise token.error("generic type structure exceeds implementation limit (128 nodes)")
        pending.extend(children(item))


def substitute(ty, bindings):
    if isinstance(ty, FixedArrayType):
        return FixedArrayType(substitute(ty.element, bindings), ty.length)
    if isinstance(ty, TypeParameter):
        return bindings.get(ty, ty)
    if isinstance(ty, TupleType):
        return TupleType(tuple(substitute(t, bindings) for t in ty.elements))
    if isinstance(ty, TypeApplication):
        return TypeApplication(ty.name, tuple(substitute(t, bindings) for t in ty.arguments))
    if isinstance(ty, SharedReference):
        return SharedReference(substitute(ty.referent, bindings), ty.mutable, ty.lifetime)
    return ty


class GenericDeclarations:
    def __init__(self, checker):
        self.checker = checker
        self.definitions = dict(checker.records, **checker.enums)
        self.enum_definitions = tuple(checker.enums.values())
        self.parameters = {}
        self.instances = InstanceRegistry()

    def canonical_name(self, name, token, scope=None):
        scope = self.checker.type_scope if scope is None else scope
        if name in scope or name in PRIMITIVES or name in self.definitions:
            return name
        if isinstance(name, str) and '::' in name and name.split('::', 1)[0] in scope:
            raise token.error('associated types on generic parameters are not implemented')
        resolution = self.checker.module_resolution
        return resolution.type_name(name, self.checker.current_module, token) if resolution is not None else name

    def normalize(self, ty, token, scope=None, *, placeholder_context=None):
        scope = self.checker.type_scope if scope is None else scope
        bounded_type(ty, token)
        if ty == "_":
            if placeholder_context is None:
                raise token.error("unsupported Rust type '_': inferred type placeholders are implemented only in function/method call type arguments, local annotations, struct/enum constructors and ordinary enum patterns")
            from .semantics import InferredType
            return InferredType(token, description=placeholder_context + " type placeholder")
        if isinstance(ty, FixedArrayType):
            from .arrays import length_value
            return FixedArrayType(self.normalize(ty.element, token, scope, placeholder_context=placeholder_context),
                                  length_value(self.checker, ty.length, token))
        if isinstance(ty, TupleType):
            return TupleType(tuple(self.normalize(t, token, scope, placeholder_context=placeholder_context)
                                   for t in ty.elements))
        if is_static_str(ty):
            return STATIC_STR
        if isinstance(ty, SliceType):
            raise (ty.token or token).error(SLICE_GAP)
        if isinstance(ty, SharedReference):
            return SharedReference(self.normalize(ty.referent, token, scope, placeholder_context=placeholder_context),
                                   ty.mutable, ty.lifetime)
        if isinstance(ty, TypeApplication):
            lifetimes = ty.lifetimes
            if lifetimes and not self.checker.externally_checked:
                raise token.error("explicit lifetime arguments require a matching external rustc check record")
            ty = TypeApplication(self.canonical_name(ty.name, token, scope), ty.arguments)
            if ty.name in scope or ty.name in PRIMITIVES:
                raise token.error("generic arguments require a named generic data declaration")
            declaration = self.definitions.get(ty.name)
            if declaration is None:
                raise token.error(f"unsupported Rust type {ty.name!r}")
            if len(lifetimes) > len(declaration.lifetime_parameters):
                raise token.error(f"{ty.name} takes {len(declaration.lifetime_parameters)} lifetime arguments "
                                  f"but {len(lifetimes)} were supplied (rustc E0107)")
            if not ty.arguments and not declaration.type_parameters:
                # Only lifetimes were written: after the actual check they are
                # erased, leaving the plain nominal type.
                return ty.name
            if len(ty.arguments) != len(declaration.type_parameters):
                raise token.error(f"generic argument count mismatch for {ty.name}")
            arguments = tuple(self.normalize(t, token, scope, placeholder_context=placeholder_context)
                              for t in ty.arguments)
            for argument in arguments:
                self.checker.standard_markers.require_sized(argument, token)
            return TypeApplication(ty.name, arguments)
        if isinstance(ty, TypeParameter):
            return ty
        if ty in scope:
            return scope[ty]
        if ty in PRIMITIVES:
            return ty
        ty = self.canonical_name(ty, token, scope)
        declaration = self.definitions.get(ty)
        if declaration is None:
            raise token.error(f"unsupported Rust type {ty!r}")
        if declaration.type_parameters:
            raise token.error(f"generic type {ty} requires explicit type arguments; constructor inference is not implemented")
        return ty

    def enum_constructor_type(self, owner, token):
        """Fresh holes belong to a constructor use, never a bare type name."""
        if isinstance(owner, str):
            owner = self.canonical_name(owner, token)
            declaration = self.definitions.get(owner)
            if (owner not in self.checker.type_scope
                    and isinstance(declaration, Enumeration) and declaration.type_parameters):
                owner = TypeApplication(owner, ("_",) * len(declaration.type_parameters))
        return self.normalize(owner, token, placeholder_context="enum constructor")

    def prepare(self):
        for name, declaration in self.definitions.items():
            if declaration.lifetime_parameters and not self.checker.externally_checked:
                raise declaration.lifetime_parameters[0].error(
                    "lifetime parameters on data declarations require a matching external rustc check record")
            if declaration.outlives_bounds and not self.checker.externally_checked:
                # Region constraints only: proven by the actual check, then erased.
                raise declaration.outlives_bounds[0][1][0].error(
                    "outlives bounds on data declarations require a matching external rustc check record")
            if declaration.item_identity is not None:
                self.checker.current_module = declaration.item_identity.module
            scope = {}
            for index, parameter in enumerate(declaration.type_parameters):
                if parameter.text in scope:
                    raise parameter.error("duplicate generic parameter")
                if parameter.text in PRIMITIVES:
                    raise parameter.error("generic parameters shadowing primitive names are not implemented")
                scope[parameter.text] = TypeParameter(name, index, parameter.text)
            self.parameters[name] = tuple(scope.values())
            if isinstance(declaration, Enumeration):
                declaration.payloads = {
                    variant: tuple(self.normalize(t, declaration.token, scope) for t in fields)
                    for variant, fields in declaration.payloads.items()
                }
                fields = [t for types in declaration.payloads.values() for t in types]
            else:
                declaration.fields = [(field, self.normalize(t, field, scope))
                                      for field, t in declaration.fields]
                names = [field.text for field, _ in declaration.fields]
                if len(set(names)) != len(names):
                    raise declaration.token.error("duplicate struct field")
                if not names:
                    raise declaration.token.error("zero-field structs are not implemented")
                fields = [t for _, t in declaration.fields]
            pending, used = list(fields), set()
            while pending:
                ty = pending.pop()
                if isinstance(ty, TypeParameter):
                    used.add(ty)
                pending.extend(children(ty))
            for parameter in scope.values():
                if parameter not in used:
                    raise declaration.token.error(f"unused generic parameter {parameter.name!r}")
        # Keep templates out of the concrete storage tables. They are checked
        # below even when no function or constructor ever requests an instance.
        self.checker.records = {n: d for n, d in self.checker.records.items() if not d.type_parameters}
        self.checker.enums = {n: d for n, d in self.checker.enums.items() if not d.type_parameters}

    def specialized_fields(self, ty):
        name = ty.name if isinstance(ty, TypeApplication) else ty
        declaration = self.definitions[name]
        bindings = dict(zip(self.parameters[name], ty.arguments)) if isinstance(ty, TypeApplication) else {}
        raw = ([t for fields in declaration.payloads.values() for t in fields]
               if isinstance(declaration, Enumeration) else [t for _, t in declaration.fields])
        return tuple(substitute(t, bindings) for t in raw)

    def specialized_payload(self, ty, variant):
        name = ty.name if isinstance(ty, TypeApplication) else ty
        fields = self.definitions[name].payloads.get(variant)
        if fields is None:
            return None
        bindings = dict(zip(self.parameters[name], ty.arguments)) if isinstance(ty, TypeApplication) else {}
        return tuple(substitute(field, bindings) for field in fields)

    def check_layouts(self):
        for name, declaration in self.definitions.items():
            start = TypeApplication(name, self.parameters[name]) if self.parameters[name] else name
            budget = MAX_LAYOUT_STEPS

            def visit(ty, stack=()):
                nonlocal budget
                budget -= 1
                if budget < 0 or len(stack) > 32:
                    raise declaration.token.error("recursive/growing generic layout exceeds implementation limit")
                bounded_type(ty, declaration.token)
                if isinstance(ty, TypeParameter) or ty in PRIMITIVES or ty == STATIC_STR:
                    return  # A `&'static str` field stores literal data only.
                if isinstance(ty, SharedReference):
                    raise declaration.token.error("aggregate reference storage requires lifetime analysis, which is not implemented")
                if isinstance(ty, FixedArrayType):
                    visit(ty.element, stack)
                    return
                if isinstance(ty, TupleType):
                    for element in ty.elements:
                        visit(element, stack)
                    return
                if ty in stack:
                    raise declaration.token.error("recursive aggregate has infinite size")
                for field in self.specialized_fields(ty):
                    visit(field, stack + (ty,))

            visit(start)

    def instantiate(self, ty, token):
        if not isinstance(ty, TypeApplication):
            return ty
        bounded_type(ty, token)
        declaration = self.definitions[ty.name]
        # Declaration identity, not equal physical layout, owns each instance.
        key = (id(declaration), ty.arguments)
        entry = self.instances.get(key)
        if entry is None:
            if len(self.instances) >= MAX_INSTANCES:
                raise token.error("generic data instances exceed implementation limit (128)")
            entry = self.instances.request(key, ty, token)
        # Data declarations are needed immediately for field/move checking.
        # The same session-owned scheduler serves C++'s deferred function work;
        # finite Rust storage was checked separately, not inferred from states.
        self.instances.drain(self._materialize)
        return entry.value

    def _materialize(self, entry):
        ty = entry.value
        declaration = self.definitions[ty.name]
        bindings = dict(zip(self.parameters[ty.name], ty.arguments))
        if isinstance(declaration, Enumeration):
            instance = replace(declaration, type_parameters=(),
                               discriminants=dict(declaration.discriminants),
                               payloads={v: tuple(substitute(t, bindings) for t in fields)
                                         for v, fields in declaration.payloads.items()})
            self.checker.enums[ty] = instance
        else:
            instance = replace(declaration, type_parameters=(),
                               fields=[(f, substitute(t, bindings)) for f, t in declaration.fields])
            self.checker.records[ty] = instance
        return instance

    def path_type(self, path, token, expected=None, *, allow_inferred=False):
        if allow_inferred and isinstance(path, TypeApplication):
            # Explicit pattern holes contribute constraints just like payload
            # patterns. Their slots must unify with the scrutinee before any
            # concrete nominal instance is registered by whole-body checking.
            return self.normalize(path, token, placeholder_context="enum pattern")
        if isinstance(path, str):
            path = self.canonical_name(path, token)
        # A pattern inherits the scrutinee's known nominal owner and shares
        # its unresolved slots. Payload/arm constraints can still solve them;
        # do not normalize fresh holes or register an unfinished instance.
        if (isinstance(expected, TypeApplication) and path == expected.name
                and self.definitions[path].type_parameters):
            if allow_inferred and path not in self.checker.type_scope:
                self.checker.valid_type(expected, token)
                return expected
            if not allow_inferred:
                from .call_inference import pending_integer
                if pending_integer(expected):
                    raise token.error("matching an unresolved inferred generic type requires later pattern constraints, which is not implemented")
            path = expected
        return self.instantiate(self.normalize(path, token), token)
