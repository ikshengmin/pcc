"""Integer range iteration: `for p in a..b` and `for p in a..=b`.

Rust Reference: https://doc.rust-lang.org/reference/expressions/loop-expr.html#iterator-loops
and https://doc.rust-lang.org/reference/expressions/range-expr.html; core
Range::next / RangeInclusive::next over integer Step types. Bounds are
evaluated once, start first. `a..b` yields a..b-1 and its increment follows
`cursor < end`; `a..=b` yields b last through an exhausted flag, so no step
overflows even at the type maximum. Other Step types, RangeFrom and custom
iterators stay explicit gaps.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite

PROGRAMS = ('range_exclusive', 'range_inclusive_max', 'range_signed', 'range_control', 'range_bounds_once')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text,kind', [
    ('fn f(){for i in 0..10u64{}}', 'for_range'),
    ('fn f(){for _ in 1u32..=2{}}', 'for_range_inclusive'),
    ('fn f(n:u64){for i in n-1..n+1{}}', 'for_range'),
])
def test_range_loops_keep_both_bounds_and_body(text, kind):
    function, = Parser(text).parse()
    loop = function.body.children[0]
    while loop.kind not in {'for_range', 'for_range_inclusive'}:
        loop = loop.children[0]
    assert loop.kind == kind and len(loop.children) == 3
    assert loop.children[2].kind == 'block'


def test_unbounded_range_iteration_is_an_explicit_gap():
    with pytest.raises(RustError, match='unbounded range iteration'):
        Parser('fn f(){for i in 0u64..{}}').parse()


def test_array_iteration_keeps_its_own_node():
    function, = Parser('fn f(a:[u64;2]){for x in a{}}').parse()
    assert function.body.children[0].kind == 'for'


@pytest.mark.parametrize('name', PROGRAMS)
def test_range_originals_lower_to_owned_objects(name):
    result = compile_rust(source(name), experimental_rustc_checked=True)
    assert result.emit_object().startswith(b'\x7fELF')
    assert 'range.next' in result.ir_text


@pytest.mark.parametrize('name', PROGRAMS)
def test_range_originals_pass_the_owned_move_model(name):
    analyze_source(source(name))


INVALID = (
    ('mismatched-bounds', 'fn f(){for i in 0u32..1u64{}}', 'type mismatch', 'E0308'),
    ('bool-range', 'fn f(){for b in false..true{}}', 'requires a Step implementation', 'E0277'),
    ('binding-escapes', 'fn f(){for i in 0u64..2{}let x=i;}', 'unknown local', 'E0425'),
    ('move-in-range-body', 'struct R{x:u64}fn f(value:R){for _ in 0u64..2{let moved=value;}}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_range_loops_reject_in_owned_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_range_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_range_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
