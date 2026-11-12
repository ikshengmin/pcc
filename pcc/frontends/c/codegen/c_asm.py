"""GNU asm with an empty template, rewritten to the C it means before AST passes.

An empty template executes no instruction; only its operands matter (GCC
manual, "Extended Asm"). An "=" output tied to an input (a digit or
"[name]" constraint) receives that input; a "+" or untied "=" output keeps
its value (an untied output is unspecified, so its old value is a valid
result); every operand expression is evaluated once. Rewriting before the
high-tier passes lets each pass see the assignment. Real templates, asm goto
and file-scope asm are left for codegen_AsmStatement to reject explicitly.
"""
from pcc.frontends.c.ast import c_ast


def _text(literal):
    value = literal.value
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.replace("\\n", "\n").replace("\\t", "\t")


def is_empty_template(node):
    return (isinstance(node, c_ast.AsmStatement) and not node.file_scope and not node.labels
            and "goto" not in node.qualifiers and not _text(node.template).strip())


def _void(expression, coord):
    void = c_ast.TypeDecl(None, [], c_ast.IdentifierType(["void"], coord), coord)
    return c_ast.Cast(c_ast.Typename(None, [], void, coord), expression, coord)


def _evaluated_once(expression, coord):
    """A statement evaluating an lvalue operand's side effects without reading it."""
    if isinstance(expression, c_ast.ID):
        return None
    return _void(c_ast.UnaryOp("&", expression, coord), coord)


def rewrite(node):
    coord = node.coord
    names = {name: index for index, (_c, _e, name) in enumerate(node.outputs) if name}
    tied, statements = {}, []
    for constraint, expression, _name in node.inputs:
        body = _text(constraint).lstrip("=+&%")
        target = int(body) if body.isdigit() else names.get(body[1:-1]) if body.startswith("[") else None
        if target is not None and target < len(node.outputs):
            tied[target] = expression
        else:
            statements.append(_void(expression, coord))
    for index, (_constraint, expression, _name) in enumerate(node.outputs):
        if index in tied:
            statements.append(c_ast.Assignment("=", expression, tied[index], coord))
        else:
            evaluated = _evaluated_once(expression, coord)
            if evaluated is not None:
                statements.append(evaluated)
    return c_ast.Compound(statements, coord)


def rewrite_empty_templates(ast):
    """Replace every empty-template asm statement in place; return the AST."""
    pending = [ast]
    while pending:
        node = pending.pop()
        for slot in getattr(type(node), "__slots__", ()):
            if slot in ("coord", "__weakref__"):
                continue
            value = getattr(node, slot, None)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if is_empty_template(item):
                        value[index] = rewrite(item)
                    elif isinstance(item, c_ast.Node):
                        pending.append(item)
            elif is_empty_template(value):
                setattr(node, slot, rewrite(value))
            elif isinstance(value, c_ast.Node):
                pending.append(value)
    return ast
