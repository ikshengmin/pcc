"""Named generic function types are not generic records or captured closures."""
import hashlib
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from tests.go.owned_execution import (Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact)
from tests.go.test_execution import forbid_external_compiler

UPSTREAM = Path(__file__).parent / 'upstream/go1.27.3/src/iter/iter.go'
SEQ = 'type Seq[V any] func(yield func(V) bool)'
SEQ2 = 'type Seq2[K, V any] func(yield func(K, V) bool)'
BOTH = SEQ+'\n'+SEQ2
PROGRAM = '''package main
'''+BOTH+'''
type Count = Seq[int]
var total int
var selected func(int) bool
func add(x int) bool { total += x; return x != 2 }
func wrong(x int) bool { total += 99; return false }
func supply() func(int)bool { total+=1; return selected }
func argument() int { selected=wrong; return 2 }
func count(yield func(int)bool) { if yield(34) { yield(2) } }
func run(s Count) { s(add) }
func pair(yield func(int,bool)bool){ yield(1,true) }
func addPair(x int, ok bool) bool { if ok {total+=x};return true }
func Result() int {
 var zero Seq[int]
 if nil != zero { return -1 }
 var s Seq[int]=count
 if s==nil { return -2 }
 run(s)
 var two Seq2[int,bool]=pair
 two(addPair)
 selected=add
 if supply()(argument()) { return -3 }
 if !selected(0) { total-=97 }
 return total
}
func main(){if Result()!=42{panic("callback value")}}
'''
# count gives 36, pair gives 1, supply adds 1, argument to saved add adds
# 2, then wrong adds 99 before subtracting 97: full-width result is 42.

@pytest.mark.integration
def test_real_sequence_declarations_and_callable_code_addresses(tmp_path,monkeypatch):
    assert hashlib.sha256(UPSTREAM.read_bytes()).hexdigest()=="e868b6ef54b1c4f437444e10bc455479dfc6e936540e738016a3447bd244f249"
    raw=UPSTREAM.read_text()
    assert [line for line in raw.splitlines() if line.startswith(('type Seq[','type Seq2['))]==[SEQ,SEQ2]
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        art=compile_owned((('sequence.go',PROGRAM),),observe_result=True)
    path=tmp_path/'owned';write_artifact(art,path)
    assert run(path)==Execution(0,(42).to_bytes(8,'little'),b'')

@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_original_sequence_program_against_official_go(tmp_path,monkeypatch):
    units=(('sequence.go',PROGRAM),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);art=compile_owned(units)
    path=tmp_path/'owned';write_artifact(art,path)
    ref=compile_reference(units,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],
                          cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(path)==run(ref)==Execution(0,b'',b'')

from tests.go.test_function_literal_syntax import GRADUATED_FUNCTION_TYPES
from tests.go.test_diagnostics import GRADUATED_FUNCTION_TYPE_CASE
GRADUATED = (*GRADUATED_FUNCTION_TYPES, GRADUATED_FUNCTION_TYPE_CASE[1])

