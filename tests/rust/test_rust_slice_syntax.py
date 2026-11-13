"""Slice types `[T]` are parsed so a source can be read past them.

https://doc.rust-lang.org/reference/types/slice.html
Slices (unsized referents, fat pointers, unsizing coercions) are not
implemented. `[T]` is retained as a SliceType carrying its token, and every
use is reported there as the slice gap, before the generic checked-reference
boundary and in the checked semantic model alike.
"""
import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import SharedReference, SliceType


def test_slice_types_parse_with_their_token():
    parser = Parser('fn f(a: &[u64], b: &[[u32; 2]]) {}')
    function, = parser.parse()
    (_, first, _), (_, second, _) = function.params
    assert first == SharedReference(SliceType('u64'))
    assert isinstance(second.referent, SliceType) and second.referent.element.element == 'u32'
    assert (first.referent.token.line, first.referent.token.column) == (1, 10)


@pytest.mark.parametrize('source', [
    'fn f(a: &[u64]) {}',
    'fn f(a: [u64]) {}',
    'struct S{x:u64} impl S{fn g(&self, a: &[u64]) {}}',
])
def test_every_slice_use_reports_the_slice_gap_at_its_token(source):
    with pytest.raises(RustError, match=r'^1:\d+: slice types and array-to-slice unsizing are not implemented'):
        compile_rust(source, experimental_rustc_checked=True)
    parser = Parser(source)
    functions = parser.parse()
    with pytest.raises(RustError, match='slice types and array-to-slice unsizing'):
        Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits,
                externally_checked=True).check()  # isolated semantic model, never certification
