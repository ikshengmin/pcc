"""One-byte Rust literals use unsigned i8 storage; no FFI or slice shortcut."""
import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import lex


@pytest.mark.parametrize(('literal','value'), [
    ("b'A'", 65), ("b' '", 32), ("b'\"'", 34), ("b'\x00'", 0),
    (r"b'\x00'", 0), (r"b'\x7f'", 127), (r"b'\x80'", 128), (r"b'\xff'", 255),
    (r"b'\n'", 10), (r"b'\r'", 13), (r"b'\t'", 9), (r"b'\0'", 0),
    (r"b'\\'",92), (r"b'\''", 39), ("b'\\\"'", 34),
])
def test_byte_token_has_the_original_span_and_one_unsigned_value(literal, value):
    token, eof = lex(' \n  '+literal)
    assert (token.kind,token.text,token.literal_value) == ('byte',literal,value)
    assert (token.line,token.column,token.source_start,token.source_end) == (2,3,4,4+len(literal))
    assert eof.kind == 'eof'
    assert compile_rust('fn code()->u8 {'+literal+'}').emit_object().startswith(b'\x7fELF')


@pytest.mark.parametrize('literal', ["b''", "b'", "b'A", "b'AB'", "b'''", "b'é'", "b'📦'",
    "b'\r'", "b'\n'", "b'\t'", r"b'\x0'", r"b'\xfg'", r"b'\x001'", r"b'\u{41}'",
    r"b'\q'", "b'A'foo", "b'\udccc'", r"b'\xAA", "b'\\\n'",
])
def test_invalid_or_unimplemented_byte_tokens_are_located_before_ir(literal):
    with pytest.raises(RustError,match=r'2:3: .*byte'):
        lex('\n  '+literal)


@pytest.mark.parametrize('source', [
    "pub fn f()->u8{256}","pub fn f()->u64{256u8 as u64}","fn f()->u8{-1u8}",
    "fn f()->u8{'A'}","fn f()->char{b'A'}","fn f()->char{65u32 as char}",
    "fn f()->bool{b'a'=='a'}","fn f()->u32{b'a'}", "fn f()->u8{b'a' + 1u32}",
    "const BAD:u8=255u8+1u8; fn f()->u8{BAD}",
    "fn f()->u8{1u8 << 8}","fn f()->bool{1u8 as bool}",
    "extern \"C\" fn f(x:u8)->u32{x as u32}","extern \"C\" fn f()->u8{1}",
    "#[repr(C)] struct R{x:u8} extern \"C\" fn f(x:&R)->u64{x.x as u64}",
    "struct u8 {k:u32} fn f()->u8{b'a'}", "trait u8{} fn f()->u8{b'a'}",
    "fn f<u8>()->u8{b'a'}", "mod m{pub struct u8{pub field:u32}} use m::u8; fn f()->u8{b'a'}",
])
def test_width_shadowed_names_and_unqualified_ffi_stay_fail_closed(source):
    with pytest.raises(RustError):
        compile_rust(source)


PROGRAM = r'''const BYTE:u8=b'\xff';
#[derive(Clone, Copy, PartialEq)] struct Byte { n:u8 }
fn wrap(n:u8,other:u8)->u8{n+other}
fn match_byte(n:u8)->u8{match n {b'\xff'=>b'*',b'\x00'=>b'!',_=>b'?'}}
fn high(n:u8)->bool{n>b'\x7f'}
pub fn answer()->u64{
    let s=Byte{n:BYTE}; let copy=s;
    let chars=[b'*', 'Ī' as u8];
    if s==copy && high(s.n) && !high(b'\x7f') && chars[0]==chars[1] && (copy.n as char)=='ÿ'{
        ((wrap(250,17) as u64)<<32) | ((match_byte(s.n) as u64)<<16) |
        ((match_byte(b'\0') as u64)<<8) | match_byte(7u8) as u64
    }else{0}
}'''
EXPECTED = (11<<32)|(42<<16)|(33<<8)|63


def test_ordinary_nominal_and_generic_u8_shadow_the_type_but_never_numeric_literals():
    compile_rust("struct u8 {n:u32} mod inner{pub fn a()->u8{b'x'}} fn f(x:u8)->u32{x.n}")
    compile_rust("struct u8 {n:u32} fn f()->u32{ let a=u8{n:2}; a.n+b'a' as u32+1u8 as u32 }")
    compile_rust('fn pick<u8>(value:u8)->u8{value} pub fn go()->u64{pick::<u64>(42u64)}')


@pytest.mark.parametrize('declarations', [
    'mod u8 {pub fn show()->u64{1}}',
    'mod other {pub fn show()->u64{1}} use other as u8;',
    'extern crate self as u8; pub fn show()->u64{1}',
])
def test_module_path_and_bare_byte_type_resolve_in_their_respective_contexts(declarations):
    source = declarations + " fn byte(x:u8)->u8{x} pub fn go()->u64{u8::show()+byte(b'A') as u64}"
    assert compile_rust(source).emit_object().startswith(b'\x7fELF')


def test_bare_byte_fallback_never_bypasses_qualified_module_visibility():
    with pytest.raises(RustError, match='private'):
        compile_rust("mod u8{fn private()->u64{1}} fn f(x:u8)->u64{u8::private()+x as u64}")


def test_program_emits_real_i8_and_cross_target_objects():
    for target, magic in [('x86_64-unknown-linux-gnu',b'\x7fELF'),('arm64-apple-darwin',b'\xcf\xfa\xed\xfe')]:
        unit=compile_rust(PROGRAM,target_triple=target)
        assert 'add i8' in unit.ir_text and 'icmp ugt i8' in unit.ir_text
        assert 'zext i8' in unit.ir_text and unit.emit_object().startswith(magic)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
def test_real_byte_loads_branches_wrap_casts_and_patterns_on_host(tmp_path, monkeypatch):
    from tests.rust.str_execution import HOST_TARGET,UNAVAILABLE,run_owned_u64_function
    if HOST_TARGET is None: pytest.skip(UNAVAILABLE)
    assert run_owned_u64_function(PROGRAM,'answer',tmp_path,monkeypatch,
                                  experimental_rustc_checked=False) == EXPECTED


def test_match_all_and_all_but_one_byte_are_distinct_finite_domains():
    # Match an exhaustive set of values, not merely the presence of 256 arms.
    arms = [f"{n}u8=>{n}u64" for n in range(256)]
    def code(terms):
        return "pub fn f(x:u8)->u64{match x{" + ",".join(terms) + "}}"
    compile_rust(code(arms))
    with pytest.raises(RustError, match='non-exhaustive'):
        compile_rust(code(arms[:-1] + [arms[0]]))


def test_default_enum_discriminants_do_not_admit_typed_byte_expressions():
    for literal in ("1u8", "b'A'"):
        with pytest.raises(RustError, match='unsuffixed integer'):
            compile_rust("enum E{A="+literal+"} fn f(){}")
