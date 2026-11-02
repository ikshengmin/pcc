"""Qualified non-member function declarations retain their namespace entity.

N4659 [namespace.memdef]/2, [namespace.qual]/6-7 and [basic.lookup.qual]/3.
Using-directives may find the qualifier, but cannot manufacture a member of
the nominated namespace. No template binder or C/backend ownership lives here.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic


def declaration_scope(nominated, lexical, name, coord):
    """Find the already-declared namespace before parsing the parameters.

    An inline namespace's members may be named through its enclosing namespace.
    If the name combines overloads from distinct namespace scopes, selecting
    the parameter lookup context needs additional declarator machinery; keep
    that shape explicit rather than choosing by overload insertion order.
    """
    if lexical.kind != "namespace":
        raise CppDiagnostic("cpp.namespace-definition-scope",
                            "qualified namespace functions require an enclosing namespace scope", coord)
    # Lookup must diagnose ambiguity across all declaration kinds before we
    # restrict the result to genuine namespace-owned functions. In particular,
    # a function in an inline namespace cannot hide a class/enum in a different
    # namespace ([namespace.qual]/6). Reuse ordinary qualified lookup unchanged.
    resolved = nominated.lookup_qualified(name, coord)
    if resolved is None or resolved.kind != "function":
        raise CppDiagnostic("cpp.namespace-member-not-found",
                            "qualified function must already be declared in the nominated namespace", coord)
    allowed = nominated._inline_set()
    locations = []
    for scope in allowed:
        group = scope.names.get(name)
        if group is None or group.kind != "function":
            continue
        for candidate in group.overloads:
            # An imported using-declaration is not a declaration of a new
            # namespace member. C-linkage entities can instead have genuine
            # declarations in more than one namespace; retain that scope.
            owner = scope if scope in candidate.declaration_scopes else candidate.owner
            if owner in allowed and owner not in locations:
                locations.append(owner)
    if not locations:
        raise CppDiagnostic("cpp.namespace-member-not-found",
                            "qualified function must already be declared in the nominated namespace", coord)
    if len(locations) != 1:
        raise CppDiagnostic("cpp.unsupported.qualified-names",
                            "qualified overload sets spanning inline namespaces require additional declarator lookup", coord)
    owner = locations[0]
    enclosing = owner
    while enclosing is not None and enclosing is not lexical:
        enclosing = enclosing.parent
    if enclosing is None:
        raise CppDiagnostic("cpp.namespace-definition-scope",
                            "definition is not in a namespace enclosing the declared function", coord)
    return owner


def declare_function(scope, symbol, definition):
    """Redeclare an exact existing overload; never add a new qualified one."""
    group = scope.names.get(symbol.name)
    matches = [] if group is None or group.kind != "function" else [
        candidate for candidate in group.overloads
        if not candidate.template_parameters
        and candidate.parameters == symbol.parameters
        and candidate.variadic == symbol.variadic
        and (candidate.owner is scope or scope in candidate.declaration_scopes)
    ]
    if not matches:
        raise CppDiagnostic("cpp.no-matching-namespace-function",
                            "qualified declaration does not match an existing namespace function", symbol.coord)
    # This existing owner checks return type, language linkage, exception
    # contracts and redefinition, and returns the original canonical Symbol.
    return scope.declare_function(symbol, definition)
