"""Scalar replacement of function-local tuples that never escape.

``pair = (index, 1)`` followed only by ``pair[1]`` allocates a tuple whose
only observers are those reads. A function local qualifies when

- every binding of it is an assignment ``name = (e0, ..., en-1)`` of a tuple
  literal of one arity ``n``, without a starred element;
- every other occurrence is a read ``name[k]`` with an integer literal ``k``
  in ``-n <= k < n``, or an unpacking ``t0, ..., tn-1 = name`` of that arity;
- every such read is preceded, in its own block or an enclosing one, by one
  of those assignments at that block's level (so the local is always bound
  where it is read: no UnboundLocalError changes);
- it is not global, nonlocal, deleted, imported, an exception or ``with``
  binding, a loop target, a nested definition's name, nor mentioned inside a
  nested function, lambda, class or comprehension;
- the function does not yield or await, and has no ``locals``/``vars``/
  ``eval``/``exec`` call that could observe its frame;
- each element position has one type in every assignment and every read.

Its elements then live in hidden locals ``__pcc_tuple_<name>_<k>``: an
assignment stores them, a read loads one, an unpacking assigns from them.
Python's order is kept: when an element after the first could raise, every
element is evaluated into a temporary before any hidden local changes, so a
failing assignment leaves the old elements, as it leaves the old tuple.
Nothing else can observe the tuple: no identity, no escape, no frame view.
"""
from __future__ import annotations

from dataclasses import fields, replace

from pcc.frontends.python.py_ast import (
    Assign,
    AugAssign,
    BoolLit,
    Call,
    ClassDef,
    Delete,
    ExceptHandler,
    Expr,
    FloatLit,
    FuncDef,
    Global,
    Import,
    ImportFrom,
    IntLit,
    Lambda,
    Name,
    Nonlocal,
    NoneLit,
    Stmt,
    StrLit,
    Subscript,
    TupleExpr,
    Try,
    UnaryOp,
)


# Both front-end spellings of the comprehension, suspension and starred
# sentinels (the lifter's and the parser's).
_NESTED_SCOPE_CALLS = (
    "_list_comp", "_dict_comp", "_set_comp", "_gen_comp", "_gen_clause",
    "__listcomp__", "__dictcomp__", "__setcomp__", "__genexpr__",
)
_FRAME_OBSERVERS = ("locals", "vars", "eval", "exec")
_SUSPENDING_CALLS = (
    "_yield", "_yield_from", "_await", "__yield__", "__yield_from__", "__await__",
)
_STARRED_CALLS = ("*", "__starred__")
_LITERALS = (IntLit, FloatLit, StrLit, BoolLit, NoneLit)


def _children(node):
    """The AST nodes directly below ``node`` (statements, expressions,
    handlers), in field order."""
    out = []
    for field in fields(node):
        if field.name in ("span", "ty", "annotation", "return_ty"):
            continue
        value = getattr(node, field.name)
        _collect_nodes(value, out)
    return out


def _collect_nodes(value, out) -> None:
    if isinstance(value, (Expr, Stmt, ExceptHandler)):
        out.append(value)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _collect_nodes(item, out)


def _is_call_to(node, names) -> bool:
    return (
        isinstance(node, Call)
        and isinstance(node.func, Name)
        and node.func.ident in names
    )


def _function_is_eligible(fd: FuncDef) -> bool:
    """No suspension and no frame observer anywhere in the body (nested
    scopes included: a nested ``locals()`` cannot see this frame, but the
    check stays simple and conservative)."""
    if fd.is_async:
        return False
    pending = list(fd.body)
    while pending:
        node = pending.pop()
        if _is_call_to(node, _SUSPENDING_CALLS) or _is_call_to(node, _FRAME_OBSERVERS):
            return False
        pending.extend(_children(node))
    return True


def _int_index(expr):
    """The value of an integer-literal index (``-k`` included), or None."""
    negative = False
    if isinstance(expr, UnaryOp) and expr.op == "-":
        negative = True
        expr = expr.operand
    if isinstance(expr, IntLit) and isinstance(expr.value, int) and not isinstance(expr.value, bool):
        return -expr.value if negative else expr.value
    return None


class _Candidate:
    def __init__(self, name: str) -> None:
        self.name = name
        self.arity = -1
        self.element_types = None
        self.accounted = set()
        self.ok = True

    def note_types(self, types) -> None:
        if self.element_types is None:
            self.element_types = list(types)
            return
        if len(types) != len(self.element_types):
            self.ok = False
            return
        for index, ty in enumerate(types):
            if ty != self.element_types[index]:
                self.ok = False


