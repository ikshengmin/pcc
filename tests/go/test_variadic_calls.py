"""Direct-source variadics in the original errors.Join dependency frontier.

Execution fixtures below qualify only borrowed call storage. The complete
go-enry project test still requires the untouched standard packages and IR.
"""
import hashlib
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go,compile_go_packages
from tests.go.owned_execution import Execution,compile_module,compile_reference_packages,run,write_artifact
from tests.go.test_execution import forbid_external_compiler

LIB='''package span
type N int
func Sum(first int, xs ...N) int {
 n:=first
 for i,x:=range xs { n+=int(x)+i }
 return n
}
func Count(errs ...error)int {
 n:=0
 for _,err:=range errs { if err!=nil {n++} }
 return n
}
func Slots(xs ...N)int {
 if xs==nil { if len(xs)!=0 || cap(xs)!=0{return -200}; return -1 }
 return 10*len(xs)+cap(xs)
}
func Empty(xs ...N)int { return Slots(xs[:0]...) }
func Erase(xs ...N){clear(xs)}
func Float(xs ...float64)float64{var s float64;for _,v:=range xs{s+=v};clear(xs);return s}
func Bools(xs ...bool)bool{for _,v:=range xs{if v{return true}};return false}

func Forward(xs ...N)int {
 before:=Sum(0,xs...)
 Erase(xs...)
 return before+Sum(0,xs...)+Slots(xs...)
}
func Recur(k int,xs ...N)int {
 if k<=0{return Sum(0,xs...)}
 return Recur(k-1,xs...)
}
'''
MAIN='''package main
import s "example.org/span"
var seq int
func tick(x int)s.N{seq=seq*10+x;return s.N(x)}
func Result()int{
 a:=s.Forward(tick(1),tick(2),tick(3))
 if seq!=123 || a!=45 {return -1}
 if s.Slots()!=-1 || s.Slots(nil...)!=-1 || s.Slots(0)!=11 {return -2}
 if s.Count(nil,nil)!=0 || s.Count()!=0 {return -3}
 if s.Empty(1,2,3)!=3 || s.Empty()!=-1{return -6}
 if s.Float(1.25,2.25)!=3.5 || !s.Bools(false,true) || s.Bools(){return -7}
 if s.Recur(4,5,7)!=13 {return -4}
 for i:=0;i<3;i++ { if s.Sum(3,2,4)!=10{return -5} }
 return 42
}
func main(){if Result()!=42 {panic("incorrect variadic backing")}}
'''
PACKAGES={'example.org/span':(('span.go',LIB),),'main':(('main.go',MAIN),)}

def test_actual_join_original_declaration_is_variadic_and_unchanged():
    from pcc.frontends.go.syntax import GoParser,SliceSyntax
    path=Path(__file__).parent/'upstream/go1.27.3/src/errors/join.go'
    raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='87da7c120f71d17e37f110ae1dc6af6493ec841a22ee825e13a6a8024a014dba'
    p=GoParser().parse(raw.decode(),str(path),require_main=False)
    join,= (f for f in p.functions if f.token.text=='Join')
    assert join.variadic and len(join.params)==1
    token,t=join.params[0];assert token.text=='errs' and isinstance(t,SliceSyntax) and t.token.text=='...'
    assert [n.kind for n in join.body].count('range')==2
    assert join.body[-1].kind=='return'

def test_call_retains_span_storage_and_direct_pointer_forwarding(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module=compile_go_packages(PACKAGES)
    # In addition to the native behavioral gate check existing caller lifetime
    # boundaries cannot turn a still-unsupported heap closure into a solution.
    text=str(module)
    assert 'rest_data_' in text and 'rest_clear_element' in text
    assert 'malloc' not in text

@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_real_owned_call_frames_and_forwarded_mutation_match_original_go(tmp_path,monkeypatch):
    with monkeypatch.context() as blocked:
        forbid_external_compiler(blocked)
        m=compile_go_packages(PACKAGES)
        observed=compile_module(m,observe_result=True);owned=compile_module(m)
    result=tmp_path/'result';write_artifact(observed,result)
    assert run(result)==Execution(0,(42).to_bytes(8,'little'),b'')
    binary=tmp_path/'actual';write_artifact(owned,binary)
    ref=compile_reference_packages(PACKAGES,tmp_path/'ref',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(binary)==run(ref)==Execution(0,b'',b'')

@pytest.mark.parametrize('decl,body,reason',[
    ('func f(x ...int){}','f(1,true)','use|mismatched'),
    ('func f(a int,x ...int){}','f()','count'),
    ('func f(a, b ...int){}','','variadic parameter'),
    ('func f(x ...int,y int){}','','variadic parameter'),
    ('func f()(x ...int){return}','','variadic parameter'),
    ('func f(x ...int){}','var a [2]int;f(a...)','expansion requires'),
    ('func f(a ...int){};func g(b ...int){f(1,b...)}','','count mismatch'),
    ('func f(a ...int){};func g(b ...bool){f(b...)}','','expansion requires'),
    ('func f(a ...int){_=a==a}','','only be compared to nil'),
    ('func f(a ...int){var b=a;_=b}','','slice storage|slice identity'),
    ('func f(a ...int){_=a}','','slice storage|slice identity'),
    ('func f(a ...int){}','_=f','non-retention'),
    ('func f(a ...int){_=a[:0]}','','slice storage|slice identity'),
    ('func f(a ...int){n:=1;_=cap(a[:n])}','','backed slice'),
    ('func f(a ...int){_=a[:0:0]}','','backed slice'),
    ('func f(a ...*int){}','','variadic elements'),
    ('func f(a ...int)[]int{return a}','','slice types'),
    ('func f(a ...int){_= &a}','','slice identity|slice storage'),
    ('func f[T any](a ...T){}','','generic variadic'),
    ('type T int;func(t T) f(a ...int){}','','variadic methods'),
    ('func f(a ...any){}','','variadic elements'),
])
def test_unqualified_lifetime_storage_and_invalid_calls_stop_before_lowering(decl,body,reason,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unqualified variadic program reached machine lowering'))
    with pytest.raises(GoDiagnostic,match=reason):
        compile_go('package main;'+decl+';func main(){'+body+'}')


def test_variadic_call_storage_is_bounded_across_sites_not_only_each_argument_list(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unbounded variadic frame reached lowering'))
    call='f('+','.join(['0']*256)+');'
    for body in ('f('+','.join(['0']*257)+')',call*8):
        with pytest.raises(UnsupportedGo) as e:
            compile_go('package main;func f(v ...int){};func main(){'+body+'}')
        assert e.value.code=='go.unsupported.limit-function-arity'

@pytest.mark.parametrize('order', ['views', 'packs-first', 'views-first'])
def test_every_materialized_view_shares_the_pack_budget(order,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unbounded variadic views reached lowering'))
    view='_=len(xs[:0]);'
    pack='f('+','.join(['0']*256)+');'
    # Seven packs are 14,504 bytes on this ABI; 79 descriptors add 1,896.
    # Each category fits by itself. Interleave the variants in both orders.
    code={'views':view*257,'packs-first':pack*7+view*79,'views-first':view*79+pack*7}[order]
    with pytest.raises(UnsupportedGo) as e:
        compile_go('package main;func f(v ...int){};func g(xs ...int){'+code+'};func main(){}')
    assert e.value.code=='go.unsupported.limit-function-arity'
