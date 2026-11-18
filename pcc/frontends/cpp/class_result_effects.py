"""Whole class-result function call effects while normal-exit D1s exist.

This is fail-closed admission, not exception unwinding. Maps carry exact AST call
identities from the constructor and automatic-destruction semantic owners.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic


class ResultBodyEffects:
    def __init__(self, parser, symbol):
        self.symbol = symbol
        self.constructions = {}
        self.destructions = {}
        self.previous = getattr(parser, 'class_result_body_effects', None)
        parser.class_result_body_effects = self
        self.previous_lifetime_observer = getattr(parser.lifetimes, 'class_result_body_effects', None)
        parser.lifetimes.class_result_body_effects = self

    def restore(self, parser):
        parser.class_result_body_effects = self.previous
        parser.lifetimes.class_result_body_effects = self.previous_lifetime_observer

    def check(self, body, coord):
        if not self.destructions:
            return
        nodes = []
        pending = [body]
        while pending:
            node = pending.pop()
            nodes.append(node)
            pending.extend(child for _, child in node.children())
        # The whole-function conservative rule is activated only by an actual
        # cleanup in this function's emitted tree, not by a destructor name.
        if not any(node in self.destructions for node in nodes):
            return
        from pcc.frontends.cpp.constructors import closed_construction_body
        for node in nodes:
            if not isinstance(node, c_ast.FuncCall):
                continue
            if node in self.destructions:
                if not getattr(self.destructions[node], 'closed_destruction_body', False):
                    self.fail(node.coord)
            elif node in self.constructions:
                # Existing owner also examines every expanded argument/default
                # and preserves its deferred C1 proof obligations.
                if not closed_construction_body(node, self.constructions):
                    self.fail(node.coord)
            else:
                self.fail(node.coord)
        # No return/condition/loop subtree is omitted, including initializers.
        # No ordinary call is certified by its spelling or a noexcept token.

    @staticmethod
    def fail(coord):
        raise CppDiagnostic('cpp.unsupported.class-result-effects',
                            'class result function with destruction requires closed, role-proven call effects throughout its body', coord)


def record_constructor(parser, call, selected):
    context = getattr(parser, 'class_result_body_effects', None)
    if context is not None and parser.current_function is context.symbol:
        context.constructions[call] = selected


def record_destructor(lifetimes, call, selected):
    context = getattr(lifetimes, 'class_result_body_effects', None)
    if context is not None:
        context.destructions[call] = selected
