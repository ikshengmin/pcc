"""Declaration binding and call-site evaluation: N4659 [dcl.fct.default].

The SetStr declaration is an unchanged line from TinyXML2 11.0.0 h:172,
Zlib license; provenance is projects/tinyxml2-11.0.0/pcc-fixture.json.
These are language originals, not a claim that the upstream project builds.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

SETSTR_DECLARATION = '    void SetStr( const char* str, int flags=0 );'

CASES = (
    ('declaration-bound-call-time-value',
     'int n=1;int f(int x=n){return x;}int cpp_test_entry(){n=42;int n=7;return f();}'),
    ('explicit-override-no-default-effect',
     'int count=0;int next(){count=count+1;return 7;}int f(int x=next()){return x;}'
     'int cpp_test_entry(){int result=f(42);if(count!=0)return 1;return result;}'),
    ('omitted-evaluated-once-per-call',
     'int count=0;int next(){count=count+1;return 21;}int f(int x=next()){return x;}'
     'int cpp_test_entry(){int a=f();int b=f();if(count!=2)return 1;return a+b;}'),
    ('two-defaults-no-relative-order-assumption',
     'int a=0;int b=0;int first(){a=a+1;return 20;}int second(){b=b+1;return 22;}'
     'int f(int x=first(),int y=second()){return x+y;}'
     'int cpp_test_entry(){int result=f();if(a!=1||b!=1)return 1;return result;}'),
    ('same-scope-union',
     'int f(int x,int y=22);int f(int x=20,int y);int f(int x,int y){return x+y;}'
     'int cpp_test_entry(){return f();}'),
    ('overload-rank-explicit-only',
     'int f(int x,int y=22){return x+y;}int f(long x){return 1;}'
     'int cpp_test_entry(){return f(20);}'),
    ('parameter-unevaluated',
     'int f(int x,int n=sizeof(x)){return n+38;}int cpp_test_entry(){return f(7);}'),
    ('class-complete-private-later-enum',
     'class S{public:int f(int n=Answer)const{return n;}private:enum{Answer=42};};'
     'int cpp_test_entry(){const S s{};const S*p=&s;return p->f();}'),
    ('nested-default-binding-unevaluated-parameter',
     'struct S{static int later(int a,int b=sizeof(a)){return b;}'
     'static int first(int n=sizeof(later(1))){return n+38;}};'
     'int cpp_test_entry(){return S::first();}'),
    ('explicit-object-member-in-unevaluated-default',
     'struct S{int m;static int f(S*p,int n=sizeof(p->m)){return n+38;}};'
     'int cpp_test_entry(){S s;return S::f(&s);}'),
    ('explicit-object-member-call-time-value',
     'struct S{int m;};S*object=nullptr;int f(int n=object->m){return n;}'
     'int cpp_test_entry(){S s{42};object=&s;return f();}'),
    ('class-static-later-function',
     'struct S{static int f(int n=answer()){return n;}static int answer(){return 42;}};'
     'int cpp_test_entry(){return S::f();}'),
    ('later-parameter-does-not-hide-outer',
     'int n=40;struct S{int f(int x=n,int n=2){return x+n;}};'
     'int cpp_test_entry(){S s;S*p=&s;return p->f();}'),
    ('receiver-before-default',
     'int count=0;int argument(){if(count!=1)return 1;count=2;return 42;}'
     'struct S{static int f(int n=argument()){return n;}};'
     'S* receiver(S*p){count=1;return p;}'
     'int cpp_test_entry(){S s;int n=receiver(&s)->f();if(count!=2)return 2;return n;}'),
    ('nonstatic-receiver-before-default',
     'int count=0;int argument(){if(count!=1)return 1;count=2;return 42;}'
     'struct S{int f(int n=argument()){return n;}};'
     'S* receiver(S*p){count=count+1;return p;}'
     'int cpp_test_entry(){S s;int n=receiver(&s)->f();if(count!=2)return 2;return n;}'),
    ('nominal-class-pointer-default',
     'struct S{int n;};S*g=nullptr;int f(S*p=g){return p->n;}'
     'int cpp_test_entry(){S s{42};g=&s;return f();}'),
    ('nominal-enum-default',
     'enum E{Answer=42};E value=Answer;int f(E n=value){return n;}'
     'int cpp_test_entry(){return f();}'),
    ('pointer-default-binding',
     'int n=42;int* p=&n;int f(int* value=p){return *value;}'
     'int cpp_test_entry(){int n=7;int*p=&n;return f();}'),
    ('null-default',
     'int f(int*p=nullptr){if(p==nullptr)return 42;return 1;}int cpp_test_entry(){return f();}'),
    ('tinyxml-original-setstr-declaration',
     'class StrPair{public:'+SETSTR_DECLARATION+'int value;};'
     'void StrPair::SetStr(const char*str,int flags){value=flags+42;}'
     'int cpp_test_entry(){StrPair s;StrPair*p=&s;p->SetStr(nullptr);return s.value;}'),
)
ORIGINAL_INLINE_DECLARATION = 'struct S{int f(int n=42){return n;}};'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name,source in CASES) + (
    ('original-inline-default-declaration', (
        ('original.cpp', ORIGINAL_INLINE_DECLARATION),
        ('entry.cpp', ORIGINAL_INLINE_DECLARATION+'int cpp_test_entry(){S s;S*p=&s;return p->f();}'),
    )),
    ('cross-tu-default-not-in-ABI', (
        ('provider.cpp', 'int answer(int n){return n;}'),
        ('entry.cpp', 'int answer(int n=42);int cpp_test_entry(){return answer();}'),
    )),
)


def test_exact_former_inline_negative_has_default_record_and_definition():
    parser=CppParser()
    tree=parser.parse(ORIGINAL_INLINE_DECLARATION)
    definition=parser.scope.names['S'].dtype.definition
    method=definition.scope.names['f'].overloads[0]
    assert method.default_arguments[0].tokens[0].value == '42'
    assert any(isinstance(node,c_ast.FuncDef) and node.decl.name==method.c_name for node in tree.ext)



@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_default_argument_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def walk(node):
    yield node
    for _,child in node.children():
        yield from walk(child)


def test_default_records_are_separate_from_function_type_and_abi():
    parser=CppParser()
    tree=parser.parse('int n=42;int f(int x=n){return x;}int g(){return f();}')
    symbol=parser.scope.names['f'].overloads[0]
    assert symbol.parameters == ('int',)
    default,=symbol.default_arguments
    assert [token.value for token in default.tokens] == ['n']
    assert all(any(token is source for source in parser.cursor.tokens) for token in default.tokens)
    assert default.expression.node.name == parser.scope.names['n'].c_name
    calls=[node for node in walk(tree) if isinstance(node,c_ast.FuncCall) and node.name.name==symbol.c_name]
    assert len(calls)==1 and len(calls[0].args.exprs)==1
    assert calls[0].args.exprs[0] is not default.expression.node


@pytest.mark.parametrize('source,code', (
    ('struct S { static int first(int n=sizeof(later(1))); static int later(int a,int b=a); };',
     'cpp.default-argument-parameter-use'),
    ('struct S{static int later(int a,int b=a);static int first(int n=sizeof(later(1)));};',
     'cpp.default-argument-parameter-use'),
    ('struct S{int m;int f(int n=sizeof(m));};','cpp.unsupported.default-argument-context'),
    ('struct S{int m;int f(int n=sizeof(S::m));};','cpp.unsupported.default-argument-context'),
    ('struct S{static int first(int n=sizeof(later(1)));static int later(int a,int b=sizeof(a));};',
     'cpp.unsupported.default-argument-context'),
    ('struct S{static int first(int n=later());static int later(int n=42);using R=decltype(later());};',
     'cpp.unsupported.default-argument-context'),
    ('struct S{static int later(int n=Answer);using R=decltype(later());enum{Answer=42};};',
     'cpp.unsupported.default-argument-context'),
    ('int f(int x=1);int f(int x=1);','cpp.default-argument-redefinition'),
    ('int f(int x=1,int y);','cpp.default-argument-trailing'),
    ('int f(int x,int y=x);','cpp.default-argument-parameter-use'),
    ('int f(int x=x);','cpp.default-argument-parameter-use'),
    ('int f(int x=later);int later=1;','cpp.undeclared'),
    ('int f(int x=f());','cpp.undeclared'),
    ('int f(int x=1);int f(int x,int y=2);int g(){return f(1);}','cpp.ambiguous-overload'),
    ('int f(int x={1});','cpp.unsupported.default-argument-context'),
    ('template<class T> int f(T x=1){return x;}','cpp.unsupported.default-argument-context'),
    ('int f(int x=1,...);','cpp.unsupported.default-argument-context'),
    ('struct S{S(int x=1);};','cpp.unsupported.default-argument-context'),
))
def test_invalid_or_explicitly_unsupported_default_contexts(source,code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code
