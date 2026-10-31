"""Nominal C++ class identity and checked projection to PCC's C object model.

This layer owns class declaration/completeness, non-static data members and
access. It does not erase class syntax or give a C struct C++ constructor
semantics. Only classes with implicit trivial special members can currently
reach the projection; user-declared special members, bases, virtual functions,
unions and default member initializers remain explicit capability boundaries.

The parser and this module share ``Scope``/``Symbol``. There is no second
ordinary-name lookup implementation. Physical layout is performed by the
existing C aggregate layout and self backend, not calculated here. Empty
complete classes receive one storage byte, as required by the Itanium ABI.

Rules: C++17 draft N4659 [class], [class.mem], [class.access], [dcl.init],
[class.default.ctor], with the CWG 2394 defect resolution for const class
members; Itanium C++ ABI sections 2.2 and 2.4. Tests are original
PCC cases, not copied compiler-suite inputs.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import ConstType, PointerType, qualify_const, unqualified


class ClassType:
    """One declaration identity, retained through forwards, aliases and cv.

    Equality is deliberately object identity. Equal spelling in two scopes is
    not equal C++ type identity, and a definition must complete its earlier
    declaration rather than replace it with a structurally equal object.
    ``qualified_name`` is a source name, whereas ``c_tag`` is an internal C AST
    tag and must never be used as a C++ ABI type encoding.
    """

    cpp_class_type = True

    def __init__(self, name, qualified_name, coord, internal_linkage=False,
                 declaration_scope=None):
        self.name = name
        self.qualified_name = tuple(qualified_name)
        self.coord = coord
        self.internal_linkage = internal_linkage
        self.declaration_scope = declaration_scope
        self.c_tag = "__pcc_cpp_class_" + "".join(
            str(len(part)) + part for part in self.qualified_name
        )
        self.definition = None

    @property
    def complete(self):
        return self.definition is not None and self.definition.complete


def is_class_type(dtype):
    return isinstance(unqualified(dtype), ClassType)


def require_complete(dtype, coord):
    """Require an object type to be complete, without completing pointees."""
    dtype = unqualified(dtype)
    if isinstance(dtype, ClassType) and not dtype.complete:
        raise CppDiagnostic(
            "cpp.incomplete-class", "incomplete class '" + dtype.name + "'", coord
        )


def c_class_type(name, dtype, coord):
    """Project a class type reference, retaining top-level const qualifiers."""
    qualifiers = ["const"] if isinstance(dtype, ConstType) else []
    dtype = unqualified(dtype)
    if not isinstance(dtype, ClassType):
        raise TypeError("c_class_type requires a C++ class type")
    return c_ast.TypeDecl(name, qualifiers, c_ast.Struct(dtype.c_tag, None, coord), coord)


class ClassDefinition:
    def __init__(self, dtype, parent, class_key="struct"):
        if class_key not in ("class", "struct"):
            raise ValueError("only non-union class definitions are implemented")
        if dtype.definition is not None:
            raise CppDiagnostic(
                "cpp.class-redefinition", "redefinition of class '" + dtype.name + "'",
                dtype.coord,
            )
        self.dtype = dtype
        self.scope = Scope(parent, kind="class", name=dtype.name)
        self.class_key = class_key
        self.access = "private" if class_key == "class" else "public"
        self.members = []
        self.complete = False
        # The enclosing declaration provides the injected name for the current
        # non-nested/non-template class boundary. A data member can hide it just
        # as it can hide another enclosing type name; no lexer reclassification.
        dtype.definition = self

    def set_access(self, access):
        if access not in ("public", "protected", "private"):
            raise ValueError("invalid class access specifier")
        self.access = access

    def add_member(self, symbol, mutable=False):
        if self.complete:
            raise ValueError("cannot append a member to a completed class")
        dtype = unqualified(symbol.dtype)
        if dtype == "void":
            raise CppDiagnostic(
                "cpp.invalid-object-type", "a data member cannot have type void", symbol.coord
            )
        require_complete(dtype, symbol.coord)
        if mutable and isinstance(symbol.dtype, ConstType):
            raise CppDiagnostic(
                "cpp.invalid-mutable", "a mutable member cannot be const-qualified", symbol.coord
            )
        symbol.kind = "member"
        symbol.access = self.access
        symbol.mutable = mutable
        symbol.class_owner = self.dtype
        self.scope.declare(symbol)
        self.members.append(symbol)
        return symbol

    def finish(self):
        self.complete = True
        return self

    @property
    def aggregate(self):
        return all(member.access == "public" for member in self.members)

    @property
    def const_default_constructible(self):
        # There are no bases, DMI or user-provided constructors at this boundary.
        # Empty classes satisfy this vacuously; scalar members do not.
        return all(
            is_class_type(member.dtype)
            and unqualified(member.dtype).definition.const_default_constructible
            for member in self.members
        )

    @property
    def default_constructor_deleted(self):
        for member in self.members:
            dtype = unqualified(member.dtype)
            if isinstance(dtype, ClassType):
                definition = dtype.definition
                if definition.default_constructor_deleted:
                    return True
                if (isinstance(member.dtype, ConstType)
                        and not definition.const_default_constructible):
                    return True
            elif isinstance(member.dtype, ConstType):
                return True
        return False

    @property
    def copy_assignment_deleted(self):
        return any(
            isinstance(member.dtype, ConstType)
            or is_class_type(member.dtype)
            and unqualified(member.dtype).definition.copy_assignment_deleted
            for member in self.members
        )

    def c_declaration(self, project_type):
        require_complete(self.dtype, self.dtype.coord)
        declarations = [
            c_ast.Decl(
                member.c_name, [], [], [],
                project_type(member.c_name, member.dtype, member.coord),
                None, None, member.coord,
            )
            for member in self.members
        ]
        if not declarations:
            name = "__pcc_cpp_empty_byte"
            declarations.append(c_ast.Decl(
                name, [], [], [], project_type(name, "unsigned char", self.dtype.coord),
                None, None, self.dtype.coord,
            ))
        return c_ast.Decl(
            None, [], [], [], c_ast.Struct(self.dtype.c_tag, declarations, self.dtype.coord),
            None, None, self.dtype.coord,
        )


def _unsupported(token, detail):
    raise CppDiagnostic("cpp.unsupported.classes", detail, token.coord)


def parse_class(parser):
    """Parse a namespace-scope class declaration using the ordinary parser.

    The owner calls this before its ordinary function declaration parser. The
    type projection hook must recognize ``ClassType``; no source-to-source C
    translation or separate tokenization occurs here.
    """
    keyword = parser.cursor.take()
    if keyword.value not in ("class", "struct"):
        _unsupported(keyword, "union object/lifetime semantics are not implemented")
    if parser.parsing_template:
        _unsupported(keyword, "class template semantics are not implemented")
    if parser.scope.kind != "namespace":
        _unsupported(keyword, "local and nested class semantics are not implemented")
    name = parser._identifier()
    previous = parser.scope.names.get(name.value)
    if previous is None:
        dtype = ClassType(
            name.value, parser.scope.namespace_path() + (name.value,), name.coord,
            internal_linkage=parser.scope.namespace().internal_linkage,
            declaration_scope=parser.scope,
        )
        previous = parser.scope.declare(Symbol(name.value, "type", name.coord, dtype=dtype))
        previous.class_declaration = True
    elif not getattr(previous, "class_declaration", False):
        raise CppDiagnostic(
            "cpp.redeclaration", "'" + name.value + "' is not a class declaration", name.coord
        )
    elif previous.owner is not parser.scope:
        _unsupported(name, "redeclaration of an imported class outside its declaring namespace is not implemented")
    dtype = previous.dtype
    if parser._accept(";"):
        return c_ast.Decl(
            None, [], [], [], c_ast.Struct(dtype.c_tag, None, name.coord), None, None, name.coord
        )
    if parser._peek().value == ":":
        _unsupported(parser._peek(), "base class layout and lifetime semantics are not implemented")
    if parser._peek().value != "{":
        _unsupported(parser._peek(), "elaborated class declarations are not implemented here")
    parser._expect("{")
    definition = ClassDefinition(dtype, parser.scope, keyword.value)
    parent = parser.scope
    parser.scope = definition.scope
    try:
        while not parser._accept("}"):
            token = parser._peek()
            if token.kind == "EOF":
                parser._expect("}")
            if parser._accept(";"):
                continue
            if token.value in ("public", "protected", "private"):
                parser.cursor.take()
                parser._expect(":")
                definition.set_access(token.value)
                continue
            if token.value in ("using", "typedef"):
                if (token.value == "using"
                        and parser.cursor.tokens[parser.cursor.position + 1].value == "namespace"):
                    raise CppDiagnostic(
                        "cpp.using-directive-scope", "using-directives are not permitted in class scope",
                        token.coord,
                    )
                if token.value == "using":
                    alias_index = parser.cursor.position + 2
                    if (alias_index >= len(parser.cursor.tokens)
                            or parser.cursor.tokens[alias_index].value != "="):
                        _unsupported(token, "class using-declaration member/base lookup is not implemented")
                member_count = len(definition.scope.names)
                parser._alias()
                class_name = definition.scope.names.get(dtype.name)
                if class_name is not None and class_name.kind == "type":
                    # [class.mem]/18 permits a same-named ordinary data member
                    # when there is no user-declared constructor, but not a
                    # member type alias which replaces the injected class name.
                    raise CppDiagnostic(
                        "cpp.class-name-member", "a member type alias cannot have its enclosing class name",
                        class_name.coord,
                    )
                if len(definition.scope.names) == member_count:
                    # Unlike namespace/block typedef redeclarations, the same
                    # member cannot be declared twice in one member-specification
                    # ([class.mem]/5). _alias installs one name and never removes
                    # names; unchanged membership means its same-type exemption
                    # reused a previously declared member alias.
                    raise CppDiagnostic(
                        "cpp.redeclaration", "a member type alias cannot be redeclared in its class",
                        token.coord,
                    )
                continue
            if token.value in ("class", "struct", "union", "friend", "static", "virtual", "~",
                               "template", "constexpr", "explicit", "operator"):
                _unsupported(token, "non-data class member semantics are not implemented")
            if token.value == "enum":
                raise CppDiagnostic("cpp.unsupported.enum-class-scope",
                                    "class-member enums require class-qualified lookup and access semantics",
                                    token.coord)
            if (token.value == dtype.name
                    and parser.cursor.tokens[parser.cursor.position + 1].value == "("):
                _unsupported(token, "user-declared constructor semantics are not implemented")
            mutable = parser._accept("mutable") is not None
            base = parser._type()
            while True:
                member, member_type = parser._declarator(base)
                if parser._peek().value in ("(", "=", "{", ":"):
                    _unsupported(parser._peek(),
                                 "member functions, bit-fields and default member initializers are not implemented")
                definition.add_member(Symbol(member.value, "member", member.coord, dtype=member_type), mutable)
                if not parser._accept(","):
                    break
            parser._expect(";")
    finally:
        parser.scope = parent
    parser._expect(";")
    definition.finish()
    return definition.c_declaration(parser._scalar_type)


def member_access(parser, expression, operator, member):
    """Resolve a data member, check access, then emit the shared C StructRef."""
    dtype = expression.dtype
    if operator == "->":
        pointer = unqualified(dtype)
        if not isinstance(pointer, PointerType):
            raise CppDiagnostic("cpp.invalid-member-access", "'->' requires a class pointer", member.coord)
        dtype = pointer.pointee
    elif operator != ".":
        raise ValueError("invalid member access operator")
    owner = unqualified(dtype)
    if not isinstance(owner, ClassType):
        raise CppDiagnostic("cpp.invalid-member-access", "member access requires a class object", member.coord)
    require_complete(owner, member.coord)
    definition = owner.definition
    # Qualified member lookup must not find an unrelated enclosing declaration.
    symbol = definition.scope.names.get(member.value)
    if symbol is None:
        raise CppDiagnostic("cpp.no-member", "class '" + owner.name + "' has no member '" + member.value + "'", member.coord)
    if symbol.kind != "member":
        raise CppDiagnostic("cpp.member-not-value", "member '" + member.value + "' is not a data member", member.coord)
    context = parser.scope
    while context is not None and context is not definition.scope:
        context = context.parent
    if symbol.access != "public" and context is None:
        raise CppDiagnostic("cpp.member-access", "'" + member.value + "' is a " + symbol.access + " member", member.coord)
    if not expression.lvalue and operator == ".":
        _unsupported(member, "class temporary materialization and xvalue members are not implemented")
    result_type = symbol.dtype
    if isinstance(dtype, ConstType) and not symbol.mutable:
        result_type = qualify_const(result_type)
    node = c_ast.StructRef(expression.node, operator, c_ast.ID(symbol.c_name, member.coord), member.coord)
    return type(expression)(node, result_type, lvalue=True)


def default_initialize(dtype, coord):
    """Check implicit trivial default construction; no synthetic constructor."""
    require_complete(dtype, coord)
    owner = unqualified(dtype)
    if not isinstance(owner, ClassType):
        raise TypeError("default_initialize requires a class type")
    if owner.definition.default_constructor_deleted:
        raise CppDiagnostic("cpp.deleted-default-constructor", "default constructor of '" + owner.name + "' is deleted", coord)
    if isinstance(dtype, ConstType) and not owner.definition.const_default_constructible:
        raise CppDiagnostic("cpp.const-initializer", "const class requires initialization of its subobjects", coord)
    return None


def empty_list_initialize(dtype, coord):
    """Empty-list initialization at the no-user-constructor boundary.

    An aggregate's omitted elements are value-initialized even if a const scalar
    member deletes its default constructor. A non-aggregate instead invokes its
    constructor, so that deleted-default-constructor distinction must survive.
    """
    require_complete(dtype, coord)
    owner = unqualified(dtype)
    if not isinstance(owner, ClassType):
        raise TypeError("empty_list_initialize requires a class type")
    definition = owner.definition
    if not definition.aggregate and definition.default_constructor_deleted:
        raise CppDiagnostic("cpp.deleted-default-constructor", "default constructor of '" + owner.name + "' is deleted", coord)
    if (not definition.aggregate and isinstance(dtype, ConstType)
            and not definition.const_default_constructible):
        # Keep the value-init/default-init const constraint boundary explicit
        # until N4659 [dcl.init]/8.2 and its later DR handling are qualified.
        raise CppDiagnostic("cpp.unsupported.const-class-initialization",
                            "const non-aggregate value-initialization constraints are not qualified", coord)
    values = []
    for member in definition.members:
        if is_class_type(member.dtype):
            values.append(empty_list_initialize(member.dtype, coord))
        else:
            # All admitted scalar/pointer members have ordinary C zero-init
            # semantics; no class conversion or user constructor is bypassed.
            values.append(c_ast.Constant("int", "0", coord))
    if not values:
        values.append(c_ast.Constant("int", "0", coord))
    return c_ast.InitList(values, coord)
