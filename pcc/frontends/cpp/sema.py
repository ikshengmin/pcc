"""C++ ordinary-name lookup, independent of lexer token classification.

Only declarations whose semantics are implemented are installed. In particular,
a local value hides an outer type name; searching onwards for a typedef would
misparse C++ declaration/expression ambiguities.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic


class Symbol:
    def __init__(self, name, kind, coord, c_name=None, arity=None):
        self.name = name
        self.kind = kind
        self.coord = coord
        self.c_name = c_name if c_name is not None else name
        self.arity = arity
        self.defined = False


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
            if previous.kind == symbol.kind == "type":
                # All accepted aliases currently denote precisely int.
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
            if previous.arity != symbol.arity:
                raise CppDiagnostic(
                    "cpp.unsupported.overload",
                    "overload resolution is not implemented",
                    symbol.coord,
                )
            if definition and previous.defined:
                raise CppDiagnostic(
                    "cpp.redefinition", f"redefinition of '{symbol.name}'", symbol.coord
                )
            symbol = previous
        else:
            self.names[symbol.name] = symbol
        if definition:
            symbol.defined = True
        return symbol
