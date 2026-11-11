"""Named notation for building core terms and case trees in kernel tests.

The kernel works on de Bruijn terms.  Tests write names and this module
resolves them, so a test reads like the declaration it states.
"""

from __future__ import annotations

from pcc.proof.terms import App, Const, Lam, Leaf, Pi, Sort, Split, Var, With

TYPE = ("sort", 0)
TYPE1 = ("sort", 1)


def V(name):
    return ("v", name)


def C(name):
    return ("c", name)


def A(fn, *args):
    return ("app", fn, args)


def P(name, domain, codomain):
    return ("pi", name, domain, codomain)


def L(name, domain, body):
    return ("lam", name, domain, body)


def pis(binders, result):
    """``pis([("x", A), ("y", B)], R)`` is ``Pi x: A. Pi y: B. R``."""
    for name, domain in reversed(binders):
        result = P(name, domain, result)
    return result


def fn(domain, codomain):
    return P("_", domain, codomain)


def core(expr, names=()):
    names = list(names)
    kind = expr[0]
    if kind == "v":
        for position in range(len(names) - 1, -1, -1):
            if names[position] == expr[1]:
                return Var(len(names) - 1 - position)
        raise KeyError(f"unbound test variable {expr[1]}")
    if kind == "c":
        return Const(expr[1])
    if kind == "sort":
        return Sort(expr[1])
    if kind == "app":
        term = core(expr[1], names)
        for arg in expr[2]:
            term = App(term, core(arg, names))
        return term
    if kind == "pi":
        return Pi(expr[1], core(expr[2], names), core(expr[3], names + [expr[1]]))
    if kind == "lam":
        return Lam(expr[1], core(expr[2], names), core(expr[3], names + [expr[1]]))
    raise ValueError(f"unknown test term {expr!r}")


def leaf(expr):
    return ("leaf", expr)


def split(name, *branches):
    """``split("n", ("zero", [], t0), ("suc", ["m"], t1))``."""
    return ("split", name, branches)


def with_(expr, name, motive, body):
    return ("with", expr, name, motive, body)


def tree(node, names):
    names = list(names)
    kind = node[0]
    if kind == "leaf":
        return Leaf(core(node[1], names))
    if kind == "split":
        level = None
        for position in range(len(names) - 1, -1, -1):
            if names[position] == node[1]:
                level = position
                break
        if level is None:
            raise KeyError(f"unbound test variable {node[1]}")
        branches = tuple(
            (cname, tree(subtree, names + list(new_names)))
            for cname, new_names, subtree in node[2]
        )
        return Split(level, branches)
    if kind == "with":
        return With(
            core(node[1], names),
            core(node[3], names + [node[2]]),
            tree(node[4], names + [node[2]]),
        )
    raise ValueError(f"unknown test tree {node!r}")


def function(kernel, name, binders, result, body, extra_type=None):
    """Declare ``name`` with parameters ``binders`` and case tree ``body``."""
    type_expr = pis(binders, result)
    names = [binder_name for binder_name, _ in binders]
    kernel.add_function(name, core(type_expr), len(binders), tree(body, names))


def data(kernel, name, params, indices, constructors):
    """Declare a data type.

    ``constructors`` is ``[(name, [(arg, type), ...], [index terms])]``; each
    constructor result is ``name params indices``.
    """
    type_expr = pis(list(params) + list(indices), TYPE)
    param_vars = [V(param_name) for param_name, _ in params]
    built = []
    for cname, args, result_indices in constructors:
        result = A(C(name), *(param_vars + list(result_indices))) if (
            param_vars or result_indices
        ) else C(name)
        built.append((cname, core(pis(list(params) + list(args), result))))
    kernel.add_data(name, len(params), core(type_expr), built)
