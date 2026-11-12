"""Virtual functions, overriding and the primary-base table pointer.

N4659 [class.virtual], [class.abstract], [dcl.fct.spec]/1, [class.mem]/1
(pure-specifier), [class.base.init]/13, [class.cdtor]/4 and [class.dtor]/9;
Itanium C++ ABI 2.4 (the virtual pointer is at offset 0 of a dynamic class
and a dynamic single base is the primary base sharing it) and 2.5.2 (the
primary base's virtual function pointers first, in its order, followed by
the new virtual functions in declaration order; a virtual destructor takes
the complete-object then the deleting entry).

The class that introduces dynamism projects a pointer to its virtual table
as the first C record field; a derived class with a dynamic single base
shares it through the base subobject at offset 0. Each entry holds the final
overrider of its class, so a derived table has the base entries, overrider
replaced, then its own. Every declared constructor stores the class's table
after base construction and before member initialization, and every
destructor stores it on entry; calls during construction and destruction
therefore select the class's own overriders. Unqualified calls load the
callee from the table, viewed through the table type of the selected
member's class; qualified calls call the member directly.

Each translation unit that defines the class emits one internal, read-only
table. Only the function entries are modeled: there is no offset-to-top,
RTTI, typeinfo or cross-compiler table interoperability claim, so tables of
different translation units are interchangeable but not identical. With a
single base every subobject is at offset 0, so an overrider receives the
caller's this pointer and a covariant pointer or reference result needs no
adjustment. Pure entries and the deleting-destructor entry hold a null
pointer because delete-expressions are an explicit allocation gap.
"""
from copy import deepcopy
from dataclasses import dataclass

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, const_qualified, unqualified


VPTR_FIELD = "__pcc_cpp_vptr"


def _unsupported(coord, detail):
    raise CppDiagnostic("cpp.unsupported.virtual-functions", detail, coord)


@dataclass
class Entry:
    """One table entry: the declaration that introduced it and its final overrider."""
    introducer: object
    deleting: bool
    overrider: object


def primary_base(definition):
    """The dynamic direct base sharing this class's table pointer, if any."""
    from pcc.frontends.cpp.inheritance import base_specifier
    specifier = base_specifier(definition)
    if specifier is not None and specifier.dtype.definition.polymorphic:
        return specifier.dtype
    return None


def vptr_owner(dtype):
    """The class whose record holds the shared table pointer field."""
    current = unqualified(dtype)
    while True:
        base = primary_base(current.definition)
        if base is None:
            return current
        current = base


def _class_pointer(dtype, node, coord, const=False):
    from pcc.frontends.cpp.classes import c_class_type
    target = ConstType(dtype) if const else dtype
    typename = c_ast.Typename(None, [], c_ast.PtrDecl([], c_class_type(None, target, coord), coord), coord)
    return c_ast.Cast(typename, node, coord)


def _table_pointer(dtype, node, coord):
    pointee = c_ast.TypeDecl(None, ["const"], c_ast.Struct(table_tag(dtype), None, coord), coord)
    return c_ast.Cast(c_ast.Typename(None, [], c_ast.PtrDecl([], pointee, coord), coord), node, coord)


def table_tag(dtype):
    return "__pcc_cpp_vtable_" + dtype.c_tag


def table_object(dtype):
    return "__pcc_cpp_vtable_object_" + dtype.c_tag


def take_virtual(parser, previous):
    token = parser._expect("virtual")
    if previous is not None:
        raise CppDiagnostic("cpp.duplicate-virtual-specifier",
                            "virtual may occur only once in a declaration", token.coord)
    return token


