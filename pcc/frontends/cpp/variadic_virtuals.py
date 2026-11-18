"""Bind ordinary SysV x86-64 variadic virtuals to real definitions and slots.

The declaration is provisional until this translation unit has the actual
body and a compatible field for every final overrider. This does not claim
interoperability with a foreign C++ vtable or an unresolved external method.
"""
from pcc.backend.self_backend_target_match import is_x86_64_linux_triple
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import ReferenceType, contains_function, unqualified


def unsupported(coord):
    raise CppDiagnostic('cpp.unsupported.virtual-functions',
                        'variadic virtual functions require variadic table entries', coord)


def _attribute(value):
    if isinstance(value, (list, tuple)):
        return tuple(_attribute(item) for item in value)
    if isinstance(value, c_ast.Node):
        return _shape(value)
    return value


def _shape(node):
    # Declaration, field and definition may use different formal parameter names.
    attrs = tuple((name, _attribute(getattr(node, name))) for name in node.attr_names
                  if name != 'declname' and not (isinstance(node, c_ast.Decl) and name == 'name'))
    return type(node).__name__, attrs, tuple(_shape(child) for _, child in node.children())


def _signature(symbol, function):
    if not isinstance(function, c_ast.FuncDecl) or function.args is None:
        unsupported(symbol.coord)
    params = function.args.params
    if (len(params) != len(symbol.parameters) + 2
            or not isinstance(params[0], c_ast.Decl)
            or not isinstance(params[0].type, c_ast.PtrDecl)
            or not isinstance(params[-1], c_ast.EllipsisParam)
            or any(isinstance(p, c_ast.EllipsisParam) for p in params[:-1])):
        unsupported(symbol.coord)
    return _shape(function)


class Obligations:
    def __init__(self):
        # Insertion order makes unbound diagnostics follow source order.
        self.declarations = {}
        self.entries = {}
        self.bodies = {}

    def slot(self, definition, index, entry, field):
        symbol = entry.overrider
        if (entry.deleting or symbol.method_pure or not isinstance(field.type, c_ast.PtrDecl)
                or symbol not in self.declarations):
            unsupported(symbol.coord)
        shape = _signature(symbol, field.type.type)
        if shape != _signature(symbol, symbol.virtual_prototype.type):
            unsupported(symbol.coord)
        self.entries[definition.dtype, index] = symbol, shape

    def bound(self, symbol, function):
        if symbol not in self.declarations:
            return
        if not isinstance(function, c_ast.FuncDef) or function.body is None:
            unsupported(symbol.coord)
        self.bodies[symbol] = _signature(symbol, function.decl.type)

    def finish(self):
        for symbol in self.declarations:
            key = unqualified(symbol.class_owner), symbol.virtual_index
            own = self.entries.get(key)
            if own is None or own[0] is not symbol or symbol not in self.bodies:
                unsupported(symbol.coord)
        for symbol, shape in self.entries.values():
            if self.bodies.get(symbol) != shape:
                unsupported(symbol.coord)


def declare(parser, definition, symbol, pure, coord):
    if (pure or not is_x86_64_linux_triple(parser.types.target_triple)
            or parser.parsing_template or parser.instantiating_template
            or getattr(definition.dtype, 'template_origin', None) is not None
            or symbol.template_parameters or symbol.template_origin is not None
            or getattr(symbol, 'method_static', False)
            or isinstance(symbol.dtype, ReferenceType) or contains_function(symbol.dtype)):
        unsupported(coord)
    state = parser.variadic_virtual_obligations
    if state is None:
        state = parser.variadic_virtual_obligations = Obligations()
    state.declarations.setdefault(symbol, None)


def check_slot(parser, selected, coord):
    state = parser.variadic_virtual_obligations
    key = unqualified(selected.class_owner), selected.virtual_index
    if state is None or key not in state.entries or state.entries[key][0] is not selected:
        unsupported(coord)
