"""Fail closed at ordinary expression consumers; never lower pending plans."""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic


class PendingClassResult:
    """Source C++ prvalue, not yet projected to a C operation or object."""


def pending_node(node):
    return isinstance(node, PendingClassResult)


def reject_pending(context, coord, *expressions):
    for expression in expressions:
        if expression is not None and pending_node(expression.node):
            raise CppDiagnostic('cpp.unsupported.class-result-context',
                                'unmaterialized class result has no owner for ' + context,
                                coord if coord is not None else getattr(expression.node, 'coord', None))


def seal_projection(tree, has_plans):
    """Last defense at the C++ -> C AST boundary, after all template draining.

    No rewrite or disappearance: a surviving typed construction action is an
    explicit capability failure, including one nested in otherwise-valid AST.
    """
    if not has_plans:
        return tree
    pending = [tree]
    while pending:
        node = pending.pop()
        if pending_node(node):
            raise CppDiagnostic('cpp.unsupported.class-result-context',
                                'unconsumed class result reached C projection', getattr(node, 'coord', None))
        if not isinstance(node, c_ast.Node):
            raise CppDiagnostic('cpp.unsupported.cpp-projection',
                                'non-C node reached the finalized C++ projection', getattr(node, 'coord', None))
        pending.extend(child for _, child in node.children())
    return tree