def _literal_assignment_of(stmt, name: str):
    """The tuple literal ``stmt`` binds to ``name``, or None."""
    if not isinstance(stmt, Assign) or not stmt.has_value or len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if not isinstance(target, Name) or target.ident != name:
        return None
    value = stmt.value
    if not isinstance(value, TupleExpr) or not value.elems:
        return None
    for element in value.elems:
        if _is_call_to(element, _STARRED_CALLS):
            return None
    return value


def _unpacking_of(stmt, name: str):
    """The target tuple of ``t0, ..., = name``, or None."""
    if not isinstance(stmt, Assign) or not stmt.has_value or len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if not isinstance(target, TupleExpr):
        return None
    if not isinstance(stmt.value, Name) or stmt.value.ident != name:
        return None
    for element in target.elems:
        if _is_call_to(element, _STARRED_CALLS):
            return None
    return target


def _binds_name_elsewhere(node, name: str) -> bool:
    """A binding of ``name`` that is not a Name occurrence."""
    if isinstance(node, (Global, Nonlocal)):
        return name in node.names
    if isinstance(node, ExceptHandler):
        return node.name == name
    if isinstance(node, (FuncDef, ClassDef)):
        return node.name == name
    if isinstance(node, Import):
        for module, alias in node.names:
            bound = alias if alias is not None else module.split(".")[0]
            if bound == name:
                return True
        return False
    if isinstance(node, ImportFrom):
        for entry in getattr(node, "names", ()):
            if isinstance(entry, tuple) and len(entry) == 2:
                bound = entry[1] if entry[1] is not None else entry[0]
                if bound == name:
                    return True
        return False
    return False


def _names_in_scope(body, out) -> None:
    """Every local Name identifier of this scope (nested scopes skipped)."""
    pending = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, (FuncDef, ClassDef, Lambda)) or _is_call_to(node, _NESTED_SCOPE_CALLS):
            continue
        if isinstance(node, Name):
            out.add(node.ident)
        pending.extend(_children(node))


def _scan(candidate: _Candidate, stmts, dominated: bool) -> None:
    """Account every occurrence of the candidate in ``stmts``; ``dominated``
    says an enclosing block already bound it before this block began."""
    name = candidate.name
    bound = dominated
    for stmt in stmts:
        if not candidate.ok:
            return
        literal = _literal_assignment_of(stmt, name)
        if literal is not None:
            if candidate.arity < 0:
                candidate.arity = len(literal.elems)
            elif candidate.arity != len(literal.elems):
                candidate.ok = False
                return
            candidate.note_types([element.ty for element in literal.elems])
            candidate.accounted.add(id(stmt.targets[0]))
            # The elements are read before this binding takes effect.
            _scan_expr(candidate, literal, bound)
            bound = True
            continue
        unpack = _unpacking_of(stmt, name)
        if unpack is not None:
            if not bound:
                candidate.ok = False
                return
            candidate.accounted.add(id(stmt.value))
            if candidate.arity >= 0 and candidate.arity != len(unpack.elems):
                candidate.ok = False
                return
            candidate.unpack_arities = getattr(candidate, "unpack_arities", set())
            candidate.unpack_arities.add(len(unpack.elems))
            for target in unpack.elems:
                _scan_expr(candidate, target, bound)
            continue
        _scan_stmt(candidate, stmt, bound)


def _stores_into(target, name: str) -> bool:
    """A store target that writes an element of ``name`` (``name[k] = v``
    raises TypeError on a tuple and must keep doing so)."""
    if isinstance(target, Subscript) and isinstance(target.obj, Name):
        return target.obj.ident == name
    if isinstance(target, TupleExpr):
        return any(_stores_into(element, name) for element in target.elems)
    if _is_call_to(target, _STARRED_CALLS):
        return any(_stores_into(arg, name) for arg in target.args)
    return False


def _scan_stmt(candidate: _Candidate, stmt, bound: bool) -> None:
    name = candidate.name
    if _binds_name_elsewhere(stmt, name):
        candidate.ok = False
        return
    targets = ()
    if isinstance(stmt, (Assign, Delete)):
        targets = stmt.targets
    elif isinstance(stmt, AugAssign):
        targets = (stmt.target,)
    if any(_stores_into(target, name) for target in targets):
        candidate.ok = False
        return
    if isinstance(stmt, (FuncDef, ClassDef)):
        _scan_nested(candidate, stmt)
        return
    if isinstance(stmt, Delete):
        for target in stmt.targets:
            if isinstance(target, Name) and target.ident == name:
                candidate.ok = False
                return
    if isinstance(stmt, Try):
        _scan(candidate, stmt.body, bound)
        for handler in stmt.handlers:
            if handler.name == name:
                candidate.ok = False
                return
            if handler.exc_type is not None:
                _scan_expr(candidate, handler.exc_type, bound)
            _scan(candidate, handler.body, bound)
        _scan(candidate, stmt.else_body, bound)
        _scan(candidate, stmt.finally_body, bound)
        return
    for field in fields(stmt):
        if field.name in ("span", "ty", "annotation", "return_ty"):
            continue
        value = getattr(stmt, field.name)
        if isinstance(value, tuple) and value and all(isinstance(item, Stmt) for item in value):
            _scan(candidate, value, bound)
        else:
            nodes = []
            _collect_nodes(value, nodes)
            for node in nodes:
                if isinstance(node, Stmt):
                    _scan(candidate, (node,), bound)
                elif isinstance(node, ExceptHandler):
                    candidate.ok = False
                    return
                else:
                    _scan_expr(candidate, node, bound)


