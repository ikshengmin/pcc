"""PCC's literal match reads the UTF-8 contents of a shared str, not address identity.

A guard can fall through and a finite list of literal strings is never exhaustive.
This is not a primitive std::str method, String, general PartialEq or match ergonomics.
"""
import platform
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import HOST_TARGET, UNAVAILABLE, run_owned_u64_function
ROOT='pub fn arch(s:&str)->u64{match s{"x64"|"x86_64"=>1,"arm64"|"aarch64"=>2,"arm64ec"=>3,"x86"|"i686"|"i586"=>4,"arm"|"thumbv7a"=>5,_=>0}}'
TEXT=r'''pub fn inspect(s:&str)->u64 { match s {
 ""=>6,"a\0b"=>7,"é"=>8,"e\u{301}"=>9,r#"a\nb"#=>10,"😀"=>11,
 "same" if false=>27, "same"=>12, _=>13
}}'''
FORMER='pub fn g() -> u64 { match "a" { "a" => 1, _ => 2 } }'
CASES=[(ROOT,'ok'),(TEXT,'ok'),(FORMER,'ok'),('pub fn s()->u64{match "apple"{"apple"=>1}}','non-exhaustive'),
       ('pub fn s(x:&str)->u64{match x{""=>"a".len() as u64,_=>42}}','ok'),
       ('pub fn s(x:&u64)->u64{match x{"x"=>1,_=>2}}','shared str slice'),
       ('pub fn s(x:u64)->u64{match x{"x"=>1,_=>2}}','shared str slice'),
       ('pub fn s(x:&str)->u64{match x{"a" if true=>1,"z"=>2}}','non-exhaustive'),
       ('pub fn s(x:&str)->u64{match x{_ if true=>1,"z"=>2}}','non-exhaustive'),
       ('pub fn s(x:&str)->u64{match x{""=>1,_=>2}}','ok'),
       ('pub fn s(x:(&str,bool))->u64{match x{(_,true)=>1,("x",false)=>2}}','aggregate reference storage'),
       ("pub fn s(x:(&'static str,bool))->u64{match x{(_,true)=>1,(\"x\",false)=>2}}",'non-exhaustive'),
       ('pub fn s(x:(&str,bool))->u64{match x{(_,true)=>1,("x",false)=>2,(_,false)=>3}}','aggregate reference storage'),
       ("pub fn s(x:(&'static str,bool))->u64{match x{(_,true)=>1,(\"x\",false)=>2,(_,false)=>3}}",'ok'),
       ('pub fn s(x:&str)->u64{if let "x" = x { 1 } else { 2 }}','ok'),
       ('struct str{p:u64}pub fn s(x:&str)->u64{match x{"x"=>1,_=>0}}','shared str slice')]

SINGLE='fn next(n:&mut u64)->&\'static str{*n+=1;"abc"}pub fn once()->u64{let mut n=0; let v=match next(&mut n){"cd"|"de"=>1,"abc"=>2,_=>3};n*10+v}'
CASES.extend([
 (SINGLE,'ok'),
 ('pub fn guard(mut value:&\'static str)->u64{match value{"a" if {value="b";false}=>1,_=>2}}','E0510'),
 ('pub fn guard(mut value:&\'static str)->u64{match value{_ if {value="b";false}=>1,_=>2}}','ok'),
])

WILD='pub fn change(mut value:&\'static str)->usize{match value{_ if {value="changed";false}=>0,_=>value.len()}}pub fn observe()->u64{change("old") as u64}'
CASES.append((WILD, 'ok'))


@pytest.mark.parametrize('source,expected',CASES)
def test_literal_content_is_typed_and_preserves_a_real_default(source,expected,monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kwargs:pytest.fail('external tool in owned phase'))
    if expected=='ok':
        result=compile_rust(source,target_triple='x86_64-unknown-linux-gnu')
        assert result.emit_object()[:4]==b'\x7fELF'
    else:
        with pytest.raises(RustError,match=expected):
            compile_rust(source,target_triple='x86_64-unknown-linux-gnu')


@pytest.mark.parametrize('target,magic', [('arm64-apple-darwin',b'\xcf\xfa\xed\xfe'),
                                         ('x86_64-unknown-linux-gnu',b'\x7fELF')])
def test_literal_pattern_emits_a_real_target_object(target,magic):
    c=compile_rust(TEXT,target_triple=target)
    assert c.emit_object().startswith(magic)
    assert 'str.match.byte' in c.ir_text and 'load i8' in c.ir_text


def test_long_literal_uses_one_loop_not_unbounded_instruction_expansion():
    source='pub fn large(x:&str)->bool{match x{"'+'X'*4096+'"=>true,_=>false}}'
    c=compile_rust(source,target_triple='x86_64-unknown-linux-gnu')
    assert c.emit_object().startswith(b'\x7fELF')
    assert c.ir_text.count('load i8,')==2


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
@pytest.mark.xdist_group(name='rust-native-reference')
def test_original_accepted_literal_and_unequal_guard_have_owned_native_results(tmp_path,monkeypatch):
    assert run_owned_u64_function(FORMER,'g',tmp_path,monkeypatch,target_triple=HOST_TARGET)==1
    assert run_owned_u64_function(SINGLE,'once',tmp_path,monkeypatch,target_triple=HOST_TARGET)==12
    assert run_owned_u64_function(WILD,'observe',tmp_path,monkeypatch,target_triple=HOST_TARGET)==7
    source=ROOT+TEXT+'pub fn result()->u64{arch("x86_64")+arch("aarch64")+inspect("")+inspect("same")+inspect("other")+inspect("é")}'
    assert run_owned_u64_function(source,'result',tmp_path,monkeypatch,target_triple=HOST_TARGET)==42


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
                     else 'executes a Linux x86-64 ELF')
def test_nonstatic_external_buffers_match_only_their_exact_bytes(tmp_path,monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable,parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    # Read a fresh buffer at an address distinct from every reference literal.
    # Argument zero is the actual stack pointer and argument one its exact
    # length. No zero termination, length discovery, allocation or libc.
    with monkeypatch.context() as owned:
        owned.setattr(subprocess,'Popen',lambda *a,**kwargs:pytest.fail('external tool in owned compile/link'))
        c=compile_rust(TEXT,target_triple='x86_64-unknown-linux-gnu')
        asm=assemble_file(f""".intel_syntax noprefix
.text
.globl _start
_start:
 sub rsp, 64
 xor eax, eax
 xor edi, edi
 mov rsi, rsp
 mov edx, 64
 syscall
 mov rsi, rax
 mov rdi, rsp
 call {c.symbols['inspect']}
 mov rdi, rax
 mov eax, 60
 syscall
""")
        image=link_static_executable([parse_relocatable(c.emit_object()),asm])
    exe=tmp_path/'owned-contents';exe.write_bytes(image);exe.chmod(0o700)
    for raw,expected in [(b'',6),(b'a\0b',7),(b'a\0x',13),(b'a\0',13),('é'.encode(),8),
        ('e\u0301'.encode(),9),(b'a\\nb',10),('😀'.encode(),11),(b'same',12),
        (b'samX',13),(b'sameX',13),(b'z'*64,13)]:
        r=subprocess.run([str(exe)],input=raw,capture_output=True,timeout=5)
        assert (r.returncode,r.stdout,r.stderr)==(expected,b'',b''),raw
