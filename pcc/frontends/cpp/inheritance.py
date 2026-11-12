"""Single non-virtual base classes of non-polymorphic classes.

N4659 [class.derived], [class.member.lookup], [class.access.base],
[conv.ptr]/3, [dcl.init.ref]/5 and [class.protected]; Itanium C++ ABI 2.4
(a primary non-virtual base at offset 0, the empty-base optimization, and
tail-padding reuse of a non-POD base).

One direct base per class, so every base subobject is at offset 0 of its
derived object and a Derived* converts to Base* without adjustment. A base
whose layout the existing C aggregate projection reproduces exactly is a
leading C field; an empty base is allocated no storage. Layouts that need
tail-padding reuse, and bases or derived classes with user-declared special
members or virtual functions, remain explicit capability boundaries.
"""
from dataclasses import dataclass

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import PointerType, const_qualified, unqualified

BASE_FIELD = "__pcc_cpp_base"
ACCESS_ORDER = {"public": 0, "protected": 1, "private": 2}


def unsupported(message, coord):
    raise CppDiagnostic("cpp.unsupported.base-classes", message, coord)


@dataclass(frozen=True)
class BaseSpecifier:
    dtype: object
    access: str
    coord: object


def base_specifier(definition):
    return getattr(definition, "base", None)


def direct_base(dtype):
    dtype = unqualified(dtype)
    definition = getattr(dtype, "definition", None)
    specifier = base_specifier(definition) if definition is not None else None
    return None if specifier is None else specifier.dtype


def base_chain(dtype):
    """The proper bases of a class, nearest first."""
    chain = []
    current = direct_base(dtype)
    while current is not None:
        chain.append(current)
        current = direct_base(current)
    return chain


def is_proper_base(base, derived):
    base = unqualified(base)
    return any(item is base for item in base_chain(derived))


def is_empty_class(dtype):
    """Itanium empty class: no data members, virtual functions or nonempty bases."""
    definition = unqualified(dtype).definition
    if definition.members or definition.polymorphic:
        return False
    base = direct_base(dtype)
    return base is None or is_empty_class(base)


def pod_for_layout(dtype):
    """Itanium 'POD for the purpose of layout' for the admitted class shapes.

    Conservative: no base, no virtual function, no user-declared special
    member, only public data members without default member initializers, and
    recursively POD class members. Anything else may have its tail padding
    reused by a derived class, so it is not treated as POD here.
    """
    from pcc.frontends.cpp.classes import is_class_type
    definition = unqualified(dtype).definition
    if (base_specifier(definition) is not None or definition.polymorphic
            or definition.constructors.overloads or definition.destructor is not None
            or definition.scope.names.get("operator=") is not None):
        return False
    for member in definition.members:
        if member.access != "public" or getattr(member, "default_initializer_tokens", None) is not None:
            return False
        if is_class_type(member.dtype) and not pod_for_layout(member.dtype):
            return False
    return True


def parse_base_clause(parser, definition_keyword, dtype):
    """`: [virtual] [access] class-name` with exactly one base."""
    colon = parser.cursor.take()
    if parser._peek().value == "virtual":
        unsupported("virtual base classes are not implemented", parser._peek().coord)
    access = "private" if definition_keyword == "class" else "public"
    if parser._peek().value in ACCESS_ORDER:
        access = parser.cursor.take().value
        if parser._peek().value == "virtual":
            unsupported("virtual base classes are not implemented", parser._peek().coord)
    name = parser._name()
    if name.template_arguments if hasattr(name, "template_arguments") else False:
        unsupported("class template-id bases are not implemented", name.coord)
    symbol = parser.scope.resolve_name(name)
    from pcc.frontends.cpp.classes import ClassType, require_complete
    base = None if symbol is None else unqualified(symbol.dtype)
    if symbol is None or symbol.kind != "type" or not isinstance(base, ClassType):
        raise CppDiagnostic("cpp.invalid-base", "a base specifier requires a class name", name.coord)
    if base is dtype:
        raise CppDiagnostic("cpp.invalid-base", "a class cannot be its own base", name.coord)
    require_complete(base, name.coord)
    if parser._peek().value == ",":
        unsupported("multiple base classes are not implemented", parser._peek().coord)
    if parser._peek().value == "...":
        unsupported("base pack expansions are not implemented", parser._peek().coord)
    definition = base.definition
    if definition.polymorphic or any(getattr(item, "polymorphic_bases", False) for item in base_chain(base)):
        unsupported("polymorphic base classes and overriding are not implemented", name.coord)
    if (definition.constructors.overloads or definition.destructor is not None
            or any(item.definition.constructors.overloads or item.definition.destructor is not None
                   for item in base_chain(base))):
        unsupported("base subobject construction and destruction are not implemented", name.coord)
    del colon
    return BaseSpecifier(base, access, name.coord)


