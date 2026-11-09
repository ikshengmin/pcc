"""Virtual functions of polymorphic classes without base classes.

N4659 [class.virtual], [class.abstract], [dcl.fct.spec]/1, [class.mem]/1
(pure-specifier), [class.base.init]/13 and [class.cdtor]/4; Itanium C++ ABI
2.4 (the virtual pointer is at offset 0 of a dynamic class) and 2.5.2 (virtual
function pointers in declaration order, a virtual destructor taking the
complete-object then the deleting entry).

A polymorphic class projects a pointer to its virtual table as the first C
record field. Every declared constructor stores it before member
initialization; unqualified calls of a virtual member load the callee from
that table, and qualified calls call the member directly. Each translation
unit that defines the class emits one internal, read-only table. Only the
function entries are modeled: there is no offset-to-top, RTTI, typeinfo or
cross-compiler table interoperability claim, so tables of different
translation units are interchangeable but not identical.

Without base classes no overrider other than the class's own member exists,
and every object or member subobject of the class has exactly that dynamic
type. Copying such a record therefore copies an equal table pointer, and a
destructor need not restore the pointer. Base classes must revisit both.
Pure entries hold a null pointer; with no derived classes they cannot be
reached. The deleting-destructor entry is null because delete-expressions
are an explicit allocation gap.
"""
from copy import deepcopy

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import unqualified


VPTR_FIELD = "__pcc_cpp_vptr"


def _unsupported(coord, detail):
    raise CppDiagnostic("cpp.unsupported.virtual-functions", detail, coord)


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
        declaration = parse_destructor(parser, definition)
        symbol = definition.destructor
        if getattr(symbol, "destructor_defaulted", False):
            _unsupported(token.coord, "defaulted virtual destructors require table-requested definitions")
        symbol.method_virtual = True
        symbol.method_pure = False
        symbol.virtual_prototype = declaration
        definition.virtual_functions.append(symbol)
        return declaration
    if (following.value == definition.dtype.name and position + 2 < len(tokens)
            and tokens[position + 2].value == "("):
        raise CppDiagnostic("cpp.invalid-virtual", "a constructor cannot be virtual", token.coord)
    return parser._function(member_definition=definition)


def declare_virtual(definition, symbol, virtual_token, specifiers, pure, coord):
    """Apply the declaration rules once the member's identity is known."""
    if virtual_token is None:
        if pure:
            raise CppDiagnostic("cpp.invalid-pure-specifier",
                                "a pure-specifier requires a virtual function", coord)
        if specifiers:
            # A class without bases has no member for a non-virtual function
            # to override, so it cannot become virtual implicitly.
            raise CppDiagnostic("cpp.invalid-virt-specifier",
                                "override and final require a virtual function", coord)
        return
    if getattr(symbol, "method_static", False):
        raise CppDiagnostic("cpp.invalid-virtual", "a static member function cannot be virtual", coord)
    if "override" in specifiers:
        raise CppDiagnostic("cpp.invalid-override",
                            "'" + symbol.name + "' is marked override but does not override a base member",
                            coord)
    if symbol.variadic:
        _unsupported(coord, "variadic virtual functions require variadic table entries")
    if symbol.name.startswith("operator"):
        _unsupported(coord, "virtual operator functions require operator-call dispatch")
    symbol.method_virtual = True
    symbol.method_pure = pure
    symbol.method_final = "final" in specifiers
    definition.virtual_functions.append(symbol)


def require_concrete(dtype, coord):
    """[class.abstract]/3: no object of an abstract class is created here."""
    owner = unqualified(dtype)
    definition = getattr(owner, "definition", None)
    if definition is not None and getattr(definition, "abstract", False):
        raise CppDiagnostic("cpp.abstract-class",
                            "cannot create an object of abstract class '" + owner.name + "'", coord)


def _entries(definition):
    for symbol in definition.virtual_functions:
        if getattr(symbol, "destructor", False):
            yield symbol, False
            yield symbol, True
        else:
            yield symbol, False


def _slot_name(index):
    return "__pcc_cpp_slot_" + str(index)


def _rename(node, name):
    while not isinstance(node, c_ast.TypeDecl):
        node = node.type
    node.declname = name


def complete_class(parser, definition):
    """Check construction rules, then project the table type and object."""
    coord = definition.dtype.coord
    if definition.defaulted_constructors:
        _unsupported(coord, "defaulted constructors of polymorphic classes require table-pointer initialization")
    if not definition.abstract and not definition.constructors.overloads:
        _unsupported(coord, "implicit constructors of polymorphic classes require table-pointer initialization")
    fields, values = [], []
    for index, (symbol, deleting) in enumerate(_entries(definition)):
        name = _slot_name(index)
        function = deepcopy(symbol.virtual_prototype.type)
        _rename(function, name)
        fields.append(c_ast.Decl(name, [], [], [], c_ast.PtrDecl([], function, coord), None, None, coord))
        if deleting or symbol.method_pure:
            values.append(c_ast.Constant("int", "0", coord))
        else:
            values.append(c_ast.ID(symbol.c_name, coord))
        if not deleting:
            symbol.virtual_slot = name
    tag = table_tag(definition.dtype)
    table = c_ast.Decl(None, [], [], [], c_ast.Struct(tag, fields, coord), None, None, coord)
    name = table_object(definition.dtype)
    storage = c_ast.Decl(
        name, ["const"], ["static"], [],
        c_ast.TypeDecl(name, ["const"], c_ast.Struct(tag, None, coord), coord),
        c_ast.InitList(values, coord), None, coord,
    )
    return [table, storage]


def vptr_field(definition):
    coord = definition.dtype.coord
    pointee = c_ast.TypeDecl(VPTR_FIELD, ["const"], c_ast.Struct(table_tag(definition.dtype), None, coord), coord)
    return c_ast.Decl(VPTR_FIELD, [], [], [], c_ast.PtrDecl([], pointee, coord), None, None, coord)


def vptr_store(definition, this, coord):
    """[class.cdtor]/4: calls during construction use the class's own table."""
    field = c_ast.StructRef(c_ast.ID(this.c_name, coord), "->", c_ast.ID(VPTR_FIELD, coord), coord)
    address = c_ast.UnaryOp("&", c_ast.ID(table_object(definition.dtype), coord), coord)
    return c_ast.Assignment("=", field, address, coord)


def callee(selected, this_name, coord):
    """Load the final overrider of an unqualified virtual call from the object."""
    table = c_ast.StructRef(c_ast.ID(this_name, coord), "->", c_ast.ID(VPTR_FIELD, coord), coord)
    return c_ast.StructRef(table, "->", c_ast.ID(selected.virtual_slot, coord), coord)
