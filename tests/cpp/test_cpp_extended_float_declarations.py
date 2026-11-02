"""N4659 [dcl.fct], [expr.sizeof]: type declarations are not value ABI support.

Keep real x86_fp80/FP128 identity/storage. Definitions and evaluated calls remain
explicit until extended scalar ABI lowering is owned and tested.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINAL = 'extern "C" long double f(long double);'
UNITS = (('original.cpp',ORIGINAL),('entry.cpp','int cpp_test_entry(){return 42;}'))
CASES = (
    ('const-return-inside-template', 'const int answer(){return 42;} '
     'template<class T> int f(T x){return answer();} int cpp_test_entry(){return f(0);}'),
    ('unevaluated-return', 'extern "C" long double f();int cpp_test_entry(){return int(sizeof(f()))+26;}'),
    ('pointer-is-not-value-abi', 'extern "C" long double*same(long double*p){return &*p;}'
     'int cpp_test_entry(){if(same(nullptr)==nullptr)return 42;return 1;}'),
)


def test_original_extended_declaration_has_real_ir_type_not_double(owned_only):
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(ORIGINAL)
    symbol = parser.scope.names['f'].overloads[0]
    assert symbol.dtype == 'long double' and symbol.parameters == ('long double',)
    first = compile_host_cpp(UNITS)
    assert 'x86_fp80' in first.ir[0]
    assert first == compile_host_cpp(UNITS)


@pytest.mark.integration
def test_original_extended_declaration_matches_real_cpp17(tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,UNITS,42)


@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_extended_type_only_originals_are_byte_stable(name,source,owned_only):
    units=((name+'.cpp',source),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_extended_type_only_originals_match_real_cpp17(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


@pytest.mark.parametrize('source',[
    'extern "C" long double f(long double x){return x;}',
    'extern "C" long double f();int main(){f();return 0;}',
    'extern "C" int f(long double);int main(){return f(42);}',
    'int f(long double*p){return int(*p);}',
    'int f(long double*p){*p=42;return 0;}',
    'int f(long double*p){++*p;return 0;}',
    'long double object;',
    'int f(){long double object;return 0;}',
    'long double f();template<class T> int g(T x){f();return 0;}',
])
def test_evaluated_extended_float_paths_still_fail_before_emission(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert error.value.code=='cpp.unsupported.extended-float'
