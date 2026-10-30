"""Bounded text templates, inspired by arXiv:2610.31342, not Typos itself.

The preprocessing annotations are checked in. No LLM or Python parser is used
inside the mutation loop. A separate safety validator protects the execution
harness; it is not used to select or repair mutants.
"""

from dataclasses import dataclass
import random


TYPES = ("int", "str", "bool", "list[int]", "tuple[int,int]")
MAX_DEPTH = 2
MAX_SOURCE_BYTES = 4096


@dataclass(frozen=True)
class Term:
    text: str
    typ: str
    free: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class Operator:
    text: str
    typ: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class Template:
    name: str
    text: str
    seed: str
    default: Term
    live: frozenset[tuple[str, str]]

    def instantiate(self, term: Term) -> str:
        if self.text.count("@@TERM@@") != 1:
            raise ValueError("one occurrence is required")
        source = self.text.replace("@@TERM@@", term.text)
        if len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("source size bound exceeded")
        return source

    def check_grounded(self) -> None:
        if self.instantiate(self.default) != self.seed:
            raise ValueError("template does not reproduce its seed")
        if not self.default.free <= self.live:
            raise ValueError("seed annotation is out of scope")


CONSTANTS = (
    Term("0", "int"), Term("1", "int"), Term("3", "int"),
    Term("-2", "int"), Term("'a'", "str"), Term("'bc'", "str"),
    Term("''", "str"), Term("True", "bool"), Term("False", "bool"),
    Term("[1, 2]", "list[int]"), Term("[3]", "list[int]"),
    Term("(1, 2)", "tuple[int,int]"), Term("(3, 0)", "tuple[int,int]"),
)

# Cross-template variables are intentional: the naive ablation does not filter
# them. The scope-only ablation removes that source of invalidity.
VARIABLES = tuple(Term(name, typ, frozenset({(name, typ)})) for name, typ in (
    ("a", "int"), ("b", "int"), ("xs", "list[int]"),
    ("text", "str"), ("flag", "bool"), ("pair", "tuple[int,int]"),
    ("other", "int"), ("label", "str"),
))

OPERATORS = (
    Operator("({0} + {1})", "int", ("int", "int")),
    Operator("({0} - {1})", "int", ("int", "int")),
    Operator("({0} * {1})", "int", ("int", "int")),
    Operator("len({0})", "int", ("str",)),
    Operator("len({0})", "int", ("list[int]",)),
    Operator("len({0})", "int", ("tuple[int,int]",)),
    Operator("({0} + {1})", "str", ("str", "str")),
    Operator("({0} < {1})", "bool", ("int", "int")),
    Operator("({0} == {1})", "bool", ("str", "str")),
    Operator("(not {0})", "bool", ("bool",)),
    Operator("({0} and {1})", "bool", ("bool", "bool")),
    Operator("[{0}, {1}]", "list[int]", ("int", "int")),
    Operator("({0} + {1})", "list[int]", ("list[int]", "list[int]")),
    Operator("({0}, {1})", "tuple[int,int]", ("int", "int")),
)


def _template(name: str, typ: str, expression: str, default: str) -> Template:
    text = (
        "def probe(a: int, b: int):\n"
        "    xs = [a, b]\n"
        "    text = 'abc'\n"
        "    flag = a < b\n"
        "    pair = (a, b)\n"
        f"    return {expression}\n"
        "result = probe(2, 5)\n"
    )
    term = Term(default, typ)
    return Template(name, text, text.replace("@@TERM@@", default), term,
                    frozenset((t.text, t.typ) for t in VARIABLES[:6]))


TEMPLATES = (
    _template("integer", "int", "@@TERM@@ + a", "1"),
    _template("string", "str", "@@TERM@@ + text", "'a'"),
    _template("list", "list[int]", "@@TERM@@ + xs", "[1, 2]"),
    _template("tuple", "tuple[int,int]", "@@TERM@@[0] + a", "(1, 2)"),
    _template("boolean", "bool", "1 if @@TERM@@ else 2", "True"),
)


def generate_term(rng: random.Random, typ: str, live: frozenset,
                  depth: int, mode: str) -> Term:
    if mode not in ("typed", "scope_only", "naive"):
        raise ValueError("unknown mutation mode")
    if not 0 <= depth <= MAX_DEPTH:
        raise ValueError("depth exceeds fixed generator bound")
    leaves = [term for term in CONSTANTS + VARIABLES
              if (mode == "naive" or term.free <= live)
              and (mode != "typed" or term.typ == typ)]
    operators = [op for op in OPERATORS if mode != "typed" or op.typ == typ]
    if depth == 0 or rng.random() < 0.4:
        return rng.choice(leaves)
    operator = rng.choice(operators)
    args = [generate_term(rng, arg_type, live, depth - 1, mode)
            for arg_type in operator.inputs]
    return Term(operator.text.format(*(arg.text for arg in args)), operator.typ,
                frozenset().union(*(arg.free for arg in args)))


def mutant(seed: int, index: int, mode: str, depth: int = 2):
    # Paired seed/template schedule across modes; no adaptive corpus feedback.
    rng = random.Random((seed << 20) + index)
    template = TEMPLATES[index % len(TEMPLATES)]
    template.check_grounded()
    term = generate_term(rng, template.default.typ, template.live, depth, mode)
    return template, term, template.instantiate(term)