def _scan_expr(candidate: _Candidate, expr, bound: bool) -> None:
    name = candidate.name
    pending = [expr]
    while pending:
        node = pending.pop()
        if not candidate.ok:
            return
        if isinstance(node, Lambda) or _is_call_to(node, _NESTED_SCOPE_CALLS):
            _scan_nested(candidate, node)
            continue
        if isinstance(node, Subscript) and isinstance(node.obj, Name) and node.obj.ident == name:
            index = _int_index(node.idx)
            if index is None or not bound:
                candidate.ok = False
                return
            candidate.reads = getattr(candidate, "reads", [])
            candidate.reads.append((index, node.ty))
            candidate.accounted.add(id(node.obj))
            continue
        if isinstance(node, Name) and node.ident == name and id(node) not in candidate.accounted:
            candidate.ok = False
            return
        pending.extend(_children(node))


def _scan_nested(candidate: _Candidate, node) -> None:
    """A nested scope may not mention the candidate at all."""
    name = candidate.name
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, Name) and current.ident == name:
            candidate.ok = False
            return
        if _binds_name_elsewhere(current, name):
            candidate.ok = False
            return
        for arg in getattr(current, "args", ()) if isinstance(current, (FuncDef, Lambda)) else ():
            if getattr(arg, "name", None) == name:
                candidate.ok = False
                return
        pending.extend(_children(current))


def _qualifies(candidate: _Candidate) -> bool:
    if not candidate.ok or candidate.arity <= 0 or candidate.element_types is None:
        return False
    for arity in getattr(candidate, "unpack_arities", set()):
        if arity != candidate.arity:
            return False
    for index, ty in getattr(candidate, "reads", []):
        if index < -candidate.arity or index >= candidate.arity:
            return False
        if ty != candidate.element_types[index % candidate.arity]:
            return False
    return True


def _hidden(name: str, index: int) -> str:
    return "__pcc_tuple_" + name + "_" + str(index)


def _temporary(name: str, index: int) -> str:
    return "__pcc_tuple_" + name + "_" + str(index) + "_next"


def _rewrite_stmts(stmts, plans):
    out = []
    for stmt in stmts:
        out.extend(_rewrite_stmt(stmt, plans))
    return tuple(out)


def _rewrite_stmt(stmt, plans):
    if isinstance(stmt, Assign) and stmt.has_value and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if isinstance(target, Name) and target.ident in plans and isinstance(stmt.value, TupleExpr):
            return _rewrite_literal_assignment(stmt, target.ident, plans)
        if (isinstance(target, TupleExpr) and isinstance(stmt.value, Name)
                and stmt.value.ident in plans):
            name = stmt.value.ident
            plan = plans[name]
            assigns = []
            for index, element in enumerate(target.elems):
                source = Name(span=stmt.value.span, ty=plan[index], ident=_hidden(name, index))
                assigns.append(Assign(
                    span=stmt.span,
                    targets=(_rewrite_expr(element, plans),),
                    value=source,
                ))
            return assigns
    if isinstance(stmt, (FuncDef, ClassDef)):
        # A nested scope never mentions a replaced local.
        return [stmt]
    changes = {}
    for field in fields(stmt):
        if field.name in ("span", "ty", "annotation", "return_ty"):
            continue
        value = getattr(stmt, field.name)
        rewritten = _rewrite_value(value, plans)
        if rewritten is not value:
            changes[field.name] = rewritten
    if changes:
        return [replace(stmt, **changes)]
    return [stmt]


