"""Tuple struct declarations, constructors and positional fields.

https://doc.rust-lang.org/reference/items/structs.html
https://doc.rust-lang.org/reference/expressions/struct-expr.html#tuple-struct-expression
`struct S<T>(pub T, u64);` parses into a record whose fields are named by
their decimal indices, so `s.0` is ordinary field access. `S(a, b)`,
`S::<T>(a)` and `module::S(a)` call the constructor in the value namespace
and are checked and lowered as the equivalent struct expression. A braced
struct is not callable (rustc E0423), the argument count must match (E0061)
and a private field keeps the constructor private outside its module.
Tuple-struct patterns keep the existing unqualified-pattern gap.
fallible-streaming-iterator's `pub struct Empty<T, E>(PhantomData<(T, E)>);`
(lib.rs:427) has this shape; the programs below are original.
"""
import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function

PROGRAM = ('#[derive(Clone, Copy)] struct Pair(u64, u32,);\n'
           'pub struct Wrap<T>(pub T);\n'
           'mod m { pub struct Cell(pub u64); }\n'
           'fn sum(p: Pair) -> u64 { p.0 + p.1 as u64 }\n'
           'pub fn answer() -> u64 {\n'
           '    let w = Wrap::<u64>(0xfedcba9876543200);\n'
           '    let v: Wrap<u64> = Wrap(0x10);\n'
           '    w.0 + v.0 + sum(Pair(1, 1)) + m::Cell(0).0\n'
           '}\n')
EXPECTED = 0xfedcba9876543212


def test_fields_are_named_by_index():
    parser = Parser('pub struct S<T>(pub T, u64,);')
    parser.parse()
    record, = parser.records
    assert record.tuple_fields and [field.text for field, _ in record.fields] == ['0', '1']
    assert [str(ty) for _, ty in record.fields] == ['T', 'u64']


def test_program_checks_and_lowers():
    result = compile_rust(PROGRAM, experimental_rustc_checked=True)
    assert 'answer' in result.symbols


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch) == EXPECTED


@pytest.mark.parametrize('source,message', [
    ('struct P(u64); pub fn f()->u64{P(1,2).0}', r'takes 1 field\(s\) but 2 were supplied \(rustc E0061\)'),
    ('struct S{a:u64} pub fn f()->u64{S(1).a}', r'found struct `S` \(rustc E0423\)'),
    ('struct P(u64); pub fn f()->u64{P(1).1}', 'unknown struct field'),
    ('mod m { pub struct P(u64); } pub fn f()->u64{ m::P(42).0 }', 'private field'),
    ('struct P(u64); pub fn f()->u64{match P(1){P(x)=>x}}', 'unqualified tuple-struct/variant patterns'),
])
def test_invalid_uses_and_patterns_stay_errors_or_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)
