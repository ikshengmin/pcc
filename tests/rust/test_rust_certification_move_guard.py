"""The checked-lane test guard must preserve owned reference-free moves."""
import pytest

from pcc.frontends.rust.moves import MoveChecker
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser, RustError
from tests.rust.certification_support import forbid_moves_for_references


MIXED = 'fn id<T>(x:T)->T{x} fn read(x:&u64)->u64{*x}'


def forbidden(*args, **kwargs):
    raise AssertionError('reference-bearing move admission')


def checker_for(source):
    parser = Parser(source)
    return Checker(parser.parse(), parser.records, parser.enums,
                   parser.implementations, externally_checked=True)


def test_mixed_source_keeps_reference_free_generic_move_check(monkeypatch):
    calls = []
    original = MoveChecker.check_function

    def observe(self, function):
        calls.append(function.token.text)
        return original(self, function)

    monkeypatch.setattr(MoveChecker, 'check_function', observe)
    forbid_moves_for_references(monkeypatch, MIXED, forbidden)
    checker_for(MIXED).check()
    assert calls == ['id']


def test_mixed_source_does_not_hide_invalid_reference_free_generic(monkeypatch):
    source = 'fn bad<T>(x:T)->T{let y=x;x} fn read(x:&u64)->u64{*x}'
    forbid_moves_for_references(monkeypatch, source, forbidden)
    with pytest.raises(RustError, match='moved'):
        checker_for(source).check()


def test_mixed_source_still_forbids_whole_program_moves(monkeypatch):
    forbid_moves_for_references(monkeypatch, MIXED, forbidden)
    with pytest.raises(AssertionError, match='reference-bearing move admission'):
        MoveChecker(checker_for(MIXED)).check()


def test_mixed_source_still_forbids_reference_function_moves(monkeypatch):
    checker = checker_for(MIXED)
    reference_function = checker.functions[1]
    forbid_moves_for_references(monkeypatch, MIXED, forbidden)
    with pytest.raises(AssertionError, match='reference-bearing move admission'):
        MoveChecker(checker).check_function(reference_function)


def test_legacy_shared_generic_preserves_reference_free_method_moves(monkeypatch):
    # This is semantic guard coverage, not a minted external check or execution
    # claim. The public checked test separately requires real rustc evidence.
    from pathlib import Path
    source = Path(__file__).with_name('programs').joinpath('shared_generic.rs').read_text()
    calls = []
    original = MoveChecker.check_function

    def observe(self, function):
        calls.append((function.token.text, function.instance_arguments is not None))
        return original(self, function)

    monkeypatch.setattr(MoveChecker, 'check_function', observe)
    forbid_moves_for_references(monkeypatch, source, forbidden)
    checker_for(source).check()
    assert calls == [('into', False), ('into', True)]
