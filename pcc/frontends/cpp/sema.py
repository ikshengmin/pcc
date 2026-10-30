"""C++ ordinary-name lookup, independent of lexer token classification.

Only declarations whose semantics are implemented are installed. In particular,
a local value hides an outer type name; searching onwards for a typedef would
misparse C++ declaration/expression ambiguities.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import mangle_function


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
        return symbol is not None and symbol.kind == "type"

    def declare(self, symbol):
        previous = self.names.get(symbol.name)
        if previous is not None:
            if previous.kind == symbol.kind == "type" and previous.dtype == symbol.dtype:
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
                if candidate.parameters != symbol.parameters:
                    continue
                if candidate.dtype != symbol.dtype:
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
        symbol.c_name = mangle_function(symbol.name, symbol.parameters)
        if definition:
            symbol.defined = True
        return symbol


def resolve_overload(group, arguments, types, coord):
    """Select a unique best viable candidate, using a per-argument partial order.

    Summing conversion costs is wrong: better on one argument and worse on
    another makes candidates incomparable, not a numerical tie-break. The
    richer reference/template/user-defined rules must be added before those
    forms are admitted by the parser.
    """
    viable = []
    for candidate in group.overloads:
        if len(candidate.parameters) != len(arguments):
            continue
        ranks = []
        for source, destination in zip(arguments, candidate.parameters):
            rank = types.conversion_rank(source, destination)
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
            no_worse = all(a <= b for a, b in zip(ranks, other_ranks))
            better = any(a < b for a, b in zip(ranks, other_ranks))
            if not (no_worse and better):
                beats_all = False
                break
        if beats_all:
            best.append(candidate)
    if len(best) != 1:
        raise CppDiagnostic("cpp.ambiguous-overload",
                            f"ambiguous overload for '{group.name}'", coord)
    return best[0]