def parse_member_specifiers(parser):
    """Parse an in-class virt-specifier-seq and pure-specifier, in that order."""
    specifiers = []
    while parser._peek().kind == "ID" and parser._peek().value in ("override", "final"):
        token = parser.cursor.take()
        if token.value in specifiers:
            raise CppDiagnostic("cpp.duplicate-virt-specifier",
                                "a virt-specifier may occur only once", token.coord)
        specifiers.append(token.value)
    pure = False
    tokens, position = parser.cursor.tokens, parser.cursor.position
    if (parser._peek().value == "=" and position + 1 < len(tokens)
            and tokens[position + 1].value == "0"):
        parser._expect("=")
        parser.cursor.take()
        pure = True
        if parser._peek().value == "{":
            raise CppDiagnostic("cpp.invalid-pure-specifier",
                                "a function definition cannot have a pure-specifier", parser._peek().coord)
    return tuple(specifiers), pure


def parse_virtual_member(parser, definition):
    """Route an in-class declaration beginning with virtual."""
    token = parser._peek()
    tokens, position = parser.cursor.tokens, parser.cursor.position
    following = tokens[position + 1] if position + 1 < len(tokens) else token
    if following.value == "~":
        parser.cursor.take()
        from pcc.frontends.cpp.destructors import parse_destructor
        return parse_destructor(parser, definition, virtual_token=token)
    if (following.value == definition.dtype.name and position + 2 < len(tokens)
            and tokens[position + 2].value == "("):
        raise CppDiagnostic("cpp.invalid-virtual", "a constructor cannot be virtual", token.coord)
    return parser._function(member_definition=definition)


def _same_signature(candidate, symbol):
    from pcc.frontends.cpp.templates import same_type
    return (len(candidate.parameters) == len(symbol.parameters)
            and all(same_type(left, right) for left, right in zip(candidate.parameters, symbol.parameters))
            and bool(getattr(candidate, "method_const", False)) == bool(getattr(symbol, "method_const", False))
            and candidate.variadic == symbol.variadic)


def overridden_member(definition, symbol):
    """[class.virtual]/2: the nearest base virtual function with the same signature.

    Hiding does not matter: the parameter-type-list, cv-qualification and
    name decide, through every base of the single-base chain.
    """
    from pcc.frontends.cpp.inheritance import base_chain
    for base in base_chain(definition.dtype):
        group = base.definition.scope.names.get(symbol.name)
        if group is None or group.kind != "function":
            continue
        for candidate in group.overloads:
            if getattr(candidate, "method_virtual", False) and _same_signature(candidate, symbol):
                return candidate
    return None


def overridden_destructor(definition):
    base = primary_base(definition)
    if base is None:
        return None
    destructor = base.definition.destructor
    return destructor if getattr(destructor, "method_virtual", False) else None


def _check_final(overridden, coord):
    if getattr(overridden, "method_final", False):
        raise CppDiagnostic("cpp.override-final",
                            "'" + overridden.name + "' is final and cannot be overridden", coord)


def _check_return(definition, symbol, overridden, coord):
    """[class.virtual]/7-8: the same type, or a covariant class pointer/reference."""
    from pcc.frontends.cpp.templates import same_type
    if same_type(symbol.dtype, overridden.dtype):
        return
    derived, base = unqualified(symbol.dtype), unqualified(overridden.dtype)
    kinds = (PointerType, ReferenceType)
    if any(isinstance(derived, kind) and isinstance(base, kind) for kind in kinds):
        inner = derived.pointee if isinstance(derived, PointerType) else derived.referent
        outer = base.pointee if isinstance(base, PointerType) else base.referent
        from pcc.frontends.cpp.classes import ClassType
        from pcc.frontends.cpp.inheritance import base_accessible, is_proper_base
        if (isinstance(unqualified(inner), ClassType) and isinstance(unqualified(outer), ClassType)
                and is_proper_base(outer, inner)
                and (not const_qualified(inner) or const_qualified(outer))):
            if not base_accessible(definition.scope, inner, outer):
                raise CppDiagnostic("cpp.invalid-override-return",
                                    "covariant return requires an accessible base class", coord)
            return
    raise CppDiagnostic("cpp.invalid-override-return",
                        "return type of overriding '" + symbol.name + "' differs from the overridden function", coord)


