"""C++ ordinary-name lookup, independent of lexer token classification.

Only declarations whose semantics are implemented are installed. In particular,
a local value hides an outer type name; searching onwards for a typedef would
misparse C++ declaration/expression ambiguities.
"""

from types import MappingProxyType

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ConstType, ReferenceType, array_type, is_enum_type, mangle_function, mangle_object, pointer_type, unqualified,
)
from pcc.frontends.cpp.templates import (
    deduce, mangle_instance, same_type, signature_key, substitute,
)


class Symbol:
    def __init__(self, name, kind, coord, c_name=None, arity=None,
                 dtype="int", parameters=None):
        self.name = name
        self.kind = kind
        self.coord = coord
        self.c_name = c_name if c_name is not None else name
        self.arity = arity
        self.defined = False
        self.dtype = dtype
        self.parameters = parameters
        self.variadic = False
        self.nonthrowing = False
        self.noreturn = False
        self.overloads = []
        self.template_parameters = ()
        self.template_origin = None
        self.template_arguments = ()
        self.pattern = None
        self.parameter_symbols = []
        # Canonical template declarations bind the shared registry's value
        # view. Speculative signatures have no independent writable cache.
        self.instances = MappingProxyType({})
        self.owner = None
        self.namespace_scope = None
        self.internal_linkage = False
        # Function type/name and external object-name language linkage belong
        # to semantic identity, not a post-lowering symbol-name patch.
        # Object types themselves do not acquire language linkage.
        # None means no specification.
        self.requested_language_linkage = None
        self.language_linkage = "C++"
        self.declaration_scopes = []
        # Constant-expression proof belongs to this declaration identity, not
        # to its spelling or C AST. Publish only after successful initialization.
        self.constant_fact = None
        self.initialization_complete = False


