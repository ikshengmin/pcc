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


class Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.names = {}

    def lookup(self, name):
        scope = self
        while scope is not None:
            symbol = scope.names.get(name)
            if symbol is not None:
                return symbol
            scope = scope.parent
        return None

    def is_type_name(self, name):
        symbol = self.lookup(name)
        return symbol is not None and symbol.kind in ("type", "template-type")

    def declare(self, symbol):
        hidden = self.lookup(symbol.name)
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
        if not symbol.template_parameters:
            symbol.c_name = mangle_function(symbol.name, symbol.parameters)
        if definition:
            symbol.defined = True
        return symbol


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