def declare_virtual(definition, symbol, virtual_token, specifiers, pure, coord):
    """Apply the declaration rules once the member's identity is known."""
    overridden = overridden_member(definition, symbol)
    if overridden is not None and getattr(symbol, "method_static", False):
        raise CppDiagnostic("cpp.invalid-virtual",
                            "a static member function cannot override a virtual function", coord)
    if "override" in specifiers and overridden is None:
        raise CppDiagnostic("cpp.invalid-override",
                            "'" + symbol.name + "' is marked override but does not override a base member",
                            coord)
    if virtual_token is None and overridden is None:
        if pure:
            raise CppDiagnostic("cpp.invalid-pure-specifier",
                                "a pure-specifier requires a virtual function", coord)
        if specifiers:
            raise CppDiagnostic("cpp.invalid-virt-specifier",
                                "final requires a virtual function", coord)
        return
    if getattr(symbol, "method_static", False):
        raise CppDiagnostic("cpp.invalid-virtual", "a static member function cannot be virtual", coord)
    if overridden is not None:
        _check_final(overridden, coord)
        _check_return(definition, symbol, overridden, coord)
    if symbol.variadic:
        _unsupported(coord, "variadic virtual functions require variadic table entries")
    if symbol.name.startswith("operator"):
        _unsupported(coord, "virtual operator functions require operator-call dispatch")
    symbol.method_virtual = True
    symbol.method_pure = pure
    symbol.method_final = "final" in specifiers
    symbol.overridden = overridden
    definition.virtual_functions.append(symbol)


def declare_destructor(definition, symbol, declaration, virtual_token, specifiers, defaulted, coord):
    """A destructor is virtual if declared so or if a base destructor is virtual."""
    overridden = overridden_destructor(definition)
    if "override" in specifiers and overridden is None:
        raise CppDiagnostic("cpp.invalid-override",
                            "'" + symbol.name + "' is marked override but does not override a base member", coord)
    if virtual_token is None and overridden is None:
        if specifiers:
            raise CppDiagnostic("cpp.invalid-virt-specifier", "final requires a virtual function", coord)
        return
    if overridden is not None:
        _check_final(overridden, coord)
    if defaulted:
        _unsupported(coord, "defaulted virtual destructors require table-requested definitions")
    symbol.method_virtual = True
    symbol.method_pure = False
    symbol.method_final = "final" in specifiers
    symbol.virtual_prototype = declaration
    symbol.overridden = overridden
    definition.virtual_functions.append(symbol)


def require_concrete(dtype, coord):
    """[class.abstract]/3: no object of an abstract class is created here."""
    owner = unqualified(dtype)
    definition = getattr(owner, "definition", None)
    if definition is not None and getattr(definition, "abstract", False):
        raise CppDiagnostic("cpp.abstract-class",
                            "cannot create an object of abstract class '" + owner.name + "'", coord)


def table_entries(definition):
    """The primary base's entries with this class's overriders, then new entries."""
    base = primary_base(definition)
    entries = [] if base is None else [Entry(entry.introducer, entry.deleting, entry.overrider)
                                       for entry in base.definition.virtual_entries]
    for symbol in definition.virtual_functions:
        overridden = getattr(symbol, "overridden", None)
        if overridden is not None:
            index = overridden.virtual_index
            entries[index].overrider = symbol
            if getattr(symbol, "destructor", False):
                entries[index + 1].overrider = symbol
            continue
        if getattr(symbol, "destructor", False):
            entries.append(Entry(symbol, False, symbol))
            entries.append(Entry(symbol, True, symbol))
        else:
            entries.append(Entry(symbol, False, symbol))
    return entries


def _slot_name(index):
    return "__pcc_cpp_slot_" + str(index)


def _rename(node, name):
    while not isinstance(node, c_ast.TypeDecl):
        node = node.type
    node.declname = name


