"""Private precedence climbing preserves the original parser grammar."""

from itertools import product

import pytest

from pcc.frontends.python import py_parse as pp
from pcc.frontends.python import py_lex as lex


OPS = ("|", "^", "&", "<<", ">>", "+", "-", "*", "/", "//", "%", "@")


def legacy(source, filename="<precedence>"):
    parser = pp.Parser(source, filename)
    return pp.Parser.parse_module(parser)


@pytest.mark.parametrize("left,right", product(OPS, repeat=2))
def test_all_binary_pairs_keep_exact_ast_and_locations(left, right):
    source = f"x = (a {left}\n b {right}\n c)\n"
    assert pp.parse(source, "<precedence>") == legacy(source)


@pytest.mark.parametrize("source", [
    "x = a | b ^ c & d << e + f * g\n",
    "x = a - b - c / d // e % f @ g\n",
    "x = -a ** -b + ~c * +d\n",
    "x = not a + b < c << d and e is not f or g not in h\n",
    "x = a + b < c * d <= e | f\n",
    "x = (a + b) * (c - d) ** e\n",
    "x = (a +\n b *\n c -\n d)\n",
    "x = [a + b * c for a in items if a & 1]\n",
    "x = {a << b: c - d for a in items}\n",
    "x = lambda a: a + 1 << 2\n",
    "x = f'{a + b * c!r}'\n",
    "def f(a: int | str = 1 + 2) -> int | None:\n    return a << 1\n",
    "async def f():\n    return await g() + h()\n",
    "x = a + b if c * d else e | f\n",
    "x = (a, b + c, d * e)\n",
    "x = obj[a + b:c * d:e << f]\n",
])
def test_other_expression_owners_keep_exact_ast(source):
    assert pp.parse(source, "<precedence>") == legacy(source)


@pytest.mark.parametrize("source", [
    "x = a +\n", "x = a * * b\n", "x = (a | )\n",
    "x = a <<\n", "x = a ^ ^ b\n", "x = (a % b\n",
    "x = a / / b\n", "x = (a + b, c &)\n",
])
def test_malformed_expression_keeps_exact_error(source):
    with pytest.raises(Exception) as old:
        legacy(source)
    with pytest.raises(type(old.value)) as new:
        pp.parse(source, "<precedence>")
    assert str(new.value) == str(old.value)


def test_only_top_level_exact_owner_enables_private_precedence(monkeypatch):
    seen = []
    original = pp._binary_precedence_route_is_owned

    def observe(parser, source, filename):
        result = original(parser, source, filename)
        seen.append((parser, result))
        return result

    monkeypatch.setattr(pp, "_binary_precedence_route_is_owned", observe)
    source = "x = a + b * c\n"
    direct = pp.Parser(source, "<precedence>")
    assert not direct._owned_binary_precedence
    baseline = direct.parse_module()
    assert pp.parse(source, "<precedence>") == baseline
    assert len(seen) == 1
    assert seen[0][1] is True
    assert seen[0][0]._owned_binary_precedence is True


def test_public_parser_subclass_keeps_old_hook(monkeypatch):
    seen = []
    original = pp.Parser

    class Custom(original):
        def _parse_mul(self):
            seen.append("mul")
            return super()._parse_mul()

    source = "x = a + b * c\n"
    direct = Custom(source, "<precedence>")
    baseline = direct.parse_module()
    assert seen
    expected = list(seen)
    seen.clear()
    monkeypatch.setattr(pp, "Parser", Custom)
    assert pp.parse(source, "<precedence>") == baseline
    assert seen == expected


def test_exact_parser_class_method_mutation_keeps_old_hook(monkeypatch):
    seen = []
    original = pp.Parser._parse_bitxor

    def custom(self):
        seen.append("bitxor")
        return original(self)

    monkeypatch.setattr(pp.Parser, "_parse_bitxor", custom)
    source = "x = a + b * c\n"
    baseline = legacy(source)
    expected = list(seen)
    assert expected
    seen.clear()
    assert pp.parse(source, "<precedence>") == baseline
    assert seen == expected


def test_custom_lexer_hook_preserves_token_getter_sequence(monkeypatch):
    reads = []
    original = lex.Lexer._read_name

    class CustomToken(lex.Token):
        def __getattribute__(self, name):
            if name in ("kind", "text"):
                reads.append(name)
            return super().__getattribute__(name)

    def custom(self):
        token = original(self)
        return CustomToken(token.kind, token.text, token.line, token.col, token.raw_text)

    monkeypatch.setattr(lex.Lexer, "_read_name", custom)
    source = "x = a + b * c\n"
    baseline = legacy(source)
    expected = list(reads)
    assert expected
    reads.clear()
    assert pp.parse(source, "<precedence>") == baseline
    assert reads == expected


def test_custom_source_type_and_new_helper_mutation_fail_closed(monkeypatch):
    class Source(str):
        pass

    source = Source("x = a + b * c\n")
    parser = pp.Parser(source, "<precedence>")
    assert not pp._binary_precedence_route_is_owned(parser, source, "<precedence>")

    def broken(self, minimum):
        raise AssertionError("replacement private helper must not be selected")

    monkeypatch.setattr(pp.Parser, "_parse_binary_precedence", broken)
    ordinary = str(source)
    assert pp.parse(ordinary, "<precedence>") == legacy(ordinary)


def test_changed_private_priority_table_retains_legacy_grammar(monkeypatch):
    source = "x = a + b * c\n"
    expected = legacy(source)
    monkeypatch.setitem(pp._BINARY_PRECEDENCE, "+", 99)
    parser = pp.Parser(source, "<precedence>")
    assert not pp._binary_precedence_route_is_owned(parser, source, "<precedence>")
    assert pp.parse(source, "<precedence>") == expected


def test_lexer_token_factory_substitution_keeps_legacy_route(monkeypatch):
    reads = []
    original = lex.Token

    class Foreign(original):
        def __getattribute__(self, name):
            if name in ("kind", "text"):
                reads.append(name)
            return super().__getattribute__(name)

    monkeypatch.setattr(lex, "Token", Foreign)
    source = "x = a + b * c\n"
    expected = legacy(source)
    before = list(reads)
    reads.clear()
    assert pp.parse(source, "<precedence>") == expected
    assert reads == before