class Scope:
    """Lexical scopes plus a namespace graph; directives do not copy names.

    Qualified lookup follows [namespace.qual], stopping at direct/inline
    members. Unqualified lookup instead places each active transitive directive
    at its nearest common namespace ancestor ([namespace.udir]).
    """

    def __init__(self, parent=None, kind=None, name=None, inline=False, anonymous=False):
        self.parent = parent
        self.kind = kind or ("namespace" if parent is None else "block")
        self.name = name
        self.names = {}
        self.forbidden_redeclarations = frozenset()
        self.using_directives = []
        self.inline_namespaces = []
        self.inline = inline
        self.anonymous = anonymous
        self.unnamed_namespace = None
        if parent is None:
            self.c_linkage_functions = {}
            self.c_linkage_objects = {}
        self.internal_linkage = anonymous or (parent is not None and parent.internal_linkage)

    def namespace(self):
        scope = self
        while scope.kind != "namespace":
            scope = scope.parent
        return scope

    def global_scope(self):
        scope = self
        while scope.parent is not None:
            scope = scope.parent
        return scope

    def namespace_path(self):
        scope = self.namespace()
        if scope.parent is None:
            return ()
        return scope.parent.namespace_path() + (scope.name,)

    @staticmethod
    def _accepted(symbol, kinds):
        return symbol is not None and (kinds is None or symbol.kind in kinds)

    @staticmethod
    def _merge(symbols, name, coord=None):
        unique = []
        for symbol in symbols:
            if symbol not in unique:
                unique.append(symbol)
        if not unique:
            return None
        if len(unique) == 1:
            return unique[0]
        if all(symbol.kind == "function" for symbol in unique):
            group = Symbol(name, "function", coord or unique[0].coord)
            for symbol in unique:
                for candidate in symbol.overloads:
                    if candidate not in group.overloads:
                        group.overloads.append(candidate)
            return group
        if all(symbol.kind == "type" and same_type(symbol.dtype, unique[0].dtype)
               for symbol in unique):
            return unique[0]
        if all(symbol.kind in ("namespace", "namespace-alias")
               and symbol.namespace_scope is unique[0].namespace_scope for symbol in unique):
            return unique[0]
        raise CppDiagnostic("cpp.ambiguous-name", "ambiguous name '" + name + "'",
                            coord or unique[-1].coord)

    def _inline_set(self):
        result = [self]
        for child in self.inline_namespaces:
            result.extend(child._inline_set())
        return result

    def _qualified_symbols(self, name, kinds, visited):
        if self in visited:
            return []
        visited.add(self)
        scopes = self._inline_set()
        direct = [scope.names[name] for scope in scopes
                  if self._accepted(scope.names.get(name), kinds)]
        if direct:
            return direct
        found = []
        for scope in scopes:
            for nominated in scope.using_directives:
                found.extend(nominated._qualified_symbols(name, kinds, visited))
        return found

    def lookup_qualified(self, name, coord=None, kinds=None):
        return self._merge(self._qualified_symbols(name, kinds, set()), name, coord)

    @staticmethod
    def _common_namespace(first, second):
        ancestors = []
        while first is not None:
            ancestors.append(first)
            first = first.parent
        while second not in ancestors:
            second = second.parent
        return second

    def lookup(self, name, coord=None, kinds=None, stop_at=None):
        scope = self
        injected = {}
        while scope is not None:
            # Transitive directives act as if written at the original active
            # directive, not as ordinary parent lookup in the nominated scope.
            pending = list(scope.using_directives)
            visited = set()
            while pending:
                nominated = pending.pop()
                if nominated in visited:
                    continue
                visited.add(nominated)
                common = self._common_namespace(scope.namespace(), nominated)
                symbol = nominated.names.get(name)
                if self._accepted(symbol, kinds):
                    injected.setdefault(common, []).append(symbol)
                pending.extend(nominated.using_directives)
            found = list(injected.get(scope, ()))
            direct = scope.names.get(name)
            if self._accepted(direct, kinds):
                found.append(direct)
            if found:
                return self._merge(found, name, coord)
            if scope is stop_at:
                break
            scope = scope.parent
        return None

    def resolve_name(self, name, namespace_only=False, qualifier_only=False,
                     elaborated_type=False):
        """Resolve a parsed path without replacing it by a flattened spelling."""
        qualifier_kinds = ("namespace", "namespace-alias", "type", "template-type")
        if elaborated_type:
            qualifier_kinds += ("class-template",)
        final_kinds = (("type", "template-type", "class-template") if elaborated_type else
                       ("namespace", "namespace-alias") if namespace_only else
                       qualifier_kinds if qualifier_only else None)
        scope = self.global_scope() if name.global_scope else self
        symbol = None
        for index, part in enumerate(name.parts):
            kinds = final_kinds if index == len(name.parts) - 1 else qualifier_kinds
            symbol = (scope.lookup(part, name.coord, kinds) if index == 0 and not name.global_scope
                      else scope.lookup_qualified(part, name.coord, kinds))
            if elaborated_type and symbol is None:
                injected = getattr(scope, "class_type", None)
                if injected is not None and part == injected.name:
                    # [class.qual]/2 keeps an elaborated injected-class-name
                    # a type, not a constructor. Reuse its canonical symbol.
                    symbol = getattr(injected, "declaration_symbol", None)
            self.check_member_access(symbol, name.coord)
            if index != len(name.parts) - 1:
                if symbol is not None and symbol.kind in ("template-type", "class-template"):
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "dependent qualified lookup requires template/class semantics", name.coord)
                if (symbol is not None and symbol.kind == "type"
                        and getattr(unqualified(symbol.dtype), "cpp_class_type", False)):
                    owner = unqualified(symbol.dtype)
                    complete_layout = getattr(owner, "complete_layout", None)
                    if complete_layout is not None and not owner.complete:
                        complete_layout(name.coord)
                    definition = owner.definition
                    if definition is None:
                        raise CppDiagnostic("cpp.incomplete-class",
                                            "class-qualified lookup requires a class definition", name.coord)
                    scope = definition.scope
                    continue
                if symbol is not None and symbol.kind == "type" and is_enum_type(symbol.dtype):
                    scope = unqualified(symbol.dtype).scope
                    continue
                if symbol is None or symbol.namespace_scope is None:
                    raise CppDiagnostic("cpp.not-a-namespace", "'" + part + "' does not name a namespace", name.coord)
                scope = symbol.namespace_scope
        return symbol

    def check_member_access(self, symbol, coord):
        """Check the selected declaration, not a nominal type's alias history.

        A lexically enclosing class or a directly nominated class friend
        grants access while its own declarations are parsed. Friendship is
        checked by identity and is not propagated through another friend.
        Bases and friend functions retain their separate unsupported boundary.
        """
        if symbol is None or getattr(symbol, "access", "public") == "public":
            return
        owner = getattr(symbol, "class_owner", None)
        if owner is None:
            return
        deferred = getattr(self, "deferred_member_access", None)
        if deferred is not None:
            # The declaration parser resolves the owner after its return type.
            # Retain symbols, not spellings: later lookup cannot change access.
            deferred.append((symbol, coord))
            return
        context = self
        friends = getattr(getattr(owner, "definition", None), "friend_classes", ())
        while context is not None:
            enclosing_class = getattr(context, "class_type", None)
            if enclosing_class is owner or any(enclosing_class is friend for friend in friends):
                return
            context = context.parent
        raise CppDiagnostic("cpp.member-access",
                            "'" + symbol.name + "' is a " + symbol.access + " member", coord)

    def is_type_name(self, name):
        symbol = self.lookup(name)
        return symbol is not None and symbol.kind in ("type", "template-type")

    def open_namespace(self, name, coord, inline=False):
        if self.kind != "namespace":
            raise CppDiagnostic("cpp.namespace-scope", "namespace definition requires namespace scope", coord)
        if name is None:
            previous = self.unnamed_namespace
        else:
            candidates = [scope.names[name] for scope in self._inline_set() if name in scope.names]
            existing = self._merge(candidates, name, coord)
            if existing is not None and existing.kind != "namespace":
                raise CppDiagnostic("cpp.namespace-conflict", "namespace name conflicts with a declaration", coord)
            previous = existing.namespace_scope if existing is not None else None
        if previous is not None:
            if inline and not previous.inline:
                raise CppDiagnostic("cpp.namespace-inline-mismatch", "non-inline namespace cannot become inline", coord)
            return previous
        child = Scope(self, "namespace", name or "_GLOBAL__N_1", inline, name is None)
        if name is None:
            self.unnamed_namespace = child
        else:
            symbol = Symbol(name, "namespace", coord)
            symbol.owner, symbol.namespace_scope = self, child
            self.names[name] = symbol
        if inline:
            self.inline_namespaces.append(child)
        if inline or name is None:
            self.add_using_directive(child)
        return child

    def add_using_directive(self, namespace):
        if namespace not in self.using_directives:
            self.using_directives.append(namespace)

    def add_using_declaration(self, symbol, coord):
        if symbol.name in self.forbidden_redeclarations:
            raise CppDiagnostic("cpp.redeclaration", "for-init name redeclared in outermost loop body", coord)
        if symbol.kind in ("namespace", "namespace-alias"):
            raise CppDiagnostic("cpp.using-namespace-name", "using-declaration cannot name a namespace", coord)
        if getattr(symbol, "class_owner", None) is not None:
            raise CppDiagnostic("cpp.using-class-member",
                                "a class member using-declaration requires a derived class scope", coord)
        if symbol.kind == "enumerator" and symbol.enum_type.scoped:
            raise CppDiagnostic("cpp.using-scoped-enumerator",
                                "C++17 using-declarations cannot name a scoped enumerator", coord)
        hidden = self.lookup_lexical(symbol.name)
        if hidden is not None and hidden.kind in ("template-type", "template-value"):
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                "using-declaration shadows a template parameter", coord)
        previous = self.names.get(symbol.name)
        if previous is not None and previous.kind == symbol.kind == "function":
            for own in previous.overloads:
                for imported in symbol.overloads:
                    if (own is not imported and (own.owner is self or self in own.declaration_scopes)
                            and not own.template_parameters and not imported.template_parameters
                            and own.parameters == imported.parameters
                            and own.variadic == imported.variadic):
                        raise CppDiagnostic("cpp.using-declaration-conflict",
                                            "imported function conflicts with a local declaration", coord)
        merged = self._merge([s for s in (previous, symbol) if s is not None], symbol.name, coord)
        if merged.kind == "function":
            snapshot = Symbol(symbol.name, "function", coord)
            snapshot.overloads = list(merged.overloads)
            merged = snapshot
        self.names[symbol.name] = merged

    def lookup_lexical(self, name):
        """Check declaration conflicts without consulting using-directives."""
        scope = self
        while scope is not None:
            if name in scope.names:
                return scope.names[name]
            scope = scope.parent
        return None

    def declare(self, symbol):
        if symbol.name in self.forbidden_redeclarations:
            raise CppDiagnostic("cpp.redeclaration", "for-init name redeclared in outermost loop body", symbol.coord)
        hidden = self.lookup_lexical(symbol.name)
        if hidden is not None and hidden.kind in ("template-type", "template-value"):
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                f"declaration shadows template parameter '{symbol.name}'", symbol.coord)
        previous = self.names.get(symbol.name)
        if previous is not None:
            if previous.kind == symbol.kind == "type" and same_type(previous.dtype, symbol.dtype):
                return previous
            raise CppDiagnostic(
                "cpp.redeclaration", f"redeclaration of '{symbol.name}'", symbol.coord
            )
        symbol.owner = self
        self.names[symbol.name] = symbol
        return symbol

    def declare_namespace_object(self, symbol, definition, storage=None):
        if self.kind != "namespace":
            raise CppDiagnostic("cpp.unsupported.block-storage",
                                "non-namespace object linkage is not implemented", symbol.coord)
        root = self.global_scope()
        previous = self.names.get(symbol.name)
        if previous is not None:
            if (previous.owner is not self and self not in previous.declaration_scopes
                    and not (symbol.requested_language_linkage == "C"
                             and previous.language_linkage == "C")):
                raise CppDiagnostic("cpp.using-declaration-conflict",
                                    "object declaration conflicts with an imported entity", symbol.coord)
            if not getattr(previous, "namespace_object", False):
                if (previous.kind == "type" and
                        (getattr(unqualified(previous.dtype), "cpp_class_type", False)
                         or is_enum_type(previous.dtype))):
                    raise CppDiagnostic("cpp.unsupported.tag-hiding",
                                        "object/tag dual name lookup is not implemented", symbol.coord)
                raise CppDiagnostic("cpp.redeclaration", "object name conflicts with a declaration", symbol.coord)
            if not same_type(previous.dtype, symbol.dtype):
                raise CppDiagnostic("cpp.conflicting-object-type",
                                    "object redeclaration has a different type", symbol.coord)
            if storage == "static" and not previous.internal_linkage:
                raise CppDiagnostic("cpp.conflicting-linkage",
                                    "static declaration follows an external object declaration", symbol.coord)
            if (not previous.internal_linkage and symbol.requested_language_linkage is not None
                    and symbol.requested_language_linkage != previous.language_linkage):
                raise CppDiagnostic("cpp.conflicting-language-linkage",
                                    "object redeclared with different language linkage", symbol.coord)
            if previous.internal_linkage and symbol.requested_language_linkage == "C":
                # [dcl.link]/5; both C++17 references reject a C-linkage
                # redeclaration of an internal-linkage (C++-linkage) object.
                raise CppDiagnostic("cpp.conflicting-language-linkage",
                                    "C-linkage redeclaration of an internal-linkage object", symbol.coord)
            if definition and previous.defined:
                raise CppDiagnostic("cpp.redefinition", "object has more than one definition", symbol.coord)
            if definition:
                previous.defined = True
            if self not in previous.declaration_scopes:
                previous.declaration_scopes.append(self)
            return previous
        symbol.namespace_object = True
        symbol.defined = definition
        local_name = storage == "static" or isinstance(symbol.dtype, ConstType) and storage != "extern"
        symbol.internal_linkage = local_name or self.internal_linkage
        # [dcl.link] specifies the language of external variable *names*.
        # In particular a block-form static/const object does not join the
        # cross-namespace C registry merely because it is lexically in "C".
        symbol.language_linkage = (symbol.requested_language_linkage or "C++") if not symbol.internal_linkage else "C++"
        canonical = root.c_linkage_objects.get(symbol.name)
        if self is root and (canonical is not None or symbol.name in root.c_linkage_functions):
            if not (symbol.language_linkage == "C" and canonical is not None):
                raise CppDiagnostic("cpp.c-linkage-name-conflict",
                                    "a global object cannot share a distinct C entity's name", symbol.coord)
        if symbol.language_linkage == "C":
            global_object = root.names.get(symbol.name)
            if (symbol.name in root.c_linkage_functions
                    or getattr(global_object, "namespace_object", False)
                    and global_object.owner is root and global_object is not canonical):
                raise CppDiagnostic("cpp.c-linkage-name-conflict",
                                    "a C object conflicts with a C function or distinct global object", symbol.coord)
            if canonical is not None:
                if not same_type(canonical.dtype, symbol.dtype):
                    raise CppDiagnostic("cpp.conflicting-c-linkage-type",
                                        "C object declarations across namespaces have different types", symbol.coord)
                if definition and canonical.defined:
                    raise CppDiagnostic("cpp.redefinition", "C object has more than one definition", symbol.coord)
                if definition:
                    canonical.defined = True
                if self not in canonical.declaration_scopes:
                    canonical.declaration_scopes.append(self)
                self.names[symbol.name] = canonical
                return canonical
            root.c_linkage_objects[symbol.name] = symbol
            symbol.c_name = symbol.name
        else:
            symbol.c_name = mangle_object(symbol.name, self.namespace_path(), local_name)
        symbol.declaration_scopes.append(self)
        return self.declare(symbol)

    @staticmethod
    def _check_function_contract(previous, symbol):
        if previous.nonthrowing != symbol.nonthrowing:
            raise CppDiagnostic("cpp.conflicting-exception-specification", "function declarations have different exception specifications", symbol.coord)
        if symbol.noreturn and not previous.noreturn:
            raise CppDiagnostic("cpp.invalid-noreturn", "the first declaration of a noreturn function must carry the attribute", symbol.coord)

    def declare_function(self, symbol, definition):
        previous = self.names.get(symbol.name)
        if previous is not None:
            if previous.kind != "function":
                raise CppDiagnostic(
                    "cpp.redeclaration", f"'{symbol.name}' is not a function", symbol.coord
                )
            for candidate in previous.overloads:
                if bool(candidate.template_parameters) != bool(symbol.template_parameters):
                    continue
                if symbol.template_parameters:
                    if signature_key(candidate) != signature_key(symbol):
                        continue
                elif (candidate.parameters != symbol.parameters
                      or candidate.variadic != symbol.variadic):
                    continue
                if (candidate.owner is not self and self not in candidate.declaration_scopes
                        and not (symbol.requested_language_linkage == "C"
                                 and candidate.language_linkage == "C")):
                    raise CppDiagnostic("cpp.using-declaration-conflict",
                                        "declaration conflicts with an imported function", symbol.coord)
                if (symbol.requested_language_linkage is not None
                        and symbol.requested_language_linkage != candidate.language_linkage):
                    raise CppDiagnostic("cpp.conflicting-language-linkage",
                                        "function redeclared with different language linkage", symbol.coord)
                self._check_function_contract(candidate, symbol)
                if not symbol.template_parameters and candidate.dtype != symbol.dtype:
                    raise CppDiagnostic("cpp.return-type-overload",
                                        "functions cannot differ only in return type", symbol.coord)
                if definition and candidate.defined:
                    raise CppDiagnostic("cpp.redefinition",
                                        f"redefinition of '{symbol.name}'", symbol.coord)
                if definition:
                    candidate.defined = True
                if self not in candidate.declaration_scopes:
                    candidate.declaration_scopes.append(self)
                return candidate
        symbol.language_linkage = symbol.requested_language_linkage or "C++"
        if symbol.language_linkage == "C":
            global_object = self.global_scope().names.get(symbol.name)
            if (symbol.name in self.global_scope().c_linkage_objects
                    or getattr(global_object, "namespace_object", False)
                    and global_object.owner is self.global_scope()):
                raise CppDiagnostic("cpp.c-linkage-name-conflict",
                                    "a C function cannot share a C or global object's name", symbol.coord)
            registry = self.global_scope().c_linkage_functions
            canonical = registry.get(symbol.name)
            if canonical is not None:
                self._check_function_contract(canonical, symbol)
                if (canonical.parameters != symbol.parameters or canonical.dtype != symbol.dtype
                        or canonical.variadic != symbol.variadic):
                    raise CppDiagnostic("cpp.conflicting-c-linkage-type",
                                        "a C-linkage function cannot be overloaded across namespaces", symbol.coord)
                if definition and canonical.defined:
                    raise CppDiagnostic("cpp.redefinition",
                                        "C-linkage function already has a definition", symbol.coord)
                symbol = canonical
            else:
                registry[symbol.name] = symbol
            symbol.c_name = symbol.name
        elif not symbol.template_parameters:
            symbol.c_name = mangle_function(symbol.name, symbol.parameters, self.namespace_path(), symbol.variadic)
        if previous is None:
            previous = Symbol(symbol.name, "function", symbol.coord)
            self.names[symbol.name] = previous
        if symbol not in previous.overloads:
            previous.overloads.append(symbol)
        if symbol.owner is None:
            symbol.owner = self
            symbol.internal_linkage = self.namespace().internal_linkage
        if self not in symbol.declaration_scopes:
            symbol.declaration_scopes.append(self)
        if definition:
            symbol.defined = True
        return symbol


