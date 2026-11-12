"""Nominal C++ class identity and checked projection to PCC's C object model.

This layer owns class declaration/completeness, non-static data members and
access. It does not erase class syntax or give a C struct C++ constructor
semantics. Implicit trivial special members and bounded complete-object
constructors use this layout; constructors.py owns explicit construction of
scalar/pointer subobjects and declared C1 members with bounded closed lifetime
proofs when destruction is nontrivial.
destructors.py and lifetimes.py own bounded normal exits and explicitly
declared member destruction. virtual_functions.py owns the table pointer and
dispatch of polymorphic classes without bases. Bases, unions, implicit
non-trivial special members and exception unwinding remain gaps.

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
from pcc.frontends.cpp.constants import NOT_CONSTANT, not_constant, unmodeled
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import ConstType, PointerType, array_type, contains_function, qualify_const, unqualified


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
        self.declaration_symbol = None
        self.c_tag = "__pcc_cpp_class_" + "".join(
            str(len(part)) + part for part in self.qualified_name
        )
        self.definition = None

    @property
    def complete(self):
        return self.definition is not None and self.definition.complete


def is_class_type(dtype):
    return isinstance(unqualified(dtype), ClassType)


def record_declaration_projection_supported(dtype, types):
    """A nonempty trivial public record can retain its C declaration shape.

    This is not admission of construction/copy/materialization at a call. Those
    operations still have independent frontend guards. Restrict the projection
    to records whose existing object layout is the ordinary shared C layout.
    """
    dtype = unqualified(dtype)
    if not is_class_type(dtype) or not dtype.complete:
        return False
    definition = dtype.definition
    if (not definition.members or definition.constructors.overloads
            or definition.destructor is not None or definition.polymorphic
            or definition.base is not None):
        return False
    for member in definition.members:
        plain = unqualified(member.dtype)
        if (member.access != "public" or getattr(member, "default_initializer_tokens", None) is not None
                or not (isinstance(plain, PointerType)
                        or isinstance(plain, str) and types.arithmetic_operand(plain))):
            return False
    return True


def require_complete(dtype, coord):
    """Require an object type to be complete, without completing pointees."""
    dtype = unqualified(dtype)
    if isinstance(dtype, ClassType) and getattr(dtype, 'layout_state', None) == 'failed':
        # A late layout/emission check can fail after a definition was marked
        # complete. Never let that predicate erase the original sticky error.
        raise dtype.layout_failure
    if isinstance(dtype, ClassType) and not dtype.complete:
        complete_layout = getattr(dtype, "complete_layout", None)
        if complete_layout is not None:
            complete_layout(coord)
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
        self.scope.class_type = dtype
        self.class_key = class_key
        self.access = "private" if class_key == "class" else "public"
        self.members = []
        self.method_declarations = []
        self.inline_constructors = []
        self.defaulted_constructors = []
        self.inline_methods = []
        self.default_arguments = []
        # Nominal class identities, not copied declarations or access flags.
        # Forward completion must not replace the nominated friend's identity.
        self.friend_classes = []
        # ABI lexical ordinal for unnamed class-scope types, including empty ones.
        self.unnamed_type_count = 0
        self.constructors = Symbol(dtype.name, "function", dtype.coord)
        self.destructor = None
        # Virtual members in declaration order: the table entry order.
        self.virtual_functions = []
        # The single direct non-virtual base specifier (inheritance.py), if any.
        self.base = None
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
        from pcc.frontends.cpp.types import ReferenceType
        if isinstance(dtype, ReferenceType):
            raise CppDiagnostic("cpp.unsupported.references", "reference data members require initialization/lifetime semantics", symbol.coord)
        if contains_function(dtype):
            raise CppDiagnostic("cpp.unsupported.function-pointers", "callback members require function-value semantics", symbol.coord)
        if array_type(dtype):
            from pcc.frontends.cpp.arrays import require_trivial_member_array
            require_trivial_member_array(dtype, symbol.coord)
        if dtype == "void":
            raise CppDiagnostic(
                "cpp.invalid-object-type", "a data member cannot have type void", symbol.coord
            )
        require_complete(dtype, symbol.coord)
        from pcc.frontends.cpp.virtual_functions import require_concrete
        require_concrete(dtype, symbol.coord)
        from pcc.frontends.cpp.arrays import const_array_elements
        if mutable and const_array_elements(symbol.dtype):
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
        from pcc.frontends.cpp.member_initialization import require_declared_constructor
        if self.destructor is not None and not getattr(self.destructor, 'destructor_defaulted', False):
            from pcc.frontends.cpp.destructors import member_destructors
            member_destructors(self)
        if self.constructors.overloads:
            for member in self.members:
                if is_class_type(member.dtype):
                    require_declared_constructor(member)
                if member.name == self.dtype.name:
                    raise CppDiagnostic("cpp.class-name-member",
                                        "a class with a constructor cannot have a same-named data member", member.coord)
        from pcc.frontends.cpp.inheritance import check_derived_definition
        check_derived_definition(self)
        self.complete = True
        return self

    @property
    def polymorphic(self):
        return bool(self.virtual_functions)

    @property
    def abstract(self):
        # Without bases every pure virtual member is its own final overrider.
        return any(getattr(symbol, "method_pure", False) for symbol in self.virtual_functions)

    @property
    def aggregate(self):
        # C++17 excludes user-provided/explicit constructors, not every
        # user-declared constructor. A first-declaration, non-explicit defaulted
        # C1 does not prevent aggregate initialization, even if private/deleted.
        # [dcl.init.aggr]/1 also excludes classes with virtual functions.
        # [dcl.init.aggr]/1 admits public non-virtual bases in C++17, but their
        # leading base elements are not modeled; keep derived classes outside
        # aggregate initialization (an explicit boundary at its use).
        return (not self.polymorphic and self.base is None
                and all(not getattr(constructor, "constructor_user_provided", True)
                        and not getattr(constructor, "constructor_explicit", False)
                        for constructor in self.constructors.overloads)
                and all(member.access == "public" for member in self.members))

    @property
    def const_default_constructible(self):
        # Scalar DMI initializes its member; remaining subobjects recurse.
        # Empty classes satisfy this vacuously; scalar members do not.
        return all(
            getattr(member, "default_initializer_tokens", None) is not None
            or (is_class_type(member.dtype)
                and unqualified(member.dtype).definition.const_default_constructible)
            for member in self.members
        )

    @property
    def default_constructor_deleted(self):
        from pcc.frontends.cpp.arrays import const_array_elements
        for member in self.members:
            if getattr(member, "default_initializer_tokens", None) is not None:
                continue
            dtype = unqualified(member.dtype)
            if isinstance(dtype, ClassType):
                definition = dtype.definition
                if definition.default_constructor_deleted:
                    return True
                if (isinstance(member.dtype, ConstType)
                        and not definition.const_default_constructible):
                    return True
            elif const_array_elements(member.dtype):
                return True
        return False

    @property
    def copy_assignment_deleted(self):
        from pcc.frontends.cpp.arrays import const_array_elements
        return any(
            const_array_elements(member.dtype)
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
        from pcc.frontends.cpp.inheritance import base_field
        leading = base_field(self, project_type)
        if leading is not None:
            # Itanium 2.4: the primary non-virtual base subobject is at offset 0.
            declarations.insert(0, leading)
        if self.polymorphic:
            from pcc.frontends.cpp.virtual_functions import vptr_field
            declarations.insert(0, vptr_field(self))
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
    parser.last_class_definition = None
    keyword = parser.cursor.take()
    if keyword.value not in ("class", "struct"):
        _unsupported(keyword, "union object/lifetime semantics are not implemented")
    if parser.parsing_template:
        _unsupported(keyword, "class template semantics are not implemented")
    if parser.scope.kind != "namespace":
        _unsupported(keyword, "local and nested class semantics are not implemented")
    name = parser._identifier()
    previous = parser.scope.names.get(name.value)
    if getattr(previous, "namespace_object", False):
        raise CppDiagnostic("cpp.unsupported.tag-hiding",
                            "class/object dual name lookup is not implemented", name.coord)
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
    dtype.declaration_symbol = previous
    if parser._accept(";"):
        return c_ast.Decl(
            None, [], [], [], c_ast.Struct(dtype.c_tag, None, name.coord), None, None, name.coord
        )
    base = None
    if parser._peek().value == ":":
        from pcc.frontends.cpp.inheritance import parse_base_clause
        base = parse_base_clause(parser, keyword.value, dtype)
    if parser._peek().value != "{":
        _unsupported(parser._peek(), "elaborated class declarations are not implemented here")
    parser._expect("{")
    definition = ClassDefinition(dtype, parser.scope, keyword.value)
    definition.base = base
    definition.layout_types = parser.types
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
            if token.value == "friend":
                from pcc.frontends.cpp.friend_classes import parse_friend_class
                parse_friend_class(parser, definition)
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
                prior_names = set(definition.scope.names)
                member_count = len(prior_names)
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
                for added in set(definition.scope.names) - prior_names:
                    alias = definition.scope.names[added]
                    alias.access, alias.class_owner = definition.access, dtype
                continue
            if token.value == "~":
                from pcc.frontends.cpp.destructors import parse_destructor
                definition.method_declarations.append(parse_destructor(parser, definition))
                continue
            if token.value == "explicit":
                parser.cursor.take()
                next_token = parser._peek()
                if (next_token.value == dtype.name
                        and parser.cursor.tokens[parser.cursor.position + 1].value == "("):
                    from pcc.frontends.cpp.constructors import parse_constructor
                    definition.method_declarations.append(parse_constructor(parser, definition, explicit=True))
                    continue
                if next_token.value in ("operator", "inline", "constexpr", "("):
                    _unsupported(token, "conversion functions and combined/parenthesized constructor specifiers are not implemented")
                raise CppDiagnostic("cpp.invalid-explicit", "explicit requires a constructor or conversion function", token.coord)
            if token.value == "virtual":
                from pcc.frontends.cpp.virtual_functions import parse_virtual_member
                definition.method_declarations.append(parse_virtual_member(parser, definition))
                continue
            if token.value in ("class", "struct", "union",
                               "template", "constexpr", "operator"):
                _unsupported(token, "non-data class member semantics are not implemented")
            if token.value == "enum":
                from pcc.frontends.cpp.enums import parse_enum
                parse_enum(parser)
                continue
            if (token.value == dtype.name
                    and parser.cursor.tokens[parser.cursor.position + 1].value == "("):
                from pcc.frontends.cpp.constructors import parse_constructor
                definition.method_declarations.append(parse_constructor(parser, definition))
                continue
            member_start = parser.cursor.position
            mutable = parser._accept("mutable") is not None
            if parser._peek().value in ("static", "extern", "inline"):
                if mutable:
                    raise CppDiagnostic("cpp.invalid-mutable", "mutable applies only to non-static data members", token.coord)
                definition.method_declarations.append(parser._function(member_definition=definition))
                continue
            storage, inline_specifiers = [], []
            base = parser._type(storage_specifiers=storage, inline_specifiers=inline_specifiers)
            if storage or inline_specifiers:
                if mutable:
                    raise CppDiagnostic("cpp.invalid-mutable", "mutable applies only to non-static data members", token.coord)
                parser.cursor.position = member_start
                definition.method_declarations.append(parser._function(member_definition=definition))
                continue
            if parser._peek().value == "operator":
                if mutable:
                    raise CppDiagnostic("cpp.invalid-mutable", "mutable applies only to data members", token.coord)
                parser.cursor.position = member_start
                definition.method_declarations.append(parser._function(member_definition=definition))
                continue
            prior_data = False
            function_member = False
            while True:
                from pcc.frontends.cpp.operators import declaration_name
                member, member_type = parser._declarator(
                    base, parse_name=lambda: declaration_name(parser))
                if member.kind == 'OPERATOR_FUNCTION_ID' and member.value not in ('operator[]', 'operator='):
                    _unsupported(member, 'this prefixed operator declarator requires separate operator semantics')
                if member.kind == 'OPERATOR_FUNCTION_ID' and parser._peek().value != '(':
                    code = ('cpp.invalid-assignment-operator' if member.value == 'operator='
                            else 'cpp.invalid-subscript-operator')
                    raise CppDiagnostic(code, member.value + ' must declare a function', member.coord)
                if parser._peek().value == "(":
                    if prior_data:
                        _unsupported(member, "mixed data/function declarator lists are not implemented")
                    if mutable:
                        raise CppDiagnostic("cpp.invalid-mutable", "mutable applies only to data members", member.coord)
                    parser.cursor.position = member_start
                    definition.method_declarations.append(parser._function(member_definition=definition))
                    function_member = True
                    break
                if parser._peek().value in ("{", ":"):
                    _unsupported(parser._peek(), "braced default member initializers and bit-fields are not implemented")
                symbol = definition.add_member(Symbol(member.value, "member", member.coord, dtype=member_type), mutable)
                if parser._accept("="):
                    from pcc.frontends.cpp.default_member_initializers import collect, unsupported
                    if is_class_type(member_type) or array_type(member_type):
                        unsupported(member.coord)
                    symbol.default_initializer_tokens = collect(parser)
                    break
                prior_data = True
                if not parser._accept(","):
                    break
            # _function consumes its own declaration/body terminator. Only
            # data-member declarators share the semicolon requirement here.
            if not function_member and parser.cursor.tokens[parser.cursor.position - 1].value != ";":
                parser._expect(";")
    finally:
        parser.scope = parent
    parser._expect(";")
    definition.finish()
    if definition.polymorphic:
        # After every member prototype and before any published body.
        from pcc.frontends.cpp.virtual_functions import complete_class
        definition.method_declarations.extend(complete_class(parser, definition))
    if any(array_type(member.dtype) for member in definition.members):
        from pcc.frontends.cpp.size_queries import sizeof_type
        sizeof_type(dtype, parser.types, dtype.coord)
    # Complete deletion/triviality before binding any deferred expression:
    # even an unevaluated D1 call in a class default argument checks deletion.
    from pcc.frontends.cpp.default_destructors import bind_definition
    bind_definition(definition)
    from pcc.frontends.cpp.default_arguments import bind_class
    bind_class(parser, definition)
    from pcc.frontends.cpp.default_member_initializers import bind
    bind(parser, definition)
    from pcc.frontends.cpp.default_constructors import bind_defaulted_definitions
    bind_defaulted_definitions(parser, definition)
    from pcc.frontends.cpp.inline_constructors import bind_definitions
    bind_definitions(parser, definition)
    from pcc.frontends.cpp.inline_methods import bind_methods
    bind_methods(parser, definition)
    parser.last_class_definition = definition
    return definition.c_declaration(parser._scalar_type)


def member_access(parser, expression, operator, member):
    """Resolve a data member, check access, then emit the shared C StructRef."""
    if getattr(parser, 'class_template_method', None) is not None:
        from pcc.frontends.cpp.class_template_members import pattern_member_access
        return pattern_member_access(parser, expression, operator, member)
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
    if member.value == 'operator=' and getattr(owner, 'template_origin', None) is not None:
        raise CppDiagnostic('cpp.unsupported.class-operators',
                            'class-template assignment calls require their own body owner', member.coord)
    definition = owner.definition
    # Qualified member lookup must not find an unrelated enclosing declaration.
    symbol = definition.scope.names.get(member.value)
    inherited = False
    if symbol is None and definition.base is not None:
        # [class.member.lookup]: a single base chain, nearest declaration first.
        from pcc.frontends.cpp.inheritance import base_pointer, base_subobject, find_member, require_member_access
        found, declaring = find_member(owner, member.value)
        if found is not None:
            if found.kind in ("member", "function") and not getattr(found, "static_member", False):
                require_member_access(parser.scope, found, owner, owner, member.coord)
            elif getattr(found, "access", "public") != "public":
                require_member_access(parser.scope, found, owner, None, member.coord)
            expression = (base_pointer(expression, declaring, member.coord) if operator == "->"
                          else base_subobject(parser, expression, declaring, member.coord))
            dtype = expression.dtype if operator == "." else unqualified(expression.dtype).pointee
            owner, definition, symbol, inherited = declaring, declaring.definition, found, True
    cursor = getattr(parser, "cursor", None)
    if (symbol is None and cursor is not None and cursor.peek().value == ":"
            and cursor.position + 1 < len(cursor.tokens) and cursor.tokens[cursor.position + 1].value == ":"):
        # x.B::m names m through a nested-name-specifier ([basic.lookup.classref]).
        raise CppDiagnostic("cpp.unsupported.base-classes",
                            "qualified class member access (x.C::m) is not implemented", member.coord)
    if symbol is None:
        if member.value == 'operator=':
            raise CppDiagnostic('cpp.unsupported.class-assignment',
                                'implicit assignment operator lookup and synthesis are not implemented', member.coord)
        raise CppDiagnostic("cpp.no-member", "class '" + owner.name + "' has no member '" + member.value + "'", member.coord)
    if symbol.kind == "function":
        from pcc.frontends.cpp.members import call_member, call_static_member, is_static_group
        if is_static_group(symbol):
            if not parser._accept("("):
                raise CppDiagnostic("cpp.unsupported.function-pointers", "static function names require an immediate call", member.coord)
            arguments = parser._call_arguments()
            if operator == ".":
                if not expression.lvalue or not expression.discarded_known:
                    raise CppDiagnostic("cpp.unsupported.discarded-class-expression",
                                        "static call base needs modeled discarded class evaluation", member.coord)
                discarded = expression.discarded_node
            else:
                discarded = expression.node
            return call_static_member(parser, symbol, arguments, member.coord, discarded)
        if operator == ".":
            if not expression.lvalue:
                _unsupported(member, "class temporary materialization is not implemented")
            # The common C address-only owner now lowers this glvalue without
            # fetching its aggregate value. Keep exact nominal/cv identity;
            # call_member captures the resulting address before every argument.
            object_expression = parser._unary_expression("&", expression, member.coord)
        else:
            object_expression = expression
        if not parser._accept("("):
            raise CppDiagnostic("cpp.unsupported.member-pointers", "a member function name requires an immediate call", member.coord)
        arguments = parser._call_arguments()
        if member.value == 'operator=':
            from pcc.frontends.cpp.operators import require_explicit_assignment_candidates
            require_explicit_assignment_candidates(owner, symbol, arguments, member.coord)
        return call_member(parser, symbol, object_expression, arguments, member.coord,
                           address_available=True)
    if not inherited:
        parser.scope.check_member_access(symbol, member.coord)
    if symbol.kind == "enumerator":
        if operator == ".":
            if not expression.lvalue:
                _unsupported(member, "class temporary materialization is not implemented")
            if not expression.discarded_known:
                raise CppDiagnostic("cpp.unsupported.discarded-class-expression",
                                    "class glvalue discarded evaluation is not modeled", member.coord)
            discarded, fact = expression.discarded_node, expression.discarded_fact
        else:
            discarded = expression.node
            fact = parser._constant_fact(expression)
        result = parser._enum_constant_expression(symbol.dtype, symbol.enum_value, member.coord)
        if discarded is not None:
            result.node = c_ast.ExprList([discarded, result.node], member.coord)
        if fact is not None and fact.status == NOT_CONSTANT:
            result.constant_fact = not_constant(result.dtype, fact.reason, fact.coord)
        else:
            # A known enumerator value does not prove the evaluated base is a
            # core constant expression. Object/pointer constexpr analysis is a
            # separate dependency; do not erase calls or invent such a proof.
            result.constant_fact = unmodeled(result.dtype, "object enumerator base constant evaluation", member.coord)
        return result
    if symbol.kind != "member":
        raise CppDiagnostic("cpp.member-not-value", "member '" + member.value + "' is not a data member", member.coord)
    if not expression.lvalue and operator == ".":
        _unsupported(member, "class temporary materialization and xvalue members are not implemented")
    result_type = symbol.dtype
    if isinstance(dtype, ConstType) and not symbol.mutable:
        result_type = qualify_const(result_type)
    node = c_ast.StructRef(expression.node, operator, c_ast.ID(symbol.c_name, member.coord), member.coord)
    result = type(expression)(node, result_type, lvalue=True, decltype_entity=symbol.dtype)
    if is_class_type(result_type) or unqualified(result_type) == "nullptr":
        # Selecting a class or nullptr_t subobject as a glvalue does not load that object.
        # Keep only evaluation required by its base, with the nominal/member
        # checks already performed above. No C-AST pattern guessing is needed.
        result.discarded_known = operator == "->" or expression.discarded_known
        result.discarded_node = expression.node if operator == "->" else expression.discarded_node
        result.discarded_fact = (parser._constant_fact(expression) if operator == "->"
                                 else expression.discarded_fact)
    return result


def _check_implicit_default_destructor_access(definition):
    """Check each implicit constructor in its own member-access context."""
    pending = [definition]
    while pending:
        current = pending.pop()
        if current.constructors.overloads:
            # A user-declared constructor owns its subobject initialization.
            continue
        for member in current.members:
            if not is_class_type(member.dtype):
                continue
            child = unqualified(member.dtype).definition
            # [class.ctor]/5.8,7: a declared enclosing D1 does not supply
            # this constructor check. No destructor body proof is needed.
            if child.destructor is not None:
                from pcc.frontends.cpp.destructors import check_usable
                check_usable(current.scope, child.destructor, member.coord)
            pending.append(child)


def default_initialize(dtype, coord):
    """Check implicit trivial default construction; no synthetic constructor."""
    require_complete(dtype, coord)
    owner = unqualified(dtype)
    if not isinstance(owner, ClassType):
        raise TypeError("default_initialize requires a class type")
    pending = [owner.definition]
    while pending:
        current = pending.pop()
        if current.constructors.overloads:
            raise CppDiagnostic("cpp.unsupported.constructor-subobjects",
                                "implicit subobject construction requires lifetime integration", coord)
        children = [unqualified(member.dtype).definition for member in current.members
                    if is_class_type(member.dtype)]
        from pcc.frontends.cpp.default_member_initializers import has_initializers, unsupported
        if any(has_initializers(child) for child in children):
            unsupported(coord)
        pending.extend(children)
    _check_implicit_default_destructor_access(owner.definition)
    if owner.definition.default_constructor_deleted:
        raise CppDiagnostic("cpp.deleted-default-constructor", "default constructor of '" + owner.name + "' is deleted", coord)
    if isinstance(dtype, ConstType) and not owner.definition.const_default_constructible:
        raise CppDiagnostic("cpp.const-initializer", "const class requires initialization of its subobjects", coord)
    return None


def check_aggregate_destructor_access(definition, scope):
    """CWG2227: actual aggregate elements use their initializer's context.

    This explicit-brace owner initializes every aggregate element, including
    omissions. A nonaggregate instead invokes its constructor; do not borrow
    the caller's privileges for that constructor's own subobjects.
    """
    pending = [definition]
    while pending:
        current = pending.pop()
        if not current.aggregate:
            continue
        for member in current.members:
            if not is_class_type(member.dtype):
                continue
            child = unqualified(member.dtype).definition
            if child.destructor is not None:
                from pcc.frontends.cpp.destructors import check_usable
                check_usable(scope, child.destructor, member.coord)
            pending.append(child)


def empty_list_initialize(dtype, coord, *, scope):
    """Validate real list-initialization before projecting its zero storage."""
    require_complete(dtype, coord)
    if not is_class_type(dtype):
        raise TypeError("empty_list_initialize requires a class type")
    check_aggregate_destructor_access(unqualified(dtype).definition, scope)
    return _empty_list_initialize(dtype, coord)


def _empty_list_initialize(dtype, coord):
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
    from pcc.frontends.cpp.default_member_initializers import has_initializers, unsupported
    if has_initializers(definition):
        unsupported(coord)
    if definition.constructors.overloads and not definition.aggregate:
        raise CppDiagnostic("cpp.unsupported.constructor-list-initialization",
                            "constructor list initialization requires separate overload rules", coord)
    if not definition.aggregate:
        # Only a nonaggregate's empty list invokes its implicit default
        # constructor. Aggregate lists may bypass a deleted default ctor.
        _check_implicit_default_destructor_access(definition)
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
            # Under a nonaggregate this InitList is only zero-storage
            # projection, not a new caller-context aggregate initialization.
            values.append(_empty_list_initialize(member.dtype, coord))
        elif array_type(member.dtype):
            from pcc.frontends.cpp.arrays import zero_array_initializer
            values.append(zero_array_initializer(member.dtype, coord))
        else:
            # All admitted scalar/pointer members have ordinary C zero-init
            # semantics; no class conversion or user constructor is bypassed.
            values.append(c_ast.Constant("int", "0", coord))
    if not values:
        values.append(c_ast.Constant("int", "0", coord))
    return c_ast.InitList(values, coord)
