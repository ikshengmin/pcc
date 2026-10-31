"""Typed C++ switch admission and dispatch, using the existing C AST owner.

N4659 [stmt.switch], [stmt.dcl]/3, [expr.const]. Integral promotions and enum
identity belong to ArithmeticTypes; case constants use the C++ proof owner,
never the C evaluator or token rewriting. Classes, dependent templates and
nonlocal case-label jumps retain explicit dependency diagnostics.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT, converted_fact
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.initializers import check_list_conversion
from pcc.frontends.cpp.sema import Scope
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.types import is_enum_type, unqualified


class SwitchContext:
    def __init__(self, dtype, body_scope):
        self.dtype = dtype
        self.body_scope = body_scope
        self.values = set()
        self.has_default = False
        self.labels = 0


def parse_switch(parser):
    token = parser._expect("switch")
    if parser.template_function is not None:
        raise CppDiagnostic("cpp.unsupported.switch-templates",
                            "dependent switch/case instantiation is not implemented", token.coord)
    parser._expect("(")
    if parser._is_declaration():
        raise CppDiagnostic("cpp.unsupported.switch-condition-declaration",
                            "switch condition/init declarations require separate lifetime scopes", parser._peek().coord)
    condition = parser._expression()
    if parser._peek().value == ";":
        raise CppDiagnostic("cpp.unsupported.switch-init-statement",
                            "switch init-statements require separate lifetime scopes", parser._peek().coord)
    parser._expect(")")
    parser._require_value(condition, allow_class=True)
    dtype = unqualified(condition.dtype)
    if is_class_type(dtype):
        raise CppDiagnostic("cpp.unsupported.switch-class-conversion",
                            "switch class conditions require conversion-function lookup", token.coord)
    if is_enum_type(dtype) and dtype.scoped:
        if not dtype.complete:
            raise CppDiagnostic("cpp.incomplete-enum", "switch requires a complete enum type", token.coord)
    elif parser.types.integral_operand(dtype):
        dtype = parser.types.promote(dtype)
    else:
        raise CppDiagnostic("cpp.switch-condition-type", "switch requires an integral or enumeration condition", token.coord)
    condition_node = parser._convert(condition, dtype, token.coord)
    parent = parser.scope
    body_scope = Scope(parent)
    context = SwitchContext(dtype, body_scope)
    parser.scope = body_scope
    parser.switch_contexts.append(context)
    try:
        body = (parser._block(new_scope=False) if parser._peek().value == "{"
                else parser._statement())
    finally:
        parser.switch_contexts.pop()
        parser.scope = parent
    if not context.labels:
        # Every original statement was parsed/type-checked. Without any label,
        # dispatch cannot enter the body; only the condition is evaluated.
        body = c_ast.Compound([], token.coord)
    return c_ast.Switch(condition_node, body, token.coord)


def parse_case(parser):
    token = parser.cursor.take()
    if not parser.switch_contexts:
        raise CppDiagnostic("cpp.case-outside-switch", "case/default requires an enclosing switch", token.coord)
    context = parser.switch_contexts[-1]
    if parser.scope is not context.body_scope:
        raise CppDiagnostic("cpp.unsupported.nonlocal-case-label",
                            "case labels nested inside other substatements require nonlocal jump analysis", token.coord)
    # Dispatch enters the label from outside the body's automatic bindings.
    # Do not invent a new scope per label to evade [stmt.dcl]/3.
    for symbol in parser.scope.names.values():
        if symbol.kind != "value" or symbol.owner is not parser.scope:
            continue
        entry = getattr(symbol, "jump_entry", None)
        if entry == "initialized":
            raise CppDiagnostic("cpp.switch-jump-initialization",
                                "case dispatch bypasses initialization of '" + symbol.name + "'", token.coord)
        if entry != "uninitialized":
            raise CppDiagnostic("cpp.unsupported.switch-class-lifetime",
                                "case entry requires proven trivial default construction and destruction", token.coord)
    if token.value == "default":
        if context.has_default:
            raise CppDiagnostic("cpp.duplicate-default", "switch has more than one default label", token.coord)
        context.has_default = True
        expression = None
    else:
        expression = parser._expression()
        source = unqualified(expression.dtype)
        if not (parser.types.integral_operand(source) or is_enum_type(source)):
            raise CppDiagnostic("cpp.case-type", "case requires an integral or enumeration constant", token.coord)
        fact = parser._constant_fact(expression)
        if fact.status == NOT_CONSTANT:
            raise CppDiagnostic("cpp.case-constant-expression", "case is not a constant expression: " + fact.reason, token.coord)
        if fact.status != KNOWN:
            raise CppDiagnostic("cpp.unsupported.case-constant-evaluation", "case constant semantics are not modeled", token.coord)
        check_list_conversion(expression, context.dtype, parser.types, token.coord)
        fact = converted_fact(fact, context.dtype, parser.types, token.coord)
        if fact.status != KNOWN:
            raise CppDiagnostic("cpp.unsupported.case-constant-evaluation", "case converted constant is not modeled", token.coord)
        if fact.value in context.values:
            raise CppDiagnostic("cpp.duplicate-case", "duplicate converted case value", token.coord)
        context.values.add(fact.value)
        # Only after typed C++ proof and representability checks do we project
        # the known value; C's looser case evaluator does not decide validity.
        expression = parser._enum_constant_expression(context.dtype, fact.value, token.coord).node
    parser._expect(":")
    context.labels += 1
    if parser._peek().value == "}":
        raise CppDiagnostic("cpp.case-statement-required", "C++17 labels require a following statement", token.coord)
    statement = parser._statement()
    statement = c_ast.EmptyStatement(token.coord) if statement is None else statement
    return (c_ast.Default([statement], token.coord) if expression is None
            else c_ast.Case(expression, [statement], token.coord))
