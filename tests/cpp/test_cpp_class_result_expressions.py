"""C++17 return-object admission and explicitly bounded non-owning contexts."""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser

H = '''struct H {
    int n; H(int v):n(v){} H(const H& h):n(h.n){} int get(){return n;}
}; H f(){return H(42);}'''
TARGET = 'x86_64-unknown-linux-gnu'


def parse(source):
    return CppParser(TARGET).parse(H + source, 'class-result.cpp')


@pytest.mark.parametrize('source', [
    'H g(){return H(42);}', 'H g(){return (H(42));}', 'H g(){return f();}',
    'int g(){H x=f();return x.n;}', 'void g(){f();}',
    'const H g(){return f();}', 'const H a(){return H(42);} H b(){return a();}',
    'H g(int n){if(n)return H(41);else return H(42);}',
    'H g(int n){if(n)return H(41);return H(42);}',
])
def test_result_has_a_complete_destination(source):
    parse(source)


@pytest.mark.parametrize('source,code', [
    ('int g(){return f().get();}', 'class-result-context'),
    ('int g(){return f().n;}', 'class-result-context'),
    ('int g(){const H& x=f();return x.n;}', 'class-result-context'),
    ('H g(int n){return n?f():f();}', 'class-result-context'),
    ('int g(){return f()?1:2;}', 'class-result-context'),
    ('int g(){return (f(),42);}', 'class-result-context'),
    ('H g(){return (0,f());}', 'class-result-context'),
    ('int g(){return f()+1;}', 'class-result-context'),
    ('int use(const H&); int g(){return use(f());}', 'class-result-context'),
    ('int use(H); int g(){return use(f());}', 'class-value-abi'),
    ('H g(){return H(f());}', 'class-result-context'),
    ('H g(){return H(H(42));}', 'class-result-context'),
    ('H g(int n){return n?H(1):H(2);}', 'class-result-context'),
    ('H g(){return (H(1),H(2));}', 'class-result-context'),
    ('H g(){return H(1).get();}', 'class-result-context'),
    ('void g(){H x(1);x=f();}', 'class-result-context'),
    ('void g(){for(int i=0;i<1;f()){i+=1;}}', 'class-result-context'),
    ('void g(){&f();}', 'class-result-context'),
    ('H g(int n){if(n)return H(n);}', 'class-result-fallthrough'),
    ('H g(){H x(4);return x;}', 'class-result-prvalue'),
    ('H g(){goto e;e:return H(42);}', 'syntax'),
    ('H g(){while(true){return H(42);}}', 'class-result-fallthrough'),
])
def test_missing_owner_is_a_typed_diagnostic(source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == 'cpp.unsupported.' + code


def test_class_result_does_not_grant_external_noexcept_call_support():
    with pytest.raises(CppDiagnostic) as error:
        parse('H external() noexcept; H g(){return external();}')
    assert error.value.code == 'cpp.unsupported.exceptions'
