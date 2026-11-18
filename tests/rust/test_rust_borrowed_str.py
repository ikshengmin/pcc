"""Nonsized str is never a value or a one-word pointer. Shared string slices
borrow their bytes for a checked region and carry the byte count, including
embedded NULs. usize here follows the selected 64-bit Rust target.
"""
import platform
import shutil
import subprocess
import sys
import pytest
from pcc.frontends.rust import RustError,compile_rust
from tests.rust.str_execution import UNAVAILABLE,run_owned_str_function,run_owned_u64_function,run_rustc_str_function

PROGRAM = r'''
fn identity<'a>(s: &'a str) -> &'a str { s }
fn count(s: &str) -> usize { s.len() }
fn empty(s: &str) -> bool { s.is_empty() }
fn literal<'a>() -> &'a str { "a\0é𐀀" }
static mut N: u64 = 0;
fn active() -> &'static str {unsafe { N = N + 1; } "é" }
pub fn lengths() -> u64 {
    let s: &str = "é";
    let mut changed: &str = "a";
    changed = "abc";
    let measured = active().len();
    let all = count(identity(s)) + count(identity(literal())) + count(changed) + measured;
    if empty(identity("")) && !empty(s) { (all as u64) + unsafe { N } * 256 } else {0}
}
pub fn bytes<'a>() -> &'a str { identity(literal()) }
'''
@pytest.mark.parametrize('source,fragment',[
    ("fn f(x: &str) -> &'static str {x}",'mismatch'),
    ("fn f(x:&str,y:&str)->&str{x}",'missing lifetime specifier'),
    ("fn f<'a,'b>(x:&'a str,y:&'b str)->&'a str{y}",'lifetime'),
    ("fn f(x: &'bad str){}",'undeclared lifetime'),
    ('fn f(x: &mut str){}','mutable str references'),
    ('fn f(x:str){}','unsupported Rust type'),
    ('fn f(x:&str){*x;}','dereferencing an unsized str'),
    ('extern "C" fn f(x:&str){}','foreign shared references'),
    ('struct X{s:&str}', 'lifetime'),
    ('fn f(x:&str)->usize{x.len(7usize)}','needs no generic or value arguments'),
    ('fn f(x:&str)->usize{x.len::<i32>()}','needs no generic or value arguments'),
    ('fn f(x:&str)->bool{x==x}', 'operator'),
    ('fn f(x:&str)->usize{x as usize}', 'supported casts'),
    ('struct str{x:usize} fn f(s:&str)->usize{s.len()}', 'method'),
    ("struct str{x:usize} fn f()->&'static str{\"wrong\"}",'mismatch'),
    ('fn f<str>(x:&str)->usize{x.len()}','method'),
    ('trait str{} fn f(x:&str)->usize{x.len()}', 'unsupported Rust type'),
    ("fn f<'a>(x:&'a str){let y:&'static str=x;}",'mismatch'),
    ("fn f(x:&str){let mut y:&'static str=\"abc\";y=x;}",'mismatch'),
    ("struct str{x:i32} const S:&str=\"wrong\";",'items of type'),
])
def test_rejects_invalid_lifetimes_unsized_access_and_abi(source,fragment):
    with pytest.raises(RustError,match=fragment): compile_rust(source)

@pytest.mark.parametrize("source",[
    'fn take<T>(s:&str,x:T)->usize{s.len()} pub fn n()->usize{take("日本",1u64)}',
    'struct str{n:usize} fn q(x:&str)->usize{x.n}',
    'struct str{n:i32} mod inner{pub fn n(x:&str)->usize{x.len()}}',
    'struct str{n:usize} mod inner{use super::str;pub fn n(x:&str)->usize{x.n}}',
    "struct View<'a>{data:&'a str} fn id<'a>(s:&'a str)->View<'a>{View{data:s}}",
])
def test_ordinary_type_namespace_and_borrowed_layout(source):
    assert compile_rust(source).emit_object()

@pytest.mark.parametrize('target,magic',[('x86_64-unknown-linux-gnu',b'\x7fELF'),('arm64-apple-darwin',b'\xcf\xfa\xed\xfe')])
def test_scalar_pair_and_nonempty_object(target,magic):
    result=compile_rust(PROGRAM,target_triple=target)
    assert '{ ptr, i64 }' in result.ir_text and 'extractvalue' in result.ir_text
    assert result.emit_object().startswith(magic)

@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_owned_exact_byte_length_with_nul_multibyte_empty_and_receiver_effect(tmp_path,monkeypatch):
    assert run_owned_u64_function(PROGRAM,'lengths',tmp_path,monkeypatch)==271
    assert run_owned_str_function(PROGRAM,'bytes',tmp_path,monkeypatch)=='a\0é𐀀'.encode()

@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_real_rustc_exact_byte_length_with_same_inputs(tmp_path):
    assert int.from_bytes(run_rustc_str_function(PROGRAM,'lengths',tmp_path,output='&{call}.to_le_bytes()'),'little')==271
    assert run_rustc_str_function(PROGRAM,'bytes',tmp_path)=='a\0é𐀀'.encode()

@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
                     else 'executes an x86-64 Linux ELF')
def test_nonstatic_caller_stack_bytes_and_both_borrowed_result_words(tmp_path,monkeypatch):
    """The assembly caller supplies stdin bytes at a changing stack address, not a literal."""
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    source = "pub fn carry<'a>(x:&'a str)->&'a str{x} pub fn bytes(x:&str)->usize{x.len()}"
    with monkeypatch.context() as owned:
        owned.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('owned compile/link used another process'))
        unit = compile_rust(source,target_triple='x86_64-unknown-linux-gnu')
        driver = assemble_file(f""".intel_syntax noprefix
.text
.globl _start
_start:
 sub rsp, 64
 mov r12, rsp
 xor eax, eax
 xor edi, edi
 mov rsi, r12
 mov edx, 64
 syscall
 mov r13, rax
 mov rdi, r12
 mov rsi, r13
 call {unit.symbols['carry']}
 cmp rax, r12
 jne bad
 cmp rdx, r13
 jne bad
 mov rdi, rax
 mov rsi, rdx
 call {unit.symbols['bytes']}
 cmp rax, r13
 jne bad
 mov rdx, rax
 mov rsi, r12
 mov eax, 1
 mov edi, 1
 syscall
 xor edi, edi
 mov eax, 60
 syscall
bad:
 mov edi, 57
 mov eax, 60
 syscall
""")
        image=link_static_executable([parse_relocatable(unit.emit_object()),driver])
    binary=tmp_path/'stack-str'
    binary.write_bytes(image);binary.chmod(0o700)
    for raw in (b'','a\0é𐀀'.encode(),b'x'*64):
        result=subprocess.run([str(binary)],input=raw,capture_output=True,timeout=5)
        assert (result.returncode,result.stdout,result.stderr)==(0,raw,b'')