def record_fields(dtype, types, coord):
    """Storage facts in ClassDefinition.c_declaration order: (layout, data size)."""
    from pcc.frontends.cpp.size_queries import object_layout
    from pcc.frontends.c.c_abi_layout import pointer_scalar_layout
    definition = unqualified(dtype).definition
    fields = []
    if definition.polymorphic:
        layout = pointer_scalar_layout()
        fields.append((layout, layout.size))
    specifier = base_specifier(definition)
    if specifier is not None and not is_empty_class(specifier.dtype):
        fields.append((object_layout(specifier.dtype, types, coord), data_size(specifier.dtype, types, coord)))
    for member in definition.members:
        layout = object_layout(member.dtype, types, member.coord)
        fields.append((layout, layout.size))  # A member's tail padding is never reused.
    return fields


def data_size(dtype, types, coord):
    """Itanium dsize: the end of the last field's data, excluding tail padding."""
    from pcc.frontends.c.c_abi_layout import natural_record_layout
    fields = record_fields(dtype, types, coord)
    record = natural_record_layout([layout for layout, _ in fields], minimum_size=1)
    return max((offset + size for offset, (_, size) in zip(record.offsets, fields)), default=0)


def has_tail_padding(dtype, types, coord):
    from pcc.frontends.cpp.size_queries import object_layout
    return data_size(dtype, types, coord) < object_layout(dtype, types, coord).size


def check_derived_definition(definition):
    """Reject derived shapes whose semantics or layout are not modeled."""
    specifier = base_specifier(definition)
    if specifier is None:
        return
    coord = specifier.coord
    if definition.polymorphic:
        unsupported("virtual functions of derived classes require overriding semantics", coord)
    if definition.constructors.overloads or definition.destructor is not None:
        unsupported("constructors and destructors of derived classes require base subobject construction",
                    coord)
    from pcc.frontends.cpp.classes import is_class_type
    base = specifier.dtype
    if is_empty_class(base):
        # [intro.object]/8: two distinct objects of the same type cannot share
        # an address, which Itanium resolves by moving the empty base.
        if definition.members and is_class_type(definition.members[0].dtype):
            first = unqualified(definition.members[0].dtype)
            if first is base or is_proper_base(base, first):
                unsupported("an empty base at the address of a same-typed member is not implemented", coord)
        return
    # Itanium 2.4: derived members may occupy a non-POD base's tail padding.
    # Without tail padding the natural C record already gives that layout.
    if (definition.members and not pod_for_layout(base)
            and has_tail_padding(base, definition.layout_types, coord)):
        unsupported("tail-padding reuse of a non-POD base by derived members is not implemented", coord)


def base_field(definition, project_type):
    """The leading C field holding a nonempty base subobject, if any."""
    specifier = base_specifier(definition)
    if specifier is None or is_empty_class(specifier.dtype):
        return None
    from pcc.frontends.cpp.classes import c_class_type
    coord = specifier.coord
    return c_ast.Decl(BASE_FIELD, [], [], [], c_class_type(BASE_FIELD, specifier.dtype, coord),
                      None, None, coord)


def find_member(dtype, name):
    """[class.member.lookup] for a single-base chain: (symbol, declaring class)."""
    current = unqualified(dtype)
    while current is not None:
        symbol = current.definition.scope.names.get(name)
        if symbol is not None:
            return symbol, current
        current = direct_base(current)
    return None, None


def effective_access(derived, owner, declared):
    """Access of a member of `owner` named through `derived` ([class.access.base]/1)."""
    access = declared
    current = unqualified(derived)
    while current is not unqualified(owner):
        specifier = base_specifier(current.definition)
        if access == "private":
            return None  # Private members of a base are inaccessible as members of the derived class.
        if ACCESS_ORDER[specifier.access] > ACCESS_ORDER[access]:
            access = specifier.access
        current = unqualified(specifier.dtype)
    return access


