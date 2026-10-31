"""C++17 do-while: actual post-test control flow, scope, contextual conversion.

Original cases for N4659 [stmt.do], [stmt.cont], [stmt.break], [basic.scope.block].
https://www.open-std.org/JTC1/SC22/WG21/docs/papers/2017/n4659.pdf
No constructor/destructor or class conversion capability is implied.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


CASES = (
    ('body-runs-once-with-false-test', 'int cpp_test_entry(){int x=0;do{x=42;}while(false);return x;}'),
    ('post-test-repeats', 'int cpp_test_entry(){int x=0;do{x+=7;}while(x<42);return x;}'),
    ('unbraced-expression-body', 'int cpp_test_entry(){int x=0;do x+=14;while(x<42);return x;}'),
    ('empty-body', 'int cpp_test_entry(){int n=0;do;while(++n<3);return n+39;}'),
    ('condition-effect-exactly-once-per-iteration', 'int test(int*n){*n+=1;return *n<3;}'
     'int cpp_test_entry(){int n=0;int x=0;do{x+=14;}while(test(&n));if(n!=3)return 1;return x;}'),
    ('continue-targets-test', 'int cpp_test_entry(){int n=0;int x=0;do{n+=1;'
     'if(n<3)continue;x=42;}while(n<3);return x;}'),
    ('break-skips-test', 'int test(int*n){*n+=1;return 1;}'
     'int cpp_test_entry(){int n=0;do{break;}while(test(&n));return n+42;}'),
    ('return-leaves-loop', 'int cpp_test_entry(){do{return 42;}while(true);}'),
    ('nested-loop-break-is-innermost', 'int cpp_test_entry(){int x=0;int n=0;do{'
     'do{x+=14;break;}while(true);n+=1;}while(n<3);return x;}'),
    ('nested-while-continue-is-innermost', 'int cpp_test_entry(){int x=0;int outer=0;do{'
     'int inner=0;while(inner<3){inner+=1;if(inner<3)continue;x+=21;}outer+=1;'
     '}while(outer<2);return x;}'),
    ('do-continue-inside-for', 'int cpp_test_entry(){int x=0;for(int i=0;i<3;++i){'
     'int n=0;do{n+=1;if(n<2)continue;x+=14;}while(n<2);}return x;}'),
    ('body-shadow-does-not-affect-test', 'int cpp_test_entry(){int n=0;int x=0;do{'
     'int n=42;x=n;}while(n);return x;}'),
    ('unbraced-declaration-scope', 'int cpp_test_entry(){do int hidden=1;while(false);int hidden=42;return hidden;}'),
    ('contextual-pointer-test', 'int cpp_test_entry(){int x=0;int*p=&x;do{x=42;p=nullptr;}while(p);return x;}'),
    ('contextual-floating-test', 'int cpp_test_entry(){int x=0;double n=2.0;do{x+=21;n-=1.0;}while(n);return x;}'),
    ('contextual-unscoped-enum-test', 'enum State{Done,Ready};int cpp_test_entry(){'
     'State s=Ready;int x=0;do{x=42;s=Done;}while(s);return x;}'),
    ('template-post-test', 'template<class T>T f(T x){do{x+=7;}while(x<42);return x;}'
     'int cpp_test_entry(){return f(0);}'),
    ('template-continue', 'template<class T>T f(T x){T n=0;do{n+=1;if(n<3)continue;x+=42;}'
     'while(n<3);return x;}int cpp_test_entry(){return f(0);}'),
)

INVALID = (
    ('int f(){do{}while(int x=1);return 0;}', 'cpp.invalid-do-condition'),
    ('int f(){do{int x=0;}while(x);return 0;}', 'cpp.undeclared'),
    ('int f(){do int x=0;while(x);return 0;}', 'cpp.undeclared'),
    ('void nothing();int f(){do{}while(nothing());return 0;}', 'cpp.void-value'),
    ('enum class E{A};int f(){do{}while(E::A);return 0;}', 'cpp.invalid-conversion'),
    ('int f(){do{}while(false);continue;return 0;}', 'cpp.jump-outside-loop'),
    ('int f(){do{}while(false);break;return 0;}', 'cpp.jump-outside-loop'),
)


@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_do_while_compiles_twice_identically(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID)
def test_do_while_rules_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid do-while reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid-do.cpp', source),))
    assert error.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[row[0] for row in CASES])
def test_do_while_executes_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)