def _rewrite_literal_assignment(stmt, name, plans):
    plan = plans[name]
    elements = [_rewrite_expr(element, plans) for element in stmt.value.elems]
    span = stmt.span
    # Only the first element may raise when the others are literals: then
    # each hidden local can take its element directly. Otherwise every
    # element is evaluated before any hidden local changes.
    direct = all(isinstance(element, _LITERALS) for element in elements[1:])
    assigns = []
    if direct:
        for index, element in enumerate(elements):
            assigns.append(Assign(
                span=span,
                targets=(Name(span=span, ty=plan[index], ident=_hidden(name, index)),),
                value=element,
            ))
        return assigns
    for index, element in enumerate(elements):
        assigns.append(Assign(
            span=span,
            targets=(Name(span=span, ty=plan[index], ident=_temporary(name, index)),),
            value=element,
        ))
    for index in range(len(elements)):
        assigns.append(Assign(
            span=span,
            targets=(Name(span=span, ty=plan[index], ident=_hidden(name, index)),),
            value=Name(span=span, ty=plan[index], ident=_temporary(name, index)),
        ))
    return assigns


def _rewrite_value(value, plans):
    if isinstance(value, Stmt):
        rewritten = _rewrite_stmt(value, plans)
        if len(rewritten) == 1:
            return rewritten[0]
        return value
    if isinstance(value, ExceptHandler):
        body = _rewrite_stmts(value.body, plans)
        exc_type = _rewrite_expr(value.exc_type, plans) if value.exc_type is not None else None
        if body != value.body or exc_type is not value.exc_type:
            return replace(value, body=body, exc_type=exc_type)
        return value
    if isinstance(value, Expr):
        return _rewrite_expr(value, plans)
    if isinstance(value, tuple):
        if value and all(isinstance(item, Stmt) for item in value):
            rewritten = _rewrite_stmts(value, plans)
            return rewritten if rewritten != value else value
        items = tuple(_rewrite_value(item, plans) for item in value)
        if any(new is not old for new, old in zip(items, value)):
            return items
        return value
    return value


def _rewrite_expr(expr, plans):
    if isinstance(expr, Subscript) and isinstance(expr.obj, Name) and expr.obj.ident in plans:
        name = expr.obj.ident
        plan = plans[name]
        index = _int_index(expr.idx) % len(plan)
        return Name(span=expr.span, ty=expr.ty, ident=_hidden(name, index))
    if isinstance(expr, Lambda) or _is_call_to(expr, _NESTED_SCOPE_CALLS):
        return expr
    changes = {}
    for field in fields(expr):
        if field.name in ("span", "ty"):
            continue
        value = getattr(expr, field.name)
        rewritten = _rewrite_value(value, plans)
        if rewritten is not value:
            changes[field.name] = rewritten
    if changes:
        return replace(expr, **changes)
    return expr


def _plan_function(fd: FuncDef):
    """``{name: element types}`` for this function's replaceable locals."""
    if not _function_is_eligible(fd):
        return {}
    names = set()
    _names_in_scope(fd.body, names)
    parameters = {arg.name for arg in fd.args}
    plans = {}
    for name in sorted(names):
        if name in parameters or name.startswith("__pcc_tuple_"):
            continue
        assigned = False
        pending = list(fd.body)
        while pending and not assigned:
            node = pending.pop()
            if isinstance(node, (FuncDef, ClassDef, Lambda)):
                continue
            if _literal_assignment_of(node, name) is not None:
                assigned = True
            pending.extend(_children(node))
        if not assigned:
            continue
        candidate = _Candidate(name)
        _scan(candidate, fd.body, False)
        if not _qualifies(candidate):
            continue
        hidden = [_hidden(name, index) for index in range(candidate.arity)]
        hidden += [_temporary(name, index) for index in range(candidate.arity)]
        if any(ident in names for ident in hidden):
            continue
        plans[name] = tuple(candidate.element_types)
    return plans


def _rewrite_function(fd: FuncDef) -> FuncDef:
    body = _rewrite_nested(fd.body)
    plans = _plan_function(replace(fd, body=body) if body is not fd.body else fd)
    if plans:
        body = _rewrite_stmts(body, plans)
    if body is fd.body:
        return fd
    return replace(fd, body=body)


def _rewrite_nested(stmts):
    """Rewrite the functions defined in ``stmts`` (methods, nested defs)."""
    out = []
    changed = False
    for stmt in stmts:
        new = stmt
        if isinstance(stmt, FuncDef):
            new = _rewrite_function(stmt)
        elif isinstance(stmt, ClassDef):
            body = _rewrite_nested(stmt.body)
            if body is not stmt.body:
                new = replace(stmt, body=body)
        if new is not stmt:
            changed = True
        out.append(new)
    if not changed:
        return stmts
    return tuple(out)


def scalar_replace_local_tuples(body):
    """The module body with every function's non-escaping local tuples
    replaced by their elements (see the module docstring)."""
    return _rewrite_nested(tuple(body))
