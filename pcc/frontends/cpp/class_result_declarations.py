"""Class-result source signatures and completed-class physical declarations.

Pending declarations are deliberately NOT C AST nodes. Every projection consumes
one canonical function's separately classified ABI, never a changed source dtype.
"""
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import unqualified


def admitted_signature(parser, dtype, member_definition, template_parameters, coord):
    if template_parameters or parser.parsing_template:
        return False
    plain = unqualified(dtype)
    if not is_class_type(plain):
        return False
    # The only incomplete result we defer is the actual enclosing class. Other
    # forward declarations keep the old unsupported result diagnostic.
    if not plain.complete:
        return member_definition is not None and plain is member_definition.dtype
    from pcc.frontends.cpp.class_result_abi import classify_result
    return classify_result(parser, dtype, coord) == 'indirect'


def plan_for(parser, symbol, coord):
    from pcc.frontends.cpp.class_result_abi import IndirectResult
    plan = getattr(symbol, 'class_result_abi', None)
    if plan is None:
        plan = IndirectResult(parser, symbol, coord)
        symbol.class_result_abi = plan
    if plan.result_type is not unqualified(symbol.dtype) or plan.linkage_name != symbol.c_name:
        raise CppDiagnostic('cpp.class-result-identity', 'class result ABI/source identity changed', coord)
    return plan


class DeferredClassResultDeclaration:
    def __init__(self, symbol, parameters, coord):
        self.symbol, self.parameters, self.coord = symbol, parameters, coord

    def materialize(self, parser):
        symbol = self.symbol
        plan = plan_for(parser, symbol, self.coord)
        hidden = getattr(symbol, 'class_result_hidden_name', None)
        if hidden is None:
            hidden = parser._fresh_projection_name('__pcc_cpp_result_dest_')
            symbol.class_result_hidden_name = hidden
        declaration = plan.declaration(parser, self.parameters, hidden, self.coord)
        if symbol.noreturn:
            declaration.funcspec = ['_Noreturn']
        if symbol.internal_linkage:
            declaration.storage = ['static']
        return declaration


def materialize_completed_class(parser, definition):
    replacements = {}
    for index, item in enumerate(definition.method_declarations):
        if isinstance(item, DeferredClassResultDeclaration):
            concrete = item.materialize(parser)
            replacements[id(item)] = concrete
            definition.method_declarations[index] = concrete
    for pending in definition.inline_methods:
        if isinstance(pending.declaration, DeferredClassResultDeclaration):
            pending.declaration = replacements[id(pending.declaration)]
