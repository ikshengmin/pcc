"""Lexical type scopes, not a package type table keyed by a bare spelling."""
import hashlib
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_blank_types import GRADUATED_LOCAL_BLANK
from tests.go.test_diagnostics import GRADUATED_LOCAL_TYPE_CASE
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_records import GRADUATED_LOCAL_TYPE, source as record_source


UPSTREAM = Path(__file__).parent / 'upstream/go1.27.3/test/fixedbugs/bug167.go'
UPSTREAM_SHA = 'f369705a4bf4a26779be67b80d63c07e37a8169ff943baac5df24e88973328d9'
UPSTREAM_BLOB = '3a50e6ff0c12730e398601eb470971b24496af00'
GRADUATED = (
    GRADUATED_LOCAL_TYPE_CASE[1],
    record_source(*GRADUATED_LOCAL_TYPE[:2]),
    GRADUATED_LOCAL_BLANK,
)
PROGRAM = '''package main
type outer uint64
func (x outer) Val() int { return int(x) }
func id[T any](x T) T { return x }
func first() int { type N int8; return int(id[N](17)) }
func second() int { type N int64; return int(id(N(9))) }
func Result() int {
    type span struct { start int; end int }
    type (same = span; distinct span)
    v:=same{start:2,end:5}
    copied:=id(v)
    v.start=7
    if copied.start!=2 || distinct(copied).end!=5 { return 1 }
    total:=first()+second()
    { type N int16; total+=int(id[N](2)) }
    { type N uint32; total+=int(id[N](3)) }
    type ancestor = outer
    { type outer struct { n int }; total+=outer{int(ancestor(4).Val())}.n }
    { type _ int; type _ = span }
    goto valid
    type A int
valid: total+=int(A(3))
    switch total { case 38: type Q int; total+=int(Q(4)); default: type Q int8; total=int(Q(0)) }
    return total
}
func main(){if Result()!=42{println("bad result")}}
'''
ACCEPT = (
    'type (A int; B = A; C B);var x B=1;_=C(x)',
    'type ();type _ struct{};type _=int',
    'type T int;{T:=1;_=T};_=T(2)',
    'type Q int;{const Q=2;var a [Q]int;_=a};var q Q;_=q',
    'type A struct{X int};type B=A;var a A;var b B=a;_=b',
    'goto X; type A int; X: type B A;var b B;_=b',
    'type I interface{M() I};type _=I',
)
# Kept separate from implementation limits: these originals are invalid Go.
INVALID = (
    ('var x T;type T int;_=x','undefined type T','undefined: T'),
    ('{type T int};var x T;_=x','undefined type T','undefined: T'),
    ('type (A B; B int)','undefined type B','undefined: B'),
    ('type A A','recursive type','invalid recursive type'),
    ('type A=A','recursive type','invalid recursive type'),
    ('type A struct{x A}','recursive type','invalid recursive type'),
    ('type A int;type A int','redeclaration','redeclared'),
    ('type A int;var A int;_=A','redeclaration','redeclared'),
    ('var A int;type A int;_=A','redeclaration','redeclared'),
    ('type A int;_=A','type is not an expression','is not an expression'),
    ('type A int;A,v:=1,2;_,_=A,v','type is not an expression','cannot assign to A'),
    ('type A int;{A:=1;_=A;var x A;_=x}','not a type','A (local variable) is not a type'),
    ('type A int;var out A;{type A int; var in A;out=in};_=out','cannot use','cannot use'),
    ('type _ Missing','undefined type Missing','undefined: Missing'),
    ('goto L;type A int;var x A;L:_=x','jumps over','jumps over declaration'),
)
BOUNDARIES = (
    ('func F[T any](){type X struct{}}','go.unsupported.local-type-declarations'),
    ('func F(){type X []int}','go.unsupported.slice-storage'),
    ('func F(){type X struct{p *int}}','go.unsupported.pointer-storage'),
    ('func F(){type max int}','go.unsupported.predeclared-shadowing'),
)


def unit(body):
    return 'package main;func main(){'+body+'}'


def test_original_upstream_and_all_previously_unimplemented_programs_keep_their_bytes():
    data=UPSTREAM.read_bytes()
    assert hashlib.sha256(data).hexdigest()==UPSTREAM_SHA
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==UPSTREAM_BLOB
    assert GRADUATED==('package main;\n\nfunc main(){type T int}\n',
                      'package main;func main(){type A int}', 'package main;func main(){type _ int}')


@pytest.mark.parametrize('body',ACCEPT)
def test_grouped_declarations_use_the_current_ordinary_block(body,monkeypatch):
    forbid_external_compiler(monkeypatch)
    assert 'define' in str(compile_go(unit(body)))


@pytest.mark.parametrize('body,message,reference',INVALID)
def test_invalid_lexical_declarations_fail_before_ir(body,message,reference,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *args:pytest.fail('invalid lexical type reached IR'))
    with pytest.raises(GoDiagnostic,match=message) as caught:compile_go(unit(body))
    assert type(caught.value) is GoDiagnostic


@pytest.mark.parametrize('decl,code',BOUNDARIES)
def test_checked_rhs_and_generic_owner_frontiers_are_not_runtime_support(decl,code):
    with pytest.raises(UnsupportedGo) as caught:compile_go('package main;'+decl+';func main(){}')
    assert caught.value.code==code


def test_import_name_and_type_qualifier_obey_the_lexical_cut(monkeypatch):
    forbid_external_compiler(monkeypatch)
    pkgs={'main':[('main.go','package main;import q "p";func main(){type Before=q.T;type q struct{n int};_=q{int(Before(1))}.n}')],
          'p':[('p.go','package p;type T int')]}
    assert 'define' in str(compile_go_packages(pkgs))
    pkgs['main']=[('main.go','package main;import q "p";func main(){type q int;type X=q.T}')]
    with pytest.raises(GoDiagnostic,match='shadowed'):compile_go_packages(pkgs)


@pytest.mark.integration
def test_owned_native_uses_distinct_generic_symbols_and_returns_full_width_result(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);art=compile_owned((('scope.go',PROGRAM),),observe_result=True)
    binary=tmp_path/'owned';write_artifact(art,binary)
    assert run(binary)==Execution(0,(42).to_bytes(8,'little'),b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,text',tuple(('graduated-'+str(i)+'.go',s) for i,s in enumerate(GRADUATED))+
                         (('bug167.go',UPSTREAM.read_text()),('scope.go',PROGRAM)))
def test_identical_upstream_graduated_and_scoped_programs_run_both_owners(name,text,tmp_path,monkeypatch):
    inputs=((name,text),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);art=compile_owned(inputs)
    path=tmp_path/'owned';write_artifact(art,path)
    ref=compile_reference(inputs,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],
                          cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (ref.parent/name).read_bytes()==text.encode()
    assert run(path)==run(ref)==Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,message,reference',INVALID)
def test_invalid_declarations_have_normal_source_attributed_official_rejections(body,message,reference,tmp_path):
    compile_reference_rejection((('lexical.go',unit(body)),),tmp_path/'ref',compiler=os.environ['PCC_GO_REFERENCE'],
                                cache=os.environ['PCC_GO_REFERENCE_CACHE'],diagnostics=(reference.encode(),))