@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('text',GRADUATED)
def test_exact_former_unsupported_source_runs_on_both_owners(text,tmp_path,monkeypatch):
    inputs=(('graduate.go',text),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);art=compile_owned(inputs)
    path=tmp_path/'owned';write_artifact(art,path)
    ref=compile_reference(inputs,tmp_path/'ref',compiler=os.environ['PCC_GO_REFERENCE'],
                          cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (ref.parent/'graduate.go').read_bytes()==text.encode()
    assert run(path)==run(ref)==Execution(0,b'',b'')

INVALID = (
    ('type S[T any] func(T);type R[T any] func(T)','var s S[int];var r R[int];s=r;_=s','cannot use','cannot use'),
    ('type S[T any] func()','var x S[int];var y S[uint];x=y;_=x','cannot use','cannot use'),
    ('type S[T comparable] func(T)','var x S[func()];_=x','does not satisfy comparable','does not satisfy comparable'),
    ('type S[T any] func(T)','var x S[int,uint];_=x','wrong number of type arguments','too many type arguments'),
    ('type S[T any] func(T)','var x S;_=x','without instantiation','without instantiation'),
    ('type S func(int)','var x S;_=x==x','only be compared to nil','func can only be compared to nil'),
    ('type S func(int)','var x S;x(1,2)','argument count','too many arguments'),
    ('type S func(int)','var x S;x(true)','cannot use','cannot use'),
    ('type S func(x int)(x bool)','','duplicate parameter name','x redeclared'),
    ('type S int','var x S[int];_=x','requires a generic type','is not a generic type'),
    ('type S[T any] func(T);type A=S[int];func (A) M(){}','','alias to an instantiated generic type','cannot define'),
    ('func named(x int)int{return x}','named:=2;_=named(1)','cannot call non-function','cannot call'),
)
def unit(declarations,body):
    return 'package main;'+declarations+';func main(){'+body+'}'

@pytest.mark.parametrize('declarations,body,message,official',INVALID)
def test_invalid_callables_are_source_errors_not_unsupported(declarations,body,message,official,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid function value reached IR'))
    with pytest.raises(GoDiagnostic,match=message) as caught:compile_go(unit(declarations,body))
    assert type(caught.value) is GoDiagnostic

@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,body,message,official',INVALID)
def test_reference_rejects_identical_invalid_sources(declarations,body,message,official,tmp_path):
    compile_reference_rejection((('bad.go',unit(declarations,body)),),tmp_path/'ref',
              compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'],
              diagnostics=(official.encode(),))

BOUNDARIES = (
    ('type S[V any] func(V)','var x S[[]byte];_=x','slice-storage'),
    ('type S func(*int)','var x S;_=x','pointer-signatures'),
    ('type S func() *int','var x S;_=x','pointer-signatures'),
    ('type S func()(int,int)','var x S;_,_=x()','function-values'),
    ('func f(){}','z:=1;g:=func()int{return z};_=g','function-values'),
    ('type S[T any] struct{v T}','','generic-boundaries'),
    ('type S[T any] func(T);func (s S[U]) M(x U){}','','generic-boundaries'),
    ('type S[T any]=func(T)','','generic-boundaries'),
    ('func f(){}','var s func();defer s()','defer'),
    ('type S func(S)','var s S;_=s','function-values'),
    ('func named(){};var f=named','','package-initialization'),
    ('func named(){}','var i any=named;_=i','interface-types'),
    ('type S[T any] func(T)','var s S[int];_= &s','function-values'),
)
@pytest.mark.parametrize('declarations,body,code',BOUNDARIES)
def test_closed_runtime_and_generic_boundaries(declarations,body,code,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unqualified representation reached IR'))
    with pytest.raises(UnsupportedGo) as caught:compile_go(unit(declarations,body))
    assert caught.value.code=='go.unsupported.'+code

NIL = '''package main
var f func(int)
func callee() func(int){println("callee");return f}
func arg() int {println("argument");return 3}
func main(){callee()(arg());println("continued")}
'''
@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_nil_calls_evaluate_callee_and_args_before_uncaught_runtime_error(tmp_path,monkeypatch):
    units=(('nilcall.go',NIL),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);art=compile_owned(units)
    path=tmp_path/'owned';write_artifact(art,path)
    ref=compile_reference(units,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],
                          cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    prefix=b'callee\nargument\npanic: runtime error: invalid memory address or nil pointer dereference\n'
    assert run(path)==Execution(2,b'',prefix)
    official=run(ref)
    assert official.returncode==2 and official.stdout==b'' and official.stderr.startswith(prefix)
    assert b'continued' not in official.stderr

PACKAGES={
    'seqs': (('seq.go','package seqs\n'+BOTH+'''
func Identity[V any](s Seq[V]) Seq[V] {return s}
func Emit(k func(int)bool){ k(42) }
'''),),
    'main': (('main.go','''package main
import q "seqs"
var result int
func save(x int)bool{result=x;return true}
func Result()int{
 var s q.Seq[int]=q.Emit
 s=q.Identity(s)
 s=q.Identity(q.Emit)
 var other=q.Seq[int](q.Emit)
 s(save)
 if other==nil{return 0}
 return result
}
func main(){if Result()!=42{panic("imported Seq")}}
'''),)}
@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_cross_package_template_and_specialization_own_the_same_signature(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        module=compile_go_packages(PACKAGES)
        value=compile_module(module,observe_result=True)
        main=compile_module(module)
    observed=tmp_path/'observed';write_artifact(value,observed)
    assert run(observed)==Execution(0,(42).to_bytes(8,'little'),b'')
    path=tmp_path/'owned';write_artifact(main,path)
    ref=compile_reference_packages(PACKAGES,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],
                                   cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(path)==run(ref)==Execution(0,b'',b'')

def test_the_entire_original_bytes_iter_file_keeps_all_applied_results_and_capturing_body():
    from pcc.frontends.go.syntax import GoParser, SliceSyntax, TypeInstantiationSyntax
    filename=UPSTREAM.parent.parent/'bytes/iter.go'
    raw=filename.read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='628f8dc51af2c70325050c0125340ec01c6a7da84c53f2f5ae3ec040c7e5fc6f'
    unit=GoParser().parse(raw.decode(), 'bytes/iter.go', require_main=False)
    assert [f.token.text for f in unit.functions]==['Lines','splitSeq','SplitSeq','SplitAfterSeq','FieldsSeq','FieldsFuncSeq']
    for f in unit.functions:
        sig=f.result
        assert isinstance(sig,TypeInstantiationSyntax) and (sig.base.qualifier,sig.base.token.text)==('iter','Seq')
        assert len(sig.arguments)==1 and isinstance(sig.arguments[0],SliceSyntax) and sig.arguments[0].element=='uint8'
    first=unit.functions[0].body[0].exprs[0]
    assert first.kind=='func_literal' and first.value.body[0].kind=='for'

def test_unavailable_nonnative_panic_owner_is_never_replaced_with_null_call():
    with pytest.raises(UnsupportedGo) as exc:
        compile_go('package main;func main(){var f func();f()}','nil.go',target_triple='arm64-apple-darwin')
    assert exc.value.code=='go.unsupported.builtin-io'

def test_bounded_nested_type_application_precedes_host_recursive_expansion():
    with pytest.raises(UnsupportedGo) as exc:
        compile_go('package main;type P[T any] func();var v '+'P['*33+'int'+']'*33+';func main(){}')
    assert exc.value.code=='go.unsupported.limit-type-expansion'

def test_nominal_identity_budget_is_shared_by_distinct_generic_declarations():
    specs=';'.join('type T'+str(i)+'[A any] func();var x'+str(i)+' T'+str(i)+'[int]' for i in range(129))
    with pytest.raises(UnsupportedGo) as exc:compile_go(unit(specs,''))
    assert exc.value.code=='go.unsupported.limit-generic-instances'

def test_hidden_metadata_arguments_do_not_alias_in_generic_symbol_names(monkeypatch):
    forbid_external_compiler(monkeypatch)
    source=unit('type P[T any] func();func id[T any](x T)T{return x}',
       'var a P[*int];var b P[[]byte];a=id(a);b=id(b);_,_=a,b')
    module=compile_go(source)
    symbols={f.name for f in module.functions if '_instance_' in f.name}
    assert len(symbols)==2

def test_pointer_metadata_substitution_is_exact_without_admitting_pointer_calls():
    from pcc.frontends.go.callables import callable_signature
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    from pcc.frontends.go.types import PointerType
    a=Analyzer(GoParser().parse(unit('type S[T any] func(*T);type A=S[int];type B=S[uint]','')))
    a.analyze()
    assert callable_signature(a.types['A'].resolved).parameters==(PointerType('int'),)
    assert callable_signature(a.types['B'].resolved).parameters==(PointerType('uint'),)

MIXED = '''package main
func mix(a int8,b uint64,c float32,d bool,e float64,f int16,g uint8,h uint64,i int,j int,k int)int {
 if !d || c!=3.5 || e!=4.25 {return 0}
 return int(a)+int(b)+int(f)+int(g)+int(h)+i+j+k
}
func Result()int{f:=mix;return f(-2,5,3.5,true,4.25,3,4,7,8,9,8)}
func main(){if Result()!=42{panic("indirect argument transport")}}
'''
@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_indirect_mixed_scalar_registers_and_stack_arguments(tmp_path,monkeypatch):
    inputs=(('mixed.go',MIXED),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        observed=compile_owned(inputs,observe_result=True);main=compile_owned(inputs)
    path=tmp_path/'observed';write_artifact(observed,path)
    assert run(path)==Execution(0,(42).to_bytes(8,'little'),b'')
    path=tmp_path/'owned';write_artifact(main,path)
    ref=compile_reference(inputs,tmp_path/'ref',compiler=os.environ['PCC_GO_REFERENCE'],
                          cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(path)==run(ref)==Execution(0,b'',b'')
