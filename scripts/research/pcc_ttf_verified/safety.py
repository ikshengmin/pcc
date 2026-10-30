"""Execution boundary for the fixed, non-recursive generated Python subset."""

import ast
from dataclasses import dataclass

from .templates import MAX_SOURCE_BYTES


ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.UnaryOp, ast.USub, ast.Not,
    ast.Compare, ast.Lt, ast.Eq, ast.BoolOp, ast.And, ast.IfExp,
    ast.Subscript, ast.Call,
)
ALLOWED_NAMES = {"probe", "result", "a", "b", "xs", "text", "flag", "pair",
                 "other", "label", "len", "int"}
MAX_INTEGER_MAGNITUDE = 4096
MAX_SEQUENCE_LENGTH = 1024
MAX_EXPRESSION_DEPTH = 8


@dataclass(frozen=True)
class Budget:
    # Conservative bounds include nested elements, so indexing cannot increase
    # either bound. Invalid operations may be overestimated, never executed.
    integer: int = 0
    sequence: int = 0


def expression_budget(node, environment, depth=0):
    if depth > MAX_EXPRESSION_DEPTH:
        raise ValueError("expression depth bound exceeded")
    visit = lambda child: expression_budget(child, environment, depth + 1)
    if isinstance(node, ast.Constant):
        result = Budget(0, len(node.value)) if isinstance(node.value, str) else Budget(abs(node.value))
    elif isinstance(node, ast.Name):
        # Names absent from the fixed local environment raise NameError before
        # producing any object. Giving them zero bounds cannot hide growth in
        # other subexpressions, all of which are inspected independently.
        result = environment.get(node.id, Budget())
    elif isinstance(node, (ast.List, ast.Tuple)):
        items = [visit(item) for item in node.elts]
        result = Budget(max((x.integer for x in items), default=0),
                        max(len(items), max((x.sequence for x in items), default=0)))
    elif isinstance(node, ast.BinOp):
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, ast.Mult):
            result = Budget(left.integer * right.integer,
                            max(left.sequence * right.integer, left.integer * right.sequence))
        else:
            result = Budget(left.integer + right.integer, left.sequence + right.sequence)
    elif isinstance(node, ast.UnaryOp):
        operand = visit(node.operand)
        result = Budget(1) if isinstance(node.op, ast.Not) else operand
    elif isinstance(node, ast.Compare):
        visit(node.left)
        for operand in node.comparators:
            visit(operand)
        result = Budget(1)
    elif isinstance(node, ast.BoolOp):
        values = [visit(value) for value in node.values]
        result = Budget(max(x.integer for x in values), max(x.sequence for x in values))
    elif isinstance(node, ast.IfExp):
        visit(node.test)
        body, other = visit(node.body), visit(node.orelse)
        result = Budget(max(body.integer, other.integer), max(body.sequence, other.sequence))
    elif isinstance(node, ast.Subscript):
        result = visit(node.value)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        if len(node.args) != 1:
            raise ValueError("len must have one argument")
        result = Budget(visit(node.args[0]).sequence)
    else:
        raise ValueError("unsupported expression in resource bound")
    if result.integer > MAX_INTEGER_MAGNITUDE:
        raise ValueError("integer growth bound exceeded")
    if result.sequence > MAX_SEQUENCE_LENGTH:
        raise ValueError("sequence growth bound exceeded")
    return result


def validate(source: str) -> ast.Module:
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("source byte limit exceeded")
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    if len(nodes) > 512:
        raise ValueError("node limit exceeded")
    functions = [node for node in nodes if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "probe":
        raise ValueError("only the single probe function is allowed")
    arguments = functions[0].args
    if ([arg.arg for arg in arguments.args] != ["a", "b"] or
            arguments.posonlyargs or arguments.kwonlyargs or arguments.defaults or
            arguments.kw_defaults or arguments.vararg or arguments.kwarg):
        raise ValueError("only fixed probe(a, b) arguments are allowed")
    if functions[0].returns is not None or any(
            not isinstance(arg.annotation, ast.Name) or arg.annotation.id != "int"
            for arg in arguments.args):
        raise ValueError("only the fixed int parameter annotations are allowed")
    for node in nodes:
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError(f"forbidden node: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in ALLOWED_NAMES:
            raise ValueError("unknown name")
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.type_params):
            raise ValueError("decorators and type parameters are forbidden")
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, bool, str):
                raise ValueError("forbidden literal kind")
            if type(node.value) is int and abs(node.value) > 16:
                raise ValueError("integer literal bound exceeded")
            if isinstance(node.value, str) and len(node.value) > 8:
                raise ValueError("string literal bound exceeded")
        if isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Constant) or node.slice.value != 0:
                raise ValueError("only constant index zero is allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise ValueError("only direct calls without keywords are allowed")
            if node.func.id not in ("probe", "len"):
                raise ValueError("forbidden call")
    for node in ast.walk(functions[0]):
        if isinstance(node, ast.Call) and node.func.id == "probe":
            raise ValueError("recursion is forbidden")
    if len(tree.body) != 2 or not isinstance(tree.body[1], ast.Assign):
        raise ValueError("only a function and one result assignment are allowed")
    targets = tree.body[1].targets
    if len(targets) != 1 or not isinstance(targets[0], ast.Name) or targets[0].id != "result":
        raise ValueError("top-level target must be result")
    call = tree.body[1].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "probe":
        raise ValueError("result must be the fixed probe call")
    if [ast.literal_eval(arg) for arg in call.args] != [2, 5]:
        raise ValueError("probe input must be the fixed bounded pair")
    environment = {"a": Budget(2), "b": Budget(5)}
    for statement in functions[0].body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                raise ValueError("only simple local assignments are allowed")
            target = statement.targets[0].id
            if target in ("probe", "int", "len"):
                raise ValueError("call targets cannot be rebound")
            environment[target] = expression_budget(statement.value, environment)
        elif isinstance(statement, ast.Return):
            expression_budget(statement.value, environment)
        else:
            raise ValueError("only assignments and return are allowed in probe")
    return tree


def run_python(source: str):
    tree = validate(source)
    namespace = {"__builtins__": {"len": len, "int": int}}
    exec(compile(tree, "<bounded-ttf>", "exec"), namespace)
    return namespace["result"]
