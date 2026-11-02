"""N4659 [basic.fundamental], [conv.prom]: distinct target-owned wchar_t.

The scalar type does not implement wide literals, locale or conversion APIs.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArithmeticTypes, canonical_type, mangle_function
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

CASES=(
    ('nominal-overload','int pick(wchar_t){return 42;}int pick(int){return 1;}'
     'int cpp_test_entry(){wchar_t x=17;return pick(x);}'),
    ('integer-promotion','int pick(int){return 42;}int pick(long){return 1;}'
     'int cpp_test_entry(){wchar_t x=17;return pick(+x);}'),
    ('native-scalar-and-pointer','extern "C" wchar_t read(const wchar_t*p){return *p;}'
     'int cpp_test_entry(){wchar_t x=42;return int(read(&x));}'),
    ('storage-query','int cpp_test_entry(){return int(sizeof(wchar_t))+38;}'),
)


@pytest.mark.parametrize('triple,bits,unsigned,promotion',[
    ('x86_64-unknown-linux-gnu',32,False,'int'),
    ('aarch64-unknown-linux-gnu',32,True,'unsigned int'),
    ('x86_64-pc-windows-gnu',16,True,'int'),
])
def test_wchar_width_signedness_and_promotion_come_from_target_abi(triple,bits,unsigned,promotion):
    types=ArithmeticTypes(triple)
    assert types.width('wchar_t')==bits
    assert types.unsigned('wchar_t') is unsigned
    assert types.promote('wchar_t')==promotion
    assert types.conversion_rank('wchar_t',promotion)==1


def test_wchar_is_not_an_alias_for_int_or_a_host_typedef():
    assert canonical_type(['wchar_t'])=='wchar_t'
    assert mangle_function('pick',('wchar_t',))=='_Z4pickw'
    assert mangle_function('pick',('int',))=='_Z4picki'
    parser=CppParser()
    parser.parse('int pick(wchar_t);int pick(int);')
    assert len(parser.scope.names['pick'].overloads)==2


@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_wchar_originals_have_stable_owned_artifacts(name,source,owned_only):
    units=((name+'.cpp',source),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',CASES,ids=[x[0] for x in CASES])
def test_wchar_originals_match_actual_cpp17_reference(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


@pytest.mark.parametrize('source', ['unsigned wchar_t f();','long wchar_t f();','wchar_t int f();'])
def test_wchar_cannot_be_combined_with_integer_decl_specifiers(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code=='cpp.invalid-type'
