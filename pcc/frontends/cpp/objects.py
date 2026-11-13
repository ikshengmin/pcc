"""Namespace objects with checked identity, linkage and static initialization.

This is not a dynamic-initialization runtime: only target-proven integral/enum
values, zero initialization, null pointers, addresses of declared namespace
objects and static data members, and ordinary string literals (a `const char*`
initialized with the literal's static array, [conv.array]) are admitted. Class lifetime, floating constant evaluation, inline/TLS,
block extern/static and dependent object declarations stay explicit gaps.

Rules: N4659 [basic.def], [basic.link], [basic.start.static], [dcl.init].
ABI: https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling
Original implementation; no upstream compiler source is copied.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.constants import KNOWN, converted_fact
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import ConstType, array_type, is_enum_type, pointer_type, unqualified


def static_initializer(parser, dtype, coord):
    """`= expression` of an object with static storage duration ([basic.start.static]).

    Returns the projected constant initializer and, for integral/enum types,
    its proof. Anything needing dynamic initialization stays explicit.
    """
    equals = parser._accept("=")
    if parser._peek().value == "{":
        raise CppDiagnostic("cpp.unsupported.namespace-object-initialization",
                            "namespace list initialization is not implemented", parser._peek().coord)
    assert equals is not None
    expression = parser._expression()
    parser._require_value(expression)
    converted = parser._convert(expression, dtype, coord)
    if pointer_type(dtype):
        if expression.null_pointer or expression.static_address is not None:
            # A null pointer is a target pointer constant, not a host int.
            return converted, None
        node = expression.node
        if isinstance(node, c_ast.Constant) and node.type == "string":
            # The literal's array object has static storage; its decayed
            # address is an address constant ([expr.const]/6).
            return converted, None
        raise CppDiagnostic("cpp.unsupported.static-initialization-analysis",
                            "pointer static initializer requires a null constant, a string literal or a namespace object address", coord)
    if parser.types.integral(dtype) or is_enum_type(dtype):
        proof = converted_fact(parser._constant_fact(expression), dtype, parser.types, coord)
        if proof.status != KNOWN:
            raise CppDiagnostic("cpp.unsupported.dynamic-initialization",
                                "initializer is not a modeled target constant; dynamic initialization is not implemented", coord)
        # Static storage needs a typed constant initializer. This does not
        # fold executable expressions or publish a mutable object's proof.
        return parser._enum_constant_expression(dtype, proof.value, coord).node, proof
    raise CppDiagnostic("cpp.unsupported.static-initialization-analysis",
                        "floating static constant evaluation is not implemented", coord)


def zero_initializer(parser, dtype, coord):
    """[dcl.init]/6: a definition without an initializer is zero-initialized."""
    if isinstance(dtype, ConstType):
        raise CppDiagnostic("cpp.const-initializer", "const objects require an initializer", coord)
    zero = c_ast.Constant("int", "0", coord)
    target = c_ast.Typename(None, [], parser._scalar_type(None, dtype, coord), coord)
    return c_ast.Cast(target, zero, coord)


def parse_namespace_object(parser, name, dtype, storage, implicit_extern=False):
    """Bind before initialization; merge declarations of the same entity.

    A prior extern declaration and its later definition retain source order,
    sharing one C++ identity and C name. A later initializer must not be moved
    before the declarations it references. Every C++ definition has an initializer,
    so C tentative-definition rules cannot weaken C++'s one-definition check.
    """
    coord = name.coord
    if name.value == "main" and not parser.scope.namespace_path():
        raise CppDiagnostic("cpp.main-object", "main cannot name a global object", coord)
    if array_type(dtype):
        raise CppDiagnostic("cpp.unsupported.namespace-array-objects",
                            "namespace arrays require static aggregate initialization and ODR rules", coord)
    if is_class_type(dtype):
        raise CppDiagnostic("cpp.unsupported.static-class-lifetime",
                            "namespace class objects require lifetime/static initialization semantics", coord)
    parser._check_object_type(dtype, coord)
    has_initializer = parser._peek().value in ("=", "{")
    specifier = storage.value if storage is not None else ("extern" if implicit_extern else None)
    definition = specifier != "extern" or has_initializer
    fresh = Symbol(name.value, "value", coord, dtype=dtype)
    fresh.requested_language_linkage = parser.language_linkage
    symbol = parser.scope.declare_namespace_object(fresh, definition, specifier)
    first = symbol is fresh
    if first:
        symbol.object_declaration = parser._decl(symbol.c_name, dtype, coord)
        parser.namespace_objects.append(symbol)
    decl = (parser._decl(symbol.c_name, dtype, coord)
            if definition and not first else symbol.object_declaration)
    init = None
    proof = None
    if has_initializer:
        init, proof = static_initializer(parser, dtype, coord)
    elif definition:
        init = zero_initializer(parser, dtype, coord)
    if parser._peek().value == ",":
        raise CppDiagnostic("cpp.unsupported.init-declarator-list",
                            "multiple object declarators require separate point-of-declaration processing", parser._peek().coord)
    parser._expect(";")
    if definition:
        decl.init = init
        decl.storage = ["static"] if symbol.internal_linkage else []
        symbol.initialization_complete = True
        if isinstance(dtype, ConstType) and proof is not None:
            symbol.constant_fact = proof
    elif first:
        decl.storage = ["extern"]
    return decl if first or definition else None