def argument_dependent_lookup(name, arguments, ordinary, coord):
    """ADL for base-free classes and their type template arguments.

    N4659 [basic.lookup.argdep]/2: aliases add no namespace; non-type
    arguments add no associated entities. Follow type structure without
    requesting layout, including function types appearing as type arguments.
    Bases, friends, member templates and template-template arguments remain
    rejected by their declaration/type owners.
    """
    if ordinary is not None and ordinary.kind != "function":
        return ordinary
    from pcc.frontends.cpp.templates import IntegralArgument
    from pcc.frontends.cpp.types import FunctionType
    namespaces = []
    pending = list(arguments)
    seen_types = set()
    while pending:
        dtype = unqualified(pending.pop())
        if dtype in seen_types:
            continue
        seen_types.add(dtype)
        if isinstance(dtype, ReferenceType):
            pending.append(dtype.referent)
            continue
        if pointer_type(dtype) or array_type(dtype):
            pending.append(dtype.element if array_type(dtype) else dtype.pointee)
            continue
        if isinstance(dtype, FunctionType):
            pending.extend(dtype.parameters)
            pending.append(dtype.result)
            continue
        if getattr(dtype, "cpp_class_type", False) or is_enum_type(dtype):
            namespace = dtype.declaration_scope
            if namespace is None:
                raise CppDiagnostic("cpp.unsupported.associated-lookup",
                                    "nominal type has no recorded declaring namespace for ADL", coord)
            namespaces.append(namespace.namespace())
            pending.extend(argument for argument in getattr(dtype, "template_arguments", ())
                           if not isinstance(argument, IntegralArgument))
            enclosing = getattr(dtype, "enclosing_class", None)
            if enclosing is not None:
                pending.append(enclosing)
    candidates = [] if ordinary is None else [ordinary]
    visited = set()
    while namespaces:
        namespace = namespaces.pop()
        if namespace in visited:
            continue
        visited.add(namespace)
        symbol = namespace.names.get(name)
        if symbol is not None and symbol.kind == "function":
            candidates.append(symbol)
        # Unlike ordinary qualified lookup, ADL ignores using-directives.
        namespaces.extend(namespace.inline_namespaces)
        if namespace.inline and namespace.parent is not None:
            namespaces.append(namespace.parent)
    return Scope._merge(candidates, name, coord)


