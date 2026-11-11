"""Readable rendering of core terms for diagnostics."""

from __future__ import annotations

from pcc.proof.terms import App, Const, Lam, Meta, Pi, Sort, Var, unapply


def _short(name):
    return name.rsplit(".", 1)[-1]


def _numeral(term):
    """The natural number a ``suc (suc ... zero)`` chain denotes, if any."""
    count = 0
    while True:
        if isinstance(term, Const) and term.name == "zero":
            return count
        if isinstance(term, App) and isinstance(term.fn, Const) and term.fn.name == "suc":
            count += 1
            term = term.arg
            continue
        return None


def _fresh(name, names):
    base = name if name and name != "_" else "x"
    candidate = base
    suffix = 1
    while candidate in names:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def show(term, names=()):
    """Render ``term``; ``names`` lists the context, innermost last."""
    return _show(term, list(names), False)


def _show(term, names, atom):
    numeral = _numeral(term)
    if numeral is not None:
        return str(numeral)
    if isinstance(term, Var):
        position = len(names) - 1 - term.index
        if 0 <= position < len(names):
            return names[position]
        return f"#{term.index}"
    if isinstance(term, Const):
        return _short(term.name)
    if isinstance(term, Sort):
        return "Type" if term.level == 0 else f"Type{term.level}"
    if isinstance(term, Meta):
        return f"?{term.ident}"
    if isinstance(term, App):
        head, args = unapply(term)
        rendered = ", ".join(_show(arg, names, False) for arg in args)
        return f"{_show(head, names, True)}({rendered})"
    if isinstance(term, Lam):
        name = _fresh(term.name, names)
        text = f"lambda {name}: {_show(term.body, names + [name], False)}"
        return f"({text})" if atom else text
    if isinstance(term, Pi):
        domain = _show(term.domain, names, True)
        name = _fresh(term.name, names)
        codomain = _show(term.codomain, names + [name], False)
        if term.name in ("", "_"):
            text = f"Fn[{domain}, {codomain}]"
        else:
            text = f"Pi[{name}: {domain}, {codomain}]"
        return text
    return repr(term)