def complete_class(parser, definition):
    """Check construction rules, then project the table type and object."""
    coord = definition.dtype.coord
    entries = table_entries(definition)
    definition.virtual_entries = entries
    for index, entry in enumerate(entries):
        if entry.introducer is entry.overrider and not entry.deleting:
            entry.overrider.virtual_index = index
            entry.overrider.virtual_slot = _slot_name(index)
    for symbol in definition.virtual_functions:
        overridden = getattr(symbol, "overridden", None)
        if overridden is not None:
            symbol.virtual_index, symbol.virtual_slot = overridden.virtual_index, overridden.virtual_slot
    if base_without_destructor(definition):
        _unsupported(coord, "the implicit virtual destructor of a derived class requires special-member synthesis")
    if definition.defaulted_constructors:
        _unsupported(coord, "defaulted constructors of polymorphic classes require table-pointer initialization")
    if not definition.abstract and not definition.constructors.overloads:
        _unsupported(coord, "implicit constructors of polymorphic classes require table-pointer initialization")
    fields, values = [], []
    for index, entry in enumerate(entries):
        name = _slot_name(index)
        function = deepcopy(entry.overrider.virtual_prototype.type)
        _rename(function, name)
        fields.append(c_ast.Decl(name, [], [], [], c_ast.PtrDecl([], function, coord), None, None, coord))
        if entry.deleting or entry.overrider.method_pure:
            values.append(c_ast.Constant("int", "0", coord))
        else:
            values.append(c_ast.ID(entry.overrider.c_name, coord))
    tag = table_tag(definition.dtype)
    table = c_ast.Decl(None, [], [], [], c_ast.Struct(tag, fields, coord), None, None, coord)
    name = table_object(definition.dtype)
    storage = c_ast.Decl(
        name, ["const"], ["static"], [],
        c_ast.TypeDecl(name, ["const"], c_ast.Struct(tag, None, coord), coord),
        c_ast.InitList(values, coord), None, coord,
    )
    return [table, storage]


def base_without_destructor(definition):
    """A virtual base destructor whose derived final overrider would be implicit."""
    return definition.destructor is None and overridden_destructor(definition) is not None


def abstract(definition):
    """[class.abstract]/2: some entry's final overrider is pure."""
    entries = getattr(definition, "virtual_entries", None)
    if entries is None:
        # Before completion only the class's own declarations are known.
        return any(getattr(symbol, "method_pure", False) for symbol in definition.virtual_functions)
    return any(getattr(entry.overrider, "method_pure", False) for entry in entries)


def owns_vptr(definition):
    return definition.polymorphic and primary_base(definition) is None


def vptr_field(definition):
    coord = definition.dtype.coord
    pointee = c_ast.TypeDecl(VPTR_FIELD, ["const"], c_ast.Struct(table_tag(definition.dtype), None, coord), coord)
    return c_ast.Decl(VPTR_FIELD, [], [], [], c_ast.PtrDecl([], pointee, coord), None, None, coord)


def vptr_store(definition, this, coord):
    """[class.cdtor]/4: during construction and destruction the class's own table."""
    owner = definition.dtype
    root = vptr_owner(owner)
    target = c_ast.ID(this.c_name, coord)
    address = c_ast.UnaryOp("&", c_ast.ID(table_object(owner), coord), coord)
    if root is not owner:
        target = _class_pointer(root, target, coord)
        address = _table_pointer(root, address, coord)
    field = c_ast.StructRef(target, "->", c_ast.ID(VPTR_FIELD, coord), coord)
    return c_ast.Assignment("=", field, address, coord)


def callee(selected, this_node, coord):
    """Load the final overrider of an unqualified virtual call from the object.

    The entry is read through the table type of the selected member's class,
    whose slot has that class's final-overrider prototype.
    """
    owner = unqualified(selected.class_owner)
    root = vptr_owner(owner)
    if root is not owner:
        this_node = _class_pointer(root, this_node, coord, const=True)
    table = c_ast.StructRef(this_node, "->", c_ast.ID(VPTR_FIELD, coord), coord)
    if root is not owner:
        table = _table_pointer(owner, table, coord)
    return c_ast.StructRef(table, "->", c_ast.ID(selected.virtual_slot, coord), coord)
