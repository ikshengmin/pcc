"""Rust char is a Unicode scalar, not a u32/u8 ABI or a named Rust struct."""
import pytest

from pcc.frontends.rust import RustError,compile_rust
from pcc.frontends.rust.syntax import lex,Parser
from tests.rust.str_execution import HOST_TARGET as OWNED_HOST_TARGET


@pytest.mark.parametrize('source,value',[
    ("'A'",'A'),("'📦'",'📦'),("' '",' '),("""'"'""",'"'),("'\u0301'",'\u0301'),
    (r"'\n'",'\n'),(r"'\r'",'\r'),(r"'\t'",'\t'),(r"'\0'",'\0'),(r"'\\'",'\\'),
    (r"'\''","'"),(r"""'\"'""",'"'),(r"'\x7f'",'\x7f'),
    (r"'\u{1_f4_e6___}'",'📦'),(r"'\u{10ffff}'",'\U0010ffff'),
])
def test_each_character_is_one_token_with_the_original_codepoint_span(source,value):
    token=lex(' '+source)[0]
    assert (token.text,token.kind,token.literal_value)==(source,'character',value)
    assert (token.line,token.column,token.source_start,token.source_end)==(1,2,1,len(source)+1)
    assert compile_rust('fn code()->u32{'+source+' as u32}').emit_object()[:4]==b'\x7fELF'


@pytest.mark.parametrize('source',["''","'''","'AB'","'e\u0301'","'\udfff'","'\t'","'\n'","'\r'",
    r"'\xFF'",r"'\x4'",r"'\x0z'",r"'\u{d800}'",r"'\u{110000}'",r"'\u{_41}'",
    r"'\u{}'",r"'\u{0000000}'",r"'\q'", "'\\\n'", "'A'foo"])
def test_malformed_or_different_literal_kinds_never_lower_as_char(source):
    with pytest.raises(RustError):
        compile_rust('fn code()->u32{'+source+' as u32}')


@pytest.mark.parametrize('src',[
    "fn f()->char{b'A'}", "fn f()->u32{'A'}","fn f()->char{65u32}","fn f()->char{65u32 as char}",
    "fn f()->bool{'a'==97u32}","fn f()->char{'a'+'a'}","fn f()->char{'a'&'b'}",
    "fn f()->bool{!'a'}","fn f()->u32{'a'&&'b';0}",
    "fn f(){let mut a='a'; a+= 'a';}",
])
def test_invalid_integer_aliases_and_operators_reject(src):
    with pytest.raises(RustError):
        compile_rust(src)


def test_lifetimes_do_not_change_and_normal_nominal_type_resolution_wins():
    toks=lex("'a 'static '_ 'x' 'xyz")
    assert [t.kind for t in toks[:-1]]==['lifetime','lifetime','lifetime','character','lifetime']
    compile_rust("pub fn same<'a>(x:&'a i32)->&'a i32{x}")
    compile_rust("struct char {n:u32} fn f()->u32{let c=char{n:22};c.n}")
    with pytest.raises(RustError):
        compile_rust("struct char {n:u32} fn f()->char{'a'}")
    # A name in an ancestor module is not an implicit lexical import. A
    # scoped module graph uses opaque declaration keys, never the root spelling.
    compile_rust("struct char {n:u32} mod inner {pub fn letter()->char{'x'}}")
    compile_rust("struct char {n:u32} mod inner {use super::char; pub fn read(c:char)->u32{c.n}}")
    with pytest.raises(RustError):
        compile_rust("struct char {n:u32} mod inner {use super::char; pub fn letter()->char{'x'}}")
    compile_rust("mod m {pub struct char {pub n:u32}} use m::char; fn f(v:char)->u32{v.n}")
    with pytest.raises(RustError):
        compile_rust("mod m {pub struct char {pub n:u32}} use m::char; fn f()->char{'a'}")


def test_char_const_copy_aggregate_and_unsigned_comparisons_are_typed():
    src="""const C:char='📦'; #[derive(Clone,Copy,PartialEq)] struct S{value:char}
fn less(x:char,y:char)->bool{x<y}
pub fn f()->u32{let a=S{value:C};let b=a;if a==b && less('a',C){b.value as u32}else{0}}
"""
    c=compile_rust(src)
    assert 'icmp ult i32' in c.ir_text and c.emit_object()[:4]==b'\x7fELF'


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(probe=lambda: OWNED_HOST_TARGET is not None)
def test_machine_result_observes_scalar_value_unsigned_order_and_copy(tmp_path,monkeypatch):
    from tests.rust.str_execution import HOST_TARGET,UNAVAILABLE,run_owned_u64_function
    if HOST_TARGET is None:
        pytest.fail(UNAVAILABLE)
    source = """fn less(c:char,d:char)->bool{c<d}
pub fn value()->u64{let c='📦';let copy=c;if less('中',copy) && c==copy{copy as u64}else{1}}"""
    assert run_owned_u64_function(source,'value',tmp_path,monkeypatch)==0x1f4e6
