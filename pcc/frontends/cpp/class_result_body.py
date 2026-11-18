"""Fail-closed body completion proof for bounded constructed class results."""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def record_constructed_return(parser, final_return):
    """Call only AFTER finish_return has successfully constructed the destination.

    Pass the exact ABI Return node contained in its completed output tree, not
    the whole cleanup Compound. Works uniformly for direct/copy/forward owners.
    """
    proof = getattr(parser, 'class_result_constructed_returns', None)
    if proof is None or getattr(parser, 'class_result_projection', None) is None:
        raise CppDiagnostic('cpp.unsupported.class-result-construction',
                            'constructed return has no active result body', final_return.coord)
    if not isinstance(final_return, c_ast.Return):
        raise TypeError('constructed return proof requires the exact ABI Return node')
    proof.append(final_return)


def require_constructed_returns(body, constructed_returns, coord):
    """Recognize control flow, not physical return types or constructor names.

    Identities come only from the successful result-construction owner. Loops,
    switch exhaustiveness, goto, exceptions and no-return call reasoning have
    no completion proof here. Unreachable code may conservatively be rejected.
    """
    allowed = {id(node) for node in constructed_returns}
    def check(node, in_statement_expression=False):
        in_statement_expression = in_statement_expression or isinstance(node, c_ast.StmtExpr)
        if in_statement_expression and isinstance(node, c_ast.Return):
            raise CppDiagnostic('cpp.unsupported.class-result-control-flow',
                                'return from a statement expression has no completion proof', node.coord)
        if isinstance(node, (c_ast.Goto, c_ast.Label)):
            raise CppDiagnostic('cpp.unsupported.class-result-control-flow',
                                'class result goto/label completion is not proven', node.coord)
        if isinstance(node, c_ast.Return) and id(node) not in allowed:
            raise CppDiagnostic('cpp.unsupported.class-result-construction',
                                'a class result return lacks successful destination construction', node.coord)
        for _, child in node.children():
            check(child, in_statement_expression)
    def terminates(node):
        if isinstance(node, c_ast.Return):
            return id(node) in allowed
        if isinstance(node, c_ast.Compound):
            return any(terminates(item) for item in (node.block_items or ()))
        if isinstance(node, c_ast.If):
            return node.iffalse is not None and terminates(node.iftrue) and terminates(node.iffalse)
        return False
    check(body)
    if not terminates(body):
        raise CppDiagnostic('cpp.unsupported.class-result-fallthrough',
                            'class result body is not proven to construct a result on every returning path', coord)
