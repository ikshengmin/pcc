"""Go method sets and basic-interface satisfaction, without a runtime ABI.

Package-qualified identifiers, exact signatures, pointer receiver membership,
shallow shadowing and same-depth ambiguity are language facts. Neither an
addressable call nor a box address establishes interface implementation.
Storage, allocation, itables, tracing and interface calls have separate owners.
"""
from dataclasses import dataclass
import unicodedata

from pcc.frontends.go.diagnostics import GoDiagnostic
from pcc.frontends.go.records import (
    DefinedType, InterfaceMethod, InterfaceType, MethodSignature, StructType, underlying,
)
from pcc.frontends.go.types import PointerType


def member_key(name, package=""):
    return name, "" if unicodedata.category(name[0]) == "Lu" else (package or "<main>")


def interface_type(dtype):
    result = underlying(dtype)
    return result if isinstance(result, InterfaceType) else None


def basic_interface(explicit, embedded, token):
    """Merge completed embedded interfaces; explicit names remain unique.

    Signatures retain canonical named types, not their layouts. Overlapping
    embedded methods must agree, including parameter/result identities and
    variadicness. Resolution owns embedding cycles before calling this helper.
    """
    methods, declared = {}, set()
    for method in explicit:
        if method.name == "_":
            raise GoDiagnostic(token, "interface method name cannot be blank")
        key = member_key(method.name, method.package)
        if key in declared:
            raise GoDiagnostic(token, "duplicate interface method " + method.name)
        declared.add(key)
        methods[key] = InterfaceMethod(method.name, key[1], method.signature)
    for dtype in embedded:
        owner = interface_type(dtype)
        if owner is None:
            raise GoDiagnostic(token, "embedded interface element must denote an interface type")
        for method in owner.methods:
            key = member_key(method.name, method.package)
            previous = methods.get(key)
            if previous is not None and previous.signature != method.signature:
                raise GoDiagnostic(token, "conflicting embedded interface method " + method.name)
            methods[key] = InterfaceMethod(method.name, key[1], method.signature)
    return InterfaceType(tuple(methods[key] for key in sorted(methods)))


@dataclass(frozen=True)
class MethodSelection:
    method: InterfaceMethod
    implementation: object
    field_path: tuple = ()
    indirect: bool = False


@dataclass(frozen=True)
class MethodSet:
    selections: tuple = ()
    blocked: tuple = ()

    def lookup(self, name, package=""):
        key = member_key(name, package)
        return next((item for item in self.selections
                     if member_key(item.method.name, item.method.package) == key), None)


@dataclass(frozen=True)
class _Search:
    type: object
    path: tuple = ()
    indirect: bool = False
    multiple: bool = False


def method_set(dtype):
    """Compute a method set, never the extra calls allowed by addressability.

    Each breadth-first layer consolidates repeated types while retaining path
    multiplicity. A field or collision at a shallower layer blocks a method
    below it. Named types already visited above cannot add new selections;
    this also terminates legal recursive pointer embeddings without recursion.
    The checked source record owner remains responsible for valid embeddings.
    """
    if isinstance(dtype, DefinedType) and isinstance(underlying(dtype), PointerType):
        return MethodSet()
    pointer = isinstance(dtype, PointerType)
    root = dtype.element if pointer else dtype
    if pointer and interface_type(root) is not None:
        return MethodSet()
    frontier = [_Search(root, indirect=pointer)]
    selected, seen = {}, set()
    while frontier:
        grouped = {}
        for item in frontier:
            previous = grouped.get(item.type)
            grouped[item.type] = (item if previous is None else
                                  _Search(item.type, previous.path,
                                          previous.indirect or item.indirect, True))
        candidates, fields, following, visited = {}, set(), [], set()

        def offer(method, implementation, item, pointer_receiver=False):
            key = member_key(method.name, method.package)
            choice = (None if item.multiple or (pointer_receiver and not item.indirect) else
                      MethodSelection(method, implementation, item.path, item.indirect))
            candidates[key] = None if key in candidates else choice

        for dtype, item in grouped.items():
            if isinstance(dtype, DefinedType):
                if dtype in seen:
                    continue
                visited.add(dtype)
                for name, function in dtype.methods.items():
                    if name == "_":
                        continue
                    method = InterfaceMethod(name, member_key(name, dtype.package)[1],
                                             MethodSignature(tuple(t for _, t in function.params[1:]),
                                                             function.result,
                                                             getattr(function, "variadic", False)))
                    offer(method, function, item,
                          isinstance(function.receiver[1], PointerType))
            base = underlying(dtype)
            if isinstance(base, StructType):
                for index, field in enumerate(base.fields):
                    fields.add(member_key(field.name, field.package))
                    if field.embedded:
                        pointer = isinstance(field.type, PointerType)
                        child = field.type.element if pointer else field.type
                        following.append(_Search(child, item.path + (index,),
                                                 item.indirect or pointer, item.multiple))
            elif isinstance(base, InterfaceType):
                for method in base.methods:
                    offer(method, None, _Search(dtype, item.path, True, item.multiple))
        for key in set(candidates) | fields:
            if key not in selected:
                selected[key] = None if key in fields else candidates[key]
        seen.update(visited)
        frontier = following
    return MethodSet(tuple(selected[key] for key in sorted(selected) if selected[key] is not None),
                     tuple(key for key in sorted(selected) if selected[key] is None))


def require_implements(dtype, target, token):
    """Check ordinary basic-interface inclusion before choosing representation."""
    target_set = interface_type(target)
    if target_set is None:
        raise TypeError("interface target required")
    actual = method_set(dtype)
    for required in target_set.methods:
        selected = actual.lookup(required.name, required.package)
        if selected is None:
            raise GoDiagnostic(token, f"cannot use {dtype} as {target}: does not implement {target}: missing method " + required.name)
        if selected.method.signature != required.signature:
            raise GoDiagnostic(token, f"cannot use {dtype} as {target}: does not implement {target}: wrong type for method " + required.name)
    return actual
