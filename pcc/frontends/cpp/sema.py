"""C++ ordinary-name lookup, independent of lexer token classification.

Only declarations whose semantics are implemented are installed. In particular,
a local value hides an outer type name; searching onwards for a typedef would
misparse C++ declaration/expression ambiguities.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import mangle_function, pointer_type, unqualified
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
        self.overloads = []
        self.template_parameters = ()
        self.template_origin = None
        self.template_arguments = ()
        self.pattern = None
        self.parameter_symbols = []
        self.instances = {}
        self.owner = None
        self.namespace_scope = None
        self.internal_linkage = False
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
        self.using_directives = []
        self.inline_namespaces = []
        self.inline = inline
        self.anonymous = anonymous
        self.unnamed_namespace = None
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

    def lookup(self, name, coord=None, kinds=None):
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
            scope = scope.parent
        return None

    def resolve_name(self, name, namespace_only=False):
        """Resolve a parsed path without replacing it by a flattened spelling."""
        qualifier_kinds = ("namespace", "namespace-alias", "type", "template-type")
        final_kinds = ("namespace", "namespace-alias") if namespace_only else None
        scope = self.global_scope() if name.global_scope else self
        symbol = None
        for index, part in enumerate(name.parts):
            kinds = final_kinds if index == len(name.parts) - 1 else qualifier_kinds
            symbol = (scope.lookup(part, name.coord, kinds) if index == 0 and not name.global_scope
                      else scope.lookup_qualified(part, name.coord, kinds))
            if index != len(name.parts) - 1:
                if symbol is not None and symbol.kind == "template-type":
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "dependent qualified lookup requires template/class semantics", name.coord)
                if symbol is not None and getattr(symbol.dtype, "cpp_class_type", False):
                    raise CppDiagnostic("cpp.unsupported.class-qualified-lookup",
                                        "class-qualified lookup requires member/access semantics", name.coord)
                if symbol is None or symbol.namespace_scope is None:
                    raise CppDiagnostic("cpp.not-a-namespace", "'" + part + "' does not name a namespace", name.coord)
                scope = symbol.namespace_scope
        return symbol

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
        if symbol.kind in ("namespace", "namespace-alias"):
            raise CppDiagnostic("cpp.using-namespace-name", "using-declaration cannot name a namespace", coord)
        hidden = self.lookup_lexical(symbol.name)
        if hidden is not None and hidden.kind == "template-type":
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                "using-declaration shadows a template parameter", coord)
        previous = self.names.get(symbol.name)
        if previous is not None and previous.kind == symbol.kind == "function":
            for own in previous.overloads:
                for imported in symbol.overloads:
                    if (own is not imported and own.owner is self
                            and not own.template_parameters and not imported.template_parameters
                            and own.parameters == imported.parameters):
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
        hidden = self.lookup_lexical(symbol.name)
        if hidden is not None and hidden.kind == "template-type":
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
                elif candidate.parameters != symbol.parameters:
                    continue
                if candidate.owner is not self:
                    raise CppDiagnostic("cpp.using-declaration-conflict",
                                        "declaration conflicts with an imported function", symbol.coord)
                if not symbol.template_parameters and candidate.dtype != symbol.dtype:
                    raise CppDiagnostic("cpp.return-type-overload",
                                        "functions cannot differ only in return type", symbol.coord)
                if definition and candidate.defined:
                    raise CppDiagnostic("cpp.redefinition",
                                        f"redefinition of '{symbol.name}'", symbol.coord)
                if definition:
                    candidate.defined = True
                return candidate
            previous.overloads.append(symbol)
        else:
            group = Symbol(symbol.name, "function", symbol.coord)
            group.overloads.append(symbol)
            self.names[symbol.name] = group
        symbol.owner = self
        symbol.internal_linkage = self.namespace().internal_linkage
        if not symbol.template_parameters:
            symbol.c_name = mangle_function(symbol.name, symbol.parameters, self.namespace_path())
        if definition:
            symbol.defined = True
        return symbol


def argument_dependent_lookup(name, arguments, ordinary, coord):
    """ADL for the admitted non-template, non-nested, base-free class types.

    The class parser rejects bases, nested classes, friends and class templates
    rather than pretending their associated entities are empty. Fundamental
    types add nothing; aliases do not add the alias's declaring namespace.
    """
    if ordinary is not None and ordinary.kind != "function":
        return ordinary
    namespaces = []
    for dtype in arguments:
        dtype = unqualified(dtype)
        if pointer_type(dtype):
            dtype = unqualified(dtype.pointee)
        if getattr(dtype, "cpp_class_type", False):
            namespace = dtype.declaration_scope
            if namespace is None:
                raise CppDiagnostic("cpp.unsupported.associated-lookup",
                                    "class has no recorded declaring namespace for ADL", coord)
            namespaces.append(namespace)
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


def resolve_overload(group, arguments, types, coord, null_constants=None):
    """Select a unique best viable candidate, using a per-argument partial order.

    Summing conversion costs is wrong: better on one argument and worse on
    another makes candidates incomparable, not a numerical tie-break. The
    richer reference/template/user-defined rules must be added before those
    forms are admitted by the parser.
    """
    arguments = [unqualified(argument) for argument in arguments]
    if null_constants is None:
        null_constants = [False] * len(arguments)
    if (any(pointer_type(t) or t == "nullptr" for t in arguments)
            and any(candidate.template_parameters and len(candidate.parameters) == len(arguments)
                    for candidate in group.overloads)):
        raise CppDiagnostic("cpp.unsupported.pointer-templates",
                            "pointer-involving template overload sets are not implemented", coord)
    viable = []
    for candidate in group.overloads:
        if len(candidate.parameters) != len(arguments):
            continue
        if candidate.template_parameters:
            bindings = deduce(candidate, arguments)
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
        ranks = []
        for source, destination, null in zip(arguments, candidate.parameters, null_constants):
            rank = types.conversion_rank(source, destination, null)
            if rank is None:
                break
            ranks.append(rank)
        if len(ranks) == len(arguments):
            viable.append((candidate, ranks))
    if not viable:
        raise CppDiagnostic("cpp.no-viable-overload",
                            f"no viable overload for '{group.name}'", coord)
    best = []
    for candidate, ranks in viable:
        beats_all = True
        for other, other_ranks in viable:
            if other is candidate:
                continue
            comparisons = [types.compare_conversions(source, first, second, null)
                           for source, first, second, null in zip(
                               arguments, candidate.parameters, other.parameters, null_constants)]
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