def base_subobject(parser, expression, base, coord):
    """An lvalue of the `base` subobject of a class lvalue, preserving cv."""
    from pcc.frontends.cpp.classes import c_class_type
    from pcc.frontends.cpp.types import ConstType
    result = expression
    for step in base_chain(expression.dtype):
        qualified = ConstType(step) if const_qualified(result.dtype) else step
        if is_empty_class(step):
            pointer = PointerType(qualified)
            address = parser._unary_expression("&", result, coord)
            typename = c_ast.Typename(None, [], c_ast.PtrDecl([], c_class_type(None, qualified, coord), coord), coord)
            node = c_ast.UnaryOp("*", c_ast.Cast(typename, address.node, coord), coord)
            del pointer
        else:
            node = c_ast.StructRef(result.node, ".", c_ast.ID(BASE_FIELD, coord), coord)
        result = type(expression)(node, qualified, lvalue=True)
        result.discarded_known = expression.discarded_known
        result.discarded_node = expression.discarded_node
        result.discarded_fact = expression.discarded_fact
        if step is unqualified(base):
            return result
    raise ValueError("base_subobject requires a proper base")


def lexical_classes(scope):
    """Classes whose member (or member-function body) contains this scope."""
    classes = []
    while scope is not None:
        current = getattr(scope, "class_type", None)
        if current is not None and current not in classes:
            classes.append(current)
        scope = scope.parent
    return classes


def _member_or_friend(lexicals, dtype):
    friends = getattr(dtype.definition, "friend_classes", ())
    return any(item is dtype or any(item is friend for friend in friends) for item in lexicals)


def member_accessible(scope, owner, declared, naming, object_class=None, lexicals=None):
    """[class.access.base]/5 and [class.protected] for a single-base chain.

    `declared` is the access of the member in its declaring class `owner`;
    `naming` is the class in which it is named. `object_class` is the class of
    the object expression for a non-static member, if any.
    """
    if lexicals is None:
        lexicals = lexical_classes(scope)
    naming, owner = unqualified(naming), unqualified(owner)
    path = [naming] + [item for item in base_chain(naming)]
    for current in path:
        access = effective_access(current, owner, declared)
        if access is None:
            if current is owner:
                break
            continue
        if current is not naming and not base_accessible(scope, naming, current, lexicals):
            continue
        if access == "public" or _member_or_friend(lexicals, current):
            return True
        if access == "protected":
            for lexical in lexicals:
                if is_proper_base(current, lexical) and (
                        object_class is None or unqualified(object_class) is lexical
                        or is_proper_base(lexical, object_class)):
                    return True
        if current is owner:
            break
    return False


def base_accessible(scope, derived, base, lexicals=None):
    """[class.access.base]/4: an invented public member of `base` named in `derived`."""
    derived, base = unqualified(derived), unqualified(base)
    if derived is base:
        return True
    if lexicals is None:
        lexicals = lexical_classes(scope)
    current = derived
    while current is not base:
        specifier = base_specifier(current.definition)
        if specifier.access != "public" and not _member_or_friend(lexicals, current) and not (
                specifier.access == "protected"
                and any(is_proper_base(current, lexical) for lexical in lexicals)):
            return False
        current = unqualified(specifier.dtype)
    return True


def require_member_access(scope, symbol, naming, object_class, coord):
    if not member_accessible(scope, symbol.class_owner, getattr(symbol, "access", "public"), naming, object_class):
        raise CppDiagnostic("cpp.member-access", "'" + symbol.name + "' is inaccessible as a member of '"
                            + unqualified(naming).name + "'", coord)


def require_accessible_base(scope, derived, base, coord):
    if not base_accessible(scope, derived, base):
        raise CppDiagnostic("cpp.inaccessible-base", "'" + unqualified(base).name + "' is an inaccessible base of '"
                            + unqualified(derived).name + "'", coord)


def base_pointer(expression, base, coord):
    """D cv* to B cv*: offset 0 for a single non-virtual base, so a C cast."""
    from pcc.frontends.cpp.classes import c_class_type
    from pcc.frontends.cpp.types import ConstType
    pointee = unqualified(expression.dtype).pointee
    target = ConstType(unqualified(base)) if const_qualified(pointee) else unqualified(base)
    typename = c_ast.Typename(None, [], c_ast.PtrDecl([], c_class_type(None, target, coord), coord), coord)
    return type(expression)(c_ast.Cast(typename, expression.node, coord), PointerType(target))


def derived_to_base_pointer(source, destination):
    """[conv.ptr]/3 with [conv.qual]: D cv1* to B cv2* where cv2 covers cv1."""
    source, destination = unqualified(source), unqualified(destination)
    if not isinstance(source, PointerType) or not isinstance(destination, PointerType):
        return False
    from pcc.frontends.cpp.classes import ClassType
    derived, base = unqualified(source.pointee), unqualified(destination.pointee)
    if not isinstance(derived, ClassType) or not isinstance(base, ClassType):
        return False
    if const_qualified(source.pointee) and not const_qualified(destination.pointee):
        return False
    return is_proper_base(base, derived)