def resolve_overload(group, arguments, types, coord, null_constants=None, lvalues=None,
                     explicit_arguments=None):
    """Select a unique best viable candidate, using a per-argument partial order.

    Summing conversion costs is wrong: better on one argument and worse on
    another makes candidates incomparable, not a numerical tie-break. The
    richer reference/template/user-defined rules must be added before those
    forms are admitted by the parser.
    """
    source_arguments = list(arguments)
    arguments = [unqualified(argument) for argument in arguments]
    if lvalues is None and any(isinstance(t, ReferenceType)
                               for candidate in group.overloads for t in candidate.parameters):
        raise CppDiagnostic("cpp.unsupported.references",
                            "reference overload resolution requires source value categories", coord)
    lvalues = [False] * len(arguments) if lvalues is None else lvalues
    if null_constants is None:
        null_constants = [False] * len(arguments)
    if (any(pointer_type(t) or t == "nullptr" for t in arguments)
            and any(candidate.template_parameters and len(candidate.parameters) == len(arguments)
                    for candidate in group.overloads)):
        raise CppDiagnostic("cpp.unsupported.pointer-templates",
                            "pointer-involving template overload sets are not implemented", coord)
    viable = []
    binding_errors = []
    for candidate in group.overloads:
        if explicit_arguments is not None and not candidate.template_parameters:
            continue
        count = len(candidate.parameters)
        if len(arguments) < count:
            defaults = getattr(candidate, "default_arguments", ())
            if len(defaults) != count or any(value is None for value in defaults[len(arguments):]):
                continue
        elif len(arguments) > count and not candidate.variadic:
            continue
        if candidate.template_parameters:
            try:
                bindings = deduce(candidate, arguments, explicit_arguments, types)
            except CppDiagnostic as error:
                binding_errors.append(error)
                continue
            if bindings is None:
                continue
            origin = candidate
            candidate = origin.instances.get(bindings)
            if candidate is None:
                candidate = Symbol(origin.name, "function", origin.coord,
                                   c_name=mangle_instance(origin, bindings),
                                   arity=len(arguments), dtype=substitute(origin.dtype, bindings),
                                   parameters=tuple(substitute(p, bindings) for p in origin.parameters))
                candidate.template_origin = origin
                candidate.template_arguments = bindings
                candidate.explicit_template_arguments = explicit_arguments
                # A specialization belongs to its declaration's namespace,
                # not the scope importing or calling the template. In
                # particular, unnamed-namespace instances remain TU-local.
                candidate.owner = origin.owner
                candidate.internal_linkage = origin.internal_linkage
        ranks = []
        for index, (source, destination, null) in enumerate(zip(arguments, candidate.parameters, null_constants)):
            if isinstance(destination, ReferenceType):
                from pcc.frontends.cpp.references import conversion_rank
                rank = conversion_rank(source_arguments[index], destination, lvalues[index], types, null)
            else:
                rank = types.conversion_rank(source, destination, null)
            if rank is None:
                break
            ranks.append(rank)
        if len(ranks) == len(candidate.parameters) and candidate.variadic:
            ranks.extend([3] * (len(arguments) - len(candidate.parameters)))
        if len(ranks) == len(arguments):
            viable.append((candidate, ranks))
    if not viable:
        if binding_errors and all(error.code == binding_errors[0].code for error in binding_errors):
            raise binding_errors[0]
        raise CppDiagnostic("cpp.no-viable-overload",
                            f"no viable overload for '{group.name}'", coord)
    best = []
    for candidate, ranks in viable:
        beats_all = True
        for other, other_ranks in viable:
            if other is candidate:
                continue
            comparisons = []
            for index, (source, null) in enumerate(zip(arguments, null_constants)):
                first_ellipsis = index >= len(candidate.parameters)
                second_ellipsis = index >= len(other.parameters)
                if first_ellipsis or second_ellipsis:
                    comparisons.append(int(first_ellipsis) - int(second_ellipsis))
                else:
                    first, second = candidate.parameters[index], other.parameters[index]
                    if isinstance(first, ReferenceType) or isinstance(second, ReferenceType):
                        from pcc.frontends.cpp.references import compare
                        comparisons.append(compare(source_arguments[index], first, second, types, null))
                    else:
                        comparisons.append(types.compare_conversions(source, first, second, null))
            no_worse = all(value <= 0 for value in comparisons)
            better = any(value < 0 for value in comparisons)
            non_template_preferred = (candidate.template_origin is None
                                      and other.template_origin is not None)
            if not (no_worse and (better or non_template_preferred)):
                beats_all = False
                break
        if beats_all:
            best.append(candidate)
    if len(best) != 1:
        if sum(candidate.template_origin is not None for candidate, ranks in viable) > 1:
            raise CppDiagnostic("cpp.unsupported.template-ordering",
                                "function-template partial ordering is not implemented", coord)
        raise CppDiagnostic("cpp.ambiguous-overload",
                            f"ambiguous overload for '{group.name}'", coord)
    return best[0]
